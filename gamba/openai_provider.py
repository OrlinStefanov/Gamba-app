"""OpenAI vision calls with a hard latency budget.

Uses the Chat Completions endpoint with streaming, so partial text reaches the
overlay well before the deadline; when the budget runs out we stop reading and
keep whatever arrived.

`reasoning_effort` matters a lot here. The GPT-5.6 models reason by default,
and on a 4-second budget that reasoning is pure latency for a task that is
mostly "read this screen and answer". Setting it to "none" turns the model into
a fast non-reasoning responder, which is what the default config does.
"""

from __future__ import annotations

import base64
import time
from typing import Callable, Optional

from .config import DEFAULT_API_KEY_ENV_VARS
from .provider import Answer, MissingAPIKey


def key_env_var(config) -> str:
    """The env var this config reads its key from - the user's name, or the default."""
    return config.api_key_env or DEFAULT_API_KEY_ENV_VARS.get(
        config.provider.lower(), "OPENAI_API_KEY"
    )


class OpenAIProvider:
    def __init__(self, config, api_key: str) -> None:
        if not api_key:
            env_var = key_env_var(config)
            hint = (
                "A ChatGPT Plus/Pro subscription is billed separately from the "
                "API - create a key at https://platform.openai.com/api-keys and "
                "make sure the organisation has credit."
                if config.provider.lower() == "openai"
                else f"Set it to the key your endpoint at {config.base_url} expects."
            )
            raise MissingAPIKey(
                f"No API key. Set {env_var}, or put \"api_key\" in the model "
                f"section of your Gamba config. {hint}"
            )
        import openai

        self._openai = openai
        self.config = config
        # Retries are disabled: a retry can never land inside the answer budget.
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=config.base_url or None,
            timeout=max(config.deadline_seconds + 5.0, 10.0),
            max_retries=0,
        )

    def warm_up(self) -> None:
        """Open the TLS connection so the first real request isn't paying for it."""
        try:
            self.client.models.retrieve(self.config.model)
        except Exception:
            pass  # purely an optimisation

    def _request_kwargs(self, question: str, image_jpeg: bytes) -> dict:
        data_url = "data:image/jpeg;base64," + base64.standard_b64encode(
            image_jpeg
        ).decode("ascii")
        kwargs = {
            "model": self.config.model,
            "max_completion_tokens": self.config.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [
                {"role": "system", "content": self.config.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                "detail": self.config.image_detail or "auto",
                            },
                        },
                    ],
                },
            ],
        }
        # Only sent when configured: models outside the GPT-5 family reject it.
        if self.config.reasoning_effort:
            kwargs["reasoning_effort"] = self.config.reasoning_effort
        return kwargs

    def stream_answer(
        self,
        question: str,
        image_jpeg: bytes,
        on_text: Callable[[str], None],
        deadline_seconds: Optional[float] = None,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> Answer:
        openai = self._openai
        budget = deadline_seconds or self.config.deadline_seconds
        started = time.monotonic()
        deadline = started + budget
        answer = Answer()
        chunks: list[str] = []
        stream = None

        try:
            stream = self.client.chat.completions.create(
                **self._request_kwargs(question, image_jpeg)
            )
            for event in stream:
                if is_cancelled():
                    answer.cancelled = True
                    break
                if time.monotonic() > deadline:
                    answer.truncated = True
                    break
                # The final chunk carries usage and has no choices.
                usage = getattr(event, "usage", None)
                if usage is not None:
                    answer.input_tokens = usage.prompt_tokens or 0
                    answer.output_tokens = usage.completion_tokens or 0
                if not event.choices:
                    continue
                text = event.choices[0].delta.content
                if text:
                    chunks.append(text)
                    on_text(text)
        except openai.APIConnectionError:
            answer.error = "Network error - could not reach the API."
        except openai.AuthenticationError:
            answer.error = f"API key rejected (401). Check {key_env_var(self.config)}."
        except openai.PermissionDeniedError:
            answer.error = "API key lacks access to this model (403)."
        except openai.NotFoundError:
            answer.error = f"Unknown model id: {self.config.model} (404)."
        except openai.RateLimitError:
            answer.error = (
                "Rate limited or out of credit (429). Check your usage limits "
                "and billing at https://platform.openai.com/settings/organization/billing."
            )
        except openai.BadRequestError as exc:
            answer.error = _explain_bad_request(exc, self.config)
        except openai.APIStatusError as exc:
            answer.error = f"API error {exc.status_code}: {exc.message}"
        except Exception as exc:  # keep the app alive on anything unexpected
            answer.error = f"{type(exc).__name__}: {exc}"
        finally:
            if stream is not None:
                try:
                    stream.close()  # stop the download when we bail early
                except Exception:
                    pass

        answer.text = "".join(chunks)
        answer.latency = time.monotonic() - started
        if answer.truncated and not answer.text:
            answer.error = f"No response within {budget:.1f}s."
        return answer


def _explain_bad_request(exc, config) -> str:
    """400s here are usually one of two config mistakes - say which."""
    message = getattr(exc, "message", str(exc))
    lowered = message.lower()
    if "reasoning_effort" in lowered:
        return (
            f"{config.model} rejected reasoning_effort="
            f"{config.reasoning_effort!r}. Set model.reasoning_effort to \"\" "
            "in your config to omit it."
        )
    if "image" in lowered or "vision" in lowered:
        return f"{config.model} does not appear to accept image input: {message}"
    return f"Bad request (400): {message}"
