"""Wiring: capture thread + hotkeys + auto-watch + overlay."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .capture import ScreenCapturer, encode_jpeg, should_trigger
from .config import Config
from .hotkeys import HotkeyManager
from .overlay import Overlay
from .provider import Answer, Usage, create_provider


class GambaApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.usage = Usage()
        self.provider = create_provider(config.model, config.resolved_api_key())
        self.capturer = ScreenCapturer(config.capture)
        self.overlay = Overlay(config.ui, on_question=self.ask, on_quit=self._teardown)
        self.hotkeys = HotkeyManager(
            {
                config.hotkeys.ask: lambda: self.overlay.ask_question(),
                config.hotkeys.quick: self.ask_default,
                config.hotkeys.toggle_watch: self.toggle_watch,
                config.hotkeys.toggle_overlay: self.overlay.toggle_visible,
                config.hotkeys.quit: self.overlay.shutdown,
            }
        )
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gamba-ask")
        self._request_lock = threading.Lock()
        self._request_id = 0
        self._last_request_at = 0.0
        self._watch_enabled = config.watch.enabled_at_startup
        self._stop = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None

    # -- lifecycle ----------------------------------------------------------

    def run(self) -> None:
        self.capturer.start()
        threading.Thread(target=self.provider.warm_up, name="gamba-warmup",
                         daemon=True).start()

        if not self.hotkeys.start():
            self.overlay.set_status(f"hotkeys unavailable: {self.hotkeys.error}", "#ff7b72")
        else:
            self.overlay.set_status(f"ready — {self.config.hotkeys.quick} to ask")
        self.overlay.set_meta(self._usage_line())

        self._watch_thread = threading.Thread(target=self._watch_loop,
                                              name="gamba-watch", daemon=True)
        self._watch_thread.start()
        if self._watch_enabled:
            self.overlay.set_status("watching screen", self.config.ui.accent)

        self.overlay.mainloop()

    def _teardown(self) -> None:
        self._stop.set()
        self.hotkeys.stop()
        self.capturer.stop()
        self._executor.shutdown(wait=False)

    # -- asking -------------------------------------------------------------

    def ask_default(self) -> None:
        self.ask(self.config.watch.question)

    def ask(self, question: str) -> None:
        frame = self.capturer.latest()
        if frame is None:
            error = self.capturer.error
            self.overlay.set_status(
                f"no screenshot yet ({error})" if error else "no screenshot yet",
                "#ff7b72",
            )
            return

        with self._request_lock:
            self._request_id += 1
            request_id = self._request_id
            self._last_request_at = time.monotonic()

        self.overlay.start_request(question)
        self._executor.submit(self._run_request, request_id, question, frame)

    def _is_stale(self, request_id: int) -> bool:
        with self._request_lock:
            return request_id != self._request_id

    def _run_request(self, request_id: int, question: str, frame) -> None:
        try:
            image = encode_jpeg(frame.image, self.config.capture.jpeg_quality)
        except Exception as exc:
            self.overlay.finish(Answer(error=f"screenshot encode failed: {exc}"), "")
            return

        def on_text(chunk: str) -> None:
            if not self._is_stale(request_id):
                self.overlay.append_text(chunk)

        answer = self.provider.stream_answer(
            question=question,
            image_jpeg=image,
            on_text=on_text,
            is_cancelled=lambda: self._is_stale(request_id) or self._stop.is_set(),
        )
        if answer.cancelled or self._is_stale(request_id):
            return

        self.usage.add(answer)
        meta = f"{answer.latency:.1f}s · {self._usage_line()}"
        self.overlay.finish(answer, meta)

    def _usage_line(self) -> str:
        cost = self.usage.cost_usd(
            self.config.model.input_cost_per_mtok,
            self.config.model.output_cost_per_mtok,
        )
        return f"{self.usage.requests} asks · ${cost:.4f}"

    # -- continuous mode ----------------------------------------------------

    def toggle_watch(self) -> None:
        self._watch_enabled = not self._watch_enabled
        if self._watch_enabled:
            self.overlay.show()
            self.overlay.set_status("watching screen", self.config.ui.accent)
        else:
            self.overlay.set_status("watch off")

    def _watch_loop(self) -> None:
        """Re-ask when the screen changes and then settles."""
        watch = self.config.watch
        previous = None  # frame seen on the previous poll
        answered = None  # frame the last automatic answer was based on

        while not self._stop.wait(watch.interval):
            if not self._watch_enabled:
                previous = answered = None
                continue
            frame = self.capturer.latest()
            if frame is None:
                continue
            triggered = should_trigger(previous, frame, answered,
                                       watch.change_threshold)
            previous = frame
            if not triggered:
                continue
            with self._request_lock:
                since_last = time.monotonic() - self._last_request_at
            if since_last < watch.cooldown:
                continue
            answered = frame
            self.ask(watch.question)
