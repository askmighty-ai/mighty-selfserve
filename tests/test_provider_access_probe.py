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
    AUTH_UNKNOWN,
    PROBE_BLOCKED,
    PROBE_ERROR,
    PROBE_NEEDS_SIGN_IN,
    PROBE_NOT_STARTED,
    PROBE_SIGNED_IN_DATA,
    PROBE_SIGNED_IN_NO_DATA,
    ConcurrentProbeError,
    FAILURE_BLANK_OR_UNLOADED,
    PROBE_LIFECYCLE_DONE,
    PROBE_LIFECYCLE_RUNNING,
    classify_auth_state,
    classify_probe_result,
    complete_manual_probe,
    detect_private_data,
    detect_signed_in_from_text,
    evaluate_probe_payload,
    extract_page_diagnostics,
    extract_deep_inspect,
    sanitize_deep_inspect,
    sanitize_probe_url,
    sanitize_auth_network_trace,
    compute_auth_network_diagnostic,
    get_manual_probe_state,
    is_automatic_probe_disabled,
    is_blank_or_unloaded_page,
    is_marketing_url,
    merge_probe_summaries,
    record_probe_run,
    ensure_probe_tables,
    get_latest_probe_per_provider,
    start_manual_probe,
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

    def test_blank_amex_page_records_blank_or_unloaded_page(self):
        result = evaluate_probe_payload("amex", {
            "url_visited": AMEX_ACCOUNT_URL,
            "dom_text": "",
            "page_diagnostics": {
                "ready_state": "complete",
                "body_exists": True,
                "body_text_length": 0,
                "visible_text_preview": "",
                "page_title": "",
                "iframe_count": 0,
                "input_count": 0,
                "button_count": 0,
                "password_input_count": 0,
                "final_url": AMEX_ACCOUNT_URL,
            },
        })
        assert result["auth_state"] == AUTH_UNKNOWN
        assert result["failure_reason"] == FAILURE_BLANK_OR_UNLOADED
        assert result["status"] == PROBE_NEEDS_SIGN_IN

    def test_unknown_result_includes_page_diagnostics(self):
        payload = {
            "url_visited": AMEX_ACCOUNT_URL,
            "dom_text": "",
            "page_diagnostics": {
                "ready_state": "interactive",
                "body_exists": True,
                "body_text_length": 4,
                "visible_text_preview": "    ",
                "page_title": "",
                "iframe_count": 2,
                "input_count": 0,
                "button_count": 0,
                "password_input_count": 0,
                "final_url": AMEX_ACCOUNT_URL,
                "classifier_started_at": "2026-07-07T16:00:00.000Z",
                "dom_wait_ms": 5000,
            },
        }
        result = evaluate_probe_payload("amex", payload)
        diag = result["page_diagnostics"]
        assert diag["ready_state"] == "interactive"
        assert diag["body_exists"] is True
        assert diag["body_text_length"] == 4
        assert diag["iframe_count"] == 2
        assert diag["dom_wait_ms"] == 5000
        assert result["failure_reason"] == FAILURE_BLANK_OR_UNLOADED

    def test_delta_login_page_unchanged_with_diagnostics_present(self):
        result = evaluate_probe_payload("delta", {
            "url_visited": "https://www.delta.com/login",
            "dom_text": DELTA_LOGIN_TEXT,
            "page_diagnostics": {
                "ready_state": "complete",
                "body_exists": True,
                "body_text_length": len(DELTA_LOGIN_TEXT),
                "input_count": 2,
                "password_input_count": 1,
                "final_url": "https://www.delta.com/login",
            },
        })
        assert result["auth_state"] == AUTH_LOGIN_PAGE
        assert result["failure_reason"] == "login_required"
        assert result["page_diagnostics"]["password_input_count"] == 1


class TestBlankPageDetection:
    def test_is_blank_or_unloaded_page_detects_empty_body(self):
        assert is_blank_or_unloaded_page("", payload={
            "page_diagnostics": {"body_exists": False, "body_text_length": 0},
        })

    def test_is_blank_or_unloaded_page_allows_login_content(self):
        assert not is_blank_or_unloaded_page(DELTA_LOGIN_TEXT, payload={
            "page_diagnostics": {
                "body_exists": True,
                "body_text_length": len(DELTA_LOGIN_TEXT),
                "input_count": 2,
            },
        })


class TestDeepInspect:
    _OUTER_HTML = "<html><head><title>One App</title></head><body><div>Give Feedback</div></body></html>"

    def test_deep_inspect_captures_outer_html_length_and_preview(self):
        raw = {
            "outer_html_length": len(self._OUTER_HTML),
            "outer_html_preview": self._OUTER_HTML,
            "iframe_count": 0,
            "final_url": AMEX_ACCOUNT_URL,
        }
        result = sanitize_deep_inspect(raw)
        assert result["outer_html_length"] == len(self._OUTER_HTML)
        assert result["outer_html_preview"] == self._OUTER_HTML
        assert len(result["outer_html_preview"]) <= 2000

    def test_iframe_metadata_stored_without_contents(self):
        raw = {
            "iframes": [
                {
                    "index": 0,
                    "src": "https://example.com/frame",
                    "id": "auth-frame",
                    "name": "login",
                    "sandbox": "allow-scripts",
                    "innerHTML": "<secret>must not persist</secret>",
                    "contentDocument": "blocked",
                },
            ],
        }
        result = sanitize_deep_inspect(raw)
        assert len(result["iframes"]) == 1
        frame = result["iframes"][0]
        assert frame["src"] == "https://example.com/frame"
        assert frame["id"] == "auth-frame"
        assert "innerHTML" not in frame
        assert "contentDocument" not in frame

    def test_cookie_and_storage_names_only_never_values(self):
        raw = {
            "cookie_names": ["sessionId=abc123", "tracking=xyz"],
            "local_storage_keys": ["authToken", "prefs"],
            "session_storage_keys": ["flowState"],
            "cookie_values": {"sessionId": "secret"},
            "local_storage": {"authToken": "secret"},
        }
        result = sanitize_deep_inspect(raw)
        assert result["cookie_names"] == ["sessionId", "tracking"]
        assert result["local_storage_keys"] == ["authToken", "prefs"]
        assert result["session_storage_keys"] == ["flowState"]
        assert "cookie_values" not in result
        assert "local_storage" not in result

    def test_evaluate_probe_payload_preserves_deep_inspect(self):
        payload = {
            "url_visited": AMEX_ACCOUNT_URL,
            "dom_text": "",
            "deep_inspect": {
                "outer_html_length": 5000,
                "outer_html_preview": self._OUTER_HTML,
                "iframe_count": 1,
                "iframes": [{"index": 0, "src": "https://amex.example/iframe"}],
                "cookie_names": ["a=b"],
                "local_storage_keys": ["k1"],
                "session_storage_keys": [],
                "content_script_injection_succeeded": True,
                "final_url": AMEX_ACCOUNT_URL,
                "page_title": "One App",
                "ready_state": "complete",
                "visible_text_preview": "Give Feedback",
            },
            "page_diagnostics": {
                "ready_state": "complete",
                "body_exists": True,
                "body_text_length": 13,
            },
        }
        result = evaluate_probe_payload("amex", payload)
        deep = result["deep_inspect"]
        assert deep["outer_html_length"] == 5000
        assert deep["cookie_names"] == ["a"]
        assert deep["content_script_injection_succeeded"] is True
        assert result["failure_reason"] == FAILURE_BLANK_OR_UNLOADED

    def test_recorded_run_includes_deep_inspect(self, tmp_path):
        import sqlite3

        db_path = str(tmp_path / "deep.db")
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        ensure_probe_tables(db)

        result = evaluate_probe_payload("amex", {
            "url_visited": AMEX_ACCOUNT_URL,
            "dom_text": "",
            "deep_inspect": {
                "outer_html_length": 1234,
                "outer_html_preview": "<html></html>",
            },
            "page_diagnostics": {"body_text_length": 0, "body_exists": True},
        })
        record_probe_run(db, "user-1", result)
        latest = get_latest_probe_per_provider(db, "user-1")
        assert latest["amex"]["deep_inspect"]["outer_html_length"] == 1234

    def test_extract_deep_inspect_from_payload(self):
        payload = {"deep_inspect": {"script_count": 7, "script_srcs": ["https://a.js"]}}
        assert extract_deep_inspect(payload)["script_count"] == 7

    def test_spa_root_inspection_sanitized(self):
        raw = {
            "spa_roots": [
                {"key": "root", "exists": True, "child_element_count": 1, "inner_html_length": 40000, "text_length": 13},
                {"key": "app", "exists": False, "innerHTML": "<secret>drop</secret>"},
            ],
        }
        result = sanitize_deep_inspect(raw)
        root = result["spa_roots"][0]
        assert root["key"] == "root"
        assert root["exists"] is True
        assert root["child_element_count"] == 1
        assert root["inner_html_length"] == 40000
        assert root["text_length"] == 13
        assert result["spa_roots"][1]["exists"] is False
        assert "innerHTML" not in result["spa_roots"][1]

    def test_mutation_timeline_collection(self):
        raw = {
            "mutation_timeline": {
                "total_count": 42,
                "first_mutation_ms": 120,
                "last_mutation_ms": 9800,
                "observe_duration_ms": 10000,
                "mutation_activity": "continued",
            },
        }
        result = sanitize_deep_inspect(raw)
        mt = result["mutation_timeline"]
        assert mt["total_count"] == 42
        assert mt["first_mutation_ms"] == 120
        assert mt["last_mutation_ms"] == 9800
        assert mt["mutation_activity"] == "continued"

    def test_console_diagnostics_message_only(self):
        raw = {
            "console_diagnostics": [
                {"level": "error", "message": "Failed to bootstrap app", "stack": "secret stack"},
                {"level": "warn", "message": "locale key missing"},
            ],
        }
        result = sanitize_deep_inspect(raw)
        assert len(result["console_diagnostics"]) == 2
        assert result["console_diagnostics"][0]["message"] == "Failed to bootstrap app"
        assert "stack" not in result["console_diagnostics"][0]

    def test_framework_detection_preserved(self):
        raw = {"framework_detection": ["React", "Next.js"]}
        assert sanitize_deep_inspect(raw)["framework_detection"] == ["React", "Next.js"]

    def test_resource_diagnostics_summary(self):
        raw = {
            "resource_diagnostics": {
                "js_count": 42,
                "css_count": 5,
                "fetch_xhr_count": 8,
                "failed_loads": [
                    {"name": "https://amex.example/bundle.js", "response_status": 404, "body": "secret"},
                ],
                "slow_loads": [
                    {"name": "https://amex.example/api/config", "duration_ms": 4500},
                ],
            },
        }
        result = sanitize_deep_inspect(raw)
        rd = result["resource_diagnostics"]
        assert rd["js_count"] == 42
        assert rd["failed_loads"][0]["response_status"] == 404
        assert "body" not in rd["failed_loads"][0]
        assert rd["slow_loads"][0]["duration_ms"] == 4500

    def test_observation_window_15_seconds(self):
        raw = {
            "observation_window": {
                "observation_ms": 15000,
                "start_dom_size": 43000,
                "end_dom_size": 43050,
                "start_visible_text_length": 13,
                "end_visible_text_length": 13,
                "dom_size_delta": 50,
                "visible_text_length_delta": 0,
                "start_visible_text_preview": "Give Feedback",
                "end_visible_text_preview": "Give Feedback",
            },
        }
        result = sanitize_deep_inspect(raw)
        ow = result["observation_window"]
        assert ow["observation_ms"] == 15000
        assert ow["dom_size_delta"] == 50
        assert ow["visible_text_length_delta"] == 0

    def test_existing_deep_inspect_fields_regression(self):
        raw = {
            "outer_html_length": 43000,
            "outer_html_preview": "<html></html>",
            "iframe_count": 0,
            "script_count": 42,
            "cookie_names": ["a=1"],
            "content_script_injection_succeeded": True,
            "spa_roots": [{"key": "root", "exists": True, "text_length": 13}],
            "mutation_timeline": {"total_count": 0, "mutation_activity": "none"},
        }
        result = sanitize_deep_inspect(raw)
        assert result["outer_html_length"] == 43000
        assert result["cookie_names"] == ["a"]
        assert result["spa_roots"][0]["text_length"] == 13


class TestAuthNetworkTrace:
    def test_network_trace_stores_url_status_type_duration_safely(self):
        raw = {
            "request_count": 2,
            "status_counts": {"401": 1, "200": 1},
            "requests": [
                {
                    "url": "https://functions.americanexpress.com/ReadUserSession.v1",
                    "method": "POST",
                    "resource_type": "fetch",
                    "initiator_type": "fetch",
                    "status_code": 401,
                    "duration_ms": 120,
                    "response_header_names": ["content-type", "x-request-id"],
                },
            ],
        }
        result = sanitize_auth_network_trace(raw)
        req = result["requests"][0]
        assert req["url"].endswith("ReadUserSession.v1")
        assert req["status_code"] == 401
        assert req["duration_ms"] == 120
        assert req["response_header_names"] == ["content-type", "x-request-id"]

    def test_query_params_and_tokens_are_redacted(self):
        url = sanitize_probe_url(
            "https://functions.americanexpress.com/Auth?token=secret&session=abc&foo=bar"
        )
        assert "secret" not in url
        assert "abc" not in url
        assert "REDACTED" in url
        assert "foo=bar" in url

    def test_response_header_values_not_stored(self):
        raw = {
            "requests": [{
                "url": "https://example.com/session",
                "response_header_names": ["set-cookie", "authorization"],
                "response_headers": {"set-cookie": "secret=1"},
                "authorization": "Bearer secret",
            }],
        }
        result = sanitize_auth_network_trace(raw)
        req = result["requests"][0]
        assert "response_headers" not in req
        assert "authorization" not in req
        assert req["response_header_names"] == ["set-cookie", "authorization"]

    def test_401_session_apis_highlighted(self):
        raw = {
            "highlighted_requests": [
                {"url": "https://functions.americanexpress.com/ReadUserSession.v1", "status_code": 401, "highlighted": True},
                {"url": "https://functions.americanexpress.com/UpdateUserSession.v1", "status_code": 401, "highlighted": True},
            ],
            "status_401_requests": [
                {"url": "https://functions.americanexpress.com/ReadUserSession.v1", "status_code": 401},
            ],
        }
        result = sanitize_auth_network_trace(raw)
        assert len(result["highlighted_requests"]) == 2
        assert "ReadUserSession.v1 returned 401" in result["diagnostic_summary"]
        assert "UpdateUserSession.v1 returned 401" in result["diagnostic_summary"]

    def test_never_stores_cookie_values_or_bodies(self):
        raw = {
            "requests": [{
                "url": "https://example.com/session",
                "request_body": '{"token":"secret"}',
                "response_body": '{"session":"secret"}',
                "cookie": "session=secret",
            }],
        }
        result = sanitize_auth_network_trace(raw)
        req = result["requests"][0]
        assert "request_body" not in req
        assert "response_body" not in req
        assert "cookie" not in req

    def test_compute_auth_network_diagnostic_with_cookies(self):
        trace = {
            "highlighted_requests": [
                {"url": "https://functions.americanexpress.com/ReadUserSession.v1", "status_code": 401},
            ],
            "status_401_requests": [
                {"url": "https://functions.americanexpress.com/ReadUserSession.v1", "status_code": 401},
            ],
        }
        msg = compute_auth_network_diagnostic(trace, cookie_names=["s_sess"])
        assert "ReadUserSession.v1 returned 401" in msg
        assert "cookies present at document level" in msg

    def test_deep_inspect_includes_auth_network_trace(self):
        payload = {
            "deep_inspect": {
                "cookie_names": ["a"],
                "auth_network_trace": {
                    "request_count": 1,
                    "status_counts": {"401": 1},
                    "requests": [{
                        "url": "https://functions.americanexpress.com/ReadUserSession.v1",
                        "status_code": 401,
                    }],
                },
            },
        }
        result = sanitize_deep_inspect(payload["deep_inspect"])
        assert result["auth_network_trace"]["request_count"] == 1
        assert "401" in result["auth_network_trace"]["diagnostic_summary"]


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


class TestManualProbeRunner:
    def test_start_manual_probe_one_provider(self, tmp_path):
        import sqlite3

        db = sqlite3.connect(str(tmp_path / "manual.db"))
        db.row_factory = sqlite3.Row
        state = start_manual_probe(db, "user-1", "amex")
        assert state["provider"] == "amex"
        assert state["lifecycle"] == PROBE_LIFECYCLE_RUNNING

    def test_concurrent_manual_probe_rejected(self, tmp_path):
        import sqlite3

        db = sqlite3.connect(str(tmp_path / "manual2.db"))
        db.row_factory = sqlite3.Row
        start_manual_probe(db, "user-1", "amex")
        with pytest.raises(ConcurrentProbeError):
            start_manual_probe(db, "user-1", "delta")

    def test_complete_manual_probe(self, tmp_path):
        import sqlite3

        db = sqlite3.connect(str(tmp_path / "manual3.db"))
        db.row_factory = sqlite3.Row
        state = start_manual_probe(db, "user-1", "delta")
        complete_manual_probe(
            db,
            "user-1",
            state["manual_run_id"],
            lifecycle=PROBE_LIFECYCLE_DONE,
            probe_run_id="run-123",
        )
        latest = get_manual_probe_state(db, "user-1")
        assert latest["lifecycle"] == PROBE_LIFECYCLE_DONE
        assert latest["probe_run_id"] == "run-123"


class TestAutomaticProbeDisabled:
    def test_disabled_in_development(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "development")
        assert is_automatic_probe_disabled() is True

    def test_enabled_in_production(self, monkeypatch):
        monkeypatch.delenv("FLASK_ENV", raising=False)
        monkeypatch.delenv("MIGHTY_ADMIN_TEST", raising=False)
        monkeypatch.delenv("DISABLE_AUTOMATIC_PROVIDER_PROBES", raising=False)
        assert is_automatic_probe_disabled() is False
