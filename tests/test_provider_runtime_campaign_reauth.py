"""Regression tests for repeated campaign reauthentication."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from mighty.provider_runtime import (
    DEFAULT_AMEX_CAMPAIGN_TRIALS,
    MANAGED_BROWSER_HEALTHY,
    BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    ensure_expiration_campaign_signed_in,
    parse_args,
    run_amex_expiration_campaign,
    run_amex_provider_campaign,
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


class _Http:
    def __init__(self, verify_states: list[str]) -> None:
        self.verify_states = list(verify_states)
        self._verify_index = 0
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, payload=None, *, timeout: float = 60):
        from urllib.parse import urlsplit

        path = urlsplit(url).path or url
        self.calls.append((method, path))
        if path == "/health":
            return {"ok": True}
        if path == "/providers/amex/verify":
            state = self.verify_states[
                min(self._verify_index, len(self.verify_states) - 1)
            ]
            self._verify_index += 1
            return _verify_payload(state)
        raise AssertionError(f"unexpected {method} {path}")


def _fake_experiment(**kwargs):
    output_dir = Path(kwargs["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "recorder").mkdir(parents=True, exist_ok=True)
    (output_dir / "experiment-summary.json").write_text("{}", encoding="utf-8")
    return {
        "ok": True,
        "outcome": "logged_out",
        "keepalive_outcome": "logged_out",
        "final_authentication_state": "SIGNED_OUT",
        "experiment_dir": str(output_dir),
        "zip_path": str(output_dir / "trial.zip"),
        "summary": {
            "outcome": "logged_out",
            "recorder_outcome": "logged_out",
            "keepalive_outcome": "logged_out",
            "keepalive_strategy": kwargs["strategy"],
            "keepalive_interval_seconds": kwargs["keepalive_interval_seconds"],
            "keepalive_wait_seconds": 1.0,
            "keepalive_completion_timeout": False,
            "final_authentication_state": "SIGNED_OUT",
        },
        "recorder": {
            "outcome": "logged_out",
            "logout_detected_at": "2026-01-01T00:10:00+00:00",
            "initial_canonical_authentication_state": "SIGNED_IN",
            "final_canonical_authentication_state": "SIGNED_OUT",
            "observations": [],
        },
        "keepalive_status": {
            "ok": True,
            "keepalive_logged_out": True,
            "keepalive_expiration_dialog_seen": False,
            "keepalive_events": [],
        },
        "keepalive_wait_seconds": 1.0,
        "keepalive_completion_timeout": False,
        "exit_code": 0,
    }


def _browser_ensure(**_kwargs):
    return {
        "ok": True,
        "state": MANAGED_BROWSER_HEALTHY,
        "cdp_url": "http://127.0.0.1:9223",
        "managed_browser_preexisting": True,
        "managed_browser_launched_by_campaign": False,
        "managed_browser_restarted_by_campaign": False,
        "managed_cdp_port": 9223,
        "managed_profile_path": "/tmp/amex",
    }


def _run(tmp_path: Path, *, verify_states: list[str], trials: list[str], input_fn, print_lines=None):
    http = _Http(verify_states)
    lines = print_lines if print_lines is not None else []
    stop = MagicMock()

    def emit(msg="", **_k):
        lines.append(str(msg))

    result = run_amex_provider_campaign(
        root=tmp_path / "runtime",
        output_dir=tmp_path / "campaign",
        trials=trials,
        request_json_fn=http,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": False,
            "runtime_started_by_campaign": True,
            "process": MagicMock(),
        },
        stop_runtime_fn=stop,
        run_campaign_fn=None,
        ensure_managed_browser_fn=_browser_ensure,
        classify_managed_browser_fn=lambda: {
            "state": MANAGED_BROWSER_HEALTHY,
            "cdp_url": "http://127.0.0.1:9223",
            "page_target_count": 1,
        },
        close_managed_browser_fn=lambda **_k: {
            "closed": False,
            "reason": "test",
            "browser_cleanup_policy": BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        },
        bring_to_foreground_fn=lambda: {"ok": True},
        run_experiment_fn=_fake_experiment,
        sleep_fn=lambda _s: None,
        input_fn=input_fn,
        print_fn=emit,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    )
    return result, http, stop, lines


def test_regression_two_consecutive_reauth_cycles_then_trial_3(tmp_path: Path):
    """Trial1 -> auth -> Trial2 -> auth -> Trial3 starts successfully."""
    # verify sequence:
    # trial1 preflight SIGNED_IN
    # trial2 preflight SIGNED_OUT, after Enter SIGNED_IN
    # trial3 preflight SIGNED_OUT, after Enter SIGNED_IN
    verify_states = [
        "SIGNED_IN",
        "SIGNED_OUT",
        "SIGNED_IN",
        "SIGNED_OUT",
        "SIGNED_IN",
    ]
    prompts: list[str] = []
    lines: list[str] = []

    def input_fn() -> str:
        prompts.append("enter")
        return ""

    result, _http, stop, lines = _run(
        tmp_path,
        verify_states=verify_states,
        trials=["NONE:30", "SESSION_API:30", "SESSION_API:5"],
        input_fn=input_fn,
        print_lines=lines,
    )
    joined = "\n".join(lines)
    assert result["outcome"] == "completed"
    assert len(result["trial_summaries"]) == 3
    assert all(row.get("error") is None for row in result["trial_summaries"])
    assert [row["strategy"] for row in result["trial_summaries"]] == [
        "NONE",
        "SESSION_API",
        "SESSION_API",
    ]
    assert prompts == ["enter", "enter"]
    assert joined.count("Authentication required for trial 2.") == 1
    assert joined.count("Authentication required for trial 3.") == 1
    assert joined.count("Input received.") == 2
    assert joined.count("Verifying authentication...") == 2
    assert joined.count("Authentication verified.") == 2
    assert "Starting trial 3 of 3: SESSION_API at 5 seconds..." in joined
    assert "Trial 1 completed: logged_out" in joined
    assert "Trial 2 completed: logged_out" in joined
    # Owned serve stops only after true completion, not during auth pauses.
    assert result["runtime_stopped_by_campaign"] is True
    stop.assert_called_once()


def test_authentication_required_for_every_trial(tmp_path: Path):
    verify_states = [
        "SIGNED_OUT",
        "SIGNED_IN",
        "SIGNED_OUT",
        "SIGNED_IN",
        "SIGNED_OUT",
        "SIGNED_IN",
    ]
    prompts: list[str] = []

    result, _http, _stop, lines = _run(
        tmp_path,
        verify_states=verify_states,
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        input_fn=lambda: prompts.append("e") or "",
    )
    assert result["outcome"] == "completed"
    assert len(prompts) == 3
    assert len(result["trial_summaries"]) == 3
    assert all(row.get("status") == "completed" for row in result["trial_summaries"])


def test_second_auth_prompt_calls_input_again(tmp_path: Path):
    verify_states = ["SIGNED_IN", "SIGNED_OUT", "SIGNED_IN", "SIGNED_OUT", "SIGNED_IN"]
    calls = {"n": 0}

    def input_fn() -> str:
        calls["n"] += 1
        return ""

    result, _http, _stop, _lines = _run(
        tmp_path,
        verify_states=verify_states,
        trials=["NONE:30", "SESSION_API:30", "SESSION_API:5"],
        input_fn=input_fn,
    )
    assert calls["n"] == 2
    assert result["outcome"] == "completed"


def test_no_trial_failure_before_user_authentication_attempt(tmp_path: Path):
    http = _Http(["SIGNED_OUT", "SIGNED_IN"])
    seen_before_input = {"failed_rows": None}

    def input_fn() -> str:
        # During the auth pause, campaign must not have recorded a failed trial.
        seen_before_input["failed_rows"] = "pending"
        return ""

    result = run_amex_expiration_campaign(
        root=tmp_path / "runtime",
        output_dir=tmp_path / "camp",
        trials=["NONE:30"],
        request_json_fn=http,
        ensure_managed_browser_fn=_browser_ensure,
        classify_managed_browser_fn=lambda: {
            "state": MANAGED_BROWSER_HEALTHY,
            "page_target_count": 1,
        },
        close_managed_browser_fn=lambda **_k: {"closed": False},
        bring_to_foreground_fn=lambda: {"ok": True},
        run_experiment_fn=_fake_experiment,
        sleep_fn=lambda _s: None,
        input_fn=input_fn,
        print_fn=lambda *_a, **_k: None,
    )
    assert seen_before_input["failed_rows"] == "pending"
    assert result["outcome"] == "completed"
    assert result["trial_summaries"][0]["error"] is None


def test_failed_verification_prompts_again_without_failing_trial():
    http = _Http(["SIGNED_OUT", "SIGNED_OUT", "SIGNED_OUT", "SIGNED_IN"])
    prompts: list[int] = []
    lines: list[str] = []

    auth = ensure_expiration_campaign_signed_in(
        trial_number=3,
        base_url="http://127.0.0.1:8765",
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        input_fn=lambda: prompts.append(1) or "",
        print_fn=lambda msg="", **_k: lines.append(str(msg)),
    )
    assert auth["ok"] is True
    assert len(prompts) == 3
    assert "Authentication was not verified:" in "\n".join(lines)
    assert auth.get("outcome") != "authentication_reverify_failed"


def test_ctrl_c_during_second_auth_prompt_is_interrupted(tmp_path: Path):
    verify_states = ["SIGNED_IN", "SIGNED_OUT", "SIGNED_IN", "SIGNED_OUT"]
    prompts = {"n": 0}

    def input_fn() -> str:
        prompts["n"] += 1
        if prompts["n"] >= 2:
            raise KeyboardInterrupt
        return ""

    result, _http, stop, lines = _run(
        tmp_path,
        verify_states=verify_states,
        trials=["NONE:30", "SESSION_API:30", "SESSION_API:5"],
        input_fn=input_fn,
    )
    assert result["interrupted"] is True or result["outcome"] == "interrupted"
    assert result["exit_code"] == 130
    # Only completed trials recorded; pending trial 3 not marked failed.
    assert len(result["trial_summaries"]) == 2
    assert all(row.get("status") == "completed" for row in result["trial_summaries"])
    assert "authentication_reverify_failed" not in "\n".join(lines)
    assert result["runtime_stopped_by_campaign"] is True
    stop.assert_called_once()


def test_resume_skips_completed_and_starts_pending_trial(tmp_path: Path):
    campaign_dir = tmp_path / "amex-expiration-campaign-resume"
    # Seed a prior run with trials 1-2 completed and trial 3 failed/auth.
    first_http = _Http(["SIGNED_IN", "SIGNED_OUT", "SIGNED_IN"])
    prompts1: list[str] = []
    first = run_amex_expiration_campaign(
        root=tmp_path / "runtime",
        output_dir=campaign_dir,
        trials=["NONE:30", "SESSION_API:30"],
        request_json_fn=first_http,
        ensure_managed_browser_fn=_browser_ensure,
        classify_managed_browser_fn=lambda: {
            "state": MANAGED_BROWSER_HEALTHY,
            "page_target_count": 1,
        },
        close_managed_browser_fn=lambda **_k: {"closed": False},
        bring_to_foreground_fn=lambda: {"ok": True},
        run_experiment_fn=_fake_experiment,
        sleep_fn=lambda _s: None,
        input_fn=lambda: prompts1.append("e") or "",
        print_fn=lambda *_a, **_k: None,
    )
    assert first["outcome"] == "completed"
    assert len(first["trial_summaries"]) == 2

    second_calls: list[str] = []

    def run_second(**kwargs):
        second_calls.append(str(kwargs["strategy"]))
        return _fake_experiment(**kwargs)

    second_http = _Http(["SIGNED_IN"])
    second = run_amex_provider_campaign(
        root=tmp_path / "runtime",
        trials=["NONE:30", "SESSION_API:30", "SESSION_API:5"],
        resume_dir=campaign_dir,
        request_json_fn=second_http,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        },
        stop_runtime_fn=MagicMock(),
        ensure_managed_browser_fn=_browser_ensure,
        classify_managed_browser_fn=lambda: {
            "state": MANAGED_BROWSER_HEALTHY,
            "page_target_count": 1,
        },
        close_managed_browser_fn=lambda **_k: {"closed": False},
        bring_to_foreground_fn=lambda: {"ok": True},
        run_experiment_fn=run_second,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
        print_fn=lambda *_a, **_k: None,
    )
    assert second_calls == ["SESSION_API"]
    assert second["trial_summaries"][0]["skipped"] is True
    assert second["trial_summaries"][1]["skipped"] is True
    assert second["trial_summaries"][2]["strategy"] == "SESSION_API"
    assert second["trial_summaries"][2]["keepalive_interval_seconds"] == 5
    assert second["trial_summaries"][2].get("skipped") is not True


def test_cli_resume_flag():
    with patch(
        "sys.argv",
        [
            "provider_runtime.py",
            "campaign",
            "amex",
            "--resume",
            "/tmp/amex-expiration-campaign-x",
        ],
    ):
        args = parse_args()
    assert args.command == "campaign"
    assert str(args.resume).endswith("amex-expiration-campaign-x")


def test_owned_serve_remains_running_marker_during_auth_loop():
    """Auth loop lives inside campaign; top-level stop runs only afterward."""
    import inspect

    source = inspect.getsource(run_amex_provider_campaign)
    assert "ensure_expiration_campaign_signed_in" in inspect.getsource(
        run_amex_expiration_campaign
    )
    assert "Stopping Provider Runtime started by this campaign" in source
    # Stop is in finally after run_campaign returns, not around auth prompts.
    assert source.index("run_campaign(") < source.index(
        "Stopping Provider Runtime started by this campaign"
    )


def test_default_trials_still_expand():
    assert DEFAULT_AMEX_CAMPAIGN_TRIALS[2] == "SESSION_API:5"
