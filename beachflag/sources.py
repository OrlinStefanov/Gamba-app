"""Fetching and normalising the input data.

Everything comes from Open-Meteo, which needs no API key and no signup:

  marine-api.open-meteo.com/v1/marine   waves, swell, sea surface temperature,
                                        ocean currents and modelled tide height
  api.open-meteo.com/v1/forecast        wind, gusts, air temperature, UV, CAPE,
                                        visibility, precipitation, daylight
  air-quality-api.open-meteo.com        UV index as a cross-check where the
                                        forecast model does not carry it

Units are normalised on the way in - metres, seconds, m/s, degrees Celsius,
compass degrees - so nothing downstream has to think about them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from .fetch import FetchError, get_json

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

MARINE_HOURLY = (
    "wave_height",
    "wave_direction",
    "wave_period",
    "wind_wave_height",
    "wind_wave_direction",
    "wind_wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "sea_surface_temperature",
    "ocean_current_velocity",
    "ocean_current_direction",
    "sea_level_height_msl",
)

WEATHER_HOURLY = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "weather_code",
    "cloud_cover",
    "visibility",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "uv_index",
    "cape",
    "is_day",
)

WEATHER_DAILY = ("sunrise", "sunset", "uv_index_max")

# WMO weather codes that mean thunderstorm, i.e. clear-the-water.
THUNDERSTORM_CODES = frozenset({95, 96, 99})

PAST_DAYS = 2  # Enough history for a 48-hour antecedent-rainfall total.


@dataclass(frozen=True)
class Hour:
    """One hour of everything the model knows about, in SI-ish units."""

    time: datetime

    # sea state
    wave_height: float | None = None          # m, significant
    wave_period: float | None = None          # s
    wave_direction: float | None = None       # deg, coming FROM
    swell_height: float | None = None         # m
    swell_period: float | None = None         # s
    swell_direction: float | None = None      # deg, coming FROM
    wind_wave_height: float | None = None     # m
    wind_wave_period: float | None = None     # s
    wind_wave_direction: float | None = None  # deg, coming FROM
    sea_temperature: float | None = None      # degC
    current_speed: float | None = None        # m/s
    current_direction: float | None = None    # deg, flowing TOWARD

    # tide
    sea_level: float | None = None            # m relative to MSL
    tide_phase: float | None = None           # 0 at local low, 1 at local high
    tide_rising: bool | None = None
    tide_range: float | None = None           # m, local high minus low

    # atmosphere
    air_temperature: float | None = None      # degC
    apparent_temperature: float | None = None # degC
    humidity: float | None = None             # %
    wind_speed: float | None = None           # m/s
    wind_gusts: float | None = None           # m/s
    wind_direction: float | None = None       # deg, coming FROM
    precipitation: float | None = None        # mm in this hour
    precipitation_probability: float | None = None  # %
    weather_code: int | None = None
    cloud_cover: float | None = None          # %
    visibility: float | None = None           # m
    uv_index: float | None = None
    cape: float | None = None                 # J/kg
    is_day: bool | None = None

    # derived from the surrounding series
    rain_24h: float | None = None             # mm over the previous 24h
    rain_48h: float | None = None             # mm over the previous 48h

    @property
    def is_thunderstorm(self) -> bool:
        return self.weather_code in THUNDERSTORM_CODES

    def dominant_swell(self) -> tuple[float | None, float | None, float | None]:
        """The wave train that matters, as (height, period, direction).

        Prefers the swell partition when it carries real energy: a 1 m
        12-second groundswell breaks far heavier than 1 m of 4-second chop, and
        the combined significant height alone cannot tell you which you have.
        """
        if self.swell_height and self.swell_period and self.swell_height >= 0.25:
            wind_h = self.wind_wave_height or 0.0
            if self.swell_height >= wind_h * 0.7:
                return self.swell_height, self.swell_period, self.swell_direction
        if self.wave_height and self.wave_period:
            return self.wave_height, self.wave_period, self.wave_direction
        if self.wind_wave_height and self.wind_wave_period:
            return self.wind_wave_height, self.wind_wave_period, self.wind_wave_direction
        return None, None, None


@dataclass(frozen=True)
class Forecast:
    hours: tuple[Hour, ...]
    sunrise: tuple[datetime, ...] = ()
    sunset: tuple[datetime, ...] = ()
    utc_offset_seconds: int = 0
    timezone_name: str = "UTC"
    missing: tuple[str, ...] = ()
    """Names of data sources that did not answer, for the confidence estimate."""

    def at(self, when: datetime) -> Hour | None:
        """The hour covering `when`, or None if it is outside the forecast."""
        if not self.hours:
            return None
        target = _naive(when)
        best = min(self.hours, key=lambda h: abs((h.time - target).total_seconds()))
        if abs((best.time - target).total_seconds()) > 5400:
            return None
        return best

    def future(self, start: datetime, hours: int) -> tuple[Hour, ...]:
        target = _naive(start)
        window = [h for h in self.hours if h.time >= target - timedelta(minutes=30)]
        return tuple(window[:hours])

    def is_daylight(self, when: datetime) -> bool:
        hour = self.at(when)
        if hour is not None and hour.is_day is not None:
            return hour.is_day
        return True


def _naive(when: datetime) -> datetime:
    """Drop tzinfo. Open-Meteo returns local wall time and so do we."""
    return when.replace(tzinfo=None) if when.tzinfo else when


def load(
    latitude: float,
    longitude: float,
    *,
    timeout: float = 20.0,
    forecast_days: int = 4,
) -> Forecast:
    """Fetch marine and atmospheric forecasts and merge them onto one timeline."""
    common = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "auto",
        "past_days": PAST_DAYS,
        "forecast_days": max(1, min(7, forecast_days)),
    }
    missing: list[str] = []

    try:
        marine = get_json(MARINE_URL, {**common, "hourly": list(MARINE_HOURLY)},
                          timeout=timeout)
    except FetchError as exc:
        # A beach with no marine grid cell is usually a lake or an estuary. The
        # atmospheric half still says something useful, so carry on degraded.
        marine = {}
        missing.append(f"marine ({exc.reason})")

    weather = get_json(
        FORECAST_URL,
        {
            **common,
            "hourly": list(WEATHER_HOURLY),
            "daily": list(WEATHER_DAILY),
            "wind_speed_unit": "ms",
        },
        timeout=timeout,
    )

    return build(marine, weather, missing=missing)


def build(marine: dict[str, Any], weather: dict[str, Any], *, missing: Iterable[str] = ()) -> Forecast:
    """Merge two Open-Meteo payloads into a Forecast. Split out for testing."""
    weather_hourly = weather.get("hourly") or {}
    marine_hourly = marine.get("hourly") or {}

    times = [_parse_time(t) for t in (weather_hourly.get("time") or [])]
    if not times:
        raise FetchError(FORECAST_URL, "forecast contained no hourly timeline")

    marine_index = _index_by_time(marine_hourly)
    precipitation = _column(weather_hourly, "precipitation", len(times))

    hours: list[Hour] = []
    for i, when in enumerate(times):
        m = marine_index.get(when, {})
        hours.append(
            Hour(
                time=when,
                wave_height=_num(m.get("wave_height")),
                wave_period=_num(m.get("wave_period")),
                wave_direction=_num(m.get("wave_direction")),
                swell_height=_num(m.get("swell_wave_height")),
                swell_period=_num(m.get("swell_wave_period")),
                swell_direction=_num(m.get("swell_wave_direction")),
                wind_wave_height=_num(m.get("wind_wave_height")),
                wind_wave_period=_num(m.get("wind_wave_period")),
                wind_wave_direction=_num(m.get("wind_wave_direction")),
                sea_temperature=_num(m.get("sea_surface_temperature")),
                current_speed=_num(m.get("ocean_current_velocity")),
                current_direction=_num(m.get("ocean_current_direction")),
                sea_level=_num(m.get("sea_level_height_msl")),
                air_temperature=_num(_get(weather_hourly, "temperature_2m", i)),
                apparent_temperature=_num(_get(weather_hourly, "apparent_temperature", i)),
                humidity=_num(_get(weather_hourly, "relative_humidity_2m", i)),
                wind_speed=_num(_get(weather_hourly, "wind_speed_10m", i)),
                wind_gusts=_num(_get(weather_hourly, "wind_gusts_10m", i)),
                wind_direction=_num(_get(weather_hourly, "wind_direction_10m", i)),
                precipitation=_num(_get(weather_hourly, "precipitation", i)),
                precipitation_probability=_num(_get(weather_hourly, "precipitation_probability", i)),
                weather_code=_int(_get(weather_hourly, "weather_code", i)),
                cloud_cover=_num(_get(weather_hourly, "cloud_cover", i)),
                visibility=_num(_get(weather_hourly, "visibility", i)),
                uv_index=_num(_get(weather_hourly, "uv_index", i)),
                cape=_num(_get(weather_hourly, "cape", i)),
                is_day=_bool(_get(weather_hourly, "is_day", i)),
                rain_24h=_window_sum(precipitation, i, 24),
                rain_48h=_window_sum(precipitation, i, 48),
            )
        )

    hours = _add_tide_terms(hours)

    # ocean_current_velocity comes back in km/h; everything else here is m/s.
    hours = [
        replace(h, current_speed=h.current_speed / 3.6) if h.current_speed is not None else h
        for h in hours
    ]

    daily = weather.get("daily") or {}
    return Forecast(
        hours=tuple(hours),
        sunrise=tuple(_parse_time(t) for t in (daily.get("sunrise") or []) if t),
        sunset=tuple(_parse_time(t) for t in (daily.get("sunset") or []) if t),
        utc_offset_seconds=int(weather.get("utc_offset_seconds") or 0),
        timezone_name=str(weather.get("timezone") or "UTC"),
        missing=tuple(missing),
    )


# --------------------------------------------------------------------------
# tide shaping
# --------------------------------------------------------------------------

def _add_tide_terms(hours: list[Hour]) -> list[Hour]:
    """Turn a sea-level series into tide phase, direction and local range.

    Rip currents and shore break both key off where you are in the tidal cycle
    rather than the absolute height, so what the model wants is a 0-at-low,
    1-at-high phase measured against the local high and low either side - not
    a number in metres that means something different on every coast.
    """
    levels = [h.sea_level for h in hours]
    if not any(level is not None for level in levels):
        return hours

    out: list[Hour] = []
    for i, hour in enumerate(hours):
        level = levels[i]
        if level is None:
            out.append(hour)
            continue

        # +/- 6 hours brackets one semidiurnal half-cycle either side, which is
        # enough to find the surrounding high and low on any coast.
        lo_idx = max(0, i - 6)
        hi_idx = min(len(levels), i + 7)
        window = [v for v in levels[lo_idx:hi_idx] if v is not None]
        if len(window) < 3:
            out.append(hour)
            continue

        low, high = min(window), max(window)
        span = high - low
        phase = 0.5 if span < 0.05 else (level - low) / span

        previous = _previous_level(levels, i)
        rising = None if previous is None else level > previous

        out.append(replace(hour, tide_phase=round(phase, 3), tide_rising=rising,
                           tide_range=round(span, 3)))
    return out


def _previous_level(levels: Sequence[float | None], index: int) -> float | None:
    for j in range(index - 1, max(-1, index - 4), -1):
        if levels[j] is not None:
            return levels[j]
    return None


# --------------------------------------------------------------------------
# payload plumbing
# --------------------------------------------------------------------------

def _index_by_time(hourly: dict[str, Any]) -> dict[datetime, dict[str, Any]]:
    """Marine and weather grids can be offset; index marine by timestamp."""
    times = hourly.get("time") or []
    index: dict[datetime, dict[str, Any]] = {}
    for i, raw in enumerate(times):
        when = _parse_time(raw)
        index[when] = {key: _get(hourly, key, i) for key in hourly if key != "time"}
    return index


def _get(hourly: dict[str, Any], key: str, index: int) -> Any:
    series = hourly.get(key)
    if not isinstance(series, list) or index >= len(series):
        return None
    return series[index]


def _column(hourly: dict[str, Any], key: str, length: int) -> list[float | None]:
    series = hourly.get(key)
    if not isinstance(series, list):
        return [None] * length
    values = [_num(v) for v in series[:length]]
    values.extend([None] * (length - len(values)))
    return values


def _window_sum(series: Sequence[float | None], index: int, hours: int) -> float | None:
    start = index - hours + 1
    if start < 0:
        return None  # Not enough history: say nothing rather than under-report.
    values = [v for v in series[start : index + 1] if v is not None]
    if not values:
        return None
    return round(sum(values), 2)


def _parse_time(raw: str) -> datetime:
    text = str(raw).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN check


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(number)


def _bool(value: Any) -> bool | None:
    number = _num(value)
    return None if number is None else bool(number)


def now_local(forecast: Forecast) -> datetime:
    """Current wall-clock time at the beach, matching the forecast timeline."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=forecast.utc_offset_seconds
    )
