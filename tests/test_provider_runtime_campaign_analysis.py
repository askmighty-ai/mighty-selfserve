"""Tests for offline Amex expiration campaign analysis."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mighty.provider_runtime import (
    _maybe_analyze_campaign_after_run,
    parse_args,
    run_client_command,
    sanitize_keepalive_attempt,
    write_keepalive_attempts_jsonl,
)
from mighty.provider_runtime_campaign_analysis import (
    RESULT_BASELINE,
    RESULT_EFFECTIVE_CANDIDATE,
    RESULT_INCONCLUSIVE,
    RESULT_INEFFECTIVE,
    RESULT_OPERATIONALLY_FAILED,
    RESULT_PARTIALLY_EFFECTIVE,
    analyze_campaign_directory,
    analyze_campaign_path,
    analyze_campaign_zip,
    analyze_trial_directory,
    apply_baseline_comparisons,
    classify_trial_result,
    detect_idle_warning,
    detect_logout_events,
    finalize_trial_classifications,
    load_keepalive_attempts,
    observation_contains_idle_warning,
    recommend_campaign_next_step,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _observation(
    *,
    observed_at: str,
    canonical: str = "SIGNED_IN",
    browser: str = "SIGNED_IN",
    login_url: bool = False,
    ax: str = "",
    dom: str = "",
    title: str = "Overview",
    verified: bool = False,
    searches: list[dict] | None = None,
) -> dict:
    return {
        "observed_at": observed_at,
        "browser_observation_authentication_state": browser,
        "canonical_authentication_state": canonical,
        "canonical_verified_this_poll": verified,
        "login_url_detected": login_url,
        "selected_page_url": (
            "https://www.americanexpress.com/en-us/account/login"
            if login_url
            else "https://global.americanexpress.com/overview"
        ),
        "selected_page_title": title,
        "accessibility_text_summary": ax,
        "dom_text_summary": dom,
        "optional_text_searches": searches or [],
        "browser_inspector": {"candidates": []},
        "collection_errors": [],
    }


def _build_trial(
    trial_dir: Path,
    *,
    strategy: str,
    interval: int,
    keepalive_outcome: str,
    recorder_outcome: str,
    final_canonical: str,
    started_at: str,
    warning_at: str | None,
    logout_at: str | None,
    action_success: int = 0,
    action_failure: int = 0,
    attempts: list[dict] | None = None,
    events: list[dict] | None = None,
    include_attempt_file: bool = False,
    keepalive_completed_at: str | None = None,
    trial_duration_seconds: int = 600,
) -> None:
    trial_dir.mkdir(parents=True, exist_ok=True)
    action_count = action_success + action_failure
    observations = [
        _observation(
            observed_at=started_at,
            canonical="SIGNED_IN",
            browser="SIGNED_IN",
            verified=True,
        )
    ]
    if warning_at:
        observations.append(
            _observation(
                observed_at=warning_at,
                ax=(
                    "for your security, this session will expire in 00:59 "
                    "due to inactivity"
                ),
                dom="session will expire due to inactivity",
                searches=[
                    {"term": "expire", "match_count": 1},
                    {"term": "continue", "match_count": 1},
                ],
            )
        )
    if logout_at:
        observations.append(
            _observation(
                observed_at=logout_at,
                canonical="SIGNED_OUT",
                browser="SIGNED_OUT",
                login_url=True,
                title="Log In",
                ax="we logged you out automatically",
                verified=True,
            )
        )
    completed_at = logout_at or keepalive_completed_at or started_at
    recorder = {
        "started_at": started_at,
        "completed_at": completed_at,
        "outcome": recorder_outcome,
        "logout_detected_at": logout_at,
        "initial_canonical_authentication_state": "SIGNED_IN",
        "final_canonical_authentication_state": final_canonical,
        "observations": observations,
    }
    _write_json(trial_dir / "recorder" / "recording.json", recorder)

    if events is None:
        events = [
            {
                "timestamp": started_at,
                "event_type": "trial_started",
                "strategy": strategy,
                "action_result": None,
                "authentication_state": "SIGNED_IN",
            }
        ]
        for idx in range(action_count):
            events.append(
                {
                    "timestamp": started_at,
                    "event_type": "action",
                    "strategy": strategy,
                    "action_result": "success" if idx < action_success else "failure",
                    "authentication_state": "SIGNED_IN",
                }
            )

    keepalive_status = {
        "ok": True,
        "keepalive_strategy": strategy,
        "keepalive_started_at": started_at,
        "keepalive_completed_at": keepalive_completed_at or completed_at,
        "keepalive_duration_seconds": trial_duration_seconds,
        "keepalive_interval_seconds": interval,
        "keepalive_action_count": action_count,
        "keepalive_action_success_count": action_success,
        "keepalive_action_failure_count": action_failure,
        "keepalive_logged_out": recorder_outcome == "logged_out"
        and keepalive_outcome == "logged_out",
        "keepalive_final_authentication_state": (
            "SIGNED_IN" if keepalive_outcome == "duration_completed" else final_canonical
        ),
        "keepalive_final_reason": keepalive_outcome,
        "keepalive_events": events,
    }
    _write_json(trial_dir / "keepalive-status.json", keepalive_status)

    if include_attempt_file and attempts is not None:
        write_keepalive_attempts_jsonl(trial_dir / "keepalive-attempts.jsonl", attempts)
    elif attempts is not None:
        keepalive_status["keepalive_attempts"] = attempts
        _write_json(trial_dir / "keepalive-status.json", keepalive_status)

    summary = {
        "provider": "amex",
        "outcome": recorder_outcome,
        "keepalive_outcome": keepalive_outcome,
        "final_authentication_state": final_canonical,
        "started_at": started_at,
        "completed_at": completed_at,
        "recorder_completed_at": completed_at,
        "keepalive_completed_at": keepalive_completed_at or completed_at,
        "keepalive_strategy": strategy,
        "trial_duration_seconds": trial_duration_seconds,
        "keepalive_interval_seconds": interval,
        "experiment_duration_seconds": 300.0,
        "recorder_duration_seconds": 290.0,
        "recorder_outcome": recorder_outcome,
    }
    _write_json(trial_dir / "experiment-summary.json", summary)
    _write_json(trial_dir / "runtime-status.json", {"ok": True})


def _build_campaign(campaign_dir: Path) -> Path:
    campaign_dir.mkdir(parents=True, exist_ok=True)
    trials = campaign_dir / "trials"
    _build_trial(
        trials / "001-none-30s",
        strategy="NONE",
        interval=30,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T17:33:19+00:00",
        warning_at="2026-07-19T17:37:19+00:00",
        logout_at="2026-07-19T17:38:23+00:00",
    )
    _build_trial(
        trials / "002-session-api-30s",
        strategy="SESSION_API",
        interval=30,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T17:45:31+00:00",
        warning_at="2026-07-19T17:49:27+00:00",
        logout_at="2026-07-19T17:50:33+00:00",
        action_failure=9,
    )
    _build_trial(
        trials / "003-session-api-5s",
        strategy="SESSION_API",
        interval=5,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T18:46:31+00:00",
        warning_at="2026-07-19T18:50:34+00:00",
        logout_at="2026-07-19T18:51:38+00:00",
        action_failure=31,
    )
    _build_trial(
        trials / "004-page-activity-30s",
        strategy="PAGE_ACTIVITY",
        interval=30,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T18:52:40+00:00",
        warning_at="2026-07-19T18:56:40+00:00",
        logout_at="2026-07-19T18:57:45+00:00",
        action_failure=9,
    )
    # OVERVIEW_RELOAD contradiction fixture
    _build_trial(
        trials / "005-overview-reload-30s",
        strategy="OVERVIEW_RELOAD",
        interval=30,
        keepalive_outcome="duration_completed",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T18:58:55+00:00",
        warning_at="2026-07-19T19:13:06+00:00",
        logout_at="2026-07-19T19:14:03+00:00",
        action_success=16,
        keepalive_completed_at="2026-07-19T19:09:03+00:00",
        trial_duration_seconds=600,
    )
    _write_json(
        campaign_dir / "campaign-summary.json",
        {
            "provider": "amex",
            "campaign_name": "amex-keepalive-comparison",
            "trial_count": 5,
            "trials": [],
        },
    )
    return campaign_dir


def test_analyze_campaign_directory(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    analysis = analyze_campaign_directory(campaign_dir)
    assert analysis["ok"] is True
    assert len(analysis["trials"]) == 5
    assert (campaign_dir / "campaign-analysis.json").is_file()
    assert (campaign_dir / "campaign-analysis.csv").is_file()
    assert (campaign_dir / "campaign-analysis.md").is_file()
    md = (campaign_dir / "campaign-analysis.md").read_text(encoding="utf-8")
    assert "# Amex Expiration Campaign Analysis" in md
    assert "## Executive conclusion" in md


def test_analyze_campaign_zip(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    zip_path = tmp_path / "campaign.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in campaign_dir.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(campaign_dir)))
    before = zip_path.read_bytes()
    analysis = analyze_campaign_zip(zip_path)
    assert analysis["ok"] is True
    assert zip_path.read_bytes() == before
    assert (zip_path.parent / "campaign-analysis.md").is_file()


def test_none_baseline_selection(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    analysis = analyze_campaign_directory(campaign_dir, write_outputs=False)
    assert analysis["baseline_available"] is True
    assert analysis["baseline_trial"]["strategy"] == "NONE"
    none = next(t for t in analysis["trials"] if t["strategy"] == "NONE")
    assert none["result_classification"] == RESULT_BASELINE


def test_warning_phrase_detection_from_dom_and_accessibility():
    dom_obs = {
        "dom_text_summary": "For your security, this session will expire soon",
        "accessibility_text_summary": "",
        "optional_text_searches": [],
        "browser_inspector": {},
    }
    ax_obs = {
        "dom_text_summary": "",
        "accessibility_text_summary": "session will expire due to inactivity",
        "optional_text_searches": [],
        "browser_inspector": {},
    }
    assert observation_contains_idle_warning(dom_obs) is True
    assert observation_contains_idle_warning(ax_obs) is True


def test_canonical_logout_detection():
    recorder = {
        "initial_canonical_authentication_state": "SIGNED_IN",
        "observations": [
            _observation(observed_at="2026-01-01T00:00:00+00:00", canonical="SIGNED_IN"),
            _observation(
                observed_at="2026-01-01T00:05:00+00:00",
                canonical="SIGNED_OUT",
                browser="SIGNED_IN",
            ),
        ],
    }
    info = detect_logout_events(recorder)
    assert info["canonical_logout_at"] == "2026-01-01T00:05:00+00:00"
    assert info["logout_source"] == "canonical_SIGNED_OUT"


def test_browser_logout_fallback():
    recorder = {
        "initial_canonical_authentication_state": "SIGNED_IN",
        "observations": [
            _observation(
                observed_at="2026-01-01T00:00:00+00:00",
                canonical="SIGNED_IN",
                browser="SIGNED_IN",
            ),
            _observation(
                observed_at="2026-01-01T00:05:00+00:00",
                canonical="SIGNED_IN",
                browser="SIGNED_OUT",
            ),
        ],
    }
    info = detect_logout_events(recorder)
    assert info["browser_logout_at"] == "2026-01-01T00:05:00+00:00"
    assert info["logout_source"] == "browser_observation_SIGNED_OUT"


def test_login_url_logout_fallback():
    recorder = {
        "initial_canonical_authentication_state": "SIGNED_IN",
        "observations": [
            _observation(observed_at="2026-01-01T00:00:00+00:00"),
            _observation(
                observed_at="2026-01-01T00:05:00+00:00",
                canonical="SIGNED_IN",
                browser="LOGIN_UNKNOWN",
                login_url=True,
                title="Log In",
            ),
        ],
    }
    info = detect_logout_events(recorder)
    assert info["login_url_logout_at"] == "2026-01-01T00:05:00+00:00"
    assert info["logout_source"] == "login_url_detected"


def test_warning_to_logout_calculation(tmp_path: Path):
    trial_dir = tmp_path / "001-none-30s"
    _build_trial(
        trial_dir,
        strategy="NONE",
        interval=30,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T17:33:19+00:00",
        warning_at="2026-07-19T17:37:19+00:00",
        logout_at="2026-07-19T17:38:23+00:00",
    )
    row = analyze_trial_directory(trial_dir)
    assert row["warning_to_logout_seconds"] == pytest.approx(64.0, abs=0.1)


def test_baseline_comparison_tolerance():
    trials = [
        {
            "strategy": "NONE",
            "logout_elapsed_seconds": 300.0,
            "idle_warning_elapsed_seconds": 240.0,
        },
        {
            "strategy": "SESSION_API",
            "logout_elapsed_seconds": 305.0,
            "idle_warning_elapsed_seconds": 242.0,
        },
        {
            "strategy": "OVERVIEW_RELOAD",
            "logout_elapsed_seconds": 800.0,
            "idle_warning_elapsed_seconds": 740.0,
        },
    ]
    apply_baseline_comparisons(trials, tolerance_seconds=15.0)
    assert trials[1]["meaningful_logout_delay_vs_baseline"] is False
    assert trials[1]["logout_delay_vs_baseline_seconds"] == pytest.approx(5.0)
    assert trials[2]["meaningful_logout_delay_vs_baseline"] is True
    assert trials[2]["logout_delay_vs_baseline_seconds"] == pytest.approx(500.0)


def test_effective_candidate_classification():
    classification, *_ = classify_trial_result(
        strategy="OVERVIEW_RELOAD",
        strategy_execution_verified=True,
        keepalive_outcome="duration_completed",
        recorder_outcome="timeout",
        final_canonical_state="SIGNED_IN",
        logout_elapsed_seconds=None,
        configured_duration_seconds=600,
        keepalive_attempt_count=10,
        keepalive_success_count=10,
        keepalive_failure_count=0,
        meaningful_logout_delay_vs_baseline=None,
        baseline_available=True,
        logout_observed=False,
        duration_completed_before_logout=None,
    )
    assert classification == RESULT_EFFECTIVE_CANDIDATE


def test_ineffective_classification():
    classification, *_ = classify_trial_result(
        strategy="SESSION_API",
        strategy_execution_verified=True,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical_state="SIGNED_OUT",
        logout_elapsed_seconds=302.0,
        configured_duration_seconds=600,
        keepalive_attempt_count=10,
        keepalive_success_count=10,
        keepalive_failure_count=0,
        meaningful_logout_delay_vs_baseline=False,
        baseline_available=True,
        logout_observed=True,
        duration_completed_before_logout=False,
    )
    assert classification == RESULT_INEFFECTIVE


def test_partially_effective_classification():
    classification, *_ = classify_trial_result(
        strategy="OVERVIEW_RELOAD",
        strategy_execution_verified=True,
        keepalive_outcome="duration_completed",
        recorder_outcome="logged_out",
        final_canonical_state="SIGNED_OUT",
        logout_elapsed_seconds=900.0,
        configured_duration_seconds=600,
        keepalive_attempt_count=16,
        keepalive_success_count=16,
        keepalive_failure_count=0,
        meaningful_logout_delay_vs_baseline=True,
        baseline_available=True,
        logout_observed=True,
        duration_completed_before_logout=True,
    )
    assert classification == RESULT_PARTIALLY_EFFECTIVE


def test_operational_failure_classification():
    classification, *_ = classify_trial_result(
        strategy="SESSION_API",
        strategy_execution_verified=True,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical_state="SIGNED_OUT",
        logout_elapsed_seconds=300.0,
        configured_duration_seconds=600,
        keepalive_attempt_count=9,
        keepalive_success_count=0,
        keepalive_failure_count=9,
        meaningful_logout_delay_vs_baseline=False,
        baseline_available=True,
        logout_observed=True,
        duration_completed_before_logout=False,
    )
    assert classification == RESULT_OPERATIONALLY_FAILED


def test_inconclusive_classification():
    classification, *_ = classify_trial_result(
        strategy="PAGE_ACTIVITY",
        strategy_execution_verified=None,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical_state="SIGNED_OUT",
        logout_elapsed_seconds=300.0,
        configured_duration_seconds=600,
        keepalive_attempt_count=0,
        keepalive_success_count=0,
        keepalive_failure_count=0,
        meaningful_logout_delay_vs_baseline=None,
        baseline_available=True,
        logout_observed=True,
        duration_completed_before_logout=False,
    )
    assert classification == RESULT_INCONCLUSIVE


def test_duration_completed_plus_signed_out_is_not_effective(tmp_path: Path):
    trial_dir = tmp_path / "005-overview-reload-30s"
    _build_trial(
        trial_dir,
        strategy="OVERVIEW_RELOAD",
        interval=30,
        keepalive_outcome="duration_completed",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T18:58:55+00:00",
        warning_at="2026-07-19T19:13:06+00:00",
        logout_at="2026-07-19T19:14:03+00:00",
        action_success=16,
        keepalive_completed_at="2026-07-19T19:09:03+00:00",
    )
    row = analyze_trial_directory(trial_dir)
    trials = [
        {
            "strategy": "NONE",
            "logout_elapsed_seconds": 304.0,
            "idle_warning_elapsed_seconds": 240.0,
        },
        row,
    ]
    apply_baseline_comparisons(trials, tolerance_seconds=15.0)
    finalize_trial_classifications(trials, baseline_available=True)
    assert trials[1]["result_classification"] != RESULT_EFFECTIVE_CANDIDATE
    assert trials[1]["result_classification"] == RESULT_PARTIALLY_EFFECTIVE
    assert trials[1]["duration_completed_before_logout"] is True


def test_missing_attempt_history_handled_honestly(tmp_path: Path):
    trial_dir = tmp_path / "004-page-activity-30s"
    _build_trial(
        trial_dir,
        strategy="PAGE_ACTIVITY",
        interval=30,
        keepalive_outcome="logged_out",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T18:52:40+00:00",
        warning_at="2026-07-19T18:56:40+00:00",
        logout_at="2026-07-19T18:57:45+00:00",
        action_failure=0,
        events=[
            {
                "timestamp": "2026-07-19T18:52:40+00:00",
                "event_type": "trial_started",
                "strategy": "PAGE_ACTIVITY",
            }
        ],
    )
    # Force missing counts as well.
    status = json.loads((trial_dir / "keepalive-status.json").read_text(encoding="utf-8"))
    status["keepalive_action_count"] = 0
    status["keepalive_action_success_count"] = 0
    status["keepalive_action_failure_count"] = 0
    status["keepalive_events"] = [
        {
            "timestamp": "2026-07-19T18:52:40+00:00",
            "event_type": "trial_started",
            "strategy": "PAGE_ACTIVITY",
        }
    ]
    _write_json(trial_dir / "keepalive-status.json", status)
    row = analyze_trial_directory(trial_dir)
    assert row["strategy_execution_verified"] is None
    assert "No attempt-level history" in row["strategy_execution_evidence"]


def test_future_attempt_history_files_are_parsed(tmp_path: Path):
    trial_dir = tmp_path / "005-overview-reload-30s"
    attempts = [
        {
            "attempted_at": "2026-07-19T18:59:00+00:00",
            "strategy": "OVERVIEW_RELOAD",
            "action": "overview_reload",
            "target": "https://global.americanexpress.com/overview",
            "success": True,
            "result": "success",
            "reason": "success",
            "duration_ms": 1200,
            "authentication_state_after_attempt": "SIGNED_IN",
        }
    ]
    _build_trial(
        trial_dir,
        strategy="OVERVIEW_RELOAD",
        interval=30,
        keepalive_outcome="duration_completed",
        recorder_outcome="logged_out",
        final_canonical="SIGNED_OUT",
        started_at="2026-07-19T18:58:55+00:00",
        warning_at="2026-07-19T19:13:06+00:00",
        logout_at="2026-07-19T19:14:03+00:00",
        action_success=1,
        attempts=attempts,
        include_attempt_file=True,
    )
    loaded = load_keepalive_attempts(
        trial_dir,
        json.loads((trial_dir / "keepalive-status.json").read_text(encoding="utf-8")),
    )
    assert len(loaded) == 1
    assert loaded[0]["action"] == "overview_reload"
    row = analyze_trial_directory(trial_dir)
    assert row["strategy_execution_verified"] is True
    assert "attempt record" in row["strategy_execution_evidence"]


def test_current_campaign_format_remains_supported(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    # Remove any attempt files to mimic current artifacts.
    for path in campaign_dir.rglob("keepalive-attempts.jsonl"):
        path.unlink()
    analysis = analyze_campaign_directory(campaign_dir, write_outputs=False)
    overview = next(t for t in analysis["trials"] if t["strategy"] == "OVERVIEW_RELOAD")
    assert overview["strategy_execution_verified"] is True
    assert overview["result_classification"] == RESULT_PARTIALLY_EFFECTIVE
    session = next(
        t
        for t in analysis["trials"]
        if t["strategy"] == "SESSION_API" and t["keepalive_interval_seconds"] == 30
    )
    assert session["result_classification"] == RESULT_OPERATIONALLY_FAILED


def test_markdown_json_csv_outputs(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    analysis = analyze_campaign_directory(campaign_dir)
    payload = json.loads((campaign_dir / "campaign-analysis.json").read_text(encoding="utf-8"))
    assert payload["executive_conclusion"]
    csv_text = (campaign_dir / "campaign-analysis.csv").read_text(encoding="utf-8")
    assert "result_classification" in csv_text
    assert "OVERVIEW_RELOAD" in csv_text
    assert analysis["output_paths"]["markdown_path"].endswith("campaign-analysis.md")


def test_analyzer_never_starts_runtime_or_chrome():
    source = Path("mighty/provider_runtime_campaign_analysis.py").read_text(encoding="utf-8")
    assert "sync_playwright" not in source
    assert "run_server" not in source
    assert "launch_native_chrome" not in source
    assert "ensure_provider_runtime" not in source


def test_analyzer_never_mutates_source_evidence(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    trial = campaign_dir / "trials" / "001-none-30s"
    before_summary = (trial / "experiment-summary.json").read_bytes()
    before_keepalive = (trial / "keepalive-status.json").read_bytes()
    before_recording = (trial / "recorder" / "recording.json").read_bytes()
    analyze_campaign_directory(campaign_dir)
    assert (trial / "experiment-summary.json").read_bytes() == before_summary
    assert (trial / "keepalive-status.json").read_bytes() == before_keepalive
    assert (trial / "recorder" / "recording.json").read_bytes() == before_recording


def test_cli_analyze_campaign_and_campaign_analyze_flag():
    with patch(
        "sys.argv",
        [
            "provider_runtime.py",
            "analyze-campaign",
            "/tmp/campaign",
        ],
    ):
        args = parse_args()
    assert args.command == "analyze-campaign"
    assert str(args.campaign_path) == "/tmp/campaign"

    with patch(
        "sys.argv",
        ["provider_runtime.py", "campaign", "amex", "--analyze"],
    ):
        args = parse_args()
    assert args.command == "campaign"
    assert args.analyze is True


def test_campaign_analyze_failure_does_not_destroy_evidence(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    zip_path = campaign_dir / f"{campaign_dir.name}.zip"
    zip_path.write_bytes(b"campaign-zip")
    result = {
        "ok": True,
        "exit_code": 0,
        "campaign_dir": str(campaign_dir),
        "zip_path": str(zip_path),
    }
    with patch(
        "mighty.provider_runtime_campaign_analysis.run_analyze_campaign_command",
        side_effect=RuntimeError("boom"),
    ):
        code = _maybe_analyze_campaign_after_run(result)
    assert code == 1
    assert zip_path.read_bytes() == b"campaign-zip"
    assert (campaign_dir / "trials" / "001-none-30s" / "experiment-summary.json").is_file()


def test_run_client_analyze_campaign_offline(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    with patch(
        "sys.argv",
        ["provider_runtime.py", "analyze-campaign", str(campaign_dir)],
    ):
        args = parse_args()
    with patch("mighty.provider_runtime.request_json") as request_json:
        code = run_client_command(args)
    assert code == 0
    request_json.assert_not_called()
    assert (campaign_dir / "campaign-analysis.md").is_file()


def test_detect_idle_warning_uses_structured_text():
    recorder = {
        "observations": [
            {
                "observed_at": "2026-01-01T00:01:00+00:00",
                "accessibility_text_summary": (
                    "for your security, this session will expire due to inactivity"
                ),
                "dom_text_summary": "",
                "optional_text_searches": [],
                "browser_inspector": {},
            }
        ]
    }
    warning_at, source = detect_idle_warning(recorder, {})
    assert warning_at == "2026-01-01T00:01:00+00:00"
    assert source == "accessibility_text_summary"


def test_sanitize_keepalive_attempt_strips_secrets():
    cleaned = sanitize_keepalive_attempt(
        {
            "attempted_at": "2026-01-01T00:00:00+00:00",
            "strategy": "SESSION_API",
            "action": "session_api_fetch",
            "target": "https://functions.americanexpress.com/ReadUserSession.v1?token=abc",
            "success": False,
            "result": "failure",
            "cookies": "secret",
            "authorization": "Bearer x",
            "body": "{\"account\":1}",
            "error_message": "Error: blocked",
        }
    )
    assert "cookies" not in cleaned
    assert "authorization" not in cleaned
    assert "body" not in cleaned
    assert "token=abc" not in (cleaned.get("target") or "")


def test_recommend_overview_reload_validation(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    analysis = analyze_campaign_directory(campaign_dir, write_outputs=False)
    recommendation = analysis["recommendation"]
    assert recommendation["recommendation_code"] == "B"
    assert "OVERVIEW_RELOAD" in recommendation["recommended_next_experiment"]


def test_analyze_campaign_path_accepts_directory_and_zip(tmp_path: Path):
    campaign_dir = _build_campaign(tmp_path / "campaign")
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in campaign_dir.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=str(path.relative_to(campaign_dir)))
    dir_analysis = analyze_campaign_path(campaign_dir, write_outputs=False)
    zip_analysis = analyze_campaign_path(zip_path, write_outputs=False)
    assert dir_analysis["source_mode"] == "directory"
    assert zip_analysis["source_mode"] == "zip"
    assert len(dir_analysis["trials"]) == len(zip_analysis["trials"]) == 5
