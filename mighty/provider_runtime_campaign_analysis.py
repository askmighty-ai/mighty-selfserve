"""Offline analysis of Amex expiration campaign evidence.

Operates only on saved campaign directories or ZIPs. Never starts serve/Chrome
and never mutates original trial artifacts.
"""

from __future__ import annotations

import csv
import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CAMPAIGN_ANALYSIS_JSON = "campaign-analysis.json"
CAMPAIGN_ANALYSIS_CSV = "campaign-analysis.csv"
CAMPAIGN_ANALYSIS_MD = "campaign-analysis.md"
KEEPALIVE_ATTEMPTS_JSONL = "keepalive-attempts.jsonl"
KEEPALIVE_ATTEMPTS_JSON = "keepalive-attempts.json"

DEFAULT_BASELINE_COMPARISON_TOLERANCE_SECONDS = 15.0

RESULT_BASELINE = "BASELINE"
RESULT_EFFECTIVE_CANDIDATE = "EFFECTIVE_CANDIDATE"
RESULT_INEFFECTIVE = "INEFFECTIVE"
RESULT_PARTIALLY_EFFECTIVE = "PARTIALLY_EFFECTIVE"
RESULT_OPERATIONALLY_FAILED = "OPERATIONALLY_FAILED"
RESULT_INCONCLUSIVE = "INCONCLUSIVE"

IDLE_WARNING_PHRASES = (
    "session will expire",
    "session is about to expire",
    "for your security",
    "due to inactivity",
    "your session will expire",
    "your session is about to expire",
)

LOGOUT_TEXT_PHRASES = (
    "we logged you out",
    "logged you out automatically",
    "you have been logged out",
    "signed you out",
)

LOGIN_PAGE_TITLE_MARKERS = (
    "log in",
    "sign in",
    "login",
)

ANALYSIS_TRIAL_CSV_FIELDS = (
    "trial_number",
    "strategy",
    "keepalive_interval_seconds",
    "trial_started_at",
    "trial_completed_at",
    "recorder_started_at",
    "recorder_completed_at",
    "configured_trial_duration_seconds",
    "actual_experiment_duration_seconds",
    "actual_recorder_duration_seconds",
    "recorder_outcome",
    "keepalive_outcome",
    "initial_canonical_authentication_state",
    "final_canonical_authentication_state",
    "initial_browser_authentication_state",
    "final_browser_authentication_state",
    "first_idle_warning_at",
    "idle_warning_elapsed_seconds",
    "first_logout_at",
    "logout_elapsed_seconds",
    "warning_to_logout_seconds",
    "canonical_logout_at",
    "browser_logout_at",
    "browser_canonical_disagreement_seconds",
    "verification_call_count",
    "observation_count",
    "collection_error_count",
    "keepalive_attempt_count",
    "keepalive_success_count",
    "keepalive_failure_count",
    "first_keepalive_attempt_at",
    "last_keepalive_attempt_at",
    "mean_keepalive_interval_seconds",
    "maximum_keepalive_gap_seconds",
    "strategy_execution_verified",
    "strategy_execution_evidence",
    "duration_completed_before_logout",
    "logout_detected_after_keepalive_completed",
    "warning_delay_vs_baseline_seconds",
    "logout_delay_vs_baseline_seconds",
    "logout_delay_vs_baseline_percent",
    "meaningful_logout_delay_vs_baseline",
    "result_classification",
    "conclusion",
    "confidence",
    "recommended_next_step",
)


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _elapsed_seconds(start: Any, end: Any) -> float | None:
    start_dt = _parse_iso_timestamp(start)
    end_dt = _parse_iso_timestamp(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _text_blob(*parts: Any) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, str):
            chunks.append(part)
        elif isinstance(part, list):
            for item in part:
                chunks.append(_text_blob(item))
        elif isinstance(part, dict):
            for key in (
                "text",
                "snippet",
                "summary",
                "matched_text",
                "accessibility_text_summary",
                "dom_text_summary",
            ):
                if key in part:
                    chunks.append(_text_blob(part.get(key)))
            candidates = part.get("candidates")
            if isinstance(candidates, list):
                for candidate in candidates:
                    if isinstance(candidate, dict):
                        chunks.append(_text_blob(candidate.get("text"), candidate.get("snippet")))
        else:
            chunks.append(str(part))
    return " ".join(chunk for chunk in chunks if chunk).lower()


def observation_contains_idle_warning(observation: dict[str, Any]) -> bool:
    """Detect Amex idle-warning phrases from structured observation text."""
    searches = observation.get("optional_text_searches")
    if isinstance(searches, list):
        matched = {
            str(item.get("term") or "").lower()
            for item in searches
            if isinstance(item, dict) and int(item.get("match_count") or 0) > 0
        }
        if "expire" in matched and ("continue" in matched or "inactivity" in matched):
            return True
        if any("expire" in term for term in matched) and any(
            "security" in term or "inactiv" in term for term in matched
        ):
            return True

    inspector = observation.get("browser_inspector")
    if isinstance(inspector, dict):
        for candidate in inspector.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            text = _text_blob(candidate.get("text"), candidate.get("snippet"))
            if any(phrase in text for phrase in IDLE_WARNING_PHRASES):
                return True
            classified = candidate.get("classified_as_expiration_dialog")
            if classified is True:
                return True

    blob = _text_blob(
        observation.get("dom_text_summary"),
        observation.get("accessibility_text_summary"),
        observation.get("browser_inspector"),
    )
    return any(phrase in blob for phrase in IDLE_WARNING_PHRASES)


def observation_contains_logout_text(observation: dict[str, Any]) -> bool:
    blob = _text_blob(
        observation.get("dom_text_summary"),
        observation.get("accessibility_text_summary"),
        observation.get("selected_page_title"),
        observation.get("browser_inspector"),
    )
    if any(phrase in blob for phrase in LOGOUT_TEXT_PHRASES):
        return True
    title = str(observation.get("selected_page_title") or "").lower()
    if observation.get("login_url_detected") and any(
        marker in title for marker in LOGIN_PAGE_TITLE_MARKERS
    ):
        return True
    return False


def load_keepalive_attempts(trial_dir: Path, keepalive_status: dict[str, Any]) -> list[dict[str, Any]]:
    """Load attempt-level history, preferring dedicated files over status aggregates."""
    attempts: list[dict[str, Any]] = []
    jsonl_path = trial_dir / KEEPALIVE_ATTEMPTS_JSONL
    if jsonl_path.is_file():
        try:
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    attempts.append(item)
        except OSError:
            pass
        if attempts:
            return attempts

    json_path = trial_dir / KEEPALIVE_ATTEMPTS_JSON
    payload = _read_json(json_path)
    if payload is not None:
        raw = payload.get("attempts") if "attempts" in payload else payload.get("keepalive_attempts")
        if isinstance(raw, list):
            attempts = [item for item in raw if isinstance(item, dict)]
            if attempts:
                return attempts

    raw_status = keepalive_status.get("keepalive_attempts")
    if isinstance(raw_status, list):
        attempts = [item for item in raw_status if isinstance(item, dict)]
        if attempts:
            return attempts

    # Backward compatible: reconstruct coarse attempts from keepalive_events.
    events = keepalive_status.get("keepalive_events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("event_type") != "action":
                continue
            attempts.append(
                {
                    "attempted_at": event.get("timestamp") or event.get("attempted_at"),
                    "strategy": event.get("strategy"),
                    "action": "keepalive_action",
                    "target": None,
                    "success": str(event.get("action_result") or "").lower() == "success",
                    "result": event.get("action_result"),
                    "reason": event.get("action_result"),
                    "duration_ms": None,
                    "authentication_state_after_attempt": event.get("authentication_state"),
                    "error_type": None,
                    "error_message": None,
                    "source": "keepalive_events",
                }
            )
    return attempts


def _attempt_timing_stats(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [
        _parse_iso_timestamp(item.get("attempted_at") or item.get("timestamp"))
        for item in attempts
    ]
    timestamps = [ts for ts in timestamps if ts is not None]
    timestamps.sort()
    first_at = timestamps[0].isoformat() if timestamps else None
    last_at = timestamps[-1].isoformat() if timestamps else None
    gaps: list[float] = []
    for idx in range(1, len(timestamps)):
        gaps.append((timestamps[idx] - timestamps[idx - 1]).total_seconds())
    mean_interval = (sum(gaps) / len(gaps)) if gaps else None
    max_gap = max(gaps) if gaps else None
    return {
        "first_keepalive_attempt_at": first_at,
        "last_keepalive_attempt_at": last_at,
        "mean_keepalive_interval_seconds": mean_interval,
        "maximum_keepalive_gap_seconds": max_gap,
    }


def verify_strategy_execution(
    *,
    strategy: str,
    keepalive_status: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> tuple[bool | None, str]:
    """Prove strategy execution from strongest available evidence."""
    strategy = str(strategy or "NONE").upper()
    action_count = int(keepalive_status.get("keepalive_action_count") or 0)
    success_count = int(keepalive_status.get("keepalive_action_success_count") or 0)
    failure_count = int(keepalive_status.get("keepalive_action_failure_count") or 0)

    if strategy == "NONE":
        if action_count == 0 and not attempts:
            return True, "NONE strategy recorded zero keepalive actions as expected."
        if action_count > 0 or attempts:
            return False, (
                "NONE strategy unexpectedly recorded keepalive actions; "
                "execution evidence conflicts with strategy selection."
            )
        return True, "NONE strategy had no keepalive action history."

    if attempts:
        successes = sum(1 for item in attempts if item.get("success") is True)
        failures = sum(
            1
            for item in attempts
            if item.get("success") is False
            or str(item.get("result") or "").lower() == "failure"
        )
        source_note = ""
        if all(item.get("source") == "keepalive_events" for item in attempts):
            source_note = " Reconstructed from keepalive_events (no attempt-history file)."
        return True, (
            f"{len(attempts)} attempt record(s) found "
            f"({successes} success, {failures} failure).{source_note}"
        )

    if action_count > 0:
        return True, (
            f"Aggregate keepalive counters show {action_count} attempt(s) "
            f"({success_count} success, {failure_count} failure); "
            "per-attempt history file was missing."
        )

    return None, (
        "No attempt-level history and no aggregate action counts were available "
        f"to verify {strategy} execution."
    )


def detect_idle_warning(
    recorder: dict[str, Any],
    keepalive_status: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Return (timestamp, source) for first idle warning."""
    events = keepalive_status.get("keepalive_events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("event_type") == "expiration_dialog" or event.get(
                "expiration_dialog_detected"
            ):
                stamp = event.get("timestamp") or event.get("observed_at")
                if stamp:
                    return str(stamp), "keepalive_expiration_dialog_event"

    for observation in recorder.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        if observation_contains_idle_warning(observation):
            stamp = observation.get("observed_at")
            if stamp:
                # Prefer a more specific source label.
                ax = _text_blob(observation.get("accessibility_text_summary"))
                dom = _text_blob(observation.get("dom_text_summary"))
                if any(phrase in ax for phrase in IDLE_WARNING_PHRASES):
                    return str(stamp), "accessibility_text_summary"
                if any(phrase in dom for phrase in IDLE_WARNING_PHRASES):
                    return str(stamp), "dom_text_summary"
                searches = observation.get("optional_text_searches")
                if isinstance(searches, list) and any(
                    isinstance(item, dict) and int(item.get("match_count") or 0) > 0
                    for item in searches
                ):
                    return str(stamp), "optional_text_searches"
                return str(stamp), "browser_inspector_or_text"
    return None, None


def detect_logout_events(
    recorder: dict[str, Any],
) -> dict[str, Any]:
    """Detect logout using structured evidence precedence."""
    observations = [
        item for item in (recorder.get("observations") or []) if isinstance(item, dict)
    ]
    canonical_logout_at: str | None = None
    browser_logout_at: str | None = None
    login_url_logout_at: str | None = None
    text_logout_at: str | None = None
    previous_canonical: str | None = recorder.get("initial_canonical_authentication_state")
    previous_browser: str | None = None

    for observation in observations:
        observed_at = observation.get("observed_at")
        canonical = observation.get("canonical_authentication_state")
        browser = observation.get("browser_observation_authentication_state")
        if (
            canonical_logout_at is None
            and previous_canonical != "SIGNED_OUT"
            and canonical == "SIGNED_OUT"
            and observed_at
        ):
            canonical_logout_at = str(observed_at)
        if (
            login_url_logout_at is None
            and observation.get("login_url_detected")
            and observed_at
        ):
            login_url_logout_at = str(observed_at)
        if (
            browser_logout_at is None
            and previous_browser != "SIGNED_OUT"
            and browser == "SIGNED_OUT"
            and observed_at
        ):
            browser_logout_at = str(observed_at)
        if text_logout_at is None and observation_contains_logout_text(observation) and observed_at:
            text_logout_at = str(observed_at)
        if canonical is not None:
            previous_canonical = str(canonical)
        if browser is not None:
            previous_browser = str(browser)

    if canonical_logout_at is None and recorder.get("final_canonical_authentication_state") == "SIGNED_OUT":
        canonical_logout_at = (
            recorder.get("logout_detected_at")
            or recorder.get("completed_at")
            or (str(observations[-1].get("observed_at")) if observations else None)
        )
        if canonical_logout_at is not None:
            canonical_logout_at = str(canonical_logout_at)

    first_logout_at: str | None = None
    logout_source: str | None = None
    for stamp, source in (
        (canonical_logout_at, "canonical_SIGNED_OUT"),
        (login_url_logout_at, "login_url_detected"),
        (browser_logout_at, "browser_observation_SIGNED_OUT"),
        (text_logout_at, "logout_text"),
        (
            str(recorder["logout_detected_at"])
            if recorder.get("logout_detected_at")
            else None,
            "recorder_logout_detected_at",
        ),
    ):
        if stamp:
            first_logout_at = stamp
            logout_source = source
            break

    disagreement = _elapsed_seconds(canonical_logout_at, browser_logout_at)
    if disagreement is None:
        disagreement = _elapsed_seconds(browser_logout_at, canonical_logout_at)

    return {
        "first_logout_at": first_logout_at,
        "logout_source": logout_source,
        "canonical_logout_at": canonical_logout_at,
        "browser_logout_at": browser_logout_at,
        "login_url_logout_at": login_url_logout_at,
        "text_logout_at": text_logout_at,
        "browser_canonical_disagreement_seconds": disagreement,
    }


def classify_trial_result(
    *,
    strategy: str,
    strategy_execution_verified: bool | None,
    keepalive_outcome: str | None,
    recorder_outcome: str | None,
    final_canonical_state: str | None,
    logout_elapsed_seconds: float | None,
    configured_duration_seconds: float | None,
    keepalive_attempt_count: int,
    keepalive_success_count: int,
    keepalive_failure_count: int,
    meaningful_logout_delay_vs_baseline: bool | None,
    baseline_available: bool,
    logout_observed: bool,
    duration_completed_before_logout: bool | None,
) -> tuple[str, str, str, str]:
    """Return (classification, conclusion, confidence, recommended_next_step)."""
    strategy = str(strategy or "NONE").upper()
    reasons: list[str] = []

    if strategy == "NONE":
        return (
            RESULT_BASELINE,
            "NONE trial establishes the idle logout baseline.",
            "high" if logout_elapsed_seconds is not None else "medium",
            "Compare active strategies against this baseline logout timing.",
        )

    if strategy_execution_verified is None:
        return (
            RESULT_INCONCLUSIVE,
            "Strategy execution could not be verified from saved evidence.",
            "low",
            "Instrument strategy attempts before further experimentation.",
        )

    if strategy_execution_verified is False and strategy != "NONE":
        return (
            RESULT_INCONCLUSIVE,
            "Strategy execution evidence conflicts with the selected strategy.",
            "low",
            "Inspect keepalive evidence and re-run with attempt-level instrumentation.",
        )

    if keepalive_outcome == "preflight_failed":
        return (
            RESULT_OPERATIONALLY_FAILED,
            (
                "Preflight probe failed before the timed trial; strategy execution "
                "is operationally broken and was not evaluated for idle-delay effect."
            ),
            "high",
            "Repeat this strategy only after correcting the implementation failure.",
        )

    if keepalive_attempt_count > 0 and keepalive_success_count == 0 and keepalive_failure_count > 0:
        return (
            RESULT_OPERATIONALLY_FAILED,
            (
                f"All {keepalive_failure_count} keepalive attempt(s) failed; "
                "the strategy never reached a successful intended action."
            ),
            "high",
            "Repeat this strategy only after correcting the implementation failure.",
        )

    if (
        keepalive_outcome == "duration_completed"
        and final_canonical_state == "SIGNED_OUT"
        and logout_observed
    ):
        reasons.append(
            "keepalive_outcome=duration_completed but canonical state ended SIGNED_OUT"
        )

    survived_duration = (
        not logout_observed
        and final_canonical_state == "SIGNED_IN"
        and keepalive_outcome == "duration_completed"
        and recorder_outcome not in {"logged_out"}
    )
    if survived_duration and strategy_execution_verified:
        return (
            RESULT_EFFECTIVE_CANDIDATE,
            (
                "Configured duration completed with verified strategy execution and "
                "no logout observed while canonical state remained SIGNED_IN."
            ),
            "high",
            "Run a longer focused validation of this strategy.",
        )

    # Explicit guard: duration_completed + SIGNED_OUT is never effective.
    if (
        keepalive_outcome == "duration_completed"
        and final_canonical_state == "SIGNED_OUT"
        and strategy_execution_verified
        and logout_observed
    ):
        if meaningful_logout_delay_vs_baseline:
            return (
                RESULT_PARTIALLY_EFFECTIVE,
                (
                    "Keepalive reported duration_completed while the recorder later "
                    "observed logout; logout was materially later than NONE, so the "
                    "strategy delayed but did not prevent expiration."
                ),
                "high",
                "Run a focused longer validation of this strategy with attempt-level instrumentation.",
            )
        if baseline_available and meaningful_logout_delay_vs_baseline is False:
            return (
                RESULT_INEFFECTIVE,
                (
                    "Keepalive completed its duration, but logout still occurred and "
                    "was not meaningfully later than the NONE baseline."
                ),
                "medium",
                "Reject this strategy for keepalive effect and evaluate alternatives.",
            )
        return (
            RESULT_INCONCLUSIVE,
            (
                "Keepalive duration completed before recorder logout; without a valid "
                "NONE baseline comparison the effectiveness is unclear. "
                + ("; ".join(reasons) if reasons else "")
            ).strip(),
            "medium",
            "Re-run with a NONE baseline and longer post-keepalive observation.",
        )

    if strategy_execution_verified and logout_observed:
        if meaningful_logout_delay_vs_baseline:
            if duration_completed_before_logout or (
                configured_duration_seconds is not None
                and logout_elapsed_seconds is not None
                and logout_elapsed_seconds < configured_duration_seconds
            ):
                return (
                    RESULT_PARTIALLY_EFFECTIVE,
                    "Strategy executed and delayed logout relative to NONE, but the session did not survive the intended duration.",
                    "high",
                    "Run a focused longer validation of this strategy.",
                )
            return (
                RESULT_PARTIALLY_EFFECTIVE,
                "Strategy executed and delayed logout relative to NONE, but logout still occurred.",
                "high",
                "Run a focused longer validation of this strategy.",
            )
        if baseline_available and meaningful_logout_delay_vs_baseline is False:
            return (
                RESULT_INEFFECTIVE,
                "Strategy executed, but logout timing was not meaningfully later than NONE.",
                "high",
                "Reject this strategy for keepalive effect.",
            )
        if not baseline_available:
            return (
                RESULT_INCONCLUSIVE,
                "Strategy executed and logout occurred, but no valid NONE baseline is available for comparison.",
                "medium",
                "Re-run the campaign including a NONE baseline trial.",
            )

    if recorder_outcome and keepalive_outcome and recorder_outcome != keepalive_outcome:
        return (
            RESULT_INCONCLUSIVE,
            (
                f"Recorder outcome ({recorder_outcome}) conflicts with keepalive "
                f"outcome ({keepalive_outcome})."
            ),
            "low",
            "Inspect dual-channel evidence before further experimentation.",
        )

    return (
        RESULT_INCONCLUSIVE,
        "Evidence was insufficient or contradictory for a reliable classification.",
        "low",
        "Instrument strategy attempts and re-run a focused comparison.",
    )


def analyze_trial_directory(trial_dir: Path) -> dict[str, Any]:
    """Analyze one trial evidence directory."""
    trial_dir = Path(trial_dir)
    summary = _read_json(trial_dir / "experiment-summary.json") or {}
    keepalive_status = _read_json(trial_dir / "keepalive-status.json") or {}
    recorder = _read_json(trial_dir / "recorder" / "recording.json") or {}
    attempts = load_keepalive_attempts(trial_dir, keepalive_status)

    strategy = str(
        summary.get("keepalive_strategy")
        or keepalive_status.get("keepalive_strategy")
        or "NONE"
    ).upper()
    interval = int(
        summary.get("keepalive_interval_seconds")
        or keepalive_status.get("keepalive_interval_seconds")
        or 0
    )
    configured_duration = summary.get("trial_duration_seconds")
    if configured_duration is None:
        configured_duration = keepalive_status.get("keepalive_duration_seconds")
    configured_duration_f = (
        float(configured_duration) if configured_duration is not None else None
    )

    trial_started_at = (
        keepalive_status.get("keepalive_started_at")
        or summary.get("started_at")
        or recorder.get("started_at")
    )
    trial_completed_at = summary.get("completed_at") or keepalive_status.get(
        "keepalive_completed_at"
    )
    recorder_started_at = recorder.get("started_at")
    recorder_completed_at = (
        summary.get("recorder_completed_at") or recorder.get("completed_at")
    )

    observations = [
        item for item in (recorder.get("observations") or []) if isinstance(item, dict)
    ]
    observation_count = len(observations)
    collection_error_count = 0
    verification_call_count = 0
    for observation in observations:
        errors = observation.get("collection_errors")
        if isinstance(errors, list):
            collection_error_count += len(errors)
        elif errors:
            collection_error_count += 1
        if observation.get("canonical_verified_this_poll"):
            verification_call_count += 1

    initial_canonical = recorder.get("initial_canonical_authentication_state") or recorder.get(
        "initial_authentication_state"
    )
    final_canonical = recorder.get("final_canonical_authentication_state") or recorder.get(
        "final_authentication_state"
    ) or summary.get("final_authentication_state")
    initial_browser = None
    final_browser = None
    if observations:
        initial_browser = observations[0].get("browser_observation_authentication_state")
        final_browser = observations[-1].get("browser_observation_authentication_state")

    warning_at, warning_source = detect_idle_warning(recorder, keepalive_status)
    logout_info = detect_logout_events(recorder)
    first_logout_at = logout_info["first_logout_at"]
    if first_logout_at is None and (
        summary.get("recorder_outcome") == "logged_out"
        or recorder.get("outcome") == "logged_out"
        or keepalive_status.get("keepalive_logged_out")
    ):
        first_logout_at = (
            recorder.get("logout_detected_at")
            or summary.get("recorder_completed_at")
            or trial_completed_at
        )
        if first_logout_at is not None:
            first_logout_at = str(first_logout_at)
            logout_info["logout_source"] = logout_info["logout_source"] or "outcome_fallback"

    idle_warning_elapsed = _elapsed_seconds(trial_started_at, warning_at)
    logout_elapsed = _elapsed_seconds(trial_started_at, first_logout_at)
    warning_to_logout = _elapsed_seconds(warning_at, first_logout_at)

    attempt_count = len(attempts)
    if attempt_count == 0:
        attempt_count = int(keepalive_status.get("keepalive_action_count") or 0)
    success_count = sum(1 for item in attempts if item.get("success") is True)
    failure_count = sum(
        1
        for item in attempts
        if item.get("success") is False
        or str(item.get("result") or "").lower() == "failure"
    )
    if not attempts:
        success_count = int(keepalive_status.get("keepalive_action_success_count") or 0)
        failure_count = int(keepalive_status.get("keepalive_action_failure_count") or 0)

    timing = _attempt_timing_stats(attempts)
    verified, evidence = verify_strategy_execution(
        strategy=strategy,
        keepalive_status=keepalive_status,
        attempts=attempts,
    )

    keepalive_completed_at = keepalive_status.get("keepalive_completed_at") or summary.get(
        "keepalive_completed_at"
    )
    keepalive_outcome = summary.get("keepalive_outcome") or keepalive_status.get(
        "keepalive_final_reason"
    )
    recorder_outcome = summary.get("recorder_outcome") or recorder.get("outcome")

    duration_completed_before_logout = None
    logout_after_keepalive = None
    if first_logout_at and keepalive_completed_at:
        delta = _elapsed_seconds(keepalive_completed_at, first_logout_at)
        logout_after_keepalive = delta is not None and delta > 0
        duration_completed_before_logout = bool(
            keepalive_outcome == "duration_completed" and logout_after_keepalive
        )
    elif keepalive_outcome == "duration_completed" and first_logout_at:
        duration_completed_before_logout = True

    actual_experiment_duration = summary.get("experiment_duration_seconds")
    if actual_experiment_duration is None:
        actual_experiment_duration = _elapsed_seconds(trial_started_at, trial_completed_at)
    actual_recorder_duration = summary.get("recorder_duration_seconds")
    if actual_recorder_duration is None:
        actual_recorder_duration = _elapsed_seconds(recorder_started_at, recorder_completed_at)

    # dirname trial number fallback
    trial_number = None
    name = trial_dir.name
    if len(name) >= 3 and name[:3].isdigit():
        trial_number = int(name[:3])

    return {
        "trial_number": trial_number,
        "strategy": strategy,
        "keepalive_interval_seconds": interval,
        "trial_started_at": trial_started_at,
        "trial_completed_at": trial_completed_at,
        "recorder_started_at": recorder_started_at,
        "recorder_completed_at": recorder_completed_at,
        "configured_trial_duration_seconds": configured_duration_f,
        "actual_experiment_duration_seconds": (
            float(actual_experiment_duration)
            if actual_experiment_duration is not None
            else None
        ),
        "actual_recorder_duration_seconds": (
            float(actual_recorder_duration)
            if actual_recorder_duration is not None
            else None
        ),
        "recorder_outcome": recorder_outcome,
        "keepalive_outcome": keepalive_outcome,
        "initial_canonical_authentication_state": initial_canonical,
        "final_canonical_authentication_state": final_canonical,
        "initial_browser_authentication_state": initial_browser,
        "final_browser_authentication_state": final_browser,
        "first_idle_warning_at": warning_at,
        "idle_warning_source": warning_source,
        "idle_warning_elapsed_seconds": idle_warning_elapsed,
        "first_logout_at": first_logout_at,
        "logout_source": logout_info.get("logout_source"),
        "logout_elapsed_seconds": logout_elapsed,
        "warning_to_logout_seconds": warning_to_logout,
        "canonical_logout_at": logout_info.get("canonical_logout_at"),
        "browser_logout_at": logout_info.get("browser_logout_at"),
        "browser_canonical_disagreement_seconds": logout_info.get(
            "browser_canonical_disagreement_seconds"
        ),
        "verification_call_count": verification_call_count,
        "observation_count": observation_count,
        "collection_error_count": collection_error_count,
        "keepalive_attempt_count": attempt_count,
        "keepalive_success_count": success_count,
        "keepalive_failure_count": failure_count,
        "first_keepalive_attempt_at": timing["first_keepalive_attempt_at"],
        "last_keepalive_attempt_at": timing["last_keepalive_attempt_at"],
        "mean_keepalive_interval_seconds": timing["mean_keepalive_interval_seconds"],
        "maximum_keepalive_gap_seconds": timing["maximum_keepalive_gap_seconds"],
        "strategy_execution_verified": verified,
        "strategy_execution_evidence": evidence,
        "duration_completed_before_logout": duration_completed_before_logout,
        "logout_detected_after_keepalive_completed": logout_after_keepalive,
        "evidence_directory": str(trial_dir),
        "warning_delay_vs_baseline_seconds": None,
        "logout_delay_vs_baseline_seconds": None,
        "logout_delay_vs_baseline_percent": None,
        "meaningful_logout_delay_vs_baseline": None,
        "result_classification": None,
        "conclusion": None,
        "confidence": None,
        "recommended_next_step": None,
        "classification_reasons": [],
    }


def _discover_trial_dirs(campaign_dir: Path) -> list[Path]:
    trials_root = campaign_dir / "trials"
    if trials_root.is_dir():
        dirs = [path for path in sorted(trials_root.iterdir()) if path.is_dir()]
        if dirs:
            return dirs
    # ZIP extract may flatten trial dirs beside summaries.
    dirs = []
    for path in sorted(campaign_dir.iterdir()):
        if not path.is_dir():
            continue
        if (path / "experiment-summary.json").is_file() or (
            path / "keepalive-status.json"
        ).is_file():
            dirs.append(path)
    return dirs


def apply_baseline_comparisons(
    trials: list[dict[str, Any]],
    *,
    tolerance_seconds: float = DEFAULT_BASELINE_COMPARISON_TOLERANCE_SECONDS,
) -> dict[str, Any] | None:
    baseline = None
    for trial in trials:
        if str(trial.get("strategy") or "").upper() == "NONE":
            baseline = trial
            break
    if baseline is None:
        for trial in trials:
            trial["warning_delay_vs_baseline_seconds"] = None
            trial["logout_delay_vs_baseline_seconds"] = None
            trial["logout_delay_vs_baseline_percent"] = None
            trial["meaningful_logout_delay_vs_baseline"] = None
        return None

    baseline_logout = baseline.get("logout_elapsed_seconds")
    baseline_warning = baseline.get("idle_warning_elapsed_seconds")
    for trial in trials:
        if trial is baseline:
            trial["warning_delay_vs_baseline_seconds"] = 0.0 if baseline_warning is not None else None
            trial["logout_delay_vs_baseline_seconds"] = 0.0 if baseline_logout is not None else None
            trial["logout_delay_vs_baseline_percent"] = 0.0 if baseline_logout is not None else None
            trial["meaningful_logout_delay_vs_baseline"] = False
            continue
        warning_delay = None
        logout_delay = None
        logout_pct = None
        meaningful = None
        if baseline_warning is not None and trial.get("idle_warning_elapsed_seconds") is not None:
            warning_delay = float(trial["idle_warning_elapsed_seconds"]) - float(baseline_warning)
        if baseline_logout is not None and trial.get("logout_elapsed_seconds") is not None:
            logout_delay = float(trial["logout_elapsed_seconds"]) - float(baseline_logout)
            if baseline_logout > 0:
                logout_pct = (logout_delay / float(baseline_logout)) * 100.0
            meaningful = abs(logout_delay) > float(tolerance_seconds) and logout_delay > 0
            if logout_delay is not None and logout_delay <= float(tolerance_seconds):
                meaningful = False
        trial["warning_delay_vs_baseline_seconds"] = warning_delay
        trial["logout_delay_vs_baseline_seconds"] = logout_delay
        trial["logout_delay_vs_baseline_percent"] = logout_pct
        trial["meaningful_logout_delay_vs_baseline"] = meaningful
    return baseline


def finalize_trial_classifications(
    trials: list[dict[str, Any]],
    *,
    baseline_available: bool,
) -> None:
    for trial in trials:
        classification, conclusion, confidence, next_step = classify_trial_result(
            strategy=str(trial.get("strategy") or "NONE"),
            strategy_execution_verified=trial.get("strategy_execution_verified"),
            keepalive_outcome=trial.get("keepalive_outcome"),
            recorder_outcome=trial.get("recorder_outcome"),
            final_canonical_state=trial.get("final_canonical_authentication_state"),
            logout_elapsed_seconds=trial.get("logout_elapsed_seconds"),
            configured_duration_seconds=trial.get("configured_trial_duration_seconds"),
            keepalive_attempt_count=int(trial.get("keepalive_attempt_count") or 0),
            keepalive_success_count=int(trial.get("keepalive_success_count") or 0),
            keepalive_failure_count=int(trial.get("keepalive_failure_count") or 0),
            meaningful_logout_delay_vs_baseline=trial.get(
                "meaningful_logout_delay_vs_baseline"
            ),
            baseline_available=baseline_available,
            logout_observed=bool(
                trial.get("first_logout_at")
                or trial.get("recorder_outcome") == "logged_out"
                or trial.get("final_canonical_authentication_state") == "SIGNED_OUT"
            ),
            duration_completed_before_logout=trial.get("duration_completed_before_logout"),
        )
        trial["result_classification"] = classification
        trial["conclusion"] = conclusion
        trial["confidence"] = confidence
        trial["recommended_next_step"] = next_step


def recommend_campaign_next_step(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive campaign-level recommendation from classified trial evidence."""
    rejected: list[dict[str, str]] = []
    inconclusive: list[dict[str, str]] = []
    operational_failures: list[dict[str, str]] = []
    partial: list[dict[str, Any]] = []
    effective: list[dict[str, Any]] = []

    for trial in trials:
        strategy = str(trial.get("strategy") or "NONE")
        interval = trial.get("keepalive_interval_seconds")
        label = f"{strategy} @ {interval}s"
        classification = trial.get("result_classification")
        entry = {"strategy": label, "reason": str(trial.get("conclusion") or "")}
        if classification == RESULT_BASELINE:
            continue
        if classification == RESULT_EFFECTIVE_CANDIDATE:
            effective.append(trial)
        elif classification == RESULT_PARTIALLY_EFFECTIVE:
            partial.append(trial)
        elif classification == RESULT_OPERATIONALLY_FAILED:
            operational_failures.append(entry)
            rejected.append(entry)
        elif classification == RESULT_INEFFECTIVE:
            rejected.append(entry)
        elif classification == RESULT_INCONCLUSIVE:
            inconclusive.append(entry)

    best = None
    best_score = float("-inf")
    for trial in effective + partial:
        score = float(trial.get("logout_elapsed_seconds") or 0.0)
        if trial.get("result_classification") == RESULT_EFFECTIVE_CANDIDATE:
            score += 1_000_000.0
        if score > best_score:
            best_score = score
            best = trial

    missing_attempt_history = any(
        trial.get("strategy") != "NONE"
        and "attempt-history file" in str(trial.get("strategy_execution_evidence") or "")
        for trial in trials
    ) or any(
        trial.get("strategy_execution_verified") is None for trial in trials
    )

    if missing_attempt_history and not partial and not effective and inconclusive:
        recommendation = (
            "Instrument strategy attempts before further experimentation."
        )
        letter = "A"
        confidence = "medium"
    elif best is not None and best.get("result_classification") == RESULT_PARTIALLY_EFFECTIVE:
        strategy = best.get("strategy")
        interval = best.get("keepalive_interval_seconds")
        recommendation = (
            f"Run a focused {strategy} validation for 30 minutes with attempt-level "
            f"instrumentation (interval {interval}s)."
        )
        letter = "B"
        confidence = "high"
    elif best is not None and best.get("result_classification") == RESULT_EFFECTIVE_CANDIDATE:
        strategy = best.get("strategy")
        interval = best.get("keepalive_interval_seconds")
        recommendation = (
            f"Run a longer focused {strategy} validation with attempt-level "
            f"instrumentation (interval {interval}s)."
        )
        letter = "B"
        confidence = "high"
    elif operational_failures and not partial and not effective:
        failed = operational_failures[0]["strategy"]
        recommendation = (
            f"Repeat {failed} only after correcting the implementation failure; "
            "otherwise design a new interaction mechanism."
        )
        letter = "D"
        confidence = "high"
    elif rejected and not partial and not effective:
        recommendation = (
            "Reject all existing strategies and design a new interaction mechanism."
        )
        letter = "C"
        confidence = "medium"
    else:
        recommendation = (
            "Inspect inconclusive evidence, then re-run a focused comparison with "
            "attempt-level instrumentation."
        )
        letter = "A"
        confidence = "low"

    best_label = None
    if best is not None:
        best_label = f"{best.get('strategy')} @ {best.get('keepalive_interval_seconds')}s"

    return {
        "best_performing_strategy": best_label,
        "rejected_strategies": rejected,
        "inconclusive_strategies": inconclusive,
        "operational_failures": operational_failures,
        "confidence": confidence,
        "recommendation_code": letter,
        "recommended_next_experiment": recommendation,
        "more_instrumentation_required_first": letter == "A",
    }


def build_executive_conclusion(
    trials: list[dict[str, Any]],
    recommendation: dict[str, Any],
    *,
    baseline: dict[str, Any] | None,
) -> str:
    parts: list[str] = []
    if baseline and baseline.get("logout_elapsed_seconds") is not None:
        parts.append(
            f"NONE baseline logged out after {baseline['logout_elapsed_seconds']:.1f}s."
        )
    elif baseline is None:
        parts.append("No valid NONE baseline was available, limiting comparative conclusions.")

    for trial in trials:
        if trial.get("strategy") == "NONE":
            continue
        classification = trial.get("result_classification")
        label = f"{trial.get('strategy')} @ {trial.get('keepalive_interval_seconds')}s"
        if classification == RESULT_OPERATIONALLY_FAILED:
            parts.append(
                f"{label} is operationally broken "
                f"({trial.get('keepalive_failure_count')} failed attempts)."
            )
        elif classification == RESULT_INEFFECTIVE:
            delay = trial.get("logout_delay_vs_baseline_seconds")
            delay_text = f"{delay:+.1f}s vs baseline" if delay is not None else "no meaningful delay"
            parts.append(f"{label} was ineffective ({delay_text}).")
        elif classification == RESULT_PARTIALLY_EFFECTIVE:
            delay = trial.get("logout_delay_vs_baseline_seconds")
            logout = trial.get("logout_elapsed_seconds")
            parts.append(
                f"{label} delayed logout to {logout:.1f}s "
                f"({delay:+.1f}s vs baseline) but did not prevent expiration."
                if logout is not None and delay is not None
                else f"{label} partially delayed logout."
            )
        elif classification == RESULT_EFFECTIVE_CANDIDATE:
            parts.append(f"{label} kept the session signed in through the configured duration.")
        elif classification == RESULT_INCONCLUSIVE:
            parts.append(f"{label} was inconclusive.")

    parts.append(str(recommendation.get("recommended_next_experiment") or "").strip())
    return " ".join(part for part in parts if part)


def format_delta(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    return f"{seconds:+.1f}s"


def render_campaign_analysis_markdown(analysis: dict[str, Any]) -> str:
    trials = analysis.get("trials") or []
    lines = [
        "# Amex Expiration Campaign Analysis",
        "",
        "## Executive conclusion",
        "",
        str(analysis.get("executive_conclusion") or ""),
        "",
        "## Results",
        "",
        "| Trial | Strategy | Attempts | Warning | Logout | Δ vs baseline | Classification |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for trial in trials:
        warning = trial.get("idle_warning_elapsed_seconds")
        logout = trial.get("logout_elapsed_seconds")
        warning_text = f"{warning:.1f}s" if warning is not None else "n/a"
        logout_text = f"{logout:.1f}s" if logout is not None else "n/a"
        lines.append(
            "| "
            f"{trial.get('trial_number')} | "
            f"{trial.get('strategy')} @ {trial.get('keepalive_interval_seconds')}s | "
            f"{trial.get('keepalive_attempt_count')} | "
            f"{warning_text} | "
            f"{logout_text} | "
            f"{format_delta(trial.get('logout_delay_vs_baseline_seconds'))} | "
            f"{trial.get('result_classification')} |"
        )

    lines.extend(["", "## Strategy details", ""])
    for trial in trials:
        lines.extend(
            [
                f"### Trial {trial.get('trial_number')}: "
                f"{trial.get('strategy')} @ {trial.get('keepalive_interval_seconds')}s",
                "",
                f"- Classification: `{trial.get('result_classification')}`",
                f"- Strategy execution verified: `{trial.get('strategy_execution_verified')}`",
                f"- Execution evidence: {trial.get('strategy_execution_evidence')}",
                f"- Keepalive outcome: `{trial.get('keepalive_outcome')}`",
                f"- Recorder outcome: `{trial.get('recorder_outcome')}`",
                f"- Warning: `{trial.get('first_idle_warning_at')}` "
                f"({trial.get('idle_warning_elapsed_seconds')}s; source={trial.get('idle_warning_source')})",
                f"- Logout: `{trial.get('first_logout_at')}` "
                f"({trial.get('logout_elapsed_seconds')}s; source={trial.get('logout_source')})",
                f"- Canonical logout: `{trial.get('canonical_logout_at')}`",
                f"- Browser logout: `{trial.get('browser_logout_at')}`",
                f"- Browser/canonical disagreement: `{trial.get('browser_canonical_disagreement_seconds')}`",
                f"- Duration completed before logout: `{trial.get('duration_completed_before_logout')}`",
                f"- Logout after keepalive completed: `{trial.get('logout_detected_after_keepalive_completed')}`",
                f"- Conclusion: {trial.get('conclusion')}",
                "",
            ]
        )

    gaps = analysis.get("evidence_gaps") or []
    lines.extend(["## Evidence quality and gaps", ""])
    if gaps:
        for gap in gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("- No major evidence gaps detected.")
    lines.append("")

    recommendation = analysis.get("recommendation") or {}
    lines.extend(
        [
            "## Recommended next experiment",
            "",
            str(recommendation.get("recommended_next_experiment") or ""),
            "",
        ]
    )
    command = recommendation.get("recommended_command")
    if command:
        lines.extend(["```bash", str(command), "```", ""])
    return "\n".join(lines)


def collect_evidence_gaps(trials: list[dict[str, Any]], baseline_available: bool) -> list[str]:
    gaps: list[str] = []
    if not baseline_available:
        gaps.append("No NONE baseline trial was available for comparative conclusions.")
    for trial in trials:
        label = f"{trial.get('strategy')} @ {trial.get('keepalive_interval_seconds')}s"
        if trial.get("strategy_execution_verified") is None:
            gaps.append(f"{label}: strategy execution could not be verified.")
        evidence = str(trial.get("strategy_execution_evidence") or "")
        if "No attempt-level history" in evidence or "attempt-history file was missing" in evidence:
            gaps.append(f"{label}: per-attempt history file was missing.")
        if (
            trial.get("keepalive_outcome") == "duration_completed"
            and trial.get("final_canonical_authentication_state") == "SIGNED_OUT"
        ):
            gaps.append(
                f"{label}: keepalive reported duration_completed while final canonical "
                "state was SIGNED_OUT (recorder continued after keepalive completion)."
            )
        if (
            trial.get("browser_canonical_disagreement_seconds") is not None
            and float(trial["browser_canonical_disagreement_seconds"]) > 5
        ):
            gaps.append(
                f"{label}: browser and canonical logout timestamps disagreed by "
                f"{trial['browser_canonical_disagreement_seconds']:.1f}s."
            )
    return gaps


def write_campaign_analysis_outputs(
    output_dir: Path,
    analysis: dict[str, Any],
) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / CAMPAIGN_ANALYSIS_JSON
    csv_path = output_dir / CAMPAIGN_ANALYSIS_CSV
    md_path = output_dir / CAMPAIGN_ANALYSIS_MD

    json_path.write_text(json.dumps(analysis, indent=2) + "\n", encoding="utf-8")

    trials = analysis.get("trials") or []
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(ANALYSIS_TRIAL_CSV_FIELDS),
            extrasaction="ignore",
        )
        writer.writeheader()
        for trial in trials:
            writer.writerow({key: trial.get(key) for key in ANALYSIS_TRIAL_CSV_FIELDS})

    md_path.write_text(render_campaign_analysis_markdown(analysis), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "csv_path": str(csv_path),
        "markdown_path": str(md_path),
    }


def format_campaign_analysis_terminal_summary(analysis: dict[str, Any]) -> str:
    trials = analysis.get("trials") or []
    recommendation = analysis.get("recommendation") or {}
    baseline = analysis.get("baseline_trial")
    lines = ["Campaign analysis complete.", ""]
    if isinstance(baseline, dict) and baseline.get("logout_elapsed_seconds") is not None:
        lines.append(f"Baseline logout: {baseline['logout_elapsed_seconds']:.1f} seconds")
        lines.append("")
    elif not analysis.get("baseline_available"):
        lines.append("Baseline logout: unavailable (no NONE trial)")
        lines.append("")

    for trial in trials:
        label = f"{trial.get('strategy')} @ {trial.get('keepalive_interval_seconds')}s"
        lines.append(label)
        lines.append(f"  {trial.get('result_classification')}")
        attempts = trial.get("keepalive_attempt_count")
        successes = trial.get("keepalive_success_count")
        failures = trial.get("keepalive_failure_count")
        if trial.get("strategy") != "NONE":
            if successes and int(successes) > 0:
                lines.append(f"  Attempts: {successes} successful")
            elif failures and int(failures) > 0:
                lines.append(f"  Attempts: {failures} failed")
            elif attempts:
                lines.append(f"  Attempts: {attempts}")
        logout = trial.get("logout_elapsed_seconds")
        delay = trial.get("logout_delay_vs_baseline_seconds")
        if logout is not None:
            if trial.get("strategy") == "NONE" or delay is None:
                lines.append(f"  Logout: {logout:.1f}s")
            else:
                lines.append(f"  Logout: {logout:.1f}s ({delay:+.1f}s vs baseline)")
        elif trial.get("result_classification") == RESULT_INCONCLUSIVE:
            lines.append(f"  {trial.get('conclusion')}")
        lines.append("")

    lines.append("Recommended next step:")
    lines.append(str(recommendation.get("recommended_next_experiment") or ""))
    lines.append("")
    outputs = analysis.get("output_paths") or {}
    md_path = outputs.get("markdown_path")
    if md_path:
        lines.append("Analysis written to:")
        lines.append(str(md_path))
    return "\n".join(lines).rstrip() + "\n"


def analyze_campaign_directory(
    campaign_dir: Path,
    *,
    output_dir: Path | None = None,
    tolerance_seconds: float = DEFAULT_BASELINE_COMPARISON_TOLERANCE_SECONDS,
    write_outputs: bool = True,
) -> dict[str, Any]:
    """Analyze a campaign directory and optionally write analysis artifacts."""
    campaign_dir = Path(campaign_dir).expanduser().resolve()
    if not campaign_dir.is_dir():
        raise FileNotFoundError(f"Campaign directory not found: {campaign_dir}")

    trial_dirs = _discover_trial_dirs(campaign_dir)
    if not trial_dirs:
        raise FileNotFoundError(f"No trial evidence directories found in {campaign_dir}")

    trials = [analyze_trial_directory(path) for path in trial_dirs]
    for index, trial in enumerate(trials, start=1):
        if trial.get("trial_number") is None:
            trial["trial_number"] = index

    baseline = apply_baseline_comparisons(trials, tolerance_seconds=tolerance_seconds)
    baseline_available = baseline is not None
    finalize_trial_classifications(trials, baseline_available=baseline_available)
    recommendation = recommend_campaign_next_step(trials)
    if recommendation.get("recommendation_code") == "B" and recommendation.get(
        "best_performing_strategy"
    ):
        best_strategy = str(recommendation["best_performing_strategy"]).split("@", 1)[0].strip()
        recommendation["recommended_command"] = (
            "PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py campaign amex \\\n"
            f"  --trial {best_strategy}:30 \\\n"
            "  --trial-duration-seconds 1800 \\\n"
            "  --analyze"
        )
    elif recommendation.get("recommendation_code") == "A":
        recommendation["recommended_command"] = (
            "PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py campaign amex --analyze"
        )
    gaps = collect_evidence_gaps(trials, baseline_available=baseline_available)
    executive = build_executive_conclusion(
        trials, recommendation, baseline=baseline
    )

    summary = _read_json(campaign_dir / "campaign-summary.json") or {}
    analysis = {
        "ok": True,
        "provider": "amex",
        "campaign_dir": str(campaign_dir),
        "campaign_name": summary.get("campaign_name"),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "baseline_available": baseline_available,
        "baseline_comparison_tolerance_seconds": float(tolerance_seconds),
        "baseline_trial": {
            "strategy": baseline.get("strategy"),
            "keepalive_interval_seconds": baseline.get("keepalive_interval_seconds"),
            "logout_elapsed_seconds": baseline.get("logout_elapsed_seconds"),
            "idle_warning_elapsed_seconds": baseline.get("idle_warning_elapsed_seconds"),
        }
        if baseline
        else None,
        "executive_conclusion": executive,
        "trials": trials,
        "recommendation": recommendation,
        "evidence_gaps": gaps,
        "source_artifacts": [
            "campaign-summary.json",
            "trials/*/experiment-summary.json",
            "trials/*/keepalive-status.json",
            "trials/*/keepalive-attempts.jsonl (when present)",
            "trials/*/recorder/recording.json",
        ],
    }

    resolved_output = Path(output_dir).expanduser().resolve() if output_dir else campaign_dir
    if write_outputs:
        analysis["output_paths"] = write_campaign_analysis_outputs(resolved_output, analysis)
    else:
        analysis["output_paths"] = {
            "json_path": str(resolved_output / CAMPAIGN_ANALYSIS_JSON),
            "csv_path": str(resolved_output / CAMPAIGN_ANALYSIS_CSV),
            "markdown_path": str(resolved_output / CAMPAIGN_ANALYSIS_MD),
        }
    analysis["output_dir"] = str(resolved_output)
    return analysis


def _extract_campaign_zip(zip_path: Path) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    temp_dir = tempfile.TemporaryDirectory(prefix="amex-campaign-analysis-")
    extract_root = Path(temp_dir.name)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(extract_root)

    # Prefer a nested campaign directory when present.
    candidates = [
        path
        for path in extract_root.rglob("campaign-summary.json")
        if path.is_file()
    ]
    if candidates:
        return candidates[0].parent, temp_dir
    trial_markers = list(extract_root.rglob("experiment-summary.json"))
    if trial_markers:
        # Use common parent of trial dirs when possible.
        parents = {path.parent.parent for path in trial_markers}
        if len(parents) == 1:
            return next(iter(parents)), temp_dir
        return trial_markers[0].parent.parent, temp_dir
    return extract_root, temp_dir


def analyze_campaign_zip(
    zip_path: Path,
    *,
    output_dir: Path | None = None,
    tolerance_seconds: float = DEFAULT_BASELINE_COMPARISON_TOLERANCE_SECONDS,
    write_outputs: bool = True,
) -> dict[str, Any]:
    """Analyze a campaign ZIP without mutating the archive."""
    zip_path = Path(zip_path).expanduser().resolve()
    if not zip_path.is_file():
        raise FileNotFoundError(f"Campaign ZIP not found: {zip_path}")

    extracted_dir, temp_dir = _extract_campaign_zip(zip_path)
    try:
        resolved_output = (
            Path(output_dir).expanduser().resolve()
            if output_dir is not None
            else zip_path.parent
        )
        analysis = analyze_campaign_directory(
            extracted_dir,
            output_dir=resolved_output,
            tolerance_seconds=tolerance_seconds,
            write_outputs=write_outputs,
        )
        analysis["campaign_zip"] = str(zip_path)
        analysis["source_mode"] = "zip"
        return analysis
    finally:
        temp_dir.cleanup()


def analyze_campaign_path(
    campaign_path: Path,
    *,
    output_dir: Path | None = None,
    tolerance_seconds: float = DEFAULT_BASELINE_COMPARISON_TOLERANCE_SECONDS,
    write_outputs: bool = True,
) -> dict[str, Any]:
    """Analyze a campaign directory or ZIP path."""
    campaign_path = Path(campaign_path).expanduser().resolve()
    if campaign_path.is_file() and campaign_path.suffix.lower() == ".zip":
        return analyze_campaign_zip(
            campaign_path,
            output_dir=output_dir,
            tolerance_seconds=tolerance_seconds,
            write_outputs=write_outputs,
        )
    if campaign_path.is_dir():
        # If a directory contains only the ZIP, still support analyzing beside it.
        analysis = analyze_campaign_directory(
            campaign_path,
            output_dir=output_dir,
            tolerance_seconds=tolerance_seconds,
            write_outputs=write_outputs,
        )
        analysis["source_mode"] = "directory"
        return analysis
    raise FileNotFoundError(f"Campaign path not found: {campaign_path}")


def run_analyze_campaign_command(
    campaign_path: Path,
    *,
    output_dir: Path | None = None,
    tolerance_seconds: float = DEFAULT_BASELINE_COMPARISON_TOLERANCE_SECONDS,
    print_fn: Any = None,
) -> dict[str, Any]:
    """CLI entry for analyze-campaign."""
    emit = print_fn or print
    analysis = analyze_campaign_path(
        campaign_path,
        output_dir=output_dir,
        tolerance_seconds=tolerance_seconds,
        write_outputs=True,
    )
    emit(format_campaign_analysis_terminal_summary(analysis), end="")
    return analysis
