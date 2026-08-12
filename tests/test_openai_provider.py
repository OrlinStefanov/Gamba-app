"""OpenAI provider tests with a stubbed stream - no network, no API key."""

import base64
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gamba.config import Config, ModelConfig, apply_provider_defaults
from gamba.openai_provider import OpenAIProvider
from gamba.provider import MissingAPIKey, create_provider


def delta_chunk(text):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=text))],
        usage=None,
    )


def usage_chunk(prompt_tokens, completion_tokens):
    return types.SimpleNamespace(
        choices=[],
        usage=types.SimpleNamespace(prompt_tokens=prompt_tokens,
                                    completion_tokens=completion_tokens),
    )


class FakeCompletionStream:
    def __init__(self, chunks, delay=0.0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            if self.delay:
                time.sleep(self.delay)
            yield chunk

    def close(self):
        self.closed = True


def install_fake(provider, stream=None, raises=None):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        if raises is not None:
            raise raises
        return stream

    provider.client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    )
    return captured


def build_provider(**overrides):
    return OpenAIProvider(ModelConfig(**overrides), api_key="sk-test-not-used")


class TestOpenAIProvider(unittest.TestCase):
    def test_missing_key_explains_chatgpt_billing(self):
        with self.assertRaises(MissingAPIKey) as ctx:
            OpenAIProvider(ModelConfig(), api_key="")
        self.assertIn("billed separately", str(ctx.exception))

    def test_streams_text_and_usage(self):
        provider = build_provider()
        stream = FakeCompletionStream([
            delta_chunk("B - Paris"),
            delta_chunk("\nThe map is centred on France."),
            usage_chunk(1500, 22),
        ])
        install_fake(provider, stream)
        chunks = []
        answer = provider.stream_answer("what?", b"jpegbytes", chunks.append)

        self.assertIsNone(answer.error)
        self.assertEqual(answer.headline, "B - Paris")
        self.assertEqual(answer.detail, "The map is centred on France.")
        self.assertEqual(answer.input_tokens, 1500)
        self.assertEqual(answer.output_tokens, 22)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(stream.closed, "stream should be closed after reading")

    def test_request_shape_includes_image_and_reasoning_effort(self):
        provider = build_provider(model="gpt-5.6-luna", reasoning_effort="none",
                                  image_detail="high", max_tokens=123)
        captured = install_fake(provider, FakeCompletionStream([]))
        provider.stream_answer("what is on screen?", b"\xff\xd8jpeg", lambda _c: None)

        self.assertEqual(captured["model"], "gpt-5.6-luna")
        self.assertEqual(captured["reasoning_effort"], "none")
        self.assertEqual(captured["max_completion_tokens"], 123)
        self.assertTrue(captured["stream"])
        self.assertTrue(captured["stream_options"]["include_usage"])

        system, user = captured["messages"]
        self.assertEqual(system["role"], "system")
        text_part, image_part = user["content"]
        self.assertEqual(text_part["text"], "what is on screen?")
        self.assertEqual(image_part["image_url"]["detail"], "high")
        url = image_part["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))
        self.assertEqual(
            base64.standard_b64decode(url.split(",", 1)[1]), b"\xff\xd8jpeg"
        )

    def test_reasoning_effort_omitted_when_blank(self):
        provider = build_provider(model="gpt-4.1-mini", reasoning_effort="")
        captured = install_fake(provider, FakeCompletionStream([]))
        provider.stream_answer("q", b"jpeg", lambda _c: None)
        self.assertNotIn("reasoning_effort", captured)

    def test_deadline_truncates_but_keeps_partial_text(self):
        provider = build_provider()
        install_fake(provider, FakeCompletionStream(
            [delta_chunk(f"word{i} ") for i in range(50)], delay=0.02))
        answer = provider.stream_answer("q", b"jpeg", lambda _c: None,
                                        deadline_seconds=0.15)
        self.assertTrue(answer.truncated)
        self.assertTrue(answer.text)
        self.assertLess(answer.latency, 1.0)

    def test_cancellation_stops_early(self):
        provider = build_provider()
        install_fake(provider, FakeCompletionStream(
            [delta_chunk("x") for _ in range(20)], delay=0.01))
        seen = []
        answer = provider.stream_answer("q", b"jpeg", seen.append,
                                        is_cancelled=lambda: len(seen) >= 2)
        self.assertTrue(answer.cancelled)
        self.assertEqual(len(seen), 2)

    def test_rate_limit_error_mentions_billing(self):
        import httpx
        import openai

        provider = build_provider()
        response = httpx.Response(
            429, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        )
        install_fake(provider, raises=openai.RateLimitError(
            "rate limited", response=response, body=None))
        answer = provider.stream_answer("q", b"jpeg", lambda _c: None)
        self.assertIn("billing", (answer.error or "").lower())

    def test_bad_request_about_reasoning_effort_is_actionable(self):
        import httpx
        import openai

        provider = build_provider(model="gpt-4.1-mini", reasoning_effort="none")
        response = httpx.Response(
            400, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        )
        install_fake(provider, raises=openai.BadRequestError(
            "Unsupported parameter: 'reasoning_effort'", response=response, body=None))
        answer = provider.stream_answer("q", b"jpeg", lambda _c: None)
        self.assertIn("reasoning_effort", answer.error)
        self.assertIn('set model.reasoning_effort to ""', answer.error.lower())

    def test_unexpected_error_is_reported_not_raised(self):
        provider = build_provider()
        install_fake(provider, raises=ValueError("boom"))
        answer = provider.stream_answer("q", b"jpeg", lambda _c: None)
        self.assertEqual(answer.error, "ValueError: boom")


class TestProviderSelection(unittest.TestCase):
    def test_default_config_targets_openai(self):
        config = Config()
        self.assertEqual(config.model.provider, "openai")
        self.assertEqual(config.model.model, "gpt-5.6-luna")
        self.assertEqual(config.api_key_env_var, "OPENAI_API_KEY")

    def test_factory_builds_the_named_provider(self):
        config = Config()
        self.assertIsInstance(
            create_provider(config.model, "sk-test"), OpenAIProvider)

        apply_provider_defaults(config, "anthropic")
        from gamba.anthropic_provider import AnthropicProvider

        self.assertIsInstance(
            create_provider(config.model, "sk-test"), AnthropicProvider)

    def test_switching_provider_carries_model_and_pricing(self):
        config = Config()
        apply_provider_defaults(config, "anthropic")
        self.assertEqual(config.model.model, "claude-haiku-4-5")
        self.assertEqual(config.model.input_cost_per_mtok, 1.0)
        self.assertEqual(config.model.reasoning_effort, "")
        self.assertEqual(config.api_key_env_var, "ANTHROPIC_API_KEY")

        apply_provider_defaults(config, "openai")
        self.assertEqual(config.model.model, "gpt-5.6-luna")
        self.assertEqual(config.model.input_cost_per_mtok, 0.20)

    def test_unknown_provider_is_rejected(self):
        config = Config()
        config.model.provider = "gemini"
        with self.assertRaises(ValueError):
            create_provider(config.model, "sk-test")
        with self.assertRaises(ValueError):
            apply_provider_defaults(config, "gemini")

    def test_api_key_env_var_follows_provider(self):
        import os
        from unittest import mock

        config = Config()
        env = {"OPENAI_API_KEY": "sk-openai", "ANTHROPIC_API_KEY": "sk-anthropic"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(config.resolved_api_key(), "sk-openai")
            apply_provider_defaults(config, "anthropic")
            self.assertEqual(config.resolved_api_key(), "sk-anthropic")


if __name__ == "__main__":
    unittest.main()
