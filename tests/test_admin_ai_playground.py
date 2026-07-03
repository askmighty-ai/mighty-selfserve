"""Tests for admin AI Playground."""

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_playground.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
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


def _seed_account(c, uid: str, source: str = "delta"):
    import app as mighty

    raw = (
        "=== URL: https://example.com/account ===\n"
        "SkyMiles Gold Medallion\n"
        "Balance 45,320 miles\n"
        "Minimum Payment Due $35 by Jul 12, 2026\n"
    )
    enc = mighty.encrypt_account_data(uid, {"raw_text": raw, "items": []})
    with c.application.app_context():
        mighty.get_db().execute(
            "INSERT INTO account_data (user_id, source, display_name, data_enc, synced_at) VALUES (?,?,?,?,?)",
            (uid, source, "Delta Air Lines", enc, "2026-07-01T00:00:00Z"),
        )
        mighty.get_db().commit()


def test_ai_playground_page_requires_admin(client):
    c, _ = client
    assert c.get("/admin/ai-playground").status_code == 403


def test_ai_playground_page_loads_for_admin(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get("/admin/ai-playground")
    assert r.status_code == 200
    assert b"AI Playground" in r.data
    assert b"Compare GPT-5.4-mini vs GPT-5.5" in r.data


def test_ai_playground_snapshot(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    with c.session_transaction() as sess:
        uid = sess["user_id"]
    _seed_account(c, uid)

    r = c.get("/api/admin/debug/ai-playground/delta?provider=openai")
    assert r.status_code == 200
    data = r.get_json()
    assert data["source"] == "delta"
    assert data["prompt_version"] == "1.0.0"
    assert data["raw_captured_html"]["chars"] > 0
    assert data["preprocessed_html"]["chars"] > 0
    assert data["token_estimate"] > 0


def test_ai_playground_extract_mocked(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    with c.session_transaction() as sess:
        uid = sess["user_id"]
    _seed_account(c, uid)

    fake_fields = [{
        "key": "miles_balance",
        "label": "Miles Balance",
        "value": "45,320",
        "value_type": "points",
        "confidence": 0.97,
        "source_snippet": "Balance 45,320 miles",
    }]

    def _fake_run(provider_name, source, content, context, model=None):
        return {
            "provider": provider_name,
            "model": model or "gpt-5.4-mini",
            "validation": {"ok": True, "field_count": 1, "error": None},
            "fields_before_filter": fake_fields,
            "fields_after_filter": fake_fields,
            "token_estimate": 100,
            "prompt_version": context.prompt_version,
        }

    import mighty.admin_ai_playground as playground
    monkeypatch.setattr(playground, "_run_provider_extraction", _fake_run)

    r = c.post(
        "/api/admin/debug/ai-playground/delta",
        json={"mode": "extract", "provider": "openai"},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["mode"] == "extract"
    assert data["fields_after_filter"][0]["key"] == "miles_balance"


def test_ai_playground_compare_prompts_mocked(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    with c.session_transaction() as sess:
        uid = sess["user_id"]
    _seed_account(c, uid)

    import mighty.admin_ai_playground as playground

    def _fake_run(provider_name, source, content, context, model=None):
        return {
            "prompt_version": context.prompt_version,
            "prompt_id": context.prompt_id,
            "fields_after_filter": [],
        }

    monkeypatch.setattr(playground, "_run_provider_extraction", _fake_run)

    r = c.post(
        "/api/admin/debug/ai-playground/delta",
        json={"mode": "compare_prompts", "provider": "openai"},
    )
    data = r.get_json()
    assert data["mode"] == "compare_prompts"
    assert "field_discovery@1.0.0" in data["compare"]
    assert "field_discovery_v2@2.0.0" in data["compare"]


def test_field_discovery_v2_prompt_loads():
    from mighty.prompts import render_prompt

    text = render_prompt(
        "field_discovery_v2",
        site="Delta",
        text="Balance 1,000 miles",
        today="July 3, 2026",
        category_hint="",
    )
    assert text.version == "2.0.0"
    assert "ORDERING v2" in text.text
    assert "1,000 miles" in text.text
