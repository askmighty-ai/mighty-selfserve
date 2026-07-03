"""Tests for the AI platform integration helpers."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_platform import (
    build_field_discovery_context,
    discovery_log_suffix,
    render_field_discovery_prompt_text,
    render_missing_pages_prompt,
)
from mighty.ai_provider import DiscoveryResult
from mighty.ai_metrics import AIMetrics


class TestAIPlatform:
    def test_build_field_discovery_context_includes_prompt_metadata(self):
        context = build_field_discovery_context(
            site_name="Delta Air Lines",
            source="delta",
            snippets="Balance 45,320 miles",
            today="July 2, 2026",
            category_hint="",
        )
        assert context.prompt_id == "field_discovery"
        assert context.prompt_version == "1.0.0"
        assert "Delta Air Lines" in context.prompt
        assert "45,320" in context.prompt

    def test_render_missing_pages_prompt(self):
        text = render_missing_pages_prompt(source="amex", missing_str="certificates")
        assert "amex" in text
        assert "certificates" in text
        assert "/my-account/certificates" in text or "/loyalty/wallet" in text

    def test_render_field_discovery_prompt_text(self):
        text = render_field_discovery_prompt_text(
            site_name="Amex",
            snippets="Balance $100",
            today="July 2, 2026",
            category_hint="",
        )
        assert "Amex" in text
        assert "Balance $100" in text

    def test_discovery_log_suffix_with_metrics(self):
        result = DiscoveryResult(
            fields=[],
            provider="openai",
            model="gpt-5.4-mini",
            metrics=AIMetrics(
                provider="openai",
                model="gpt-5.4-mini",
                latency_ms=42.5,
                cache_hit=False,
                estimated_cost_usd=0.000012,
                prompt_version="1.0.0",
                prompt_id="field_discovery",
            ),
        )
        suffix = discovery_log_suffix(result)
        assert "field_discovery@1.0.0" in suffix
        assert "latency=42ms" in suffix
        assert "cache_hit=False" in suffix
        assert "est_cost=$" in suffix

    def test_discovery_log_suffix_without_metrics(self):
        result = DiscoveryResult(fields=[], provider="openai", model="gpt-5.4-mini")
        assert discovery_log_suffix(result) == ""
