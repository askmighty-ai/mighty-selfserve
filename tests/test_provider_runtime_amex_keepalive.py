"""Tests for developer-only Amex keepalive trials in Provider Runtime."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mighty.provider_runtime import (
    ProviderRuntime,
    KeepaliveActionResult,
    VerificationResult,
    inspect_amex_page_signals,
    perform_keepalive_action,
    sanitize_keepalive_event,
)


def _runtime(tmp_path: Path) -> ProviderRuntime:
    runtime = ProviderRuntime(
        root=tmp_path,
        cdp_port=9333,
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
        keepalive_result_path=tmp_path / "keepalive.json",
    )
    runtime.cdp_url = "http://127.0.0.1:9333"
    return runtime


def _signed_in_result() -> VerificationResult:
    return VerificationResult(
        provider="amex",
        authentication_state="SIGNED_IN",
        reason="ok",
        observed_at="2026-01-01T00:00:00+00:00",
        final_url="https://global.americanexpress.com/overview",
        page_title="Overview",
        login_url_detected=False,
        login_marker_count=0,
        authenticated_marker_count=2,
        session_api_200_count=1,
        session_api_denied_count=0,
    )


def _signed_out_result() -> VerificationResult:
    return VerificationResult(
        provider="amex",
        authentication_state="SIGNED_OUT",
        reason="login",
        observed_at="2026-01-01T00:00:00+00:00",
        final_url="https://www.americanexpress.com/en-us/account/login",
        page_title="Login",
        login_url_detected=True,
        login_marker_count=2,
        authenticated_marker_count=0,
        session_api_200_count=0,
        session_api_denied_count=0,
    )


def _playwright_page_ent(page: MagicMock):
    browser = MagicMock()
    browser.contexts = [MagicMock()]
    cm = MagicMock()
    cm.__enter__.return_value = MagicMock(
        chromium=MagicMock(connect_over_cdp=MagicMock(return_value=browser))
    )
    cm.__exit__.return_value = None
    return cm


def test_rejects_trial_when_not_signed_in(tmp_path: Path):
    runtime = _runtime(tmp_path)
    with patch(
        "mighty.provider_runtime.verify_amex_over_cdp",
        return_value=_signed_out_result(),
    ):
        payload = runtime.start_keepalive_trial(
            "amex",
            strategy="NONE",
            duration_seconds=8,
            interval_seconds=2,
        )
    assert payload["ok"] is False
    assert payload["error"] == "not_signed_in"
    assert runtime.keepalive_trial_running is False


def test_prevents_concurrent_trials(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.keepalive_trial_running = True
    runtime.keepalive_trial_id = "existing"
    with patch(
        "mighty.provider_runtime.verify_amex_over_cdp",
        return_value=_signed_in_result(),
    ):
        payload = runtime.start_keepalive_trial(
            "amex",
            strategy="NONE",
            duration_seconds=8,
            interval_seconds=2,
        )
    assert payload["ok"] is False
    assert payload["error"] == "keepalive_trial_already_running"


def test_none_performs_no_actions(tmp_path: Path):
    page = MagicMock()
    result = perform_keepalive_action(page, "NONE")
    assert result.result == "skipped"
    assert result.ok is True
    page.evaluate.assert_not_called()
    page.goto.assert_not_called()

    runtime = _runtime(tmp_path)
    runtime.keepalive_strategy = "NONE"
    runtime.keepalive_trial_running = True
    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.inspect_amex_page_signals",
        return_value={
            "authentication_state": "SIGNED_IN",
            "inspection_authentication_state_source": "LATEST_CANONICAL",
            "expiration_dialog_detected": False,
            "login_page_detected": False,
            "final_url": "https://global.americanexpress.com/overview",
        },
    ), patch(
        "mighty.provider_runtime.perform_keepalive_action",
    ) as action:
        logged_out = runtime._keepalive_tick()

    assert logged_out is False
    assert runtime.keepalive_action_count == 0
    action.assert_not_called()


def test_each_strategy_dispatches_correct_action():
    page = MagicMock()
    page.evaluate.side_effect = [
        {"status": 200, "ok": True},
        {"ok": True},
    ]
    page.goto.return_value = None
    page.wait_for_timeout.return_value = None

    session = perform_keepalive_action(page, "SESSION_API")
    assert session.ok is True
    assert session.response_status == 200
    assert page.evaluate.call_count == 1

    activity = perform_keepalive_action(page, "PAGE_ACTIVITY")
    assert activity.ok is True
    assert page.evaluate.call_count == 2

    reload_result = perform_keepalive_action(page, "OVERVIEW_RELOAD")
    assert reload_result.ok is True
    page.goto.assert_called_once()
    assert "overview" in page.goto.call_args.args[0]


def test_action_failures_do_not_fabricate_signed_out(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.keepalive_strategy = "SESSION_API"
    runtime.keepalive_trial_running = True
    page = MagicMock()

    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.inspect_amex_page_signals",
        return_value={
            "authentication_state": "SIGNED_IN",
            "expiration_dialog_detected": False,
            "login_page_detected": False,
            "final_url": "https://global.americanexpress.com/overview",
        },
    ), patch(
        "mighty.provider_runtime.perform_keepalive_action",
        return_value=KeepaliveActionResult(
            ok=False,
            result="failure",
            error="RuntimeError: boom",
        ),
    ):
        logged_out = runtime._keepalive_tick()

    assert logged_out is False
    assert runtime.keepalive_logged_out is False
    assert runtime.keepalive_action_failure_count == 1
    assert runtime.keepalive_last_action_result == "failure"
    assert runtime.keepalive_final_authentication_state is None


def test_expiration_dialog_is_recorded(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.keepalive_strategy = "NONE"
    runtime.keepalive_trial_running = True
    page = MagicMock()

    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.inspect_amex_page_signals",
        side_effect=[
            {
                "authentication_state": "SIGNED_IN",
                "expiration_dialog_detected": True,
                "login_page_detected": False,
                "final_url": "https://global.americanexpress.com/overview",
            },
            {
                "authentication_state": "SIGNED_IN",
                "expiration_dialog_detected": True,
                "login_page_detected": False,
                "final_url": "https://global.americanexpress.com/overview",
            },
        ],
    ):
        runtime._keepalive_tick()

    assert runtime.keepalive_expiration_dialog_seen is True
    assert any(event["event_type"] == "expiration_dialog" for event in runtime.keepalive_events)


def test_login_logout_is_recorded(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.keepalive_strategy = "PAGE_ACTIVITY"
    runtime.keepalive_trial_running = True
    page = MagicMock()

    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.inspect_amex_page_signals",
        return_value={
            "authentication_state": "SIGNED_OUT",
            "expiration_dialog_detected": False,
            "login_page_detected": True,
            "final_url": "https://www.americanexpress.com/en-us/account/login",
        },
    ):
        logged_out = runtime._keepalive_tick()

    assert logged_out is True
    assert runtime.keepalive_logged_out is True
    assert any(event["event_type"] == "logged_out" for event in runtime.keepalive_events)
    assert any(event.get("login_page_detected") for event in runtime.keepalive_events)


def test_trial_stops_cleanly_and_persists_final_verification(tmp_path: Path):
    runtime = _runtime(tmp_path)

    with patch(
        "mighty.provider_runtime.verify_amex_over_cdp",
        return_value=_signed_in_result(),
    ), patch.object(runtime, "_keepalive_tick", return_value=False):
        payload = runtime.start_keepalive_trial(
            "amex",
            strategy="NONE",
            duration_seconds=30,
            interval_seconds=5,
        )
        assert payload["ok"] is True
        assert payload["trial_id"]
        stop_payload = runtime.stop_keepalive_trial(reason="manually_stopped")

    assert stop_payload["ok"] is True
    assert runtime.keepalive_trial_running is False
    assert runtime.keepalive_completed_at is not None
    assert runtime.keepalive_final_reason == "manually_stopped"
    assert runtime.keepalive_final_authentication_state == "SIGNED_IN"
    assert runtime.keepalive_result_path.exists()
    persisted = json.loads(runtime.keepalive_result_path.read_text(encoding="utf-8"))
    assert persisted["keepalive_final_authentication_state"] == "SIGNED_IN"
    assert "keepalive_events" in persisted


def test_maintenance_watcher_does_not_click_continue_during_trial(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.keepalive_trial_running = True
    page = MagicMock()
    from tests.test_provider_runtime_browser_inspector import (
        CdpSessionMock,
        _bind_cdp,
        _dialog_tree,
    )

    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)

    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.dismiss_amex_expiration_dialog",
    ) as dismiss, patch(
        "mighty.provider_runtime.click_expiration_continue",
    ) as click:
        outcome = runtime._inspect_and_extend_session(runtime.cdp_url)

    assert outcome.dialog_detected is True
    assert "Observation-only" in (outcome.reason or "")
    dismiss.assert_not_called()
    click.assert_not_called()
    assert runtime.keepalive_expiration_dialog_seen is True
    assert page.evaluate.call_count == 0


def test_no_sensitive_data_in_serialized_trial_state(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.keepalive_trial_id = "trial-1"
    runtime.keepalive_strategy = "SESSION_API"
    runtime.keepalive_events = [
        sanitize_keepalive_event(
            {
                "timestamp": "2026-01-01T00:00:00+00:00",
                "event_type": "action",
                "strategy": "SESSION_API",
                "action_result": "success",
                "response_status": 200,
                "authentication_state": "SIGNED_IN",
                "expiration_dialog_detected": False,
                "login_page_detected": False,
                "cookies": "secret-cookie-value",
                "authorization": "Bearer xyz",
                "body": "<html>account balance 123</html>",
                "password": "nope",
                "query": "token=abc",
            }
        )
    ]
    runtime._persist_keepalive_result()
    payload = json.loads(runtime.keepalive_result_path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload)
    assert "secret-cookie-value" not in serialized
    assert "Bearer xyz" not in serialized
    assert "<html>" not in serialized
    assert "nope" not in serialized
    assert "token=abc" not in serialized
    allowed = {
        "timestamp",
        "event_type",
        "strategy",
        "action_result",
        "response_status",
        "authentication_state",
        "expiration_dialog_detected",
        "login_page_detected",
    }
    assert set(payload["keepalive_events"][0]) <= allowed


def test_inspect_amex_page_signals_detects_login_page():
    from tests.test_provider_runtime_browser_inspector import (
        CdpSessionMock,
        _bind_cdp,
        _document,
        _element,
    )

    page = MagicMock()
    page.url = "https://www.americanexpress.com/en-us/account/login"
    page.main_frame = page
    page.frames = [page]
    session = CdpSessionMock(
        document=_document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY")])),
        container_node_ids=[],
    )
    _bind_cdp(page, session)
    page.locator.return_value.inner_text.return_value = (
        "Sign in to your account User ID Show password Forgot password"
    )
    signals = inspect_amex_page_signals(page)
    assert signals["login_page_detected"] is True
    assert signals["authentication_state"] == "SIGNED_OUT"
    assert page.evaluate.call_count == 0


def test_stale_canonical_signed_in_does_not_override_login_page_signed_out():
    """Login URL/page evidence wins over a stale latest_canonical SIGNED_IN."""
    from tests.test_provider_runtime_browser_inspector import (
        CdpSessionMock,
        _bind_cdp,
        _document,
        _element,
    )

    page = MagicMock()
    page.url = "https://www.americanexpress.com/en-us/account/login"
    page.main_frame = page
    page.frames = [page]
    session = CdpSessionMock(
        document=_document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY")])),
        container_node_ids=[],
    )
    _bind_cdp(page, session)
    page.locator.return_value.inner_text.return_value = (
        "Sign in to your account User ID Show password Forgot password"
    )
    signals = inspect_amex_page_signals(
        page,
        latest_canonical_state="SIGNED_IN",
    )
    assert signals["login_page_detected"] is True
    assert signals["authentication_state"] == "SIGNED_OUT"
    assert signals["inspection_authentication_state_source"] == "LATEST_CANONICAL"
    assert page.evaluate.call_count == 0


def test_latest_auth_fields_update_on_tick_while_final_unset(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.keepalive_strategy = "NONE"
    runtime.keepalive_trial_running = True
    page = MagicMock()
    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.inspect_amex_page_signals",
        return_value={
            "authentication_state": "SIGNED_IN",
            "inspection_authentication_state_source": "LATEST_CANONICAL",
            "expiration_dialog_detected": False,
            "login_page_detected": False,
            "final_url": "https://global.americanexpress.com/overview",
        },
    ):
        runtime._keepalive_tick()

    status = runtime.keepalive_status()
    assert status["keepalive_trial_running"] is True
    assert status["keepalive_latest_authentication_state"] == "SIGNED_IN"
    assert status["keepalive_latest_authentication_state_source"] == "LATEST_CANONICAL"
    assert status["keepalive_latest_reason"] == "inspection"
    assert status["keepalive_latest_observed_at"]
    assert status["keepalive_final_authentication_state"] is None
    assert status["keepalive_final_reason"] is None
    assert status["authentication_state"] == "SIGNED_IN"


def test_compatibility_auth_uses_final_state_after_completion(tmp_path: Path):
    runtime = _runtime(tmp_path)
    with patch(
        "mighty.provider_runtime.verify_amex_over_cdp",
        return_value=_signed_in_result(),
    ), patch.object(runtime, "_keepalive_tick", return_value=False):
        payload = runtime.start_keepalive_trial(
            "amex",
            strategy="NONE",
            duration_seconds=30,
            interval_seconds=5,
        )
        assert payload["ok"] is True
        assert payload["keepalive_trial_running"] is True
        assert payload["authentication_state"] == "SIGNED_IN"
        assert payload["keepalive_final_authentication_state"] is None
        stop_payload = runtime.stop_keepalive_trial(reason="manually_stopped")

    assert stop_payload["keepalive_trial_running"] is False
    assert stop_payload["keepalive_final_authentication_state"] == "SIGNED_IN"
    assert stop_payload["authentication_state"] == "SIGNED_IN"
    assert (
        stop_payload["authentication_state"]
        == stop_payload["keepalive_final_authentication_state"]
    )
