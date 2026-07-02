"""Tests for standardized AI retry and timeout helpers."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_retry import (
    ai_request_max_retries,
    ai_request_retry_backoff_seconds,
    ai_request_timeout_seconds,
    call_with_retry,
    is_transient_error,
)


class TestAIRetry:
    def test_is_transient_error_detects_rate_limit(self):
        assert is_transient_error(RuntimeError("429 rate limit exceeded"))

    def test_is_transient_error_rejects_validation_error(self):
        assert is_transient_error(ValueError("invalid json")) is False

    def test_call_with_retry_retries_transient(self, monkeypatch):
        monkeypatch.setenv("AI_REQUEST_MAX_RETRIES", "2")
        monkeypatch.setenv("AI_REQUEST_RETRY_BACKOFF_SECONDS", "0")
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("503 temporarily unavailable")
            return "ok"

        assert call_with_retry(flaky) == "ok"
        assert len(calls) == 2

    def test_env_defaults(self, monkeypatch):
        monkeypatch.delenv("AI_REQUEST_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("AI_REQUEST_MAX_RETRIES", raising=False)
        monkeypatch.delenv("AI_REQUEST_RETRY_BACKOFF_SECONDS", raising=False)
        assert ai_request_timeout_seconds() == 60.0
        assert ai_request_max_retries() == 3
        assert ai_request_retry_backoff_seconds() == 1.0
