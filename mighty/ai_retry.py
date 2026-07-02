"""Standardized retry and timeout for AI provider calls."""

from __future__ import annotations

import os
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_TRANSIENT_PATTERNS = (
    "429",
    "rate limit",
    "timeout",
    "timed out",
    "503",
    "502",
    "500",
    "connection",
    "temporarily unavailable",
    "overloaded",
)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def ai_request_timeout_seconds() -> float:
    return _env_float("AI_REQUEST_TIMEOUT_SECONDS", 60.0)


def ai_request_max_retries() -> int:
    return _env_int("AI_REQUEST_MAX_RETRIES", 3)


def ai_request_retry_backoff_seconds() -> float:
    return _env_float("AI_REQUEST_RETRY_BACKOFF_SECONDS", 1.0)


def is_transient_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(pattern in message for pattern in _TRANSIENT_PATTERNS)


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_retries: int | None = None,
    backoff_seconds: float | None = None,
) -> T:
    retries = ai_request_max_retries() if max_retries is None else max_retries
    backoff = (
        ai_request_retry_backoff_seconds()
        if backoff_seconds is None
        else backoff_seconds
    )
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if attempt >= retries or not is_transient_error(exc):
                raise
            attempt += 1
            if backoff > 0:
                time.sleep(backoff * attempt)
