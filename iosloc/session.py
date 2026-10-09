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
#: How often to poke an idle channel so the device does not close it. Well
#: under the window where the drop was observed, and cheap enough to ignore.
HEARTBEAT_SECONDS = 20.0
#: While a route is running, keep trying to get the device back for this long
#: before giving up -- long enough to walk over and plug the cable back in.
RECONNECT_WINDOW_SECONDS = 180.0
#: Delay between reconnection attempts.
RECONNECT_INTERVAL_SECONDS = 3.0

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
        #: Remaining legs of a multi-leg journey, and which one is running.
        self._journey: list[dict[str, Any]] = []
        self._leg_index = 0
        self._heartbeat: Optional[asyncio.Task[None]] = None
        #: Set while the device is gone and the route is waiting for it back.
        self._reconnecting = False

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
            self._start_heartbeat()
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
        await self._stop_heartbeat()
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

            # A plain route replaces whatever journey was running.
            self._journey = []
            self._leg_index = 0

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

    async def follow_journey(
        self,
        legs: Sequence[dict[str, Any]],
        from_current: bool = True,
    ) -> dict[str, Any]:
        """Travel a sequence of legs, each with its own movement profile.

        This is what makes a long trip believable: drive to the airport, fly,
        then walk at the other end. Each leg runs as an ordinary route; the pump
        moves to the next one as soon as the current finishes, switching profile
        with it.
        """
        if not legs:
            raise ValueError(t("A route needs at least one point"))

        async with self._command_lock:
            self._require_connection()

            prepared: list[dict[str, Any]] = []
            for leg in legs:
                points = [(float(p[0]), float(p[1])) for p in leg["points"]]
                if len(points) < 2:
                    continue
                prepared.append({
                    "points": points,
                    "profile": leg.get("profile") or DEFAULT_PROFILE,
                    "note": leg.get("note", ""),
                })
            if not prepared:
                raise ValueError(t("A route needs at least one point"))

            # Start from where the device is, so the first leg does not teleport.
            if from_current and self._last_fix is not None:
                first = prepared[0]
                here = (self._last_fix.latitude, self._last_fix.longitude)
                if distance(*here, *first["points"][0]) > MIN_RESEND_DISTANCE_M:
                    first["points"].insert(0, here)

            self._journey = prepared
            self._leg_index = 0
            self._paused = False
            self._trail.clear()
            self._begin_leg(0)
            self._start_pump(restart=True)

            total = sum(self._leg_length(leg) for leg in prepared)
            return {
                "legs": [
                    {
                        "note": leg["note"],
                        "profile": leg["profile"],
                        "length_m": round(self._leg_length(leg), 1),
                        "points": len(leg["points"]),
                    }
                    for leg in prepared
                ],
                "total_m": round(total, 1),
                "eta_s": round(self._journey_eta()),
            }

    @staticmethod
    def _leg_length(leg: dict[str, Any]) -> float:
        points = leg["points"]
        return sum(distance(*points[i - 1], *points[i]) for i in range(1, len(points)))

    def _journey_eta(self) -> float:
        """Rough time for the legs not yet finished, at each one's cruise speed."""
        total = 0.0
        for index in range(self._leg_index, len(self._journey)):
            leg = self._journey[index]
            speed = get_profile(leg["profile"]).speed
            if speed <= 0:
                continue
            remaining = self._leg_length(leg)
            if index == self._leg_index and isinstance(self._runner, RouteRunner):
                remaining = self._runner.remaining
            total += remaining / speed
        return total

    def _begin_leg(self, index: int) -> None:
        """Make leg `index` the active route, with its own profile."""
        leg = self._journey[index]
        self._leg_index = index
        self._profile = get_profile(leg["profile"])
        self._runner = RouteRunner(
            track=Track(leg["points"]),
            speed=self._profile.speed,
            mode=LoopMode.ONCE,
            jitter_m=self._profile.jitter_m,
            speed_jitter=self._profile.speed_jitter,
            accel=self._profile.accel,
        )
        logger.info(
            "journey leg %d/%d: %s (%s)",
            index + 1, len(self._journey), leg["note"] or "leg", leg["profile"],
        )

    def _advance_journey(self) -> bool:
        """Move to the next leg. False when the journey is over."""
        if not self._journey or self._leg_index + 1 >= len(self._journey):
            return False
        self._begin_leg(self._leg_index + 1)
        return True

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
                self._journey = []
                self._leg_index = 0
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
            "reconnecting": self._reconnecting,
            "error": self._error,
            "position": None,
            "route": None,
            "trail": [{"lat": lat, "lon": lon} for lat, lon in self._trail],
            "journey": None,
        }
        if self._journey:
            data["journey"] = {
                "leg": self._leg_index + 1,
                "legs": len(self._journey),
                # Geometry of every leg, so the map can draw each one in the
                # colour of its profile instead of one undifferentiated line.
                "shape": [
                    {
                        "profile": leg["profile"],
                        "note": leg.get("note", ""),
                        "points": [{"lat": lat, "lon": lon} for lat, lon in leg["points"]],
                        "done": index < self._leg_index,
                    }
                    for index, leg in enumerate(self._journey)
                ],
                "note": self._journey[self._leg_index].get("note", ""),
                "profile": self._journey[self._leg_index].get("profile", ""),
                "eta_s": round(self._journey_eta()),
                "finished": (
                    self._leg_index + 1 >= len(self._journey)
                    and getattr(self._runner, "finished", False)
                ),
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

    def _start_heartbeat(self) -> None:
        if self._heartbeat is not None and not self._heartbeat.done():
            return
        self._heartbeat = asyncio.create_task(self._run_heartbeat(), name="iosloc-heartbeat")

    async def _stop_heartbeat(self) -> None:
        task, self._heartbeat = self._heartbeat, None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run_heartbeat(self) -> None:
        """Keep an idle channel from being closed by the device.

        A channel that nothing writes to gets closed, and the next override
        fails -- which is invisible until someone finally moves the device.
        While a route is running the pump already keeps it warm; when nothing is
        moving, clearing an override that is not set is a harmless no-op that
        still counts as traffic.
        """
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                if self._link is None or not self._link.is_open:
                    return
                if self._override_active or self._reconnecting:
                    continue
                try:
                    await self._link.clear()
                except Exception as exc:
                    # The link repairs itself on the next real call; a failed
                    # heartbeat is not worth bothering the user about.
                    logger.debug("heartbeat failed: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("heartbeat stopped")

    async def _wait_for_device(self) -> bool:
        """Try to get the device back after it vanished mid-route.

        Returns True once the link is usable again, False if it never came back
        within the window -- unplugging the cable should pause a journey, not
        destroy it.
        """
        if self._link is None:
            return False

        self._reconnecting = True
        self._error = None
        deadline = time.monotonic() + RECONNECT_WINDOW_SECONDS
        attempt = 0
        try:
            while time.monotonic() < deadline:
                attempt += 1
                await asyncio.sleep(RECONNECT_INTERVAL_SECONDS)
                try:
                    await self._link.close()
                except Exception:
                    pass
                try:
                    await self._link.open()
                except Exception as exc:
                    logger.debug("reconnect attempt %d failed: %s", attempt, exc)
                    continue
                logger.info("device is back after %d attempt(s); resuming", attempt)
                # The position was last pushed before the drop, so re-send it to
                # put the device back where the route had got to.
                self._last_sent_fix = None
                return True
            self._error = t(
                "Lost the device and could not get it back. Check the cable and connect again."
            )
            logger.warning("device did not come back within %.0fs", RECONNECT_WINDOW_SECONDS)
            return False
        finally:
            self._reconnecting = False

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
                try:
                    await self._send(fix)
                except Exception as exc:
                    # The link already retries a dead channel on its own, so
                    # reaching here means the device itself went away.
                    logger.info("send failed (%s); waiting for the device", exc)
                    if not await self._wait_for_device():
                        return
                    last_tick = time.monotonic()
                    continue

                # A finished leg hands over to the next one without a gap.
                if getattr(self._runner, "finished", False) and self._journey:
                    if self._advance_journey():
                        last_tick = time.monotonic()
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
