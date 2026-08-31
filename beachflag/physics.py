"""Surf-zone physics.

Pure functions, no I/O, no scoring decisions - just the standard coastal
engineering relations that turn an offshore forecast into the numbers a
lifeguard actually cares about: how big the waves break, how fast the water
moves along the beach and out through a rip, and what shape the surf zone is in.

Sources for the relations used here:
  Komar & Gaughan (1972)   breaker height from deep-water height and period
  Snell's law + linear theory  wave refraction into the surf zone
  Komar (1979)             longshore current from breaker height and angle
  Battjes (1974)           surf similarity (Iribarren) number
  Wright & Short (1984)    dimensionless fall velocity and beach state
"""

from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.80665
RHO_SEAWATER = 1025.0

# Depth-limited breaking: waves break when height reaches ~0.78 of water depth.
BREAKER_INDEX = 0.78

# Settling velocity of the sand grain, m/s. 0.03 is medium sand (~0.25 mm),
# the default for an ordinary sandy surf beach.
DEFAULT_FALL_VELOCITY = 0.03


@dataclass(frozen=True)
class BeachProfile:
    """The fixed, local part of the problem: what the beach itself is like."""

    name: str
    slope: float
    """tan(beta) of the beach face and inner surf zone."""

    fall_velocity: float = DEFAULT_FALL_VELOCITY
    rip_channels: float = 1.0
    """Multiplier for how rip-prone the bar morphology is. 1.0 = ordinary
    sandy beach with intermittent rip channels; 0 = a beach that cannot form
    them (a seawall, a reef platform, a shingle steep-face)."""

    tidal_sensitivity: float = 1.0
    """How strongly the tide reshapes the hazard. High on wide flat beaches
    with bars that dry out, low on steep or micro-tidal beaches."""


BEACH_PROFILES: dict[str, BeachProfile] = {
    # Wide, flat, fine sand. Waves spill a long way out; big dissipative surf
    # zones with strong, wide rip circulation.
    "dissipative": BeachProfile("dissipative sand", slope=0.015, fall_velocity=0.02,
                                rip_channels=1.15, tidal_sensitivity=1.3),
    # The default: a normal sandy beach with bars and rip channels. This is the
    # morphology that drowns the most people.
    "sandy": BeachProfile("sandy bar-and-rip", slope=0.03, fall_velocity=0.03,
                          rip_channels=1.0, tidal_sensitivity=1.0),
    # Steep coarse sand or shingle: waves surge on the face, plunging shore
    # break and undertow rather than rip channels.
    "steep": BeachProfile("steep / shingle", slope=0.10, fall_velocity=0.07,
                          rip_channels=0.35, tidal_sensitivity=0.6),
    # Reef or rock platform with a lagoon: breaks offshore, channelised outflow.
    "reef": BeachProfile("reef / rock platform", slope=0.05, fall_velocity=0.05,
                         rip_channels=0.9, tidal_sensitivity=1.2),
    # Enclosed, near-tideless, wave-poor: a lake shore or a sheltered bay.
    "sheltered": BeachProfile("sheltered bay", slope=0.04, fall_velocity=0.03,
                              rip_channels=0.2, tidal_sensitivity=0.4),
}

DEFAULT_PROFILE = BEACH_PROFILES["sandy"]


def deep_water_wavelength(period: float) -> float:
    """L0 = gT^2 / 2pi, metres."""
    return G * period * period / (2.0 * math.pi)


def deep_water_celerity(period: float) -> float:
    """C0 = gT / 2pi, m/s."""
    return G * period / (2.0 * math.pi)


def wave_energy_flux(height: float, period: float) -> float:
    """Deep-water energy flux (wave power) in kW per metre of crest.

    P = rho g^2 Hs^2 Tp / (64 pi). This is the honest measure of "how much
    ocean is arriving": it goes as the square of height and linearly with
    period, which is why a long-period 1.5 m groundswell is a different animal
    from 1.5 m of local windchop.
    """
    if height <= 0 or period <= 0:
        return 0.0
    watts_per_m = RHO_SEAWATER * G * G * height * height * period / (64.0 * math.pi)
    return watts_per_m / 1000.0


def breaker_height(deep_height: float, period: float) -> float:
    """Komar & Gaughan: Hb = 0.39 g^0.2 (T H0^2)^0.4, metres.

    Shoaling typically amplifies an offshore swell by 1.3-1.5x before it
    breaks, and this is the number that matters for a swimmer - not the
    offshore significant height the forecast reports.
    """
    if deep_height <= 0 or period <= 0:
        return 0.0
    return 0.39 * (G ** 0.2) * ((period * deep_height * deep_height) ** 0.4)


def breaker_depth(break_height: float) -> float:
    """Water depth at the break point, metres."""
    return break_height / BREAKER_INDEX if break_height > 0 else 0.0


def surf_zone_width(break_height: float, slope: float) -> float:
    """Roughly how far out the whitewater extends, metres."""
    if break_height <= 0 or slope <= 0:
        return 0.0
    return breaker_depth(break_height) / slope


def breaker_angle(deep_angle_deg: float, period: float, break_height: float) -> float:
    """Refract a deep-water approach angle in to the break point (Snell).

    sin(a_b) / sin(a_0) = C_b / C_0. Refraction always bends waves toward
    shore-normal, so a 40-degree offshore approach may arrive at 10 degrees -
    which is exactly why you cannot read longshore drift off the offshore
    wave direction.
    """
    if period <= 0 or break_height <= 0:
        return 0.0
    deep_angle = max(-89.9, min(89.9, deep_angle_deg))
    celerity_break = math.sqrt(G * breaker_depth(break_height))
    ratio = celerity_break / deep_water_celerity(period)
    sin_b = math.sin(math.radians(deep_angle)) * min(1.0, ratio)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_b))))


def longshore_current(break_height: float, break_angle_deg: float) -> float:
    """Komar (1979): V = 1.17 sqrt(g Hb) sin(ab) cos(ab), m/s.

    The current that walks you down the beach away from your towel, and away
    from the flagged area. Peaks at a 45-degree breaker angle, which almost
    never happens after refraction - real values sit under ~1 m/s.
    """
    if break_height <= 0:
        return 0.0
    angle = math.radians(break_angle_deg)
    return 1.17 * math.sqrt(G * break_height) * abs(math.sin(angle) * math.cos(angle))


def iribarren(slope: float, break_height: float, period: float) -> float:
    """Surf similarity number at breaking. <0.4 spilling, 0.4-2 plunging, >2 surging."""
    if break_height <= 0 or period <= 0 or slope <= 0:
        return 0.0
    return slope / math.sqrt(break_height / deep_water_wavelength(period))


def breaker_type(iribarren_number: float) -> str:
    if iribarren_number <= 0:
        return "no surf"
    if iribarren_number < 0.4:
        return "spilling"
    if iribarren_number < 2.0:
        return "plunging"
    return "surging"


def dimensionless_fall_velocity(break_height: float, period: float, fall_velocity: float) -> float:
    """Omega = Hb / (ws T), the Wright & Short beach state index.

    Omega < 1 reflective, 1-6 intermediate, > 6 dissipative. The intermediate
    band is where bar-and-rip morphology lives, so this is what tells us
    whether the beach is currently built to produce rip currents at all.
    """
    if break_height <= 0 or period <= 0 or fall_velocity <= 0:
        return 0.0
    return break_height / (fall_velocity * period)


def beach_state(omega: float) -> str:
    if omega <= 0:
        return "no surf"
    if omega < 1.0:
        return "reflective"
    if omega < 6.0:
        return "intermediate (bar and rip)"
    return "dissipative"


def rip_morphology_factor(omega: float) -> float:
    """0..1 for how well the current beach state supports rip channels.

    Reflective beaches are too steep to hold a bar; fully dissipative beaches
    saturate into a uniform surf zone. Rip cells peak in the intermediate
    band, around Omega ~ 3.
    """
    if omega <= 0:
        return 0.0
    if omega < 1.0:
        return 0.25 * omega
    if omega <= 5.0:
        return 0.25 + 0.75 * min(1.0, (omega - 1.0) / 2.0)
    return max(0.45, 1.0 - (omega - 5.0) / 10.0)


def rip_speed(break_height: float, morphology: float, forcing: float) -> float:
    """Coarse estimate of mean rip flow in m/s.

    Scales as sqrt(g Hb) - the surf-zone velocity scale - modulated by whether
    the beach has channels (`morphology`) and whether today's forcing is
    feeding them (`forcing`, 0..1). Real rips pulse: the peak is roughly
    1.6x this mean, which is the number worth comparing to a swimmer's pace.

    For reference, a competent recreational swimmer holds about 0.5 m/s, and
    an unhurried freestyle sprint is around 1.2 m/s. A rip only has to beat
    the first of those to be lethal.
    """
    if break_height <= 0:
        return 0.0
    scale = 0.20 * math.sqrt(G * break_height)
    return scale * max(0.0, min(1.0, morphology)) * max(0.0, min(1.2, forcing))


def wind_components(speed: float, wind_from_deg: float, facing_deg: float) -> tuple[float, float]:
    """Split wind into (onshore, alongshore) components in the beach's frame.

    Onshore is positive when the wind blows from the sea onto the beach, and
    negative for an offshore wind - the direction that flattens the surf and
    quietly carries inflatables, boards and small children out to sea.

    Alongshore is positive when the push runs toward `facing - 90` (your left
    as you look out to sea), matching the sign used for longshore drift.
    """
    delta = math.radians(wind_from_deg - facing_deg)
    return speed * math.cos(delta), speed * math.sin(delta)
