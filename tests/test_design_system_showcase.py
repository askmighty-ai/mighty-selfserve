"""Tests for the design-system showcase page and admin route."""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.design_system import render_showcase_page

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "static" / "design-system" / "mighty-ds.css"
SHOWCASE_JS = ROOT / "static" / "design-system" / "showcase.js"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_design_system.db")
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
        email = f"admin_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c, email


def test_showcase_page_includes_all_component_stories():
    html = render_showcase_page()
    for title in (
        "Design tokens",
        "Button",
        "Card",
        "Section",
        "Hero",
        "Status Badge",
        "Trust Card",
        "Permission Card",
        "Timeline",
        "Account Row",
        "Empty State",
        "Modal",
        "Progress Stepper",
        "Navigation",
        "Form Controls",
        "Toast",
        "Banner",
        "Icons",
    ):
        assert title in html, f"missing story: {title}"


def test_showcase_loads_design_system_assets():
    html = render_showcase_page()
    assert "/static/design-system/mighty-ds.css" in html
    assert "/static/design-system/showcase.js" in html
    assert 'class="mds mds-atmosphere"' in html
    assert 'class="mds-skip-link"' in html
    assert BUNDLE.is_file()
    assert SHOWCASE_JS.is_file()


def test_showcase_demonstrates_key_states():
    html = render_showcase_page()
    assert "mds-btn--primary" in html
    assert "aria-busy" in html
    assert "mds-badge--attention" in html
    assert 'role="dialog"' in html
    assert 'aria-current="step"' in html
    assert 'role="switch"' in html


def test_admin_design_system_forbidden_for_non_admin(client):
    c, _ = client
    assert c.get("/admin/design-system").status_code == 403


def test_admin_design_system_renders_for_admin(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    response = c.get("/admin/design-system")
    assert response.status_code == 200
    assert b"Design system showcase" in response.data
    assert b"mds-btn--primary" in response.data
    assert b"/static/design-system/mighty-ds.css" in response.data
