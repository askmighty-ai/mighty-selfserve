"""Tests for admin-only debug pages."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADMIN_PAGES = [
    "/admin", "/admin/account-json", "/admin/extracted-fields", "/admin/provider-schemas",
    "/admin/discovery-cache", "/admin/ai-cache", "/admin/sync-history", "/admin/sync-timeline",
    "/admin/replay-discovery", "/admin/pipeline-runs", "/admin/coverage",
    "/admin/recommendation-unlocks", "/admin/capture-capability",
    "/admin/provider-benchmark",
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_admin.db")
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


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_forbidden_for_non_admin(client, path):
    c, _ = client
    assert c.get(path).status_code == 403


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_load_for_admin(client, monkeypatch, path):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get(path)
    assert r.status_code == 200
    assert b"Admin Debug" in r.data


def test_field_schema_cache_snapshot():
    from mighty.field_discovery import DiscoveryError, get_field_schema_cache
    cache = get_field_schema_cache()
    cache.clear()
    cache.record_success("hilton:abc", [{"key": "points"}])
    cache.record_failure("delta:def", DiscoveryError("fail"))
    assert len(cache.snapshot()) == 2
    cache.clear()
