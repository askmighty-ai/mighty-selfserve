"""Regression tests for load_accounts performance (Fernet cache, bulk lifecycle)."""

import json
import os
import secrets
import sys
import time

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_load_accounts.db")
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
    c.post(
        "/signup",
        data={
            "email": f"perf_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _seed_accounts(mighty, uid, count: int):
    db = mighty.get_db()
    now = mighty.iso()
    for i in range(count):
        src = f"provider_{i}"
        data_enc = mighty.encrypt_account_data(uid, {
            "items": [{"key": "balance", "label": "Balance", "value": f"${i}"}],
            "sync_status": "ok",
        })
        extra_enc = mighty.encrypt_cred(uid, json.dumps({"enabled_fields": []}))
        db.execute(
            "INSERT INTO account_credentials "
            "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, src, "", "", extra_enc, now, now),
        )
        db.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, "
            "connection_status, extraction_status, sync_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, src, src, "x", "#fff", data_enc, now, "connected", "complete", "ok"),
        )
    db.commit()


def test_fernet_derived_once_per_user():
    """PBKDF2 key derivation must not repeat on every decrypt."""
    import app as mighty

    uid = secrets.token_hex(8)
    blob = mighty.encrypt_account_data(uid, {"items": []})
    mighty._fernet_data_cache.clear()

    t0 = time.perf_counter()
    for _ in range(20):
        mighty.decrypt_account_data(uid, blob)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 200, f"20 decrypts took {elapsed_ms:.0f}ms — Fernet likely not cached"


def test_credentials_under_500ms_with_many_accounts(client, capsys):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        _seed_accounts(mighty, uid, 25)

    t0 = time.perf_counter()
    r = client.get("/credentials")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert r.status_code == 200
    assert elapsed_ms < 500, f"/credentials took {elapsed_ms:.0f}ms"
    out = capsys.readouterr().out
    assert "[LoadAccountsProfile] /credentials" in out
    assert "top3:" in out


def test_dashboard_under_500ms_with_many_accounts(client, capsys):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        _seed_accounts(mighty, uid, 25)
        db = mighty.get_db()
        now = mighty.iso()
        db.execute(
            "INSERT INTO actions (id, user_id, action_type, label, status, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (secrets.token_hex(8), uid, "test", "Test", "pending", now),
        )
        db.commit()

    t0 = time.perf_counter()
    r = client.get("/dashboard")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert r.status_code == 200
    assert elapsed_ms < 500, f"/dashboard took {elapsed_ms:.0f}ms"
    out = capsys.readouterr().out
    assert "[LoadAccountsProfile] /dashboard" in out
