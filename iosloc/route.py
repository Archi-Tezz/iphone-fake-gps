"""Routes and the movement engine.

A `Track` is a polyline with precomputed cumulative distances, so a position can
be looked up by "metres travelled" in O(log n). `RouteRunner` walks that track at
a requested speed; `ManualRunner` is the free-roam equivalent driven by a heading.

Both runners are pure state machines: `advance(dt)` returns the next fix and
mutates nothing outside the runner. The device I/O lives in `session.py`, which
makes the engine testable with no phone attached.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from .geo import bearing, destination, distance, interpolate, validate_coordinate

__all__ = ["Fix", "LoopMode", "ManualRunner", "RouteRunner", "Track", "parse_gpx"]


@dataclass(frozen=True)
class Fix:
    """A single simulated position handed to the device."""

    latitude: float
    longitude: float
    heading: float = 0.0
    speed: float = 0.0


class LoopMode(str, Enum):
    ONCE = "once"
    LOOP = "loop"
    PINGPONG = "pingpong"


class Track:
    """A polyline of coordinates, addressable by distance travelled along it."""

    def __init__(self, points: Sequence[tuple[float, float]]) -> None:
        cleaned: list[tuple[float, float]] = []
        for latitude, longitude in points:
            point = validate_coordinate(latitude, longitude)
            # Drop consecutive duplicates: they add no length but would form a
            # zero-length segment, whose heading is undefined.
            if cleaned and distance(cleaned[-1][0], cleaned[-1][1], point[0], point[1]) < 1e-6:
                continue
            cleaned.append(point)
        if not cleaned:
            raise ValueError("a track needs at least one point")
        self.points: list[tuple[float, float]] = cleaned
        self._cumulative: list[float] = [0.0]
        for i in range(1, len(cleaned)):
            previous, current = cleaned[i - 1], cleaned[i]
            self._cumulative.append(
                self._cumulative[-1] + distance(previous[0], previous[1], current[0], current[1])
            )

    def __len__(self) -> int:
        return len(self.points)

    @property
    def length(self) -> float:
        """Total length of the track in metres (0.0 for a single point)."""
        return self._cumulative[-1]

    @property
    def is_degenerate(self) -> bool:
        """True when the track has no length to travel along."""
        return self.length < 1e-6

    def position_at(self, travelled: float) -> Fix:
        """The fix at `travelled` metres along the track, clamped to its ends."""
        if self.is_degenerate:
            latitude, longitude = self.points[0]
            return Fix(latitude, longitude, 0.0, 0.0)
        travelled = min(max(travelled, 0.0), self.length)
        # The containing segment is the one whose cumulative end is the first
        # value >= travelled.
        index = bisect.bisect_left(self._cumulative, travelled)
        if index == 0:
            index = 1
        start = self.points[index - 1]
        end = self.points[index]
        segment_start = self._cumulative[index - 1]
        segment_length = self._cumulative[index] - segment_start
        fraction = 1.0 if segment_length < 1e-9 else (travelled - segment_start) / segment_length
        latitude, longitude = interpolate(start[0], start[1], end[0], end[1], fraction)
        return Fix(latitude, longitude, bearing(start[0], start[1], end[0], end[1]), 0.0)

    def to_list(self) -> list[dict[str, float]]:
        return [{"lat": lat, "lon": lon} for lat, lon in self.points]

    @classmethod
    def from_pairs(cls, pairs: Iterable[Sequence[float]]) -> "Track":
        return cls([(float(pair[0]), float(pair[1])) for pair in pairs])


def parse_gpx(path: Union[str, Path]) -> Track:
    """Build a `Track` from the trackpoints, route points or waypoints of a GPX file.

    Timestamps are ignored here on purpose: ios-loc paces movement by a requested
    speed rather than replaying the recorded timing. Pass --gpx-timing to
    `iosloc play` for timestamp-faithful replay instead.
    """
    import gpxpy  # imported lazily: only the GPX paths need it

    with open(path, encoding="utf-8") as handle:
        gpx = gpxpy.parse(handle)

    points: list[tuple[float, float]] = []
    for track in gpx.tracks:
        for segment in track.segments:
            points.extend((p.latitude, p.longitude) for p in segment.points)
    if not points:
        for gpx_route in gpx.routes:
            points.extend((p.latitude, p.longitude) for p in gpx_route.points)
    if not points:
        points.extend((p.latitude, p.longitude) for p in gpx.waypoints)
    if not points:
        raise ValueError(f"{path} contains no track points, route points or waypoints")
    return Track(points)


def _jitter(fix: Fix, radius_m: float, rng: random.Random) -> Fix:
    """Scatter a fix within `radius_m`, uniformly over the disc."""
    if radius_m <= 0.0:
        return fix
    # sqrt keeps the spread uniform by area instead of clustering at the centre.
    offset = radius_m * math.sqrt(rng.random())
    latitude, longitude = destination(fix.latitude, fix.longitude, rng.uniform(0.0, 360.0), offset)
    return Fix(latitude, longitude, fix.heading, fix.speed)


def _approach(current: float, target: float, max_delta: float) -> float:
    """Move `current` towards `target` by at most `max_delta`."""
    if current < target:
        return min(target, current + max_delta)
    return max(target, current - max_delta)


@dataclass
class RouteRunner:
    """Walks a `Track` at a target speed, optionally looping and jittering.

    Speed is ramped rather than switched: the runner accelerates towards
    `speed` at `accel` m/s^2 and brakes into the final waypoint over exactly the
    distance that deceleration needs. That is what makes the simulated movement
    read as a person setting off or a plane climbing out, instead of a point
    teleporting at a constant rate from the first tick.
    """

    track: Track
    speed: float = 1.4  # m/s cruise target; 1.4 m/s is an unhurried walking pace
    mode: LoopMode = LoopMode.ONCE
    jitter_m: float = 0.0
    speed_jitter: float = 0.0  # fraction of `speed`, e.g. 0.15 for +/-15%
    accel: float = 0.0  # m/s^2; 0 disables ramping (instant cruise speed)
    travelled: float = 0.0
    direction: int = 1
    finished: bool = False
    current_speed: float = 0.0
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        if self.accel <= 0.0:
            self.current_speed = self.speed

    def reset(self) -> None:
        self.travelled = 0.0
        self.direction = 1
        self.finished = False
        self.current_speed = self.speed if self.accel <= 0.0 else 0.0

    def retarget(self, speed: Optional[float] = None, accel: Optional[float] = None) -> None:
        """Change the cruise speed mid-run; the ramp carries the change smoothly."""
        if speed is not None:
            self.speed = max(0.0, float(speed))
        if accel is not None:
            self.accel = max(0.0, float(accel))
            if self.accel <= 0.0:
                self.current_speed = self.speed

    @property
    def progress(self) -> float:
        """Fraction of the track covered, 0..1."""
        if self.track.is_degenerate:
            return 1.0
        return min(1.0, max(0.0, self.travelled / self.track.length))

    @property
    def remaining(self) -> float:
        """Metres left to the end of the track in the current direction."""
        if self.mode is LoopMode.ONCE:
            return max(0.0, self.track.length - self.travelled)
        return self.track.length - self.travelled if self.direction > 0 else self.travelled

    @property
    def eta(self) -> Optional[float]:
        """Seconds to the end of the route at cruise speed, or None if it never ends."""
        if self.mode is not LoopMode.ONCE or self.finished:
            return None
        if self.speed <= 0.0:
            return None
        return self.remaining / self.speed

    def current(self) -> Fix:
        fix = self.track.position_at(self.travelled)
        heading = fix.heading if self.direction > 0 else (fix.heading + 180.0) % 360.0
        return Fix(fix.latitude, fix.longitude, heading, 0.0 if self.finished else self.current_speed)

    def advance(self, dt: float) -> Fix:
        """Move `dt` seconds along the track and return the resulting fix."""
        if self.track.is_degenerate:
            self.finished = True
            self.current_speed = 0.0
            return _jitter(self.current(), self.jitter_m, self.rng)

        dt = max(0.0, dt)
        if not self.finished:
            self.current_speed = self._next_speed(dt)
            self.travelled += self.current_speed * dt * self.direction
            self._handle_ends()
        else:
            self.current_speed = 0.0

        return _jitter(self.current(), self.jitter_m, self.rng)

    def _next_speed(self, dt: float) -> float:
        """The speed for this tick: ramped towards cruise, braked near the end."""
        target = self.speed
        if self.speed_jitter > 0.0:
            target *= 1.0 + self.rng.uniform(-self.speed_jitter, self.speed_jitter)
        target = max(0.0, target)

        if self.accel <= 0.0:
            return target

        # Brake into a stop only where the run actually ends: looping and
        # ping-pong carry momentum through the turn.
        if self.mode is LoopMode.ONCE:
            # v = sqrt(2*a*s) is the fastest speed from which `remaining` metres
            # still suffice to stop at `accel`.
            target = min(target, math.sqrt(2.0 * self.accel * max(0.0, self.remaining)))

        return _approach(self.current_speed, target, self.accel * dt)

    def _handle_ends(self) -> None:
        length = self.track.length
        if self.mode is LoopMode.LOOP:
            # Modulo keeps a long step from overshooting more than one lap.
            self.travelled %= length
            return
        if self.mode is LoopMode.PINGPONG:
            while self.travelled > length or self.travelled < 0.0:
                if self.travelled > length:
                    self.travelled = 2 * length - self.travelled
                    self.direction = -1
                if self.travelled < 0.0:
                    self.travelled = -self.travelled
                    self.direction = 1
            return
        if self.travelled >= length:
            self.travelled = length
            self.finished = True


def _angle_delta(from_deg: float, to_deg: float) -> float:
    """Shortest signed turn from one bearing to another, in (-180, 180].

    A half turn is equally short either way; it resolves to +180 so a reversal
    goes clockwise rather than landing on the -180 edge of the range.
    """
    delta = (to_deg - from_deg + 180.0) % 360.0 - 180.0
    return 180.0 if delta == -180.0 else delta


@dataclass
class ManualRunner:
    """Free-roam movement: hold a heading and a speed, like a joystick.

    Both the heading and the speed are approached gradually (`turn_rate`,
    `accel`), so steering with the keyboard produces a curve rather than a
    right-angle jump -- the same reason the route runner ramps its speed.
    """

    latitude: float
    longitude: float
    heading: float = 0.0
    speed: float = 0.0  # target speed in m/s
    jitter_m: float = 0.0
    accel: float = 0.0  # m/s^2; 0 means the target speed applies at once
    turn_rate: float = 0.0  # deg/s; 0 means the heading applies at once
    current_speed: float = 0.0
    target_heading: float = 0.0
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        self.target_heading = self.heading
        if self.accel <= 0.0:
            self.current_speed = self.speed

    def teleport(self, latitude: float, longitude: float) -> None:
        self.latitude, self.longitude = validate_coordinate(latitude, longitude)

    def steer(
        self,
        heading: Optional[float] = None,
        speed: Optional[float] = None,
        snap_heading: bool = False,
    ) -> None:
        if heading is not None:
            self.target_heading = float(heading) % 360.0
            if snap_heading or self.turn_rate <= 0.0:
                self.heading = self.target_heading
        if speed is not None:
            self.speed = max(0.0, float(speed))
            if self.accel <= 0.0:
                self.current_speed = self.speed

    def current(self) -> Fix:
        return Fix(self.latitude, self.longitude, self.heading, self.current_speed)

    def advance(self, dt: float) -> Fix:
        dt = max(0.0, dt)
        if self.turn_rate > 0.0:
            delta = _angle_delta(self.heading, self.target_heading)
            limit = self.turn_rate * dt
            self.heading = (self.heading + max(-limit, min(limit, delta))) % 360.0
        else:
            self.heading = self.target_heading

        if self.accel > 0.0:
            self.current_speed = _approach(self.current_speed, self.speed, self.accel * dt)
        else:
            self.current_speed = self.speed

        step = self.current_speed * dt
        if step > 0.0:
            self.latitude, self.longitude = destination(
                self.latitude, self.longitude, self.heading, step
            )
        return _jitter(self.current(), self.jitter_m, self.rng)
