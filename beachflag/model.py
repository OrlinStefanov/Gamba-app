"""The prediction itself: conditions in, flag out.

The model is deliberately not a black box. Every hazard is scored on its own
0-100 scale and, separately, states the lowest flag it alone would justify. The
final flag is the worst of those demands, with an escalation rule for the case
where several moderate hazards stack up. That structure is what lets the CLI
show you *why* it said red, and lets you disagree with one term without having
to distrust the whole answer.

The thresholds are calibrated against ordinary lifeguard practice (surf height
bands, USLA rip guidance, the wind and lightning rules most services use) and
against the physics in `physics.py`. They are not the decision procedure of any
particular beach patrol, and the model cannot see the two things a lifeguard
sees best: the sandbar in front of them today, and the crowd in the water.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time as dtime

from . import physics
from .flags import Advisory, Flag
from .geo import compass_point, wrap180
from .numfmt import fmt
from .physics import BeachProfile, DEFAULT_PROFILE
from .shoreline import Shoreline
from .sources import Forecast, Hour

# Typical lifeguard patrol window, used only for the "nobody is watching"
# advisory. Overridable from the CLI.
DEFAULT_PATROL = (dtime(10, 0), dtime(18, 0))


@dataclass(frozen=True)
class Swimmer:
    """Who is going in. Changes the advice and the personal verdict, never the flag."""

    key: str
    label: str
    swim_speed: float          # m/s sustained, for comparison against rip flow
    surf_tolerance: float      # metres of breaking wave they can handle
    cold_tolerance: float      # degC of sea temperature they can enter unprotected


SWIMMERS: dict[str, Swimmer] = {
    "strong": Swimmer("strong", "strong swimmer", 1.1, 1.8, 12.0),
    "average": Swimmer("average", "average swimmer", 0.7, 1.0, 16.0),
    "weak": Swimmer("weak", "weak swimmer", 0.4, 0.5, 19.0),
    "child": Swimmer("child", "child", 0.25, 0.3, 20.0),
    "nonswimmer": Swimmer("nonswimmer", "non-swimmer", 0.1, 0.2, 21.0),
}
DEFAULT_SWIMMER = SWIMMERS["average"]


@dataclass(frozen=True)
class Metrics:
    """Everything the physics layer derived, kept for display and explanation."""

    breaker_height: float = 0.0
    breaker_type: str = "no surf"
    breaker_angle: float = 0.0
    deep_height: float = 0.0
    deep_period: float = 0.0
    deep_direction: float | None = None
    wave_power: float = 0.0
    surf_zone_width: float = 0.0
    iribarren: float = 0.0
    omega: float = 0.0
    beach_state: str = "no surf"
    longshore_current: float = 0.0
    longshore_toward: float | None = None
    rip_speed: float = 0.0
    rip_peak: float = 0.0
    onshore_wind: float = 0.0
    alongshore_wind: float = 0.0
    shelter: float = 1.0
    incidence: float = 0.0


@dataclass(frozen=True)
class Driver:
    """One hazard, scored and explained."""

    key: str
    label: str
    score: float
    demand: Flag
    detail: str

    @property
    def is_notable(self) -> bool:
        return self.score >= 20.0 or self.demand > Flag.GREEN


@dataclass(frozen=True)
class Prediction:
    time: datetime
    flag: Flag
    score: float
    confidence: float
    drivers: tuple[Driver, ...]
    advisories: tuple[Advisory, ...]
    metrics: Metrics
    headline: str
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=(), repr=False)

    def top_drivers(self, limit: int = 4) -> tuple[Driver, ...]:
        ranked = sorted(self.drivers, key=lambda d: (-d.demand, -d.score))
        return tuple(d for d in ranked if d.is_notable)[:limit]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ramp(value: float, start: float, end: float) -> float:
    """0 below `start`, 1 above `end`, linear between."""
    if end <= start:
        return 1.0 if value >= end else 0.0
    return clamp((value - start) / (end - start))


def ladder(value: float, yellow: float, red: float, double_red: float) -> Flag:
    """Map a magnitude onto the flag it alone justifies."""
    if value >= double_red:
        return Flag.DOUBLE_RED
    if value >= red:
        return Flag.RED
    if value >= yellow:
        return Flag.YELLOW
    return Flag.GREEN


# --------------------------------------------------------------------------
# derived physics for one hour
# --------------------------------------------------------------------------

def derive(hour: Hour, shore: Shoreline, profile: BeachProfile) -> Metrics:
    height, period, direction = hour.dominant_swell()
    onshore = alongshore = 0.0
    if hour.wind_speed is not None and hour.wind_direction is not None:
        onshore, alongshore = physics.wind_components(
            hour.wind_speed, hour.wind_direction, shore.facing
        )

    if not height or not period:
        return Metrics(onshore_wind=onshore, alongshore_wind=alongshore)

    shelter = shore.shelter_factor(direction) if direction is not None else 1.0
    incidence = wrap180(direction - shore.facing) if direction is not None else 0.0

    # Energy flux onto a straight shoreline falls as cos(angle of approach), so
    # the equivalent shore-normal height scales as its square root.
    obliquity = math.sqrt(max(0.0, math.cos(math.radians(min(abs(incidence), 89.0)))))
    effective_height = height * shelter * obliquity

    break_height = physics.breaker_height(effective_height, period)
    break_angle = physics.breaker_angle(incidence, period, break_height)
    omega = physics.dimensionless_fall_velocity(break_height, period, profile.fall_velocity)
    morphology = physics.rip_morphology_factor(omega) * profile.rip_channels

    forcing = _rip_forcing(hour, break_height, period, incidence, onshore, profile)
    mean_rip = physics.rip_speed(break_height, morphology, forcing)

    longshore = physics.longshore_current(break_height, break_angle)
    drift_toward = None
    if longshore > 0.05 and abs(break_angle) > 0.5:
        # Waves from the right of the shore normal drive drift to the left.
        drift_toward = (shore.facing - 90.0 * (1 if incidence > 0 else -1)) % 360.0

    return Metrics(
        breaker_height=break_height,
        breaker_type=physics.breaker_type(physics.iribarren(profile.slope, break_height, period)),
        breaker_angle=break_angle,
        deep_height=height,
        deep_period=period,
        deep_direction=direction,
        wave_power=physics.wave_energy_flux(effective_height, period),
        surf_zone_width=physics.surf_zone_width(break_height, profile.slope),
        iribarren=physics.iribarren(profile.slope, break_height, period),
        omega=omega,
        beach_state=physics.beach_state(omega),
        longshore_current=longshore,
        longshore_toward=drift_toward,
        rip_speed=mean_rip,
        rip_peak=mean_rip * 1.6,
        onshore_wind=onshore,
        alongshore_wind=alongshore,
        shelter=shelter,
        incidence=incidence,
    )


def _rip_forcing(
    hour: Hour,
    break_height: float,
    period: float,
    incidence: float,
    onshore: float,
    profile: BeachProfile,
) -> float:
    """0..1.2 for how hard today's conditions drive rip circulation.

    Four things feed a rip: enough breaking energy to pile water on the bar,
    waves arriving near shore-normal (oblique swell spends itself on longshore
    drift instead of on rip cells), long enough period to reach the bar, and a
    tide low enough that the bar is actually doing the damming.
    """
    energy = ramp(break_height, 0.25, 1.6)

    # Shore-normal weighting: full at 0 degrees, ~0.3 by 55 degrees.
    normality = clamp(math.cos(math.radians(min(abs(incidence), 90.0))) ** 2, 0.15, 1.0)

    period_gain = 0.65 + 0.35 * ramp(period, 5.0, 12.0)

    tide_gain = 1.0
    if hour.tide_phase is not None:
        # Low tide concentrates flow through the channels; mid-falling is the
        # window lifeguards watch hardest.
        low_bonus = (1.0 - hour.tide_phase) * 0.45 * profile.tidal_sensitivity
        falling_bonus = 0.10 * profile.tidal_sensitivity if hour.tide_rising is False else 0.0
        tide_gain = 1.0 + low_bonus + falling_bonus

    wind_gain = 1.0 + 0.15 * ramp(onshore, 3.0, 12.0)

    return clamp(energy * normality * period_gain * tide_gain * wind_gain, 0.0, 1.2)


# --------------------------------------------------------------------------
# hazard drivers
# --------------------------------------------------------------------------

def _surf_driver(m: Metrics) -> Driver:
    hb = m.breaker_height
    score = 100.0 * ramp(hb, 0.15, 2.8)
    demand = ladder(hb, yellow=0.55, red=1.25, double_red=2.60)

    if hb < 0.1:
        detail = "Flat - no meaningful surf."
    else:
        detail = (
            f"{fmt(hb, 1)} m {m.breaker_type} breakers from a {fmt(m.deep_height, 1)} m / "
            f"{fmt(m.deep_period, 0)} s swell; surf zone about {fmt(m.surf_zone_width, 0)} m wide."
        )
        if m.shelter < 0.5:
            detail += " Mostly blocked by land, so much of that swell never arrives."
    return Driver("surf", "Surf", score, demand, detail)


def _shorebreak_driver(m: Metrics, profile: BeachProfile) -> Driver:
    """Plunging waves on a steep face: the neck and spine injury mechanism.

    A 1 m dumping shore break on a steep beach hurts more people than 2 m of
    spilling surf on a flat one, and no wave-height threshold on its own sees
    that difference - it takes the Iribarren number.
    """
    if m.breaker_height < 0.3 or m.iribarren < 0.6:
        return Driver("shorebreak", "Shore break", 0.0, Flag.GREEN, "No significant shore dump.")

    severity = ramp(m.iribarren, 0.6, 2.2) * ramp(m.breaker_height, 0.3, 1.6)
    score = 100.0 * severity
    demand = ladder(severity, yellow=0.25, red=0.6, double_red=1.01)
    detail = (
        f"Waves are {m.breaker_type} onto a {profile.name} profile "
        f"(Iribarren {fmt(m.iribarren, 1)}) - a dumping shore break that injures necks and shoulders."
    )
    return Driver("shorebreak", "Shore break", score, demand, detail)


def _rip_driver(m: Metrics, swimmer: Swimmer) -> Driver:
    peak = m.rip_peak
    score = 100.0 * ramp(peak, 0.15, 1.6)
    demand = ladder(peak, yellow=0.35, red=0.80, double_red=1.60)

    if peak < 0.15:
        detail = "Little to drive rip currents."
    else:
        comparison = (
            "faster than you can swim against"
            if peak > swimmer.swim_speed
            else "within what you could swim against, briefly"
        )
        detail = (
            f"Rip channels running about {fmt(m.rip_speed, 1)} m/s, pulsing to {fmt(peak, 1)} m/s "
            f"- {comparison}. Beach state: {m.beach_state}."
        )
    return Driver("rip", "Rip currents", score, demand, detail)


def _longshore_driver(m: Metrics) -> Driver:
    v = m.longshore_current
    score = 100.0 * ramp(v, 0.15, 1.1)
    demand = ladder(v, yellow=0.30, red=0.70, double_red=1.30)
    if v < 0.15:
        detail = "No appreciable longshore drift."
    else:
        toward = f" toward the {compass_point(m.longshore_toward)}" if m.longshore_toward is not None else ""
        detail = (
            f"Longshore current about {fmt(v, 1)} m/s{toward} (breakers arriving at "
            f"{fmt(abs(m.breaker_angle), 0)} deg) - it will walk you down the beach."
        )
    return Driver("longshore", "Longshore drift", score, demand, detail)


def _wind_driver(hour: Hour, m: Metrics) -> Driver:
    gusts = hour.wind_gusts or hour.wind_speed or 0.0
    speed = hour.wind_speed or 0.0

    score = 100.0 * max(ramp(gusts, 8.0, 22.0), ramp(abs(m.onshore_wind), 6.0, 18.0))
    demand = ladder(gusts, yellow=11.0, red=18.0, double_red=26.0)

    if speed < 2.0:
        detail = "Light and variable wind."
    elif m.onshore_wind < -4.0:
        detail = (
            f"{fmt(abs(m.onshore_wind), 0)} m/s offshore wind: it flattens the surf but pushes "
            "anything floating - inflatables, boards, air beds - straight out to sea."
        )
        demand = max(demand, Flag.YELLOW)
    elif m.onshore_wind > 8.0:
        detail = f"{fmt(m.onshore_wind, 0)} m/s onshore wind piling up choppy, disorganised surf."
    else:
        direction = compass_point(hour.wind_direction) if hour.wind_direction is not None else "?"
        detail = f"{fmt(speed, 0)} m/s wind from the {direction}, gusting {fmt(gusts, 0)} m/s."
    return Driver("wind", "Wind", score, demand, detail)


def _current_driver(hour: Hour) -> Driver:
    speed = hour.current_speed or 0.0
    score = 100.0 * ramp(speed, 0.2, 1.2)
    demand = ladder(speed, yellow=0.40, red=0.90, double_red=1.60)
    if speed < 0.15:
        detail = "Ambient current is negligible."
    else:
        toward = (
            f" setting {compass_point(hour.current_direction)}"
            if hour.current_direction is not None
            else ""
        )
        detail = f"Background ocean current {fmt(speed, 1)} m/s{toward}, on top of anything the surf does."
    return Driver("current", "Ocean current", score, demand, detail)


def _thermal_driver(hour: Hour, swimmer: Swimmer) -> Driver:
    sst = hour.sea_temperature
    if sst is None:
        return Driver("thermal", "Water temperature", 0.0, Flag.GREEN, "No sea temperature available.")

    # Below 15 degC the cold shock response - gasp, hyperventilation, rapid
    # incapacitation - is the drowning mechanism, not the swim itself.
    score = 100.0 * ramp(18.0 - sst, 0.0, 12.0)
    demand = Flag.GREEN
    if sst < 10.0:
        demand = Flag.RED
    elif sst < 15.0:
        demand = Flag.YELLOW

    if sst >= 22.0:
        detail = f"Sea {fmt(sst, 0)} C - comfortable."
    elif sst >= 18.0:
        detail = f"Sea {fmt(sst, 0)} C - brisk but fine for a swim."
    elif sst >= 15.0:
        detail = f"Sea {fmt(sst, 0)} C - cold enough to shorten how long you last in it."
    elif sst >= 10.0:
        detail = f"Sea {fmt(sst, 0)} C - cold shock territory. Enter slowly, or wear a wetsuit."
    else:
        detail = f"Sea {fmt(sst, 0)} C - cold water shock is the real hazard here, whatever the surf does."

    if sst < swimmer.cold_tolerance:
        detail += f" Below what an unprotected {swimmer.label} should be entering."
    if hour.air_temperature is not None and hour.air_temperature - sst > 12.0:
        detail += " Hot air over cold water makes it feel far milder than it is."
    return Driver("thermal", "Water temperature", score, demand, detail)


def _storm_driver(hour: Hour) -> Driver:
    """Lightning is the one hazard where the water empties regardless of surf."""
    if hour.is_thunderstorm:
        return Driver(
            "storm",
            "Thunderstorms",
            100.0,
            Flag.DOUBLE_RED,
            "Thunderstorms overhead. Water is cleared for lightning - open water is the worst "
            "place to be, and beaches close for this even in flat calm.",
        )

    cape = hour.cape or 0.0
    probability = hour.precipitation_probability or 0.0
    instability = ramp(cape, 800.0, 2500.0) * ramp(probability, 30.0, 80.0)
    visibility = hour.visibility

    score = 100.0 * instability
    demand = Flag.GREEN
    details = []
    if instability > 0.45:
        demand = Flag.YELLOW
        details.append(
            f"Unstable air (CAPE {fmt(cape, 0)} J/kg, {fmt(probability, 0)}% rain) - thunderstorms "
            "could build with little warning."
        )
    if visibility is not None and visibility < 2000:
        score = max(score, 100.0 * ramp(2000 - visibility, 0.0, 1500.0))
        demand = max(demand, Flag.YELLOW if visibility < 1000 else Flag.GREEN)
        details.append(f"Visibility down to {fmt(visibility / 1000, 1)} km - a swimmer in trouble is hard to spot.")
    if not details:
        details.append("Settled weather.")
    return Driver("storm", "Weather", score, demand, " ".join(details))


def _water_quality_driver(hour: Hour) -> Driver:
    """Rain-driven runoff, the usual reason a clean beach fails a bathing test."""
    rain24 = hour.rain_24h
    rain48 = hour.rain_48h
    if rain24 is None and rain48 is None:
        return Driver("quality", "Water quality", 0.0, Flag.GREEN, "No rainfall history available.")

    r24 = rain24 or 0.0
    r48 = rain48 or 0.0
    score = 100.0 * max(ramp(r24, 8.0, 40.0), ramp(r48, 15.0, 60.0))
    demand = Flag.YELLOW if score >= 60 else Flag.GREEN
    if score < 20:
        detail = "No significant recent rain, so runoff is unlikely to be an issue."
    else:
        detail = (
            f"{fmt(r24, 0)} mm of rain in the last 24 h ({fmt(r48, 0)} mm over 48 h). Storm drains and "
            "rivers push bacteria onto beaches for a day or two after this - many services post "
            "a swim advisory."
        )
    return Driver("quality", "Water quality", score, demand, detail)


def _marine_life_driver(hour: Hour, m: Metrics, override: str | None) -> Driver:
    """Jellyfish and man-o-war, inferred rather than observed.

    No free API reports stings, so this is a seasonal-and-wind heuristic: warm
    water plus a persistent onshore wind is what puts drifting stingers on a
    beach. Treat it as a prompt to look at the sand for blue bubbles, not as an
    observation. `override` lets you state what the beach actually reported.
    """
    if override == "yes":
        return Driver("marine_life", "Marine life", 80.0, Flag.GREEN,
                      "Dangerous marine life reported (you told the model so).")
    if override == "no":
        return Driver("marine_life", "Marine life", 0.0, Flag.GREEN,
                      "No marine life reported (you told the model so).")

    sst = hour.sea_temperature
    if sst is None or sst < 20.0:
        return Driver("marine_life", "Marine life", 0.0, Flag.GREEN,
                      "Water too cool for the usual drifting stingers.")

    warmth = ramp(sst, 20.0, 27.0)
    onshore = ramp(m.onshore_wind, 3.0, 10.0)
    likelihood = warmth * onshore
    score = 60.0 * likelihood
    if likelihood < 0.35:
        return Driver("marine_life", "Marine life", score, Flag.GREEN,
                      "Nothing in the conditions particularly favours stingers today.")
    return Driver(
        "marine_life",
        "Marine life",
        score,
        Flag.GREEN,
        f"Warm water ({fmt(sst, 0)} C) with a steady onshore wind is what blows jellyfish and "
        "man-o-war ashore. Check the sand and the local notice board.",
    )


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------

# How much each hazard contributes to the aggregate score. Rips dominate
# because they cause the large majority of surf-beach rescues and drownings.
WEIGHTS: dict[str, float] = {
    "rip": 0.30,
    "surf": 0.22,
    "shorebreak": 0.10,
    "longshore": 0.10,
    "wind": 0.08,
    "current": 0.06,
    "thermal": 0.07,
    "storm": 0.05,
    "quality": 0.02,
    "marine_life": 0.00,  # Advisory only: never moves the hazard flag.
}


def predict(
    hour: Hour,
    shore: Shoreline,
    *,
    profile: BeachProfile = DEFAULT_PROFILE,
    swimmer: Swimmer = DEFAULT_SWIMMER,
    forecast: Forecast | None = None,
    marine_life: str | None = None,
    patrol: tuple[dtime, dtime] = DEFAULT_PATROL,
    lead_hours: float = 0.0,
) -> Prediction:
    """Score one hour of conditions and pick the flag."""
    m = derive(hour, shore, profile)

    drivers = (
        _rip_driver(m, swimmer),
        _surf_driver(m),
        _shorebreak_driver(m, profile),
        _longshore_driver(m),
        _wind_driver(hour, m),
        _current_driver(hour),
        _thermal_driver(hour, swimmer),
        _storm_driver(hour),
        _water_quality_driver(hour),
        _marine_life_driver(hour, m, marine_life),
    )
    by_key = {d.key: d for d in drivers}

    # Only weighted drivers can move the hazard flag. Advisory-only terms
    # (marine life) raise their own flag beside it and nothing more.
    hazards = [d for d in drivers if WEIGHTS.get(d.key, 0.0) > 0.0]

    total_weight = sum(WEIGHTS[d.key] for d in hazards)
    score = sum(WEIGHTS[d.key] * d.score for d in hazards) / total_weight
    score = round(min(100.0, score), 1)

    flag = max(d.demand for d in hazards)

    # Escalation: several moderate hazards at once is worse than any of them
    # alone. A day with head-high surf, a stiff onshore and a falling tide is
    # not a yellow-flag day just because no single term crossed red.
    moderate = sum(1 for d in hazards if d.demand >= Flag.YELLOW)
    if flag < Flag.RED and score >= 62.0 and moderate >= 2:
        flag = Flag.RED
    elif flag < Flag.YELLOW and score >= 34.0:
        flag = Flag.YELLOW

    advisories = _advisories(hour, m, by_key, forecast, patrol)
    reasons = tuple(d.detail for d in _ranked(drivers) if d.is_notable)[:4]

    return Prediction(
        time=hour.time,
        flag=flag,
        score=score,
        confidence=confidence(hour, shore, lead_hours),
        drivers=drivers,
        advisories=advisories,
        metrics=m,
        headline=_headline(flag, by_key, m),
        reasons=reasons,
    )


def _ranked(drivers: tuple[Driver, ...]) -> list[Driver]:
    return sorted(drivers, key=lambda d: (-d.demand, -d.score))


def _advisories(
    hour: Hour,
    m: Metrics,
    by_key: dict[str, Driver],
    forecast: Forecast | None,
    patrol: tuple[dtime, dtime],
) -> tuple[Advisory, ...]:
    found: list[Advisory] = []

    if hour.is_thunderstorm:
        found.append(Advisory.LIGHTNING)
    if by_key["marine_life"].score >= 20.0:
        found.append(Advisory.PURPLE)
    if by_key["quality"].score >= 35.0:
        found.append(Advisory.WATER_QUALITY)
    if hour.sea_temperature is not None and hour.sea_temperature < 15.0:
        found.append(Advisory.COLD_WATER)
    if m.onshore_wind <= -4.0:
        found.append(Advisory.OFFSHORE_WIND)
    if (hour.uv_index or 0.0) >= 8.0:
        found.append(Advisory.UV_EXTREME)

    clock = hour.time.time()
    outside_patrol = clock < patrol[0] or clock >= patrol[1]
    dark = hour.is_day is False
    if outside_patrol or dark:
        found.append(Advisory.NO_LIFEGUARD)

    return tuple(dict.fromkeys(found))


def _headline(flag: Flag, by_key: dict[str, Driver], m: Metrics) -> str:
    """One sentence naming the hazard that actually decided the flag."""
    deciding = max(
        (d for d in by_key.values() if WEIGHTS.get(d.key, 0.0) > 0.0),
        key=lambda d: (d.demand, d.score),
    )
    if flag == Flag.GREEN:
        if m.breaker_height < 0.15:
            return "Flat and calm - about as benign as a beach gets."
        return f"Small {fmt(m.breaker_height, 1)} m surf and nothing else of note."
    subject = {
        "rip": "rip currents",
        "surf": "the surf",
        "shorebreak": "the shore break",
        "longshore": "longshore drift",
        "wind": "the wind",
        "current": "the ocean current",
        "thermal": "the water temperature",
        "storm": "the weather",
        "quality": "water quality",
    }.get(deciding.key, deciding.label.lower())
    verb = {
        Flag.YELLOW: "makes this a swim-with-care day",
        Flag.RED: "is the reason to stay out of the water",
        Flag.DOUBLE_RED: "should have the beach closed",
    }[flag]
    return f"Driven by {subject}: it {verb}."


def confidence(hour: Hour, shore: Shoreline, lead_hours: float) -> float:
    """0..1 for how much the answer is worth.

    Three things degrade it: missing inputs, a shoreline we could not pin down,
    and forecast lead time. A prediction five days out from a headland with no
    marine data should not present itself the same way as one for this hour on
    a straight open coast.
    """
    wanted = (
        hour.wave_height, hour.wave_period, hour.wave_direction,
        hour.wind_speed, hour.wind_direction, hour.sea_temperature,
        hour.sea_level, hour.air_temperature,
    )
    completeness = sum(1 for v in wanted if v is not None) / len(wanted)

    # Marine models hold up well for a couple of days and drift after that.
    lead = 1.0 - 0.55 * clamp(lead_hours / 120.0)

    value = 0.45 * completeness + 0.30 * shore.confidence + 0.25 * lead
    return round(clamp(value), 3)


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------

def predict_series(
    forecast: Forecast,
    shore: Shoreline,
    start: datetime,
    hours: int,
    **kwargs,
) -> tuple[Prediction, ...]:
    """Predictions for a run of hours, with lead time folded into confidence."""
    out = []
    for hour in forecast.future(start, hours):
        lead = max(0.0, (hour.time - start).total_seconds() / 3600.0)
        out.append(predict(hour, shore, forecast=forecast, lead_hours=lead, **kwargs))
    return tuple(out)


def best_window(
    predictions: tuple[Prediction, ...],
    *,
    max_flag: Flag = Flag.YELLOW,
    daylight_only: bool = True,
    forecast: Forecast | None = None,
) -> tuple[datetime, datetime] | None:
    """Longest upcoming stretch that stays at or below `max_flag`."""
    best: tuple[datetime, datetime] | None = None
    best_length = 0
    run_start: datetime | None = None
    run_length = 0

    for prediction in predictions:
        daylight = True
        if daylight_only and forecast is not None:
            daylight = forecast.is_daylight(prediction.time)
        acceptable = prediction.flag <= max_flag and daylight

        if acceptable:
            if run_start is None:
                run_start = prediction.time
                run_length = 0
            run_length += 1
            if run_length > best_length:
                best_length = run_length
                best = (run_start, prediction.time)
        else:
            run_start, run_length = None, 0

    return best if best_length >= 2 else None
