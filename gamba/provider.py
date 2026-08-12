"""Provider-neutral result types and the provider factory.

Each backend lives in its own module and imports its SDK lazily, so you only
need the SDK for the provider you actually use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Answer:
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency: float = 0.0
    truncated: bool = False  #: deadline hit before the model finished
    cancelled: bool = False  #: superseded by a newer request
    error: Optional[str] = None

    @property
    def headline(self) -> str:
        return self.text.strip().split("\n", 1)[0] if self.text.strip() else ""

    @property
    def detail(self) -> str:
        parts = self.text.strip().split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""


@dataclass
class Usage:
    """Running totals for the session cost readout."""

    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0

    def add(self, answer: Answer) -> None:
        self.input_tokens += answer.input_tokens
        self.output_tokens += answer.output_tokens
        self.requests += 1

    def cost_usd(self, input_per_mtok: float, output_per_mtok: float) -> float:
        return (
            self.input_tokens * input_per_mtok / 1_000_000
            + self.output_tokens * output_per_mtok / 1_000_000
        )


class MissingAPIKey(RuntimeError):
    pass


def create_provider(model_config, api_key: str):
    """Build the provider named by `model_config.provider`."""
    provider = (model_config.provider or "openai").lower()
    if provider == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(model_config, api_key)
    if provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(model_config, api_key)
    raise ValueError(
        f"Unknown provider {provider!r}. Use \"openai\" or \"anthropic\"."
    )
