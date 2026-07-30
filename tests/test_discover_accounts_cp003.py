"""CP-003 Slice 1 — Discover My Accounts preface → review."""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_discover.db")
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
            "email": f"discover_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _csrf(client) -> str:
    with client.session_transaction() as sess:
        return sess["_csrf"]


def _uid(client) -> str:
    with client.session_transaction() as sess:
        return sess["user_id"]


def _unenroll_amex(client, mighty) -> None:
    """Clear signup auto-enroll so Discover can still exercise Amex review/confirm."""
    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        db.execute(
            "DELETE FROM account_credentials WHERE user_id=? AND source='amex'",
            (uid,),
        )
        db.execute(
            "DELETE FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        )
        db.commit()


def _seed_gmail(client, mighty):
    uid = _uid(client)
    now = datetime.now(timezone.utc).isoformat()
    with mighty.app.app_context():
        db = mighty.get_db()
        db.execute(
            """
            INSERT INTO email_connections(
                user_id, provider, access_token_enc, refresh_token_enc,
                email_address, scanned_at, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (uid, "gmail", "x", "y", "tester@gmail.com", now, now),
        )
        db.commit()


def test_email_scan_requires_login(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_discover_anon.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    r = c.get("/email-scan", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "/login" in (r.headers.get("Location") or "")


def test_preface_continue_starts_gmail_oauth(client, monkeypatch):
    import app as mighty

    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_SECRET", "test-secret")

    r = client.post(
        "/email-scan/continue",
        data={"_csrf": _csrf(client)},
        follow_redirects=False,
    )
    assert r.status_code in (301, 302, 303, 307, 308)
    loc = r.headers.get("Location") or ""
    assert loc.endswith("/email/gmail/auth") or "/email/gmail/auth" in loc

    r2 = client.get("/email/gmail/auth", follow_redirects=False)
    assert r2.status_code in (301, 302, 303, 307, 308)
    assert "accounts.google.com" in (r2.headers.get("Location") or "")


def test_gmail_auth_requires_preface_ack(client, monkeypatch):
    import app as mighty

    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_SECRET", "test-secret")

    r = client.get("/email/gmail/auth", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)
    assert (r.headers.get("Location") or "").endswith("/email-scan")


def test_callback_empty_discovery(client, monkeypatch):
    import app as mighty

    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(mighty, "scan_gmail", lambda *_a, **_k: [])

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout=15):
        if "oauth2.googleapis.com/token" in req.full_url:
            return FakeResp({"access_token": "tok", "refresh_token": "ref"})
        if "gmail.googleapis.com" in req.full_url:
            return FakeResp({"emailAddress": "tester@gmail.com"})
        raise AssertionError(req.full_url)

    monkeypatch.setattr(mighty.urllib.request, "urlopen", fake_urlopen)
    with client.session_transaction() as sess:
        sess["gmail_oauth_state"] = "state123"

    r = client.get("/email/gmail/callback?code=abc&state=state123")
    assert r.status_code == 200
    assert b"No accounts found" in r.data
    assert b"identify any supported accounts" in r.data
    assert b"enrollable" not in r.data
    assert b"Go to Home" in r.data
    assert b"Add an account manually" in r.data
    assert b"Try another provider" not in r.data


def test_connected_user_sees_review_from_store(client):
    import app as mighty
    from mighty.discovery_pipeline import process_email_scan

    _unenroll_amex(client, mighty)
    _seed_gmail(client, mighty)
    uid = _uid(client)
    now = datetime.now(timezone.utc)
    with mighty.app.app_context():
        db = mighty.get_db()
        process_email_scan(
            db,
            uid,
            [
                {
                    "site_key": "amex",
                    "display_name": "American Express",
                    "category": "credit_card",
                    "email_count": 8,
                    "sender": "americanexpress.com",
                }
            ],
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=None,
            auto_enroll=False,
            now=now,
        )

    r = client.get("/email-scan")
    assert r.status_code == 200
    assert b"Confirm what Mighty should watch" in r.data
    assert b"American Express" in r.data
    assert b'checked' in r.data  # preselected confident match


def test_confirm_enrolls_selected_amex(client):
    import app as mighty

    _unenroll_amex(client, mighty)
    _seed_gmail(client, mighty)
    # Seed discovery fact so enroll_from_discovery can require_eligible.
    uid = _uid(client)
    now = datetime.now(timezone.utc)
    with mighty.app.app_context():
        db = mighty.get_db()
        from mighty.discovery_pipeline import process_email_scan

        process_email_scan(
            db,
            uid,
            [
                {
                    "site_key": "amex",
                    "display_name": "American Express",
                    "category": "credit_card",
                    "email_count": 8,
                    "sender": "americanexpress.com",
                }
            ],
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=None,
            auto_enroll=False,
            now=now,
        )

    r = client.post(
        "/email-scan/confirm",
        data={"_csrf": _csrf(client), "watch": "amex"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert (r.headers.get("Location") or "").endswith("/enable-monitoring")
    with mighty.app.app_context():
        db = mighty.get_db()
        cred = db.execute(
            "SELECT 1 FROM account_credentials WHERE user_id=? AND source='amex'",
            (_uid(client),),
        ).fetchone()
        assert cred is not None


def test_confirm_empty_selection_goes_home_without_enroll(client):
    import app as mighty

    _unenroll_amex(client, mighty)
    _seed_gmail(client, mighty)
    r = client.post(
        "/email-scan/confirm",
        data={"_csrf": _csrf(client)},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    loc = r.headers.get("Location") or ""
    assert "/enable-monitoring" not in loc
    with mighty.app.app_context():
        db = mighty.get_db()
        cred = db.execute(
            "SELECT 1 FROM account_credentials WHERE user_id=? AND source='amex'",
            (_uid(client),),
        ).fetchone()
        assert cred is None
