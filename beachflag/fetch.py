"""Tiny JSON-over-HTTPS client.

Standard library only, so the predictor installs with nothing but Python. Adds
the three things urllib does not give you for free: a timeout that is always
set, retry with backoff on the failures that are worth retrying, and a short
on-disk cache so re-running the CLI a few times in a row does not re-hit the
upstream API.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

USER_AGENT = "beachflag/1.0 (+https://github.com/OrlinStefanov/Gamba-app)"

DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3
CACHE_TTL_SECONDS = 600.0

# Retrying a 404 or a 400 just wastes the user's time; these are the codes where
# a second attempt plausibly succeeds.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    """A request failed in a way the caller is expected to report, not retry."""

    def __init__(self, url: str, reason: str, status: int | None = None) -> None:
        super().__init__(f"{reason} ({url})")
        self.url = url
        self.reason = reason
        self.status = status


def cache_dir() -> Path:
    override = os.environ.get("BEACHFLAG_CACHE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "beachflag"


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return cache_dir() / f"{digest}.json"


def _read_cache(url: str, ttl: float) -> Any | None:
    if ttl <= 0:
        return None
    path = _cache_path(url)
    try:
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(url: str, payload: Any) -> None:
    path = _cache_path(url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # A cache we cannot write is not a reason to fail the request.


def build_url(base: str, params: Mapping[str, Any]) -> str:
    """Join a base URL and params, dropping None and joining sequences with commas."""
    clean: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            clean[key] = ",".join(_format_scalar(v) for v in value)
        elif isinstance(value, bool):
            clean[key] = "true" if value else "false"
        else:
            clean[key] = _format_scalar(value)
    query = urllib.parse.urlencode(clean, safe=",:")
    return f"{base}?{query}" if query else base


def _format_scalar(value: Any) -> str:
    if isinstance(value, float):
        # Coordinates only need ~1 cm of precision, and trimming keeps cache
        # keys stable across float noise.
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def get_json(
    base: str,
    params: Mapping[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    cache_ttl: float = CACHE_TTL_SECONDS,
) -> Any:
    """GET a URL and parse JSON, with retry and a short-lived cache."""
    url = build_url(base, params or {})

    cached = _read_cache(url, cache_ttl)
    if cached is not None:
        return cached

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    delay = 1.0
    last_reason = "unknown error"

    for attempt in range(1, max(1, retries) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            payload = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = _error_body(exc)
            last_reason = f"HTTP {exc.code}{': ' + body if body else ''}"
            if exc.code not in RETRYABLE_STATUS:
                raise FetchError(url, last_reason, status=exc.code) from exc
        except urllib.error.URLError as exc:
            last_reason = f"network error: {exc.reason}"
        except TimeoutError:
            last_reason = f"timed out after {timeout:.0f}s"
        except ValueError as exc:
            raise FetchError(url, f"invalid JSON in response: {exc}") from exc
        else:
            _write_cache(url, payload)
            return payload

        if attempt < retries:
            time.sleep(delay)
            delay *= 2

    raise FetchError(url, last_reason)


def _error_body(exc: urllib.error.HTTPError) -> str:
    """Open-Meteo puts a useful 'reason' in the body of its 4xx responses."""
    try:
        detail = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return ""
    if isinstance(detail, dict):
        return str(detail.get("reason") or detail.get("error") or "")
    return ""
