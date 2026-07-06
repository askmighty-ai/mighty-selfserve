"""Tests for shared Account Access Loop presentation."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from mighty.account_center_ui import (
    primary_action,
    resolve_primary_action_href,
    status_label,
)
from mighty.account_presentation import (
    build_access_loop_summary,
    is_recent_session_verification,
    resolve_account_presentation,
    resolve_presentation_from_status_signals,
)
from mighty.account_state import (
    ACCESS_BROWSER_SESSION,
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_NEEDS_LOGIN,
    DATA_COMPLETE,
    DATA_NONE,
    SESSION_EXPIRED,
    SESSION_HEALTHY,
    SESSION_UNKNOWN,
    AccountState,
    Confidence,
    ConfidenceFactors,
    recompute_account_state,
)
from mighty.account_lifecycle import resolve_account_lifecycle
from mighty.connection_state import CONNECTED, NEEDS_LOGIN as CONN_NEEDS_LOGIN
from mighty.provider_account import ProviderAccount
from mighty.user_copy import (
    ACCOUNT_STATE_CTAS,
    ACCOUNT_STATE_LABELS,
    ACCOUNT_STATE_NEEDS_ATTENTION,
    ACCOUNT_STATE_NEEDS_SIGN_IN,
    ACCOUNT_STATE_READY,
    ACCOUNT_STATE_UPDATING,
    CTA_FIX,
    CTA_SIGN_IN,
    CTA_UPDATING,
    CTA_VIEW,
    EXT_ACCOUNT_NEEDS_SIGN_IN_HINT,
)


def _state(**kwargs) -> AccountState:
    defaults = dict(
        user_id="u1",
        provider="amex",
        display_name="American Express",
        category="credit_card",
        access_method=ACCESS_BROWSER_SESSION,
        connection_state=CONN_CONNECTED,
        session_health=SESSION_HEALTHY,
        last_verified_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        data_status=DATA_COMPLETE,
        last_data_refresh=(datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
        observations_available=["statement_balance"],
        field_count=1,
        next_recommended_action=None,
        confidence=Confidence(level="high", score=90, factors=ConfidenceFactors()),
        status_line="Connected",
        is_actionable=False,
        updated_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
    )
    defaults.update(kwargs)
    return AccountState(**defaults)


def _login_state(**kwargs) -> AccountState:
    """Account with no fresh session/data — should resolve to Needs sign in."""
    return _state(
        connection_state=CONN_NEEDS_LOGIN,
        session_health=SESSION_EXPIRED,
        last_verified_at=None,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        data_status=DATA_NONE,
        sync_status="login_required",
        **kwargs,
    )


def _connecting_state(**kwargs) -> AccountState:
    """Account mid-connect without cached fresh data."""
    return _state(
        connection_state=CONN_CONNECTING,
        session_health=SESSION_UNKNOWN,
        last_verified_at=None,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        data_status=DATA_NONE,
        updated_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        **kwargs,
    )


class TestSharedLabels:
    def test_account_center_uses_shared_labels(self):
        assert status_label(_state()) == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_READY]
        assert status_label(_login_state()) == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN]
        assert status_label(_connecting_state()) == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING]
        assert status_label(_state(connection_state=CONN_NEEDS_LOGIN, last_verified_at=None)) == (
            ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN]
        )
        assert status_label(_state(data_status=DATA_NONE, last_data_refresh=None)) == (
            ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING]
        )

    def test_extension_projection_agrees_on_labels(self):
        lifecycle = resolve_account_lifecycle(
            "amex",
            in_credentials=True,
            account=ProviderAccount(
                source="amex",
                connection_status=CONN_NEEDS_LOGIN,
                sync_status="login_required",
            ),
        )
        presentation = resolve_presentation_from_status_signals(
            provider="amex",
            connection_status=CONN_NEEDS_LOGIN,
            sync_status="login_required",
            lifecycle_state=lifecycle.state,
            has_meaningful_data=False,
            last_verified_at=None,
            is_updating=False,
        )
        assert presentation.label == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN]
        assert presentation.extension_hint == EXT_ACCOUNT_NEEDS_SIGN_IN_HINT

        connected = resolve_presentation_from_status_signals(
            provider="amex",
            connection_status=CONNECTED,
            sync_status="login_required",
            lifecycle_state="connected",
            has_meaningful_data=False,
            last_verified_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            is_updating=False,
        )
        assert connected.label == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING]

    def test_access_loop_summary_uses_same_labels(self):
        presentations = [
            resolve_account_presentation(_state()),
            resolve_account_presentation(
                _state(connection_state=CONN_NEEDS_LOGIN, last_verified_at=None),
            ),
        ]
        loop = build_access_loop_summary(presentations)
        assert loop.ready == 1
        assert loop.needs_sign_in == 1
        assert "needs sign in" in loop.detail_lines[0]


class TestRecentVerificationOverridesNeedsLogin:
    def test_recent_verification_changes_projection(self):
        verified_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        state = _state(
            connection_state=CONN_CONNECTED,
            data_status=DATA_NONE,
            last_data_refresh=None,
            last_verified_at=verified_at,
            observations_available=[],
            field_count=0,
            updated_at=verified_at,
        )
        assert status_label(state) == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING]
        assert status_label(state) != ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN]

    def test_account_state_recompute_respects_connected_session(self, account_state_db):
        verified_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        import json

        from mighty.connection_state import CONNECTED as CONN_CONNECTED_VAL
        from mighty.pipeline_inspector import finalize_run, record_stage, start_run
        from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus

        payload = {
            "items": [],
            "sync_status": "login_required",
            "connection_status": CONN_CONNECTED_VAL,
            "data_source": "extension",
        }
        account_state_db.execute(
            """
            UPDATE account_data
            SET data_enc=?, sync_status=?, connection_status=?
            WHERE user_id=? AND source=?
            """,
            (
                "plain:" + json.dumps(payload),
                "login_required",
                CONN_CONNECTED_VAL,
                "user-1",
                "amex",
            ),
        )
        run_id = start_run(
            account_state_db,
            user_id="user-1",
            source="amex",
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
        )
        record_stage(
            account_state_db,
            run_id,
            PipelineStageId.CONNECTION.value,
            started_at=verified_at,
            finished_at=verified_at,
            status=StageStatus.SUCCESS.value,
            artifacts={"session_verified": True},
        )
        finalize_run(
            account_state_db,
            run_id,
            terminal_stage=PipelineStageId.CONNECTION.value,
            run_status=RunStatus.COMPLETE.value,
        )
        account_state_db.commit()

        state = recompute_account_state(account_state_db, "user-1", "amex")
        assert state.connection_state == CONN_CONNECTED
        assert status_label(state) != ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN]

    def test_is_recent_session_verification(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        assert is_recent_session_verification(recent, provider="amex") is True
        assert is_recent_session_verification(old, provider="amex") is False


class TestPresentationPrecedence:
    """Coherence fixes — fresh success beats stale failure."""

    def test_fresh_data_stale_login_required_is_ready(self):
        now = datetime.now(timezone.utc)
class TestCanonicalCtas:
    def test_sign_in_uses_provider_url(self):
        state = _state(
            connection_state=CONN_NEEDS_LOGIN,
            sync_status="login_required",
            last_data_refresh=(now - timedelta(minutes=5)).isoformat(),
            last_verified_at=(now - timedelta(hours=2)).isoformat(),
            observations_available=["statement_balance"],
            data_status=DATA_COMPLETE,
        )
        presentation = resolve_account_presentation(state)
        assert presentation.key == ACCOUNT_STATE_READY
        assert presentation.label == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_READY]

    def test_healthy_session_fresh_data_not_updating(self):
        now = datetime.now(timezone.utc)
        state = _state(
            provider="delta",
            connection_state=CONN_CONNECTED,
            session_health=SESSION_HEALTHY,
            last_verified_at=(now - timedelta(hours=1)).isoformat(),
            last_data_refresh=(now - timedelta(minutes=5)).isoformat(),
            observations_available=["miles_balance"],
            data_status=DATA_COMPLETE,
            extraction_status="pending",
            updated_at=(now - timedelta(hours=3)).isoformat(),
        )
        presentation = resolve_account_presentation(state)
        assert presentation.key == ACCOUNT_STATE_READY
        assert presentation.key != ACCOUNT_STATE_UPDATING

    def test_verified_session_refreshed_data_no_observations_not_contradictory(self):
        now = datetime.now(timezone.utc)
        state = _state(
            observations_available=[],
            field_count=0,
            data_status=DATA_NONE,
            last_verified_at=(now - timedelta(minutes=20)).isoformat(),
            last_data_refresh=(now - timedelta(minutes=20)).isoformat(),
            extraction_status=None,
            next_recommended_action=None,
        )
        presentation = resolve_account_presentation(state)
        assert presentation.key != ACCOUNT_STATE_NEEDS_ATTENTION
        assert presentation.key != ACCOUNT_STATE_NEEDS_SIGN_IN

    def test_stale_pending_with_fresh_data_is_ready(self):
        now = datetime.now(timezone.utc)
        state = _state(
            extraction_status="pending",
            updated_at=(now - timedelta(hours=3)).isoformat(),
            last_data_refresh=(now - timedelta(minutes=10)).isoformat(),
            observations_available=["statement_balance"],
            data_status=DATA_COMPLETE,
        )
        presentation = resolve_account_presentation(state)
        assert presentation.key == ACCOUNT_STATE_READY

    def test_stale_login_with_recent_connected_not_needs_sign_in(self):
        now = datetime.now(timezone.utc)
        state = _state(
            sync_status="login_required",
            connection_state=CONN_CONNECTED,
            last_verified_at=(now - timedelta(hours=1)).isoformat(),
            last_data_refresh=(now - timedelta(minutes=30)).isoformat(),
            observations_available=["statement_balance"],
            data_status=DATA_COMPLETE,
        )
        presentation = resolve_account_presentation(state)
        assert presentation.key != ACCOUNT_STATE_NEEDS_SIGN_IN
        assert presentation.key in {ACCOUNT_STATE_READY, ACCOUNT_STATE_UPDATING}

    def test_presentation_debug_fields_populated(self):
        now = datetime.now(timezone.utc)
        state = _state(
            sync_status="login_required",
            last_data_refresh=(now - timedelta(minutes=5)).isoformat(),
            observations_available=["statement_balance"],
            data_status=DATA_COMPLETE,
        )
        from mighty.account_presentation import resolve_account_presentation_with_debug

        presentation, debug = resolve_account_presentation_with_debug(state)
        assert presentation.key == ACCOUNT_STATE_READY
        assert debug.why_state
        assert debug.winning_signal
        assert "sync_status:login_required" in debug.ignored_stale_signals


class TestCanonicalCtas:
    def test_sign_in_uses_provider_url(self):
        state = _login_state()
        label, kind, disabled = primary_action(state)
        assert label == CTA_SIGN_IN
        assert disabled is False
        href, external = resolve_primary_action_href(
            kind,
            "amex",
            provider_login_url="https://www.americanexpress.com/en-us/account/login",
        )
        assert href == "https://www.americanexpress.com/en-us/account/login"
        assert external is True

    def test_updating_cta_disabled(self):
        state = _state(connection_state=CONN_CONNECTING, data_status=DATA_NONE, last_verified_at=None)
        label, kind, disabled = primary_action(state)
        assert label == CTA_UPDATING
        assert disabled is True

    def test_ready_uses_view_cta(self):
        label, kind, disabled = primary_action(_state())
        assert label == CTA_VIEW
        assert kind == "view"
        assert disabled is False

    def test_needs_attention_uses_fix_cta(self):
        state = _state(session_health=SESSION_EXPIRED, connection_state=CONN_CONNECTED)
        label, kind, disabled = primary_action(state)
        assert label == CTA_FIX
        assert disabled is False

    def test_one_cta_per_state(self):
        for key, cta in ACCOUNT_STATE_CTAS.items():
            if key == ACCOUNT_STATE_UPDATING:
                presentation = resolve_account_presentation(
                    _state(connection_state=CONN_CONNECTING, data_status=DATA_NONE, last_verified_at=None),
                )
            elif key == ACCOUNT_STATE_NEEDS_SIGN_IN:
                presentation = resolve_account_presentation(
                    _state(connection_state=CONN_NEEDS_LOGIN, last_verified_at=None),
                )
            elif key == ACCOUNT_STATE_READY:
                presentation = resolve_account_presentation(_state())
            else:
                presentation = resolve_account_presentation(
                    _state(session_health=SESSION_EXPIRED, connection_state=CONN_CONNECTED),
                )
            assert presentation.key == key
            assert presentation.cta_label == cta
            assert presentation.cta_disabled is (key == ACCOUNT_STATE_UPDATING)


@pytest.fixture()
def account_state_db(tmp_path):
    import sqlite3

    from mighty.account_state import configure_account_state, ensure_account_state_tables
    from mighty.pipeline_inspector import ensure_pipeline_tables

    db_path = tmp_path / "presentation.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            sync_running INTEGER DEFAULT 0,
            sync_current_source TEXT
        );
        CREATE TABLE account_data (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            display_name TEXT NOT NULL,
            icon TEXT,
            color TEXT,
            data_enc TEXT,
            synced_at TEXT,
            sync_failure_reason TEXT,
            sync_status TEXT,
            connection_status TEXT,
            extraction_status TEXT,
            PRIMARY KEY (user_id, source)
        );
        CREATE TABLE account_credentials (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (user_id, source)
        );
        """
    )
    ensure_pipeline_tables(conn)
    ensure_account_state_tables(conn)
    conn.execute("INSERT INTO users (id) VALUES ('user-1')")
    conn.execute(
        """
        INSERT INTO account_data (
            user_id, source, display_name, icon, color, data_enc, synced_at,
            sync_status, connection_status, extraction_status
        ) VALUES (?, 'amex', 'American Express', '', '', ?, NULL, 'login_required', 'connected', 'pending')
        """,
        ("user-1", 'plain:{"items":[],"sync_status":"login_required","connection_status":"connected","data_source":"extension"}'),
    )
    conn.execute(
        "INSERT INTO account_credentials (user_id, source) VALUES ('user-1', 'amex')",
    )
    conn.commit()

    def _plain_decrypt(_uid: str, stored: str) -> dict:
        import json

        if stored.startswith("plain:"):
            return json.loads(stored[6:])
        return json.loads(stored)

    configure_account_state(decrypt_fn=_plain_decrypt)
    yield conn
    conn.close()
