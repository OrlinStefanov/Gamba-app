"""Work out which way the beach faces, from terrain alone.

Every wave and wind term in the model is relative to the shore normal, so the
single most important local parameter is the azimuth the beach looks out along.
Asking the user for it is unreliable; instead we probe the digital elevation
model on rings around the point, call every sample at or below sea level water,
and take the circular mean of the bearings that landed in water. That vector is
the seaward shore normal.

The same sample set answers two more questions for free: how sheltered the spot
is (what fraction of the horizon is open water), and which swell directions are
blocked by land before they can arrive.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .fetch import FetchError, get_json
from .geo import angle_between, bearing_spread, mean_bearing, offset, wrap360

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# 24 bearings x 4 radii = 96 samples, inside the API's 100-coordinate limit.
PROBE_BEARINGS = tuple(float(b) for b in range(0, 360, 15))
PROBE_RADII_M = (500.0, 1200.0, 2500.0, 5000.0)

# Copernicus DEM reports a flat 0 m over ocean, so anything at or below the
# geoid is water for our purposes.
SEA_LEVEL_THRESHOLD_M = 0.0


@dataclass(frozen=True)
class Shoreline:
    """Local coastal geometry derived from terrain samples."""

    facing: float
    """Azimuth of the outward shore normal - the way you look out to sea."""

    confidence: float
    """0..1. Low on a headland or in a tight bay, high on a straight open coast."""

    open_water_fraction: float
    """Share of probed bearings that reach water. ~0.5 for a straight coast."""

    nearest_water_m: float | None
    """Distance to the closest water sample, or None if nothing was found."""

    exposed_bearings: tuple[float, ...] = field(default=(), repr=False)
    """Bearings whose outermost ring is water: swell from these can arrive."""

    source: str = "terrain"

    @property
    def is_sheltered(self) -> bool:
        """A cove or harbour: much less than half the horizon is open water."""
        return self.open_water_fraction < 0.28

    def shelter_factor(self, wave_from: float) -> float:
        """0..1 for how much of a swell from `wave_from` survives the approach.

        This is land blocking only - a headland, an island, or the beach's own
        back shore. The obliquity of the approach is a separate term that the
        model applies itself, so keeping the two apart avoids counting the
        angle twice.
        """
        if angle_between(wave_from, self.facing) >= 100.0:
            return 0.0  # Coming from over the land behind the beach.
        if not self.exposed_bearings:
            return 1.0
        nearest_open = min(angle_between(wave_from, b) for b in self.exposed_bearings)
        if nearest_open <= 15.0:
            return 1.0
        if nearest_open >= 45.0:
            return 0.15  # Land between here and the swell: refracted scraps only.
        return 1.0 - 0.85 * (nearest_open - 15.0) / 30.0


def assumed_shoreline(facing: float) -> Shoreline:
    """Shoreline for a user-supplied facing, when terrain probing is skipped."""
    facing = wrap360(facing)
    return Shoreline(
        facing=facing,
        confidence=1.0,
        open_water_fraction=0.5,
        nearest_water_m=0.0,
        exposed_bearings=tuple(
            wrap360(facing + d) for d in range(-75, 80, 15)
        ),
        source="user",
    )


def probe_points(latitude: float, longitude: float) -> list[tuple[float, float, float, float]]:
    """(lat, lon, bearing, radius) for every terrain sample we will request."""
    points = []
    for radius in PROBE_RADII_M:
        for bearing in PROBE_BEARINGS:
            lat, lon = offset(latitude, longitude, bearing, radius)
            points.append((lat, lon, bearing, radius))
    return points


def detect(latitude: float, longitude: float, *, timeout: float = 20.0) -> Shoreline:
    """Probe terrain around a point and derive the shore normal."""
    points = probe_points(latitude, longitude)
    payload = get_json(
        ELEVATION_URL,
        {
            "latitude": [p[0] for p in points],
            "longitude": [p[1] for p in points],
        },
        timeout=timeout,
        cache_ttl=30 * 86_400.0,  # Coastlines do not move on a human timescale.
    )
    elevations = payload.get("elevation")
    if not isinstance(elevations, list) or len(elevations) != len(points):
        raise FetchError(ELEVATION_URL, "elevation API returned an unexpected shape")
    return from_elevations(points, elevations)


def from_elevations(
    points: list[tuple[float, float, float, float]],
    elevations: list[float | None],
) -> Shoreline:
    """Turn terrain samples into a Shoreline. Split out so it is testable offline."""
    water_bearings: list[float] = []
    water_weights: list[float] = []
    nearest_water: float | None = None
    outer_radius = max(p[3] for p in points)
    exposed: list[float] = []
    usable = 0

    for (_, _, bearing, radius), elevation in zip(points, elevations):
        if elevation is None:
            continue
        usable += 1
        if elevation > SEA_LEVEL_THRESHOLD_M:
            continue
        water_bearings.append(bearing)
        # Near samples describe this beach; far ones describe the coast. Weight
        # toward the near rings, but keep the far ones for stability.
        water_weights.append(1.0 / math.sqrt(radius))
        if nearest_water is None or radius < nearest_water:
            nearest_water = radius
        if radius >= outer_radius:
            exposed.append(bearing)

    if usable == 0:
        raise FetchError(ELEVATION_URL, "elevation API returned no usable samples")
    if not water_bearings:
        raise FetchError(
            ELEVATION_URL,
            f"no sea within {outer_radius / 1000:.0f} km - this looks like an inland location",
        )

    facing = mean_bearing(water_bearings, water_weights)
    if facing is None:
        raise FetchError(
            ELEVATION_URL,
            "water surrounds this point evenly - pass --facing to say which way the beach looks",
        )

    open_fraction = len(water_bearings) / usable
    confidence = _confidence(water_bearings, open_fraction, nearest_water)

    return Shoreline(
        facing=facing,
        confidence=confidence,
        open_water_fraction=open_fraction,
        nearest_water_m=nearest_water,
        exposed_bearings=tuple(sorted(set(exposed))),
    )


def _confidence(water_bearings: list[float], open_fraction: float, nearest_water: float | None) -> float:
    """How much to trust the derived shore normal.

    A straight open coast puts water across a coherent half of the horizon and
    gives a sharp answer. A headland (water almost all round) or a creek mouth
    (water in a thin sliver) does not, and the model should say so rather than
    quietly pretend.
    """
    # Water spread over exactly half the horizon is the textbook straight beach.
    geometry = 1.0 - min(1.0, abs(open_fraction - 0.5) / 0.42)

    # The circular spread of water bearings peaks near 1 - 2/pi ~ 0.36 for a
    # clean half-plane of water; much tighter or much wider is a messier coast.
    spread = bearing_spread(water_bearings)
    coherence = 1.0 - min(1.0, abs(spread - 0.36) / 0.36)

    proximity = 1.0
    if nearest_water is None:
        proximity = 0.0
    elif nearest_water > 1500.0:
        # The nearest water is kilometres away; we are not standing on a beach.
        proximity = max(0.15, 1.0 - (nearest_water - 1500.0) / 4000.0)

    score = 0.45 * geometry + 0.35 * coherence + 0.20 * proximity
    return round(max(0.05, min(1.0, score)), 3)
