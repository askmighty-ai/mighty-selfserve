"""Integration tests for the Amex browser-session access loop."""

import json
import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.user_copy import (
    ACCOUNT_STATE_CHECKING,
    ACCOUNT_STATE_CONNECTED,
    ACCOUNT_STATE_LABELS,
    ACCOUNT_STATE_NEEDS_LOGIN,
    ACCOUNT_STATE_NO_DATA,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_amex_access_loop.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()

    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    email = f"amex_loop_{secrets.token_hex(4)}@test.local"
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
    assert uid, "test client is not logged in"
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT id, api_key FROM users WHERE id=?", (uid,),
        ).fetchone()
        return row["id"], row["api_key"]


def _csrf_headers(client):
    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    return {"X-CSRF-Token": csrf}


def _start_amex_connect(client):
    headers = _csrf_headers(client)
    r = client.post("/api/connect/amex", headers=headers)
    assert r.status_code == 200
    r = client.post("/api/connect/amex/waiting", headers=headers)
    assert r.status_code == 200


def _post_amex_connected(client, api_key):
    return client.post(
        "/api/extension/amex/connected",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True},
    )


def test_connected_endpoint_updates_account_state(client):
    import app as mighty
    from mighty.account_state import load_account_state
    from mighty.connection_state import CONNECTED

    _start_amex_connect(client)
    uid, api_key = _user(mighty, client)

    r = _post_amex_connected(client, api_key)
    assert r.status_code == 200
    body = r.get_json()
    assert body["connection_status"] == CONNECTED

    with mighty.app.app_context():
        state = load_account_state(mighty.get_db(), uid, "amex")
        assert state is not None
        assert state.connection_state == "connected"
        assert state.last_verified_at is not None


def test_stale_login_required_does_not_override_connected(client):
    import app as mighty
    from mighty.account_state import load_account_state, recompute_account_state
    from mighty.connection_state import CONNECTED, NEEDS_LOGIN

    _start_amex_connect(client)
    uid, api_key = _user(mighty, client)

    with mighty.app.app_context():
        db = mighty.get_db()
        stale_payload = mighty.encrypt_account_data(uid, {
            "items": [],
            "sync_status": "login_required",
            "connection_status": NEEDS_LOGIN,
        })
        db.execute(
            """
            UPDATE account_data
            SET data_enc=?, sync_status=?, connection_status=?
            WHERE user_id=? AND source='amex'
            """,
            (stale_payload, "login_required", NEEDS_LOGIN, uid),
        )
        db.commit()

    r = _post_amex_connected(client, api_key)
    assert r.status_code == 200

    with mighty.app.app_context():
        db = mighty.get_db()
        row = db.execute(
            "SELECT sync_status, connection_status FROM account_data "
            "WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        assert row["connection_status"] == CONNECTED
        assert row["sync_status"] == "ok"

        state = recompute_account_state(db, uid, "amex")
        assert state.connection_state == "connected"
        assert state.last_verified_at is not None


def test_account_center_shows_checking_or_connected_after_connect(client):
    import app as mighty
    from mighty.account_center_ui import status_label
    from mighty.account_state import load_account_state

    _start_amex_connect(client)
    uid, api_key = _user(mighty, client)

    with mighty.app.app_context():
        waiting_state = load_account_state(mighty.get_db(), uid, "amex")
        if waiting_state:
            waiting_label = status_label(waiting_state)
        else:
            from mighty.account_state import recompute_account_state
            waiting_state = recompute_account_state(mighty.get_db(), uid, "amex")
            waiting_label = status_label(waiting_state)
        assert waiting_label == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING]

    r = client.get("/account-center")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_LOGIN] not in html

    r = _post_amex_connected(client, api_key)
    assert r.status_code == 200

    with mighty.app.app_context():
        state = load_account_state(mighty.get_db(), uid, "amex")
        label = status_label(state)
        assert label in {
            ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CONNECTED],
            ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NO_DATA],
            ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING],
        }

    r = client.get("/account-center")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_LOGIN] not in html
    assert any(
        text in html
        for text in (
            ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CONNECTED],
            ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NO_DATA],
            ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING],
        )
    )


def test_refresh_queued_after_connection_verification(client):
    import app as mighty
    from mighty.provider_account import EXTRACTION_PENDING

    _start_amex_connect(client)
    _, api_key = _user(mighty, client)

    r = _post_amex_connected(client, api_key)
    assert r.status_code == 200
    body = r.get_json()
    assert body["extraction_status"] == EXTRACTION_PENDING
    assert body["refresh_queued"] is True

    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT extraction_status FROM account_data WHERE user_id=? AND source='amex'",
            (_user(mighty, client)[0],),
        ).fetchone()
        assert row["extraction_status"] == EXTRACTION_PENDING
