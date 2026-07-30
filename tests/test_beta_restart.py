"""Self-service factory reset + duplicate signup recovery."""

from __future__ import annotations

import os
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_beta_restart.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)

    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()

    mighty.app.config["TESTING"] = True
    return mighty.app.test_client()


def _csrf(client) -> str:
    client.get("/login")
    with client.session_transaction() as sess:
        if "_csrf" in sess:
            return sess["_csrf"]
    client.get("/signup")
    with client.session_transaction() as sess:
        return sess["_csrf"]


def _logout(client) -> None:
    csrf = _csrf(client)
    client.post("/logout", data={"_csrf": csrf})


def _signup(client, email: str, password: str = "pass12345"):
    csrf = _csrf(client)
    return client.post(
        "/signup",
        data={"email": email, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )


def test_duplicate_signup_offers_factory_reset_and_sign_in(client):
    email = f"dup_{secrets.token_hex(3)}@test.local"
    assert _signup(client, email).status_code == 302
    _logout(client)

    r = _signup(client, email)
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "already exists" in body
    assert 'href="/login"' in body
    assert "/beta/restart?email=" in body
    assert "Delete account and start over" in body
    assert email in body


def test_factory_reset_deletes_account_and_returns_to_landing(client):
    import app as mighty

    email = f"restart_{secrets.token_hex(3)}@test.local"
    password = "pass12345"
    assert _signup(client, email, password).status_code == 302
    with client.session_transaction() as sess:
        old_uid = sess["user_id"]
        sess["discover_preface_ack"] = True

    with mighty.app.app_context():
        db = mighty.get_db()
        db.execute(
            "INSERT INTO email_suggestions(user_id, site_key, display_name, category, email_count, sender_domain, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (old_uid, "amex", "American Express", "credit_card", 1, "americanexpress.com", mighty.iso()),
        )
        db.commit()

    csrf = _csrf(client)
    r = client.post(
        "/beta/restart",
        data={
            "email": email,
            "password": password,
            "confirm_wipe": "1",
            "_csrf": csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.startswith("/?")
    qs = parse_qs(urlparse(loc).query)
    assert qs.get("factory_reset") == ["1"]
    assert qs.get("email") == [email]

    land = client.get(loc)
    assert land.status_code == 200
    assert b"Your Mighty account was deleted" in land.data
    assert b"Create account" in land.data
    assert email.encode() in land.data or b"Get Started" in land.data

    with client.session_transaction() as sess:
        assert "user_id" not in sess
        assert "discover_preface_ack" not in sess

    with mighty.app.app_context():
        db = mighty.get_db()
        assert db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone() is None
        assert db.execute("SELECT 1 FROM users WHERE id=?", (old_uid,)).fetchone() is None
        assert (
            db.execute(
                "SELECT 1 FROM email_suggestions WHERE user_id=?", (old_uid,)
            ).fetchone()
            is None
        )

    # Same email can create a brand-new account and enter first-run onboarding.
    r2 = _signup(client, email, password)
    assert r2.status_code == 302
    assert r2.headers["Location"].endswith("/extension-setup")
    with client.session_transaction() as sess:
        assert sess["email"] == email
        assert sess["user_id"] != old_uid


def test_landing_shows_factory_reset_banner(client):
    r = client.get("/?factory_reset=1&email=tester%40example.com")
    assert r.status_code == 200
    assert b"Your Mighty account was deleted" in r.data
    assert b"Create account" in r.data
    assert b"tester%40example.com" in r.data or b"tester@example.com" in r.data


def test_signup_shows_factory_reset_banner(client):
    r = client.get("/signup?factory_reset=1&email=tester%40example.com")
    assert r.status_code == 200
    assert b"Your Mighty account was deleted" in r.data
    assert b'value="tester@example.com"' in r.data


def test_factory_reset_rejects_wrong_password(client):
    email = f"badpw_{secrets.token_hex(3)}@test.local"
    assert _signup(client, email).status_code == 302
    _logout(client)
    csrf = _csrf(client)
    r = client.post(
        "/beta/restart",
        data={
            "email": email,
            "password": "wrong-password",
            "confirm_wipe": "1",
            "_csrf": csrf,
        },
    )
    assert r.status_code == 200
    assert b"don" in r.data and b"match" in r.data


def test_factory_reset_requires_confirm(client):
    email = f"noconfirm_{secrets.token_hex(3)}@test.local"
    assert _signup(client, email).status_code == 302
    _logout(client)
    csrf = _csrf(client)
    r = client.post(
        "/beta/restart",
        data={
            "email": email,
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    assert r.status_code == 200
    assert b"confirmation" in r.data.lower()


def test_factory_reset_page_prefills_email(client):
    r = client.get("/beta/restart?email=tester%40example.com")
    assert r.status_code == 200
    assert b'value="tester@example.com"' in r.data
    assert b"Start over" in r.data
    assert b"Delete data and start over" in r.data


def test_factory_reset_allows_admin_email_with_password(client, monkeypatch):
    import app as mighty

    email = "founder.admin@test.local"
    password = "pass12345"
    monkeypatch.setenv("ADMIN_EMAIL", email)
    assert _signup(client, email, password).status_code == 302
    with client.session_transaction() as sess:
        old_uid = sess["user_id"]
    csrf = _csrf(client)
    r = client.post(
        "/beta/restart",
        data={
            "email": email,
            "password": password,
            "confirm_wipe": "1",
            "_csrf": csrf,
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/?")
    assert "factory_reset=1" in r.headers["Location"]
    with client.session_transaction() as sess:
        assert "user_id" not in sess
    with mighty.app.app_context():
        assert mighty.get_db().execute(
            "SELECT 1 FROM users WHERE id=?", (old_uid,)
        ).fetchone() is None
