"""Tests for AI metrics collection."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_metrics import (
    AIMetrics,
    build_metrics,
    cache_hit_metrics,
    estimate_cost_usd,
    get_metrics_collector,
    record_metrics,
)


class TestAIMetrics:
    def test_build_metrics_includes_all_fields(self):
        metrics = build_metrics(
            provider="openai",
            model="gpt-5.4-mini",
            latency_ms=123.4,
            cache_hit=False,
            prompt_id="field_discovery",
            prompt_version="1.0.0",
            input_chars=4000,
            output_chars=800,
        )
        assert metrics.provider == "openai"
        assert metrics.model == "gpt-5.4-mini"
        assert metrics.latency_ms == 123.4
        assert metrics.cache_hit is False
        assert metrics.prompt_id == "field_discovery"
        assert metrics.prompt_version == "1.0.0"
        assert metrics.estimated_cost_usd is not None
        assert metrics.estimated_cost_usd > 0

    def test_cache_hit_metrics(self):
        collector = get_metrics_collector()
        collector.clear()
        metrics = cache_hit_metrics(
            provider="openai",
            model="gpt-5.4-mini",
            prompt_id="field_discovery",
            prompt_version="1.0.0",
        )
        assert metrics.cache_hit is True
        assert metrics.estimated_cost_usd == 0.0
        assert collector.recent(1)[0] is metrics

    def test_estimate_cost_unknown_provider_returns_none(self):
        assert (
            estimate_cost_usd(
                provider="unknown",
                model="nope",
                input_chars=1000,
            )
            is None
        )

    def test_record_metrics_appends_to_collector(self):
        collector = get_metrics_collector()
        collector.clear()
        metrics = AIMetrics(
            provider="gemini",
            model="gemini-2.5-flash",
            latency_ms=50.0,
            cache_hit=False,
            estimated_cost_usd=0.0001,
            prompt_version="1.0.0",
            prompt_id="field_discovery",
        )
        record_metrics(metrics)
        assert collector.recent(1)[0].provider == "gemini"
