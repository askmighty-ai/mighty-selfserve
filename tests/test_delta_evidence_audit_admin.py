"""Route tests for Delta evidence audit admin pages."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_delta_audit_admin.db")
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


def _seed_delta_run(db, *, user_id, run_status=RunStatus.COMPLETE.value):
    run_id = start_run(
        db,
        user_id=user_id,
        source="delta",
        initiator=RunInitiator.EXTENSION_SYNC.value,
        data_source="extension",
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status=StageStatus.SUCCESS.value,
        artifacts={"trusted_keys": ["elite_status"], "trusted_count": 1},
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=run_status,
    )
    return run_id


def test_delta_audit_forbidden_for_non_admin(client):
    c, _ = client
    assert c.get("/admin/delta-evidence-audit").status_code == 403


def test_delta_audit_list_for_admin(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    import app as mighty

    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_pipeline_tables(db)
        with c.session_transaction() as sess:
            uid = sess["user_id"]
        _seed_delta_run(db, user_id=uid)

    r = c.get("/admin/delta-evidence-audit")
    assert r.status_code == 200
    assert b"Delta Evidence Audit" in r.data
    assert b"Trusted obs" in r.data


def test_delta_audit_detail_not_found(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get("/admin/delta-evidence-audit/does-not-exist")
    assert r.status_code == 404


def test_delta_audit_detail_shows_comparison(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    import app as mighty

    fixtures = os.path.join(os.path.dirname(__file__), "fixtures")
    with open(os.path.join(fixtures, "delta_companion_cert.txt")) as f:
        raw_text = f.read()

    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_pipeline_tables(db)
        with c.session_transaction() as sess:
            uid = sess["user_id"]
        run_id = _seed_delta_run(db, user_id=uid)
        account_payload = {
            "name": "Delta",
            "icon": "✈",
            "color": "#003366",
            "status": "ok",
            "items": [{"key": "elite_status", "label": "Status", "value": "Diamond"}],
            "raw_text": raw_text,
        }
        db.execute(
            """
            INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at)
            VALUES (?, 'delta', 'Delta', '✈', '#003366', ?, '2026-01-01T00:00:00+00:00')
            ON CONFLICT(user_id, source) DO UPDATE SET data_enc=excluded.data_enc
            """,
            (uid, mighty.encrypt_account_data(uid, account_payload)),
        )
        db.commit()

    r = c.get(f"/admin/delta-evidence-audit/{run_id}")
    assert r.status_code == 200
    assert b"Observation comparison" in r.data
    assert b"SkyMiles balance" in r.data
    assert b"API RESPONSE" in r.data
    assert b"Page / URL blocks" in r.data
