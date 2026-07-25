"""Guarded staging research entry for moderated Home V2 testing."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty import research_home as rh


@pytest.fixture()
def staging_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "staging")
    monkeypatch.delenv("MIGHTY_ENV", raising=False)
    monkeypatch.setenv("RESEARCH_HOME_ENABLED", "true")


@pytest.fixture()
def client(tmp_path, monkeypatch, staging_env):
    db_path = str(tmp_path / "research_home.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()

    mighty.app.config["TESTING"] = True
    return mighty.app.test_client()


def _users_count(app_module) -> int:
    with app_module.app.app_context():
        row = app_module.get_db().execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])


@pytest.mark.parametrize(
    "path,expected_state,marker",
    [
        ("/research/home", "healthy", "You're good."),
        ("/research/home?state=healthy", "healthy", "You're good."),
        ("/research/home?state=attention", "attention", "only step we can't complete for you"),
        ("/research/home?state=opportunity", "opportunity", "Value waiting"),
    ],
)
def test_research_urls_create_demo_session_and_home_state(
    client, path, expected_state, marker
):
    import app as mighty

    before = _users_count(mighty)
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dashboard")

    with client.session_transaction() as sess:
        assert sess.get(rh.SESSION_FLAG) is True
        assert sess.get(rh.SESSION_STATE_KEY) == expected_state
        assert sess.get("user_id") == rh.RESEARCH_USER_ID

    dash = client.get("/dashboard")
    assert dash.status_code == 200
    body = dash.get_data(as_text=True)
    assert 'data-research-preview="1"' in body
    assert "Research preview" in body
    assert f'data-state="{expected_state}"' in body
    assert marker in body or marker.replace("'", "&#x27;") in body
    assert "home-v2" in body
    assert _users_count(mighty) == before
    with mighty.app.app_context():
        assert rh.count_research_customer_rows(mighty.get_db()) == 0


def test_production_cannot_access_research_route(client, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("RESEARCH_HOME_ENABLED", "true")

    resp = client.get("/research/home")
    assert resp.status_code == 404
    assert b"unavailable" in resp.data.lower()

    with client.session_transaction() as sess:
        assert not sess.get(rh.SESSION_FLAG)


def test_research_blocked_without_demo_mode(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    resp = client.get("/research/home")
    assert resp.status_code == 404


def test_research_blocked_without_staging_identity(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RESEARCH_HOME_ENABLED", raising=False)
    monkeypatch.delenv("MIGHTY_ENV", raising=False)
    resp = client.get("/research/home")
    assert resp.status_code == 404


def test_state_override_ignored_outside_research_session(client, tmp_path, monkeypatch):
    """?state= on /dashboard must not invent a research Home state for real users."""
    import secrets

    import app as mighty

    # Sign up a normal user (not research).
    client.get("/signup")
    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    email = f"real_{secrets.token_hex(4)}@test.local"
    client.post(
        "/signup",
        data={"email": email, "password": "pass12345", "_csrf": csrf},
        follow_redirects=False,
    )
    with client.session_transaction() as sess:
        assert not sess.get(rh.SESSION_FLAG)
        real_uid = sess.get("user_id")
        assert real_uid != rh.RESEARCH_USER_ID

    # state query must be ignored — no research session, no research marker.
    dash = client.get("/dashboard?state=attention")
    assert dash.status_code == 200
    body = dash.get_data(as_text=True)
    assert 'data-research-preview="1"' not in body
    with client.session_transaction() as sess:
        assert not sess.get(rh.SESSION_FLAG)
        assert sess.get("user_id") == real_uid


def test_invalid_state_rejected(client):
    resp = client.get("/research/home?state=bogus")
    assert resp.status_code == 400
    assert b"Invalid research state" in resp.data


def test_no_persistent_customer_record(client):
    import app as mighty

    before = _users_count(mighty)
    client.get("/research/home?state=healthy", follow_redirects=True)
    assert _users_count(mighty) == before
    with mighty.app.app_context():
        assert rh.count_research_customer_rows(mighty.get_db()) == 0
        row = mighty.get_db().execute(
            "SELECT 1 FROM users WHERE id=?", (rh.RESEARCH_USER_ID,)
        ).fetchone()
        assert row is None


def test_outbound_and_mutating_actions_disabled(client):
    client.get("/research/home?state=attention", follow_redirects=True)

    # Provider / Gmail / enrollment style surfaces are stubbed.
    for path in (
        "/credentials",
        "/email-scan",
        "/oauth/gmail",
        "/extension-setup",
        "/research/stub/provider-signin",
    ):
        resp = client.get(path)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "research" in body.lower() or "Unavailable in research preview" in body

    # Mutating POSTs are hard-blocked.
    for path in (
        "/api/data/rediscover-all",
        "/api/email/suggestions",
        "/api/providers/amex/check",
        "/api/push/subscribe",
        "/api/authorize",
    ):
        resp = client.post(path, json={})
        assert resp.status_code == 403
        payload = resp.get_json()
        assert payload["error"] == "disabled_in_research_preview"
        assert payload.get("research_preview") is True


def test_research_home_allowed_helpers(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.delenv("RESEARCH_HOME_ENABLED", raising=False)
    assert rh.research_home_allowed() is True

    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert rh.research_home_allowed() is False
    assert rh.is_production_environment() is True
