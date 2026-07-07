"""Route tests for provider access probe API and admin page."""

import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_probe_admin.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("MIGHTY_ADMIN_TEST", raising=False)
    monkeypatch.delenv("DISABLE_AUTOMATIC_PROVIDER_PROBES", raising=False)
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
        email = f"probe_admin_{secrets.token_hex(4)}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    with c.session_transaction() as sess:
        uid = sess.get("user_id")
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT api_key FROM users WHERE id=?", (uid,),
        ).fetchone()
    c.email = email
    c.api_key = row["api_key"]
    return c


@pytest.fixture()
def admin_client(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    return client


AMEX_ACCOUNT_TEXT = """
Account Home
Membership Rewards
Available Points: 99,000
Card ending in 4321
Recent Activity and account services for your American Express card
"""

DELTA_ACCOUNT_TEXT = """
My SkyMiles
SkyMiles Number: 9876543210
Available Miles 22,100
Medallion Status Gold
My Trips and wallet on delta.com
"""


def test_probe_api_requires_key(client):
    r = client.post("/api/extension/provider-access-probe", json={"provider": "amex"})
    assert r.status_code == 401


def test_probe_api_records_amex_result(client):
    r = client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "amex",
            "url_visited": "https://www.americanexpress.com/en-us/account/",
            "dom_text": AMEX_ACCOUNT_TEXT,
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "signed_in_data_seen"
    assert body["auth_state"] == "private_data_visible"
    assert body["private_data_detected"] is True
    assert body["evidence_snippet"]


def test_probe_api_records_delta_needs_sign_in(client):
    r = client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "delta",
            "url_visited": "https://www.delta.com/login",
            "dom_text": "Sign In\nUser ID\nPassword\nForgot password",
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "needs_sign_in"
    assert body["auth_state"] == "login_page"


def test_admin_page_shows_auth_state_fields(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "amex",
            "url_visited": "https://www.americanexpress.com/en-us/account/",
            "dom_text": AMEX_ACCOUNT_TEXT,
            "page_title": "Amex Account Home",
        },
    )
    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    assert b"Auth state" in r.data
    assert b"private data visible" in r.data.lower() or b"private_data_visible" in r.data
    assert b"Amex Account Home" in r.data


def test_admin_run_probe_control_renders(admin_client):
    r = admin_client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "Run Probe" in text
    assert "Run Probe — Amex" in text
    assert "Manual probe runner" in text
    assert "probe-lifecycle-badge" in text


def test_manual_trigger_requires_admin(client):
    r = client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "amex"},
    )
    assert r.status_code == 403


def test_manual_trigger_accepts_one_provider(admin_client):
    r = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "amex"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["provider"] == "amex"
    assert body["lifecycle"] == "running"
    assert body["manual_run_id"]


def test_manual_trigger_rejects_unsupported_provider(admin_client):
    r = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "hilton"},
    )
    assert r.status_code == 400


def test_concurrent_probe_attempt_rejected(admin_client):
    r1 = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "amex"},
    )
    assert r1.status_code == 200
    r2 = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "delta"},
    )
    assert r2.status_code == 409
    assert "already running" in r2.get_json()["error"].lower()


def test_manual_probe_completes_on_extension_post(admin_client, client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "delta"},
    )
    manual_run_id = start.get_json()["manual_run_id"]

    client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "delta",
            "manual_run_id": manual_run_id,
            "url_visited": "https://www.delta.com/myprofile/",
            "dom_text": DELTA_ACCOUNT_TEXT,
        },
    )

    status = admin_client.get("/api/admin/provider-access-probe/run-status")
    assert status.status_code == 200
    body = status.get_json()
    assert body["lifecycle"] == "done"
    assert body["probe_run_id"]


def test_automatic_probe_disabled_in_development(client, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    from mighty.provider_access_probe import is_automatic_probe_disabled

    assert is_automatic_probe_disabled() is True

    r = client.get(
        "/api/extension/provider-access-probe/config",
        headers={"X-Mighty-Key": client.api_key},
    )
    assert r.status_code == 200
    assert r.get_json()["automatic_probes_enabled"] is False


def test_automatic_probe_disabled_via_admin_test_flag(client, monkeypatch):
    monkeypatch.setenv("MIGHTY_ADMIN_TEST", "true")
    from mighty.provider_access_probe import is_automatic_probe_disabled

    assert is_automatic_probe_disabled() is True


def test_extension_manual_trigger_endpoint(client, admin_client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "amex"},
    )
    manual_run_id = start.get_json()["manual_run_id"]

    r = client.get(
        "/api/extension/provider-access-probe/manual",
        headers={"X-Mighty-Key": client.api_key},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["lifecycle"] == "running"
    assert body["provider"] == "amex"
    assert body["manual_run_id"] == manual_run_id


def test_extension_manual_trigger_requires_key(client):
    r = client.get("/api/extension/provider-access-probe/manual")
    assert r.status_code == 401


def test_admin_page_forbidden_for_non_admin(client):
    assert client.get("/admin/provider-access-probe").status_code == 403


def test_admin_page_loads_for_admin(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    assert b"Provider Access Probe" in r.data
    assert b"amex" in r.data


def test_admin_json_endpoint(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "delta",
            "url_visited": "https://www.delta.com/myprofile/",
            "dom_text": DELTA_ACCOUNT_TEXT,
        },
    )
    r = client.get("/api/admin/provider-access-probe")
    assert r.status_code == 200
    providers = {p["provider"]: p for p in r.get_json()["providers"]}
    assert providers["delta"]["status"] == "signed_in_data_seen"
    assert providers["delta"]["auth_state"] == "private_data_visible"
    assert providers["amex"]["status"] == "not_started"
