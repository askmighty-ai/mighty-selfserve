"""Tests for standardized AI retry logic."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_retry import (
    ai_request_max_retries,
    call_with_retry,
    is_transient_error,
)


class TestAIRetry:
    def test_is_transient_error_detects_429(self):
        assert is_transient_error(RuntimeError("HTTP 429 rate limit exceeded"))
        assert is_transient_error(RuntimeError("503 temporarily unavailable"))
        assert not is_transient_error(ValueError("invalid schema"))

    def test_call_with_retry_retries_transient(self, monkeypatch):
        monkeypatch.setenv("AI_REQUEST_RETRY_BACKOFF_SECONDS", "0")
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("429 rate limit")
            return "ok"

        result = call_with_retry(fn, max_retries=3)
        assert result == "ok"
        assert len(calls) == 3

    def test_call_with_retry_does_not_retry_permanent(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("bad request")

        with pytest.raises(ValueError, match="bad request"):
            call_with_retry(fn, max_retries=3)
        assert len(calls) == 1

    def test_max_retries_from_env(self, monkeypatch):
        monkeypatch.setenv("AI_REQUEST_MAX_RETRIES", "5")
        assert ai_request_max_retries() == 5
