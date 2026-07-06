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
    assert r.get_json()["status"] == "needs_sign_in"


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
    assert providers["amex"]["status"] == "not_started"
