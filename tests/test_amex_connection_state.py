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
    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()

    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    email = f"amex_{secrets.token_hex(4)}@test.local"
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    with c.session_transaction() as sess:
        assert sess.get("user_id"), "signup did not establish a session"
    c.email = email
    return c


def _ctx(mighty):
    return dict(
        iso_fn=mighty.iso,
        encrypt_fn=mighty.encrypt_account_data,
        decrypt_fn=mighty.decrypt_account_data,
    )


def _user(mighty, client):
    with client.session_transaction() as sess:
        uid = sess.get("user_id")
    assert uid, "test client is not logged in — signup may have failed"
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT id, api_key FROM users WHERE id=?", (uid,),
        ).fetchone()
        return row["id"], row["api_key"]


def test_state_machine_happy_path(client):
    import app as mighty
    from mighty.connection_state import (
        CONNECTED,
        CONNECTING,
        NEEDS_LOGIN,
        WAITING_FOR_EXTENSION,
        advance_amex_to_waiting,
        amex_extension_connected,
        amex_extension_needs_login,
        get_amex_connection_status,
        start_amex_connect,
    )

    uid, _ = _user(mighty, client)
    with mighty.app.app_context():
        db = mighty.get_db()
        assert start_amex_connect(db, uid, **_ctx(mighty)) == CONNECTING
        row = db.execute(
            "SELECT connection_status, synced_at FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        assert row["connection_status"] == CONNECTING
        assert not row["synced_at"]
        assert advance_amex_to_waiting(db, uid, **_ctx(mighty)) == WAITING_FOR_EXTENSION
        assert amex_extension_needs_login(db, uid, **_ctx(mighty)) == NEEDS_LOGIN
        assert amex_extension_connected(
            db, uid, session_verified=True, **_ctx(mighty),
        ) == CONNECTED
        info = get_amex_connection_status(db, uid, decrypt_fn=mighty.decrypt_account_data)
        assert info["connection_status"] == CONNECTED
        assert info["label"] == "Connected"
        assert info["show_synced"] is False


def test_connected_requires_session_verified(client):
    import app as mighty
    from mighty.connection_state import (
        amex_extension_connected,
        advance_amex_to_waiting,
        start_amex_connect,
    )

    uid, _ = _user(mighty, client)
    with mighty.app.app_context():
        db = mighty.get_db()
        start_amex_connect(db, uid, **_ctx(mighty))
        advance_amex_to_waiting(db, uid, **_ctx(mighty))
        with pytest.raises(ValueError, match="session_verified"):
            amex_extension_connected(db, uid, **_ctx(mighty))


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
            amex_extension_connected(db, uid, session_verified=True, **_ctx(mighty))


def test_http_connect_flow(client):
    import app as mighty
    from mighty.connection_state import CONNECTED, CONNECTING, NEEDS_LOGIN, WAITING_FOR_EXTENSION

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
    r = client.post(
        "/api/extension/amex/needs-login",
        headers={"X-Mighty-Key": api_key},
    )
    assert r.status_code == 200
    assert r.get_json()["connection_status"] == NEEDS_LOGIN

    r = client.post(
        "/api/extension/amex/connected",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True},
    )
    assert r.status_code == 200
    assert r.get_json()["connection_status"] == CONNECTED

    r = client.get("/api/connect/amex/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["connection_status"] == CONNECTED
    assert body["show_synced"] is False
    assert body["is_synced"] is False
    assert body["extraction_status"] == "pending"


def test_connected_endpoint_rejects_missing_session_verified(client):
    import app as mighty
    from mighty.connection_state import WAITING_FOR_EXTENSION

    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    headers = {"X-CSRF-Token": csrf}
    client.post("/api/connect/amex", headers=headers)
    client.post("/api/connect/amex/waiting", headers=headers)
    _, api_key = _user(mighty, client)
    r = client.post("/api/extension/amex/connected", headers={"X-Mighty-Key": api_key})
    assert r.status_code == 400


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


def test_amex_never_show_synced_without_items(client):
    import app as mighty
    from mighty.connection_state import (
        amex_has_meaningful_items,
        amex_show_as_synced,
        amex_extension_connected,
        advance_amex_to_waiting,
        get_amex_connection_status,
        start_amex_connect,
    )

    assert amex_has_meaningful_items([]) is False
    assert amex_has_meaningful_items([{"label": "Points", "value": "—"}]) is False
    assert amex_has_meaningful_items([{"label": "Points", "value": "42,000"}]) is True
    assert amex_show_as_synced([{"label": "Points", "value": "42,000"}]) is True

    uid, _ = _user(mighty, client)
    with mighty.app.app_context():
        db = mighty.get_db()
        start_amex_connect(db, uid, **_ctx(mighty))
        advance_amex_to_waiting(db, uid, **_ctx(mighty))
        amex_extension_connected(db, uid, session_verified=True, **_ctx(mighty))
        info = get_amex_connection_status(
            db, uid, decrypt_fn=mighty.decrypt_account_data,
        )
        assert info["show_synced"] is False
