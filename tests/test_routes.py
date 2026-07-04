"""Smoke-test that user-facing Flask routes resolve without server errors."""

import os
import re
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_routes.db")
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
            "email": f"routes_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


# Paths linked from sidebar, settings, dashboard, and auth flows.
NAV_GET_ROUTES = [
    "/dashboard",
    "/credentials",
    "/settings",
    "/email-scan",
    "/extension-setup",
    "/onboarding",
    "/privacy",
    "/privacy/audit-log",
    "/privacy/domains",
    "/tos",
    "/forgot-password",
    "/health",
    "/sync/status",
    "/api/csrf-token",
    "/api/email/suggestions",
    "/credentials/fields/load",
    "/dashboard/has-pending",
]

# Registered rules — every href target in app.py should appear here.
REGISTERED_RULES = None


def _registered_rules(app):
    global REGISTERED_RULES
    if REGISTERED_RULES is None:
        REGISTERED_RULES = {rule.rule for rule in app.url_map.iter_rules()}
    return REGISTERED_RULES


def _extract_href_paths():
    root = os.path.dirname(os.path.dirname(__file__))
    scan_files = [
        os.path.join(root, "app.py"),
        os.path.join(root, "mighty", "daily_brief_ui.py"),
        os.path.join(root, "mighty", "demo_mode.py"),
    ]
    paths = set()
    for app_path in scan_files:
        if not os.path.exists(app_path):
            continue
        with open(app_path, encoding="utf-8") as f:
            src = f.read()
        paths |= set(re.findall(r"""href=["'](/[^"'#?]+)""", src))
        paths |= set(re.findall(r"""window\.location\.href\s*=\s*['"](/[^"'#?]+)""", src))
        paths |= set(re.findall(r"""_nav\(['"](/[^"'#?]+)""", src))
    return sorted(paths)


@pytest.mark.parametrize("path", NAV_GET_ROUTES)
def test_nav_get_routes_resolve(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code < 400, f"{path} returned {r.status_code}"


def test_all_app_href_targets_are_registered(client):
    import app as mighty

    rules = _registered_rules(mighty.app)
    missing = []
    for path in _extract_href_paths():
        if path.startswith("/static/"):
            continue
        if path not in rules:
            missing.append(path)
    assert not missing, f"Navigation links to unregistered routes: {missing}"


def test_signup_redirects_to_dashboard(client):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 200


def test_onboarding_redirects_to_dashboard(client):
    r = client.get("/onboarding", follow_redirects=False)
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "/dashboard" in (r.headers.get("Location") or "")


def test_credentials_connect_param_opens_modal(client):
    r = client.get("/credentials?connect=amex")
    assert r.status_code == 200
    assert b"openCredForm(key, meta[0], meta[1], meta[2])" in r.data
    assert b"_startExtPoll(key)" in r.data
    assert b"modal-overlay" in r.data


def test_gmail_callback_redirects_to_amex_connect(client, monkeypatch):
    import app as mighty

    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setattr(mighty, "GOOGLE_CLIENT_SECRET", "test-secret")

    def fake_scan(_token, already_connected=None):
        return [{"site_key": "amex", "display_name": "American Express", "category": "credit_card", "email_count": 12, "sender": "americanexpress.com"}]

    monkeypatch.setattr(mighty, "scan_gmail", fake_scan)

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            import json
            return json.dumps(self._payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = []

    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        if "oauth2.googleapis.com/token" in req.full_url:
            return FakeResp({"access_token": "tok", "refresh_token": "ref"})
        if "gmail.googleapis.com" in req.full_url:
            return FakeResp({"emailAddress": "tester@gmail.com"})
        raise AssertionError(f"unexpected url: {req.full_url}")

    monkeypatch.setattr(mighty.urllib.request, "urlopen", fake_urlopen)

    with client.session_transaction() as sess:
        sess["gmail_oauth_state"] = "state123"

    r = client.get("/email/gmail/callback?code=abc&state=state123", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/credentials?connect=amex")

    with mighty.app.app_context():
        db = mighty.get_db()
        with client.session_transaction() as sess:
            uid = sess["user_id"]
        row = db.execute(
            "SELECT added FROM email_suggestions WHERE user_id=? AND site_key='amex'",
            (uid,),
        ).fetchone()
        assert row is not None
        assert row["added"] == 1
        cred = db.execute(
            "SELECT 1 FROM account_credentials WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        assert cred is not None


def test_dashboard_suppresses_demo_with_connected_account(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "amex", "", "", "", now, now),
        )
        stub = mighty.encrypt_account_data(uid, {"items": [], "sync_status": "needs_first_visit"})
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, "amex", "American Express", "💳", "#e5e7eb", stub, "", "waiting_for_extension"),
        )
        db.commit()

    r = client.get("/dashboard")
    assert r.status_code == 200
    assert b"Demo recommendation" not in r.data
    assert b"Marriott free night before Tokyo" not in r.data
    assert b"$1,240" not in r.data


def test_email_scan_has_sidebar_nav(client):
    r = client.get("/email-scan")
    assert r.status_code == 200
    assert b"Find accounts" in r.data
    assert b"/credentials" in r.data
    assert b"app-shell" in r.data


def test_credentials_page_renders(client):
    r = client.get("/credentials")
    assert r.status_code == 200
    assert b"Accounts" in r.data
    assert b"Every account Mighty knows about." in r.data
    assert b"Connected accounts" not in r.data
    assert b"sync-howto" not in r.data
    assert b"Never miss another credit" not in r.data
    assert b"function openModal()" in r.data
    assert b"onclick=\"openModal()\"" in r.data
    assert b"/dashboard?account=" not in r.data
    assert b"View account" not in r.data


def test_credentials_page_filter_waiting_empty_still_shows_active_chip(client):
    """Active filter chip stays visible even when that bucket has zero accounts."""
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(uid, {
            "items": [{"key": "balance", "label": "Balance", "value": "$100"}],
            "sync_status": "ok",
        })
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "amex", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, connection_status, sync_status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, "amex", "Amex", "💳", "#eee", stub, now, "connected", "ok"),
        )
        db.commit()
    r = client.get("/credentials?filter=waiting")
    assert r.status_code == 200
    assert b'href="/credentials?filter=waiting"' in r.data
    assert b"acct-portfolio-chip--active" in r.data
    assert b"No accounts in this view." in r.data
    assert b"/dashboard?account=" not in r.data


def test_credentials_page_filter_waiting(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(uid, {"items": [], "sync_status": "needs_first_visit"})
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "delta", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, connection_status, sync_status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, "delta", "Delta", "✈️", "#e5e7eb", stub, "", "waiting_for_extension", "needs_first_visit"),
        )
        db.commit()
    r = client.get("/credentials?filter=waiting")
    assert r.status_code == 200
    assert b"Waiting" in r.data
    assert b"acct-portfolio-chip--active" in r.data


def test_credentials_page_section_headers(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        synced_stub = mighty.encrypt_account_data(uid, {
            "items": [{"key": "balance", "label": "Balance", "value": "$100"}],
            "sync_status": "ok",
        })
        waiting_stub = mighty.encrypt_account_data(uid, {"items": [], "sync_status": "needs_first_visit"})
        for src, name, stub, conn, sync_st, synced_at in (
            ("amex", "American Express", synced_stub, "connected", "ok", now),
            ("delta", "Delta", waiting_stub, "waiting_for_extension", "needs_first_visit", ""),
        ):
            db.execute(
                "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, src, "", "", "", now, now),
            )
            db.execute(
                "INSERT INTO account_data "
                "(user_id, source, display_name, icon, color, data_enc, synced_at, connection_status, sync_status) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, src, name, "✈️", "#e5e7eb", stub, synced_at, conn, sync_st),
            )
        db.commit()
    r = client.get("/credentials")
    assert r.status_code == 200
    assert b"Up to date" in r.data
    assert b"Waiting" in r.data
    assert b"Edit login" not in r.data
    assert b"fields-panel" not in r.data


def test_credentials_with_legacy_account_data_row(client):
    """Regression: lifecycle query must not crash when row omits source key."""
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        stub = mighty.encrypt_account_data(uid, {"items": [], "sync_status": "ok"})
        db.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, "delta", "Delta", "✈️", "#e5e7eb", stub, "", "waiting_for_extension"),
        )
        db.commit()
    r = client.get("/credentials")
    assert r.status_code == 200
    assert b"Accounts" in r.data


def test_stale_session_redirects_to_login(client):
    with client.session_transaction() as s:
        s["user_id"] = "nonexistent-user-id"
        s["email"] = "ghost@test.local"
    r = client.get("/credentials", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in (r.headers.get("Location") or "")
    assert "next=" in (r.headers.get("Location") or "")


def test_login_post_bad_csrf_shows_login_form(client):
    r = client.post(
        "/login",
        data={"email": "a@test.local", "password": "pass12345", "_csrf": "wrong", "next": "/credentials"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert b"Welcome back" in r.data
    assert b"session expired" in r.data.lower() or b"Session expired" in r.data


def test_credentials_get_does_not_auto_discover(client, monkeypatch):
    """GET /credentials must not trigger background field discovery or Gemini."""
    import app as mighty

    auto_discover_calls = []
    gemini_calls = []

    def _track_auto_discover(uid):
        auto_discover_calls.append(uid)

    def _track_gemini(*args, **kwargs):
        gemini_calls.append((args, kwargs))
        return []

    monkeypatch.setattr(mighty, "_auto_discover_missing", _track_auto_discover)
    monkeypatch.setattr(mighty, "claude_discover_fields", _track_gemini)

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(uid, {
            "items": [{"key": "balance", "label": "Balance", "value": "$100"}],
            "raw_text": "Balance $100",
            "sync_status": "ok",
        })
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "amex", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, "amex", "American Express", "💳", "#e5e7eb", stub, now, "connected"),
        )
        db.commit()

    r = client.get("/credentials")
    assert r.status_code == 200
    assert b"Accounts" in r.data
    assert auto_discover_calls == []
    assert gemini_calls == []
    assert b"fetch('/credentials/auto-discover'" not in r.data


def test_dashboard_get_does_not_discover_fields(client, monkeypatch):
    """GET /dashboard must not trigger field discovery or Gemini."""
    import app as mighty

    auto_discover_calls = []
    gemini_calls = []

    monkeypatch.setattr(mighty, "_auto_discover_missing", lambda uid: auto_discover_calls.append(uid))
    monkeypatch.setattr(mighty, "claude_discover_fields", lambda *a, **k: gemini_calls.append((a, k)) or [])

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(uid, {
            "items": [{"key": "balance", "label": "Balance", "value": "$100"}],
            "sync_status": "ok",
        })
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "amex", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, "amex", "American Express", "💳", "#e5e7eb", stub, now, "connected"),
        )
        db.commit()

    r = client.get("/dashboard")
    assert r.status_code == 200
    assert auto_discover_calls == []
    assert gemini_calls == []


def test_credentials_decrypts_account_data_once_per_request(client, monkeypatch):
    """Each account blob should be decrypted at most once during GET /credentials."""
    import app as mighty

    decrypt_calls = []
    real_decrypt = mighty.decrypt_account_data

    def _counting_decrypt(uid, stored):
        decrypt_calls.append(stored)
        return real_decrypt(uid, stored)

    monkeypatch.setattr(mighty, "decrypt_account_data", _counting_decrypt)

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(uid, {
            "items": [{"key": "balance", "label": "Balance", "value": "$100"}],
            "sync_status": "ok",
        })
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "amex", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, "amex", "American Express", "💳", "#e5e7eb", stub, now, "connected"),
        )
        db.commit()

    r = client.get("/credentials")
    assert r.status_code == 200
    assert len(decrypt_calls) == 1


def test_credentials_route_timing(client, capsys):
    r = client.get("/credentials")
    assert r.status_code == 200
    out = capsys.readouterr().out
    assert "[RouteTiming] /credentials" in out
    assert "load_accounts=" in out
    assert "build_html=" in out


def test_auto_discover_requires_admin(client, monkeypatch):
    import app as mighty

    started = []
    monkeypatch.setattr(mighty, "_auto_discover_missing", lambda uid: started.append(uid))

    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    r = client.post(
        "/credentials/auto-discover",
        data={"_csrf": csrf},
    )
    assert r.status_code == 403
    assert started == []


def test_dashboard_must_not_show_sync_now(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert b"Sync now" not in r.data


def test_accounts_maintenance_layout(client):
    r = client.get("/credentials")
    assert r.status_code == 200
    assert b"Every account Mighty knows about." in r.data
    assert b"How Mighty works" not in r.data
    assert b"How updates work" not in r.data


def test_login_required_account_shows_open_or_log_in(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        stub = mighty.encrypt_account_data(uid, {"items": [], "sync_status": "login_required"})
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "delta", "", "", "", mighty.iso(), mighty.iso()),
        )
        db.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, "delta", "Delta", "✈️", "#e5e7eb", stub, "", "needs_login"),
        )
        db.commit()

    r = client.get("/credentials")
    assert r.status_code == 200
    assert b"Needs login" in r.data or b"Log in" in r.data or b"Open provider" in r.data

