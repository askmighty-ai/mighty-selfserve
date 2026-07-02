"""Unit tests for the field discovery preprocessing pipeline."""

import os

from mighty.discovery_pipeline import (
    PipelineStats,
    extract_visible_sections,
    normalize_input,
    pipeline_cache_fingerprint,
    prepare_discovery_input,
    remove_irrelevant_regions,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


class TestNormalizeInput:
    def test_html_strips_tags(self):
        normalized = normalize_input(load("delta_wallet.html"))
        assert "45,320 miles" in normalized
        assert "<" not in normalized


class TestRemoveIrrelevantRegions:
    def test_drops_navigation(self):
        cleaned = remove_irrelevant_regions("Navigation: Book\nBalance 100")
        assert "Navigation:" not in cleaned
        assert "100" in cleaned


class TestExtractVisibleSections:
    def test_drops_marketing_only_section(self):
        text = (
            "=== URL: https://example.com/marketing ===\n"
            "Earn Miles on Everything You Do\n\n"
            "=== URL: https://example.com/wallet ===\n"
            "Points Balance\n28,500 points\n"
        )
        sections = extract_visible_sections(text)
        assert "28,500" in sections
        assert "Earn Miles on Everything" not in sections


class TestPrepareDiscoveryInput:
    def test_delta_fixture(self):
        result = prepare_discovery_input(load("delta_companion_cert.txt"))
        assert "45,320" in result.text
        assert result.stats.prepared_chars <= result.stats.raw_chars

    def test_fingerprint_stable(self):
        raw = load("delta_companion_cert.txt")
        assert pipeline_cache_fingerprint(raw) == pipeline_cache_fingerprint(raw.rstrip() + "   ")


class TestPipelineStats:
    def test_token_reduction(self):
        stats = PipelineStats(4000, 3800, 3000, 2500, 1000, 1000, 250)
        assert stats.token_reduction_pct == 75.0
