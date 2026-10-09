"""Airports and multi-leg journeys.

A realistic long trip is not one movement profile applied end to end: you drive
to the airport, fly, and then walk or drive from the one you land at. This
module turns "from here to there" into exactly that -- a sequence of legs, each
with the profile that fits it.

The airport list is built by `tools/make_airports.py` from OurAirports and
bundled as JSON, so planning works with no network at all.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

from .geo import distance

DATA_FILE = Path(__file__).parent / "data" / "airports.json"

#: Below this a flight makes no sense -- driving the whole way is faster than
#: two airports' worth of ground time.
MIN_FLIGHT_DISTANCE_M = 300_000
#: Beyond this from the start, reaching the airport is itself a drive worth
#: modelling rather than an afterthought.
FAR_AIRPORT_M = 150_000
#: A leg shorter than this is noise; it gets folded into its neighbour.
MIN_LEG_M = 200

__all__ = [
    "Airport",
    "Leg",
    "find_airports",
    "load_airports",
    "nearest_airport",
    "plan_journey",
]


@dataclass(frozen=True)
class Airport:
    iata: str
    icao: str
    name: str
    city: str
    country: str
    cc: str
    lat: float
    lon: float

    @property
    def label(self) -> str:
        """`SVO — Moscow, Russia`, which is how a person names an airport."""
        where = ", ".join(part for part in (self.city, self.country) if part)
        return f"{self.iata} — {where}" if where else self.iata

    def as_dict(self) -> dict[str, Any]:
        return {
            "iata": self.iata, "icao": self.icao, "name": self.name,
            "city": self.city, "country": self.country, "cc": self.cc,
            "lat": self.lat, "lon": self.lon, "label": self.label,
        }


@lru_cache(maxsize=1)
def load_airports() -> tuple[Airport, ...]:
    """The bundled airport list. Empty if the data file is missing."""
    try:
        rows = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(
        Airport(
            iata=row["iata"], icao=row.get("icao", ""), name=row.get("name", ""),
            city=row.get("city", ""), country=row.get("country", ""),
            cc=row.get("cc", ""), lat=row["lat"], lon=row["lon"],
        )
        for row in rows
    )


def _fold(text: str) -> str:
    """Casefold and strip accents, so `istanbul` matches `İstanbul`."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def find_airports(query: str, limit: int = 8) -> list[Airport]:
    """Search by IATA/ICAO code, city, airport name or country."""
    needle = _fold(query.strip())
    if not needle:
        return []

    exact: list[Airport] = []
    starts: list[Airport] = []
    contains: list[Airport] = []

    # Country names are only searched for longer queries: "ist" otherwise drags
    # in every airport in Afghanistan, Pakistan and Uzbekistan.
    search_country = len(needle) >= 4

    for airport in load_airports():
        if _fold(airport.iata) == needle or _fold(airport.icao) == needle:
            exact.append(airport)
            continue
        haystacks = [_fold(airport.city), _fold(airport.name)]
        if search_country:
            haystacks.append(_fold(airport.country))
        if any(h.startswith(needle) for h in haystacks):
            starts.append(airport)
        elif any(needle in h for h in haystacks):
            contains.append(airport)

    # An exact code is an unambiguous answer: offering alternatives next to it
    # only invites a misclick.
    if exact:
        return exact[:limit]

    return (starts + contains)[:limit]


def nearest_airport(lat: float, lon: float, exclude: Sequence[str] = ()) -> Optional[Airport]:
    """The closest airport to a point, skipping any IATA codes in `exclude`."""
    skip = {code.upper() for code in exclude}
    best: Optional[Airport] = None
    best_distance = float("inf")
    for airport in load_airports():
        if airport.iata in skip:
            continue
        metres = distance(lat, lon, airport.lat, airport.lon)
        if metres < best_distance:
            best, best_distance = airport, metres
    return best


@dataclass
class Leg:
    """One stretch of a journey, travelled with a single profile."""

    points: list[tuple[float, float]]
    profile: str
    #: Short human description, e.g. "Drive to SVO".
    note: str

    @property
    def length(self) -> float:
        total = 0.0
        for i in range(1, len(self.points)):
            total += distance(*self.points[i - 1], *self.points[i])
        return total

    def as_dict(self) -> dict[str, Any]:
        return {
            "points": [{"lat": lat, "lon": lon} for lat, lon in self.points],
            "profile": self.profile,
            "note": self.note,
            "length_m": round(self.length, 1),
        }


def _ground_profile(metres: float) -> str:
    """How someone would actually cover this distance on the ground."""
    if metres < 1_200:
        return "walk"
    if metres < 6_000:
        return "bike"
    if metres < 40_000:
        return "city"
    return "highway"


def plan_journey(
    start: tuple[float, float],
    finish: tuple[float, float],
    force_flight: Optional[bool] = None,
) -> list[Leg]:
    """Plan a trip from one point to another, flying when that makes sense.

    Short trips stay on the ground. Long ones become drive → fly → drive, using
    the nearest airport to each end. `force_flight` overrides the distance
    heuristic in either direction.

    :returns: the legs in travel order; never empty for two distinct points.
    """
    direct = distance(*start, *finish)
    should_fly = direct >= MIN_FLIGHT_DISTANCE_M if force_flight is None else force_flight

    if not should_fly or not load_airports():
        return [Leg([start, finish], _ground_profile(direct), "Ground route")]

    departure = nearest_airport(*start)
    arrival = nearest_airport(*finish, exclude=(departure.iata,) if departure else ())
    if departure is None or arrival is None or departure.iata == arrival.iata:
        return [Leg([start, finish], _ground_profile(direct), "Ground route")]

    departure_point = (departure.lat, departure.lon)
    arrival_point = (arrival.lat, arrival.lon)

    # Flying only pays off when the air leg dominates the drives to and from it.
    flight_length = distance(*departure_point, *arrival_point)
    if flight_length < MIN_FLIGHT_DISTANCE_M and force_flight is not True:
        return [Leg([start, finish], _ground_profile(direct), "Ground route")]

    legs: list[Leg] = []

    to_airport = distance(*start, *departure_point)
    if to_airport >= MIN_LEG_M:
        legs.append(Leg(
            [start, departure_point],
            "highway" if to_airport >= FAR_AIRPORT_M else _ground_profile(to_airport),
            f"Drive to {departure.iata}",
        ))

    legs.append(Leg(
        [departure_point, arrival_point],
        "plane",
        f"Fly {departure.iata} → {arrival.iata}",
    ))

    from_airport = distance(*arrival_point, *finish)
    if from_airport >= MIN_LEG_M:
        legs.append(Leg(
            [arrival_point, finish],
            "highway" if from_airport >= FAR_AIRPORT_M else _ground_profile(from_airport),
            f"{'Drive' if from_airport >= 1_200 else 'Walk'} from {arrival.iata}",
        ))

    return legs
