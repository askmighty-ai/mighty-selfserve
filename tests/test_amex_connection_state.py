"""Unit tests for the Amex connection state machine."""

import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    # Re-init schema on the isolated DB (app module may already be loaded).
    import app as mighty
    with mighty.app.app_context():
        mighty.init_db()

    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    email = f"amex_{secrets.token_hex(4)}@test.local"
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    c.email = email
    return c


def _ctx(mighty):
    return dict(
        iso_fn=mighty.iso,
        encrypt_fn=mighty.encrypt_account_data,
        decrypt_fn=mighty.decrypt_account_data,
    )


def _user(mighty, client):
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT id, api_key FROM users WHERE email=?", (client.email,)
        ).fetchone()
        return row["id"], row["api_key"]


def test_state_machine_happy_path(client):
    import app as mighty
    from mighty.connection_state import (
        CONNECTED,
        CONNECTING,
        WAITING_FOR_EXTENSION,
        advance_amex_to_waiting,
        amex_extension_connected,
        get_amex_connection_status,
        start_amex_connect,
    )

    uid, _ = _user(mighty, client)
    with mighty.app.app_context():
        db = mighty.get_db()
        assert start_amex_connect(db, uid, **_ctx(mighty)) == CONNECTING
        row = db.execute(
            "SELECT connection_status FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        assert row["connection_status"] == CONNECTING
        assert advance_amex_to_waiting(db, uid, **_ctx(mighty)) == WAITING_FOR_EXTENSION
        assert amex_extension_connected(db, uid, **_ctx(mighty)) == CONNECTED
        info = get_amex_connection_status(db, uid, decrypt_fn=mighty.decrypt_account_data)
        assert info["connection_status"] == CONNECTED
        assert info["label"] == "Connected"


def test_invalid_transition(client):
    import app as mighty
    from mighty.connection_state import (
        InvalidAmexConnectionTransition,
        amex_extension_connected,
        start_amex_connect,
    )

    uid, _ = _user(mighty, client)
    with mighty.app.app_context():
        db = mighty.get_db()
        start_amex_connect(db, uid, **_ctx(mighty))
        with pytest.raises(InvalidAmexConnectionTransition):
            amex_extension_connected(db, uid, **_ctx(mighty))


def test_http_connect_flow(client):
    import app as mighty
    from mighty.connection_state import CONNECTED, CONNECTING, WAITING_FOR_EXTENSION

    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    headers = {"X-CSRF-Token": csrf}

    r = client.post("/api/connect/amex", headers=headers)
    assert r.status_code == 200
    assert r.get_json()["connection_status"] == CONNECTING

    r = client.post("/api/connect/amex/waiting", headers=headers)
    assert r.status_code == 200
    assert r.get_json()["connection_status"] == WAITING_FOR_EXTENSION

    _, api_key = _user(mighty, client)
    r = client.post("/api/extension/amex/connected", headers={"X-Mighty-Key": api_key})
    assert r.status_code == 200
    assert r.get_json()["connection_status"] == CONNECTED

    r = client.get("/api/connect/amex/status")
    assert r.status_code == 200
    assert r.get_json()["connection_status"] == CONNECTED


def test_extension_accounts_includes_connection_status(client):
    import app as mighty
    from mighty.connection_state import WAITING_FOR_EXTENSION

    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    headers = {"X-CSRF-Token": csrf}
    client.post("/api/connect/amex", headers=headers)
    client.post("/api/connect/amex/waiting", headers=headers)
    _, api_key = _user(mighty, client)
    r = client.get("/api/extension/accounts", headers={"X-Mighty-Key": api_key})
    assert r.status_code == 200
    amex = next(a for a in r.get_json() if a["source"] == "amex")
    assert amex["connection_status"] == WAITING_FOR_EXTENSION
