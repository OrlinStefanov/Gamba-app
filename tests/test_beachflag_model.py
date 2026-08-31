"""Tests for the hazard model, the report and the two front ends.

The demo scenarios double as the model's calibration fixtures: each one is a
recognisable kind of beach day, and the assertions here are what stops a
threshold tweak from quietly turning a red day green.
"""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beachflag import cli, demo, model, render, sources, web
from beachflag.flags import Advisory, Flag
from beachflag.physics import BEACH_PROFILES
from beachflag.shoreline import assumed_shoreline
from beachflag.sources import Hour

REFERENCE = datetime(2026, 8, 31)
NOON = REFERENCE + timedelta(hours=12)
SHORE = assumed_shoreline(90.0)  # A beach looking due east.


def hour(**kwargs) -> Hour:
    """A benign summer hour, overridden field by field per test."""
    base = dict(
        time=NOON,
        wave_height=0.2, wave_period=4.0, wave_direction=90.0,
        sea_temperature=24.0, sea_level=0.0, tide_phase=0.5,
        tide_rising=True, tide_range=1.0,
        air_temperature=26.0, wind_speed=2.0, wind_direction=90.0, wind_gusts=3.0,
        precipitation=0.0, precipitation_probability=5.0, weather_code=1,
        cloud_cover=10.0, visibility=24000.0, uv_index=6.0, cape=100.0,
        is_day=True, rain_24h=0.0, rain_48h=0.0, current_speed=0.05,
    )
    base.update(kwargs)
    return Hour(**base)


def flag_for(**kwargs) -> Flag:
    return model.predict(hour(**kwargs), SHORE).flag


class TestScenarios(unittest.TestCase):
    """Every demo day must land where its description says it does."""

    def predict(self, key, at_hour=14):
        scenario = demo.SCENARIOS[key]
        forecast = sources.build(*demo.payloads(scenario, REFERENCE))
        when = REFERENCE + timedelta(hours=at_hour)
        return scenario, forecast, model.predict(
            forecast.at(when), scenario.shoreline(), forecast=forecast
        )

    def test_calm_day_is_green(self):
        _, _, prediction = self.predict("calm")
        self.assertEqual(prediction.flag, Flag.GREEN)
        self.assertLess(prediction.score, 15.0)

    def test_building_groundswell_is_at_least_yellow(self):
        _, _, prediction = self.predict("moderate")
        self.assertGreaterEqual(prediction.flag, Flag.YELLOW)
        self.assertGreater(prediction.metrics.breaker_height, 1.0)
        self.assertGreater(prediction.metrics.rip_peak, 0.5)

    def test_storm_day_closes_the_water(self):
        _, _, prediction = self.predict("storm")
        self.assertEqual(prediction.flag, Flag.DOUBLE_RED)
        self.assertIn(Advisory.LIGHTNING, prediction.advisories)

    def test_cold_water_is_the_hazard_even_in_small_surf(self):
        _, _, prediction = self.predict("cold")
        self.assertGreaterEqual(prediction.flag, Flag.RED)
        self.assertIn(Advisory.COLD_WATER, prediction.advisories)
        self.assertLess(prediction.metrics.breaker_height, 1.2)
        thermal = next(d for d in prediction.drivers if d.key == "thermal")
        self.assertEqual(thermal.demand, Flag.RED)

    def test_offshore_wind_raises_a_flag_on_a_small_day(self):
        _, _, prediction = self.predict("offshore")
        self.assertGreaterEqual(prediction.flag, Flag.YELLOW)
        self.assertIn(Advisory.OFFSHORE_WIND, prediction.advisories)
        self.assertLess(prediction.metrics.onshore_wind, -4.0)

    def test_runoff_shows_up_as_a_water_quality_advisory(self):
        _, _, prediction = self.predict("runoff")
        self.assertIn(Advisory.WATER_QUALITY, prediction.advisories)
        quality = next(d for d in prediction.drivers if d.key == "quality")
        self.assertGreaterEqual(quality.demand, Flag.YELLOW)

    def test_every_scenario_produces_a_usable_report(self):
        for key in demo.SCENARIOS:
            scenario, forecast, prediction = self.predict(key)
            text = render.report(
                prediction,
                location=scenario.location,
                shore=scenario.shoreline(),
                forecast=forecast,
                profile=BEACH_PROFILES["sandy"],
                swimmer=model.DEFAULT_SWIMMER,
                timeline=model.predict_series(forecast, scenario.shoreline(), prediction.time, 12),
                style=render.Style(False),
            )
            self.assertIn(prediction.flag.name.replace("_", " "), text, key)
            self.assertIn("not a lifeguard", text, key)


class TestDrivers(unittest.TestCase):
    def test_flat_calm_warm_water_is_green(self):
        self.assertEqual(flag_for(), Flag.GREEN)

    def test_flag_rises_monotonically_with_surf(self):
        flags = [flag_for(wave_height=h, wave_period=10.0) for h in (0.2, 0.8, 1.6, 3.0, 5.0)]
        self.assertEqual(flags, sorted(flags))
        self.assertEqual(flags[0], Flag.GREEN)
        self.assertGreaterEqual(flags[-1], Flag.RED)

    def test_lightning_closes_a_flat_beach(self):
        self.assertEqual(flag_for(weather_code=95), Flag.DOUBLE_RED)

    def test_long_period_swell_breaks_bigger_than_short_period_chop(self):
        long_swell = model.predict(hour(wave_height=1.0, wave_period=14.0), SHORE)
        chop = model.predict(hour(wave_height=1.0, wave_period=4.0), SHORE)
        self.assertGreater(long_swell.metrics.breaker_height, chop.metrics.breaker_height)
        self.assertGreater(long_swell.metrics.rip_peak, chop.metrics.rip_peak)

    def test_oblique_swell_trades_rips_for_longshore_drift(self):
        straight = model.predict(hour(wave_height=1.5, wave_period=10.0, wave_direction=90.0), SHORE)
        oblique = model.predict(hour(wave_height=1.5, wave_period=10.0, wave_direction=140.0), SHORE)
        self.assertGreater(straight.metrics.rip_peak, oblique.metrics.rip_peak)
        self.assertGreater(oblique.metrics.longshore_current, straight.metrics.longshore_current)

    def test_low_tide_strengthens_rips(self):
        low = model.predict(hour(wave_height=1.2, wave_period=10.0, tide_phase=0.05), SHORE)
        high = model.predict(hour(wave_height=1.2, wave_period=10.0, tide_phase=0.95), SHORE)
        self.assertGreater(low.metrics.rip_peak, high.metrics.rip_peak)

    def test_offshore_wind_is_its_own_hazard(self):
        offshore = model.predict(hour(wind_speed=9.0, wind_direction=270.0), SHORE)
        self.assertIn(Advisory.OFFSHORE_WIND, offshore.advisories)
        self.assertGreaterEqual(offshore.flag, Flag.YELLOW)

    def test_cold_water_ladder(self):
        self.assertEqual(flag_for(sea_temperature=22.0), Flag.GREEN)
        self.assertGreaterEqual(flag_for(sea_temperature=13.0), Flag.YELLOW)
        self.assertGreaterEqual(flag_for(sea_temperature=7.0), Flag.RED)

    def test_ocean_current_alone_can_raise_the_flag(self):
        self.assertGreaterEqual(flag_for(current_speed=1.0), Flag.RED)

    def test_steep_beach_reports_a_shore_break_a_flat_one_does_not(self):
        conditions = hour(wave_height=1.0, wave_period=9.0)
        steep = model.predict(conditions, SHORE, profile=BEACH_PROFILES["steep"])
        flat = model.predict(conditions, SHORE, profile=BEACH_PROFILES["dissipative"])
        steep_break = next(d for d in steep.drivers if d.key == "shorebreak")
        flat_break = next(d for d in flat.drivers if d.key == "shorebreak")
        self.assertGreater(steep_break.score, flat_break.score)

    def test_marine_life_override_beats_the_heuristic(self):
        conditions = hour(sea_temperature=27.0, wind_speed=8.0, wind_direction=90.0)
        self.assertIn(Advisory.PURPLE, model.predict(conditions, SHORE).advisories)
        cleared = model.predict(conditions, SHORE, marine_life="no")
        self.assertNotIn(Advisory.PURPLE, cleared.advisories)

    def test_marine_life_never_moves_the_hazard_flag(self):
        conditions = hour(sea_temperature=27.0, wind_speed=8.0, wind_direction=90.0)
        self.assertEqual(model.predict(conditions, SHORE, marine_life="yes").flag,
                         model.predict(conditions, SHORE, marine_life="no").flag)

    def test_swimmer_changes_the_advice_not_the_flag(self):
        conditions = hour(wave_height=1.4, wave_period=10.0)
        flags = {model.predict(conditions, SHORE, swimmer=s).flag for s in model.SWIMMERS.values()}
        self.assertEqual(len(flags), 1)

    def test_no_lifeguard_advisory_outside_patrol_hours(self):
        dawn = model.predict(hour(time=REFERENCE + timedelta(hours=6)), SHORE)
        midday = model.predict(hour(time=REFERENCE + timedelta(hours=12)), SHORE)
        self.assertIn(Advisory.NO_LIFEGUARD, dawn.advisories)
        self.assertNotIn(Advisory.NO_LIFEGUARD, midday.advisories)

    def test_patrol_hours_are_configurable(self):
        early = model.predict(
            hour(time=REFERENCE + timedelta(hours=7)), SHORE, patrol=(dtime(6, 0), dtime(20, 0))
        )
        self.assertNotIn(Advisory.NO_LIFEGUARD, early.advisories)

    def test_sheltered_coast_sees_less_of_the_same_swell(self):
        open_coast = model.predict(hour(wave_height=2.0, wave_period=11.0), SHORE)
        cove = model.predict(
            hour(wave_height=2.0, wave_period=11.0, wave_direction=200.0),
            assumed_shoreline(90.0),
        )
        self.assertGreater(open_coast.metrics.breaker_height, cove.metrics.breaker_height)
        self.assertEqual(cove.metrics.shelter, 0.0)

    def test_stacked_moderate_hazards_escalate(self):
        # Nothing here demands red on its own, but together they are not a
        # yellow-flag day.
        stacked = model.predict(
            hour(wave_height=1.15, wave_period=11.0, wind_speed=12.0, wind_gusts=16.0,
                 wind_direction=90.0, tide_phase=0.1, tide_rising=False,
                 sea_temperature=14.0, current_speed=0.5),
            SHORE,
        )
        self.assertGreaterEqual(stacked.flag, Flag.RED)

    def test_headline_names_the_deciding_hazard(self):
        prediction = model.predict(hour(sea_temperature=6.0), SHORE)
        self.assertIn("water temperature", prediction.headline)


class TestInvariants(unittest.TestCase):
    """Properties the model must hold whatever the thresholds are tuned to."""

    def test_only_weighted_drivers_can_move_the_flag(self):
        # Advisory-only drivers carry zero weight; if one ever demanded a flag
        # it would have to be promoted to a real hazard first.
        advisory_only = [k for k, w in model.WEIGHTS.items() if w == 0.0]
        self.assertIn("marine_life", advisory_only)
        for key in advisory_only:
            for override in ("yes", "no", None):
                prediction = model.predict(hour(), SHORE, marine_life=override)
                driver = next(d for d in prediction.drivers if d.key == key)
                self.assertEqual(driver.demand, Flag.GREEN, f"{key}/{override}")
                self.assertEqual(prediction.flag, Flag.GREEN, f"{key}/{override}")

    def test_flag_is_never_below_any_hazard_demand(self):
        cases = [
            {},
            dict(wave_height=2.5, wave_period=12.0),
            dict(sea_temperature=5.0),
            dict(current_speed=1.2),
            dict(weather_code=95),
            dict(wind_speed=20.0, wind_gusts=28.0),
        ]
        for case in cases:
            prediction = model.predict(hour(**case), SHORE)
            worst = max(
                d.demand for d in prediction.drivers if model.WEIGHTS.get(d.key, 0.0) > 0.0
            )
            self.assertGreaterEqual(prediction.flag, worst, case)

    def test_hazard_index_stays_in_range(self):
        for case in ({}, dict(wave_height=8.0, wave_period=18.0, wind_speed=35.0,
                              wind_gusts=50.0, sea_temperature=2.0, current_speed=3.0,
                              weather_code=95, rain_24h=200.0, rain_48h=300.0)):
            prediction = model.predict(hour(**case), SHORE)
            self.assertGreaterEqual(prediction.score, 0.0)
            self.assertLessEqual(prediction.score, 100.0)
            self.assertGreaterEqual(prediction.confidence, 0.0)
            self.assertLessEqual(prediction.confidence, 1.0)

    def test_every_driver_produces_a_sentence(self):
        prediction = model.predict(hour(wave_height=1.5, wave_period=10.0), SHORE)
        for driver in prediction.drivers:
            self.assertTrue(driver.detail.strip(), driver.key)
            self.assertTrue(driver.detail.strip().endswith("."), driver.key)


class TestRequestShape(unittest.TestCase):
    """The request parameters, checked without touching the network."""

    def test_forecast_request_asks_for_everything_the_model_reads(self):
        captured = []

        def fake_get_json(base, params=None, **kwargs):
            captured.append((base, params))
            if "marine" in base:
                return {"hourly": {"time": []}}
            return {
                "timezone": "UTC", "utc_offset_seconds": 0,
                "hourly": {"time": ["2026-08-31T00:00"], "temperature_2m": [20.0]},
                "daily": {"time": ["2026-08-31"]},
            }

        original = sources.get_json
        sources.get_json = fake_get_json
        try:
            sources.load(43.0, -1.5, forecast_days=3)
        finally:
            sources.get_json = original

        bases = {base for base, _ in captured}
        self.assertEqual(bases, {sources.MARINE_URL, sources.FORECAST_URL})
        marine_params = next(p for b, p in captured if b == sources.MARINE_URL)
        weather_params = next(p for b, p in captured if b == sources.FORECAST_URL)
        self.assertIn("wave_period", marine_params["hourly"])
        self.assertIn("sea_level_height_msl", marine_params["hourly"])
        self.assertIn("cape", weather_params["hourly"])
        self.assertEqual(weather_params["wind_speed_unit"], "ms")
        # Two days of history is what the 48 h rainfall total needs.
        self.assertEqual(weather_params["past_days"], 2)
        self.assertEqual(weather_params["forecast_days"], 3)

    def test_elevation_probe_fits_the_api_limit(self):
        from beachflag import shoreline as shoreline_module

        points = shoreline_module.probe_points(43.0, -1.5)
        self.assertLessEqual(len(points), 100)
        self.assertEqual(len(points),
                         len(shoreline_module.PROBE_BEARINGS) * len(shoreline_module.PROBE_RADII_M))


class TestConfidence(unittest.TestCase):
    def test_missing_inputs_lower_confidence(self):
        full = model.confidence(hour(), SHORE, 0.0)
        sparse = model.confidence(
            Hour(time=NOON, air_temperature=20.0), SHORE, 0.0
        )
        self.assertGreater(full, sparse)

    def test_lead_time_lowers_confidence(self):
        self.assertGreater(model.confidence(hour(), SHORE, 0.0),
                           model.confidence(hour(), SHORE, 120.0))

    def test_uncertain_shoreline_lowers_confidence(self):
        from beachflag.shoreline import Shoreline

        vague = Shoreline(facing=90.0, confidence=0.1, open_water_fraction=0.9,
                          nearest_water_m=100.0)
        self.assertGreater(model.confidence(hour(), SHORE, 0.0),
                           model.confidence(hour(), vague, 0.0))


class TestTimeline(unittest.TestCase):
    def setUp(self):
        scenario = demo.SCENARIOS["moderate"]
        self.forecast = sources.build(*demo.payloads(scenario, REFERENCE))
        self.shore = scenario.shoreline()
        self.start = REFERENCE + timedelta(hours=8)

    def test_series_covers_the_requested_hours(self):
        series = model.predict_series(self.forecast, self.shore, self.start, 18)
        self.assertEqual(len(series), 18)
        self.assertEqual(series[0].time, self.start)
        # Confidence decays across the series as lead time grows.
        self.assertLessEqual(series[-1].confidence, series[0].confidence)

    def test_best_window_prefers_daylight_and_calm(self):
        calm = demo.SCENARIOS["calm"]
        forecast = sources.build(*demo.payloads(calm, REFERENCE))
        series = model.predict_series(forecast, calm.shoreline(), self.start, 36)
        window = model.best_window(series, forecast=forecast)
        self.assertIsNotNone(window)
        start, end = window
        self.assertTrue(forecast.is_daylight(start) and forecast.is_daylight(end))

    def test_no_window_when_nothing_is_acceptable(self):
        storm = demo.SCENARIOS["storm"]
        forecast = sources.build(*demo.payloads(storm, REFERENCE))
        series = model.predict_series(forecast, storm.shoreline(), self.start, 24)
        self.assertIsNone(model.best_window(series, max_flag=Flag.GREEN, forecast=forecast))


class TestRender(unittest.TestCase):
    def setUp(self):
        scenario = demo.SCENARIOS["moderate"]
        self.scenario = scenario
        self.forecast = sources.build(*demo.payloads(scenario, REFERENCE))
        when = REFERENCE + timedelta(hours=14)
        self.prediction = model.predict(
            self.forecast.at(when), scenario.shoreline(), forecast=self.forecast
        )

    def test_units(self):
        imperial = render.Units(True)
        self.assertEqual(imperial.height(1.0), "3.3 ft")
        self.assertEqual(imperial.temperature(20.0), "68 F")
        self.assertEqual(render.METRIC.height(1.0), "1.0 m")
        self.assertEqual(render.METRIC.distance(2500.0), "2.5 km")

    def test_style_off_emits_no_escape_codes(self):
        plain = render.Style(False)
        self.assertEqual(plain.bold("x"), "x")
        self.assertNotIn("\033", plain("x", "\033[1m"))

    def test_json_payload_is_serialisable_and_complete(self):
        payload = render.as_dict(
            self.prediction,
            location=self.scenario.location,
            shore=self.scenario.shoreline(),
            forecast=self.forecast,
            profile=BEACH_PROFILES["sandy"],
            timeline=model.predict_series(
                self.forecast, self.scenario.shoreline(), self.prediction.time, 6
            ),
        )
        round_tripped = json.loads(json.dumps(payload))
        self.assertEqual(round_tripped["flag"]["key"], self.prediction.flag.name.lower())
        self.assertEqual(len(round_tripped["timeline"]), 6)
        self.assertIn("disclaimer", round_tripped)
        self.assertIn("rip_peak_ms", round_tripped["metrics"])
        self.assertEqual(len(round_tripped["drivers"]), len(self.prediction.drivers))


class TestScrubbableTimeline(unittest.TestCase):
    """The web timeline is only useful if each hour stands on its own."""

    def payload(self, scenario="building", hours=48):
        return web.predict_payload({"demo": [scenario], "hours": [str(hours)]})

    def test_timeline_spans_history_and_forecast(self):
        payload = self.payload(hours=24)
        self.assertEqual(len(payload["timeline"]), 30)  # 24 ahead + 6 back
        times = [entry["time"] for entry in payload["timeline"]]
        self.assertEqual(times, sorted(times))
        self.assertLess(times[0], payload["now"])
        self.assertGreater(times[-1], payload["now"])

    def test_a_turning_day_actually_changes_flag(self):
        # Without this the scrubber has nothing to show: the demo day has to
        # cross flag boundaries, not sit on one colour for two days.
        levels = {entry["flag"]["key"] for entry in self.payload()["timeline"]}
        self.assertIn("green", levels)
        self.assertIn("yellow", levels)
        self.assertIn("red", levels)

    def test_flags_climb_in_order_as_the_swell_fills_in(self):
        heights = [e["metrics"]["breaker_height_m"] for e in self.payload()["timeline"]]
        self.assertGreater(heights[-1], heights[0] * 2)

    def test_every_frame_can_render_a_full_card(self):
        for entry in self.payload(hours=24)["timeline"]:
            for key in ("time", "flag", "headline", "hazard_index", "confidence",
                        "advisories", "drivers", "metrics", "observations"):
                self.assertIn(key, entry, entry.get("time"))
            self.assertIn("label", entry["flag"])
            self.assertIn("advice", entry["flag"])
            self.assertEqual(len(entry["drivers"]), 10)

    def test_now_index_points_at_the_current_hour(self):
        payload = self.payload()
        index = payload["now_index"]
        self.assertIsNotNone(index)
        self.assertEqual(payload["timeline"][index]["time"], payload["time"])
        self.assertEqual(payload["timeline"][index]["flag"]["key"], payload["flag"]["key"])

    def test_frames_match_a_direct_prediction_for_the_same_hour(self):
        scenario = demo.SCENARIOS["building"]
        forecast = sources.build(*demo.payloads(scenario, REFERENCE))
        series = model.predict_series(forecast, scenario.shoreline(), REFERENCE, 12)
        for prediction in series:
            built = render.frame(prediction, forecast)
            self.assertEqual(built["flag"]["key"], prediction.flag.name.lower())
            self.assertEqual(built["headline"], prediction.headline)

    def test_cli_json_keeps_the_compact_timeline(self):
        # Detail is for the scrubber; piping to a script should not carry it.
        scenario = demo.SCENARIOS["building"]
        forecast = sources.build(*demo.payloads(scenario, REFERENCE))
        payload = render.as_dict(
            model.predict(forecast.at(REFERENCE), scenario.shoreline(), forecast=forecast),
            location=scenario.location,
            shore=scenario.shoreline(),
            forecast=forecast,
            profile=BEACH_PROFILES["sandy"],
            timeline=model.predict_series(forecast, scenario.shoreline(), REFERENCE, 4),
        )
        entry = payload["timeline"][0]
        self.assertIsInstance(entry["flag"], str)
        self.assertNotIn("drivers", entry)


class TestWebApi(unittest.TestCase):
    def test_favicon_is_served_inline(self):
        self.assertIn("<svg", web.FAVICON)

    def test_demo_payload(self):
        payload = web.predict_payload({"demo": ["storm"], "hours": ["12"], "back": ["0"]})
        self.assertEqual(payload["flag"]["key"], "double_red")
        self.assertEqual(len(payload["timeline"]), 12)

    def test_back_hours_put_now_inside_the_strip(self):
        # Without history the now marker sits on the first bar and there is
        # nothing to scrub back to.
        payload = web.predict_payload({"demo": ["calm"], "hours": ["12"], "back": ["6"]})
        self.assertEqual(len(payload["timeline"]), 18)
        self.assertEqual(payload["now_index"], 6)
        self.assertEqual(web.predict_payload(
            {"demo": ["calm"], "hours": ["12"], "back": ["0"]})["now_index"], 0)

    def test_missing_coordinates_are_rejected(self):
        with self.assertRaises(ValueError):
            web.predict_payload({})

    def test_bad_coordinates_are_rejected(self):
        with self.assertRaises(ValueError):
            web.predict_payload({"lat": ["1000"], "lon": ["0"]})

    def test_hours_are_clamped(self):
        payload = web.predict_payload({"demo": ["calm"], "hours": ["9999"]})
        self.assertLessEqual(len(payload["timeline"]), 96)
        # The scrubber needs at least two frames to be draggable.
        self.assertGreaterEqual(len(web.predict_payload({"demo": ["calm"], "hours": ["0"]})["timeline"]), 2)

    def test_facing_override_is_honoured(self):
        payload = web.predict_payload({"demo": ["calm"], "facing": ["45"]})
        self.assertEqual(payload["shoreline"]["facing_degrees"], 45.0)

    def test_page_is_self_contained(self):
        self.assertIn("navigator.geolocation", web.PAGE)
        self.assertNotIn("http://cdn", web.PAGE)
        self.assertNotIn("<script src", web.PAGE)


class TestCli(unittest.TestCase):
    def test_relative_and_absolute_times(self):
        now = datetime(2026, 8, 31, 14, 30)
        self.assertEqual(cli._parse_when(None, now), datetime(2026, 8, 31, 14, 0))
        self.assertEqual(cli._parse_when("+6h", now), datetime(2026, 8, 31, 20, 0))
        self.assertEqual(cli._parse_when("+1d", now), datetime(2026, 9, 1, 14, 0))
        self.assertEqual(cli._parse_when("07:00", now), datetime(2026, 8, 31, 7, 0))
        self.assertEqual(cli._parse_when("2026-09-02T09:00", now), datetime(2026, 9, 2, 9, 0))

    def test_unreadable_time_is_reported(self):
        with self.assertRaises(ValueError):
            cli._parse_when("next tuesday", datetime(2026, 8, 31))

    def test_patrol_parsing(self):
        self.assertEqual(cli._parse_patrol("09:30-17:45"), (dtime(9, 30), dtime(17, 45)))
        with self.assertRaises(ValueError):
            cli._parse_patrol("all day")

    def test_lat_without_lon_is_an_error(self):
        self.assertEqual(cli.main(["--lat", "26.1"]), 2)

    def test_demo_run_exits_with_the_flag_level(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["--demo", "storm", "--no-color"])
        self.assertEqual(code, int(Flag.DOUBLE_RED))
        self.assertIn("DOUBLE RED", buffer.getvalue())

    def test_calm_demo_exits_zero(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--demo", "calm", "--no-color"]), int(Flag.GREEN))

    def test_json_output_is_valid(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.main(["--demo", "moderate", "--json"])
        payload = json.loads(buffer.getvalue())
        self.assertIn("flag", payload)
        self.assertIn("drivers", payload)

    def test_unknown_demo_is_reported_not_raised(self):
        self.assertEqual(cli.main(["--demo", "nope"]), 2)

    def test_list_demos(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(cli.main(["--list-demos"]), 0)
        self.assertIn("storm", buffer.getvalue())

    def test_imperial_units_reach_the_report(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli.main(["--demo", "moderate", "--units", "imperial", "--no-color"])
        self.assertIn("ft", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
