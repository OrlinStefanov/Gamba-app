"""The Python model and the JavaScript model must agree, hour for hour.

The web app on GitHub Pages is a static page with no server, so the model had to
be ported to JavaScript. Two implementations of a safety calculation is a
liability unless something holds them together - this is that something. It runs
both over the same fixtures and compares the flag, the score, the advisories and
every driver sentence.

Skipped when node is not installed, so the suite still runs without it.
"""

import json
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beachflag import demo, model, sources
from beachflag.physics import BEACH_PROFILES

ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).resolve().parent / "js_parity_runner.mjs"
NODE = shutil.which("node")

REFERENCE = datetime(2026, 8, 31)

# Floating point crosses a JSON boundary and two rounding conventions; anything
# that survives to this tolerance is the same calculation.
TOLERANCE = 1e-6


def run_js(payload: dict) -> list[dict]:
    result = subprocess.run(
        [NODE, str(RUNNER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=90,
    )
    if result.returncode != 0:
        raise AssertionError(f"node runner failed:\n{result.stderr}")
    return json.loads(result.stdout)


@unittest.skipIf(NODE is None, "node is not installed")
class TestModelParity(unittest.TestCase):
    maxDiff = None

    def compare(self, scenario_key, profile="sandy", swimmer="average", marine_life=None):
        scenario = demo.SCENARIOS[scenario_key]
        marine, weather = demo.payloads(scenario, REFERENCE)
        # Sample across the window: night, morning, afternoon, and well ahead.
        stamps = [
            (REFERENCE + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
            for h in (0, 3, 7, 10, 14, 18, 22, 27, 33, 40, 52, 68)
        ]
        js = run_js({
            "marine": marine,
            "weather": weather,
            "facing": scenario.facing,
            "profile": profile,
            "swimmer": swimmer,
            "marineLife": marine_life,
            "stamps": stamps,
        })

        forecast = sources.build(marine, weather)
        shore = scenario.shoreline()
        self.assertEqual(len(js), len(stamps))

        for entry, stamp in zip(js, stamps):
            with self.subTest(scenario=scenario_key, stamp=stamp):
                hour = forecast.at(datetime.fromisoformat(stamp))
                self.assertIsNotNone(hour)
                self.assertNotIn("missing", entry)
                py = model.predict(
                    hour, shore,
                    profile=BEACH_PROFILES[profile],
                    swimmer=model.SWIMMERS[swimmer],
                    marine_life=marine_life,
                )
                self.assert_same(py, entry, hour)

    def assert_same(self, py, js, hour):
        self.assertEqual(int(py.flag), js["flag"], "flag")
        self.assertAlmostEqual(py.score, js["score"], delta=0.05, msg="hazard index")
        self.assertAlmostEqual(py.confidence, js["confidence"], delta=0.002, msg="confidence")
        self.assertEqual(py.headline, js["headline"], "headline")
        self.assertEqual([a.name.lower() for a in py.advisories], js["advisories"], "advisories")

        # The parsing layer has to agree too, or the models agree on bad inputs.
        self.assertAlmostEqual(hour.tide_phase or 0, js["hour"]["tidePhase"] or 0, delta=TOLERANCE)
        self.assertEqual(hour.tide_rising, js["hour"]["tideRising"])
        self.assertAlmostEqual(hour.rain_48h or 0, js["hour"]["rain48h"] or 0, delta=TOLERANCE)
        self.assertAlmostEqual(hour.current_speed or 0, js["hour"]["currentSpeed"] or 0, delta=TOLERANCE)

        self.assertEqual(len(py.drivers), len(js["drivers"]))
        for driver, other in zip(py.drivers, js["drivers"]):
            self.assertEqual(driver.key, other["key"])
            self.assertAlmostEqual(driver.score, other["score"], delta=0.001, msg=driver.key)
            self.assertEqual(int(driver.demand), other["demand"], driver.key)
            self.assertEqual(driver.detail, other["detail"], driver.key)

        m, jm = py.metrics, js["metrics"]
        for name, mine, theirs in (
            ("breaker_height", m.breaker_height, jm["breakerHeight"]),
            ("breaker_angle", m.breaker_angle, jm["breakerAngle"]),
            ("rip_speed", m.rip_speed, jm["ripSpeed"]),
            ("rip_peak", m.rip_peak, jm["ripPeak"]),
            ("longshore", m.longshore_current, jm["longshoreCurrent"]),
            ("wave_power", m.wave_power, jm["wavePower"]),
            ("omega", m.omega, jm["omega"]),
            ("iribarren", m.iribarren, jm["iribarren"]),
            ("surf_zone_width", m.surf_zone_width, jm["surfZoneWidth"]),
            ("onshore_wind", m.onshore_wind, jm["onshoreWind"]),
            ("alongshore_wind", m.alongshore_wind, jm["alongshoreWind"]),
            ("shelter", m.shelter, jm["shelter"]),
            ("incidence", m.incidence, jm["incidence"]),
        ):
            self.assertAlmostEqual(mine, theirs, delta=TOLERANCE, msg=name)
        self.assertEqual(m.breaker_type, jm["breakerType"])
        self.assertEqual(m.beach_state, jm["beachState"])
        self.assertEqual(
            None if m.longshore_toward is None else round(m.longshore_toward, 6),
            None if jm["longshoreToward"] is None else round(jm["longshoreToward"], 6),
        )

    def test_calm(self):
        self.compare("calm")

    def test_moderate(self):
        self.compare("moderate")

    def test_storm(self):
        self.compare("storm")

    def test_cold(self):
        self.compare("cold")

    def test_offshore(self):
        self.compare("offshore")

    def test_building(self):
        self.compare("building")

    def test_runoff(self):
        self.compare("runoff")

    def test_every_beach_profile_agrees(self):
        for profile in BEACH_PROFILES:
            self.compare("moderate", profile=profile)

    def test_every_swimmer_agrees(self):
        for swimmer in model.SWIMMERS:
            self.compare("cold", swimmer=swimmer)

    def test_marine_life_override_agrees(self):
        for override in ("yes", "no", None):
            self.compare("offshore", marine_life=override)


if __name__ == "__main__":
    unittest.main()
