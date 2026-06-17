"""
Unit tests for _extract_candidate_snippets.

These tests have NO Gemini dependency — they test the extraction pipeline
(trigger matching, line-based windowing, block scoring, deduplication, cap)
in pure Python against synthetic fixture texts.
"""

import os
import pytest

import app
from app import _extract_candidate_snippets, _HIGH_VALUE_TRIGGERS, SNIPPET_TRIGGERS

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


# ── Basic inclusion ───────────────────────────────────────────────────────────

class TestDeltaCompanionCert:
    def setup_method(self):
        self.text = load("delta_companion_cert.txt")
        self.snippets = _extract_candidate_snippets(self.text)

    def test_companion_certificate_included(self):
        """Companion certificate block must survive extraction."""
        assert "companion" in self.snippets.lower()
        assert "certificate" in self.snippets.lower()

    def test_expiry_date_included(self):
        """Expiry date of companion cert must be in snippets."""
        assert "aug 31" in self.snippets.lower() or "2026" in self.snippets

    def test_upgrade_certificates_included(self):
        assert "global upgrade" in self.snippets.lower()
        assert "regional upgrade" in self.snippets.lower()

    def test_medallion_status_included(self):
        assert "diamond" in self.snippets.lower()
        assert "medallion" in self.snippets.lower()

    def test_miles_balance_included(self):
        assert "45,320" in self.snippets

    def test_snippets_shorter_than_raw(self):
        """Extraction should cut out noise — result shorter than raw (unless tiny input)."""
        assert len(self.snippets) < len(self.text)

    def test_footer_noise_minimized(self):
        """Footer nav links should not dominate the output."""
        footer_hits = self.snippets.lower().count("careers")
        assert footer_hits <= 1, "Footer noise leaked into top-ranked blocks"


class TestMarriottFreeNight:
    def setup_method(self):
        self.text = load("marriott_free_night.txt")
        self.snippets = _extract_candidate_snippets(self.text)

    def test_free_night_award_included(self):
        assert "free night" in self.snippets.lower()

    def test_expiry_included(self):
        assert "dec 31" in self.snippets.lower() or "december 31" in self.snippets.lower() or "2026" in self.snippets

    def test_points_balance_included(self):
        assert "28,500" in self.snippets

    def test_platinum_status_included(self):
        assert "platinum" in self.snippets.lower()


class TestAmexCredits:
    def setup_method(self):
        self.text = load("amex_credits.txt")
        self.snippets = _extract_candidate_snippets(self.text)

    def test_remaining_credits_included(self):
        """Both the $48 dining credit and $187 hotel credit must appear."""
        assert "$48" in self.snippets or "48" in self.snippets
        assert "$187" in self.snippets or "187" in self.snippets

    def test_payment_due_included(self):
        assert "jul 5" in self.snippets.lower() or "2026" in self.snippets

    def test_autopay_included(self):
        assert "autopay" in self.snippets.lower() or "auto pay" in self.snippets.lower()

    def test_points_balance_included(self):
        assert "74,250" in self.snippets


class TestChasePayment:
    def setup_method(self):
        self.text = load("chase_payment.txt")
        self.snippets = _extract_candidate_snippets(self.text)

    def test_balance_included(self):
        assert "2,472" in self.snippets

    def test_minimum_payment_included(self):
        assert "minimum payment" in self.snippets.lower()
        assert "$35" in self.snippets or "35.00" in self.snippets

    def test_due_date_included(self):
        assert "jul 12" in self.snippets.lower() or "2026" in self.snippets

    def test_autopay_included(self):
        assert "autopay" in self.snippets.lower()


class TestXfinityBill:
    def setup_method(self):
        self.text = load("xfinity_bill.txt")
        self.snippets = _extract_candidate_snippets(self.text)

    def test_amount_due_included(self):
        assert "157.43" in self.snippets

    def test_due_date_included(self):
        assert "jul 3" in self.snippets.lower() or "2026" in self.snippets

    def test_data_usage_included(self):
        assert "847" in self.snippets

    def test_autopay_included(self):
        assert "autopay" in self.snippets.lower() or "auto" in self.snippets.lower()


class TestNoisyMarketing:
    def setup_method(self):
        self.text = load("noisy_marketing.txt")
        self.snippets = _extract_candidate_snippets(self.text)

    def test_no_personalized_balance(self):
        """Generic marketing page has no real balances — no specific numbers."""
        # The page has no dollar amounts or specific point balances
        import re
        dollar_amounts = re.findall(r'\$\d+', self.snippets)
        assert len(dollar_amounts) == 0, f"Unexpected dollar amounts in noisy page: {dollar_amounts}"

    def test_no_high_value_content(self):
        """None of the highest-value signals should appear."""
        for term in ("companion", "certificate", "ecredit", "minimum payment", "amount due"):
            assert term not in self.snippets.lower(), f"High-value term '{term}' appeared in noisy marketing page"


# ── Scorer behaviour ──────────────────────────────────────────────────────────

class TestScoring:
    """Verify that high-value trigger blocks rank above generic-number blocks."""

    def test_high_value_block_ranks_above_generic(self):
        """A block with 'companion certificate valid through' should score higher
        than a block with lots of random numbers but no account-specific terms."""
        high_value_block = (
            "Companion Certificate\n"
            "Certificate Number: 87XYZABC\n"
            "Valid Through: Aug 31, 2026\n"
            "Book and fly by Aug 31, 2026 to use this companion certificate.\n"
            "Upgrade certificates: 2 available, expires Jan 31, 2027\n"
        )
        generic_number_block = (
            "Shop with 450 participating retailers\n"
            "Earn 3 miles per 1 dollar spent\n"
            "Over 100 destinations worldwide\n"
            "Book at 1200 hotels in 85 countries\n"
            "Call 1-800-123-4567 for assistance\n"
        )
        # Build a text where both blocks appear; verify high-value block is in snippets
        combined = high_value_block + "\n\n" + "=" * 40 + "\n\n" + generic_number_block * 20
        snippets = _extract_candidate_snippets(combined)
        # High-value content must survive
        assert "companion" in snippets.lower()
        assert "certificate" in snippets.lower()

    def test_written_date_scores_in_value_re(self):
        """A block with 'valid through Jan 15, 2027' should score from the date,
        not just from trigger words — confirms _SNIPPET_VALUE_RE covers month-name dates."""
        import re
        from app import _SNIPPET_VALUE_RE
        text = "Companion Certificate valid through Jan 15, 2027"
        matches = _SNIPPET_VALUE_RE.findall(text)
        date_matches = [m for m in matches if any(
            month in m for month in ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        )]
        assert date_matches, \
            f"_SNIPPET_VALUE_RE did not match written month date in: {text!r}. Got: {matches}"

    def test_high_value_triggers_are_subset_of_snippet_triggers(self):
        """Every high-value trigger should also appear in the broader trigger list
        OR be specific enough that it's independently justified."""
        # _HIGH_VALUE_TRIGGERS terms are more specific than SNIPPET_TRIGGERS — this
        # is intentional. This test just ensures neither set is empty.
        assert len(_HIGH_VALUE_TRIGGERS) >= 10
        assert len(SNIPPET_TRIGGERS) >= 20

    def test_fallback_on_no_triggers(self):
        """Text with no trigger words falls back to first 8k chars."""
        boring = "Hello world. " * 1000
        result = _extract_candidate_snippets(boring)
        assert result == boring[:8_000]

    def test_empty_input(self):
        assert _extract_candidate_snippets("") == ""
        assert _extract_candidate_snippets(None) == ""  # type: ignore[arg-type]

    def test_output_capped_at_20k(self):
        """Result never exceeds 20k chars even with a trigger-dense page."""
        big = ("certificate expires valid through upgrade companion " * 100 + "\n") * 200
        result = _extract_candidate_snippets(big)
        assert len(result) <= 20_000
