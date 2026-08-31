"""Coordinates, place lookup and the angle arithmetic the model leans on.

All bearings here are compass azimuths in degrees: 0 = north, 90 = east,
clockwise. Two conventions coexist in the data we consume and mixing them up is
the easiest way to get a beach model backwards, so they are named explicitly
throughout:

  *from* bearings  - where wind and waves come FROM (meteorological convention,
                     what Open-Meteo reports for wind_direction and
                     wave_direction).
  *toward* bearings - where water goes TO (ocean currents, longshore drift).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .fetch import FetchError, get_json

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
IP_LOOKUP_URL = "https://ipapi.co/json/"

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    name: str | None = None
    country: str | None = None
    admin: str | None = None
    source: str = "explicit"

    def label(self) -> str:
        if not self.name:
            return f"{self.latitude:.4f}, {self.longitude:.4f}"
        parts = [self.name]
        if self.admin and self.admin != self.name:
            parts.append(self.admin)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


def validate(latitude: float, longitude: float) -> tuple[float, float]:
    if not -90.0 <= latitude <= 90.0:
        raise ValueError(f"latitude {latitude} is outside -90..90")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"longitude {longitude} is outside -180..180")
    return float(latitude), float(longitude)


# --------------------------------------------------------------------------
# angle helpers
# --------------------------------------------------------------------------

def wrap360(degrees: float) -> float:
    """Normalise an azimuth into [0, 360).

    The explicit 360 check is not redundant: a tiny negative input rounds up to
    exactly 360.0 under float modulo, which would let a bearing escape the
    range this function exists to guarantee.
    """
    value = degrees % 360.0
    return 0.0 if value >= 360.0 else value


def wrap180(degrees: float) -> float:
    """Normalise a bearing difference into (-180, 180]."""
    value = (degrees + 180.0) % 360.0 - 180.0
    return 180.0 if value == -180.0 else value


def angle_between(a: float, b: float) -> float:
    """Unsigned separation between two azimuths, 0..180."""
    return abs(wrap180(a - b))


def compass_point(degrees: float) -> str:
    """16-point compass label, the form a beach report would actually print."""
    names = (
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
    )
    index = int((wrap360(degrees) + 11.25) // 22.5) % 16
    return names[index]


def mean_bearing(bearings: list[float], weights: list[float] | None = None) -> float | None:
    """Circular mean. Averaging 350 and 10 has to give 0, not 180."""
    if not bearings:
        return None
    if weights is None:
        weights = [1.0] * len(bearings)
    x = sum(w * math.cos(math.radians(b)) for b, w in zip(bearings, weights))
    y = sum(w * math.sin(math.radians(b)) for b, w in zip(bearings, weights))
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return None  # Vectors cancel: the mean direction is undefined.
    return wrap360(math.degrees(math.atan2(y, x)))


def bearing_spread(bearings: list[float]) -> float:
    """0 for perfectly aligned bearings, 1 for uniformly scattered ones.

    This is 1 - R, the circular variance, and it is what tells us whether a
    shoreline estimate came from a clean open coast or from a confused corner
    of a bay.
    """
    if not bearings:
        return 1.0
    n = len(bearings)
    x = sum(math.cos(math.radians(b)) for b in bearings) / n
    y = sum(math.sin(math.radians(b)) for b in bearings) / n
    return max(0.0, min(1.0, 1.0 - math.hypot(x, y)))


def offset(latitude: float, longitude: float, bearing: float, distance_m: float) -> tuple[float, float]:
    """Point at a bearing and distance from a start point (spherical earth)."""
    lat1 = math.radians(latitude)
    lon1 = math.radians(longitude)
    theta = math.radians(bearing)
    delta = distance_m / EARTH_RADIUS_M

    lat2 = math.asin(
        math.sin(lat1) * math.cos(delta) + math.cos(lat1) * math.sin(delta) * math.cos(theta)
    )
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(lat1),
        math.cos(delta) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), wrap180(math.degrees(lon2))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


# --------------------------------------------------------------------------
# place lookup
# --------------------------------------------------------------------------

def geocode(query: str, *, count: int = 1) -> list[Location]:
    """Resolve a place name to coordinates."""
    payload = get_json(GEOCODE_URL, {"name": query, "count": max(1, count), "format": "json"},
                       cache_ttl=86_400.0)
    results = payload.get("results") or []
    located = []
    for item in results:
        try:
            lat, lon = validate(item["latitude"], item["longitude"])
        except (KeyError, ValueError):
            continue
        located.append(
            Location(
                latitude=lat,
                longitude=lon,
                name=item.get("name"),
                country=item.get("country"),
                admin=item.get("admin1"),
                source="geocode",
            )
        )
    if not located:
        raise FetchError(GEOCODE_URL, f"no place matched {query!r}")
    return located


def locate_by_ip() -> Location:
    """Rough fallback when there is no GPS and no place name.

    IP geolocation lands you in the right city, not on the right beach. The web
    UI's browser geolocation is the accurate path; this exists so the CLI still
    does something sensible with no arguments.
    """
    payload = get_json(IP_LOOKUP_URL, {}, cache_ttl=3600.0)
    if payload.get("error"):
        raise FetchError(IP_LOOKUP_URL, str(payload.get("reason", "IP lookup failed")))
    try:
        lat, lon = validate(float(payload["latitude"]), float(payload["longitude"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise FetchError(IP_LOOKUP_URL, "IP lookup returned no coordinates") from exc
    return Location(
        latitude=lat,
        longitude=lon,
        name=payload.get("city"),
        admin=payload.get("region"),
        country=payload.get("country_name"),
        source="ip",
    )
