"""Terminal and JSON rendering.

The report is built around one idea: the flag is the answer, but the reason is
the useful part. A colour with no explanation is something to argue with; a
colour plus "rip channels running 0.9 m/s, pulsing to 1.5 - faster than you can
swim against" is something to act on.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime

from .flags import ADVISORY_INFO, FLAG_INFO
from .geo import Location, compass_point
from .model import Driver, Prediction, Swimmer
from .numfmt import fmt, signed
from .physics import BeachProfile
from .shoreline import Shoreline
from .sources import Forecast

WIDTH = 76


# --------------------------------------------------------------------------
# units
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Units:
    imperial: bool = False

    def height(self, metres: float) -> str:
        return f"{fmt(metres * 3.281, 1)} ft" if self.imperial else f"{fmt(metres, 1)} m"

    def distance(self, metres: float) -> str:
        if self.imperial:
            return f"{fmt(metres * 3.281, 0)} ft" if metres < 300 else f"{fmt(metres / 1609, 1)} mi"
        return f"{fmt(metres, 0)} m" if metres < 1000 else f"{fmt(metres / 1000, 1)} km"

    def speed(self, ms: float) -> str:
        return f"{fmt(ms * 2.237, 0)} mph" if self.imperial else f"{fmt(ms, 1)} m/s"

    def temperature(self, celsius: float) -> str:
        return f"{fmt(celsius * 9 / 5 + 32, 0)} F" if self.imperial else f"{fmt(celsius, 0)} C"


METRIC = Units(False)


# --------------------------------------------------------------------------
# styling
# --------------------------------------------------------------------------

class Style:
    """ANSI colour that turns itself off when nobody can see it."""

    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = (
                sys.stdout.isatty()
                and os.environ.get("NO_COLOR") is None
                and os.environ.get("TERM") != "dumb"
            )
        self.enabled = enabled

    def __call__(self, text: str, code: str) -> str:
        return f"{code}{text}\033[0m" if self.enabled else text

    def dim(self, text: str) -> str:
        return self(text, "\033[2m")

    def bold(self, text: str) -> str:
        return self(text, "\033[1m")


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def report(
    prediction: Prediction,
    *,
    location: Location,
    shore: Shoreline,
    forecast: Forecast,
    profile: BeachProfile,
    swimmer: Swimmer,
    timeline: tuple[Prediction, ...] = (),
    window: tuple[datetime, datetime] | None = None,
    units: Units = METRIC,
    style: Style | None = None,
) -> str:
    style = style or Style()
    info = FLAG_INFO[prediction.flag]
    lines: list[str] = []

    lines.append("")
    lines.append(style(f"  {info.emoji}  {info.label} FLAG".ljust(WIDTH - 2), info.ansi))
    lines.append(style(f"     {info.meaning}".ljust(WIDTH - 2), info.ansi))
    lines.append("")
    lines.append(f"  {style.bold(location.label())}")
    lines.append(
        "  "
        + style.dim(
            f"{prediction.time:%a %d %b %H:%M} {forecast.timezone_name} - "
            f"beach faces {compass_point(shore.facing)} ({fmt(shore.facing, 0)} deg) - "
            f"{profile.name} - confidence {fmt(prediction.confidence * 100, 0)}%"
        )
    )
    lines.append("")
    lines.append(f"  {prediction.headline}")
    lines.append(f"  {style.dim(info.advice)}")

    lines.append("")
    lines.append(style.bold("  WHY"))
    for driver in prediction.top_drivers(5):
        lines.extend(_driver_lines(driver, style))
    if not prediction.top_drivers(5):
        lines.append("    Nothing scored above background. Enjoy it.")

    if prediction.advisories:
        lines.append("")
        lines.append(style.bold("  ALSO FLYING"))
        for advisory in prediction.advisories:
            detail = ADVISORY_INFO[advisory]
            lines.append(f"    {detail.emoji} {detail.label:<14} {style.dim(detail.meaning)}")

    lines.append("")
    lines.append(style.bold("  CONDITIONS"))
    lines.extend(_condition_lines(prediction, forecast, units))

    if timeline:
        lines.append("")
        lines.append(style.bold("  NEXT HOURS"))
        lines.extend(_timeline_lines(timeline, style))

    if window:
        start, end = window
        same_day = start.date() == end.date()
        when = (
            f"{start:%H:%M}-{end:%H:%M} today" if same_day and start.date() == prediction.time.date()
            else f"{start:%a %H:%M} to {end:%a %H:%M}"
        )
        lines.append("")
        lines.append(f"  {style.bold('BEST WINDOW')}  {when} - the longest calm stretch ahead.")

    lines.append("")
    lines.extend(_caveats(prediction, shore, forecast, swimmer, style))
    lines.append("")
    return "\n".join(lines)


def _driver_lines(driver: Driver, style: Style) -> list[str]:
    info = FLAG_INFO[driver.demand]
    bar = _bar(driver.score)
    head = f"    {style(bar, info.ansi)} {driver.label:<18} {style.dim(f'{fmt(driver.score, 0)}/100')}"
    body = _wrap(driver.detail, indent=8)
    return [head, *body]


def _bar(score: float, slots: int = 10) -> str:
    filled = int(round(max(0.0, min(100.0, score)) / 100.0 * slots))
    return "█" * filled + "░" * (slots - filled)


def _condition_lines(prediction: Prediction, forecast: Forecast, units: Units) -> list[str]:
    m = prediction.metrics
    hour = forecast.at(prediction.time)
    rows: list[tuple[str, str]] = []

    if m.breaker_height > 0:
        rows.append(("Breaking surf", f"{units.height(m.breaker_height)} {m.breaker_type}"))
        direction = compass_point(m.deep_direction) if m.deep_direction is not None else "?"
        rows.append((
            "Swell",
            f"{units.height(m.deep_height)} at {fmt(m.deep_period, 0)} s from {direction}"
            f" ({signed(m.incidence, 0)} deg off shore-normal)",
        ))
        rows.append(("Wave power", f"{_kw(m.wave_power)} kW/m of beach"))
        rows.append(("Surf zone", f"about {units.distance(m.surf_zone_width)} wide"))
        rows.append(("Beach state", f"{m.beach_state} (omega {fmt(m.omega, 1)})"))
    else:
        rows.append(("Surf", "flat"))

    if m.rip_speed > 0.05:
        rows.append(("Rip current", f"{units.speed(m.rip_speed)} mean, {units.speed(m.rip_peak)} in pulses"))
    if m.longshore_current > 0.05:
        toward = compass_point(m.longshore_toward) if m.longshore_toward is not None else "?"
        rows.append(("Longshore drift", f"{units.speed(m.longshore_current)} toward {toward}"))

    if hour is not None:
        if hour.wind_speed is not None:
            direction = compass_point(hour.wind_direction) if hour.wind_direction is not None else "?"
            gust = f", gusting {units.speed(hour.wind_gusts)}" if hour.wind_gusts else ""
            onshore = "onshore" if m.onshore_wind > 1 else ("offshore" if m.onshore_wind < -1 else "cross-shore")
            rows.append(("Wind", f"{units.speed(hour.wind_speed)} from {direction} ({onshore}){gust}"))
        if hour.sea_temperature is not None:
            air = f", air {units.temperature(hour.air_temperature)}" if hour.air_temperature is not None else ""
            rows.append(("Water", f"{units.temperature(hour.sea_temperature)}{air}"))
        if hour.tide_phase is not None:
            state = "rising" if hour.tide_rising else "falling"
            where = _tide_word(hour.tide_phase)
            rows.append((
                "Tide",
                f"{where}, {state} (range {units.height(hour.tide_range or 0.0)})",
            ))
        if hour.current_speed is not None and hour.current_speed > 0.05:
            rows.append(("Ocean current", units.speed(hour.current_speed)))
        if hour.uv_index is not None:
            rows.append(("UV index", f"{fmt(hour.uv_index, 0)}"))

    return [f"    {label:<16} {value}" for label, value in rows]


def _kw(value: float) -> str:
    """Wave power spans three orders of magnitude; a fixed precision loses the low end."""
    return f"{fmt(value, 1)}" if value < 10 else f"{fmt(value, 0)}"


def _tide_word(phase: float) -> str:
    if phase < 0.2:
        return "near low"
    if phase < 0.45:
        return "low-mid"
    if phase < 0.55:
        return "mid"
    if phase < 0.8:
        return "mid-high"
    return "near high"


def _timeline_lines(timeline: tuple[Prediction, ...], style: Style) -> list[str]:
    """A colour strip of the hours ahead, so a change of flag is visible at a glance."""
    shown = timeline[:24]
    hours = "".join(f"{p.time.hour:>3}" for p in shown)
    blocks = "".join(style("  █", FLAG_INFO[p.flag].ansi) for p in shown)
    legend = "  ".join(
        f"{style('█', FLAG_INFO[f].ansi)} {FLAG_INFO[f].label.lower()}"
        for f in sorted({p.flag for p in shown})
    )
    return [
        f"    {style.dim(hours)}",
        f"    {blocks}",
        f"    {style.dim(legend)}",
    ]


def _caveats(
    prediction: Prediction,
    shore: Shoreline,
    forecast: Forecast,
    swimmer: Swimmer,
    style: Style,
) -> list[str]:
    notes: list[str] = []

    peak = prediction.metrics.rip_peak
    if peak > swimmer.swim_speed and peak > 0.2:
        article = "An" if swimmer.label[0].lower() in "aeiou" else "A"
        notes.append(
            f"{article} {swimmer.label} holds about {fmt(swimmer.swim_speed, 1)} m/s. Rip pulses here reach "
            f"{fmt(peak, 1)} m/s, so swimming straight back at the beach will not work - go parallel first."
        )
    if shore.confidence < 0.55:
        notes.append(
            f"The shoreline orientation ({fmt(shore.facing, 0)} deg) was inferred from terrain and is "
            "uncertain here - a headland or bay. Pass --facing if you know which way the beach looks."
        )
    if shore.is_sheltered:
        notes.append("This looks like a sheltered spot, so open-coast swell mostly does not reach it.")
    if forecast.missing:
        notes.append("Some inputs were unavailable: " + "; ".join(forecast.missing) + ".")

    out = [style.bold("  NOTES")] if notes else []
    for note in notes:
        out.extend(_wrap(note, indent=4, bullet="- "))
    out.append("")
    out.extend(
        _wrap(
            "This is a model, not a lifeguard. It cannot see today's sandbars, a local closure, "
            "or the person already in trouble. Where there is a real flag flying, that flag wins.",
            indent=2,
            bullet="! ",
        )
    )
    return [style.dim(line) if line.strip().startswith("!") else line for line in out]


def _wrap(text: str, indent: int = 4, bullet: str = "") -> list[str]:
    import textwrap

    prefix = " " * indent
    lines = textwrap.wrap(text, width=WIDTH - indent - len(bullet))
    if not lines:
        return []
    return [prefix + bullet + lines[0]] + [prefix + " " * len(bullet) + line for line in lines[1:]]


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------

def frame(prediction: Prediction, forecast: Forecast) -> dict:
    """One hour of assessment, complete enough to render a full card from.

    The web app's timeline scrubber needs every hour to stand on its own - drag
    the slider and the flag, the reasons and the conditions all have to change
    together - so a frame carries the whole answer for its hour rather than
    just a colour.
    """
    info = FLAG_INFO[prediction.flag]
    hour = forecast.at(prediction.time)
    m = prediction.metrics
    return {
        "time": prediction.time.isoformat(),
        "flag": {
            "key": prediction.flag.name.lower(),
            "label": info.label,
            "level": int(prediction.flag),
            "meaning": info.meaning,
            "advice": info.advice,
            "color": info.hex_color,
        },
        "headline": prediction.headline,
        "hazard_index": prediction.score,
        "confidence": prediction.confidence,
        "advisories": [
            {
                "key": a.name.lower(),
                "label": ADVISORY_INFO[a].label,
                "meaning": ADVISORY_INFO[a].meaning,
                "color": ADVISORY_INFO[a].hex_color,
            }
            for a in prediction.advisories
        ],
        "drivers": [
            {
                "key": d.key,
                "label": d.label,
                "score": round(d.score, 1),
                "demands": d.demand.name.lower(),
                "detail": d.detail,
            }
            for d in prediction.drivers
        ],
        "metrics": {
            "breaker_height_m": round(m.breaker_height, 2),
            "breaker_type": m.breaker_type,
            "breaker_angle_deg": round(m.breaker_angle, 1),
            "swell_height_m": round(m.deep_height, 2),
            "swell_period_s": round(m.deep_period, 1),
            "swell_direction_deg": None if m.deep_direction is None else round(m.deep_direction, 0),
            "incidence_deg": round(m.incidence, 1),
            "wave_power_kw_per_m": round(m.wave_power, 1),
            "surf_zone_width_m": round(m.surf_zone_width, 0),
            "beach_state": m.beach_state,
            "omega": round(m.omega, 2),
            "iribarren": round(m.iribarren, 2),
            "rip_speed_ms": round(m.rip_speed, 2),
            "rip_peak_ms": round(m.rip_peak, 2),
            "longshore_current_ms": round(m.longshore_current, 2),
            "longshore_toward_deg": None if m.longshore_toward is None else round(m.longshore_toward, 0),
            "onshore_wind_ms": round(m.onshore_wind, 1),
            "alongshore_wind_ms": round(m.alongshore_wind, 1),
            "shelter": round(m.shelter, 2),
        },
        "observations": None if hour is None else {
            "wind_speed_ms": hour.wind_speed,
            "wind_gusts_ms": hour.wind_gusts,
            "wind_direction_deg": hour.wind_direction,
            "sea_temperature_c": hour.sea_temperature,
            "air_temperature_c": hour.air_temperature,
            "uv_index": hour.uv_index,
            "tide_phase": hour.tide_phase,
            "tide_rising": hour.tide_rising,
            "tide_range_m": hour.tide_range,
            "current_speed_ms": hour.current_speed,
            "rain_24h_mm": hour.rain_24h,
            "rain_48h_mm": hour.rain_48h,
            "visibility_m": hour.visibility,
            "is_day": hour.is_day,
        },
    }


def as_dict(
    prediction: Prediction,
    *,
    location: Location,
    shore: Shoreline,
    forecast: Forecast,
    profile: BeachProfile,
    timeline: tuple[Prediction, ...] = (),
    window: tuple[datetime, datetime] | None = None,
    now: datetime | None = None,
    detail: bool = False,
) -> dict:
    """Machine-readable form of the same answer, for the web UI and for piping.

    `detail` fills every timeline entry with a complete frame, which is what the
    scrubber needs; without it the timeline stays a compact strip, which is what
    `--json` on the command line wants.
    """
    payload = {
        "location": {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "label": location.label(),
            "source": location.source,
        },
        "shoreline": {
            "facing_degrees": round(shore.facing, 1),
            "facing_compass": compass_point(shore.facing),
            "confidence": shore.confidence,
            "sheltered": shore.is_sheltered,
            "source": shore.source,
        },
        "beach_profile": profile.name,
        "timezone": forecast.timezone_name,
        **frame(prediction, forecast),
        "timeline": [
            _timeline_entry(p, forecast, detail=detail) for p in timeline
        ],
        "now": (now or prediction.time).isoformat(),
        "now_index": _now_index(timeline, now or prediction.time),
        "best_window": None if window is None else {
            "start": window[0].isoformat(),
            "end": window[1].isoformat(),
        },
        "disclaimer": (
            "Model estimate, not an official forecast and not a substitute for a lifeguard. "
            "Where a real flag is flying, that flag wins."
        ),
    }
    return payload


def _timeline_entry(prediction: Prediction, forecast: Forecast, *, detail: bool) -> dict:
    """A strip entry, or a whole frame when the scrubber is going to render it."""
    if detail:
        entry = frame(prediction, forecast)
        entry["breaker_height_m"] = round(prediction.metrics.breaker_height, 2)
        entry["rip_peak_ms"] = round(prediction.metrics.rip_peak, 2)
        return entry
    return {
        "time": prediction.time.isoformat(),
        "flag": prediction.flag.name.lower(),
        "level": int(prediction.flag),
        "color": FLAG_INFO[prediction.flag].hex_color,
        "hazard_index": prediction.score,
        "breaker_height_m": round(prediction.metrics.breaker_height, 2),
        "rip_peak_ms": round(prediction.metrics.rip_peak, 2),
    }


def _now_index(timeline: tuple[Prediction, ...], now: datetime) -> int | None:
    """Which frame the scrubber should open on."""
    if not timeline:
        return None
    return min(
        range(len(timeline)),
        key=lambda i: abs((timeline[i].time - now).total_seconds()),
    )
