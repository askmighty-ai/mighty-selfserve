"""Unit tests for provider access probe status classification."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.provider_access_probe import (
    PROBE_BLOCKED,
    PROBE_ERROR,
    PROBE_NEEDS_SIGN_IN,
    PROBE_NOT_STARTED,
    PROBE_SIGNED_IN_DATA,
    PROBE_SIGNED_IN_NO_DATA,
    classify_probe_result,
    detect_private_data,
    detect_signed_in_from_text,
    evaluate_probe_payload,
    is_marketing_url,
    merge_probe_summaries,
)


# ── Amex fixtures ─────────────────────────────────────────────────────────────

AMEX_ACCOUNT_URL = "https://www.americanexpress.com/en-us/account/"
AMEX_LOGIN_URL = "https://www.americanexpress.com/en-us/account/login"
AMEX_MARKETING_URL = "https://www.americanexpress.com/en-us/credit-cards/"

AMEX_LOGGED_IN_TEXT = """
Account Home
Membership Rewards
Available Points: 125,430
Recent Activity
Card ending in 1234
Statement balance $1,234.56
"""

AMEX_LOGGED_IN_NO_PRIVATE = """
Account Home
Membership Rewards
Recent Activity
Manage Account
Account Services
View your card details and payment options on americanexpress.com
"""

AMEX_LOGIN_TEXT = """
Sign in to your account
User ID
Password
Show password
Forgot password
Enter your credentials to access your American Express account online
"""

AMEX_MARKETING_TEXT = """
Apply for a Card
See all credit cards
Travel benefits and offers
Explore our cards and find the right American Express product for you
"""


# ── Delta fixtures ────────────────────────────────────────────────────────────

DELTA_ACCOUNT_URL = "https://www.delta.com/myprofile/"
DELTA_LOGIN_URL = "https://www.delta.com/login"
DELTA_MARKETING_URL = "https://www.delta.com/us/en/flights"

DELTA_LOGGED_IN_WITH_DATA = """
My SkyMiles
SkyMiles Number: 1234567890
Available Miles 45,230
Medallion Status: Gold Medallion
My Trips
"""

DELTA_LOGGED_IN_NO_PRIVATE = """
Welcome back
My SkyMiles
Member since 2015
View your profile and account preferences on delta.com
"""

DELTA_LOGIN_TEXT = """
Sign In
SkyMiles Number or Username
Password
Forgot your password?
Log in to your Delta SkyMiles account to manage trips and rewards
"""


class TestAmexSignedInDetection:
    def test_account_dashboard_counts_as_signed_in(self):
        assert detect_signed_in_from_text("amex", AMEX_ACCOUNT_URL, AMEX_LOGGED_IN_TEXT)

    def test_login_page_not_signed_in(self):
        assert not detect_signed_in_from_text("amex", AMEX_LOGIN_URL, AMEX_LOGIN_TEXT)

    def test_marketing_page_not_signed_in(self):
        assert not detect_signed_in_from_text("amex", AMEX_MARKETING_URL, AMEX_MARKETING_TEXT)

    def test_is_marketing_url(self):
        assert is_marketing_url("amex", AMEX_MARKETING_URL)
        assert not is_marketing_url("amex", AMEX_ACCOUNT_URL)


class TestAmexPrivateData:
    def test_membership_rewards_balance(self):
        found, etype, snippet = detect_private_data("amex", dom_text=AMEX_LOGGED_IN_TEXT)
        assert found
        assert etype == "dom_text"
        assert "125,430" in (snippet or "")

    def test_statement_balance(self):
        text = "Statement balance $2,500.00\nAccount Home"
        found, _, snippet = detect_private_data("amex", dom_text=text)
        assert found
        assert "2,500" in (snippet or "")

    def test_card_ending(self):
        text = "Account Home\nCard ending in 9876\nRecent Activity"
        found, _, snippet = detect_private_data("amex", dom_text=text)
        assert found
        assert "9876" in (snippet or "")

    def test_no_private_data_when_only_session_signals(self):
        found, _, _ = detect_private_data("amex", dom_text=AMEX_LOGGED_IN_NO_PRIVATE)
        assert not found


class TestDeltaSignedInDetection:
    def test_myprofile_counts_as_signed_in(self):
        assert detect_signed_in_from_text("delta", DELTA_ACCOUNT_URL, DELTA_LOGGED_IN_WITH_DATA)

    def test_login_page_not_signed_in(self):
        assert not detect_signed_in_from_text("delta", DELTA_LOGIN_URL, DELTA_LOGIN_TEXT)

    def test_marketing_page_not_signed_in(self):
        text = "Book flights\nFind deals\nExplore destinations"
        assert not detect_signed_in_from_text("delta", DELTA_MARKETING_URL, text)


class TestDeltaPrivateData:
    def test_skymiles_number(self):
        found, etype, snippet = detect_private_data("delta", dom_text=DELTA_LOGGED_IN_WITH_DATA)
        assert found
        assert etype == "dom_text"
        assert "1234567890" in (snippet or "")

    def test_miles_balance(self):
        text = "My SkyMiles\nAvailable Miles 12,500"
        found, _, snippet = detect_private_data("delta", dom_text=text)
        assert found
        assert "12,500" in (snippet or "")

    def test_medallion_status(self):
        text = "Medallion Status: Platinum Medallion\nWelcome back"
        found, _, snippet = detect_private_data("delta", dom_text=text)
        assert found
        assert "Platinum" in (snippet or "")

    def test_ecredits(self):
        text = "My Wallet\neCredits $150.00\nMy SkyMiles"
        found, _, snippet = detect_private_data("delta", dom_text=text)
        assert found
        assert "150" in (snippet or "")

    def test_upcoming_trip(self):
        text = "My Trips\nUpcoming trip to LAX on Jan 15\nSkyMiles"
        found, _, snippet = detect_private_data("delta", dom_text=text)
        assert found
        assert "trip" in (snippet or "").lower()

    def test_no_private_data_when_only_session_signals(self):
        found, _, _ = detect_private_data("delta", dom_text=DELTA_LOGGED_IN_NO_PRIVATE)
        assert not found


class TestStatusClassification:
    def test_needs_sign_in_when_logged_out(self):
        status = classify_probe_result(
            provider="amex",
            signed_in_detected=False,
            private_data_detected=False,
        )
        assert status == PROBE_NEEDS_SIGN_IN

    def test_signed_in_no_data_seen(self):
        status = classify_probe_result(
            provider="amex",
            signed_in_detected=True,
            private_data_detected=False,
            url_visited=AMEX_ACCOUNT_URL,
            dom_text=AMEX_LOGGED_IN_NO_PRIVATE,
        )
        assert status == PROBE_SIGNED_IN_NO_DATA

    def test_signed_in_data_seen(self):
        status = classify_probe_result(
            provider="amex",
            signed_in_detected=True,
            private_data_detected=True,
        )
        assert status == PROBE_SIGNED_IN_DATA

    def test_blocked(self):
        status = classify_probe_result(
            provider="delta",
            signed_in_detected=False,
            private_data_detected=False,
            blocked=True,
        )
        assert status == PROBE_BLOCKED

    def test_error(self):
        status = classify_probe_result(
            provider="delta",
            signed_in_detected=False,
            private_data_detected=False,
            error="navigation timeout",
        )
        assert status == PROBE_ERROR

    def test_marketing_page_does_not_claim_signed_in(self):
        status = classify_probe_result(
            provider="amex",
            signed_in_detected=True,
            private_data_detected=False,
            url_visited=AMEX_MARKETING_URL,
            dom_text=AMEX_MARKETING_TEXT,
        )
        assert status == PROBE_NEEDS_SIGN_IN


class TestEvaluateProbePayload:
    def test_amex_full_payload_signed_in_with_data(self):
        result = evaluate_probe_payload("amex", {
            "url_visited": AMEX_ACCOUNT_URL,
            "dom_text": AMEX_LOGGED_IN_TEXT,
        })
        assert result["status"] == PROBE_SIGNED_IN_DATA
        assert result["signed_in_detected"] is True
        assert result["private_data_detected"] is True
        assert result["evidence_snippet"]

    def test_amex_logged_out(self):
        result = evaluate_probe_payload("amex", {
            "url_visited": AMEX_LOGIN_URL,
            "dom_text": AMEX_LOGIN_TEXT,
        })
        assert result["status"] == PROBE_NEEDS_SIGN_IN
        assert result["failure_reason"] == "login_required"

    def test_delta_signed_in_no_private_evidence(self):
        result = evaluate_probe_payload("delta", {
            "url_visited": DELTA_ACCOUNT_URL,
            "dom_text": DELTA_LOGGED_IN_NO_PRIVATE,
        })
        assert result["status"] == PROBE_SIGNED_IN_NO_DATA
        assert result["signed_in_detected"] is True
        assert result["private_data_detected"] is False
        assert result["failure_reason"] == "signed_in_no_private_evidence"

    def test_delta_signed_in_with_data(self):
        result = evaluate_probe_payload("delta", {
            "url_visited": DELTA_ACCOUNT_URL,
            "dom_text": DELTA_LOGGED_IN_WITH_DATA,
        })
        assert result["status"] == PROBE_SIGNED_IN_DATA
        assert result["private_data_detected"] is True


class TestMergeProbeSummaries:
    def test_not_started_for_missing_providers(self):
        rows = merge_probe_summaries({}, ["amex", "delta"])
        assert len(rows) == 2
        assert all(r["status"] == PROBE_NOT_STARTED for r in rows)
