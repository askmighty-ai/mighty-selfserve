"""Route tests for capture capability admin pages."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import (
    ensure_pipeline_tables,
    finalize_run,
    record_inferred_client_stages,
    start_run,
)
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_capture_cap_admin.db")
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


def _seed_capture_run(db, *, source="amex"):
    run_id = start_run(
        db,
        user_id="u1",
        source=source,
        initiator=RunInitiator.INTERCEPT.value,
        data_source="extension",
    )
    raw = '\n\n=== API RESPONSE: https://api.amex.com/rewards ===\n{"points": 50000}'
    record_inferred_client_stages(
        db,
        run_id,
        sync_status="ok",
        sync_failure_reason=None,
        connection_status="connected",
        raw_text=raw,
        items=[],
        json_payload_chars=40,
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.CAPTURE.value,
        run_status=RunStatus.COMPLETE.value,
    )
    return run_id


def test_capture_capability_forbidden_for_non_admin(client):
    c, _ = client
    assert c.get("/admin/capture-capability").status_code == 403


def test_capture_capability_loads_for_admin(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get("/admin/capture-capability")
    assert r.status_code == 200
    assert b"Capture Capability" in r.data
    assert b"Needed" in r.data
    assert b"Present" in r.data
    assert b"Missing" in r.data


def test_capture_capability_detail_shows_matrix(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    import app as mighty

    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_pipeline_tables(db)
        run_id = _seed_capture_run(db, source="amex")

    r = c.get("/admin/capture-capability/amex")
    assert r.status_code == 200
    assert b"Why needed" in r.data
    assert b"Next Best Improvement" in r.data
    assert b"View latest successful capture" in r.data
    assert run_id[:8].encode() in r.data
    assert b"Network JSON" in r.data


def test_capture_capability_detail_not_found(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get("/admin/capture-capability/not_a_real_provider_xyz")
    assert r.status_code == 404
