"""Synthetic beach days, for offline demos and for testing.

These are not recordings of a real forecast - they are hand-built scenarios
shaped like Open-Meteo payloads, so the whole pipeline (parsing, tide phasing,
physics, scoring, rendering) can run with no network at all. Each one is a
recognisable kind of beach day, which also makes them useful as the fixtures
the model's thresholds are asserted against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .geo import Location
from .shoreline import Shoreline, assumed_shoreline

HOURS = 144  # 2 days of history + 4 days ahead, matching a live request.


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    location: Location
    facing: float
    expectation: str

    wave_height: float
    wave_period: float
    wave_direction: float
    swell_share: float          # fraction of the sea state that is groundswell
    sea_temperature: float
    tide_range: float

    wind_speed: float
    wind_direction: float
    gust_factor: float
    air_temperature: float
    uv_index: float
    cape: float
    rain_mm: float
    thunderstorm: bool = False
    visibility: float = 24000.0
    current_speed_kmh: float = 0.4

    def shoreline(self) -> Shoreline:
        return assumed_shoreline(self.facing)


SCENARIOS: dict[str, Scenario] = {
    "calm": Scenario(
        key="calm",
        title="Mediterranean cove, still summer morning",
        location=Location(39.6280, 2.9160, "Palma Bay", "Spain", "Illes Balears", source="demo"),
        facing=200.0,
        expectation="green",
        wave_height=0.18, wave_period=3.5, wave_direction=200.0, swell_share=0.2,
        sea_temperature=26.0, tide_range=0.25,
        wind_speed=2.0, wind_direction=210.0, gust_factor=1.4,
        air_temperature=29.0, uv_index=9.0, cape=150.0, rain_mm=0.0,
    ),
    "moderate": Scenario(
        key="moderate",
        title="Atlantic beach break, building groundswell",
        location=Location(43.4770, -1.5620, "Anglet", "France", "Nouvelle-Aquitaine", source="demo"),
        facing=290.0,
        expectation="yellow to red",
        wave_height=1.3, wave_period=9.5, wave_direction=292.0, swell_share=0.75,
        sea_temperature=20.0, tide_range=3.4,
        wind_speed=6.5, wind_direction=285.0, gust_factor=1.5,
        air_temperature=23.0, uv_index=7.0, cape=400.0, rain_mm=0.0,
    ),
    "storm": Scenario(
        key="storm",
        title="Winter storm swell with thunderstorms overhead",
        location=Location(38.7100, -9.4200, "Guincho", "Portugal", "Lisboa", source="demo"),
        facing=270.0,
        expectation="double red",
        wave_height=3.6, wave_period=13.0, wave_direction=278.0, swell_share=0.85,
        sea_temperature=15.5, tide_range=3.0,
        wind_speed=17.0, wind_direction=265.0, gust_factor=1.6,
        air_temperature=14.0, uv_index=2.0, cape=1900.0, rain_mm=6.0,
        thunderstorm=True, visibility=3500.0, current_speed_kmh=2.6,
    ),
    "cold": Scenario(
        key="cold",
        title="North Sea in spring - small surf, dangerous water",
        location=Location(56.0400, -2.7200, "Belhaven Bay", "United Kingdom", "Scotland", source="demo"),
        facing=20.0,
        expectation="red on cold water",
        wave_height=0.75, wave_period=6.5, wave_direction=30.0, swell_share=0.5,
        sea_temperature=8.5, tide_range=4.2,
        wind_speed=7.0, wind_direction=40.0, gust_factor=1.5,
        air_temperature=17.0, uv_index=4.0, cape=200.0, rain_mm=1.0,
    ),
    "offshore": Scenario(
        key="offshore",
        title="Glassy day with a strong offshore wind",
        location=Location(26.1420, -80.1000, "Fort Lauderdale", "United States", "Florida", source="demo"),
        facing=90.0,
        expectation="yellow on offshore wind",
        wave_height=0.45, wave_period=6.0, wave_direction=95.0, swell_share=0.5,
        sea_temperature=27.5, tide_range=0.9,
        wind_speed=9.0, wind_direction=270.0, gust_factor=1.5,
        air_temperature=30.0, uv_index=10.0, cape=600.0, rain_mm=0.0,
    ),
    "runoff": Scenario(
        key="runoff",
        title="Calm surf two days after heavy rain",
        location=Location(33.9900, -118.4800, "Santa Monica Bay", "United States", "California", source="demo"),
        facing=250.0,
        expectation="yellow on water quality",
        wave_height=0.5, wave_period=8.0, wave_direction=250.0, swell_share=0.7,
        sea_temperature=18.5, tide_range=1.6,
        wind_speed=3.5, wind_direction=240.0, gust_factor=1.4,
        air_temperature=22.0, uv_index=8.0, cape=100.0, rain_mm=42.0,
    ),
}


def start_of_series(reference: datetime | None = None) -> datetime:
    base = (reference or datetime.now()).replace(minute=0, second=0, microsecond=0, hour=0)
    return base - timedelta(days=2)


def payloads(scenario: Scenario, reference: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build (marine, weather) payloads shaped exactly like Open-Meteo's."""
    start = start_of_series(reference)
    times = [start + timedelta(hours=i) for i in range(HOURS)]
    stamps = [t.strftime("%Y-%m-%dT%H:%M") for t in times]

    marine: dict[str, list[Any]] = {k: [] for k in (
        "wave_height", "wave_direction", "wave_period",
        "wind_wave_height", "wind_wave_direction", "wind_wave_period",
        "swell_wave_height", "swell_wave_direction", "swell_wave_period",
        "sea_surface_temperature", "ocean_current_velocity", "ocean_current_direction",
        "sea_level_height_msl",
    )}
    weather: dict[str, list[Any]] = {k: [] for k in (
        "temperature_2m", "apparent_temperature", "relative_humidity_2m",
        "precipitation", "precipitation_probability", "weather_code", "cloud_cover",
        "visibility", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "uv_index", "cape", "is_day",
    )}

    for index, moment in enumerate(times):
        hour = moment.hour
        # A gentle swell build across the window, so the timeline is not flat.
        build = 1.0 + 0.35 * math.sin(index / 30.0)
        # Semidiurnal tide, 12.42 h period.
        tide = (scenario.tide_range / 2.0) * math.sin(2 * math.pi * index / 12.42)
        daylight = 6 <= hour < 20
        solar = max(0.0, math.sin(math.pi * (hour - 6) / 14.0)) if daylight else 0.0

        total = scenario.wave_height * build
        swell_h = total * scenario.swell_share
        wind_h = max(0.05, total * (1.0 - scenario.swell_share))

        marine["wave_height"].append(round(total, 2))
        marine["wave_period"].append(round(scenario.wave_period, 1))
        marine["wave_direction"].append(round(scenario.wave_direction, 0))
        marine["swell_wave_height"].append(round(swell_h, 2))
        marine["swell_wave_period"].append(round(scenario.wave_period * 1.1, 1))
        marine["swell_wave_direction"].append(round(scenario.wave_direction, 0))
        marine["wind_wave_height"].append(round(wind_h, 2))
        marine["wind_wave_period"].append(round(max(2.5, scenario.wave_period * 0.45), 1))
        marine["wind_wave_direction"].append(round((scenario.wind_direction + 180) % 360, 0))
        marine["sea_surface_temperature"].append(round(scenario.sea_temperature, 1))
        marine["ocean_current_velocity"].append(round(scenario.current_speed_kmh, 2))
        marine["ocean_current_direction"].append(round((scenario.wave_direction + 100) % 360, 0))
        marine["sea_level_height_msl"].append(round(tide, 2))

        # Rain falls across yesterday, so the 24 h and 48 h totals differ and
        # the runoff advisory decays realistically through today.
        yesterday = 24 <= index < 48
        rain = scenario.rain_mm / 24.0 if yesterday else (0.4 if scenario.thunderstorm else 0.0)
        wind = scenario.wind_speed * (0.85 + 0.3 * solar)

        weather["temperature_2m"].append(round(scenario.air_temperature - 4 + 5 * solar, 1))
        weather["apparent_temperature"].append(round(scenario.air_temperature - 3 + 6 * solar, 1))
        weather["relative_humidity_2m"].append(70)
        weather["precipitation"].append(round(rain, 2))
        weather["precipitation_probability"].append(85 if scenario.thunderstorm else 10)
        weather["weather_code"].append(95 if scenario.thunderstorm and daylight else (3 if rain else 1))
        weather["cloud_cover"].append(90 if scenario.thunderstorm else 25)
        weather["visibility"].append(scenario.visibility)
        weather["wind_speed_10m"].append(round(wind, 1))
        weather["wind_direction_10m"].append(round(scenario.wind_direction, 0))
        weather["wind_gusts_10m"].append(round(wind * scenario.gust_factor, 1))
        weather["uv_index"].append(round(scenario.uv_index * solar, 1))
        weather["cape"].append(round(scenario.cape * (0.6 + 0.4 * solar), 0))
        weather["is_day"].append(1 if daylight else 0)

    days = sorted({t.date() for t in times})
    daily = {
        "sunrise": [f"{d.isoformat()}T06:15" for d in days],
        "sunset": [f"{d.isoformat()}T20:05" for d in days],
        "uv_index_max": [scenario.uv_index for _ in days],
    }

    marine_payload = {
        "latitude": scenario.location.latitude,
        "longitude": scenario.location.longitude,
        "timezone": "Europe/Lisbon",
        "utc_offset_seconds": 0,
        "hourly": {"time": stamps, **marine},
    }
    weather_payload = {
        "latitude": scenario.location.latitude,
        "longitude": scenario.location.longitude,
        "timezone": "Europe/Lisbon",
        "utc_offset_seconds": 0,
        "hourly": {"time": stamps, **weather},
        "daily": {"time": [d.isoformat() for d in days], **daily},
    }
    return marine_payload, weather_payload


def get(key: str) -> Scenario:
    if key not in SCENARIOS:
        raise ValueError(f"unknown demo scenario {key!r}; try one of {', '.join(SCENARIOS)}")
    return SCENARIOS[key]
