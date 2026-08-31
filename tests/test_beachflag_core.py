"""Tests for the geometry, physics and parsing layers of the beach flag app.

No network: everything here runs against constructed inputs.
"""

import math
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beachflag import geo, physics, shoreline, sources
from beachflag.fetch import FetchError, build_url


class TestAngles(unittest.TestCase):
    def test_wrapping(self):
        self.assertEqual(geo.wrap360(370.0), 10.0)
        self.assertEqual(geo.wrap360(-10.0), 350.0)
        self.assertEqual(geo.wrap180(190.0), -170.0)
        self.assertEqual(geo.wrap180(-190.0), 170.0)

    def test_mean_bearing_crosses_north(self):
        # The whole reason for a circular mean: 350 and 10 average to 0, not 180.
        self.assertAlmostEqual(geo.mean_bearing([350.0, 10.0]), 0.0, places=6)
        self.assertAlmostEqual(geo.mean_bearing([80.0, 100.0]), 90.0, places=6)

    def test_mean_bearing_of_opposites_is_undefined(self):
        self.assertIsNone(geo.mean_bearing([0.0, 180.0]))

    def test_weighted_mean_bearing(self):
        # Heavier weight on 10 degrees pulls the mean off the midpoint toward it.
        mean = geo.mean_bearing([10.0, 50.0], [3.0, 1.0])
        self.assertLess(mean, 30.0)
        self.assertGreater(mean, 10.0)

    def test_spread(self):
        self.assertLess(geo.bearing_spread([10.0, 12.0, 14.0]), 0.05)
        self.assertGreater(geo.bearing_spread([0.0, 90.0, 180.0, 270.0]), 0.9)

    def test_compass_points(self):
        self.assertEqual(geo.compass_point(0.0), "N")
        self.assertEqual(geo.compass_point(90.0), "E")
        self.assertEqual(geo.compass_point(247.5), "WSW")
        self.assertEqual(geo.compass_point(359.0), "N")

    def test_offset_and_distance_round_trip(self):
        lat, lon = geo.offset(43.0, -1.5, 90.0, 2000.0)
        self.assertAlmostEqual(geo.haversine_m(43.0, -1.5, lat, lon), 2000.0, delta=1.0)
        self.assertGreater(lon, -1.5)  # Due east means the longitude increases.

    def test_validate_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            geo.validate(91.0, 0.0)
        with self.assertRaises(ValueError):
            geo.validate(0.0, 181.0)


class TestPhysics(unittest.TestCase):
    def test_breaker_height_amplifies_swell(self):
        # Shoaling puts a 1 m 8 s swell up around 1.4 m at the break point.
        breaking = physics.breaker_height(1.0, 8.0)
        self.assertGreater(breaking, 1.25)
        self.assertLess(breaking, 1.6)

    def test_breaker_height_monotonic(self):
        self.assertGreater(physics.breaker_height(2.0, 8.0), physics.breaker_height(1.0, 8.0))
        self.assertGreater(physics.breaker_height(1.0, 14.0), physics.breaker_height(1.0, 6.0))
        self.assertEqual(physics.breaker_height(0.0, 10.0), 0.0)

    def test_refraction_bends_waves_toward_shore_normal(self):
        arriving = physics.breaker_angle(40.0, 10.0, 1.5)
        self.assertLess(abs(arriving), 40.0)
        self.assertGreater(arriving, 0.0)  # Sign of the approach is preserved.
        self.assertLess(physics.breaker_angle(-40.0, 10.0, 1.5), 0.0)

    def test_longshore_current_peaks_near_45_degrees(self):
        at_45 = physics.longshore_current(1.0, 45.0)
        self.assertGreater(at_45, physics.longshore_current(1.0, 10.0))
        self.assertGreater(at_45, physics.longshore_current(1.0, 80.0))
        self.assertEqual(physics.longshore_current(1.0, 0.0), 0.0)

    def test_wave_power_scales_with_height_squared(self):
        one = physics.wave_energy_flux(1.0, 10.0)
        two = physics.wave_energy_flux(2.0, 10.0)
        self.assertAlmostEqual(two / one, 4.0, places=6)
        # A 1 m 10 s swell is a handful of kW per metre of crest.
        self.assertGreater(one, 3.0)
        self.assertLess(one, 8.0)

    def test_iribarren_classifies_breakers(self):
        flat = physics.iribarren(0.015, 1.5, 10.0)
        steep = physics.iribarren(0.12, 1.5, 10.0)
        self.assertEqual(physics.breaker_type(flat), "spilling")
        self.assertIn(physics.breaker_type(steep), {"plunging", "surging"})

    def test_beach_state_bands(self):
        self.assertEqual(physics.beach_state(0.5), "reflective")
        self.assertTrue(physics.beach_state(3.0).startswith("intermediate"))
        self.assertEqual(physics.beach_state(9.0), "dissipative")

    def test_rip_morphology_peaks_in_the_intermediate_band(self):
        intermediate = physics.rip_morphology_factor(3.5)
        self.assertGreater(intermediate, physics.rip_morphology_factor(0.5))
        self.assertGreater(intermediate, physics.rip_morphology_factor(12.0))

    def test_wind_components_use_the_beach_frame(self):
        # Facing east: wind from the east is onshore, wind from the west offshore.
        onshore, _ = physics.wind_components(10.0, 90.0, 90.0)
        self.assertAlmostEqual(onshore, 10.0, places=6)
        offshore, _ = physics.wind_components(10.0, 270.0, 90.0)
        self.assertAlmostEqual(offshore, -10.0, places=6)
        _, alongshore = physics.wind_components(10.0, 180.0, 90.0)
        self.assertAlmostEqual(alongshore, 10.0, places=6)

    def test_surf_zone_widens_on_flatter_beaches(self):
        self.assertGreater(
            physics.surf_zone_width(1.5, 0.015), physics.surf_zone_width(1.5, 0.10)
        )


class TestShoreline(unittest.TestCase):
    @staticmethod
    def elevations_for(is_water) -> tuple[list, list]:
        points = shoreline.probe_points(43.0, -1.5)
        return points, [0.0 if is_water(bearing) else 40.0 for _, _, bearing, _ in points]

    def test_straight_coast_facing_east(self):
        points, elevations = self.elevations_for(lambda b: 0.0 < b < 180.0)
        result = shoreline.from_elevations(points, elevations)
        self.assertAlmostEqual(result.facing, 90.0, delta=2.0)
        self.assertGreater(result.confidence, 0.6)
        self.assertFalse(result.is_sheltered)

    def test_straight_coast_facing_south(self):
        points, elevations = self.elevations_for(lambda b: 90.0 < b < 270.0)
        result = shoreline.from_elevations(points, elevations)
        self.assertAlmostEqual(result.facing, 180.0, delta=2.0)

    def test_narrow_inlet_reads_as_sheltered_and_uncertain(self):
        points, elevations = self.elevations_for(lambda b: 80.0 <= b <= 100.0)
        result = shoreline.from_elevations(points, elevations)
        self.assertTrue(result.is_sheltered)
        self.assertLess(result.confidence, 0.6)

    def test_inland_point_is_an_error(self):
        points, elevations = self.elevations_for(lambda b: False)
        with self.assertRaises(FetchError) as caught:
            shoreline.from_elevations(points, elevations)
        self.assertIn("inland", caught.exception.reason)

    def test_missing_samples_are_skipped_not_counted_as_land(self):
        points = shoreline.probe_points(43.0, -1.5)
        elevations = [None if i % 2 else (0.0 if 0 < b < 180 else 40.0)
                      for i, (_, _, b, _) in enumerate(points)]
        result = shoreline.from_elevations(points, elevations)
        self.assertAlmostEqual(result.facing, 90.0, delta=6.0)

    def test_shelter_factor_blocks_swell_from_behind_the_beach(self):
        shore = shoreline.assumed_shoreline(90.0)
        self.assertEqual(shore.shelter_factor(90.0), 1.0)
        self.assertEqual(shore.shelter_factor(270.0), 0.0)

    def test_shelter_factor_blocks_swell_a_headland_stands_in_front_of(self):
        # A detected inlet open only to the east: a southerly swell has land in
        # the way long before obliquity alone would kill it.
        points, elevations = self.elevations_for(lambda b: 80.0 <= b <= 100.0)
        shore = shoreline.from_elevations(points, elevations)
        self.assertEqual(shore.shelter_factor(90.0), 1.0)
        self.assertLess(shore.shelter_factor(150.0), 0.25)


class TestSources(unittest.TestCase):
    @staticmethod
    def payloads(hours=60, **overrides):
        start = datetime(2026, 8, 29, 0, 0)
        stamps = [(start.replace(hour=0) + __import__("datetime").timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
                  for i in range(hours)]
        tide = [round(1.5 * math.sin(2 * math.pi * i / 12.42), 3) for i in range(hours)]
        marine = {
            "hourly": {
                "time": stamps,
                "wave_height": [1.0] * hours,
                "wave_period": [7.0] * hours,
                "wave_direction": [270.0] * hours,
                "swell_wave_height": [0.9] * hours,
                "swell_wave_period": [11.0] * hours,
                "swell_wave_direction": [265.0] * hours,
                "wind_wave_height": [0.4] * hours,
                "wind_wave_period": [3.5] * hours,
                "wind_wave_direction": [300.0] * hours,
                "sea_surface_temperature": [18.0] * hours,
                "ocean_current_velocity": [3.6] * hours,  # km/h in, m/s out
                "ocean_current_direction": [10.0] * hours,
                "sea_level_height_msl": tide,
            }
        }
        weather = {
            "timezone": "Europe/Paris",
            "utc_offset_seconds": 7200,
            "hourly": {
                "time": stamps,
                "temperature_2m": [22.0] * hours,
                "precipitation": [1.0] * hours,
                "wind_speed_10m": [5.0] * hours,
                "wind_direction_10m": [280.0] * hours,
                "wind_gusts_10m": [8.0] * hours,
                "weather_code": [1] * hours,
                "uv_index": [5.0] * hours,
                "is_day": [1] * hours,
                **overrides,
            },
            "daily": {"time": ["2026-08-29"], "sunrise": ["2026-08-29T07:00"],
                      "sunset": ["2026-08-29T20:30"]},
        }
        return marine, weather

    def test_merges_and_converts_units(self):
        forecast = sources.build(*self.payloads())
        hour = forecast.hours[30]
        self.assertEqual(hour.wave_height, 1.0)
        self.assertAlmostEqual(hour.current_speed, 1.0, places=6)  # 3.6 km/h -> 1 m/s
        self.assertEqual(forecast.timezone_name, "Europe/Paris")
        self.assertEqual(forecast.utc_offset_seconds, 7200)

    def test_tide_phase_spans_low_to_high(self):
        forecast = sources.build(*self.payloads())
        phases = [h.tide_phase for h in forecast.hours[12:48] if h.tide_phase is not None]
        self.assertLess(min(phases), 0.05)
        self.assertGreater(max(phases), 0.95)
        # Range is measured locally, and matches the amplitude we fed in.
        self.assertAlmostEqual(forecast.hours[24].tide_range, 3.0, delta=0.15)

    def test_tide_direction_tracks_the_series(self):
        forecast = sources.build(*self.payloads())
        rising = [h for h in forecast.hours[1:24] if h.tide_rising]
        falling = [h for h in forecast.hours[1:24] if h.tide_rising is False]
        self.assertTrue(rising and falling)

    def test_rain_windows_need_enough_history(self):
        forecast = sources.build(*self.payloads())
        self.assertIsNone(forecast.hours[5].rain_24h)     # Not 24 h of history yet.
        self.assertAlmostEqual(forecast.hours[23].rain_24h, 24.0, places=2)
        self.assertIsNone(forecast.hours[23].rain_48h)
        self.assertAlmostEqual(forecast.hours[47].rain_48h, 48.0, places=2)

    def test_dominant_swell_prefers_groundswell_over_chop(self):
        forecast = sources.build(*self.payloads())
        height, period, direction = forecast.hours[10].dominant_swell()
        self.assertEqual((height, period, direction), (0.9, 11.0, 265.0))

    def test_missing_marine_payload_degrades_rather_than_fails(self):
        _, weather = self.payloads()
        forecast = sources.build({}, weather, missing=["marine (no grid cell)"])
        self.assertIsNone(forecast.hours[0].wave_height)
        self.assertEqual(forecast.hours[0].air_temperature, 22.0)
        self.assertEqual(forecast.missing, ("marine (no grid cell)",))

    def test_empty_timeline_is_an_error(self):
        with self.assertRaises(FetchError):
            sources.build({}, {"hourly": {"time": []}})

    def test_thunderstorm_codes(self):
        marine, weather = self.payloads()
        weather["hourly"]["weather_code"] = [95] * len(weather["hourly"]["time"])
        forecast = sources.build(marine, weather)
        self.assertTrue(forecast.hours[0].is_thunderstorm)

    def test_lookup_outside_the_window_returns_nothing(self):
        forecast = sources.build(*self.payloads())
        self.assertIsNone(forecast.at(datetime(2030, 1, 1, 12, 0)))
        self.assertIsNotNone(forecast.at(datetime(2026, 8, 29, 12, 20)))


class TestFetchHelpers(unittest.TestCase):
    def test_build_url_joins_lists_and_drops_none(self):
        url = build_url("https://example.test/x", {
            "latitude": [1.5, 2.5], "hourly": ("a", "b"), "skip": None, "flag": True,
        })
        self.assertIn("latitude=1.5,2.5", url)
        self.assertIn("hourly=a,b", url)
        self.assertNotIn("skip", url)
        self.assertIn("flag=true", url)

    def test_build_url_trims_float_noise_for_stable_cache_keys(self):
        self.assertIn("lat=43.5", build_url("https://example.test", {"lat": 43.500000001}))


if __name__ == "__main__":
    unittest.main()
