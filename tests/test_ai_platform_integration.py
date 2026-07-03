"""
Integration tests for the production AI platform layer.

Uses mocked providers — no live API keys required.
"""

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_metrics import get_metrics_collector
from mighty.ai_provider import (
    DiscoveryContext,
    DiscoveryResult,
    OpenAIProvider,
    discover_fields_with_provider,
)
from mighty.prompts import render_prompt

SAMPLE_FIELD = {
    "key": "balance",
    "label": "Balance",
    "value": "$100",
    "value_type": "currency",
    "confidence": 0.99,
    "source_snippet": "Balance $100",
}


class TestAIPlatformIntegration:
    def test_end_to_end_prompt_to_provider_metrics(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        rendered = render_prompt(
            "field_discovery",
            site="American Express",
            text="Balance $100",
            today="July 2, 2026",
            category_hint="",
        )
        context = DiscoveryContext(
            site_name="American Express",
            source="amex",
            prompt=rendered.text,
            prompt_id=rendered.prompt_id,
            prompt_version=rendered.version,
        )

        fake_response = type(
            "Resp",
            (),
            {
                "choices": [
                    type(
                        "Choice",
                        (),
                        {
                            "message": type(
                                "Msg",
                                (),
                                {"content": json.dumps({"fields": [SAMPLE_FIELD]})},
                            )()
                        },
                    )()
                ]
            },
        )()

        class FakeCompletions:
            def create(self, **kwargs):
                assert kwargs["messages"][0]["content"] == rendered.text
                return fake_response

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

        result = discover_fields_with_provider("amex", "Balance $100", context)
        assert result.fields[0]["key"] == "balance"
        assert result.metrics is not None
        assert result.metrics.prompt_id == "field_discovery"
        assert result.metrics.prompt_version == "1.0.0"
        assert result.metrics.cache_hit is False

    def test_schema_cache_records_cache_hit_metrics(self, monkeypatch):
        import app as mighty

        def _fake_discovery(source, content, context):
            return DiscoveryResult(fields=[], provider="openai", model="gpt-5.4-mini")

        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setattr(mighty, "discover_fields_with_provider", _fake_discovery)
        monkeypatch.setattr(mighty, "_post_filter_fields", lambda fields, source="": fields)

        collector = get_metrics_collector()
        collector.clear()

        mighty.get_field_schema_cache().record_success(
            mighty.schema_cache_key("amex", "cached-content"),
            [],
        )
        monkeypatch.setattr(
            mighty.get_field_schema_cache(),
            "get_fields",
            lambda key: [],
        )

        mighty.claude_discover_fields("Balance $100", "Amex", source="amex")

        last = collector.recent(1)[0]
        assert last.cache_hit is True
        assert last.prompt_version == "1.0.0"

    def test_retry_used_on_transient_openai_failure(self, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AI_REQUEST_RETRY_BACKOFF_SECONDS", "0")

        calls = []

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(1)
                if len(calls) < 2:
                    raise RuntimeError("429 rate limit")
                return type(
                    "Resp",
                    (),
                    {
                        "choices": [
                            type(
                                "Choice",
                                (),
                                {
                                    "message": type(
                                        "Msg",
                                        (),
                                        {
                                            "content": json.dumps(
                                                {"fields": [SAMPLE_FIELD]}
                                            )
                                        },
                                    )()
                                },
                            )()
                        ]
                    },
                )()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        provider = OpenAIProvider()
        provider._client = FakeClient()

        rendered = render_prompt(
            "field_discovery",
            site="Amex",
            text="Balance $100",
            today="July 2, 2026",
            category_hint="",
        )
        context = DiscoveryContext(
            site_name="Amex",
            source="amex",
            prompt=rendered.text,
            prompt_id=rendered.prompt_id,
            prompt_version=rendered.version,
        )

        result = provider.discover_fields("amex", "Balance $100", context)
        assert len(calls) == 2
        assert result.fields[0]["key"] == "balance"
