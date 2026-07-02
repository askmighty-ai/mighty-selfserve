"""Standard retry and timeout settings for AI provider calls."""

from __future__ import annotations

import os
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_TRANSIENT_MARKERS = (
    "429", "503", "502", "504", "timeout", "timed out", "rate limit",
    "overloaded", "temporarily unavailable", "connection reset",
    "connection error", "server error", "resource exhausted",
)


def _env_float(name: str, *, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _env_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def ai_request_timeout_seconds() -> float:
    return _env_float("AI_REQUEST_TIMEOUT_SECONDS", default=60.0)


def ai_request_max_retries() -> int:
    return _env_int("AI_REQUEST_MAX_RETRIES", default=3)


def ai_request_retry_backoff_seconds() -> float:
    return _env_float("AI_REQUEST_RETRY_BACKOFF_SECONDS", default=1.0)


def is_transient_error(exc: Exception) -> bool:
    return any(m in str(exc).lower() for m in _TRANSIENT_MARKERS)


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int | None = None,
    backoff_seconds: float | None = None,
    retry_on: Callable[[Exception], bool] | None = None,
) -> T:
    attempts = (max_retries if max_retries is not None else ai_request_max_retries()) + 1
    delay = backoff_seconds if backoff_seconds is not None else ai_request_retry_backoff_seconds()
    should_retry = retry_on or is_transient_error
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts - 1 or not should_retry(exc):
                raise
            time.sleep(delay * (2**attempt))
    assert last_exc is not None
    raise last_exc
