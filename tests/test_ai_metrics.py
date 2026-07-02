"""Tests for AI metrics collection."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_metrics import (
    build_metrics,
    cache_hit_metrics,
    estimate_cost_usd,
    get_metrics_collector,
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
            input_text="hello world",
            output_text='{"fields":[]}',
        )
        assert metrics.provider == "openai"
        assert metrics.model == "gpt-5.4-mini"
        assert metrics.latency_ms == 123.4
        assert metrics.cache_hit is False
        assert metrics.prompt_id == "field_discovery"
        assert metrics.prompt_version == "1.0.0"
        assert metrics.estimated_cost_usd is not None

    def test_cache_hit_has_zero_cost(self):
        metrics = cache_hit_metrics(
            provider="openai",
            model="gpt-5.4-mini",
            prompt_id="field_discovery",
            prompt_version="1.0.0",
        )
        assert metrics.cache_hit is True
        assert metrics.estimated_cost_usd is None

    def test_metrics_collector_records(self):
        collector = get_metrics_collector()
        collector.clear()
        metrics = build_metrics(
            provider="openai",
            model="gpt-5.4-mini",
            latency_ms=10.0,
            cache_hit=False,
            prompt_id="field_discovery",
            prompt_version="1.0.0",
        )
        collector.record(metrics)
        assert collector.recent(1)[0] is metrics

    def test_estimate_cost_unknown_model_returns_none(self):
        assert (
            estimate_cost_usd(
                "unknown",
                "unknown-model-xyz",
                input_chars=1000,
                output_chars=1000,
            )
            is None
        )
