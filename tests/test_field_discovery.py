"""Tests for AI field discovery budget guardrails."""

import os
import secrets
import sys
import time

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_provider import DiscoveryProviderError, DiscoveryResult
from mighty.field_discovery import (
    DiscoveryDisabledError,
    DiscoveryError,
    DiscoveryUnavailableError,
    FieldSchemaCache,
    assert_field_discovery_available,
    clear_field_schema_cache,
    field_discovery_failure_ttl_seconds,
    field_discovery_max_chars,
    is_field_discovery_enabled,
    schema_cache_key,
    truncate_discovery_input,
)

SAMPLE_FIELD = {
    "key": "balance",
    "label": "Balance",
    "value": "$100",
    "value_type": "currency",
    "confidence": 0.99,
    "source_snippet": "Balance $100",
}


@pytest.fixture(autouse=True)
def _reset_discovery_cache():
    clear_field_schema_cache()
    yield
    clear_field_schema_cache()


@pytest.fixture(autouse=True)
def _default_openai_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


class TestEnvConfig:
    def test_discovery_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AI_FIELD_DISCOVERY_ENABLED", raising=False)
        assert is_field_discovery_enabled() is True

    def test_discovery_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("AI_FIELD_DISCOVERY_ENABLED", "false")
        assert is_field_discovery_enabled() is False

    def test_max_chars_default(self, monkeypatch):
        monkeypatch.delenv("AI_FIELD_DISCOVERY_MAX_CHARS", raising=False)
        assert field_discovery_max_chars() == 20_000

    def test_max_chars_from_env(self, monkeypatch):
        monkeypatch.setenv("AI_FIELD_DISCOVERY_MAX_CHARS", "4096")
        assert field_discovery_max_chars() == 4096

    def test_truncate_discovery_input(self, monkeypatch):
        monkeypatch.setenv("AI_FIELD_DISCOVERY_MAX_CHARS", "10")
        assert truncate_discovery_input("abcdefghijklmnop") == "abcdefghij"


class TestSchemaCache:
    def test_cache_key_includes_provider_and_content(self):
        key_a = schema_cache_key("delta", "Balance 100")
        key_b = schema_cache_key("delta", "Balance 200")
        key_c = schema_cache_key("amex", "Balance 100")
        assert key_a != key_b
        assert key_a != key_c
        assert key_a.startswith("delta:")

    def test_success_cache_hit(self):
        cache = FieldSchemaCache()
        key = schema_cache_key("delta", "points 1000")
        fields = [SAMPLE_FIELD]
        cache.record_success(key, fields)
        assert cache.get_fields(key) == fields

    def test_failure_negative_cache_raises(self):
        cache = FieldSchemaCache()
        key = schema_cache_key("delta", "points 1000")
        cache.record_failure(key, DiscoveryError("quota exceeded"))
        with pytest.raises(DiscoveryError, match="quota exceeded"):
            cache.get_fields(key)

    def test_failure_cache_expires(self, monkeypatch):
        monkeypatch.setenv("AI_FIELD_DISCOVERY_FAILURE_TTL_SECONDS", "1")
        cache = FieldSchemaCache()
        key = schema_cache_key("delta", "points 1000")
        cache.record_failure(key, DiscoveryError("quota exceeded"))
        assert field_discovery_failure_ttl_seconds() == 1
        cache._entries[key].timestamp = time.time() - 2
        assert cache.get_fields(key) is None


class TestAvailability:
    def test_assert_available_when_ready(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        assert_field_discovery_available()

    def test_assert_disabled(self, monkeypatch):
        monkeypatch.setenv("AI_FIELD_DISCOVERY_ENABLED", "off")
        with pytest.raises(DiscoveryDisabledError):
            assert_field_discovery_available()

    def test_assert_unavailable_without_openai_key(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(DiscoveryUnavailableError, match="OPENAI_API_KEY"):
            assert_field_discovery_available()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "field_discovery_routes.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.delenv("AI_FIELD_DISCOVERY_ENABLED", raising=False)
    monkeypatch.delenv("AI_FIELD_DISCOVERY_MAX_CHARS", raising=False)

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
            "email": f"fd_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _seed_discover_account(client, monkeypatch):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
        csrf = sess["_csrf"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(uid, {
            "items": [],
            "raw_text": "Balance $100\nGold Medallion status",
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
    return csrf


def test_credentials_discover_disabled_returns_503(client, monkeypatch):
    monkeypatch.setenv("AI_FIELD_DISCOVERY_ENABLED", "false")
    csrf = _seed_discover_account(client, monkeypatch)

    r = client.post("/credentials/discover/amex", data={"_csrf": csrf})
    assert r.status_code == 503
    body = r.get_json()
    assert body["ok"] is False
    assert "disabled" in body["error"].lower()


def test_credentials_discover_unavailable_returns_503(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    csrf = _seed_discover_account(client, monkeypatch)

    r = client.post("/credentials/discover/amex", data={"_csrf": csrf})
    assert r.status_code == 503
    body = r.get_json()
    assert body["ok"] is False
    assert "OPENAI_API_KEY" in body["error"]


def test_credentials_discover_model_failure_returns_503(client, monkeypatch):
    import app as mighty

    call_count = []

    def _fail_discovery(source, content, context):
        call_count.append(1)
        raise DiscoveryProviderError("OpenAI field discovery failed (gpt-5.4-mini): 429 quota")

    monkeypatch.setattr(mighty, "discover_fields_with_provider", _fail_discovery)
    csrf = _seed_discover_account(client, monkeypatch)

    r = client.post("/credentials/discover/amex", data={"_csrf": csrf})
    assert r.status_code == 503
    assert "OpenAI field discovery failed" in r.get_json()["error"]

    r2 = client.post("/credentials/discover/amex", data={"_csrf": csrf})
    assert r2.status_code == 503
    assert "OpenAI field discovery failed" in r2.get_json()["error"]
    assert len(call_count) == 1


def test_claude_discover_fields_schema_cache(client, monkeypatch):
    import app as mighty

    call_count = []

    def _fake_discovery(source, content, context):
        call_count.append(1)
        return DiscoveryResult(
            fields=[SAMPLE_FIELD],
            provider="openai",
            model="gpt-5.4-mini",
        )

    monkeypatch.setattr(mighty, "discover_fields_with_provider", _fake_discovery)
    monkeypatch.setattr(mighty, "_post_filter_fields", lambda fields, source="": fields)

    text = "Balance $100\nGold Medallion status"
    first = mighty.claude_discover_fields(text, "Amex", source="amex")
    count_after_first = len(call_count)
    second = mighty.claude_discover_fields(text, "Amex", source="amex")
    assert first == [SAMPLE_FIELD]
    assert second == [SAMPLE_FIELD]
    assert len(call_count) == count_after_first


def test_claude_discover_fields_failure_negative_cache(client, monkeypatch):
    import app as mighty

    call_count = []

    def _fail_discovery(source, content, context):
        call_count.append(1)
        raise DiscoveryProviderError("OpenAI field discovery failed (gpt-5.4-mini): 429 quota")

    monkeypatch.setattr(mighty, "discover_fields_with_provider", _fail_discovery)

    text = "Balance $100\nGold Medallion status"
    with pytest.raises(mighty.DiscoveryError):
        mighty.claude_discover_fields(text, "Amex", source="amex")
    count_after_first = len(call_count)
    with pytest.raises(mighty.DiscoveryError):
        mighty.claude_discover_fields(text, "Amex", source="amex")
    assert len(call_count) == count_after_first


def test_claude_discover_fields_respects_max_chars(client, monkeypatch):
    import app as mighty

    captured = []

    def _fake_prepare(raw_text, hint_phrases=None, max_chars=None):
        captured.append(len(raw_text))
        from mighty.field_discovery_preprocess import PreprocessResult, PreprocessStats
        stats = PreprocessStats(
            raw_chars=len(raw_text),
            normalized_chars=len(raw_text),
            filtered_chars=len(raw_text),
            sections_chars=len(raw_text),
            final_chars=len(raw_text),
        )
        return PreprocessResult(text=raw_text[:max_chars or len(raw_text)], stats=stats)

    def _fake_discovery(source, content, context):
        return DiscoveryResult(fields=[], provider="openai", model="gpt-5.4-mini")

    monkeypatch.setenv("AI_FIELD_DISCOVERY_MAX_CHARS", "500")
    monkeypatch.setattr(mighty, "discover_fields_with_provider", _fake_discovery)
    monkeypatch.setattr(mighty, "prepare_discovery_input", _fake_prepare)
    monkeypatch.setattr(mighty, "_post_filter_fields", lambda fields, source="": fields)

    mighty.claude_discover_fields("x" * 5000, "Amex", source="amex")
    assert captured == [5000]


def test_claude_discover_fields_kill_switch_skips_openai(client, monkeypatch):
    import app as mighty

    monkeypatch.setenv("AI_FIELD_DISCOVERY_ENABLED", "false")

    def _should_not_run(*args, **kwargs):
        raise AssertionError("OpenAI should not be called when discovery is disabled")

    monkeypatch.setattr(mighty, "discover_fields_with_provider", _should_not_run)
    assert mighty.claude_discover_fields("Balance $100", "Amex", source="amex") == []
