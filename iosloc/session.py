"""The controller: one device link, one movement runner, one pump task.

`LocationSession` is the single object both the CLI and the web UI drive. It owns

* the `DeviceLink` (USB connection and location channel),
* whichever runner is active -- `RouteRunner` for a path, `ManualRunner` for
  free roam and for standing still,
* and the pump: an asyncio task that ticks the runner on the profile's interval
  and pushes each resulting fix to the device.

Only the pump talks to the device during movement, so commands coming in from
the UI never race each other over the DTX channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from typing import Any, Deque, Optional, Sequence

from . import errors
from .i18n import t
from .device import DeviceInfo, DeviceLink, list_devices
from .geo import distance, validate_coordinate
from .profiles import DEFAULT_PROFILE, Profile, get_profile, kmh_to_ms, ms_to_kmh
from .route import Fix, LoopMode, ManualRunner, RouteRunner, Track

logger = logging.getLogger(__name__)

#: Resend an unchanged position at least this often, so a long stand-still keeps
#: the override alive even if the device drops it.
KEEPALIVE_SECONDS = 15.0
#: Below this movement a fix is considered unchanged and is not resent.
MIN_RESEND_DISTANCE_M = 0.25
#: How many recent fixes to keep for drawing the travelled path in the UI.
TRAIL_LIMIT = 2000

__all__ = ["LocationSession"]


class LocationSession:
    """Drives one device: connect, move, stop, restore."""

    def __init__(self) -> None:
        self._link: Optional[DeviceLink] = None
        self._runner: Any = None
        self._pump: Optional[asyncio.Task[None]] = None
        self._command_lock = asyncio.Lock()
        self._profile: Profile = get_profile(DEFAULT_PROFILE)
        self._paused = False
        self._override_active = False
        self._last_fix: Optional[Fix] = None
        self._last_sent_at = 0.0
        self._last_sent_fix: Optional[Fix] = None
        self._trail: Deque[tuple[float, float]] = deque(maxlen=TRAIL_LIMIT)
        self._error: Optional[str] = None
        self._fixes_sent = 0

    # ------------------------------------------------------------- connection

    @property
    def connected(self) -> bool:
        return self._link is not None and self._link.is_open

    @property
    def device(self) -> Optional[DeviceInfo]:
        return self._link.info if self._link is not None else None

    async def devices(self) -> list[DeviceInfo]:
        """List attached devices (never pairs, never starts a service)."""
        return await list_devices()

    async def connect(self, udid: Optional[str] = None, enable_developer_mode: bool = False) -> DeviceInfo:
        """Open the wired session. Reconnecting to the same device is a no-op."""
        async with self._command_lock:
            if self.connected:
                assert self._link is not None and self._link.info is not None
                if udid is None or udid == self._link.info.udid:
                    return self._link.info
                await self._teardown(restore=True)
            link = DeviceLink(udid=udid, enable_developer_mode=enable_developer_mode)
            await link.open()
            self._link = link
            self._error = None
            self._fixes_sent = 0
            assert link.info is not None
            logger.info(
                "connected to %s (iOS %s) over %s",
                link.info.name,
                link.info.ios_version,
                link.transport,
            )
            return link.info

    async def disconnect(self, restore: bool = True) -> None:
        """Stop moving, optionally restore the real location, and close the link."""
        async with self._command_lock:
            await self._teardown(restore=restore)

    async def _teardown(self, restore: bool) -> None:
        await self._stop_pump()
        self._runner = None
        if self._link is not None:
            if restore and self._override_active:
                with contextlib.suppress(Exception):
                    await self._link.clear()
                self._override_active = False
            await self._link.close()
        self._link = None
        self._last_fix = None
        self._last_sent_fix = None
        self._trail.clear()

    # ---------------------------------------------------------------- actions

    async def teleport(self, latitude: float, longitude: float, profile: Optional[str] = None) -> None:
        """Jump to a point and hold it. Cancels any route in progress."""
        latitude, longitude = validate_coordinate(latitude, longitude)
        async with self._command_lock:
            self._require_connection()
            self._profile = get_profile(profile) if profile else self._profile
            self._runner = ManualRunner(
                latitude=latitude,
                longitude=longitude,
                speed=0.0,
                jitter_m=self._profile.jitter_m,
                accel=self._profile.accel,
                turn_rate=self._profile.turn_rate,
            )
            self._paused = False
            self._trail.clear()
            await self._send(self._runner.current(), force=True)
            self._start_pump()

    async def free_roam(
        self,
        profile: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        speed_kmh: Optional[float] = None,
        heading: Optional[float] = None,
    ) -> None:
        """Switch to joystick mode, starting from the current (or given) position."""
        async with self._command_lock:
            self._require_connection()
            if profile:
                self._profile = get_profile(profile)
            start = self._start_point(latitude, longitude)
            speed = self._profile.speed if speed_kmh is None else kmh_to_ms(speed_kmh)
            current_heading = heading if heading is not None else (
                self._last_fix.heading if self._last_fix else 0.0
            )
            self._runner = ManualRunner(
                latitude=start[0],
                longitude=start[1],
                heading=current_heading,
                speed=speed,
                jitter_m=self._profile.jitter_m,
                accel=self._profile.accel,
                turn_rate=self._profile.turn_rate,
            )
            self._paused = False
            self._start_pump()

    async def steer(
        self,
        heading: Optional[float] = None,
        speed_kmh: Optional[float] = None,
        snap_heading: bool = False,
    ) -> None:
        """Set the joystick heading and/or speed. Starts free roam if not in it."""
        async with self._command_lock:
            self._require_connection()
            if not isinstance(self._runner, ManualRunner):
                start = self._start_point(None, None)
                self._runner = ManualRunner(
                    latitude=start[0],
                    longitude=start[1],
                    heading=heading or 0.0,
                    jitter_m=self._profile.jitter_m,
                    accel=self._profile.accel,
                    turn_rate=self._profile.turn_rate,
                )
            speed = None if speed_kmh is None else kmh_to_ms(speed_kmh)
            self._runner.steer(heading=heading, speed=speed, snap_heading=snap_heading)
            self._paused = False
            self._start_pump()

    async def follow(
        self,
        points: Sequence[Sequence[float]],
        profile: Optional[str] = None,
        mode: str = "once",
        speed_kmh: Optional[float] = None,
        from_current: bool = True,
    ) -> dict[str, Any]:
        """Travel along a path. Returns a summary of the route that was started.

        With `from_current`, the current position is prepended so the device
        moves to the start of the path instead of jumping to it.
        """
        if len(points) < 1:
            raise ValueError(t("A route needs at least one point"))
        async with self._command_lock:
            self._require_connection()
            if profile:
                self._profile = get_profile(profile)
            loop_mode = LoopMode(mode)

            coordinates: list[tuple[float, float]] = []
            if from_current and self._last_fix is not None:
                coordinates.append((self._last_fix.latitude, self._last_fix.longitude))
            coordinates.extend((float(p[0]), float(p[1])) for p in points)

            track = Track(coordinates)
            speed = self._profile.speed if speed_kmh is None else kmh_to_ms(speed_kmh)
            self._runner = RouteRunner(
                track=track,
                speed=speed,
                mode=loop_mode,
                jitter_m=self._profile.jitter_m,
                speed_jitter=self._profile.speed_jitter,
                accel=self._profile.accel,
            )
            self._paused = False
            self._trail.clear()
            self._start_pump()
            return {
                "points": len(track),
                "length_m": round(track.length, 1),
                "speed_kmh": round(ms_to_kmh(speed), 1),
                "mode": loop_mode.value,
                "eta_s": round(self._runner.eta) if self._runner.eta else None,
            }

    async def set_profile(self, profile: str, keep_speed: bool = False) -> Profile:
        """Switch profile mid-run; the active runner is retuned, not restarted."""
        async with self._command_lock:
            new_profile = get_profile(profile)
            previous_speed = getattr(self._runner, "speed", None)
            self._profile = new_profile
            runner = self._runner
            if runner is not None:
                runner.jitter_m = new_profile.jitter_m
                if isinstance(runner, RouteRunner):
                    runner.speed_jitter = new_profile.speed_jitter
                    runner.retarget(
                        speed=previous_speed if keep_speed else new_profile.speed,
                        accel=new_profile.accel,
                    )
                else:
                    runner.accel = new_profile.accel
                    runner.turn_rate = new_profile.turn_rate
                    if not keep_speed and runner.speed > 0.0:
                        runner.steer(speed=new_profile.speed)
                # The interval changed with the profile, so restart the pump.
                self._start_pump(restart=True)
            return new_profile

    async def set_speed(self, speed_kmh: float) -> None:
        """Override the cruise speed without changing profile."""
        async with self._command_lock:
            runner = self._runner
            if runner is None:
                raise errors.NotConnectedError(t("Set a position first."))
            speed = kmh_to_ms(max(0.0, speed_kmh))
            if isinstance(runner, RouteRunner):
                runner.retarget(speed=speed)
            else:
                runner.steer(speed=speed)

    async def pause(self) -> None:
        """Freeze movement, keeping the override at the current position."""
        async with self._command_lock:
            self._paused = True

    async def resume(self) -> None:
        async with self._command_lock:
            if self._runner is None:
                return
            self._paused = False
            self._start_pump()

    async def stop(self, restore: bool = False) -> None:
        """Stop moving. With `restore`, also hand the real GPS back to the device."""
        async with self._command_lock:
            await self._stop_pump()
            self._paused = False
            if restore:
                self._runner = None
                if self._link is not None and self._override_active:
                    await self._link.clear()
                    self._override_active = False
                self._last_fix = None
                self._last_sent_fix = None
                self._trail.clear()
            elif isinstance(self._runner, RouteRunner):
                # Keep holding the position the route reached.
                fix = self._runner.current()
                self._runner = ManualRunner(
                    latitude=fix.latitude,
                    longitude=fix.longitude,
                    heading=fix.heading,
                    speed=0.0,
                    jitter_m=self._profile.jitter_m,
                    accel=self._profile.accel,
                    turn_rate=self._profile.turn_rate,
                )
                self._start_pump()
            elif isinstance(self._runner, ManualRunner):
                self._runner.steer(speed=0.0)
                self._start_pump()

    async def restore(self) -> None:
        """Give the device its real location back, keeping the session open."""
        await self.stop(restore=True)

    async def force_clear(self) -> None:
        """Clear an override unconditionally, including one this process did not set.

        A simulated location set by an earlier run (or by Xcode) survives in the
        device's developer session, so `stop(restore=True)` -- which only clears
        what this session is tracking -- would report nothing to do.
        """
        async with self._command_lock:
            self._require_connection()
            await self._stop_pump()
            assert self._link is not None
            await self._link.clear()
            self._override_active = False
            self._runner = None
            self._last_fix = None
            self._last_sent_fix = None
            self._trail.clear()

    # ------------------------------------------------------------------ state

    def state(self) -> dict[str, Any]:
        """A JSON-ready snapshot for the UI."""
        runner = self._runner
        fix = self._last_fix
        mode = "idle"
        if isinstance(runner, RouteRunner):
            mode = "route"
        elif isinstance(runner, ManualRunner):
            mode = "free" if runner.speed > 0.0 else "hold"

        data: dict[str, Any] = {
            "connected": self.connected,
            "device": self.device.as_dict() if self.device else None,
            "transport": self._link.transport if self._link else "",
            "override_active": self._override_active,
            "mode": mode,
            "paused": self._paused,
            "profile": self._profile.as_dict(),
            "fixes_sent": self._fixes_sent,
            "error": self._error,
            "position": None,
            "route": None,
            "trail": [{"lat": lat, "lon": lon} for lat, lon in self._trail],
        }
        if fix is not None:
            data["position"] = {
                "lat": round(fix.latitude, 7),
                "lon": round(fix.longitude, 7),
                "heading": round(fix.heading, 1),
                "speed_kmh": round(ms_to_kmh(fix.speed), 1),
            }
        if isinstance(runner, RouteRunner):
            data["route"] = {
                "points": runner.track.to_list(),
                "length_m": round(runner.track.length, 1),
                "progress": round(runner.progress, 4),
                "remaining_m": round(runner.remaining, 1),
                "eta_s": round(runner.eta) if runner.eta else None,
                "mode": runner.mode.value,
                "finished": runner.finished,
                "target_speed_kmh": round(ms_to_kmh(runner.speed), 1),
            }
        elif isinstance(runner, ManualRunner):
            data["route"] = {
                "target_speed_kmh": round(ms_to_kmh(runner.speed), 1),
                "target_heading": round(runner.target_heading, 1),
            }
        return data

    # -------------------------------------------------------------- internals

    def _require_connection(self) -> None:
        if not self.connected:
            raise errors.NotConnectedError(t("Connect the iPhone by cable first."))

    def _start_point(self, latitude: Optional[float], longitude: Optional[float]) -> tuple[float, float]:
        if latitude is not None and longitude is not None:
            return validate_coordinate(latitude, longitude)
        if self._last_fix is not None:
            return self._last_fix.latitude, self._last_fix.longitude
        raise errors.NotConnectedError(t(
            "The starting point is unknown — place a position on the map first."))

    def _start_pump(self, restart: bool = False) -> None:
        if restart:
            self._cancel_pump()
        if self._pump is not None and not self._pump.done():
            return
        self._pump = asyncio.create_task(self._run_pump(), name="iosloc-pump")

    def _cancel_pump(self) -> None:
        if self._pump is not None and not self._pump.done():
            self._pump.cancel()
        self._pump = None

    async def _stop_pump(self) -> None:
        pump, self._pump = self._pump, None
        if pump is None or pump.done():
            return
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump

    async def _run_pump(self) -> None:
        """Tick the runner on the profile interval and push each fix to the device."""
        last_tick = time.monotonic()
        try:
            while True:
                interval = max(0.1, self._profile.interval)
                await asyncio.sleep(interval)
                if self._runner is None or self._link is None:
                    return
                now = time.monotonic()
                dt = now - last_tick
                last_tick = now
                if self._paused:
                    continue
                fix = self._runner.advance(dt)
                await self._send(fix)
        except asyncio.CancelledError:
            raise
        except errors.IosLocError as exc:
            self._error = str(exc)
            logger.error("pump stopped: %s", exc)
        except Exception as exc:
            self._error = t("The location channel dropped: {error}", error=exc)
            logger.exception("pump stopped unexpectedly")

    async def _send(self, fix: Fix, force: bool = False) -> None:
        """Push a fix, skipping resends of a position that has not moved."""
        if self._link is None:
            return
        now = time.monotonic()
        if not force and self._last_sent_fix is not None:
            moved = distance(
                self._last_sent_fix.latitude,
                self._last_sent_fix.longitude,
                fix.latitude,
                fix.longitude,
            )
            if moved < MIN_RESEND_DISTANCE_M and (now - self._last_sent_at) < KEEPALIVE_SECONDS:
                self._last_fix = fix
                return

        await self._link.set(fix.latitude, fix.longitude)
        self._override_active = True
        self._last_fix = fix
        self._last_sent_fix = fix
        self._last_sent_at = now
        self._fixes_sent += 1
        if not self._trail or distance(*self._trail[-1], fix.latitude, fix.longitude) > 1.0:
            self._trail.append((fix.latitude, fix.longitude))
