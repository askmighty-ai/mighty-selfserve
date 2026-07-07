"""Unit tests for provider access probe status classification."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.provider_access_probe import (
    AUTH_BOT_BLOCKED,
    AUTH_LOGIN_PAGE,
    AUTH_MARKETING,
    AUTH_MFA_REQUIRED,
    AUTH_PRIVATE_DATA_VISIBLE,
    AUTH_SESSION_EXPIRED,
    PROBE_BLOCKED,
    PROBE_ERROR,
    PROBE_NEEDS_SIGN_IN,
    PROBE_NOT_STARTED,
    PROBE_SIGNED_IN_DATA,
    PROBE_SIGNED_IN_NO_DATA,
    classify_auth_state,
    classify_probe_result,
    detect_private_data,
    detect_signed_in_from_text,
    evaluate_probe_payload,
    is_marketing_url,
    merge_probe_summaries,
    record_probe_run,
    ensure_probe_tables,
    get_latest_probe_per_provider,
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
DELTA_SKYMILES_LOGIN_URL = "https://www.delta.com/skymiles/login"
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

MFA_PAGE_TEXT = """
Two-Factor Authentication
Enter the verification code sent to your phone
Security code
Continue
"""

BOT_BLOCK_TEXT = """
Access Denied
Please complete the CAPTCHA to continue
Verify you are human
"""

SESSION_EXPIRED_TEXT = """
Your session has expired
Please sign in again to continue
Sign In
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
        found, etype, snippet, rules = detect_private_data("amex", dom_text=AMEX_LOGGED_IN_TEXT)
        assert found
        assert etype == "dom_text"
        assert "125,430" in (snippet or "")
        assert rules

    def test_statement_balance(self):
        text = "Statement balance $2,500.00\nAccount Home"
        found, _, snippet, _ = detect_private_data("amex", dom_text=text)
        assert found
        assert "2,500" in (snippet or "")

    def test_card_ending(self):
        text = "Account Home\nCard ending in 9876\nRecent Activity"
        found, _, snippet, rules = detect_private_data("amex", dom_text=text)
        assert found
        assert "9876" in (snippet or "")
        assert "card_ending" in rules

    def test_no_private_data_when_only_session_signals(self):
        found, _, _, _ = detect_private_data("amex", dom_text=AMEX_LOGGED_IN_NO_PRIVATE)
        assert not found

    def test_marketing_page_does_not_claim_private_data(self):
        text = "SkyMiles Number: 1234567890\nBook flights today"
        found, _, _, _ = detect_private_data(
            "delta", url=DELTA_MARKETING_URL, dom_text=text,
        )
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
        found, etype, snippet, rules = detect_private_data("delta", dom_text=DELTA_LOGGED_IN_WITH_DATA)
        assert found
        assert etype == "dom_text"
        assert "1234567890" in (snippet or "")
        assert "skymiles_number" in rules

    def test_miles_balance(self):
        text = "My SkyMiles\nAvailable Miles 12,500"
        found, _, snippet, _ = detect_private_data("delta", dom_text=text)
        assert found
        assert "12,500" in (snippet or "")

    def test_medallion_status(self):
        text = "Medallion Status: Platinum Medallion\nWelcome back"
        found, _, snippet, _ = detect_private_data("delta", dom_text=text)
        assert found
        assert "Platinum" in (snippet or "")

    def test_ecredits(self):
        text = "My Wallet\neCredits $150.00\nMy SkyMiles"
        found, _, snippet, _ = detect_private_data("delta", dom_text=text)
        assert found
        assert "150" in (snippet or "")

    def test_upcoming_trip(self):
        text = "My Trips\nUpcoming trip to LAX on Jan 15\nSkyMiles"
        found, _, snippet, _ = detect_private_data("delta", dom_text=text)
        assert found
        assert "trip" in (snippet or "").lower()

    def test_no_private_data_when_only_session_signals(self):
        found, _, _, _ = detect_private_data("delta", dom_text=DELTA_LOGGED_IN_NO_PRIVATE)
        assert not found


class TestAuthStateClassification:
    def test_amex_account_url_with_login_form_is_login_page(self):
        auth = classify_auth_state(
            provider="amex",
            url=AMEX_ACCOUNT_URL,
            dom_text=AMEX_LOGIN_TEXT,
        )
        assert auth["auth_state"] == AUTH_LOGIN_PAGE
        assert auth["login_form_present"] is True
        assert auth["signed_in_detected"] is False

    def test_amex_private_data_visible(self):
        auth = classify_auth_state(
            provider="amex",
            url=AMEX_ACCOUNT_URL,
            dom_text=AMEX_LOGGED_IN_TEXT,
        )
        assert auth["auth_state"] == AUTH_PRIVATE_DATA_VISIBLE
        assert auth["private_data_detected"] is True
        assert auth["matched_private_data_rules"]

    def test_delta_skymiles_login_redirect_is_login_page(self):
        auth = classify_auth_state(
            provider="delta",
            url=DELTA_SKYMILES_LOGIN_URL,
            dom_text=DELTA_LOGIN_TEXT,
        )
        assert auth["auth_state"] == AUTH_LOGIN_PAGE
        assert "login_url" in auth["matched_login_rules"]

    def test_delta_marketing_is_marketing_not_private_data(self):
        auth = classify_auth_state(
            provider="delta",
            url=DELTA_MARKETING_URL,
            dom_text="SkyMiles Number: 9999999999\nBook flights and deals",
        )
        assert auth["auth_state"] == AUTH_MARKETING
        assert auth["private_data_detected"] is False
        assert auth["signed_in_detected"] is False

    def test_delta_authenticated_with_skymiles_number(self):
        auth = classify_auth_state(
            provider="delta",
            url=DELTA_ACCOUNT_URL,
            dom_text=DELTA_LOGGED_IN_WITH_DATA,
        )
        assert auth["auth_state"] == AUTH_PRIVATE_DATA_VISIBLE
        assert "skymiles_number" in auth["matched_private_data_rules"]

    def test_mfa_page(self):
        auth = classify_auth_state(
            provider="amex",
            url=AMEX_ACCOUNT_URL,
            dom_text=MFA_PAGE_TEXT,
        )
        assert auth["auth_state"] == AUTH_MFA_REQUIRED
        assert auth["mfa_signal_present"] is True

    def test_bot_blocked_page(self):
        auth = classify_auth_state(
            provider="delta",
            url=DELTA_ACCOUNT_URL,
            dom_text=BOT_BLOCK_TEXT,
            blocked=True,
        )
        assert auth["auth_state"] == AUTH_BOT_BLOCKED
        assert auth["bot_block_signal_present"] is True
        assert auth["matched_blocking_rules"]

    def test_session_expired_page(self):
        auth = classify_auth_state(
            provider="amex",
            url=AMEX_ACCOUNT_URL,
            dom_text=SESSION_EXPIRED_TEXT,
        )
        assert auth["auth_state"] == AUTH_SESSION_EXPIRED
        assert auth["session_expired_signal_present"] is True


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
            "page_title": "Account Home | Amex",
        })
        assert result["status"] == PROBE_SIGNED_IN_DATA
        assert result["auth_state"] == AUTH_PRIVATE_DATA_VISIBLE
        assert result["signed_in_detected"] is True
        assert result["private_data_detected"] is True
        assert result["evidence_snippet"]
        assert result["page_title"] == "Account Home | Amex"

    def test_amex_logged_out(self):
        result = evaluate_probe_payload("amex", {
            "url_visited": AMEX_LOGIN_URL,
            "dom_text": AMEX_LOGIN_TEXT,
        })
        assert result["status"] == PROBE_NEEDS_SIGN_IN
        assert result["auth_state"] == AUTH_LOGIN_PAGE
        assert result["failure_reason"] == "login_required"

    def test_delta_signed_in_no_private_evidence(self):
        result = evaluate_probe_payload("delta", {
            "url_visited": DELTA_ACCOUNT_URL,
            "dom_text": DELTA_LOGGED_IN_NO_PRIVATE,
        })
        assert result["status"] == PROBE_SIGNED_IN_NO_DATA
        assert result["auth_state"] == "authenticated_no_private_data"
        assert result["signed_in_detected"] is True
        assert result["private_data_detected"] is False
        assert result["failure_reason"] == "signed_in_no_private_evidence"

    def test_delta_signed_in_with_data(self):
        result = evaluate_probe_payload("delta", {
            "url_visited": DELTA_ACCOUNT_URL,
            "dom_text": DELTA_LOGGED_IN_WITH_DATA,
        })
        assert result["status"] == PROBE_SIGNED_IN_DATA
        assert result["auth_state"] == AUTH_PRIVATE_DATA_VISIBLE
        assert result["private_data_detected"] is True


class TestStoredProbeRun:
    def test_recorded_run_includes_matched_rules_and_page_title(self, tmp_path):
        import sqlite3

        db_path = str(tmp_path / "probe.db")
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        ensure_probe_tables(db)

        result = evaluate_probe_payload("amex", {
            "url_visited": AMEX_ACCOUNT_URL,
            "dom_text": AMEX_LOGGED_IN_TEXT,
            "page_title": "American Express Account",
        })
        record_probe_run(db, "user-1", result)
        latest = get_latest_probe_per_provider(db, "user-1")

        row = latest["amex"]
        assert row["page_title"] == "American Express Account"
        assert row["auth_state"] == AUTH_PRIVATE_DATA_VISIBLE
        assert row["matched_private_data_rules"]
        assert row["final_url"] == AMEX_ACCOUNT_URL


class TestMergeProbeSummaries:
    def test_not_started_for_missing_providers(self):
        rows = merge_probe_summaries({}, ["amex", "delta"])
        assert len(rows) == 2
        assert all(r["status"] == PROBE_NOT_STARTED for r in rows)
