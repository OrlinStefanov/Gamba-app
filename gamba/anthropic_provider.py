"""Claude vision calls with a hard latency budget.

The answer is streamed so that partial text is on screen well before the
deadline; when the budget runs out we stop reading and keep whatever arrived.
"""

from __future__ import annotations

import base64
import time
from typing import Callable, Optional

from .provider import Answer, MissingAPIKey


class AnthropicProvider:
    def __init__(self, config, api_key: str) -> None:
        if not api_key:
            raise MissingAPIKey(
                "No API key. Set ANTHROPIC_API_KEY, or put \"api_key\" in the "
                "model section of your Gamba config. Note that a Claude Pro or "
                "Max subscription does not include API access - create a "
                "pay-as-you-go key at https://console.anthropic.com/settings/keys."
            )
        import anthropic

        self._anthropic = anthropic
        self.config = config
        # The SDK's own retries are disabled because a retry can never land
        # inside the answer budget.
        self.client = anthropic.Anthropic(
            api_key=api_key,
            base_url=config.base_url or None,
            timeout=max(config.deadline_seconds + 5.0, 10.0),
            max_retries=0,
        )

    def warm_up(self) -> None:
        """Open the TLS connection so the first real request isn't paying for it."""
        try:
            self.client.messages.count_tokens(
                model=self.config.model,
                messages=[{"role": "user", "content": "ping"}],
            )
        except Exception:
            pass  # purely an optimisation

    def stream_answer(
        self,
        question: str,
        image_jpeg: bytes,
        on_text: Callable[[str], None],
        deadline_seconds: Optional[float] = None,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> Answer:
        anthropic = self._anthropic
        budget = deadline_seconds or self.config.deadline_seconds
        started = time.monotonic()
        deadline = started + budget
        answer = Answer()
        chunks: list[str] = []

        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(image_jpeg).decode("ascii"),
                },
            },
            {"type": "text", "text": question},
        ]

        try:
            with self.client.messages.stream(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=self.config.system_prompt,
                messages=[{"role": "user", "content": content}],
            ) as stream:
                for event in stream:
                    if is_cancelled():
                        answer.cancelled = True
                        break
                    if time.monotonic() > deadline:
                        answer.truncated = True
                        break
                    if event.type == "message_start":
                        answer.input_tokens = event.message.usage.input_tokens
                    elif event.type == "content_block_delta":
                        if getattr(event.delta, "type", None) == "text_delta":
                            chunks.append(event.delta.text)
                            on_text(event.delta.text)
                    elif event.type == "message_delta":
                        answer.output_tokens = event.usage.output_tokens
        except anthropic.APIConnectionError:
            answer.error = "Network error - could not reach the API."
        except anthropic.AuthenticationError:
            answer.error = "API key rejected (401). Check ANTHROPIC_API_KEY."
        except anthropic.PermissionDeniedError:
            answer.error = "API key lacks access to this model (403)."
        except anthropic.NotFoundError:
            answer.error = f"Unknown model id: {self.config.model} (404)."
        except anthropic.RateLimitError:
            answer.error = "Rate limited (429). Slow down or raise your limits."
        except anthropic.APIStatusError as exc:
            answer.error = f"API error {exc.status_code}: {exc.message}"
        except Exception as exc:  # keep the app alive on anything unexpected
            answer.error = f"{type(exc).__name__}: {exc}"

        answer.text = "".join(chunks)
        answer.latency = time.monotonic() - started
        if answer.truncated and not answer.text:
            answer.error = f"No response within {budget:.1f}s."
        return answer
