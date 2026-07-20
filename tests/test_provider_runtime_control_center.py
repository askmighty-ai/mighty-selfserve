"""Tests for Mighty Access Control Center."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from mighty.provider_runtime import (
    MANAGED_BROWSER_HEALTHY,
    MANAGED_BROWSER_UNHEALTHY,
    ensure_managed_amex_browser_for_campaign,
    format_cdp_port_conflict_error,
    parse_args,
    restart_managed_amex_browser,
    wait_for_cdp_port_clear,
)
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
    format_control_center_startup_failure,
    render_control_center,
    run_control_center,
)


def _ready_browser(**_kwargs):
    return {
        "ok": True,
        "state": MANAGED_BROWSER_HEALTHY,
        "cdp_url": "http://127.0.0.1:9223",
        "managed_browser_preexisting": False,
        "managed_browser_launched_by_campaign": True,
        "managed_browser_restarted_by_campaign": False,
    }


class _QuitKeyboard:
    def __init__(self, keys: list[str] | None = None) -> None:
        self.keys = list(keys or ["q"])
        self.enabled = False

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def poll_key(self, timeout: float = 0.25) -> str | None:
        if self.keys:
            return self.keys.pop(0)
        return None


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
    stage_order: list[str] = []

    def ensure_browser(**_kwargs):
        stage_order.append("browser")
        return _ready_browser()

    def ensure_runtime(**_kwargs):
        stage_order.append("runtime")
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
        stage_order.append("auth")
        return {
            "ok": True,
            "interrupted": False,
            "managed_browser_preexisting": False,
            "managed_browser_launched": True,
            "managed_browser_restarted": False,
            "final_authentication_state": "SIGNED_IN",
            "authentication_attempt_count": 1,
        }

    def http(method: str, url: str, body=None, *, timeout: float = 30.0):
        from urllib.parse import urlsplit

        path = urlsplit(url).path
        if path == "/health":
            return {"ok": True}
        if path.endswith("/verify"):
            return {
                "ok": True,
                "result": {
                    "authentication_state": "SIGNED_IN",
                    "observed_at": "2026-07-19T15:00:00+00:00",
                },
            }
        if path.endswith("/surface/ensure"):
            return {"ok": True}
        if path.endswith("/keepalive/probe"):
            return {"ok": True, "success": True, "reason": "ok"}
        return {"ok": True}

    payload = run_control_center(
        provider="amex",
        root=tmp_path,
        interval_seconds=60,
        keepalive_interval_seconds=9999,
        request_json_fn=http,
        ensure_managed_browser_fn=ensure_browser,
        ensure_runtime_fn=ensure_runtime,
        stop_runtime_fn=stop_runtime,
        prepare_session_fn=prepare_session,
        classify_browser_fn=lambda _port: {"state": "HEALTHY"},
        close_managed_browser_fn=lambda **_k: {"closed": False},
        keyboard=_QuitKeyboard(["q"]),
        redraw_fn=lambda text: screens.append(text),
        print_fn=lambda *_a, **_k: None,
        sleep_fn=lambda _s: None,
        max_loops=5,
    )
    assert stage_order[:3] == ["browser", "runtime", "auth"]
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
    assert payload["outcome"] == "quit"


def test_run_control_center_fails_when_runtime_unavailable(tmp_path: Path):
    printed: list[str] = []
    payload = run_control_center(
        provider="amex",
        root=tmp_path,
        ensure_managed_browser_fn=_ready_browser,
        ensure_runtime_fn=lambda **_k: {
            "ok": False,
            "outcome": "runtime_start_failed",
            "error": "boom",
            "runtime_preexisting": False,
            "runtime_started_by_campaign": False,
        },
        print_fn=lambda *args, **_k: printed.append(" ".join(str(a) for a in args)),
    )
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["outcome"] == "runtime_start_failed"
    assert payload["stage"] == "runtime"
    assert any("failed to start" in line.lower() for line in printed)
    assert any("Stage: runtime" in line for line in printed)
    assert payload.get("diagnostic_path")


def test_unhealthy_browser_restarts_then_becomes_ready(tmp_path: Path):
    profile = tmp_path / "amex"
    restarted = {"n": 0}

    def restart():
        restarted["n"] += 1
        return {"ok": True, "cdp_url": "http://127.0.0.1:9223", "restarted": True}

    def owned(cdp_port: int, profile_dir: Path):
        return {
            "status": "owned",
            "cdp_port": int(cdp_port),
            "profile_dir": str(Path(profile_dir).resolve()),
            "processes": [{"pid": 7, "user_data_dir": str(Path(profile_dir).resolve())}],
            "owned_processes": [
                {"pid": 7, "user_data_dir": str(Path(profile_dir).resolve())}
            ],
            "foreign_processes": [],
        }

    with patch("mighty.provider_runtime.inspect_cdp_port_ownership", owned):
        result = ensure_managed_amex_browser_for_campaign(
            profile_dir=profile,
            cdp_port=9223,
            classify_fn=lambda: {
                "state": MANAGED_BROWSER_UNHEALTHY,
                "cdp_url": "http://127.0.0.1:9223",
                "page_target_count": 0,
                "error": "zero_page_targets",
            },
            launch_fn=lambda: (_ for _ in ()).throw(AssertionError("launch should not run")),
            restart_fn=restart,
            print_fn=lambda *_a, **_k: None,
        )
    assert restarted["n"] == 1
    assert result["managed_browser_restarted_by_campaign"] is True
    assert result["ok"] is True


def test_foreign_cdp_port_is_rejected_before_restart(tmp_path: Path):
    profile = tmp_path / "amex"
    foreign = tmp_path / "other-profile"

    def fake_ownership(cdp_port: int, profile_dir: Path):
        return {
            "status": "foreign",
            "cdp_port": int(cdp_port),
            "profile_dir": str(Path(profile_dir).resolve()),
            "processes": [{"pid": 99, "user_data_dir": str(foreign), "command": "Chrome"}],
            "owned_processes": [],
            "foreign_processes": [
                {"pid": 99, "user_data_dir": str(foreign), "command": "Chrome"}
            ],
        }

    with patch("mighty.provider_runtime.inspect_cdp_port_ownership", fake_ownership):
        with pytest.raises(RuntimeError, match="held by another Chrome process"):
            ensure_managed_amex_browser_for_campaign(
                profile_dir=profile,
                cdp_port=9223,
                classify_fn=lambda: {
                    "state": MANAGED_BROWSER_UNHEALTHY,
                    "cdp_url": "http://127.0.0.1:9223",
                    "page_target_count": 0,
                },
                restart_fn=lambda: (_ for _ in ()).throw(AssertionError("must not restart")),
                print_fn=lambda *_a, **_k: None,
            )


def test_control_center_reports_browser_startup_failure(tmp_path: Path):
    printed: list[str] = []

    def boom(**_kwargs):
        raise RuntimeError(
            "Managed Amex Chrome did not become ready within 30s "
            "(last_state=UNHEALTHY, error=zero_page_targets)"
        )

    payload = run_control_center(
        provider="amex",
        root=tmp_path,
        ensure_managed_browser_fn=boom,
        ensure_runtime_fn=lambda **_k: (_ for _ in ()).throw(
            AssertionError("runtime must not start after browser failure")
        ),
        print_fn=lambda *args, **_k: printed.append(" ".join(str(a) for a in args)),
    )
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["stage"] == "managed_browser"
    assert payload["outcome"] == "browser_start_failed"
    assert "zero_page_targets" in payload["error"]
    report = "\n".join(printed)
    assert "Mighty Access Control Center failed to start." in report
    assert "Stage: managed_browser" in report
    assert payload.get("diagnostic_path")
    assert Path(payload["diagnostic_path"]).is_file()


def test_control_center_reports_restart_timeout(tmp_path: Path):
    printed: list[str] = []

    def ensure_browser(**_kwargs):
        raise RuntimeError(
            "Managed Amex Chrome did not become ready within 30s "
            "(last_state=UNHEALTHY, error=zero_page_targets)"
        )

    payload = run_control_center(
        provider="amex",
        root=tmp_path,
        ensure_managed_browser_fn=ensure_browser,
        print_fn=lambda *args, **_k: printed.append(" ".join(str(a) for a in args)),
    )
    assert payload["stage"] == "managed_browser"
    assert payload["exit_code"] == 1
    assert "did not become ready" in payload["error"]
    assert any("Diagnostic:" in line for line in printed)


def test_restart_managed_browser_waits_for_port_clear(tmp_path: Path):
    clears: list[int] = []
    launched = {"n": 0}

    def owned(cdp_port: int, profile_dir: Path):
        return {
            "status": "owned",
            "cdp_port": int(cdp_port),
            "profile_dir": str(Path(profile_dir).resolve()),
            "processes": [],
            "owned_processes": [],
            "foreign_processes": [],
        }

    def clear(cdp_port, **_kwargs):
        clears.append(int(cdp_port))
        return True

    def launch(**_kwargs):
        launched["n"] += 1
        return {"ok": True, "cdp_url": "http://127.0.0.1:9223", "page_target_count": 1}

    with patch("mighty.provider_runtime.inspect_cdp_port_ownership", owned), patch(
        "mighty.provider_runtime.launch_managed_amex_browser",
        launch,
    ):
        result = restart_managed_amex_browser(
            profile_dir=tmp_path / "amex",
            cdp_port=9223,
            terminate_profile_processes_fn=lambda *_a, **_k: None,
            wait_for_profile_release_fn=lambda *_a, **_k: True,
            wait_for_cdp_port_clear_fn=clear,
        )
    assert clears == [9223]
    assert launched["n"] == 1
    assert result["restarted"] is True


def test_wait_for_cdp_port_clear_times_out():
    ok = wait_for_cdp_port_clear(
        9223,
        timeout_seconds=0.2,
        sleep_fn=lambda _s: None,
        monotonic_fn=iter([0.0, 0.1, 0.3]).__next__,
        list_processes_fn=lambda _port: [{"pid": 1}],
        cdp_available_fn=lambda *_a, **_k: "http://127.0.0.1:9223",
    )
    assert ok is False


def test_format_startup_failure_includes_stage_and_cleanup():
    text = format_control_center_startup_failure(
        stage="managed_browser",
        error="boom",
        ownership={
            "runtime_started_by_command": True,
            "runtime_stopped_by_command": True,
            "managed_browser_launched_by_command": False,
            "managed_browser_closed_at_completion": False,
            "browser_cleanup_policy": "leave-open",
        },
        diagnostic_path="/tmp/diag.json",
    )
    assert "Stage: managed_browser" in text
    assert "Error: boom" in text
    assert "runtime_stopped=True" in text
    assert "Diagnostic: /tmp/diag.json" in text


def test_console_stays_running_until_quit(tmp_path: Path):
    screens: list[str] = []
    keys = _QuitKeyboard(["v", "q"])

    payload = run_control_center(
        provider="amex",
        root=tmp_path,
        ensure_managed_browser_fn=_ready_browser,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        },
        prepare_session_fn=lambda **_k: {
            "ok": True,
            "final_authentication_state": "SIGNED_IN",
            "authentication_attempt_count": 0,
            "interrupted": False,
        },
        request_json_fn=_supervisor_http_factory(
            {
                "/health": {"ok": True},
                "/providers/amex/verify": {
                    "ok": True,
                    "result": {"authentication_state": "SIGNED_IN"},
                },
                "/providers/amex/surface/ensure": {"ok": True},
                "/providers/amex/keepalive/probe": {
                    "ok": True,
                    "success": True,
                    "reason": "ok",
                },
            }
        ),
        classify_browser_fn=lambda _port: {"state": "HEALTHY"},
        close_managed_browser_fn=lambda **_k: {"closed": False},
        keyboard=keys,
        redraw_fn=lambda text: screens.append(text),
        print_fn=lambda *_a, **_k: None,
        sleep_fn=lambda _s: None,
        max_loops=20,
    )
    assert payload["outcome"] == "quit"
    assert payload["ok"] is True
    assert len(screens) >= 1
    assert keys.enabled is False


def test_control_center_ctrl_c_during_console(tmp_path: Path):
    class InterruptKeyboard(_QuitKeyboard):
        def poll_key(self, timeout: float = 0.25) -> str | None:
            raise KeyboardInterrupt

    payload = run_control_center(
        provider="amex",
        root=tmp_path,
        ensure_managed_browser_fn=_ready_browser,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": False,
            "runtime_started_by_campaign": True,
            "process": object(),
        },
        stop_runtime_fn=lambda **_k: {"ok": True},
        prepare_session_fn=lambda **_k: {
            "ok": True,
            "final_authentication_state": "SIGNED_IN",
            "interrupted": False,
        },
        request_json_fn=_supervisor_http_factory(
            {
                "/health": {"ok": True},
                "/providers/amex/verify": {
                    "ok": True,
                    "result": {"authentication_state": "SIGNED_IN"},
                },
                "/providers/amex/surface/ensure": {"ok": True},
            }
        ),
        classify_browser_fn=lambda _port: {"state": "HEALTHY"},
        close_managed_browser_fn=lambda **_k: {"closed": False},
        keyboard=InterruptKeyboard(),
        redraw_fn=lambda _text: None,
        print_fn=lambda *_a, **_k: None,
        sleep_fn=lambda _s: None,
    )
    assert payload["exit_code"] == 130
    assert payload["interrupted"] is True
    assert payload["runtime_stopped_by_command"] is True


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


def test_format_cdp_port_conflict_error_message():
    text = format_cdp_port_conflict_error(
        {
            "cdp_port": 9223,
            "profile_dir": "/tmp/amex",
            "foreign_processes": [
                {"pid": 42, "user_data_dir": "/tmp/pytest/amex"},
            ],
        }
    )
    assert "9223" in text
    assert "pid=42" in text
    assert "--cdp-port" in text
