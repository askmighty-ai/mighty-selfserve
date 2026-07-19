"""Tests for Mighty Access Control Center."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mighty.provider_runtime import parse_args
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_DEGRADED,
    ACCESS_HEALTH_HEALTHY,
    ACCESS_HEALTH_RECOVERING,
    BROWSER_STATUS_HEALTHY,
    BROWSER_STATUS_UNHEALTHY,
    EVENT_BROWSER_RESTART,
    EVENT_KEEPALIVE_SUCCESS,
    EVENT_VERIFICATION_FAILURE,
    EVENT_VERIFICATION_SUCCESS,
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_IDLE,
    RUNTIME_STATUS_RUNNING,
    SCHEDULER_STATUS_RUNNING,
    SCHEDULER_STATUS_STOPPED,
    AccessState,
    AccessSupervisor,
    EventHistory,
    apply_keepalive_to_access_state,
    apply_verification_to_access_state,
    dispatch_keyboard_command,
    render_control_center,
    run_control_center,
)


def test_access_state_updates_from_verification():
    state = AccessState(provider="amex", runtime_status=RUNTIME_STATUS_RUNNING)
    updated = apply_verification_to_access_state(
        state,
        authentication_state="SIGNED_IN",
        observed_at="2026-07-19T12:00:00+00:00",
        overview_ok=True,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
    )
    assert updated.authentication_state == "SIGNED_IN"
    assert updated.access_health == ACCESS_HEALTH_HEALTHY
    assert updated.ready_for_extraction is True
    assert updated.ready_for_connector is True
    assert updated.session_started_at == "2026-07-19T12:00:00+00:00"
    assert updated.last_verification_result == "SIGNED_IN"


def test_access_state_clears_session_on_sign_out():
    state = AccessState(
        provider="amex",
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        authentication_state="SIGNED_IN",
        session_started_at="2026-07-19T10:00:00+00:00",
        overview_ok=True,
    )
    updated = apply_verification_to_access_state(
        state,
        authentication_state="SIGNED_OUT",
        observed_at="2026-07-19T12:00:00+00:00",
        overview_ok=False,
    )
    assert updated.authentication_state == "SIGNED_OUT"
    assert updated.session_started_at is None
    assert updated.ready_for_extraction is False
    assert updated.access_health == ACCESS_HEALTH_DEGRADED


def test_access_state_keepalive_update():
    state = AccessState(provider="amex", current_strategy="SESSION_API")
    updated = apply_keepalive_to_access_state(
        state,
        ok=True,
        strategy="SESSION_API",
        observed_at="2026-07-19T12:05:00+00:00",
        result="ok",
    )
    assert updated.last_keepalive_at == "2026-07-19T12:05:00+00:00"
    assert updated.last_keepalive_result == "ok"
    assert updated.current_strategy == "SESSION_API"


def test_event_history_rolls_at_limit():
    history = EventHistory(limit=3)
    history.append("a", "one")
    history.append("b", "two")
    history.append("c", "three")
    history.append("d", "four")
    events = history.list_events()
    assert len(events) == 3
    assert [e.event_type for e in events] == ["b", "c", "d"]
    assert history.list_events(limit=1)[0].event_type == "d"


def test_render_control_center_includes_required_fields():
    now = datetime(2026, 7, 19, 15, 0, tzinfo=timezone.utc)
    history = EventHistory()
    history.append(
        EVENT_VERIFICATION_SUCCESS,
        "SIGNED_IN",
        observed_at="2026-07-19T14:59:00+00:00",
    )
    state = AccessState(
        provider="amex",
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
        scheduler_status=SCHEDULER_STATUS_RUNNING,
        authentication_state="SIGNED_IN",
        access_health=ACCESS_HEALTH_HEALTHY,
        session_started_at="2026-07-19T13:00:00+00:00",
        last_verification_at="2026-07-19T14:59:00+00:00",
        last_verification_result="SIGNED_IN",
        last_keepalive_at="2026-07-19T14:55:00+00:00",
        last_keepalive_result="ok",
        current_strategy="SESSION_API",
        recovery_count=1,
        user_interruption_count=1,
        ready_for_extraction=True,
        ready_for_connector=True,
    ).snapshot(history)
    text = render_control_center(state, now=now)
    assert "Mighty Access Control Center" in text
    assert "Runtime ............ running" in text
    assert "Browser ............ healthy" in text
    assert "Recovery Planner ... idle" in text
    assert "Scheduler .......... running" in text
    assert "Authentication ..... SIGNED_IN" in text
    assert "Access health ...... healthy" in text
    assert "Session age ........" in text
    assert "Last verification .." in text
    assert "Last keepalive ....." in text
    assert "Current strategy ... SESSION_API" in text
    assert "Recoveries ......... 1" in text
    assert "User interruptions . 1" in text
    assert "Ready for extraction yes" in text
    assert "Ready for connector  yes" in text
    assert "verification_success" in text
    assert "[v]erify" in text
    assert "[q]uit" in text


def test_keyboard_command_dispatch():
    assert dispatch_keyboard_command("v") == "verify"
    assert dispatch_keyboard_command("K") == "keepalive"
    assert dispatch_keyboard_command("r") == "connector_refresh"
    assert dispatch_keyboard_command("l") == "login_recover"
    assert dispatch_keyboard_command("q") == "quit"
    assert dispatch_keyboard_command("x") is None
    assert dispatch_keyboard_command("") is None
    assert dispatch_keyboard_command("  v  ") == "verify"


def _supervisor_http_factory(responses: dict[str, Any]):
    def _http(method: str, url: str, body: dict[str, Any] | None = None, *, timeout: float = 30.0):
        path = url.split("://", 1)[-1]
        path = "/" + path.split("/", 1)[-1] if "/" in path else path
        # Normalize to path only.
        from urllib.parse import urlsplit

        key = urlsplit(url).path
        if key not in responses:
            raise AssertionError(f"Unexpected HTTP {method} {key}")
        value = responses[key]
        if callable(value):
            return value(method, body)
        return dict(value)

    return _http


def test_supervisor_loop_updates_state_and_events():
    responses = {
        "/health": {"ok": True},
        "/providers/amex/verify": {
            "ok": True,
            "result": {
                "authentication_state": "SIGNED_IN",
                "observed_at": "2026-07-19T15:00:00+00:00",
            },
        },
        "/providers/amex/surface/ensure": {"ok": True, "surface": "overview"},
        "/providers/amex/keepalive/probe": {
            "ok": True,
            "success": True,
            "reason": "ok",
            "strategy": "SESSION_API",
        },
    }
    supervisor = AccessSupervisor(
        provider="amex",
        interval_seconds=60,
        keepalive_interval_seconds=0,  # always due
        keepalive_strategy="SESSION_API",
        request_json_fn=_supervisor_http_factory(responses),
        classify_browser_fn=lambda _port: {"state": "HEALTHY"},
        state=AccessState(
            provider="amex",
            runtime_status=RUNTIME_STATUS_RUNNING,
            browser_status=BROWSER_STATUS_HEALTHY,
        ),
    )
    state = supervisor.run_once(force_keepalive=True)
    assert state.authentication_state == "SIGNED_IN"
    assert state.access_health == ACCESS_HEALTH_HEALTHY
    assert state.overview_ok is True
    assert state.ready_for_extraction is True
    assert state.last_keepalive_result == "ok"
    types = [e.event_type for e in supervisor.history.list_events()]
    assert EVENT_VERIFICATION_SUCCESS in types
    assert EVENT_KEEPALIVE_SUCCESS in types


def test_supervisor_marks_awaiting_user_when_signed_out():
    responses = {
        "/health": {"ok": True},
        "/providers/amex/verify": {
            "ok": True,
            "result": {"authentication_state": "SIGNED_OUT"},
        },
        "/providers/amex/surface/ensure": {"ok": False},
    }
    supervisor = AccessSupervisor(
        provider="amex",
        keepalive_interval_seconds=9999,
        request_json_fn=_supervisor_http_factory(responses),
        classify_browser_fn=lambda _port: {"state": "HEALTHY"},
    )
    state = supervisor.run_once()
    assert state.authentication_state == "SIGNED_OUT"
    assert state.recovery_planner_status == RECOVERY_STATUS_AWAITING_USER
    assert state.access_health == ACCESS_HEALTH_RECOVERING
    assert EVENT_VERIFICATION_FAILURE in [e.event_type for e in supervisor.history.list_events()]


def test_supervisor_repairs_unhealthy_browser():
    restarts: list[str] = []

    def _restart():
        restarts.append("once")
        return {"ok": True}

    classify_calls = {"n": 0}

    def _classify(_port: int):
        classify_calls["n"] += 1
        # First classification in the tick reports unhealthy; after restart, healthy.
        if classify_calls["n"] == 1:
            return {"state": "UNHEALTHY"}
        return {"state": "HEALTHY"}

    responses = {
        "/health": {"ok": True},
        "/providers/amex/verify": {
            "ok": True,
            "result": {"authentication_state": "SIGNED_IN"},
        },
        "/providers/amex/surface/ensure": {"ok": True},
    }
    supervisor = AccessSupervisor(
        provider="amex",
        keepalive_interval_seconds=9999,
        request_json_fn=_supervisor_http_factory(responses),
        classify_browser_fn=_classify,
        restart_browser_fn=_restart,
        state=AccessState(
            provider="amex",
            browser_status=BROWSER_STATUS_UNHEALTHY,
            recovery_planner_status=RECOVERY_STATUS_IDLE,
        ),
    )
    state = supervisor.run_once()
    assert restarts == ["once"]
    assert state.browser_status == BROWSER_STATUS_HEALTHY
    assert state.recovery_count == 1
    types = [e.event_type for e in supervisor.history.list_events()]
    assert EVENT_BROWSER_RESTART in types


def test_supervisor_start_stop_graceful():
    responses = {
        "/health": {"ok": True},
        "/providers/amex/verify": {
            "ok": True,
            "result": {"authentication_state": "SIGNED_IN"},
        },
        "/providers/amex/surface/ensure": {"ok": True},
    }
    supervisor = AccessSupervisor(
        provider="amex",
        interval_seconds=30,
        keepalive_interval_seconds=9999,
        request_json_fn=_supervisor_http_factory(responses),
        classify_browser_fn=lambda _port: {"state": "HEALTHY"},
    )
    supervisor.start()
    assert supervisor.get_state().scheduler_status == SCHEDULER_STATUS_RUNNING
    supervisor.stop(join_timeout=2.0)
    assert supervisor.get_state().scheduler_status == SCHEDULER_STATUS_STOPPED


def test_control_center_cli_parses():
    import sys

    old = sys.argv
    try:
        sys.argv = [
            "provider_runtime.py",
            "control-center",
            "amex",
            "--interval-seconds",
            "30",
            "--strategy",
            "SESSION_API",
        ]
        parsed = parse_args()
    finally:
        sys.argv = old
    assert parsed.command == "control-center"
    assert parsed.provider == "amex"
    assert parsed.interval_seconds == 30.0
    assert parsed.strategy == "SESSION_API"


def test_run_control_center_ownership_and_shutdown(tmp_path: Path):
    stops: list[str] = []
    screens: list[str] = []

    def ensure_runtime(**_kwargs):
        return {
            "ok": True,
            "runtime_preexisting": False,
            "runtime_started_by_campaign": True,
            "process": object(),
            "base_url": "http://127.0.0.1:8765",
        }

    def stop_runtime(**_kwargs):
        stops.append("runtime")
        return {"ok": True}

    def prepare_session(**_kwargs):
        return {
            "ok": True,
            "interrupted": False,
            "managed_browser_preexisting": False,
            "managed_browser_launched": True,
            "managed_browser_restarted": False,
            "final_authentication_state": "SIGNED_IN",
            "authentication_attempt_count": 1,
        }

    http_calls: list[str] = []

    def http(method: str, url: str, body=None, *, timeout: float = 30.0):
        from urllib.parse import urlsplit

        path = urlsplit(url).path
        http_calls.append(f"{method} {path}")
        if path == "/health":
            return {"ok": True}
        if path.endswith("/verify"):
            return {
                "ok": True,
                "result": {"authentication_state": "SIGNED_IN", "observed_at": "2026-07-19T15:00:00+00:00"},
            }
        if path.endswith("/surface/ensure"):
            return {"ok": True}
        if path.endswith("/keepalive/probe"):
            return {"ok": True, "success": True, "reason": "ok"}
        return {"ok": True}

    class FakeKeyboard:
        def __init__(self) -> None:
            self.keys = ["q"]
            self.enabled = False

        def enable(self) -> None:
            self.enabled = True

        def disable(self) -> None:
            self.enabled = False

        def poll_key(self, timeout: float = 0.25) -> str | None:
            if self.keys:
                return self.keys.pop(0)
            return None

    payload = run_control_center(
        provider="amex",
        root=tmp_path,
        interval_seconds=60,
        keepalive_interval_seconds=9999,
        request_json_fn=http,
        ensure_runtime_fn=ensure_runtime,
        stop_runtime_fn=stop_runtime,
        prepare_session_fn=prepare_session,
        classify_browser_fn=lambda _port: {"state": "HEALTHY"},
        close_managed_browser_fn=lambda **_k: {"closed": False},
        keyboard=FakeKeyboard(),
        redraw_fn=lambda text: screens.append(text),
        print_fn=lambda *_a, **_k: None,
        sleep_fn=lambda _s: None,
        max_loops=5,
    )
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert payload["runtime_started_by_command"] is True
    assert payload["runtime_stopped_by_command"] is True
    assert stops == ["runtime"]
    assert payload["managed_browser_launched_by_command"] is True
    assert payload["browser_cleanup_policy"] == "leave-open"
    assert screens, "console should redraw at least once"
    assert "Mighty Access Control Center" in screens[0]
    assert payload["access_state"]["authentication_state"] == "SIGNED_IN"
    assert payload["event_count"] >= 2


def test_run_control_center_fails_when_runtime_unavailable(tmp_path: Path):
    payload = run_control_center(
        provider="amex",
        root=tmp_path,
        ensure_runtime_fn=lambda **_k: {
            "ok": False,
            "outcome": "runtime_start_failed",
            "error": "boom",
            "runtime_preexisting": False,
            "runtime_started_by_campaign": False,
        },
        print_fn=lambda *_a, **_k: None,
    )
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["outcome"] == "runtime_start_failed"


def test_session_age_formatting_in_state():
    started = datetime.now(timezone.utc) - timedelta(hours=2, minutes=15)
    state = AccessState(
        provider="amex",
        session_started_at=started.isoformat(),
    )
    age = state.session_age_seconds()
    assert age is not None
    assert age >= 2 * 3600
    text = render_control_center(state)
    assert "2h" in text
