"""Route tests for AccountState shadow admin page."""

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_account_state_admin.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
        email = f"admin_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c, email


def _seed_account_and_pipeline(db, uid):
    ensure_pipeline_tables(db)
    payload = {
        "items": [{"key": "statement_balance", "label": "Balance", "value": "$900"}],
        "sync_status": "ok",
        "data_source": "extension",
    }
    import app as mighty

    stub = mighty.encrypt_account_data(uid, payload)
    db.execute(
        """
        INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at)
        VALUES (?, 'amex', '', '', '', '2026-01-01', '2026-01-01')
        """,
        (uid,),
    )
    db.execute(
        """
        INSERT INTO account_data (
            user_id, source, display_name, icon, color, data_enc, synced_at, sync_status, extraction_status
        ) VALUES (?, 'amex', 'American Express', '', '', ?, '2026-06-01T00:00:00+00:00', 'ok', 'complete')
        """,
        (uid, stub),
    )
    run_id = start_run(
        db,
        user_id=uid,
        source="amex",
        initiator=RunInitiator.EXTENSION_SYNC.value,
        data_source="extension",
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.CONNECTION.value,
        started_at="2026-06-01T00:00:00+00:00",
        finished_at="2026-06-01T00:00:01+00:00",
        status=StageStatus.SUCCESS.value,
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at="2026-06-01T00:00:02+00:00",
        finished_at="2026-06-01T00:00:03+00:00",
        status=StageStatus.SUCCESS.value,
        artifacts={"trusted_keys": ["statement_balance"]},
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=RunStatus.COMPLETE.value,
    )
    db.commit()


def test_account_state_admin_forbidden_for_non_admin(client):
    c, _ = client
    assert c.get("/admin/account-state").status_code == 403


def test_account_state_admin_renders(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    import app as mighty

    with c.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        _seed_account_and_pipeline(mighty.get_db(), uid)

    r = c.get("/admin/account-state")
    assert r.status_code == 200
    assert b"Account State (shadow)" in r.data
    assert b"browser session" in r.data or b"browser_session" in r.data
    assert b"connection" in r.data.lower()
    assert b"confidence" in r.data.lower()
    assert b"amex" in r.data or b"American Express" in r.data
