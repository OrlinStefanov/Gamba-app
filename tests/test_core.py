"""Tests for the parts that don't need a screen, a key, or a window."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from gamba.capture import (THUMB_SIZE, Frame, encode_jpeg, frame_difference,
                           should_trigger)
from gamba.config import Config, load_config, save_config
from gamba.provider import Answer, Usage


def make_frame(shade: int) -> Frame:
    image = Image.new("RGB", (32, 24), (shade, shade, shade))
    thumb = image.convert("L").resize(THUMB_SIZE, Image.BILINEAR)
    return Frame(image=image, thumb=thumb, captured_at=0.0)


class TestConfig(unittest.TestCase):
    def test_defaults_and_roundtrip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            save_config(Config(), path)
            loaded = load_config(path)
            self.assertEqual(loaded.model.model, "claude-haiku-4-5")
            self.assertEqual(loaded.model.deadline_seconds, 4.0)

    def test_partial_config_keeps_defaults(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"model": {"max_tokens": 42},
                                        "unknown_section": {"x": 1}}))
            loaded = load_config(path)
            self.assertEqual(loaded.model.max_tokens, 42)
            self.assertEqual(loaded.model.model, "claude-haiku-4-5")
            self.assertEqual(loaded.capture.max_width, 1280)

    def test_api_key_from_env(self):
        import os
        from unittest import mock

        config = Config()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertEqual(config.resolved_api_key(), "sk-test")
        config.model.api_key = "sk-explicit"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertEqual(config.resolved_api_key(), "sk-explicit")


class TestCapture(unittest.TestCase):
    def test_identical_frames_have_no_difference(self):
        self.assertEqual(frame_difference(make_frame(120), make_frame(120)), 0.0)

    def test_different_frames_exceed_threshold(self):
        self.assertGreater(frame_difference(make_frame(0), make_frame(255)), 6.0)

    def test_missing_frame_counts_as_changed(self):
        self.assertEqual(frame_difference(None, make_frame(10)), 255.0)

    def test_encode_jpeg_produces_jpeg(self):
        data = encode_jpeg(make_frame(90).image, 60)
        self.assertTrue(data.startswith(b"\xff\xd8"))


class TestWatchDecision(unittest.TestCase):
    """Continuous mode fires only on a settled screen that differs from the last answer."""

    def setUp(self):
        self.dark = make_frame(0)
        self.light = make_frame(255)

    def test_fires_when_settled_and_changed(self):
        self.assertTrue(should_trigger(self.light, self.light, self.dark, 6.0))

    def test_holds_off_mid_transition(self):
        self.assertFalse(should_trigger(self.dark, self.light, self.dark, 6.0))

    def test_does_not_repeat_on_an_unchanged_screen(self):
        self.assertFalse(should_trigger(self.light, self.light, self.light, 6.0))

    def test_first_poll_waits_for_a_second_frame(self):
        # previous is None -> difference 255 -> not settled yet
        self.assertFalse(should_trigger(None, self.light, None, 6.0))

    def test_no_frame_never_fires(self):
        self.assertFalse(should_trigger(self.light, None, self.dark, 6.0))


class TestAnswer(unittest.TestCase):
    def test_headline_and_detail_split(self):
        answer = Answer(text="B - Paris\nThe map is centred on France.\nCapital city.")
        self.assertEqual(answer.headline, "B - Paris")
        self.assertEqual(answer.detail,
                         "The map is centred on France.\nCapital city.")

    def test_empty_answer(self):
        answer = Answer()
        self.assertEqual(answer.headline, "")
        self.assertEqual(answer.detail, "")

    def test_single_line_answer_has_no_detail(self):
        self.assertEqual(Answer(text="42\n").detail, "")


class TestUsage(unittest.TestCase):
    def test_cost_accumulates(self):
        usage = Usage()
        usage.add(Answer(input_tokens=1_000_000, output_tokens=1_000_000))
        usage.add(Answer(input_tokens=0, output_tokens=1_000_000))
        self.assertEqual(usage.requests, 2)
        self.assertAlmostEqual(usage.cost_usd(1.0, 5.0), 11.0)


if __name__ == "__main__":
    unittest.main()
