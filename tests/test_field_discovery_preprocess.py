"""Tests for the field discovery preprocessing pipeline."""

import os
import re
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.field_discovery_preprocess import (
    SNIPPET_TRIGGERS,
    compress_discovery_text,
    estimate_tokens,
    extract_visible_sections,
    normalize_discovery_input,
    prepare_discovery_input,
    remove_irrelevant_regions,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


class TestNormalize:
    def test_plain_text_preserved(self):
        text = "Balance: $100\nGold Medallion status"
        assert "Balance" in normalize_discovery_input(text)
        assert "Gold Medallion" in normalize_discovery_input(text)

    def test_html_stripped_to_text(self):
        html = load_fixture("amex_credit.html")
        normalized = normalize_discovery_input(html)
        assert "<html" not in normalized.lower()
        assert "124,350" in normalized
        assert "Platinum Card Benefits" in normalized

    def test_url_markers_preserved(self):
        text = "=== URL: https://delta.com/wallet ===\nBalance 45,320"
        normalized = normalize_discovery_input(text)
        assert "=== URL:" in normalized
        assert "45,320" in normalized


class TestRemoveIrrelevant:
    def test_footer_and_nav_removed(self):
        text = load_fixture("delta_companion_cert.txt")
        filtered = remove_irrelevant_regions(text)
        assert "privacy policy" not in filtered.lower()
        assert "45,320" in filtered

    def test_noisy_marketing_reduced(self):
        text = load_fixture("noisy_marketing.txt")
        filtered = remove_irrelevant_regions(text)
        assert "how it works" not in filtered.lower()
        assert len(filtered) < len(text)


class TestExtractSections:
    def test_delta_key_data_retained(self):
        text = load_fixture("delta_companion_cert.txt")
        sections = extract_visible_sections(text)
        for fragment in ("companion", "45,320", "diamond", "2026"):
            assert fragment.lower() in sections.lower()

    def test_hint_phrases_force_include(self):
        text = "Random line\nSpecial loyalty tier: Mosaic\nOther line"
        sections = extract_visible_sections(text, hint_phrases=["Special loyalty tier"])
        assert "Mosaic" in sections


class TestCompress:
    def test_deduplicates_lines(self):
        text = "Balance $100\nBalance $100\nPoints 500"
        compressed = compress_discovery_text(text)
        assert compressed.count("Balance $100") == 1
        assert "Points 500" in compressed

    def test_respects_max_chars(self):
        compressed = compress_discovery_text("x" * 500, max_chars=100)
        assert len(compressed) == 100


class TestPreparePipeline:
    def test_end_to_end_delta(self):
        raw = load_fixture("delta_companion_cert.txt")
        result = prepare_discovery_input(raw)
        assert result.stats.raw_chars == len(raw)
        assert result.stats.final_chars <= result.stats.raw_chars
        assert "45,320" in result.text
        assert "companion" in result.text.lower()

    def test_html_fixture_pipeline(self):
        raw = load_fixture("amex_credit.html")
        result = prepare_discovery_input(raw)
        assert result.stats.final_chars < result.stats.raw_chars
        assert "124,350" in result.text

    def test_empty_input(self):
        result = prepare_discovery_input("")
        assert result.text == ""
        assert result.stats.final_chars == 0


class TestPreprocessBenchmarks:
    """Assert meaningful token reduction on representative fixtures."""

    @pytest.mark.parametrize("fixture_name,min_reduction", [
        ("delta_companion_cert.txt", 0.05),
        ("noisy_marketing.txt", 0.25),
        ("amex_credits.txt", 0.05),
        ("marriott_free_night.txt", 0.05),
    ])
    def test_token_reduction(self, fixture_name, min_reduction):
        raw = load_fixture(fixture_name)
        result = prepare_discovery_input(raw)
        assert result.stats.reduction_ratio >= min_reduction, (
            f"{fixture_name}: expected >={min_reduction:.0%} reduction, "
            f"got {result.stats.reduction_ratio:.0%} "
            f"({result.stats.raw_chars} → {result.stats.final_chars} chars)"
        )

    def test_noisy_page_has_no_dollar_amounts(self):
        raw = load_fixture("noisy_marketing.txt")
        result = prepare_discovery_input(raw)
        dollars = re.findall(r"\$\d+", result.text)
        assert not dollars, f"Marketing page still has dollar amounts: {dollars}"

    def test_account_pages_retain_required_fragments(self):
        cases = [
            ("delta_companion_cert.txt", ["45,320", "companion", "diamond"]),
            ("amex_credit.txt", ["124,350", "187 remaining"]),
            ("hilton_honors.txt", ["112,400", "diamond"]),
        ]
        for fixture, fragments in cases:
            result = prepare_discovery_input(load_fixture(fixture))
            missing = [f for f in fragments if f.lower() not in result.text.lower()]
            assert not missing, f"{fixture} missing after preprocess: {missing}"


class TestEstimateTokens:
    def test_scales_with_length(self):
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("a" * 40) == 10


class TestTriggerConstants:
    def test_trigger_lists_non_empty(self):
        assert len(SNIPPET_TRIGGERS) >= 20
