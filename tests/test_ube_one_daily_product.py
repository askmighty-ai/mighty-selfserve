"""UBE one-daily-product — production daily home shares MDS chrome."""

from __future__ import annotations

import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ube_one_daily.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.delenv("HOME_OS_ENABLED", raising=False)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("MIGHTY_ENV", "production")

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
            "email": f"ube_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def test_production_dashboard_uses_mds_shell_not_inter_sidebar(client):
    """Customer daily home must not expose an Inter implementation seam."""
    dash = client.get("/dashboard", follow_redirects=False)
    assert dash.status_code == 200
    body = dash.get_data(as_text=True)

    assert 'data-app-shell="mds"' in body
    assert 'class="sidebar"' not in body
    assert "family=Inter" not in body
    assert "mds-brand" in body
    assert 'href="/credentials"' in body
    assert 'href="/settings"' in body
    assert 'href="/dashboard"' in body
    assert "mds-nav" in body


def test_production_dashboard_and_accounts_share_nav_family(client):
    """Founder perception: Home and Accounts use the same chrome families."""
    home = client.get("/dashboard").get_data(as_text=True)
    accounts = client.get("/credentials").get_data(as_text=True)

    for body in (home, accounts):
        assert 'data-app-shell="mds"' in body
        assert 'class="sidebar"' not in body
        assert 'href="/dashboard"' in body
        assert 'href="/credentials"' in body
        assert 'href="/settings"' in body

    assert "Plus Jakarta Sans" in home or "Fraunces" in home
    assert "Plus Jakarta Sans" in accounts or "Fraunces" in accounts


def test_keep_escape_still_serves_legacy_inter_chrome(client):
    """?keep=1 remains a debug escape with Inter sidebar."""
    r = client.get("/dashboard?keep=1", follow_redirects=False)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'class="sidebar"' in body
    assert 'data-app-shell="mds"' not in body
