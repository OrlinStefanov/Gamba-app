"""Global hotkeys via pynput, running on its own listener thread."""

from __future__ import annotations

from typing import Callable, Dict, Optional


class HotkeyManager:
    def __init__(self, bindings: Dict[str, Callable[[], None]]) -> None:
        self.bindings = {combo: fn for combo, fn in bindings.items() if combo}
        self._listener = None
        self.error: Optional[Exception] = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception as exc:
            self.error = exc
            return False
        try:
            # Wrap each handler: an exception here would otherwise kill the
            # listener thread and silently disable every hotkey.
            safe = {combo: _guard(fn) for combo, fn in self.bindings.items()}
            self._listener = keyboard.GlobalHotKeys(safe)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as exc:
            self.error = exc
            return False

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


def _guard(fn: Callable[[], None]) -> Callable[[], None]:
    def wrapper() -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a bad handler must not kill hotkeys
            print(f"[gamba] hotkey handler failed: {type(exc).__name__}: {exc}")

    return wrapper
