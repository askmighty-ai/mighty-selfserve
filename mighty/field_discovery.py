"""
AI field discovery budget guardrails.

Kill switch, input caps, provider/content-hash schema cache, and failure
negative-cache to avoid repeat AI spend on unchanged or failing inputs.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Any


class DiscoveryError(Exception):
    """Raised when AI field discovery fails after attempting the provider."""


class DiscoveryDisabledError(DiscoveryError):
    """Field discovery is turned off via AI_FIELD_DISCOVERY_ENABLED."""


class DiscoveryUnavailableError(DiscoveryError):
    """Field discovery is enabled but the AI provider client is not configured."""


def _env_bool(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def is_field_discovery_enabled() -> bool:
    return _env_bool("AI_FIELD_DISCOVERY_ENABLED", default=True)


def field_discovery_max_chars() -> int:
    return _env_int("AI_FIELD_DISCOVERY_MAX_CHARS", default=20_000)


def field_discovery_cache_ttl_seconds() -> int:
    return _env_int("AI_FIELD_DISCOVERY_CACHE_TTL_SECONDS", default=3600)


def field_discovery_failure_ttl_seconds() -> int:
    return _env_int("AI_FIELD_DISCOVERY_FAILURE_TTL_SECONDS", default=300)


def truncate_discovery_input(raw_text: str, max_chars: int | None = None) -> str:
    limit = field_discovery_max_chars() if max_chars is None else max_chars
    if limit <= 0:
        return ""
    return (raw_text or "")[:limit]


def schema_cache_key(source: str | None, raw_text: str) -> str:
    provider = (source or "").strip() or "_unknown"
    content_hash = hashlib.sha256((raw_text or "").encode()).hexdigest()
    return f"{provider}:{content_hash}"


@dataclass
class _CacheEntry:
    timestamp: float
    success: bool
    fields: list[dict[str, Any]] | None = None
    error_message: str | None = None


class FieldSchemaCache:
    """Provider + content-hash cache for discovered field schemas."""

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}

    def _ttl_for(self, entry: _CacheEntry) -> int:
        if entry.success:
            return field_discovery_cache_ttl_seconds()
        return field_discovery_failure_ttl_seconds()

    def get_fields(self, key: str) -> list[dict[str, Any]] | None:
        """Return cached fields, raise DiscoveryError on a fresh failure hit, or miss."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        age = time.time() - entry.timestamp
        if age >= self._ttl_for(entry):
            del self._entries[key]
            return None
        if entry.success:
            return list(entry.fields or [])
        raise DiscoveryError(entry.error_message or "Field discovery recently failed")

    def record_success(self, key: str, fields: list[dict[str, Any]]) -> None:
        self._entries[key] = _CacheEntry(time.time(), True, fields=list(fields))

    def record_failure(self, key: str, error: DiscoveryError) -> None:
        self._entries[key] = _CacheEntry(time.time(), False, error_message=str(error))

    def clear(self) -> None:
        self._entries.clear()

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.time()
        rows = []
        for key, entry in self._entries.items():
            age = now - entry.timestamp
            ttl = self._ttl_for(entry)
            rows.append({"key": key, "source": key.split(":", 1)[0] if ":" in key else key,
                "success": entry.success, "field_count": len(entry.fields or []),
                "error_message": entry.error_message, "age_seconds": round(age, 1),
                "ttl_seconds": ttl, "expires_in_seconds": round(max(0.0, ttl - age), 1),
                "fields_preview": (entry.fields or [])[:5]})
        return sorted(rows, key=lambda r: r["age_seconds"])

_ai_call_log: list[dict[str, Any]] = []
_ai_call_log_lock = threading.Lock()

def record_ai_discovery_call(*, source, provider, model, cache_hit, field_count, latency_ms=None, error=None):
    with _ai_call_log_lock:
        _ai_call_log.append({"timestamp": time.time(), "source": source or "", "provider": provider,
            "model": model, "cache_hit": cache_hit, "field_count": field_count,
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None, "error": error})
        if len(_ai_call_log) > 200:
            del _ai_call_log[: len(_ai_call_log) - 200]

def get_ai_discovery_log(limit: int = 100) -> list[dict[str, Any]]:
    with _ai_call_log_lock:
        return list(_ai_call_log[-max(1, limit):])

def clear_ai_discovery_log() -> None:
    with _ai_call_log_lock:
        _ai_call_log.clear()

# Shared in-process cache (cleared in tests via clear_field_schema_cache()).
_field_schema_cache = FieldSchemaCache()


def get_field_schema_cache() -> FieldSchemaCache:
    return _field_schema_cache


def clear_field_schema_cache() -> None:
    _field_schema_cache.clear()


def assert_field_discovery_available(client: Any | None = None) -> None:
    """Raise a DiscoveryError subclass when discovery cannot run for a user request."""
    if not is_field_discovery_enabled():
        raise DiscoveryDisabledError(
            "AI field discovery is disabled — set AI_FIELD_DISCOVERY_ENABLED=true to re-enable"
        )
    # client is ignored — availability follows AI_PROVIDER configuration.
    from mighty.ai_provider import get_configured_field_discovery_provider

    get_configured_field_discovery_provider()
