"""Route tests for /account-center."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_account_center.db")
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
        email = f"acc_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c


def _seed_amex(db, uid):
    ensure_pipeline_tables(db)
    import app as mighty

    payload = {
        "items": [{"key": "statement_balance", "label": "Balance", "value": "$900"}],
        "sync_status": "ok",
        "data_source": "extension",
    }
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
        ) VALUES (?, 'amex', 'American Express', '💳', '#e8f0fe', ?, '2026-06-01T00:00:00+00:00', 'ok', 'complete')
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


def test_account_center_requires_login(client):
    import app as mighty

    r = mighty.app.test_client().get("/account-center")
    assert r.status_code in (302, 401)


def test_account_center_renders_cards(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        _seed_amex(mighty.get_db(), uid)
    r = client.get("/account-center")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Connections" in html
    assert "American Express" in html
    assert "acc-card" in html
    assert "Extension" in html
    assert "pipeline" not in html.lower()
    assert "extraction" not in html.lower()


def test_credentials_page_still_exists(client):
    r = client.get("/credentials")
    assert r.status_code == 200
