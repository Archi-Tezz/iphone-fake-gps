"""Movement profiles: how each kind of travel behaves.

A profile bundles the four things that make simulated movement look like the
real thing instead of a cursor sliding across a map:

* `speed` -- cruise speed in m/s.
* `accel` -- how hard it gets there. A person leans into a walk in a second or
  two; an airliner takes most of a minute to reach cruise.
* `jitter_m` -- GPS scatter. Real fixes wobble by a few metres on foot and
  barely at all at altitude, where there is no multipath off buildings.
* `speed_jitter` -- how unsteady the pace is. Walking varies a lot, a train on
  rails almost not at all.

`turn_rate` caps how fast the heading may change in free-roam mode, so a
keyboard turn curves the way the vehicle would.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

__all__ = ["PROFILES", "DEFAULT_PROFILE", "Profile", "get_profile", "kmh_to_ms", "ms_to_kmh"]


def kmh_to_ms(kmh: float) -> float:
    return float(kmh) / 3.6


def ms_to_kmh(ms: float) -> float:
    return float(ms) * 3.6


@dataclass(frozen=True)
class Profile:
    """One way of moving, with the parameters that make it believable."""

    key: str
    label: str
    icon: str
    speed: float  # m/s
    accel: float  # m/s^2
    jitter_m: float
    speed_jitter: float
    turn_rate: float  # deg/s
    interval: float  # seconds between fixes pushed to the device
    note: str = ""

    @property
    def speed_kmh(self) -> float:
        return ms_to_kmh(self.speed)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["speed_kmh"] = round(self.speed_kmh, 1)
        return data


# Cruise speeds are the ordinary, unremarkable ones: a brisk walk rather than a
# race walk, a city car rather than a chase. A simulated track that moves at a
# plausible speed is also the one that behaves plausibly in the apps reading it.
PROFILES: dict[str, Profile] = {
    "stand": Profile(
        key="stand",
        label="Stationary",
        icon="pin",
        speed=0.0,
        accel=0.0,
        jitter_m=2.5,
        speed_jitter=0.0,
        turn_rate=0.0,
        interval=2.0,
        note="Standing still, only natural GPS drift",
    ),
    "walk": Profile(
        key="walk",
        label="Walking",
        icon="walk",
        speed=1.4,
        accel=0.7,
        jitter_m=3.0,
        speed_jitter=0.14,
        turn_rate=120.0,
        interval=1.0,
        note="5 km/h, an unhurried pace",
    ),
    "run": Profile(
        key="run",
        label="Running",
        icon="run",
        speed=3.3,
        accel=1.1,
        jitter_m=3.0,
        speed_jitter=0.10,
        turn_rate=90.0,
        interval=1.0,
        note="12 km/h, an easy jog",
    ),
    "bike": Profile(
        key="bike",
        label="Cycling",
        icon="bike",
        speed=5.6,
        accel=1.0,
        jitter_m=2.5,
        speed_jitter=0.10,
        turn_rate=70.0,
        interval=1.0,
        note="20 km/h",
    ),
    "city": Profile(
        key="city",
        label="Car, city",
        icon="car",
        speed=13.9,
        accel=2.2,
        jitter_m=2.0,
        speed_jitter=0.12,
        turn_rate=45.0,
        interval=1.0,
        note="50 km/h",
    ),
    "highway": Profile(
        key="highway",
        label="Car, highway",
        icon="car",
        speed=25.0,
        accel=1.8,
        jitter_m=1.5,
        speed_jitter=0.06,
        turn_rate=20.0,
        interval=1.0,
        note="90 km/h",
    ),
    "train": Profile(
        key="train",
        label="Train",
        icon="train",
        speed=44.4,
        accel=0.7,
        jitter_m=1.0,
        speed_jitter=0.03,
        turn_rate=10.0,
        interval=1.0,
        note="160 km/h, accelerates like a train",
    ),
    "plane": Profile(
        key="plane",
        label="Plane",
        icon="plane",
        speed=250.0,
        accel=2.5,
        jitter_m=0.0,
        speed_jitter=0.02,
        turn_rate=3.0,
        interval=0.5,
        note="900 km/h, long climb and gentle turns",
    ),
}

DEFAULT_PROFILE = "walk"


def get_profile(key: Optional[str]) -> Profile:
    """Look a profile up by key, falling back to the default."""
    if not key:
        return PROFILES[DEFAULT_PROFILE]
    try:
        return PROFILES[key]
    except KeyError:
        raise ValueError(
            f"unknown profile {key!r}; available: {', '.join(PROFILES)}"
        ) from None
