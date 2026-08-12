"""Continuous screen capture.

A background thread keeps the most recent (already downscaled) frame in memory,
so pressing the hotkey costs a JPEG encode rather than a full grab + resize.
That is worth roughly 50-150ms out of the answer budget on a 4K display.
"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageChops, ImageStat

THUMB_SIZE = (64, 64)


@dataclass
class Frame:
    image: Image.Image  #: RGB, downscaled to at most capture.max_width
    thumb: Image.Image  #: 64x64 grayscale, used for change detection
    captured_at: float


def frame_difference(a: Optional[Frame], b: Optional[Frame]) -> float:
    """Mean absolute per-pixel difference of two thumbnails, 0.0-255.0."""
    if a is None or b is None:
        return 255.0
    diff = ImageChops.difference(a.thumb, b.thumb)
    return ImageStat.Stat(diff).mean[0]


def should_trigger(previous: Optional[Frame], current: Optional[Frame],
                   answered: Optional[Frame], threshold: float) -> bool:
    """Decide whether continuous mode should fire an automatic re-ask.

    Two conditions, both required: the screen has *settled* (this frame matches
    the previous poll, so we are not firing mid-transition or mid-animation),
    and it differs from the frame the last automatic answer was based on.
    """
    if current is None:
        return False
    settled = frame_difference(previous, current) < threshold
    changed = frame_difference(answered, current) >= threshold
    return settled and changed


def encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


class ScreenCapturer(threading.Thread):
    def __init__(self, config) -> None:
        super().__init__(name="gamba-capture", daemon=True)
        self.config = config
        self._latest: Optional[Frame] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._first_frame = threading.Event()
        self._error: Optional[Exception] = None

    # -- public API ---------------------------------------------------------

    def latest(self) -> Optional[Frame]:
        with self._lock:
            return self._latest

    def wait_for_first_frame(self, timeout: float = 5.0) -> bool:
        return self._first_frame.wait(timeout)

    @property
    def error(self) -> Optional[Exception]:
        return self._error

    def stop(self) -> None:
        self._stop.set()

    # -- thread body --------------------------------------------------------

    def run(self) -> None:
        import mss  # imported here: the instance must live on this thread

        interval = 1.0 / max(self.config.fps, 0.5)
        try:
            with mss.mss() as sct:
                region = self._region(sct)
                while not self._stop.is_set():
                    started = time.monotonic()
                    try:
                        frame = self._grab(sct, region)
                    except Exception as exc:  # a display can disappear mid-run
                        self._error = exc
                        self._stop.wait(1.0)
                        continue
                    with self._lock:
                        self._latest = frame
                    self._first_frame.set()
                    self._stop.wait(max(0.0, interval - (time.monotonic() - started)))
        except Exception as exc:
            self._error = exc
            self._first_frame.set()

    def _region(self, sct) -> dict:
        if self.config.region:
            return dict(self.config.region)
        monitors = sct.monitors
        index = self.config.monitor
        if index < 0 or index >= len(monitors):
            index = 1 if len(monitors) > 1 else 0
        return monitors[index]

    def _grab(self, sct, region: dict) -> Frame:
        raw = sct.grab(region)
        image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        max_width = self.config.max_width
        if max_width and image.width > max_width:
            height = round(image.height * max_width / image.width)
            image = image.resize((max_width, height), Image.LANCZOS)
        thumb = image.convert("L").resize(THUMB_SIZE, Image.BILINEAR)
        return Frame(image=image, thumb=thumb, captured_at=time.monotonic())


def capture_once(config) -> Frame:
    """Single synchronous grab, for the one-shot CLI path."""
    capturer = ScreenCapturer(config)
    capturer.start()
    try:
        if not capturer.wait_for_first_frame(10.0):
            raise RuntimeError("timed out waiting for a screenshot")
        if capturer.error is not None:
            raise capturer.error
        frame = capturer.latest()
        if frame is None:
            raise RuntimeError("screen capture produced no frame")
        return frame
    finally:
        capturer.stop()
