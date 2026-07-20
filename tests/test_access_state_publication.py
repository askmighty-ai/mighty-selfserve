"""Tests for AccessState serialization and nonblocking Railway publication."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from mighty.access_state_publication import (
    SCHEMA_VERSION,
    AccessStatePublisher,
    assert_no_sensitive_fields,
    build_publisher_from_env,
    ensure_runtime_instance_id,
    material_fingerprint,
    serialize_access_state,
)
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_HEALTHY,
    ACCESS_HEALTH_RECOVERING,
    BROWSER_STATUS_HEALTHY,
    RECOVERY_STATUS_IDLE,
    RECOVERY_STATUS_RECOVERING,
    RUNTIME_STATUS_RUNNING,
    AccessState,
    AccessSupervisor,
)


def _healthy_state(**overrides: Any) -> AccessState:
    base = AccessState(
        provider="amex",
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
        authentication_state="SIGNED_IN",
        access_health=ACCESS_HEALTH_HEALTHY,
        runtime_started_at="2026-07-20T09:55:00+00:00",
        authenticated_session_started_at="2026-07-20T10:00:00+00:00",
        autonomous_since_at="2026-07-20T10:00:00+00:00",
        authentication_state_changed_at="2026-07-20T10:00:00+00:00",
        last_verification_at="2026-07-20T11:00:00+00:00",
        last_keepalive_at="2026-07-20T11:05:00+00:00",
        recovery_attempt_count=1,
        recovery_success_count=1,
        recovery_failure_count=0,
        last_recovery_action="confirm_verify",
        last_recovery_result="succeeded",
        ready_for_extraction=True,
        ready_for_connector=True,
        initial_authentication_prompt_count=1,
        user_interruption_count=2,
        updated_at="2026-07-20T11:06:00+00:00",
    )
    return replace(base, **overrides) if overrides else base


def test_serialize_access_state_includes_required_fields():
    payload = serialize_access_state(
        _healthy_state(),
        runtime_instance_id="inst-1",
    )
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["provider"] == "amex"
    assert payload["authentication_state"] == "SIGNED_IN"
    assert payload["access_health"] == ACCESS_HEALTH_HEALTHY
    assert payload["runtime_state"] == RUNTIME_STATUS_RUNNING
    assert payload["browser_state"] == BROWSER_STATUS_HEALTHY
    assert payload["recovery_state"] == RECOVERY_STATUS_IDLE
    assert payload["recovery_attempts"] == 1
    assert payload["recovery_successes"] == 1
    assert payload["recovery_failures"] == 0
    assert payload["last_recovery_action"] == "confirm_verify"
    assert payload["last_recovery_result"] == "succeeded"
    assert payload["last_verified_at"] == "2026-07-20T11:00:00+00:00"
    assert payload["last_keepalive_at"] == "2026-07-20T11:05:00+00:00"
    assert "last_verification_result" in payload
    assert "last_keepalive_result" in payload
    assert payload["ready_for_extraction"] is True
    assert payload["ready_for_connector"] is True
    assert payload["initial_authentication_prompt_count"] == 1
    assert payload["mid_run_user_intervention_count"] == 1
    assert payload["runtime_started_at"] == "2026-07-20T09:55:00+00:00"
    assert payload["authenticated_session_started_at"] == "2026-07-20T10:00:00+00:00"
    assert payload["autonomous_since_at"] == "2026-07-20T10:00:00+00:00"
    assert payload["authentication_state_changed_at"] == "2026-07-20T10:00:00+00:00"
    assert "session_started_at" not in payload
    assert payload["runtime_instance_id"] == "inst-1"
    assert payload["updated_at"]
    assert "cookie" not in payload
    assert "api_key" not in payload


def test_serialize_rejects_sensitive_fields():
    with pytest.raises(ValueError, match="sensitive"):
        assert_no_sensitive_fields({"cookies": "abc"})
    with pytest.raises(ValueError, match="sensitive"):
        assert_no_sensitive_fields({"nested": {"password": "x"}})
    # authentication_state must not false-positive on "token"
    assert_no_sensitive_fields({"authentication_state": "SIGNED_IN"})


def test_material_change_and_heartbeat_publication():
    posts: list[dict[str, Any]] = []

    def post_fn(url, payload, *, api_key, timeout=10.0):
        posts.append(dict(payload))
        return {"ok": True, "http_status": 200, "accepted": True}

    mono = {"t": 0.0}

    publisher = AccessStatePublisher(
        api_key="mk_test",
        base_url="https://example.test",
        runtime_instance_id="inst-1",
        heartbeat_seconds=30.0,
        post_fn=post_fn,
        monotonic_fn=lambda: mono["t"],
        now_fn=lambda: "2026-07-20T12:00:00+00:00",
    )
    state = _healthy_state()
    publisher.notify(state)
    assert publisher.flush_once().ok
    assert len(posts) == 1
    assert posts[0]["access_health"] == ACCESS_HEALTH_HEALTHY

    # Identical material state before heartbeat → suppressed
    publisher.notify(state)
    attempt = publisher.flush_once()
    assert attempt.reason == "suppressed"
    assert len(posts) == 1

    # Heartbeat after interval
    mono["t"] = 31.0
    publisher.notify(state)
    attempt = publisher.flush_once()
    assert attempt.ok
    assert attempt.reason == "heartbeat"
    assert len(posts) == 2

    # Material change publishes immediately
    mono["t"] = 32.0
    recovering = _healthy_state(
        access_health=ACCESS_HEALTH_RECOVERING,
        recovery_planner_status=RECOVERY_STATUS_RECOVERING,
        updated_at="2026-07-20T12:01:00+00:00",
    )
    publisher.notify(recovering)
    attempt = publisher.flush_once()
    assert attempt.ok
    assert attempt.reason == "material_change"
    assert len(posts) == 3
    assert posts[-1]["access_health"] == ACCESS_HEALTH_RECOVERING


def test_publication_failure_preserves_pending_and_bounded_backoff():
    calls = {"n": 0}

    def post_fn(url, payload, *, api_key, timeout=10.0):
        calls["n"] += 1
        return {"ok": False, "http_status": 503, "error": "down"}

    mono = {"t": 100.0}
    publisher = AccessStatePublisher(
        api_key="mk_test",
        base_url="https://example.test",
        runtime_instance_id="inst-1",
        initial_backoff_seconds=2.0,
        max_backoff_seconds=8.0,
        post_fn=post_fn,
        monotonic_fn=lambda: mono["t"],
    )
    publisher.notify(_healthy_state())
    first = publisher.flush_once()
    assert first.ok is False
    assert publisher.failure_count == 1
    assert publisher._pending is not None
    assert publisher._backoff_seconds == 4.0

    # Latest-only buffer: newer state replaces pending
    publisher.notify(_healthy_state(access_health=ACCESS_HEALTH_RECOVERING))
    assert publisher._pending["access_health"] == ACCESS_HEALTH_RECOVERING

    # Still in backoff window — loop would skip; flush_once still attempts (test helper)
    mono["t"] = 101.0
    second = publisher.flush_once()
    assert second.ok is False
    assert publisher._backoff_seconds == 8.0  # capped


def test_publication_failure_does_not_change_access_health():
    def boom(state):
        raise RuntimeError("publish exploded")

    http_calls: list[str] = []

    def request_json(method, url, body=None, timeout=30.0):
        http_calls.append(f"{method} {url}")
        if url.endswith("/health"):
            return {"ok": True}
        if "/verify" in url:
            return {
                "ok": True,
                "authentication_state": "SIGNED_IN",
                "overview_ok": True,
            }
        return {"ok": True}

    supervisor = AccessSupervisor(
        provider="amex",
        request_json_fn=request_json,
        classify_browser_fn=lambda _port: {"state": "HEALTHY"},
        on_state_change=boom,
        interval_seconds=60,
        keepalive_interval_seconds=9999,
    )
    with supervisor._lock:
        supervisor.state = _healthy_state(
            access_health=ACCESS_HEALTH_HEALTHY,
            authentication_state="SIGNED_IN",
        )
    # _publish swallows callback errors
    supervisor._publish()
    assert supervisor.state.access_health == ACCESS_HEALTH_HEALTHY


def test_ensure_runtime_instance_id_stable(tmp_path: Path):
    first = ensure_runtime_instance_id(tmp_path)
    second = ensure_runtime_instance_id(tmp_path)
    assert first == second
    assert (tmp_path / "instance_id").is_file()


def test_build_publisher_from_env_requires_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MIGHTY_API_KEY", raising=False)
    assert build_publisher_from_env(root=tmp_path) is None
    monkeypatch.setenv("MIGHTY_API_KEY", "mk_abc")
    publisher = build_publisher_from_env(root=tmp_path, base_url="https://example.test")
    assert publisher is not None
    assert publisher.enabled is True
    assert publisher.runtime_instance_id


def test_publisher_worker_nonblocking_notify():
    started = threading.Event()
    release = threading.Event()
    posts: list[dict[str, Any]] = []

    def post_fn(url, payload, *, api_key, timeout=10.0):
        started.set()
        release.wait(timeout=2.0)
        posts.append(payload)
        return {"ok": True, "http_status": 200, "accepted": True}

    publisher = AccessStatePublisher(
        api_key="mk_test",
        base_url="https://example.test",
        runtime_instance_id="inst-1",
        post_fn=post_fn,
        heartbeat_seconds=60,
    )
    publisher.start()
    try:
        t0 = time.monotonic()
        publisher.notify(_healthy_state())
        # notify must return quickly even if post blocks
        assert time.monotonic() - t0 < 0.5
        assert started.wait(timeout=2.0)
        release.set()
        deadline = time.monotonic() + 2.0
        while not posts and time.monotonic() < deadline:
            time.sleep(0.05)
        assert posts
    finally:
        release.set()
        publisher.stop(join_timeout=2.0)


def test_fingerprint_ignores_updated_at_only_change():
    a = serialize_access_state(_healthy_state(updated_at="2026-07-20T11:00:00+00:00"), runtime_instance_id="i")
    b = serialize_access_state(_healthy_state(updated_at="2026-07-20T11:01:00+00:00"), runtime_instance_id="i")
    assert material_fingerprint(a) == material_fingerprint(b)
