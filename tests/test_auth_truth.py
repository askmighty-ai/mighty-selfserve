"""Unit tests for AuthTruth projector (RFC v2 §3 / PR1)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.access_state_publication import SCHEMA_VERSION, serialize_access_state
from mighty.auth_truth import (
    ACCESS_API,
    ACCESS_BROWSER_SESSION,
    ACCESS_MANAGED_RUNTIME,
    AuthInterruption,
    EvidenceClass,
    ensure_auth_truth_tables,
    load_auth_truth,
    normalize_access_method,
    project_auth_truth,
    recompute_auth_truth,
    replay_auth_truth,
)
from mighty.authentication_state import AuthenticationState
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_HEALTHY,
    BROWSER_STATUS_HEALTHY,
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_IDLE,
    RUNTIME_STATUS_RUNNING,
    AccessState,
)
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)
from mighty.runtime_access_state import (
    ensure_runtime_access_state_tables,
    upsert_runtime_access_state,
)


FIXED_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
FIXED_PROJECTED_AT = "2026-07-20T12:00:00+00:00"


@pytest.fixture()
def db(tmp_path):
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "auth_truth.db"))
    conn.row_factory = sqlite3.Row
    ensure_provider_session_state_tables(conn)
    ensure_runtime_access_state_tables(conn)
    ensure_auth_truth_tables(conn)
    yield conn
    conn.close()


def _iso(hours_ago: float = 0.0) -> str:
    return (FIXED_NOW - timedelta(hours=hours_ago)).replace(microsecond=0).isoformat()


def _write_pss(
    db,
    *,
    state: str,
    evidence_type: str,
    confidence: str = "high",
    hours_ago: float = 0.0,
    source: str = "access_manager",
):
    upsert_provider_session_state(
        db,
        "user-1",
        SessionEvidence(
            provider="amex",
            state=state,
            evidence_type=evidence_type,
            evidence_summary=f"{evidence_type} evidence",
            observed_at=FIXED_NOW - timedelta(hours=hours_ago),
            source=source,
            confidence=confidence,
        ),
    )


def _runtime_payload(**overrides):
    state = AccessState(
        provider="amex",
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
        authentication_state="SIGNED_IN",
        access_health=ACCESS_HEALTH_HEALTHY,
        runtime_started_at="2026-07-20T09:55:00+00:00",
        authenticated_session_started_at="2026-07-20T10:00:00+00:00",
        autonomous_since_at="2026-07-20T10:00:00+00:00",
        authentication_state_changed_at="2026-07-20T10:00:00+00:00",
        last_verification_at="2026-07-20T11:00:00+00:00",
        last_keepalive_at="2026-07-20T11:05:00+00:00",
        ready_for_extraction=True,
        ready_for_connector=True,
        updated_at="2026-07-20T11:06:00+00:00",
    )
    payload = serialize_access_state(state, runtime_instance_id="inst-amex-1")
    payload["schema_version"] = SCHEMA_VERSION
    payload.update(overrides)
    return payload


def _seed_runtime(db, **overrides):
    payload = _runtime_payload(**overrides)
    # Projector may consume optional RFC §3.4 keys even when ingest omits them.
    result = upsert_runtime_access_state(db, "user-1", payload)
    assert result["accepted"] is True
    # Re-store full payload (including optional keys stripped by validate path
    # if caller passed them only via overrides after serialize).
    if any(k in overrides for k in ("needs_human", "needs_human_reason", "interruption_expected")):
        db.execute(
            """
            UPDATE runtime_access_state
            SET payload_json=?
            WHERE user_id=? AND provider=?
            """,
            (
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
                "user-1",
                "amex",
            ),
        )
        db.commit()
    return payload


class TestNormalizeAccessMethod:
    def test_mighty_login_maps_to_managed_runtime(self):
        assert normalize_access_method("mighty_login") == ACCESS_MANAGED_RUNTIME

    def test_unknown_defaults_to_browser_session(self):
        assert normalize_access_method("weird") == ACCESS_BROWSER_SESSION


class TestBrowserSessionProjection:
    def test_definitive_signed_out_needs_human_login(self, db):
        _write_pss(db, state="signed_out", evidence_type="login_page")
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.state == AuthenticationState.SIGNED_OUT
        assert truth.evidence_class == EvidenceClass.DEFINITIVE
        assert truth.evidence_source == "access_manager"
        assert truth.needs_human is True
        assert truth.needs_human_reason == "login"
        assert truth.interruption == AuthInterruption.LOGIN
        assert truth.stale is False

    def test_definitive_signed_in(self, db):
        _write_pss(db, state="connected", evidence_type="session_verified")
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.state == AuthenticationState.SIGNED_IN
        assert truth.needs_human is False
        assert truth.interruption == AuthInterruption.NONE

    def test_weak_evidence_never_yields_terminal(self, db):
        _write_pss(
            db,
            state="connected",
            evidence_type="authenticated_page",
            confidence="medium",
        )
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.evidence_class == EvidenceClass.WEAK
        assert truth.state == AuthenticationState.LOGIN_UNKNOWN
        assert truth.needs_human is False

    def test_transport_error_is_login_unknown(self, db):
        _write_pss(db, state="error", evidence_type="probe_error", confidence="high")
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.state == AuthenticationState.LOGIN_UNKNOWN
        assert truth.needs_human is False

    def test_stale_does_not_flip_signed_in_to_signed_out(self, db):
        _write_pss(
            db,
            state="connected",
            evidence_type="session_verified",
            hours_ago=48,
        )
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
            evidence_ttl_seconds=24 * 3600,
        )
        assert truth.state == AuthenticationState.SIGNED_IN
        assert truth.stale is True
        assert truth.needs_human is False

    def test_mfa_interruption_from_evidence_type(self, db):
        _write_pss(db, state="signed_out", evidence_type="mfa_required")
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.needs_human is True
        assert truth.interruption == AuthInterruption.MFA
        assert truth.needs_human_reason == "login"

    def test_missing_pss_is_none_evidence(self, db):
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.evidence_class == EvidenceClass.NONE
        assert truth.state == AuthenticationState.LOGIN_UNKNOWN


class TestManagedRuntimeProjection:
    def test_signed_in_without_needs_human_fields(self, db):
        _seed_runtime(db)
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_MANAGED_RUNTIME,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.state == AuthenticationState.SIGNED_IN
        assert truth.evidence_source == "runtime_publication"
        assert truth.evidence_class == EvidenceClass.DEFINITIVE
        assert truth.needs_human is False
        assert truth.interruption == AuthInterruption.NONE

    def test_needs_human_mfa_from_publication(self, db):
        _seed_runtime(
            db,
            authentication_state="SIGNED_IN",
            needs_human=True,
            needs_human_reason="mfa",
            interruption_expected=False,
            recovery_planner_status=RECOVERY_STATUS_AWAITING_USER,
        )
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_MANAGED_RUNTIME,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.needs_human is True
        assert truth.interruption == AuthInterruption.MFA
        assert truth.needs_human_reason == "mfa"
        assert truth.interruption_expected is False

    def test_does_not_infer_needs_human_from_recovery_awaiting_user(self, db):
        _seed_runtime(
            db,
            recovery_state="awaiting_user",
            # serialize uses recovery_planner_status; ensure awaiting_user in payload
        )
        # Force recovery_state on stored payload without needs_human.
        row = db.execute(
            "SELECT payload_json FROM runtime_access_state WHERE user_id=? AND provider=?",
            ("user-1", "amex"),
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload["recovery_state"] = "awaiting_user"
        payload.pop("needs_human", None)
        db.execute(
            "UPDATE runtime_access_state SET payload_json=? WHERE user_id=? AND provider=?",
            (json.dumps(payload, separators=(",", ":"), sort_keys=True), "user-1", "amex"),
        )
        db.commit()

        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_MANAGED_RUNTIME,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.needs_human is False
        assert truth.interruption == AuthInterruption.NONE

    def test_mighty_login_alias_uses_runtime_publication(self, db):
        _seed_runtime(db, authentication_state="SIGNED_OUT")
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method="mighty_login",
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.access_method == ACCESS_MANAGED_RUNTIME
        assert truth.state == AuthenticationState.SIGNED_OUT
        # Without RFC needs_human on payload, do not invent human-need.
        assert truth.needs_human is False


class TestPrimaryMethodIsolation:
    def test_browser_primary_ignores_runtime_needs_human(self, db):
        _write_pss(db, state="connected", evidence_type="session_verified")
        _seed_runtime(
            db,
            needs_human=True,
            needs_human_reason="mfa",
        )
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.state == AuthenticationState.SIGNED_IN
        assert truth.needs_human is False
        assert truth.evidence_source == "access_manager"

    def test_api_method_has_no_evidence(self, db):
        _write_pss(db, state="signed_out", evidence_type="login_page")
        truth = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_API,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert truth.access_method == ACCESS_API
        assert truth.evidence_class == EvidenceClass.NONE
        assert truth.needs_human is False


class TestPersistenceAndReplay:
    def test_recompute_persists_and_load_roundtrip(self, db):
        _write_pss(db, state="signed_out", evidence_type="login_required")
        truth = recompute_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        loaded = load_auth_truth(db, "user-1", "amex")
        assert loaded is not None
        assert loaded.to_dict() == truth.to_dict()

    def test_replay_same_stream_is_identical(self, db):
        _write_pss(db, state="connected", evidence_type="session_api")
        first = replay_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        second = replay_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert first.to_dict() == second.to_dict()

        # Wipe projection store; reconstruct from source publications only.
        db.execute("DELETE FROM auth_truth")
        db.commit()
        assert load_auth_truth(db, "user-1", "amex") is None

        rebuilt = replay_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_BROWSER_SESSION,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert rebuilt.to_dict() == first.to_dict()

    def test_runtime_replay_identical_with_needs_human(self, db):
        _seed_runtime(
            db,
            needs_human=True,
            needs_human_reason="captcha",
            interruption_expected=True,
        )
        a = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_MANAGED_RUNTIME,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        b = project_auth_truth(
            db,
            "user-1",
            "amex",
            access_method=ACCESS_MANAGED_RUNTIME,
            now=FIXED_NOW,
            projected_at=FIXED_PROJECTED_AT,
        )
        assert a.to_dict() == b.to_dict()
        assert a.interruption == AuthInterruption.CAPTCHA
        assert a.interruption_expected is True
