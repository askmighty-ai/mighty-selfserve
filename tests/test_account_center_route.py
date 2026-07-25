"""Route tests for legacy /account-center → Accounts redirect."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_account_center.db")
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
        email = f"acc_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c


def test_account_center_requires_login(client):
    import app as mighty

    r = mighty.app.test_client().get("/account-center", follow_redirects=False)
    assert r.status_code in (302, 401)
    if r.status_code == 302:
        assert "/login" in (r.headers.get("Location") or "")


def test_account_center_redirects_to_credentials(client):
    r = client.get("/account-center", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/credentials")


def test_account_center_preserves_valid_filter(client):
    r = client.get("/account-center?filter=waiting", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/credentials?filter=waiting")


def test_account_center_drops_invalid_filter(client):
    r = client.get("/account-center?filter=not-a-filter", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/credentials")
    assert "filter=" not in r.headers["Location"]


def test_credentials_page_still_exists(client):
    r = client.get("/credentials")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Accounts" in html
