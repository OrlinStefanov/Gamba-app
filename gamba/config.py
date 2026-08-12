"""Configuration loading/saving for Gamba.

Config lives at ~/.gamba/config.json by default. Missing keys fall back to the
defaults below, so an old config file keeps working after an upgrade.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

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
    model: str = "claude-haiku-4-5"
    max_tokens: int = 300
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    #: Hard wall-clock budget for one answer. Streaming stops at this point and
    #: whatever has arrived is kept.
    deadline_seconds: float = 4.0
    #: USD per million tokens, used only for the running cost readout.
    input_cost_per_mtok: float = 1.0
    output_cost_per_mtok: float = 5.0
    #: Read from ANTHROPIC_API_KEY if left empty.
    api_key: str = ""


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

    def resolved_api_key(self) -> str:
        return self.model.api_key or os.environ.get("ANTHROPIC_API_KEY", "")


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
