"""Tests for keepalive strategy probes and campaign preflight."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mighty.provider_runtime import (
    KeepaliveActionResult,
    ProviderRuntime,
    VerificationResult,
    format_keepalive_probe_terminal_summary,
    parse_args,
    perform_keepalive_action,
    run_amex_expiration_campaign,
    run_client_command,
    run_keepalive_preflight_for_campaign_trial,
    sanitize_keepalive_attempt,
)
from mighty.provider_runtime_campaign_analysis import (
    RESULT_INEFFECTIVE,
    RESULT_OPERATIONALLY_FAILED,
    classify_trial_result,
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


def _signed_in() -> VerificationResult:
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


def _playwright_page_ent(page: MagicMock):
    browser = MagicMock()
    browser.contexts = [MagicMock()]
    cm = MagicMock()
    cm.__enter__.return_value = MagicMock(
        chromium=MagicMock(connect_over_cdp=MagicMock(return_value=browser))
    )
    cm.__exit__.return_value = None
    return cm


def _probe_page() -> MagicMock:
    page = MagicMock()
    page.url = "https://global.americanexpress.com/overview"
    page.context.request.get.return_value = MagicMock(status=200)
    return page


def test_successful_session_api_probe(tmp_path: Path):
    runtime = _runtime(tmp_path)
    page = _probe_page()
    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.verify_amex_canonical_on_page",
        return_value=_signed_in(),
    ), patch(
        "mighty.provider_runtime.inspect_amex_page_signals",
        return_value={
            "authentication_state": "SIGNED_IN",
            "expiration_dialog_detected": False,
            "login_page_detected": False,
        },
    ):
        payload = runtime.probe_keepalive_strategy("amex", strategy="SESSION_API")

    assert payload["ok"] is True
    assert payload["success"] is True
    assert payload["strategy"] == "SESSION_API"
    assert payload["attempt"]["success"] is True
    assert payload["attempt"]["action"] == "session_api_fetch"
    assert "cookie" not in json.dumps(payload).lower()
    assert Path(payload["evidence_path"]).is_file()
    page.evaluate.assert_not_called()


def test_failed_session_api_probe(tmp_path: Path):
    runtime = _runtime(tmp_path)
    page = _probe_page()
    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.verify_amex_canonical_on_page",
        return_value=_signed_in(),
    ), patch(
        "mighty.provider_runtime.perform_keepalive_action",
        return_value=KeepaliveActionResult(
            ok=False,
            result="failure",
            error="Error: eval is disabled",
            action="session_api_fetch",
        ),
    ):
        payload = runtime.probe_keepalive_strategy("amex", strategy="SESSION_API")

    assert payload["ok"] is True
    assert payload["success"] is False
    assert "eval is disabled" in (payload.get("reason") or "")


def test_successful_page_activity_probe(tmp_path: Path):
    runtime = _runtime(tmp_path)
    page = _probe_page()
    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.verify_amex_canonical_on_page",
        return_value=_signed_in(),
    ), patch(
        "mighty.provider_runtime.inspect_amex_page_signals",
        return_value={
            "authentication_state": "SIGNED_IN",
            "expiration_dialog_detected": False,
            "login_page_detected": False,
        },
    ):
        payload = runtime.probe_keepalive_strategy("amex", strategy="PAGE_ACTIVITY")

    assert payload["success"] is True
    assert payload["attempt"]["action"] == "page_activity_scroll"
    page.mouse.wheel.assert_called()
    page.evaluate.assert_not_called()


def test_failed_page_activity_probe(tmp_path: Path):
    runtime = _runtime(tmp_path)
    page = _probe_page()
    page.mouse.wheel.side_effect = RuntimeError("input unavailable")
    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.verify_amex_canonical_on_page",
        return_value=_signed_in(),
    ):
        payload = runtime.probe_keepalive_strategy("amex", strategy="PAGE_ACTIVITY")

    assert payload["success"] is False
    assert "input unavailable" in (payload.get("reason") or "")


def test_successful_overview_reload_probe(tmp_path: Path):
    runtime = _runtime(tmp_path)
    page = _probe_page()
    with patch(
        "mighty.provider_runtime.sync_playwright",
        return_value=_playwright_page_ent(page),
    ), patch(
        "mighty.provider_runtime.select_amex_page",
        return_value=page,
    ), patch(
        "mighty.provider_runtime.verify_amex_canonical_on_page",
        return_value=_signed_in(),
    ), patch(
        "mighty.provider_runtime.inspect_amex_page_signals",
        return_value={
            "authentication_state": "SIGNED_IN",
            "expiration_dialog_detected": False,
            "login_page_detected": False,
        },
    ):
        payload = runtime.probe_keepalive_strategy("amex", strategy="OVERVIEW_RELOAD")

    assert payload["success"] is True
    page.goto.assert_called_once()
    assert "overview" in page.goto.call_args.args[0]


def test_campaign_skips_long_observation_after_failed_preflight(tmp_path: Path):
    from tests.test_provider_runtime_expiration_campaign import (
        _browser_ensure,
        _fake_experiment_result,
        _run_campaign,
    )

    http_calls: list[str] = []

    def http(method: str, url: str, payload: dict | None = None, *, timeout: float = 60):
        from urllib.parse import urlsplit

        path = urlsplit(url).path or url
        http_calls.append(path)
        if path == "/health":
            return {"ok": True}
        if path == "/providers/amex/verify":
            return {
                "ok": True,
                "result": {"authentication_state": "SIGNED_IN", "reason": "ok"},
            }
        if path == "/providers/amex/keepalive/probe":
            return {
                "ok": True,
                "success": False,
                "strategy": payload.get("strategy") if payload else None,
                "reason": "Error: eval is disabled",
                "error": "Error: eval is disabled",
                "attempt": {
                    "attempted_at": "2026-01-01T00:00:00+00:00",
                    "strategy": "SESSION_API",
                    "action": "session_api_fetch",
                    "success": False,
                    "result": "failure",
                    "reason": "Error: eval is disabled",
                    "error_type": "Error",
                    "error_message": "eval is disabled",
                },
            }
        raise AssertionError(f"unexpected {method} {path}")

    ran: list[str] = []

    def run_experiment(**kwargs):
        ran.append(str(kwargs["strategy"]))
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    campaign_dir = tmp_path / "campaign-preflight"
    result = _run_campaign(
        tmp_path,
        output_dir=campaign_dir,
        trials=["SESSION_API:30", "NONE:30"],
        continue_on_error=True,
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        ensure_managed_browser_fn=_browser_ensure(preexisting=True, launched=False),
    )

    assert ran == ["NONE"]  # SESSION_API skipped after failed preflight
    assert result["trial_summaries"][0]["strategy"] == "SESSION_API"
    assert result["trial_summaries"][0]["keepalive_outcome"] == "preflight_failed"
    assert result["trial_summaries"][0]["result_classification"] == "OPERATIONALLY_FAILED"
    assert result["trial_summaries"][0]["recorder_outcome"] == "skipped_preflight_failed"
    assert "/providers/amex/keepalive/probe" in http_calls
    attempts = campaign_dir / "trials" / "001-session-api-30s" / "keepalive-attempts.jsonl"
    assert attempts.is_file()


def test_operational_failure_distinct_from_ineffective():
    operational, *_ = classify_trial_result(
        strategy="SESSION_API",
        strategy_execution_verified=True,
        keepalive_outcome="preflight_failed",
        recorder_outcome="skipped_preflight_failed",
        final_canonical_state="SIGNED_IN",
        logout_elapsed_seconds=None,
        configured_duration_seconds=600,
        keepalive_attempt_count=1,
        keepalive_success_count=0,
        keepalive_failure_count=1,
        meaningful_logout_delay_vs_baseline=False,
        baseline_available=True,
        logout_observed=False,
        duration_completed_before_logout=False,
    )
    ineffective, *_ = classify_trial_result(
        strategy="SESSION_API",
        strategy_execution_verified=True,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical_state="SIGNED_OUT",
        logout_elapsed_seconds=301.0,
        configured_duration_seconds=600,
        keepalive_attempt_count=10,
        keepalive_success_count=10,
        keepalive_failure_count=0,
        meaningful_logout_delay_vs_baseline=False,
        baseline_available=True,
        logout_observed=True,
        duration_completed_before_logout=False,
    )
    assert operational == RESULT_OPERATIONALLY_FAILED
    assert ineffective == RESULT_INEFFECTIVE
    assert operational != ineffective


def test_probe_cli_does_not_start_or_stop_serve():
    with patch(
        "sys.argv",
        [
            "provider_runtime.py",
            "keepalive-probe",
            "amex",
            "--strategy",
            "SESSION_API",
        ],
    ):
        args = parse_args()
    assert args.command == "keepalive-probe"

    with patch(
        "mighty.provider_runtime.request_json",
        return_value={
            "ok": True,
            "success": True,
            "strategy": "SESSION_API",
            "reason": "success",
            "attempt": {"duration_ms": 12, "target": "https://functions.americanexpress.com/ReadUserSession.v1"},
            "evidence_path": "/tmp/probe.json",
        },
    ) as request_json, patch(
        "mighty.provider_runtime.run_server"
    ) as run_server, patch(
        "mighty.provider_runtime.ensure_provider_runtime_for_campaign"
    ) as ensure_runtime:
        code = run_client_command(args)

    assert code == 0
    run_server.assert_not_called()
    ensure_runtime.assert_not_called()
    request_json.assert_called_once()
    assert request_json.call_args.args[0] == "POST"
    assert request_json.call_args.args[1].endswith("/providers/amex/keepalive/probe")


def test_probe_does_not_alter_ordinary_chrome():
    source = Path("mighty/provider_runtime.py").read_text(encoding="utf-8")
    # Probe path must not launch/terminate Chrome helpers.
    probe_fn = source.split("def probe_keepalive_strategy", 1)[1].split(
        "def start_keepalive_trial", 1
    )[0]
    assert "launch_native_chrome" not in probe_fn
    assert "terminate_profile_processes" not in probe_fn
    assert "connect_chromium_over_cdp" in probe_fn
    assert "never touches ordinary Chrome" in probe_fn or "ordinary Chrome" in probe_fn


def test_sanitized_error_output_and_no_secrets_in_evidence():
    cleaned = sanitize_keepalive_attempt(
        {
            "attempted_at": "2026-01-01T00:00:00+00:00",
            "strategy": "SESSION_API",
            "action": "session_api_fetch",
            "target": "https://functions.americanexpress.com/ReadUserSession.v1?token=abc",
            "success": False,
            "result": "failure",
            "reason": "Error: boom",
            "error_type": "Error",
            "error_message": "boom",
            "cookies": "secret",
            "authorization": "Bearer xyz",
            "body": "{\"account\":123}",
            "token": "abc",
        }
    )
    serialized = json.dumps(cleaned)
    assert "secret" not in serialized
    assert "Bearer" not in serialized
    assert "account" not in serialized
    assert "token=abc" not in serialized
    summary = format_keepalive_probe_terminal_summary(
        {
            "ok": True,
            "success": False,
            "strategy": "SESSION_API",
            "reason": "Error: eval is disabled",
            "attempt": cleaned,
            "evidence_path": "/tmp/x.json",
        }
    )
    assert "FAILURE" in summary
    assert "eval is disabled" in summary
    assert "Bearer" not in summary


def test_preflight_none_skips_probe(tmp_path: Path):
    evidence = tmp_path / "trial"
    http = MagicMock()
    payload = run_keepalive_preflight_for_campaign_trial(
        strategy="NONE",
        host="127.0.0.1",
        port=8765,
        evidence_dir=evidence,
        request_json_fn=http,
    )
    assert payload["skipped"] is True
    assert payload["success"] is True
    http.assert_not_called()
    assert (evidence / "preflight-result.json").is_file()


def test_perform_keepalive_session_api_uses_context_request_not_evaluate():
    page = MagicMock()
    page.context.request.get.return_value = MagicMock(status=200)
    result = perform_keepalive_action(page, "SESSION_API")
    assert result.ok is True
    page.context.request.get.assert_called_once()
    assert "ReadUserSession.v1" in page.context.request.get.call_args.args[0]
    page.evaluate.assert_not_called()


def test_perform_keepalive_page_activity_uses_mouse_wheel_not_evaluate():
    page = MagicMock()
    page.url = "https://global.americanexpress.com/overview"
    result = perform_keepalive_action(page, "PAGE_ACTIVITY")
    assert result.ok is True
    assert page.mouse.wheel.call_count == 2
    page.evaluate.assert_not_called()
    page.goto.assert_not_called()
