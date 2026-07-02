"""Tests for the AI provider abstraction."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_provider import (
    DiscoveryContext,
    DiscoveryProviderError,
    DiscoveryResult,
    DiscoveryValidationError,
    GeminiProvider,
    OpenAIProvider,
    ai_provider_name,
    discover_fields_with_provider,
    get_field_discovery_provider,
    validate_discovered_fields,
)
from mighty.field_discovery import DiscoveryUnavailableError


SAMPLE_FIELD = {
    "key": "balance",
    "label": "Balance",
    "value": "$100",
    "value_type": "currency",
    "confidence": 0.99,
    "source_snippet": "Balance $100",
}


class TestProviderSelection:
    def test_openai_selected_when_ai_provider_openai(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "openai")
        assert ai_provider_name() == "openai"
        assert isinstance(get_field_discovery_provider(), OpenAIProvider)

    def test_gemini_selected_when_ai_provider_gemini(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "gemini")
        assert ai_provider_name() == "gemini"
        assert isinstance(get_field_discovery_provider(), GeminiProvider)

    def test_missing_openai_api_key_returns_clean_error(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        provider = OpenAIProvider()
        assert provider.is_configured() is False
        with pytest.raises(DiscoveryUnavailableError, match="OPENAI_API_KEY"):
            provider.discover_fields(
                "amex",
                "Balance $100",
                DiscoveryContext(site_name="Amex", source="amex", prompt="prompt"),
            )


class TestValidation:
    def test_validate_discovered_fields_accepts_array(self):
        fields = validate_discovered_fields([SAMPLE_FIELD])
        assert fields[0]["key"] == "balance"
        assert fields[0]["value_type"] == "currency"

    def test_validate_discovered_fields_accepts_wrapped_object(self):
        fields = validate_discovered_fields({"fields": [SAMPLE_FIELD]})
        assert len(fields) == 1

    def test_validate_requires_value_type(self):
        bad = dict(SAMPLE_FIELD)
        del bad["value_type"]
        with pytest.raises(DiscoveryValidationError, match="value_type"):
            validate_discovered_fields([bad])


class TestDiscoverFieldsWithProvider:
    def test_openai_failure_returns_clean_error(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        class FakeCompletions:
            def create(self, **kwargs):
                raise RuntimeError("429 quota")

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        provider = OpenAIProvider()
        provider._client = FakeClient()
        monkeypatch.setattr(
            "mighty.ai_provider.get_configured_field_discovery_provider",
            lambda name=None: provider,
        )

        with pytest.raises(DiscoveryProviderError, match="OpenAI field discovery failed"):
            discover_fields_with_provider(
                "amex",
                "Balance $100",
                DiscoveryContext(site_name="Amex", source="amex", prompt="prompt"),
            )

    def test_openai_failure_does_not_fallback_without_flag(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.delenv("AI_ALLOW_FALLBACK", raising=False)

        gemini_called = []

        class FailingOpenAI(OpenAIProvider):
            def discover_fields(self, source, content, context):
                raise DiscoveryProviderError("OpenAI field discovery failed (test): boom")

        class TrackingGemini(GeminiProvider):
            def discover_fields(self, source, content, context):
                gemini_called.append(1)
                return DiscoveryResult(fields=[], provider="gemini", model="gemini-2.5-flash")

        monkeypatch.setattr(
            "mighty.ai_provider.get_configured_field_discovery_provider",
            lambda name=None: FailingOpenAI(),
        )
        monkeypatch.setattr(
            "mighty.ai_provider.GeminiProvider",
            TrackingGemini,
        )

        with pytest.raises(DiscoveryProviderError):
            discover_fields_with_provider(
                "amex",
                "Balance $100",
                DiscoveryContext(site_name="Amex", source="amex", prompt="prompt"),
            )
        assert gemini_called == []
