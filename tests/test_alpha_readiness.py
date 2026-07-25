"""Regression tests from alpha-readiness audit (onboarding, sync, disconnect, extension)."""

import json
import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_alpha.db")
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
            "email": f"alpha_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _user_api(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT id, api_key FROM users WHERE id=?", (uid,),
        ).fetchone()
        return row["id"], row["api_key"]


def _insert_account(client, source, *, sync_status="ok", connection_status=None):
    import app as mighty

    uid, _ = _user_api(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        payload = {"items": [], "sync_status": sync_status}
        if connection_status:
            payload["connection_status"] = connection_status
        stub = mighty.encrypt_account_data(uid, payload)
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, source, "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, source, source.title(), "?", "#eee", stub, now, connection_status or ""),
        )
        db.commit()
    return uid


def test_sync_failure_login_required_sets_connection_status_for_amex(client):
    import app as mighty
    from mighty.connection_state import NEEDS_LOGIN

    uid, api_key = _user_api(client)
    _insert_account(client, "amex", sync_status="ok")

    r = client.post(
        "/api/sync/failure",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"source": "amex", "reason": "login_required"}),
    )
    assert r.status_code == 200
    assert r.get_json()["updated"] is True

    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT connection_status, data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        assert row["connection_status"] == NEEDS_LOGIN
        data = mighty.decrypt_account_data(uid, row["data_enc"])
        assert data["sync_status"] == "login_required"


def test_sync_failure_login_required_skips_connection_status_for_non_amex(client):
    import app as mighty

    uid, api_key = _user_api(client)
    _insert_account(client, "delta", sync_status="ok", connection_status="")

    r = client.post(
        "/api/sync/failure",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"source": "delta", "reason": "login_required"}),
    )
    assert r.status_code == 200

    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT connection_status, data_enc FROM account_data WHERE user_id=? AND source='delta'",
            (uid,),
        ).fetchone()
        assert row["connection_status"] == ""
        data = mighty.decrypt_account_data(uid, row["data_enc"])
        assert data["sync_status"] == "login_required"


def test_login_cleared_resets_amex_connection_status(client):
    import app as mighty
    from mighty.connection_state import CONNECTED

    uid, api_key = _user_api(client)
    _insert_account(client, "amex", sync_status="login_required", connection_status="needs_login")

    r = client.post(
        "/api/sync/login-cleared",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"source": "amex"}),
    )
    assert r.status_code == 200
    assert r.get_json()["updated"] is True

    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT connection_status, data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        assert row["connection_status"] == CONNECTED
        data = mighty.decrypt_account_data(uid, row["data_enc"])
        assert data["sync_status"] == "ok"


def test_login_cleared_does_not_set_connected_for_non_amex(client):
    import app as mighty

    uid, api_key = _user_api(client)
    _insert_account(client, "delta", sync_status="login_required", connection_status="")

    r = client.post(
        "/api/sync/login-cleared",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"source": "delta"}),
    )
    assert r.status_code == 200
    assert r.get_json()["updated"] is True

    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT connection_status, data_enc FROM account_data WHERE user_id=? AND source='delta'",
            (uid,),
        ).fetchone()
        assert row["connection_status"] == ""
        data = mighty.decrypt_account_data(uid, row["data_enc"])
        assert data["sync_status"] == "ok"


def test_credentials_delete_resets_email_suggestion_added(client):
    import app as mighty

    uid, _ = _user_api(client)
    _insert_account(client, "amex")
    with mighty.app.app_context():
        db = mighty.get_db()
        db.execute(
            "INSERT INTO email_suggestions(user_id, site_key, display_name, category, email_count, sender_domain, created_at, added) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (uid, "amex", "American Express", "credit_card", 3, "americanexpress.com", mighty.iso()),
        )
        db.commit()
        with client.session_transaction() as sess:
            csrf = sess["_csrf"]

    r = client.post(
        "/credentials/delete/amex",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT added FROM email_suggestions WHERE user_id=? AND site_key='amex'",
            (uid,),
        ).fetchone()
        assert row is not None
        assert row["added"] == 0


def test_delete_account_removes_email_suggestions(client):
    import app as mighty

    uid, _ = _user_api(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        db.execute(
            "INSERT INTO email_suggestions(user_id, site_key, display_name, category, email_count, sender_domain, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "amex", "American Express", "credit_card", 1, "americanexpress.com", mighty.iso()),
        )
        db.commit()
        with client.session_transaction() as sess:
            csrf = sess["_csrf"]

    r = client.post(
        "/settings/delete-account",
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        data=json.dumps({"password": "pass12345"}),
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT 1 FROM email_suggestions WHERE user_id=?", (uid,),
        ).fetchone()
        assert row is None


def test_extension_setup_embeds_api_key_meta(client):
    r = client.get("/extension-setup")
    assert r.status_code == 200
    assert b'name="mighty-api-key"' in r.data
    assert b"Chrome extension not detected" in r.data or b"Mighty in Chrome not detected" in r.data
    assert b"should be configured now" not in r.data


def test_dashboard_ext_install_link_points_to_extension_setup(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert b'id="ext-install-link"' in r.data
    assert b'href="/extension-setup"' in r.data
    assert b"coming soon" not in r.data.lower()


def test_signup_mentions_chrome_requirement(client):
    with client.session_transaction() as sess:
        sess.clear()
    r = client.get("/signup")
    assert r.status_code == 200
    assert b"desktop Chrome" in r.data


def test_onboarding_modal_mentions_chrome(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        mighty.get_db().execute("UPDATE users SET onboarded=0 WHERE id=?", (uid,))
        mighty.get_db().commit()

    r = client.get("/dashboard")
    assert r.status_code == 200
    assert b"desktop Chrome" in r.data
