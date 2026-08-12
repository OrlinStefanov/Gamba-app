"""Configuration loading/saving for Gamba.

Config lives at ~/.gamba/config.json by default. Missing keys fall back to the
defaults below, so an old config file keeps working after an upgrade.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

DEFAULT_CONFIG_PATH = Path.home() / ".gamba" / "config.json"

DEFAULT_SYSTEM_PROMPT = (
    "You answer questions about what is currently on the user's screen. "
    "You are given one screenshot and a question.\n"
    "\n"
    "Rules:\n"
    "- The FIRST line is the direct answer, on its own, with no preamble.\n"
    "- Then at most two short lines of justification.\n"
    "- If the screen shows a multiple-choice question, the first line is the "
    "option label and its text, e.g. \"B - Paris\".\n"
    "- If the screen does not contain enough information, the first line is "
    "\"Unclear\" followed by what is missing.\n"
    "- Never describe the screenshot itself unless asked to.\n"
    "- Be terse. You are being read at a glance."
)


@dataclass
class CaptureConfig:
    #: mss monitor index. 1 = primary display, 0 = all displays stitched together.
    monitor: int = 1
    #: Optional fixed region {"left":, "top":, "width":, "height":}; overrides monitor.
    region: Optional[dict] = None
    #: Screenshots are downscaled to this width before being sent. Lower = faster + cheaper.
    max_width: int = 1280
    jpeg_quality: int = 60
    #: How often the background thread grabs a frame. The newest frame is always
    #: ready, so capture time is off the critical path when you press the hotkey.
    fps: float = 4.0


@dataclass
class ModelConfig:
    #: "openai" or "anthropic".
    provider: str = "openai"
    model: str = "gpt-5.6-luna"
    max_tokens: int = 300
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    #: Hard wall-clock budget for one answer. Streaming stops at this point and
    #: whatever has arrived is kept.
    deadline_seconds: float = 4.0
    #: OpenAI only. "none" turns reasoning off, which is what keeps GPT-5.6
    #: inside the latency budget. Set to "" to omit the parameter entirely
    #: (required for models that don't accept it, e.g. gpt-4.1).
    reasoning_effort: str = "none"
    #: OpenAI only: "auto", "low" (cheap, 512px - too lossy for small text),
    #: or "high" (best for reading dense UI text).
    image_detail: str = "auto"
    #: Endpoint override: Azure, a gateway, a local proxy, or your own
    #: OpenAI-compatible API. Required when provider is "custom".
    base_url: str = ""
    #: A label for your endpoint, shown in the UI and in `doctor`. Cosmetic -
    #: it does not affect which URL is called.
    api_name: str = ""
    #: USD per million tokens, used only for the running cost readout.
    input_cost_per_mtok: float = 0.20
    output_cost_per_mtok: float = 1.20
    #: The key itself. Prefer an environment variable over writing it here.
    api_key: str = ""
    #: Name of the environment variable holding the key. Leave empty to use the
    #: provider default (OPENAI_API_KEY / ANTHROPIC_API_KEY / GAMBA_API_KEY).
    #: Set it when your key already lives under a name of your own choosing.
    api_key_env: str = ""


#: Sensible defaults per provider, applied by `--provider` on the command line.
PROVIDER_DEFAULTS = {
    "openai": {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "none",
        "input_cost_per_mtok": 0.20,
        "output_cost_per_mtok": 1.20,
    },
    "anthropic": {
        "model": "claude-haiku-4-5",
        "reasoning_effort": "",
        "input_cost_per_mtok": 1.0,
        "output_cost_per_mtok": 5.0,
    },
    # Any OpenAI-compatible endpoint under a name of your own. Set base_url and
    # model yourself; pricing is unknown, so the cost readout starts at zero.
    "custom": {
        "model": "",
        "reasoning_effort": "",
        "input_cost_per_mtok": 0.0,
        "output_cost_per_mtok": 0.0,
    },
}

DEFAULT_API_KEY_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "custom": "GAMBA_API_KEY",
}

#: Searched in order; values never overwrite a variable already in the
#: environment, so a real env var always wins over a file.
DOTENV_PATHS = (Path(".env"), Path.home() / ".gamba" / ".env")


@dataclass
class WatchConfig:
    """Continuous mode: re-ask automatically when the screen changes."""

    enabled_at_startup: bool = False
    question: str = "What is the question on screen, and what is the answer?"
    #: Seconds between change checks.
    interval: float = 1.0
    #: Mean per-pixel difference (0-255) on a 64x64 grayscale thumbnail that
    #: counts as "the screen changed".
    change_threshold: float = 6.0
    #: Minimum seconds between two automatic requests.
    cooldown: float = 6.0


@dataclass
class HotkeyConfig:
    ask: str = "<ctrl>+<alt>+a"
    quick: str = "<ctrl>+<alt>+s"
    toggle_watch: str = "<ctrl>+<alt>+w"
    toggle_overlay: str = "<ctrl>+<alt>+h"
    quit: str = "<ctrl>+<alt>+q"


@dataclass
class UIConfig:
    width: int = 460
    #: Window position. Negative values are measured from the right/bottom edge.
    x: int = -40
    y: int = -40
    opacity: float = 0.88
    background: str = "#12141a"
    foreground: str = "#f2f4f8"
    accent: str = "#7ee787"
    muted: str = "#8b95a5"
    answer_font_size: int = 16
    detail_font_size: int = 10
    #: Hide the overlay this many seconds after an answer completes. 0 = never.
    autohide_seconds: float = 0.0


@dataclass
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    ui: UIConfig = field(default_factory=UIConfig)

    @property
    def api_key_env_var(self) -> str:
        """The environment variable the key is read from - yours, or the default."""
        return self.model.api_key_env or DEFAULT_API_KEY_ENV_VARS.get(
            self.model.provider.lower(), "OPENAI_API_KEY"
        )

    @property
    def api_label(self) -> str:
        """What to call this endpoint in the UI: your name for it, or the provider."""
        return self.model.api_name or self.model.provider

    def resolve_api_key(self) -> tuple:
        """Return (key, human-readable source). First match wins.

        1. `api_key` in the config file
        2. the environment variable named by `api_key_env` (yours)
        3. the provider's default environment variable

        `.env` files are loaded into the environment beforehand by
        `load_dotenv_files()`, so they are picked up by steps 2 and 3 without
        ever overriding a variable that is genuinely set in the environment.
        """
        if self.model.api_key:
            return self.model.api_key, "config file (model.api_key)"

        custom = self.model.api_key_env
        if custom:
            value = os.environ.get(custom, "")
            if value:
                return value, f"environment variable {custom}"
            # A custom name is an explicit instruction: don't quietly fall back
            # to the default variable and leave the user debugging the wrong one.
            return "", f"environment variable {custom} (not set)"

        default_var = self.api_key_env_var
        value = os.environ.get(default_var, "")
        return (value, f"environment variable {default_var}") if value else (
            "", f"environment variable {default_var} (not set)")

    def resolved_api_key(self) -> str:
        return self.resolve_api_key()[0]

    def api_key_source(self) -> str:
        return self.resolve_api_key()[1]


def apply_provider_defaults(config: "Config", provider: str) -> "Config":
    """Switch providers, taking that provider's model and pricing with it.

    Used by the `--provider` flag: picking a provider without also restating
    the model and its per-token prices should not leave the previous
    provider's values behind.
    """
    provider = provider.lower()
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(
            f"Unknown provider {provider!r}. Use \"openai\" or \"anthropic\"."
        )
    config.model.provider = provider
    for key, value in PROVIDER_DEFAULTS[provider].items():
        setattr(config.model, key, value)
    return config


def _merge(instance: Any, data: dict) -> Any:
    """Overlay `data` onto a dataclass instance, recursing into nested dataclasses."""
    if not isinstance(data, dict):
        return instance
    known = {f.name: f for f in fields(instance)}
    for key, value in data.items():
        if key not in known:
            continue
        current = getattr(instance, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(instance, key, value)
    return instance


def load_dotenv_files(paths=DOTENV_PATHS) -> list:
    """Load `KEY=value` lines from .env files into os.environ.

    Deliberately does not overwrite variables that are already set: a real
    environment variable should always beat a file left over from last week.
    Returns the files that were read, for `doctor` to report.
    """
    loaded = []
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            name, sep, value = line.partition("=")
            if not sep:
                continue
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name and name not in os.environ:
                os.environ[name] = value
        loaded.append(path)
    return loaded


def load_config(path: Optional[Path] = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    config = Config()
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            _merge(config, json.load(handle))
    return config


def save_config(config: Config, path: Optional[Path] = None) -> Path:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(asdict(config), handle, indent=2)
        handle.write("\n")
    return path
