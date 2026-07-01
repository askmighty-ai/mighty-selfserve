"""Dashboard must not call Gmail/Outlook APIs during HTML render — cached subjects only."""

import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_email_cache.db")
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
            "email": f"cache_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def _insert_email_connection(mighty, uid, *, subjects=None, refreshed_at=None, token="tok"):
    db = mighty.get_db()
    now = mighty.iso()
    enc = mighty.encrypt_cred(uid, token)
    subjects_json = json.dumps(subjects) if subjects is not None else None
    db.execute(
        """
        INSERT INTO email_connections(
            user_id, provider, access_token_enc, refresh_token_enc,
            email_address, scanned_at, created_at, subjects_json, subjects_refreshed_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
          access_token_enc=excluded.access_token_enc,
          subjects_json=excluded.subjects_json,
          subjects_refreshed_at=excluded.subjects_refreshed_at
        """,
        (uid, "gmail", enc, "", "user@gmail.com", now, now, subjects_json, refreshed_at),
    )
    db.commit()


def test_dashboard_does_not_call_fetch_recent_subjects(client, monkeypatch):
    """GET /dashboard must never invoke live email subject APIs."""
    import app as mighty

    def _boom(*args, **kwargs):
        raise AssertionError("fetch_recent_subjects must not run during dashboard render")

    monkeypatch.setattr(mighty, "fetch_recent_subjects", _boom)

    uid = _uid(client)
    with mighty.app.app_context():
        # Connected email but no cache — old code would have called Gmail here.
        _insert_email_connection(mighty, uid, subjects=None, refreshed_at=None)

    r = client.get("/dashboard")
    assert r.status_code == 200


def test_cached_subjects_read_without_api(client, monkeypatch):
    import app as mighty

    fetch_calls = []
    monkeypatch.setattr(
        mighty,
        "fetch_recent_subjects",
        lambda *a, **k: fetch_calls.append(1) or ["should not run"],
    )

    uid = _uid(client)
    fresh = datetime.now(timezone.utc).isoformat()
    with mighty.app.app_context():
        _insert_email_connection(
            mighty,
            uid,
            subjects=["World of Hyatt: 2x points"],
            refreshed_at=fresh,
        )
        cached = mighty._cached_dashboard_email_subjects(uid, mighty.get_db())
        assert cached == ["World of Hyatt: 2x points"]

    r = client.get("/dashboard")
    assert r.status_code == 200
    assert fetch_calls == []


def test_stale_cache_returns_empty_on_dashboard(client, monkeypatch):
    import app as mighty

    monkeypatch.setattr(
        mighty,
        "fetch_recent_subjects",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no live fetch")),
    )

    uid = _uid(client)
    stale = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    with mighty.app.app_context():
        _insert_email_connection(
            mighty,
            uid,
            subjects=["Old Marriott offer"],
            refreshed_at=stale,
        )
        assert mighty._cached_dashboard_email_subjects(uid, mighty.get_db()) == []

    r = client.get("/dashboard")
    assert r.status_code == 200


def test_refresh_dashboard_email_subjects_updates_cache(client, monkeypatch):
    import app as mighty

    monkeypatch.setattr(
        mighty,
        "fetch_recent_subjects",
        lambda provider, token: ["Amex Offer: $50 back"],
    )

    uid = _uid(client)
    with mighty.app.app_context():
        _insert_email_connection(mighty, uid, subjects=None, refreshed_at=None)
        subjects = mighty._refresh_dashboard_email_subjects(uid, mighty.get_db(), provider="gmail")
        assert subjects == ["Amex Offer: $50 back"]
        row = mighty.get_db().execute(
            "SELECT subjects_json, subjects_refreshed_at FROM email_connections "
            "WHERE user_id=? AND provider='gmail'",
            (uid,),
        ).fetchone()
        assert json.loads(row["subjects_json"]) == ["Amex Offer: $50 back"]
        assert row["subjects_refreshed_at"]


def test_gmail_callback_triggers_background_subject_refresh(client, monkeypatch):
    import app as mighty

    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(mighty, "scan_gmail", lambda _t, already_connected=None: [])

    refresh_calls = []
    monkeypatch.setattr(
        mighty,
        "_refresh_dashboard_email_subjects_bg",
        lambda uid, provider=None: refresh_calls.append((uid, provider)),
    )

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
        raise AssertionError(f"unexpected url: {req.full_url}")

    monkeypatch.setattr(mighty.urllib.request, "urlopen", fake_urlopen)

    with client.session_transaction() as sess:
        sess["gmail_oauth_state"] = "state123"

    r = client.get("/email/gmail/callback?code=abc&state=state123", follow_redirects=False)
    assert r.status_code == 200
    assert len(refresh_calls) == 1
    assert refresh_calls[0][1] == "gmail"


def test_route_timing_includes_email_subjects_cache_step(client, monkeypatch, capsys):
    import app as mighty

    monkeypatch.setattr(mighty, "fetch_recent_subjects", lambda *a, **k: [])

    uid = _uid(client)
    fresh = datetime.now(timezone.utc).isoformat()
    with mighty.app.app_context():
        _insert_email_connection(
            mighty,
            uid,
            subjects=["Delta SkyMiles bonus"],
            refreshed_at=fresh,
        )

    r = client.get("/dashboard")
    assert r.status_code == 200
    out = capsys.readouterr().out
    assert "[RouteTiming] /dashboard" in out
    assert "email_subjects_cache=" in out
