"""Tests for the Amex expiration campaign runner."""

from __future__ import annotations

import csv
import inspect
import json
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest

from mighty.provider_runtime import (
    EXPIRATION_CAMPAIGN_DIR_PREFIX,
    EXPIRATION_EXPERIMENT_SERVE_HINT,
    KEEPALIVE_STRATEGIES,
    create_expiration_campaign_zip,
    derive_expiration_campaign_trial_metrics,
    ensure_expiration_campaign_signed_in,
    expiration_campaign_trial_dirname,
    parse_args,
    parse_expiration_campaign_trial_spec,
    parse_expiration_campaign_trial_specs,
    print_expiration_campaign_result,
    run_amex_expiration_campaign,
    run_amex_expiration_experiment,
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


def test_parse_trial_spec_valid():
    parsed = parse_expiration_campaign_trial_spec("SESSION_API:30")
    assert parsed["strategy"] == "SESSION_API"
    assert parsed["keepalive_interval_seconds"] == 30
    assert parsed["spec"] == "SESSION_API:30"


def test_parse_trial_spec_invalid_strategy():
    with pytest.raises(ValueError, match="Unsupported keepalive strategy"):
        parse_expiration_campaign_trial_spec("NOT_A_STRATEGY:30")


def test_parse_trial_spec_invalid_interval():
    with pytest.raises(ValueError, match="Invalid keepalive interval"):
        parse_expiration_campaign_trial_spec("NONE:abc")
    with pytest.raises(ValueError, match="Invalid keepalive interval"):
        parse_expiration_campaign_trial_spec("NONE:0")
    with pytest.raises(ValueError, match="Invalid trial specification"):
        parse_expiration_campaign_trial_spec("NONE")


def test_parse_all_trials_before_start():
    specs = parse_expiration_campaign_trial_specs(
        ["NONE:30", "SESSION_API:5", "OVERVIEW_RELOAD:30"]
    )
    assert [item["strategy"] for item in specs] == [
        "NONE",
        "SESSION_API",
        "OVERVIEW_RELOAD",
    ]
    with pytest.raises(ValueError, match="Unsupported"):
        parse_expiration_campaign_trial_specs(["NONE:30", "BAD:1"])


def test_trial_dirname_format():
    assert expiration_campaign_trial_dirname(1, "NONE", 30) == "001-none-30s"
    assert (
        expiration_campaign_trial_dirname(2, "SESSION_API", 5) == "002-session-api-5s"
    )


def test_cli_parses_repeatable_trials():
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
            "--continue-on-error",
            "--skip-completed",
        ],
    ):
        args = parse_args()
    assert args.command == "browser-run-expiration-campaign"
    assert args.trials == ["NONE:30", "SESSION_API:5"]
    assert args.campaign_name == "amex-keepalive-comparison"
    assert args.continue_on_error is True
    assert args.skip_completed is True


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

    result = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=tmp_path / "campaign",
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
    )
    assert calls == [("NONE", 30), ("SESSION_API", 30), ("PAGE_ACTIVITY", 30)]
    assert result["outcome"] == "completed"
    assert len(result["trial_summaries"]) == 3
    assert (tmp_path / "campaign" / "trials" / "001-none-30s").is_dir()
    assert (tmp_path / "campaign" / "trials" / "002-session-api-30s").is_dir()
    assert (tmp_path / "campaign" / "trials" / "003-page-activity-30s").is_dir()


def test_authentication_pause_between_trials(tmp_path: Path, capsys):
    http = _CampaignHttp(verify_states=["SIGNED_IN", "SIGNED_OUT", "SIGNED_IN"])
    prompts: list[str] = []

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=tmp_path / "auth-pause",
        trials=["NONE:30", "SESSION_API:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        sleep_fn=lambda _s: None,
        input_fn=lambda: prompts.append("enter") or "",
    )
    out = capsys.readouterr().out
    assert "Authentication required for trial 2." in out
    assert "Sign in in the managed Amex window and complete MFA." in out
    assert "Press Enter when the overview page is visible." in out
    assert "provider_runtime.py stop" in out
    assert "provider_runtime.py bootstrap amex" in out
    assert "provider_runtime.py serve" in out
    assert prompts == ["enter"]
    assert result["outcome"] == "completed"
    assert len(result["trial_summaries"]) == 2


def test_successful_reverification_after_user_confirmation(tmp_path: Path):
    http = _CampaignHttp(verify_states=["SIGNED_OUT", "SIGNED_IN"])
    auth = ensure_expiration_campaign_signed_in(
        trial_number=1,
        base_url="http://127.0.0.1:8765",
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
        print_fn=lambda *_a, **_k: None,
    )
    assert auth["ok"] is True
    assert auth["paused"] is True
    assert auth["authentication_state"] == "SIGNED_IN"


def test_failed_reverification_after_user_confirmation(tmp_path: Path):
    http = _CampaignHttp(verify_states=["SIGNED_OUT", "SIGNED_OUT"])
    auth = ensure_expiration_campaign_signed_in(
        trial_number=3,
        base_url="http://127.0.0.1:8765",
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
        print_fn=lambda *_a, **_k: None,
    )
    assert auth["ok"] is False
    assert auth["paused"] is True
    assert auth["outcome"] == "authentication_reverify_failed"


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

    result = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=tmp_path / "continue",
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        continue_on_error=True,
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
    )
    assert calls == ["NONE", "SESSION_API", "PAGE_ACTIVITY"]
    assert result["trial_summaries"][1]["error"] == "boom"
    assert result["trial_summaries"][2]["error"] is None
    assert result["exit_code"] == 1


def test_ctrl_c_partial_campaign(tmp_path: Path):
    http = _CampaignHttp()
    calls: list[str] = []

    def run_experiment(**kwargs):
        strategy = str(kwargs["strategy"])
        calls.append(strategy)
        if strategy == "SESSION_API":
            raise KeyboardInterrupt
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=strategy,
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=tmp_path / "partial",
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
    )
    assert calls == ["NONE", "SESSION_API"]
    assert result["interrupted"] is True
    assert result["exit_code"] == 130
    assert Path(result["zip_path"]).is_file()
    assert (tmp_path / "partial" / "campaign-summary.json").is_file()
    assert (tmp_path / "partial" / "campaign-summary.csv").is_file()
    assert (tmp_path / "partial" / "campaign-report.md").is_file()
    assert len(result["trial_summaries"]) == 2
    assert result["trial_summaries"][1]["status"] == "partial"


def test_skip_completed_resume(tmp_path: Path):
    http = _CampaignHttp()
    campaign_dir = tmp_path / "resume"
    first_calls: list[str] = []

    def run_first(**kwargs):
        strategy = str(kwargs["strategy"])
        first_calls.append(strategy)
        if strategy == "SESSION_API":
            raise KeyboardInterrupt
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=strategy,
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    first = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=campaign_dir,
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=run_first,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
    )
    assert first["interrupted"] is True
    assert first_calls == ["NONE", "SESSION_API"]

    # Mark the first trial completed in the manifest (as a finished trial).
    manifest_path = campaign_dir / "campaign-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["trials"]:
        if row["strategy"] == "NONE":
            row["status"] = "completed"
            row["error"] = None
    # Drop the interrupted partial so resume re-runs remaining trials.
    manifest["trials"] = [row for row in manifest["trials"] if row["status"] == "completed"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    second_calls: list[str] = []

    def run_second(**kwargs):
        strategy = str(kwargs["strategy"])
        second_calls.append(strategy)
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=strategy,
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    second = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=campaign_dir,
        trials=["NONE:30", "SESSION_API:30", "PAGE_ACTIVITY:30"],
        skip_completed=True,
        request_json_fn=http,
        run_experiment_fn=run_second,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
    )
    assert second_calls == ["SESSION_API", "PAGE_ACTIVITY"]
    assert second["trial_summaries"][0]["skipped"] is True
    assert second["trial_summaries"][0]["strategy"] == "NONE"
    assert second["outcome"] == "completed"


def test_one_zip_contains_all_trial_directories(tmp_path: Path):
    http = _CampaignHttp()

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    result = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=tmp_path / "zipcamp",
        trials=["NONE:30", "SESSION_API:5"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
    )
    zip_path = Path(result["zip_path"])
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "campaign-summary.json" in names
    assert "campaign-summary.csv" in names
    assert "campaign-report.md" in names
    assert any(name.startswith("trials/001-none-30s/") for name in names)
    assert any(name.startswith("trials/002-session-api-5s/") for name in names)
    assert zip_path.name not in names


def test_summary_json_csv_markdown(tmp_path: Path):
    http = _CampaignHttp()

    def run_experiment(**kwargs):
        return _fake_experiment_result(
            output_dir=Path(kwargs["output_dir"]),
            strategy=str(kwargs["strategy"]),
            interval=int(kwargs["keepalive_interval_seconds"]),
        )

    campaign_dir = tmp_path / f"{EXPIRATION_CAMPAIGN_DIR_PREFIX}test"
    result = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=campaign_dir,
        campaign_name="amex-keepalive-comparison",
        trials=["NONE:30", "OVERVIEW_RELOAD:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
    )
    summary = json.loads(
        (campaign_dir / "campaign-summary.json").read_text(encoding="utf-8")
    )
    assert summary["campaign_name"] == "amex-keepalive-comparison"
    assert len(summary["trials"]) == 2
    row = summary["trials"][0]
    for key in (
        "trial_number",
        "strategy",
        "keepalive_interval_seconds",
        "started_at",
        "completed_at",
        "duration_seconds",
        "recorder_outcome",
        "keepalive_outcome",
        "initial_authentication_state",
        "final_authentication_state",
        "idle_warning_detected",
        "idle_warning_first_observed_at",
        "logged_out",
        "logout_observed_at",
        "warning_to_logout_seconds",
        "keepalive_wait_seconds",
        "keepalive_completion_timeout",
        "error",
        "evidence_directory",
    ):
        assert key in row
    assert row["idle_warning_detected"] is True
    assert row["logged_out"] is True
    assert row["warning_to_logout_seconds"] == 60.0

    with (campaign_dir / "campaign-summary.csv").open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        csv_rows = list(reader)
    assert len(csv_rows) == 2
    assert csv_rows[0]["strategy"] == "NONE"

    report = (campaign_dir / "campaign-report.md").read_text(encoding="utf-8")
    assert "amex-keepalive-comparison" in report
    assert "NONE" in report
    assert result["zip_path"]


def test_metrics_prefer_structured_text_not_screenshots():
    metrics = derive_expiration_campaign_trial_metrics(
        experiment_result={
            "outcome": "logged_out",
            "recorder": {
                "outcome": "logged_out",
                "logout_detected_at": "2026-01-01T00:10:00+00:00",
                "initial_canonical_authentication_state": "SIGNED_IN",
                "final_canonical_authentication_state": "SIGNED_OUT",
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
    assert metrics["idle_warning_first_observed_at"] == "2026-01-01T00:09:30+00:00"
    assert metrics["warning_to_logout_seconds"] == 30.0


def test_no_finder_interaction_in_campaign():
    source = inspect.getsource(run_amex_expiration_campaign)
    assert "subprocess" not in source
    assert '["open"' not in source
    assert "open_latest_expiration_experiment" not in source
    assert "Finder" not in source


def test_no_duplicate_experiment_orchestration():
    source = inspect.getsource(run_amex_expiration_campaign)
    assert "run_amex_expiration_experiment" in source or "run_experiment" in source
    assert "browser-record-expiration" not in source
    assert "/providers/amex/keepalive/start" not in source
    assert "create_expiration_experiment_zip" not in source


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
    result = run_amex_expiration_campaign(
        diagnostics_dir=tmp_path,
        output_dir=tmp_path / "down",
        trials=["NONE:30"],
        request_json_fn=http,
        run_experiment_fn=lambda **_k: (_ for _ in ()).throw(AssertionError("no run")),
        sleep_fn=lambda _s: None,
        input_fn=lambda: "",
    )
    assert result["outcome"] == "runtime_unavailable"
    print_expiration_campaign_result(result)
    assert EXPIRATION_EXPERIMENT_SERVE_HINT in capsys.readouterr().err


def test_print_campaign_result_only(tmp_path: Path, capsys):
    result = {
        "campaign_name": "amex-keepalive-comparison",
        "outcome": "completed",
        "zip_path": str(tmp_path / "campaign.zip"),
        "trial_summaries": [
            {"error": None, "skipped": False},
            {"error": None, "skipped": False},
        ],
        "interrupted": False,
    }
    print_expiration_campaign_result(result)
    out = capsys.readouterr().out
    assert "Campaign: amex-keepalive-comparison" in out
    assert "Evidence ZIP:" in out
    assert str(tmp_path / "campaign.zip") in out
    assert "Strategy:" not in out
    assert "Waiting for keepalive convergence" not in out


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
    assert "trials/001-none-30s/experiment-summary.json" in names


def test_campaign_reuses_experiment_function():
    assert callable(run_amex_expiration_experiment)
    source = inspect.getsource(run_amex_expiration_campaign)
    assert "run_experiment_fn or run_amex_expiration_experiment" in source


def test_all_keepalive_strategies_accepted_in_trial_specs():
    specs = [f"{strategy}:30" for strategy in KEEPALIVE_STRATEGIES]
    parsed = parse_expiration_campaign_trial_specs(specs)
    assert [item["strategy"] for item in parsed] == list(KEEPALIVE_STRATEGIES)
