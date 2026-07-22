"""Pure discovery matching and confidence tests (Milestone 7)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.discovery_policy import (
    AUTO_ENROLL_MIN_CONFIDENCE,
    DISPOSITION_ALREADY_ENROLLED,
    DISPOSITION_AMBIGUOUS,
    DISPOSITION_DISMISSED,
    DISPOSITION_ELIGIBLE,
    MATCH_EXACT,
    MATCH_SUFFIX,
    decide_discovery,
    match_sender_domain,
    score_confidence,
)


class TestMatchSenderDomain:
    def test_exact_amex(self):
        match = match_sender_domain("americanexpress.com")
        assert match is not None
        assert match.provider == "amex"
        assert match.match_method == MATCH_EXACT

    def test_suffix_alias(self):
        match = match_sender_domain("news.delta.com")
        assert match is not None
        assert match.provider == "delta"
        # news.delta.com is an exact registry key
        assert match.match_method in {MATCH_EXACT, MATCH_SUFFIX}

    def test_unknown_domain(self):
        assert match_sender_domain("totally-unknown-example.test") is None

    def test_aliases_resolve_same_provider(self):
        a = match_sender_domain("aexp.com")
        b = match_sender_domain("americanexpress.com")
        assert a and b
        assert a.provider == b.provider == "amex"


class TestConfidenceAndDisposition:
    def test_high_confidence_auto_enroll(self):
        decision = decide_discovery(
            domain="americanexpress.com",
            email_count=5,
            is_enrolled=False,
            is_dismissed=False,
            auto_enroll_providers=frozenset({"amex"}),
        )
        assert decision is not None
        assert decision.confidence >= AUTO_ENROLL_MIN_CONFIDENCE
        assert decision.disposition == DISPOSITION_ELIGIBLE

    def test_known_but_not_auto_enroll_is_ambiguous(self):
        decision = decide_discovery(
            domain="delta.com",
            email_count=10,
            is_enrolled=False,
            is_dismissed=False,
            auto_enroll_providers=frozenset({"amex"}),
        )
        assert decision is not None
        assert decision.disposition == DISPOSITION_AMBIGUOUS

    def test_dismissed_preserved(self):
        decision = decide_discovery(
            domain="americanexpress.com",
            email_count=5,
            is_enrolled=False,
            is_dismissed=True,
            auto_enroll_providers=frozenset({"amex"}),
        )
        assert decision.disposition == DISPOSITION_DISMISSED

    def test_already_enrolled(self):
        decision = decide_discovery(
            domain="americanexpress.com",
            email_count=5,
            is_enrolled=True,
            is_dismissed=False,
            auto_enroll_providers=frozenset({"amex"}),
        )
        assert decision.disposition == DISPOSITION_ALREADY_ENROLLED

    def test_score_deterministic(self):
        match = match_sender_domain("chase.com")
        assert match is not None
        assert score_confidence(match, 1) == score_confidence(match, 1)
        assert score_confidence(match, 5) >= score_confidence(match, 1)
