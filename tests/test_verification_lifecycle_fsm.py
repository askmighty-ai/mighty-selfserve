"""Verification lifecycle is a finite state machine.

Every cycle must end in exactly one terminal outcome. No row may remain
running (or otherwise active in the probe phase) past
VERIFICATION_MAX_DURATION_SECONDS.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.capability_state import CapabilityState, build_capability_view
from mighty.customer_account_access import CustomerAccountAccessView
from mighty.provider_access_manager import (
    complete_access_check_after_extraction,
    complete_provider_access_check,
    fail_provider_access_check,
    finish_provider_access_check,
    mark_provider_access_check_running,
    request_provider_access_check,
)
from mighty.provider_access_probe import (
    AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
    AUTH_LOGIN_PAGE,
    ensure_probe_tables,
)
from mighty.provider_session_state import ensure_provider_session_state_tables
from mighty.session_verification import (
    ACTIVE_VERIFICATION_LIFECYCLES,
    TERMINAL_VERIFICATION_LIFECYCLES,
    VERIFICATION_MAX_DURATION_SECONDS,
    VERIFICATION_TERMINAL_REASONS,
    ensure_session_verification_tables,
    expire_timed_out_verifications,
    get_latest_session_verification,
    session_verification_to_json,
)

UID = "user-lifecycle-fsm"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_provider_session_state_tables(conn)
    ensure_session_verification_tables(conn)
    ensure_probe_tables(conn)
    return conn


def _probe(*, auth_state: str = AUTH_AUTHENTICATED_NO_PRIVATE_DATA, **extra) -> dict:
    url = extra.pop(
        "url_visited",
        (
            "https://www.americanexpress.com/en-us/account/login"
            if auth_state == AUTH_LOGIN_PAGE
            else "https://global.americanexpress.com/overview"
        ),
    )
    payload = {
        "provider": "amex",
        "status": extra.pop("status", "ok"),
        "auth_state": auth_state,
        "url_visited": url,
        "final_url": extra.pop("final_url", url),
        "signed_in_detected": auth_state == AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
        "private_data_detected": bool(extra.pop("private_data_detected", False)),
        "evidence_type": "page",
        "evidence_snippet": "test",
        "failure_reason": extra.pop("failure_reason", None),
        "login_form_present": auth_state == AUTH_LOGIN_PAGE,
        "probed_at": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    return payload


def _timeout_view() -> CustomerAccountAccessView:
    return CustomerAccountAccessView(
        provider="amex",
        display_name="American Express",
        readiness="unverified",
        session_state="unknown",
        private_data_state="unknown",
        last_confirmed_at=None,
        active_verification_lifecycle="timed_out",
        discovered_from="Manual add",
        user_action_required=False,
        user_action_text=None,
        live_access="Unknown",
        private_data_label="Not yet seen",
        background_work="Timed out",
        meaning="Mighty cannot determine whether you are logged in.",
        status_label="Unknown",
    )


def test_max_duration_constant_is_finite():
    assert VERIFICATION_MAX_DURATION_SECONDS == 20
    assert VERIFICATION_MAX_DURATION_SECONDS > 0
    assert VERIFICATION_TERMINAL_REASONS == {
        "authenticated",
        "signed_out",
        "timeout",
        "navigation_failed",
        "cancelled",
        "unknown",
    }


def test_authenticated_becomes_terminal_authenticated():
    """1. authenticated → terminal authenticated."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    assert verification is not None
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    complete_provider_access_check(
        db,
        UID,
        _probe(private_data_detected=True),
        verification_id=vid,
    )
    mid = get_latest_session_verification(db, UID, "amex")
    assert mid is not None
    assert mid.lifecycle == "session_verified"
    complete_access_check_after_extraction(db, UID, vid, success=True)
    final = get_latest_session_verification(db, UID, "amex")
    assert final is not None
    assert final.lifecycle == "completed"
    assert final.lifecycle in TERMINAL_VERIFICATION_LIFECYCLES
    assert final.terminal_reason == "authenticated"
    assert final.completed_at is not None
    payload = session_verification_to_json(final)
    assert payload["verification_started_at"]
    assert payload["verification_completed_at"]
    assert payload["duration_ms"] is not None
    assert payload["terminal_reason"] == "authenticated"
    assert payload["terminal_source"]


def test_signed_out_becomes_terminal_signed_out():
    """2. signed out → terminal signed_out."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    assert verification is not None
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    complete_provider_access_check(
        db,
        UID,
        _probe(auth_state=AUTH_LOGIN_PAGE, failure_reason="login_required"),
        verification_id=vid,
    )
    final = get_latest_session_verification(db, UID, "amex")
    assert final is not None
    assert final.lifecycle == "completed"
    assert final.terminal_reason == "signed_out"
    assert final.completed_at is not None
    assert final.lifecycle not in ACTIVE_VERIFICATION_LIFECYCLES


def test_timeout_becomes_terminal_timeout():
    """3. timeout → terminal timeout (lifecycle timed_out)."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    assert verification is not None
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    old = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 1)
    ).isoformat()
    # Execution timeout is anchored on started_at once claimed.
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (old, old, vid),
    )
    db.commit()
    n = expire_timed_out_verifications(db, UID)
    assert n == 1
    final = get_latest_session_verification(db, UID, "amex")
    assert final is not None
    assert final.lifecycle == "timed_out"
    assert final.terminal_reason == "timeout"
    assert final.terminal_source == "server_timeout"
    assert final.completed_at is not None
    payload = session_verification_to_json(final)
    assert payload["duration_ms"] is not None
    assert payload["duration_ms"] >= VERIFICATION_MAX_DURATION_SECONDS * 1000


def test_navigation_failure_becomes_terminal_navigation_failed():
    """4. navigation failure → terminal navigation_failed."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    assert verification is not None
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    fail_provider_access_check(
        db,
        UID,
        error_message="probe_navigation_error",
        verification_id=vid,
        terminal_reason="navigation_failed",
        terminal_source="extension_navigation",
    )
    final = get_latest_session_verification(db, UID, "amex")
    assert final is not None
    assert final.lifecycle == "failed"
    assert final.terminal_reason == "navigation_failed"
    assert final.completed_at is not None


def test_cancelled_becomes_terminal_cancelled():
    """5. cancelled → terminal cancelled."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    assert verification is not None
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    finish_provider_access_check(
        db,
        UID,
        vid,
        lifecycle="failed",
        error_message="verification cancelled — tab closed",
        terminal_reason="cancelled",
        terminal_source="extension_tab_closed",
    )
    final = get_latest_session_verification(db, UID, "amex")
    assert final is not None
    assert final.lifecycle == "failed"
    assert final.terminal_reason == "cancelled"
    assert final.completed_at is not None


def test_exception_still_terminal():
    """6. exception → still terminal (unknown)."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    assert verification is not None
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    complete_provider_access_check(
        db,
        UID,
        _probe(
            auth_state="unknown",
            status="error",
            failure_reason="unexpected_exception",
            signed_in_detected=False,
            private_data_detected=False,
        ),
        verification_id=vid,
    )
    final = get_latest_session_verification(db, UID, "amex")
    assert final is not None
    assert final.lifecycle in TERMINAL_VERIFICATION_LIFECYCLES
    assert final.lifecycle != "running"
    assert final.terminal_reason in VERIFICATION_TERMINAL_REASONS
    assert final.completed_at is not None


def test_no_verification_remains_running_past_timeout():
    """7. no verification can remain running longer than the timeout."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    assert verification is not None
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    assert get_latest_session_verification(db, UID, "amex").lifecycle == "running"

    almost = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS - 1)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (almost, almost, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 0
    assert get_latest_session_verification(db, UID, "amex").lifecycle == "running"

    overdue = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 5)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (overdue, overdue, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 1
    final = get_latest_session_verification(db, UID, "amex")
    assert final.lifecycle == "timed_out"
    assert final.lifecycle not in ACTIVE_VERIFICATION_LIFECYCLES
    assert final.terminal_reason == "timeout"


def test_truth_dashboard_timeout_is_completed_not_in_progress():
    """Timed-out verification must never display 'Verification in progress'."""
    cap = build_capability_view(_timeout_view())
    assert cap.state == CapabilityState.LOGIN_UNKNOWN
    evidence_text = " ".join(e.text for e in cap.evidence)
    assert "Verification timed out." in evidence_text
    assert "No authenticated session observed." in evidence_text
    assert "No definitive signed-out evidence observed." in evidence_text
    assert "Verification completed without sufficient evidence." in evidence_text
    assert "Verification in progress" not in evidence_text


def test_complete_is_idempotent_once_terminal():
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    assert verification is not None
    vid = verification.verification_id
    finish_provider_access_check(
        db,
        UID,
        vid,
        terminal_reason="cancelled",
        terminal_source="test",
    )
    first = get_latest_session_verification(db, UID, "amex")
    finish_provider_access_check(
        db,
        UID,
        vid,
        terminal_reason="timeout",
        terminal_source="should_not_overwrite",
    )
    second = get_latest_session_verification(db, UID, "amex")
    assert first is not None and second is not None
    assert first.terminal_reason == "cancelled"
    assert second.terminal_reason == "cancelled"
    assert second.terminal_source == "test"
    assert second.completed_at == first.completed_at


def test_authenticated_near_deadline_beats_timeout():
    """Authenticated result accepted just before execution timeout wins."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    almost = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS - 0.1)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (almost, almost, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 0
    complete_provider_access_check(
        db, UID, _probe(private_data_detected=True), verification_id=vid
    )
    mid = get_latest_session_verification(db, UID, "amex")
    assert mid.lifecycle == "session_verified"
    # Probe ceiling must not kill mid-cycle extraction work.
    assert expire_timed_out_verifications(db, UID) == 0
    complete_access_check_after_extraction(db, UID, vid, success=True)
    final = get_latest_session_verification(db, UID, "amex")
    assert final.terminal_reason == "authenticated"
    # Late timeout attempt rejected.
    assert expire_timed_out_verifications(db, UID) == 0
    assert get_latest_session_verification(db, UID, "amex").terminal_reason == "authenticated"


def test_signed_out_near_deadline_beats_timeout():
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    almost = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS - 0.1)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (almost, almost, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 0
    complete_provider_access_check(
        db,
        UID,
        _probe(auth_state=AUTH_LOGIN_PAGE, failure_reason="login_required"),
        verification_id=vid,
    )
    final = get_latest_session_verification(db, UID, "amex")
    assert final.terminal_reason == "signed_out"
    assert expire_timed_out_verifications(db, UID) == 0
    assert get_latest_session_verification(db, UID, "amex").terminal_reason == "signed_out"


def test_timeout_wins_then_late_authenticated_rejected():
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    overdue = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 2)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (overdue, overdue, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 1
    assert get_latest_session_verification(db, UID, "amex").terminal_reason == "timeout"
    complete_provider_access_check(
        db, UID, _probe(private_data_detected=True), verification_id=vid
    )
    final = get_latest_session_verification(db, UID, "amex")
    assert final.lifecycle == "timed_out"
    assert final.terminal_reason == "timeout"
    assert final.terminal_source == "server_timeout"


def test_authenticated_wins_then_timeout_rejected():
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    complete_provider_access_check(
        db, UID, _probe(private_data_detected=True), verification_id=vid
    )
    complete_access_check_after_extraction(db, UID, vid, success=True)
    assert get_latest_session_verification(db, UID, "amex").terminal_reason == "authenticated"
    overdue = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 5)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (overdue, overdue, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 0
    final = get_latest_session_verification(db, UID, "amex")
    assert final.terminal_reason == "authenticated"
    assert final.lifecycle == "completed"


def test_cancel_cannot_overwrite_authenticated():
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    complete_provider_access_check(
        db, UID, _probe(private_data_detected=True), verification_id=vid
    )
    complete_access_check_after_extraction(db, UID, vid, success=True)
    finish_provider_access_check(
        db,
        UID,
        vid,
        terminal_reason="cancelled",
        terminal_source="extension_tab_closed",
    )
    final = get_latest_session_verification(db, UID, "amex")
    assert final.terminal_reason == "authenticated"
    assert final.terminal_source != "extension_tab_closed"


def test_duplicate_conflicting_terminal_first_wins():
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    finish_provider_access_check(
        db, UID, vid, terminal_reason="signed_out", terminal_source="first"
    )
    finish_provider_access_check(
        db, UID, vid, terminal_reason="timeout", terminal_source="second"
    )
    finish_provider_access_check(
        db, UID, vid, terminal_reason="navigation_failed", terminal_source="third"
    )
    final = get_latest_session_verification(db, UID, "amex")
    assert final.terminal_reason == "signed_out"
    assert final.terminal_source == "first"
    assert final.lifecycle == "completed"


def test_extension_crash_after_claim_server_terminates():
    """Extension claims then disappears — server execution timeout recovers."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    overdue = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 1)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification SET started_at=? WHERE verification_id=?",
        (overdue, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 1
    final = get_latest_session_verification(db, UID, "amex")
    assert final.lifecycle == "timed_out"
    assert final.terminal_reason == "timeout"
    assert final.terminal_source == "server_timeout"


def test_requested_never_claimed_queue_timeout():
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    assert get_latest_session_verification(db, UID, "amex").lifecycle == "requested"
    overdue = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 1)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification SET requested_at=? WHERE verification_id=?",
        (overdue, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 1
    final = get_latest_session_verification(db, UID, "amex")
    assert final.lifecycle == "timed_out"
    assert final.terminal_reason == "timeout"
    assert final.terminal_source == "server_timeout_queue"


def test_queue_wait_does_not_consume_execution_budget():
    """Old requested_at must not expire a freshly claimed running job."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    old_request = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 5)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification SET requested_at=? WHERE verification_id=?",
        (old_request, vid),
    )
    db.commit()
    mark_provider_access_check_running(db, UID, vid)
    # Fresh started_at — execution budget still open.
    assert expire_timed_out_verifications(db, UID) == 0
    assert get_latest_session_verification(db, UID, "amex").lifecycle == "running"


def test_authenticated_advances_without_probe_timeout_killing_extraction():
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    # Make probe-phase timestamps look overdue.
    overdue = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 5)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (overdue, overdue, vid),
    )
    db.commit()
    complete_provider_access_check(
        db, UID, _probe(private_data_detected=True), verification_id=vid
    )
    mid = get_latest_session_verification(db, UID, "amex")
    assert mid.lifecycle == "session_verified"
    # Probe timeout must not apply; extraction window still open.
    assert expire_timed_out_verifications(db, UID) == 0
    complete_access_check_after_extraction(db, UID, vid, success=True)
    assert get_latest_session_verification(db, UID, "amex").terminal_reason == "authenticated"


def test_old_stuck_production_row_reconciled():
    """Pre-deploy stuck running rows terminalize via server timeout path."""
    db = _db()
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    stuck = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=?, terminal_reason=NULL, terminal_source=NULL, "
        "completed_at=NULL WHERE verification_id=?",
        (stuck, stuck, vid),
    )
    db.commit()
    assert expire_timed_out_verifications(db, UID) == 1
    final = get_latest_session_verification(db, UID, "amex")
    assert final.lifecycle == "timed_out"
    assert final.terminal_reason == "timeout"
    assert final.completed_at is not None


def test_timeout_maps_to_login_unknown_never_signed_out():
    from mighty.provider_session_state import (
        get_provider_session_state,
        upsert_provider_session_state,
        SessionEvidence,
    )

    db = _db()
    upsert_provider_session_state(
        db,
        UID,
        SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type="session_verified",
            evidence_summary="prior connected",
            observed_at=datetime.now(timezone.utc),
            source="test",
            confidence="high",
        ),
    )
    verification = request_provider_access_check(db, UID, "amex")
    vid = verification.verification_id
    mark_provider_access_check_running(db, UID, vid)
    overdue = (
        datetime.now(timezone.utc)
        - timedelta(seconds=VERIFICATION_MAX_DURATION_SECONDS + 1)
    ).isoformat()
    db.execute(
        "UPDATE provider_session_verification "
        "SET requested_at=?, started_at=? WHERE verification_id=?",
        (overdue, overdue, vid),
    )
    db.commit()
    expire_timed_out_verifications(db, UID)
    assert get_provider_session_state(db, UID, "amex").state == "connected"
    cap = build_capability_view(_timeout_view())
    assert cap.state == CapabilityState.LOGIN_UNKNOWN
    assert cap.state != CapabilityState.SIGNED_OUT

