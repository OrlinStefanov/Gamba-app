"""Provider tests with a stubbed stream - no network, no API key."""

import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamba.anthropic_provider import AnthropicProvider
from gamba.config import ModelConfig
from gamba.provider import MissingAPIKey


def event(kind, **kwargs):
    return types.SimpleNamespace(type=kind, **kwargs)


def start_event(input_tokens):
    usage = types.SimpleNamespace(input_tokens=input_tokens)
    return event("message_start", message=types.SimpleNamespace(usage=usage))


def text_event(text):
    return event("content_block_delta",
                 delta=types.SimpleNamespace(type="text_delta", text=text))


def stop_event(output_tokens):
    return event("message_delta", usage=types.SimpleNamespace(output_tokens=output_tokens))


class FakeStream:
    """Context manager that yields canned events, optionally slowly."""

    def __init__(self, events, delay=0.0, raises=None):
        self.events = events
        self.delay = delay
        self.raises = raises

    def __enter__(self):
        if self.raises is not None:
            raise self.raises
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        for item in self.events:
            if self.delay:
                time.sleep(self.delay)
            yield item


def install_fake_stream(provider, stream):
    provider.client = types.SimpleNamespace(
        messages=types.SimpleNamespace(stream=lambda **kwargs: stream)
    )


def build_provider(**overrides):
    overrides.setdefault("provider", "anthropic")
    overrides.setdefault("model", "claude-haiku-4-5")
    config = ModelConfig(**overrides)
    return AnthropicProvider(config, api_key="sk-test-not-used")


class TestProvider(unittest.TestCase):
    def test_missing_key_is_explicit_about_pro_plans(self):
        with self.assertRaises(MissingAPIKey) as ctx:
            AnthropicProvider(ModelConfig(), api_key="")
        self.assertIn("does not include API access", str(ctx.exception))

    def test_streams_text_and_usage(self):
        provider = build_provider()
        install_fake_stream(provider, FakeStream([
            start_event(1200),
            text_event("B - Paris"),
            text_event("\nThe map is centred on France."),
            stop_event(24),
        ]))
        chunks = []
        answer = provider.stream_answer("what?", b"jpeg", chunks.append)

        self.assertIsNone(answer.error)
        self.assertFalse(answer.truncated)
        self.assertEqual(answer.headline, "B - Paris")
        self.assertEqual(answer.detail, "The map is centred on France.")
        self.assertEqual(answer.input_tokens, 1200)
        self.assertEqual(answer.output_tokens, 24)
        self.assertEqual(len(chunks), 2)

    def test_deadline_truncates_but_keeps_partial_text(self):
        provider = build_provider()
        install_fake_stream(provider, FakeStream(
            [start_event(10)] + [text_event(f"word{i} ") for i in range(50)],
            delay=0.02,
        ))
        answer = provider.stream_answer("q", b"jpeg", lambda _c: None,
                                        deadline_seconds=0.15)
        self.assertTrue(answer.truncated)
        self.assertTrue(answer.text)
        self.assertLess(answer.latency, 1.0)

    def test_deadline_with_no_output_reports_an_error(self):
        provider = build_provider()
        install_fake_stream(provider, FakeStream(
            [start_event(10)] + [text_event("x") for _ in range(5)], delay=0.2))
        answer = provider.stream_answer("q", b"jpeg", lambda _c: None,
                                        deadline_seconds=0.05)
        self.assertTrue(answer.truncated)
        self.assertIn("No response within", answer.error or "")

    def test_cancellation_stops_early(self):
        provider = build_provider()
        install_fake_stream(provider, FakeStream(
            [start_event(10)] + [text_event("x") for _ in range(20)], delay=0.01))
        seen = []

        def cancel_after_two():
            return len(seen) >= 2

        answer = provider.stream_answer("q", b"jpeg", seen.append,
                                        is_cancelled=cancel_after_two)
        self.assertTrue(answer.cancelled)
        self.assertEqual(len(seen), 2)

    def test_connection_error_is_reported_not_raised(self):
        import anthropic
        import httpx

        provider = build_provider()
        install_fake_stream(provider, FakeStream(
            [], raises=anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))))
        answer = provider.stream_answer("q", b"jpeg", lambda _c: None)
        self.assertIn("Network error", answer.error or "")

    def test_unexpected_error_is_reported_not_raised(self):
        provider = build_provider()
        install_fake_stream(provider, FakeStream([], raises=ValueError("boom")))
        answer = provider.stream_answer("q", b"jpeg", lambda _c: None)
        self.assertEqual(answer.error, "ValueError: boom")


if __name__ == "__main__":
    unittest.main()
