"""Tests for AccountState shadow-mode projector."""

import json
import os
import sys

from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_state import (
    DATA_COMPLETE,
    DATA_PARTIAL,
    configure_account_state,
    ensure_account_state_tables,
    load_account_state,
    recompute_account_state,
    recompute_account_state_from_run,
)
from mighty.connection_state import CONNECTED
from mighty.pipeline_inspector import (
    ensure_pipeline_tables,
    finalize_run,
    finalize_sync_without_discovery,
    record_stage,
    start_run,
)
from mighty.pipeline_stages import (
    FAIL_LOGIN_REQUIRED,
    PipelineStageId,
    RunInitiator,
    RunStatus,
    StageStatus,
)


def _plain_decrypt(_uid: str, stored: str) -> dict:
    if stored.startswith("plain:"):
        return json.loads(stored[6:])
    return json.loads(stored)


@pytest.fixture()
def account_state_db(tmp_path):
    import sqlite3

    db_path = tmp_path / "account_state.db"
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
    conn.commit()
    configure_account_state(decrypt_fn=_plain_decrypt)
    yield conn
    conn.close()


def _insert_account(
    db,
    *,
    items=None,
    synced_at="2026-06-01T12:00:00+00:00",
    sync_status="ok",
    connection_status=CONNECTED,
    data_source="extension",
):
    payload = {
        "items": items or [],
        "sync_status": sync_status,
        "connection_status": connection_status,
        "data_source": data_source,
    }
    db.execute(
        """
        INSERT INTO account_data (
            user_id, source, display_name, icon, color, data_enc, synced_at,
            sync_status, connection_status, extraction_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "user-1",
            "amex",
            "American Express",
            "",
            "",
            "plain:" + json.dumps(payload),
            synced_at,
            sync_status,
            connection_status,
            "complete",
        ),
    )
    db.execute(
        "INSERT INTO account_credentials (user_id, source) VALUES (?, ?)",
        ("user-1", "amex"),
    )
    db.commit()


def _recent_iso(hours_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _seed_successful_pipeline(db, trusted_keys):
    verified_at = _recent_iso(1)
    trusted_at = _recent_iso(0)
    run_id = start_run(
        db,
        user_id="user-1",
        source="amex",
        initiator=RunInitiator.EXTENSION_SYNC.value,
        data_source="extension",
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.CONNECTION.value,
        started_at=verified_at,
        finished_at=verified_at,
        status=StageStatus.SUCCESS.value,
        artifacts={"session_verified": True},
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.VALIDATION.value,
        started_at=trusted_at,
        finished_at=trusted_at,
        status=StageStatus.SUCCESS.value,
        artifacts={"fields_in": 4, "fields_out": 4},
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at=trusted_at,
        finished_at=trusted_at,
        status=StageStatus.SUCCESS.value,
        artifacts={"trusted_keys": trusted_keys},
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=RunStatus.COMPLETE.value,
    )
    return run_id


class TestAccountStateProjection:
    def test_successful_trusted_observations_run(self, account_state_db):
        items = [
            {"key": "statement_balance", "label": "Balance", "value": "$1,234"},
            {"key": "payment_due_date", "label": "Due", "value": "Jul 15"},
            {"key": "credit_limit", "label": "Limit", "value": "$10,000"},
            {"key": "points_balance", "label": "Points", "value": "100,000"},
        ]
        _insert_account(account_state_db, items=items, synced_at=_recent_iso(0))
        trusted_keys = [i["key"] for i in items]
        _seed_successful_pipeline(account_state_db, trusted_keys)

        state = load_account_state(account_state_db, "user-1", "amex")
        assert state is not None
        assert state.connection_state == "connected"
        assert state.session_health == "healthy"
        assert state.data_status == DATA_COMPLETE
        assert state.field_count == 4
        assert "statement_balance" in state.observations_available
        assert "payment_due_date" in state.observations_available
        assert state.last_verified_at is not None
        assert state.last_data_refresh is not None
        assert state.access_method == "browser_session"
        assert state.confidence.score >= 50

    def test_needs_login_from_connection_failure(self, account_state_db):
        _insert_account(
            account_state_db,
            items=[{"key": "statement_balance", "label": "Balance", "value": "$500"}],
            sync_status="login_required",
            connection_status="needs_login",
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
            started_at="2026-06-28T11:00:00+00:00",
            finished_at="2026-06-28T11:00:01+00:00",
            status=StageStatus.FAILED.value,
            failure_reason=FAIL_LOGIN_REQUIRED,
            artifacts={"session_verified": False},
        )
        finalize_run(
            account_state_db,
            run_id,
            terminal_stage=PipelineStageId.CONNECTION.value,
            terminal_reason=FAIL_LOGIN_REQUIRED,
            run_status=RunStatus.FAILED.value,
        )

        state = load_account_state(account_state_db, "user-1", "amex")
        assert state.connection_state == "needs_login"
        assert state.session_health == "expired"
        assert state.next_recommended_action is not None
        assert state.next_recommended_action.kind == "login"

    def test_stale_data_retained_when_login_expires(self, account_state_db):
        stale_synced_at = "2026-06-01T12:00:00+00:00"
        items = [
            {"key": "statement_balance", "label": "Balance", "value": "$2,000"},
            {"key": "payment_due_date", "label": "Due", "value": "Jun 20"},
            {"key": "credit_limit", "label": "Limit", "value": "$8,000"},
        ]
        _insert_account(
            account_state_db,
            items=items,
            synced_at=stale_synced_at,
            sync_status="login_required",
            connection_status="needs_login",
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
            started_at="2026-06-28T12:00:00+00:00",
            finished_at="2026-06-28T12:00:01+00:00",
            status=StageStatus.FAILED.value,
            failure_reason=FAIL_LOGIN_REQUIRED,
        )
        finalize_run(
            account_state_db,
            run_id,
            terminal_stage=PipelineStageId.CONNECTION.value,
            terminal_reason=FAIL_LOGIN_REQUIRED,
            run_status=RunStatus.FAILED.value,
        )

        state = recompute_account_state(account_state_db, "user-1", "amex")
        assert state.connection_state == "needs_login"
        assert state.data_status in {DATA_PARTIAL, DATA_COMPLETE}
        assert state.last_data_refresh == stale_synced_at
        assert state.field_count >= 1
        assert "Jun" in state.status_line or "Needs login" in state.status_line

    def test_partial_data_state(self, account_state_db):
        items = [{"key": "statement_balance", "label": "Balance", "value": "$100"}]
        _insert_account(account_state_db, items=items)
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
            started_at="2026-06-28T10:00:00+00:00",
            finished_at="2026-06-28T10:00:01+00:00",
            status=StageStatus.SUCCESS.value,
        )
        record_stage(
            account_state_db,
            run_id,
            PipelineStageId.TRUSTED_OBSERVATIONS.value,
            started_at="2026-06-28T10:00:02+00:00",
            finished_at="2026-06-28T10:00:03+00:00",
            status=StageStatus.SUCCESS.value,
            artifacts={"trusted_keys": ["statement_balance"]},
        )
        finalize_run(
            account_state_db,
            run_id,
            terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
            run_status=RunStatus.COMPLETE.value,
        )

        state = load_account_state(account_state_db, "user-1", "amex")
        assert state.data_status == DATA_PARTIAL
        assert state.observations_available == ["statement_balance"]

    def test_recompute_from_finalize_sync_without_discovery(self, account_state_db):
        items = [
            {"key": "statement_balance", "label": "Balance", "value": "$50"},
            {"key": "payment_due_date", "label": "Due", "value": "Aug 1"},
        ]
        _insert_account(account_state_db, items=items)
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
            started_at="2026-06-28T09:00:00+00:00",
            finished_at="2026-06-28T09:00:01+00:00",
            status=StageStatus.SUCCESS.value,
        )
        finalize_sync_without_discovery(
            account_state_db,
            run_id,
            items=items,
            extraction_status="complete",
            has_structured_extractor=False,
        )

        state = recompute_account_state_from_run(account_state_db, run_id)
        assert state is not None
        assert state.provider == "amex"
        assert load_account_state(account_state_db, "user-1", "amex") is not None
