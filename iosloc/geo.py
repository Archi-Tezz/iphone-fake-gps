"""Spherical geometry helpers.

Everything here works on the WGS-84 sphere (mean earth radius). At the scales a
walking/driving route covers, the error against the real ellipsoid is well under
a metre, which is far below the noise CoreLocation reports anyway.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6371008.8

__all__ = [
    "EARTH_RADIUS_M",
    "bearing",
    "destination",
    "distance",
    "interpolate",
    "normalize_longitude",
    "validate_coordinate",
]


def validate_coordinate(latitude: float, longitude: float) -> tuple[float, float]:
    """Return the coordinate as floats, raising ValueError if it is off the globe."""
    latitude = float(latitude)
    longitude = float(longitude)
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("coordinates must be finite numbers")
    if not -90.0 <= latitude <= 90.0:
        raise ValueError(f"latitude {latitude} is outside [-90, 90]")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"longitude {longitude} is outside [-180, 180]")
    return latitude, longitude


def normalize_longitude(longitude: float) -> float:
    """Wrap a longitude into [-180, 180), so a route crossing the antimeridian stays valid."""
    return (longitude + 180.0) % 360.0 - 180.0


def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (haversine)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, in degrees clockwise from north."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return math.degrees(math.atan2(y, x)) % 360.0


def destination(latitude: float, longitude: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """The point reached by travelling `distance_m` along `bearing_deg` from the given point."""
    if distance_m == 0.0:
        return latitude, longitude
    delta = distance_m / EARTH_RADIUS_M
    theta = math.radians(bearing_deg)
    phi1, lambda1 = math.radians(latitude), math.radians(longitude)
    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    phi2 = math.asin(max(-1.0, min(1.0, sin_phi2)))
    lambda2 = lambda1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * sin_phi2,
    )
    return math.degrees(phi2), normalize_longitude(math.degrees(lambda2))


def interpolate(
    lat1: float, lon1: float, lat2: float, lon2: float, fraction: float
) -> tuple[float, float]:
    """Point at `fraction` (0..1) of the great-circle path from point 1 to point 2."""
    if fraction <= 0.0:
        return lat1, lon1
    if fraction >= 1.0:
        return lat2, lon2
    total = distance(lat1, lon1, lat2, lon2)
    if total < 1e-9:
        return lat1, lon1
    return destination(lat1, lon1, bearing(lat1, lon1, lat2, lon2), total * fraction)
