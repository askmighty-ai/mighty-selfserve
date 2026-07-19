"""Tests for keepalive strategy probes and campaign preflight."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mighty.provider_runtime import (
    BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    BROWSER_CLEANUP_LEAVE_OPEN,
    KeepaliveActionResult,
    ProviderRuntime,
    VerificationResult,
    ensure_expiration_campaign_signed_in,
    ensure_managed_amex_browser_for_campaign,
    format_keepalive_probe_terminal_summary,
    parse_args,
    perform_keepalive_action,
    prepare_managed_amex_session_for_command,
    run_amex_expiration_campaign,
    run_client_command,
    run_keepalive_preflight_for_campaign_trial,
    run_keepalive_probe_with_runtime,
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


def _successful_probe_payload(
    strategy: str = "SESSION_API",
    *,
    evidence_path: str | Path | None = "/tmp/probe.json",
) -> dict:
    return {
        "ok": True,
        "success": True,
        "strategy": strategy,
        "reason": "success",
        "attempt": {
            "duration_ms": 12,
            "target": "https://functions.americanexpress.com/ReadUserSession.v1",
            "response_status": 200,
            "success": True,
            "result": "success",
        },
        "evidence_path": str(evidence_path) if evidence_path is not None else None,
        "authentication_state": "SIGNED_IN",
    }


def _signed_in_session(
    *,
    preexisting: bool = True,
    launched: bool = False,
    restarted: bool = False,
    initial_state: str = "SIGNED_IN",
    prompt_count: int = 0,
) -> dict:
    return {
        "ok": True,
        "outcome": None,
        "interrupted": False,
        "managed_browser_preexisting": preexisting,
        "managed_browser_launched": launched,
        "managed_browser_restarted": restarted,
        "initial_authentication_state": initial_state,
        "final_authentication_state": "SIGNED_IN",
        "authentication_attempt_count": prompt_count,
        "browser_info": {},
        "auth_info": {"ok": True, "prompt_count": prompt_count},
    }


def _run_probe(
    tmp_path: Path,
    *,
    strategy: str = "SESSION_API",
    runtime_preexisting: bool = True,
    runtime_started: bool = False,
    process: MagicMock | None = None,
    session: dict | None = None,
    probe_payload: dict | None = None,
    probe_request_fn=None,
    prepare_session_fn=None,
    stop_runtime_fn=None,
    close_managed_browser_fn=None,
    browser_cleanup: str = BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    print_fn=None,
    emit_summary: bool = False,
    **kwargs,
) -> dict:
    stop = stop_runtime_fn or MagicMock(return_value={"ok": True})
    closer = close_managed_browser_fn
    if closer is None:
        closer = MagicMock(return_value={"closed": False, "reason": "leave_open"})
    probe = probe_request_fn
    if probe is None:
        probe = MagicMock(
            return_value=probe_payload or _successful_probe_payload(strategy)
        )
    prepare = prepare_session_fn
    if prepare is None:
        prepare = MagicMock(return_value=session or _signed_in_session())
    return run_keepalive_probe_with_runtime(
        strategy=strategy,
        root=tmp_path,
        browser_cleanup=browser_cleanup,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": runtime_preexisting,
            "runtime_started_by_campaign": runtime_started,
            "process": process,
        },
        stop_runtime_fn=stop,
        prepare_session_fn=prepare,
        close_managed_browser_fn=closer,
        probe_request_fn=probe,
        print_fn=print_fn or (lambda *_a, **_k: None),
        emit_summary=emit_summary,
        **kwargs,
    )


def test_probe_with_existing_serve_leaves_runtime_running(tmp_path: Path):
    stop = MagicMock()
    probe = MagicMock(return_value=_successful_probe_payload())
    result = _run_probe(
        tmp_path,
        runtime_preexisting=True,
        runtime_started=False,
        stop_runtime_fn=stop,
        probe_request_fn=probe,
        session=_signed_in_session(preexisting=True, launched=False),
    )
    assert result["success"] is True
    assert result["runtime_preexisting"] is True
    assert result["runtime_started_by_probe"] is False
    assert result["runtime_stopped_by_probe"] is False
    stop.assert_not_called()
    probe.assert_called_once()


def test_probe_without_serve_auto_starts_and_stops(tmp_path: Path):
    process = MagicMock()
    stop = MagicMock(return_value={"ok": True})
    probe = MagicMock(return_value=_successful_probe_payload("PAGE_ACTIVITY"))
    result = _run_probe(
        tmp_path,
        strategy="PAGE_ACTIVITY",
        runtime_preexisting=False,
        runtime_started=True,
        process=process,
        stop_runtime_fn=stop,
        probe_request_fn=probe,
        session=_signed_in_session(preexisting=False, launched=True),
    )
    assert result["success"] is True
    assert result["runtime_started_by_probe"] is True
    assert result["runtime_stopped_by_probe"] is True
    stop.assert_called_once()
    assert stop.call_args.kwargs["process"] is process


def test_probe_successful_auto_start_messages(tmp_path: Path, capsys):
    result = _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=MagicMock(),
        print_fn=print,
        emit_summary=True,
    )
    out = capsys.readouterr().out
    assert result["runtime_started_by_probe"] is True
    assert "Keepalive probe: SESSION_API" in out
    assert "Stopping Provider Runtime started by this probe..." in out
    assert out.index("Keepalive probe: SESSION_API") < out.index(
        "Stopping Provider Runtime started by this probe..."
    )


def test_probe_successful_auto_stop_only_when_owned(tmp_path: Path, capsys):
    stop = MagicMock(return_value={"ok": True})
    result = _run_probe(
        tmp_path,
        runtime_preexisting=True,
        runtime_started=False,
        stop_runtime_fn=stop,
        print_fn=print,
        emit_summary=True,
    )
    out = capsys.readouterr().out
    assert result["runtime_stopped_by_probe"] is False
    assert "Leaving preexisting Provider Runtime running." in out
    stop.assert_not_called()


def test_probe_failure_still_cleans_up_owned_serve(tmp_path: Path):
    process = MagicMock()
    stop = MagicMock(return_value={"ok": True})
    result = _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=process,
        stop_runtime_fn=stop,
        probe_payload={
            "ok": True,
            "success": False,
            "strategy": "SESSION_API",
            "reason": "Error: boom",
            "error": "Error: boom",
        },
        session=_signed_in_session(preexisting=False, launched=True),
    )
    assert result["success"] is False
    assert result["runtime_started_by_probe"] is True
    assert result["runtime_stopped_by_probe"] is True
    stop.assert_called_once()
    assert stop.call_args.kwargs["process"] is process


def test_probe_ctrl_c_cleans_up_owned_serve(tmp_path: Path):
    process = MagicMock()
    stop = MagicMock(return_value={"ok": True})
    closer = MagicMock(
        return_value={"closed": False, "reason": "interrupted_leave_open"}
    )

    def _interrupt(**_k):
        raise KeyboardInterrupt

    result = _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=process,
        stop_runtime_fn=stop,
        close_managed_browser_fn=closer,
        probe_request_fn=_interrupt,
        session=_signed_in_session(preexisting=False, launched=True),
    )
    assert result["interrupted"] is True
    assert result["exit_code"] == 130
    assert result["runtime_stopped_by_probe"] is True
    stop.assert_called_once()
    assert closer.call_args.kwargs["interrupted"] is True


def test_probe_runtime_startup_failure(tmp_path: Path):
    stop = MagicMock()
    probe = MagicMock()
    prepare = MagicMock()
    result = run_keepalive_probe_with_runtime(
        strategy="SESSION_API",
        root=tmp_path,
        ensure_runtime_fn=lambda **_k: {
            "ok": False,
            "outcome": "runtime_start_failed",
            "message": "boom",
            "error": "boom",
            "runtime_preexisting": False,
            "runtime_started_by_campaign": False,
        },
        stop_runtime_fn=stop,
        prepare_session_fn=prepare,
        probe_request_fn=probe,
        print_fn=lambda *_a, **_k: None,
        emit_summary=False,
    )
    assert result["ok"] is False
    assert result["success"] is False
    assert result["outcome"] == "runtime_start_failed"
    assert result["exit_code"] == 1
    assert result["runtime_stopped_by_probe"] is False
    stop.assert_not_called()
    probe.assert_not_called()
    prepare.assert_not_called()


def test_probe_cli_uses_runtime_lifecycle_helper(tmp_path: Path):
    with patch(
        "sys.argv",
        [
            "provider_runtime.py",
            "keepalive-probe",
            "amex",
            "--strategy",
            "SESSION_API",
            "--browser-cleanup",
            "leave-open",
        ],
    ):
        args = parse_args()
    assert args.command == "keepalive-probe"
    assert args.browser_cleanup == "leave-open"

    with patch(
        "mighty.provider_runtime.run_keepalive_probe_with_runtime",
        return_value={
            **_successful_probe_payload(),
            "exit_code": 0,
            "runtime_preexisting": True,
            "runtime_started_by_probe": False,
            "runtime_stopped_by_probe": False,
        },
    ) as runner, patch("mighty.provider_runtime.run_server") as run_server:
        code = run_client_command(args)

    assert code == 0
    run_server.assert_not_called()
    runner.assert_called_once()
    assert runner.call_args.kwargs["strategy"] == "SESSION_API"
    assert runner.call_args.kwargs["browser_cleanup"] == "leave-open"


def test_probe_does_not_alter_ordinary_chrome():
    source = Path("mighty/provider_runtime.py").read_text(encoding="utf-8")
    # Action primitive must not launch/terminate Chrome helpers.
    probe_fn = source.split("def probe_keepalive_strategy", 1)[1].split(
        "def start_keepalive_trial", 1
    )[0]
    assert "launch_native_chrome" not in probe_fn
    assert "terminate_profile_processes" not in probe_fn
    assert "connect_chromium_over_cdp" in probe_fn
    assert "never touches ordinary Chrome" in probe_fn or "ordinary Chrome" in probe_fn

    runner_src = inspect.getsource(run_keepalive_probe_with_runtime)
    prepare_src = inspect.getsource(prepare_managed_amex_session_for_command)
    assert "Library/Application Support/Google/Chrome" not in runner_src
    assert "Library/Application Support/Google/Chrome" not in prepare_src
    assert "profile_dir" in runner_src


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


def test_probe_no_runtime_no_browser_both_auto_start(tmp_path: Path):
    process = MagicMock()
    stop = MagicMock(return_value={"ok": True})
    closer = MagicMock(return_value={"closed": True, "reason": "closed_campaign_launched_browser"})
    prepare = MagicMock(
        return_value=_signed_in_session(preexisting=False, launched=True)
    )
    probe = MagicMock(return_value=_successful_probe_payload())
    result = _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=process,
        stop_runtime_fn=stop,
        prepare_session_fn=prepare,
        close_managed_browser_fn=closer,
        probe_request_fn=probe,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    )
    assert result["success"] is True
    assert result["runtime_started_by_probe"] is True
    assert result["runtime_stopped_by_probe"] is True
    assert result["managed_browser_launched_by_probe"] is True
    assert result["managed_browser_closed_at_completion"] is True
    prepare.assert_called_once()
    probe.assert_called_once()
    stop.assert_called_once()
    closer.assert_called_once()


def test_probe_existing_runtime_no_browser_launches_browser(tmp_path: Path):
    stop = MagicMock()
    prepare = MagicMock(
        return_value=_signed_in_session(preexisting=False, launched=True)
    )
    result = _run_probe(
        tmp_path,
        runtime_preexisting=True,
        runtime_started=False,
        stop_runtime_fn=stop,
        prepare_session_fn=prepare,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        close_managed_browser_fn=MagicMock(
            return_value={"closed": True, "reason": "closed_campaign_launched_browser"}
        ),
    )
    assert result["runtime_preexisting"] is True
    assert result["runtime_stopped_by_probe"] is False
    assert result["managed_browser_launched_by_probe"] is True
    assert result["managed_browser_closed_at_completion"] is True
    stop.assert_not_called()


def test_probe_no_runtime_existing_healthy_browser(tmp_path: Path):
    process = MagicMock()
    stop = MagicMock(return_value={"ok": True})
    closer = MagicMock(return_value={"closed": False, "reason": "preexisting_never_closed"})
    result = _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=process,
        stop_runtime_fn=stop,
        close_managed_browser_fn=closer,
        session=_signed_in_session(preexisting=True, launched=False),
    )
    assert result["runtime_started_by_probe"] is True
    assert result["runtime_stopped_by_probe"] is True
    assert result["managed_browser_preexisting"] is True
    assert result["managed_browser_launched_by_probe"] is False
    assert result["managed_browser_closed_at_completion"] is False
    closer.assert_called_once()


def test_probe_existing_runtime_and_healthy_signed_in_browser(tmp_path: Path):
    stop = MagicMock()
    closer = MagicMock(return_value={"closed": False, "reason": "preexisting_never_closed"})
    probe = MagicMock(return_value=_successful_probe_payload())
    result = _run_probe(
        tmp_path,
        runtime_preexisting=True,
        runtime_started=False,
        stop_runtime_fn=stop,
        close_managed_browser_fn=closer,
        probe_request_fn=probe,
        session=_signed_in_session(preexisting=True, launched=False),
    )
    assert result["success"] is True
    assert result["runtime_preexisting"] is True
    assert result["managed_browser_preexisting"] is True
    assert result["managed_browser_closed_at_completion"] is False
    stop.assert_not_called()
    probe.assert_called_once()


def test_probe_unhealthy_zero_target_browser_restart(tmp_path: Path):
    result = _run_probe(
        tmp_path,
        session=_signed_in_session(preexisting=True, launched=True, restarted=True),
    )
    assert result["managed_browser_preexisting"] is True
    assert result["managed_browser_launched_by_probe"] is True
    assert result["managed_browser_restarted_by_probe"] is True
    assert result["success"] is True


def test_prepare_signed_out_prompts_for_authentication(tmp_path: Path):
    browser = MagicMock(
        return_value={
            "ok": True,
            "managed_browser_preexisting": False,
            "managed_browser_launched_by_campaign": True,
            "managed_browser_restarted_by_campaign": False,
        }
    )
    verifies = iter(
        [
            {
                "ok": False,
                "authentication_state": "SIGNED_OUT",
                "outcome": "initial_not_signed_in",
                "message": "signed out",
            },
            {"ok": True, "authentication_state": "SIGNED_IN", "verify_payload": {}},
        ]
    )
    prints: list[str] = []

    def fake_verify(**_k):
        return next(verifies)

    with patch(
        "mighty.provider_runtime.verify_amex_signed_in_for_experiment",
        side_effect=fake_verify,
    ):
        result = prepare_managed_amex_session_for_command(
            profile_dir=tmp_path / "amex",
            cdp_port=9223,
            base_url="http://127.0.0.1:8765",
            request_json_fn=MagicMock(),
            ensure_managed_browser_fn=browser,
            input_fn=lambda: None,
            print_fn=lambda msg: prints.append(str(msg)),
            bring_to_foreground_fn=MagicMock(),
        )
    assert result["ok"] is True
    assert result["authentication_attempt_count"] == 1
    assert result["initial_authentication_state"] == "SIGNED_OUT"
    assert result["final_authentication_state"] == "SIGNED_IN"
    assert any("Authentication required." in p for p in prints)
    assert any("Authentication verified." in p for p in prints)


def test_prepare_login_unknown_prompts_for_authentication(tmp_path: Path):
    browser = MagicMock(
        return_value={
            "ok": True,
            "managed_browser_preexisting": True,
            "managed_browser_launched_by_campaign": False,
            "managed_browser_restarted_by_campaign": False,
        }
    )
    verifies = iter(
        [
            {
                "ok": False,
                "authentication_state": "LOGIN_UNKNOWN",
                "outcome": "initial_authentication_unknown",
                "message": "unknown",
            },
            {"ok": True, "authentication_state": "SIGNED_IN", "verify_payload": {}},
        ]
    )
    with patch(
        "mighty.provider_runtime.verify_amex_signed_in_for_experiment",
        side_effect=lambda **_k: next(verifies),
    ):
        result = prepare_managed_amex_session_for_command(
            profile_dir=tmp_path / "amex",
            cdp_port=9223,
            base_url="http://127.0.0.1:8765",
            request_json_fn=MagicMock(),
            ensure_managed_browser_fn=browser,
            input_fn=lambda: None,
            print_fn=lambda *_a, **_k: None,
            bring_to_foreground_fn=MagicMock(),
        )
    assert result["ok"] is True
    assert result["initial_authentication_state"] == "LOGIN_UNKNOWN"
    assert result["authentication_attempt_count"] == 1


def test_prepare_failed_first_verification_prompts_again(tmp_path: Path):
    browser = MagicMock(
        return_value={
            "ok": True,
            "managed_browser_preexisting": False,
            "managed_browser_launched_by_campaign": True,
            "managed_browser_restarted_by_campaign": False,
        }
    )
    verifies = iter(
        [
            {
                "ok": False,
                "authentication_state": "SIGNED_OUT",
                "outcome": "initial_not_signed_in",
                "message": "signed out",
            },
            {
                "ok": False,
                "authentication_state": "SIGNED_OUT",
                "outcome": "initial_not_signed_in",
                "message": "still signed out",
            },
            {"ok": True, "authentication_state": "SIGNED_IN", "verify_payload": {}},
        ]
    )
    prints: list[str] = []
    with patch(
        "mighty.provider_runtime.verify_amex_signed_in_for_experiment",
        side_effect=lambda **_k: next(verifies),
    ):
        result = prepare_managed_amex_session_for_command(
            profile_dir=tmp_path / "amex",
            cdp_port=9223,
            base_url="http://127.0.0.1:8765",
            request_json_fn=MagicMock(),
            ensure_managed_browser_fn=browser,
            input_fn=lambda: None,
            print_fn=lambda msg: prints.append(str(msg)),
            bring_to_foreground_fn=MagicMock(),
        )
    assert result["ok"] is True
    assert result["authentication_attempt_count"] == 2
    assert sum("Authentication was not verified" in p for p in prints) == 1
    assert any("Authentication verified." in p for p in prints)


def test_successful_second_verification_runs_probe(tmp_path: Path, capsys):
    prepare = MagicMock(
        return_value=_signed_in_session(
            preexisting=False,
            launched=True,
            initial_state="SIGNED_OUT",
            prompt_count=2,
        )
    )
    probe = MagicMock(return_value=_successful_probe_payload())
    result = _run_probe(
        tmp_path,
        prepare_session_fn=prepare,
        probe_request_fn=probe,
        print_fn=print,
        emit_summary=True,
        session=None,
    )
    out = capsys.readouterr().out
    assert result["success"] is True
    assert result["authentication_attempt_count"] == 2
    assert "Running keepalive probe: SESSION_API..." in out
    probe.assert_called_once()


def test_probe_does_not_run_before_signed_in(tmp_path: Path):
    probe = MagicMock()
    prepare = MagicMock(
        return_value={
            "ok": False,
            "outcome": "interrupted",
            "interrupted": True,
            "error": "interrupted",
            "message": "Authentication interrupted by user",
            "managed_browser_preexisting": False,
            "managed_browser_launched": True,
            "managed_browser_restarted": False,
            "initial_authentication_state": "SIGNED_OUT",
            "final_authentication_state": "SIGNED_OUT",
            "authentication_attempt_count": 0,
        }
    )
    result = _run_probe(
        tmp_path,
        prepare_session_fn=prepare,
        probe_request_fn=probe,
        session=None,
    )
    assert result["interrupted"] is True
    assert result["exit_code"] == 130
    probe.assert_not_called()


def test_probe_session_api_success_through_preflight(tmp_path: Path):
    result = _run_probe(
        tmp_path,
        strategy="SESSION_API",
        probe_payload=_successful_probe_payload("SESSION_API"),
    )
    assert result["success"] is True
    assert result["strategy"] == "SESSION_API"
    assert result["attempt"]["response_status"] == 200


def test_probe_page_activity_success_through_preflight(tmp_path: Path):
    result = _run_probe(
        tmp_path,
        strategy="PAGE_ACTIVITY",
        probe_payload=_successful_probe_payload("PAGE_ACTIVITY"),
    )
    assert result["success"] is True
    assert result["strategy"] == "PAGE_ACTIVITY"


def test_action_failure_distinct_from_precondition_recovery(tmp_path: Path):
    result = _run_probe(
        tmp_path,
        session=_signed_in_session(preexisting=True, launched=False),
        probe_payload={
            "ok": True,
            "success": False,
            "strategy": "SESSION_API",
            "reason": "Error: eval is disabled",
            "error": "Error: eval is disabled",
            "attempt": {
                "duration_ms": 5,
                "target": "https://functions.americanexpress.com/ReadUserSession.v1",
                "success": False,
            },
        },
    )
    assert result["success"] is False
    assert result["outcome"] == "Error: eval is disabled"
    assert result["initial_authentication_state"] == "SIGNED_IN"
    assert "eval is disabled" in (result.get("reason") or "")


def test_probe_launched_browser_closes_under_close_on_completion(tmp_path: Path):
    closer = MagicMock(return_value={"closed": True, "reason": "closed_campaign_launched_browser"})
    result = _run_probe(
        tmp_path,
        session=_signed_in_session(preexisting=False, launched=True),
        close_managed_browser_fn=closer,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    )
    assert result["managed_browser_closed_at_completion"] is True
    assert closer.call_args.kwargs["managed_browser_launched_by_campaign"] is True
    assert closer.call_args.kwargs["managed_browser_preexisting"] is False
    assert closer.call_args.kwargs["browser_cleanup"] == BROWSER_CLEANUP_CLOSE_ON_COMPLETION


def test_preexisting_browser_remains_open(tmp_path: Path):
    closer = MagicMock(return_value={"closed": False, "reason": "preexisting_never_closed"})
    result = _run_probe(
        tmp_path,
        session=_signed_in_session(preexisting=True, launched=False),
        close_managed_browser_fn=closer,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    )
    assert result["managed_browser_closed_at_completion"] is False
    assert closer.call_args.kwargs["managed_browser_preexisting"] is True


def test_leave_open_preserves_probe_launched_browser(tmp_path: Path):
    closer = MagicMock(return_value={"closed": False, "reason": "leave_open"})
    result = _run_probe(
        tmp_path,
        session=_signed_in_session(preexisting=False, launched=True),
        close_managed_browser_fn=closer,
        browser_cleanup=BROWSER_CLEANUP_LEAVE_OPEN,
    )
    assert result["managed_browser_closed_at_completion"] is False
    assert closer.call_args.kwargs["browser_cleanup"] == BROWSER_CLEANUP_LEAVE_OPEN


def test_ctrl_c_preserves_evidence_and_does_not_close_ordinary_chrome(tmp_path: Path):
    evidence = tmp_path / "probe.json"
    evidence.write_text("{\"ok\": true}\n", encoding="utf-8")
    process = MagicMock()
    stop = MagicMock(return_value={"ok": True})
    closer = MagicMock(return_value={"closed": False, "reason": "interrupted_leave_open"})

    def _interrupt(**_k):
        raise KeyboardInterrupt

    result = _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=process,
        stop_runtime_fn=stop,
        close_managed_browser_fn=closer,
        probe_request_fn=_interrupt,
        session=_signed_in_session(preexisting=False, launched=True),
        probe_payload=None,
    )
    # Force evidence path through prepare+interrupt before probe writes one:
    # interrupted during probe still enriches ownership onto payload.
    assert result["interrupted"] is True
    assert closer.call_args.kwargs["interrupted"] is True
    assert "Library/Application Support/Google/Chrome" not in inspect.getsource(
        run_keepalive_probe_with_runtime
    )


def test_browser_start_failed_cleans_owned_runtime(tmp_path: Path):
    process = MagicMock()
    stop = MagicMock(return_value={"ok": True})
    probe = MagicMock()
    prepare = MagicMock(
        return_value={
            "ok": False,
            "outcome": "browser_start_failed",
            "error": "launch failed",
            "message": "launch failed",
            "interrupted": False,
            "managed_browser_preexisting": False,
            "managed_browser_launched": False,
            "managed_browser_restarted": False,
            "initial_authentication_state": None,
            "final_authentication_state": None,
            "authentication_attempt_count": 0,
        }
    )
    result = _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=process,
        stop_runtime_fn=stop,
        prepare_session_fn=prepare,
        probe_request_fn=probe,
        session=None,
    )
    assert result["outcome"] == "browser_start_failed"
    assert result["runtime_stopped_by_probe"] is True
    stop.assert_called_once()
    probe.assert_not_called()


def test_campaign_and_probe_use_shared_preflight_helpers():
    prepare_src = inspect.getsource(prepare_managed_amex_session_for_command)
    probe_src = inspect.getsource(run_keepalive_probe_with_runtime)
    campaign_src = inspect.getsource(run_amex_expiration_campaign)
    assert "ensure_managed_amex_browser_for_campaign" in prepare_src
    assert "ensure_expiration_campaign_signed_in" in prepare_src
    assert "prepare_managed_amex_session_for_command" in probe_src
    assert "ensure_managed_amex_browser_for_campaign" in campaign_src
    assert "ensure_expiration_campaign_signed_in" in campaign_src
    assert ensure_managed_amex_browser_for_campaign is not None
    assert ensure_expiration_campaign_signed_in is not None


def test_cleanup_output_occurs_after_probe_result(tmp_path: Path, capsys):
    _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=MagicMock(),
        print_fn=print,
        emit_summary=True,
    )
    out = capsys.readouterr().out
    assert "Running keepalive probe: SESSION_API..." in out
    assert "Result: SUCCESS" in out
    assert out.index("Result: SUCCESS") < out.index(
        "Stopping Provider Runtime started by this probe..."
    )


def test_probe_evidence_fields_populated_and_sanitized(tmp_path: Path):
    evidence = tmp_path / "amex-keepalive-probe-session_api.json"
    evidence.write_text(
        json.dumps(
            {
                "ok": True,
                "success": True,
                "strategy": "SESSION_API",
                "cookies": "secret",
                "authorization": "Bearer xyz",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _run_probe(
        tmp_path,
        runtime_preexisting=False,
        runtime_started=True,
        process=MagicMock(),
        session=_signed_in_session(
            preexisting=False,
            launched=True,
            initial_state="SIGNED_OUT",
            prompt_count=1,
        ),
        probe_payload=_successful_probe_payload(
            "SESSION_API", evidence_path=evidence
        ),
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        close_managed_browser_fn=MagicMock(
            return_value={"closed": True, "reason": "closed_campaign_launched_browser"}
        ),
    )
    assert result["success"] is True
    saved = json.loads(evidence.read_text(encoding="utf-8"))
    assert saved["runtime_started_by_probe"] is True
    assert saved["managed_browser_launched_by_probe"] is True
    assert saved["managed_browser_closed_at_completion"] is True
    assert saved["browser_cleanup_policy"] == BROWSER_CLEANUP_CLOSE_ON_COMPLETION
    assert saved["initial_authentication_state"] == "SIGNED_OUT"
    assert saved["final_authentication_state"] == "SIGNED_IN"
    assert saved["authentication_attempt_count"] == 1
    assert saved["strategy"] == "SESSION_API"
    assert saved["result"] == "SUCCESS"
    assert saved["response_status"] == 200
    assert "cookies" not in saved
    assert "authorization" not in saved
    assert "Bearer" not in json.dumps(saved)
