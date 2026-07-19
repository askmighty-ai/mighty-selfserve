"""Tests for the Amex expiration campaign runner."""

from __future__ import annotations

import csv
import inspect
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from mighty.provider_runtime import (
    BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    BROWSER_CLEANUP_LEAVE_OPEN,
    EXPIRATION_CAMPAIGN_DIR_PREFIX,
    EXPIRATION_EXPERIMENT_SERVE_HINT,
    KEEPALIVE_STRATEGIES,
    MANAGED_BROWSER_ABSENT,
    MANAGED_BROWSER_HEALTHY,
    MANAGED_BROWSER_UNHEALTHY,
    classify_managed_amex_browser,
    create_expiration_campaign_zip,
    derive_expiration_campaign_trial_metrics,
    ensure_expiration_campaign_signed_in,
    ensure_managed_amex_browser_for_campaign,
    expiration_campaign_trial_dirname,
    launch_managed_amex_browser,
    launch_native_chrome,
    maybe_close_managed_browser_for_campaign,
    parse_args,
    parse_expiration_campaign_trial_spec,
    parse_expiration_campaign_trial_specs,
    print_expiration_campaign_result,
    run_amex_expiration_campaign,
    run_amex_expiration_experiment,
    wait_for_managed_browser_ready,
)


def _verify_payload(state: str) -> dict:
    return {
        "ok": state == "SIGNED_IN",
        "result": {
            "provider": "amex",
            "authentication_state": state,
            "reason": f"state:{state}",
        },
    }


class _CampaignHttp:
    def __init__(self, verify_states: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.verify_states = list(verify_states or ["SIGNED_IN"])
        self._verify_index = 0
        self.health_raises: Exception | None = None
        self.verify_raises: Exception | None = None

    def __call__(
        self,
        method: str,
        url: str,
        payload: dict | None = None,
        *,
        timeout: float = 60,
    ) -> dict:
        from urllib.parse import urlsplit

        path = urlsplit(url).path or url
        self.calls.append((method, path))
        if path == "/health":
            if self.health_raises is not None:
                raise self.health_raises
            return {"ok": True, "runtime_pid": 1}
        if path == "/providers/amex/verify":
            if self.verify_raises is not None:
                raise self.verify_raises
            state = self.verify_states[
                min(self._verify_index, len(self.verify_states) - 1)
            ]
            self._verify_index += 1
            return _verify_payload(state)
        if path == "/providers/amex/keepalive/probe":
            strategy = str((payload or {}).get("strategy") or "NONE")
            return {
                "ok": True,
                "success": True,
                "strategy": strategy,
                "reason": "success",
                "attempt": {
                    "attempted_at": "2026-01-01T00:00:00+00:00",
                    "strategy": strategy,
                    "action": "probe",
                    "success": True,
                    "result": "success",
                },
            }
        raise AssertionError(f"unexpected request {method} {path}")


def _fake_experiment_result(
    *,
    output_dir: Path,
    strategy: str,
    interval: int,
    outcome: str = "logged_out",
    error: str | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    recorder_dir = output_dir / "recorder"
    recorder_dir.mkdir(parents=True, exist_ok=True)
    recording = {
        "ok": True,
        "outcome": outcome,
        "logout_detected_at": "2026-01-01T00:10:00+00:00",
        "initial_canonical_authentication_state": "SIGNED_IN",
        "final_canonical_authentication_state": (
            "SIGNED_OUT" if outcome == "logged_out" else "SIGNED_IN"
        ),
        "observations": [
            {
                "observed_at": "2026-01-01T00:09:00+00:00",
                "browser_inspector": {
                    "candidate_count": 1,
                    "candidates": [
                        {
                            "text_snippet": (
                                "Your session is about to expire due to inactivity. "
                                "Log Out or Continue."
                            ),
                            "visible_button_labels": ["Continue", "Log Out"],
                        }
                    ],
                    "errors": [],
                },
                "optional_text_searches": [
                    {"term": "expire", "match_count": 1, "match_summaries": []},
                    {"term": "continue", "match_count": 1, "match_summaries": []},
                ],
            }
        ],
    }
    (recorder_dir / "recording.json").write_text(
        json.dumps(recording) + "\n", encoding="utf-8"
    )
    summary = {
        "outcome": outcome,
        "keepalive_outcome": "logged_out" if outcome == "logged_out" else "duration_completed",
        "final_authentication_state": recording["final_canonical_authentication_state"],
        "keepalive_strategy": strategy,
        "keepalive_interval_seconds": interval,
        "keepalive_wait_seconds": 1.5,
        "keepalive_completion_timeout": False,
        "recorder_outcome": outcome,
        "recorder_dir": str(recorder_dir),
        "experiment_dir": str(output_dir),
        "interrupted": outcome == "interrupted",
    }
    (output_dir / "experiment-summary.json").write_text(
        json.dumps(summary) + "\n", encoding="utf-8"
    )
    return {
        "ok": error is None and outcome != "fatal_error",
        "outcome": outcome,
        "keepalive_outcome": summary["keepalive_outcome"],
        "final_authentication_state": summary["final_authentication_state"],
        "experiment_dir": str(output_dir),
        "zip_path": str(output_dir / f"{output_dir.name}.zip"),
        "summary": summary,
        "recorder": recording,
        "keepalive_status": {
            "ok": True,
            "keepalive_expiration_dialog_seen": True,
            "keepalive_logged_out": outcome == "logged_out",
            "keepalive_events": [
                {
                    "event_type": "expiration_dialog",
                    "observed_at": "2026-01-01T00:09:00+00:00",
                    "expiration_dialog_detected": True,
                }
            ],
        },
        "keepalive_wait_seconds": 1.5,
        "keepalive_completion_timeout": False,
        "error": error,
        "exit_code": 1 if error or outcome == "fatal_error" else 0,
        "message": error,
    }


def _browser_ensure(
    *,
    preexisting: bool = False,
    launched: bool = True,
    restarted: bool = False,
) -> MagicMock:
    fn = MagicMock(
        return_value={
            "ok": True,
            "state": MANAGED_BROWSER_HEALTHY if preexisting and not launched else MANAGED_BROWSER_ABSENT,
            "cdp_url": "http://127.0.0.1:9223",
            "managed_browser_preexisting": preexisting,
            "managed_browser_launched_by_campaign": launched,
            "managed_browser_restarted_by_campaign": restarted,
            "managed_cdp_port": 9223,
            "managed_profile_path": "/tmp/amex",
        }
    )
    return fn


def _run_campaign(tmp_path: Path, **kwargs):
    defaults = {
        "root": tmp_path / "runtime",
        "diagnostics_dir": tmp_path / "runtime" / "diagnostics",
        "cdp_port": 9223,
        "sleep_fn": lambda _s: None,
        "input_fn": lambda: "",
        "print_fn": lambda *_a, **_k: None,
        "bring_to_foreground_fn": lambda: {"ok": True},
        "ensure_managed_browser_fn": _browser_ensure(preexisting=True, launched=False),
        "classify_managed_browser_fn": lambda: {
            "state": MANAGED_BROWSER_HEALTHY,
            "cdp_url": "http://127.0.0.1:9223",
            "page_target_count": 1,
        },
        "close_managed_browser_fn": lambda **_k: {
            "closed": False,
            "reason": "test",
            "browser_cleanup_policy": BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        },
    }
    defaults.update(kwargs)
    return run_amex_expiration_campaign(**defaults)


def test_parse_trial_spec_valid():
    parsed = parse_expiration_campaign_trial_spec("SESSION_API:30")
    assert parsed["strategy"] == "SESSION_API"
    assert parsed["keepalive_interval_seconds"] == 30


def test_parse_trial_spec_invalid_strategy():
    with pytest.raises(ValueError, match="Unsupported keepalive strategy"):
        parse_expiration_campaign_trial_spec("NOT_A_STRATEGY:30")


def test_parse_trial_spec_invalid_interval():
    with pytest.raises(ValueError, match="Invalid keepalive interval"):
        parse_expiration_campaign_trial_spec("NONE:abc")
    with pytest.raises(ValueError, match="Invalid keepalive interval"):
        parse_expiration_campaign_trial_spec("NONE:0")


def test_parse_all_trials_before_start():
    specs = parse_expiration_campaign_trial_specs(
        ["NONE:30", "SESSION_API:5", "OVERVIEW_RELOAD:30"]
    )
    assert [item["strategy"] for item in specs] == [
        "NONE",
        "SESSION_API",
        "OVERVIEW_RELOAD",
    ]


def test_trial_dirname_format():
    assert expiration_campaign_trial_dirname(1, "NONE", 30) == "001-none-30s"
    assert (
        expiration_campaign_trial_dirname(2, "SESSION_API", 5) == "002-session-api-5s"
    )


def test_cli_parses_repeatable_trials_and_cleanup():
    with patch(
        "sys.argv",
        [
            "provider_runtime.py",
            "browser-run-expiration-campaign",
            "amex",
            "--trial",
            "NONE:30",
            "--trial",
            "SESSION_API:5",
            "--campaign-name",
            "amex-keepalive-comparison",
            "--browser-cleanup",
            "leave-open",
            "--continue-on-error",
            "--skip-completed",
        ],
    ):
        args = parse_args()
    assert args.command == "browser-run-expiration-campaign"
    assert args.trials == ["NONE:30", "SESSION_API:5"]
    assert args.browser_cleanup == "leave-open"
    assert args.continue_on_error is True


def test_classify_healthy_absent_unhealthy():
    def fetch_healthy(url: str):
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser"}
        return [{"type": "page", "url": "https://global.americanexpress.com/overview"}]

    def fetch_absent(url: str):
        raise URLError("connection refused")

    def fetch_zero(url: str):
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser"}
        return []

    assert (
        classify_managed_amex_browser(9223, fetch_cdp_json_fn=fetch_healthy)["state"]
        == MANAGED_BROWSER_HEALTHY
    )
    assert (
        classify_managed_amex_browser(9223, fetch_cdp_json_fn=fetch_absent)["state"]
        == MANAGED_BROWSER_ABSENT
    )
    assert (
        classify_managed_amex_browser(9223, fetch_cdp_json_fn=fetch_zero)["state"]
        == MANAGED_BROWSER_UNHEALTHY
    )


def test_healthy_managed_browser_is_reused(tmp_path: Path):
    launched = MagicMock()
    restarted = MagicMock()
    result = ensure_managed_amex_browser_for_campaign(
        profile_dir=tmp_path / "amex",
        cdp_port=9223,
        classify_fn=lambda: {
            "state": MANAGED_BROWSER_HEALTHY,
            "cdp_url": "http://127.0.0.1:9223",
        },
        launch_fn=launched,
        restart_fn=restarted,
        headless_fn=lambda: False,
        print_fn=lambda *_a, **_k: None,
    )
    assert result["managed_browser_preexisting"] is True
    assert result["managed_browser_launched_by_campaign"] is False
    assert result["managed_browser_restarted_by_campaign"] is False
    launched.assert_not_called()
    restarted.assert_not_called()


def test_absent_managed_browser_is_launched(tmp_path: Path):
    launched = MagicMock(
        return_value={"ok": True, "cdp_url": "http://127.0.0.1:9223", "page_target_count": 1}
    )
    result = ensure_managed_amex_browser_for_campaign(
        profile_dir=tmp_path / "amex",
        cdp_port=9223,
        classify_fn=lambda: {"state": MANAGED_BROWSER_ABSENT, "cdp_url": None},
        launch_fn=launched,
        restart_fn=MagicMock(),
        print_fn=lambda *_a, **_k: None,
    )
    assert result["managed_browser_preexisting"] is False
    assert result["managed_browser_launched_by_campaign"] is True
    launched.assert_called_once()


def test_zero_targets_triggers_managed_browser_restart(tmp_path: Path):
    restarted = MagicMock(
        return_value={"ok": True, "cdp_url": "http://127.0.0.1:9223", "restarted": True}
    )
    result = ensure_managed_amex_browser_for_campaign(
        profile_dir=tmp_path / "amex",
        cdp_port=9223,
        classify_fn=lambda: {
            "state": MANAGED_BROWSER_UNHEALTHY,
            "cdp_url": "http://127.0.0.1:9223",
            "page_target_count": 0,
        },
        launch_fn=MagicMock(),
        restart_fn=restarted,
        print_fn=lambda *_a, **_k: None,
    )
    assert result["managed_browser_preexisting"] is True
    assert result["managed_browser_launched_by_campaign"] is True
    assert result["managed_browser_restarted_by_campaign"] is True
    restarted.assert_called_once()


def test_launch_waits_for_usable_page_target(tmp_path: Path):
    calls = {"n": 0}

    def fetch(url: str):
        calls["n"] += 1
        if url.endswith("/json/version"):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser"}
        if calls["n"] < 4:
            return []
        return [{"type": "page", "url": "https://www.americanexpress.com/en-us/account/login"}]

    launch = MagicMock(return_value=MagicMock(pid=4242))
    result = launch_managed_amex_browser(
        profile_dir=tmp_path / "amex",
        cdp_port=9223,
        launch_native_chrome_fn=launch,
        fetch_cdp_json_fn=fetch,
        sleep_fn=lambda _s: None,
        monotonic_fn=lambda: calls["n"] * 0.1,
        startup_timeout_seconds=5,
    )
    launch.assert_called_once()
    assert launch.call_args.kwargs["headless"] is False
    assert launch.call_args.kwargs["profile_dir"] == (tmp_path / "amex").resolve()
    assert result["page_target_count"] == 1


def test_startup_timeout_is_bounded(tmp_path: Path):
    clock = {"t": 0.0}

    def fetch(url: str):
        raise URLError("not ready")

    with pytest.raises(RuntimeError, match="did not become ready"):
        wait_for_managed_browser_ready(
            9223,
            timeout_seconds=1.0,
            fetch_cdp_json_fn=fetch,
            sleep_fn=lambda s: clock.__setitem__("t", clock["t"] + float(s)),
            monotonic_fn=lambda: clock["t"],
        )
    assert clock["t"] >= 1.0


def test_launch_reuses_launch_native_chrome():
    source = inspect.getsource(launch_managed_amex_browser)
    assert "launch_native_chrome" in source
    assert "--user-data-dir" not in source
    assert "--remote-debugging-port" not in source
    assert callable(launch_native_chrome)


def test_authentication_prompt_after_automatic_launch(tmp_path: Path, capsys):
    http = _CampaignHttp(verify_states=["SIGNED_OUT", "SIGNED_IN"])
    ensure = _browser_ensure(preexisting=False, launched=True)

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "launch-auth",
        trials=["NONE:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        ensure_managed_browser_fn=ensure,
        print_fn=print,
    )
    out = capsys.readouterr().out
    assert "Authentication required for trial 1." in out
    assert "A dedicated Mighty Amex Chrome window has been opened." in out
    assert "Sign in and complete MFA." in out
    assert "Wait until the account overview is fully loaded." in out
    assert "Press Enter here when ready." in out
    assert "Input received." in out
    assert "Verifying authentication..." in out
    assert "Authentication verified." in out
    assert "bootstrap amex" not in out
    assert result["outcome"] == "completed"
    assert result["managed_browser_launched_by_campaign"] is True


def test_successful_verification_continues_campaign(tmp_path: Path):
    http = _CampaignHttp(verify_states=["SIGNED_IN"])
    ran = []

    def run_experiment(**kwargs):
        ran.append(kwargs["strategy"])
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "ok",
        trials=["NONE:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
    )
    assert ran == ["NONE"]
    assert result["outcome"] == "completed"


def test_failed_verification_does_not_start_trial_until_signed_in(tmp_path: Path):
    # Stay SIGNED_OUT until the second Enter, then SIGNED_IN.
    http = _CampaignHttp(verify_states=["SIGNED_OUT", "SIGNED_OUT", "SIGNED_IN"])
    ran = []
    prompts: list[str] = []

    def run_experiment(**kwargs):
        ran.append("ran")
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "fail-auth",
        trials=["NONE:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        input_fn=lambda: prompts.append("enter") or "",
    )
    assert prompts == ["enter", "enter"]
    assert ran == ["ran"]
    assert result["outcome"] == "completed"
    assert result["trial_summaries"][0]["error"] is None


def test_logout_between_trials_prompts_reauthentication(tmp_path: Path, capsys):
    http = _CampaignHttp(verify_states=["SIGNED_IN", "SIGNED_OUT", "SIGNED_IN"])
    prompts: list[str] = []

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "reauth",
        trials=["NONE:30", "SESSION_API:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        input_fn=lambda: prompts.append("enter") or "",
        print_fn=print,
        ensure_managed_browser_fn=_browser_ensure(preexisting=True, launched=False),
    )
    out = capsys.readouterr().out
    assert "Authentication required for trial 2." in out
    assert "Sign in and complete MFA." in out
    assert prompts == ["enter"]
    assert result["outcome"] == "completed"


def test_zero_target_state_between_trials_restarts_managed_browser(tmp_path: Path):
    http = _CampaignHttp()
    # classify_managed_browser_fn is consulted before trial 2+.
    states = [
        {"state": MANAGED_BROWSER_UNHEALTHY, "cdp_url": "http://127.0.0.1:9223"},
    ]
    restarts = MagicMock(return_value={"ok": True, "cdp_url": "http://127.0.0.1:9223"})

    def classify():
        return states.pop(0) if states else {
            "state": MANAGED_BROWSER_HEALTHY,
            "cdp_url": "http://127.0.0.1:9223",
        }

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "zero-targets",
        trials=["NONE:30", "SESSION_API:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        classify_managed_browser_fn=classify,
        restart_managed_browser_fn=restarts,
        ensure_managed_browser_fn=_browser_ensure(preexisting=True, launched=False),
    )
    assert restarts.call_count == 1
    assert result["managed_browser_restarted_by_campaign"] is True
    assert result["outcome"] == "completed"


def test_close_on_completion_closes_only_campaign_launched_browser(tmp_path: Path):
    closed = maybe_close_managed_browser_for_campaign(
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        managed_browser_preexisting=False,
        managed_browser_launched_by_campaign=True,
        interrupted=False,
        profile_dir=tmp_path / "amex",
        terminate_profile_processes_fn=MagicMock(),
    )
    assert closed["closed"] is True

    http = _CampaignHttp()
    terminator = MagicMock()

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "close",
        trials=["NONE:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        ensure_managed_browser_fn=_browser_ensure(preexisting=False, launched=True),
        close_managed_browser_fn=None,
        terminate_profile_processes_fn=terminator,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    )
    assert result["managed_browser_closed_at_completion"] is True
    terminator.assert_called()


def test_preexisting_managed_browser_is_never_closed(tmp_path: Path):
    terminator = MagicMock()
    closed = maybe_close_managed_browser_for_campaign(
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        managed_browser_preexisting=True,
        managed_browser_launched_by_campaign=True,
        interrupted=False,
        profile_dir=tmp_path / "amex",
        terminate_profile_processes_fn=terminator,
    )
    assert closed["closed"] is False
    assert closed["reason"] == "preexisting_never_closed"
    terminator.assert_not_called()


def test_leave_open_preserves_browser(tmp_path: Path):
    terminator = MagicMock()
    closed = maybe_close_managed_browser_for_campaign(
        browser_cleanup=BROWSER_CLEANUP_LEAVE_OPEN,
        managed_browser_preexisting=False,
        managed_browser_launched_by_campaign=True,
        interrupted=False,
        profile_dir=tmp_path / "amex",
        terminate_profile_processes_fn=terminator,
    )
    assert closed["closed"] is False
    terminator.assert_not_called()


def test_ctrl_c_preserves_partial_evidence_and_leaves_browser(tmp_path: Path):
    http = _CampaignHttp()
    terminator = MagicMock()

    def run_experiment(**kwargs):
        if kwargs["strategy"] == "SESSION_API":
            raise KeyboardInterrupt
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "partial",
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        ensure_managed_browser_fn=_browser_ensure(preexisting=False, launched=True),
        close_managed_browser_fn=None,
        terminate_profile_processes_fn=terminator,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    )
    assert result["interrupted"] is True
    assert result["exit_code"] == 130
    assert Path(result["zip_path"]).is_file()
    assert result["managed_browser_closed_at_completion"] is False
    terminator.assert_not_called()


def test_ordinary_chrome_profiles_never_targeted():
    source = "\n".join(
        [
            inspect.getsource(ensure_managed_amex_browser_for_campaign),
            inspect.getsource(launch_managed_amex_browser),
            inspect.getsource(maybe_close_managed_browser_for_campaign),
            inspect.getsource(run_amex_expiration_campaign),
        ]
    )
    assert "Library/Application Support/Google/Chrome" not in source
    assert "launch_native_chrome" in source
    assert "terminate_profile_processes" in source
    assert "profile_dir" in source


def test_campaign_does_not_start_or_stop_serve():
    source = inspect.getsource(run_amex_expiration_campaign)
    assert "run_server" not in source
    assert 'command == "serve"' not in source
    assert 'command == "stop"' not in source
    assert "/shutdown" not in source


def test_sequential_execution(tmp_path: Path):
    http = _CampaignHttp()
    calls: list[tuple[str, int]] = []

    def run_experiment(**kwargs):
        strategy = str(kwargs["strategy"])
        interval = int(kwargs["keepalive_interval_seconds"])
        calls.append((strategy, interval))
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=strategy,
            interval=interval,
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "campaign",
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
    )
    assert calls == [("NONE", 30), ("SESSION_API", 30), ("PAGE_ACTIVITY", 30)]
    assert result["outcome"] == "completed"


def test_successful_reverification_after_user_confirmation():
    http = _CampaignHttp(verify_states=["SIGNED_OUT", "SIGNED_IN"])
    auth = ensure_expiration_campaign_signed_in(
        trial_number=1,
        base_url="http://127.0.0.1:8765",
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
        print_fn=lambda *_a, **_k: None,
        browser_launched=True,
    )
    assert auth["ok"] is True
    assert auth["paused"] is True


def test_failed_reverification_prompts_again_until_signed_in():
    http = _CampaignHttp(verify_states=["SIGNED_OUT", "SIGNED_OUT", "SIGNED_IN"])
    prompts: list[str] = []
    lines: list[str] = []

    def input_fn() -> str:
        prompts.append("enter")
        return ""

    auth = ensure_expiration_campaign_signed_in(
        trial_number=3,
        base_url="http://127.0.0.1:8765",
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        input_fn=input_fn,
        print_fn=lambda msg="", **_k: lines.append(str(msg)),
    )
    assert auth["ok"] is True
    assert auth["paused"] is True
    assert len(prompts) == 2
    joined = "\n".join(lines)
    assert "Authentication was not verified:" in joined
    assert "Please finish signing in and press Enter to try again." in joined
    assert "Authentication verified." in joined


def test_continue_on_error(tmp_path: Path):
    http = _CampaignHttp()
    calls: list[str] = []

    def run_experiment(**kwargs):
        strategy = str(kwargs["strategy"])
        calls.append(strategy)
        if strategy == "SESSION_API":
            return _fake_experiment_result(
                output_dir=Path(kwargs["output_dir"]),
                strategy=strategy,
                interval=int(kwargs["keepalive_interval_seconds"]),
                outcome="fatal_error",
                error="boom",
            )
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=strategy,
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "continue",
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        continue_on_error=True,
        request_json_fn=http,
        run_experiment_fn=run_experiment,
    )
    assert calls == ["NONE", "SESSION_API", "PAGE_ACTIVITY"]
    assert result["exit_code"] == 1


def test_skip_completed_resume(tmp_path: Path):
    http = _CampaignHttp()
    campaign_dir = tmp_path / "resume"

    def run_first(**kwargs):
        if kwargs["strategy"] == "SESSION_API":
            raise KeyboardInterrupt
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    first = _run_campaign(
        tmp_path,
        output_dir=campaign_dir,
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=run_first,
    )
    assert first["interrupted"] is True

    manifest_path = campaign_dir / "campaign-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["trials"]:
        if row["strategy"] == "NONE":
            row["status"] = "completed"
            row["error"] = None
    manifest["trials"] = [row for row in manifest["trials"] if row["status"] == "completed"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    second_calls: list[str] = []

    def run_second(**kwargs):
        second_calls.append(str(kwargs["strategy"]))
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    second = _run_campaign(
        tmp_path,
        output_dir=campaign_dir,
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        skip_completed=True,
        request_json_fn=http,
        run_experiment_fn=run_second,
    )
    assert second_calls == ["SESSION_API", "PAGE_ACTIVITY"]
    assert second["trial_summaries"][0]["skipped"] is True


def test_one_zip_contains_all_trial_directories(tmp_path: Path):
    http = _CampaignHttp()

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "zipcamp",
        trials=["NONE:30", "SESSION_API:5"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
    )
    with zipfile.ZipFile(result["zip_path"]) as zf:
        names = set(zf.namelist())
    assert "campaign-summary.json" in names
    assert any(name.startswith("trials/001-none-30s/") for name in names)


def test_summary_includes_managed_browser_fields(tmp_path: Path):
    http = _CampaignHttp()

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    campaign_dir = tmp_path / f"{EXPIRATION_CAMPAIGN_DIR_PREFIX}test"
    result = _run_campaign(
        tmp_path,
        output_dir=campaign_dir,
        campaign_name="amex-keepalive-comparison",
        trials=["NONE:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        ensure_managed_browser_fn=_browser_ensure(preexisting=False, launched=True),
        browser_cleanup=BROWSER_CLEANUP_LEAVE_OPEN,
    )
    summary = json.loads(
        (campaign_dir / "campaign-summary.json").read_text(encoding="utf-8")
    )
    assert summary["managed_browser_preexisting"] is False
    assert summary["managed_browser_launched_by_campaign"] is True
    assert summary["browser_cleanup_policy"] == BROWSER_CLEANUP_LEAVE_OPEN
    assert summary["managed_cdp_port"] == 9223
    assert summary["managed_profile_path"]
    assert "password" not in json.dumps(summary).lower()
    assert result["zip_path"]


def test_metrics_prefer_structured_text_not_screenshots():
    metrics = derive_expiration_campaign_trial_metrics(
        experiment_result={
            "outcome": "logged_out",
            "recorder": {
                "outcome": "logged_out",
                "logout_detected_at": "2026-01-01T00:10:00+00:00",
                "observations": [
                    {
                        "observed_at": "2026-01-01T00:09:30+00:00",
                        "screenshot_path": "/tmp/ignored.png",
                        "optional_text_searches": [
                            {"term": "expire", "match_count": 2},
                            {"term": "continue", "match_count": 1},
                        ],
                        "browser_inspector": {"candidate_count": 0, "candidates": []},
                    }
                ],
            },
            "keepalive_status": {},
        }
    )
    assert metrics["idle_warning_detected"] is True
    assert metrics["warning_to_logout_seconds"] == 30.0


def test_no_finder_interaction_in_campaign():
    source = inspect.getsource(run_amex_expiration_campaign)
    assert '["open"' not in source
    assert "open_latest_expiration_experiment" not in source
    assert "Finder" not in source


def test_no_duplicate_experiment_orchestration():
    source = inspect.getsource(run_amex_expiration_campaign)
    assert "run_experiment_fn or run_amex_expiration_experiment" in source
    assert "browser-record-expiration" not in source
    assert "/providers/amex/keepalive/start" not in source


def test_no_forced_browser_navigation_or_page_mutation():
    source = inspect.getsource(run_amex_expiration_campaign)
    for banned in (
        "page.click",
        "page.goto",
        "page.reload",
        "page.type",
        "page.fill",
        "page.evaluate",
        "frame.evaluate",
        "perform_keepalive_action",
        "sync_playwright",
        "self.lock",
        "runtime.lock",
    ):
        assert banned not in source


def test_runtime_unavailable(tmp_path: Path, capsys):
    http = _CampaignHttp()
    http.health_raises = URLError("connection refused")
    result = _run_campaign(
        tmp_path,
        output_dir=tmp_path / "down",
        trials=["NONE:30"],
        request_json_fn=http,
        run_experiment_fn=lambda **_k: (_ for _ in ()).throw(AssertionError("no run")),
        ensure_managed_browser_fn=MagicMock(
            side_effect=AssertionError("browser ensure should not run")
        ),
    )
    assert result["outcome"] == "runtime_unavailable"
    print_expiration_campaign_result(result)
    assert EXPIRATION_EXPERIMENT_SERVE_HINT in capsys.readouterr().err


def test_print_campaign_result_only(tmp_path: Path, capsys):
    print_expiration_campaign_result(
        {
            "campaign_name": "amex-keepalive-comparison",
            "outcome": "completed",
            "zip_path": str(tmp_path / "campaign.zip"),
            "trial_summaries": [{"error": None, "skipped": False}],
            "interrupted": False,
        }
    )
    out = capsys.readouterr().out
    assert "Campaign: amex-keepalive-comparison" in out
    assert "Evidence ZIP:" in out


def test_create_campaign_zip_helper(tmp_path: Path):
    campaign = tmp_path / "amex-expiration-campaign-zip"
    (campaign / "trials" / "001-none-30s").mkdir(parents=True)
    (campaign / "campaign-summary.json").write_text("{}", encoding="utf-8")
    (campaign / "trials" / "001-none-30s" / "experiment-summary.json").write_text(
        "{}", encoding="utf-8"
    )
    zip_path = create_expiration_campaign_zip(campaign)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "campaign-summary.json" in names


def test_campaign_reuses_experiment_function():
    assert callable(run_amex_expiration_experiment)
    source = inspect.getsource(run_amex_expiration_campaign)
    assert "run_experiment_fn or run_amex_expiration_experiment" in source


def test_all_keepalive_strategies_accepted_in_trial_specs():
    specs = [f"{strategy}:30" for strategy in KEEPALIVE_STRATEGIES]
    parsed = parse_expiration_campaign_trial_specs(specs)
    assert [item["strategy"] for item in parsed] == list(KEEPALIVE_STRATEGIES)
