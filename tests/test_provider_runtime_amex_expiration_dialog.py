"""Tests for Amex expiration-dialog detection across DOM, iframes, and shadow roots."""

from __future__ import annotations

from unittest.mock import MagicMock

from mighty.provider_runtime import (
    EXPIRATION_DIALOG_CONTAINER_SELECTORS,
    INSPECT_EXPIRATION_DIALOG_IN_DOCUMENT_JS,
    diagnose_amex_expiration_dialog_on_page,
    evaluate_expiration_dialog_conditions,
    expiration_dialog_criteria_met,
    inspect_amex_expiration_dialog,
    sanitize_expiration_snippet,
)


LIVE_AMEX_DIALOG_TEXT = (
    "Your session is about to expire. "
    "You will be signed out due to inactivity. "
    "Select Continue to stay signed in."
)


def _match_payload(*, source_type: str = "DOM") -> dict:
    return {
        "detected": True,
        "continue_token": "tok-live",
        "dialog_text": LIVE_AMEX_DIALOG_TEXT.lower(),
        "source_type": source_type,
        "role_tag_class_summary": 'div class="sessionTimeoutPanel"',
        "candidates": [],
    }


def test_exact_live_amex_wording_matches():
    conditions = evaluate_expiration_dialog_conditions(
        LIVE_AMEX_DIALOG_TEXT,
        has_continue_button=True,
    )
    assert conditions["matched"] is True
    assert conditions["has_headline"] is True
    assert conditions["has_expiration_language"] is True
    assert conditions["has_continue_button"] is True
    assert expiration_dialog_criteria_met(
        LIVE_AMEX_DIALOG_TEXT,
        has_continue_button=True,
    )


def test_unrelated_continue_button_remains_ignored():
    assert not expiration_dialog_criteria_met(
        "Continue to view your card benefits and offers.",
        has_continue_button=True,
    )
    conditions = evaluate_expiration_dialog_conditions(
        "Please confirm your mailing address to continue.",
        has_continue_button=True,
    )
    assert conditions["matched"] is False
    assert "has_headline" in conditions["failed"]


def test_modal_without_role_dialog_is_detected():
    # Detector JS must include generic modal/session containers, not only role=dialog.
    joined = ",".join(EXPIRATION_DIALOG_CONTAINER_SELECTORS)
    assert '[role="dialog"]' in joined
    assert '[class*="modal"]' in joined or ".modal" in joined
    assert '[class*="session"]' in joined or '[class*="timeout"]' in joined
    assert "shadowRoot" in INSPECT_EXPIRATION_DIALOG_IN_DOCUMENT_JS

    page = MagicMock()
    page.main_frame = page
    page.frames = [page]
    page.url = "https://global.americanexpress.com/overview"
    page.evaluate.return_value = {
        **_match_payload(source_type="DOM"),
        "role_tag_class_summary": 'div class="axp-session-timeout-panel"',
    }

    info = inspect_amex_expiration_dialog(page)
    assert info["detected"] is True
    assert info["continue_token"] == "tok-live"
    assert "session is about to expire" in (info["dialog_text"] or "")


def test_modal_in_iframe_is_detected():
    main = MagicMock(name="main")
    main.url = "https://global.americanexpress.com/overview"
    main.evaluate.return_value = {
        "detected": False,
        "continue_token": None,
        "dialog_text": None,
        "candidates": [],
    }

    iframe = MagicMock(name="iframe")
    iframe.url = "https://functions.americanexpress.com/session-timeout"
    iframe.evaluate.return_value = _match_payload(source_type="IFRAME")

    page = MagicMock()
    page.url = main.url
    page.main_frame = main
    page.frames = [main, iframe]

    info = inspect_amex_expiration_dialog(page)
    assert info["detected"] is True
    assert info["source_type"] == "IFRAME"
    assert info["continue_token"] == "tok-live"


def test_modal_in_open_shadow_root_is_detected():
    page = MagicMock()
    page.main_frame = page
    page.frames = [page]
    page.url = "https://global.americanexpress.com/overview"
    page.evaluate.return_value = _match_payload(source_type="SHADOW_DOM")

    info = inspect_amex_expiration_dialog(page)
    assert info["detected"] is True
    assert info["source_type"] == "SHADOW_DOM"


def test_diagnose_reports_candidates_and_conditions():
    main = MagicMock(name="main")
    main.url = "https://global.americanexpress.com/overview?account=secret"
    main.evaluate.return_value = {
        "detected": True,
        "continue_token": None,
        "dialog_text": None,
        "candidates": [
            {
                "source_type": "DOM",
                "role_tag_class_summary": 'div class="sessionTimeoutPanel"',
                "text_snippet": LIVE_AMEX_DIALOG_TEXT.lower(),
                "button_labels": ["continue"],
                "detector_matched": True,
                "conditions": evaluate_expiration_dialog_conditions(
                    LIVE_AMEX_DIALOG_TEXT,
                    has_continue_button=True,
                ),
            },
            {
                "source_type": "DOM",
                "role_tag_class_summary": "button",
                "text_snippet": "continue to view rewards balance 1234",
                "button_labels": ["continue"],
                "detector_matched": False,
                "conditions": evaluate_expiration_dialog_conditions(
                    "continue to view rewards balance 1234",
                    has_continue_button=True,
                ),
            },
        ],
    }

    page = MagicMock()
    page.url = main.url
    page.main_frame = main
    page.frames = [main]

    report = diagnose_amex_expiration_dialog_on_page(page)
    assert report["frame_count"] == 1
    assert report["candidate_count"] == 2
    assert report["detector_matched"] is True
    assert report["page_url"] == "https://global.americanexpress.com/overview"
    candidate = report["candidates"][0]
    assert candidate["source_type"] == "DOM"
    assert candidate["detector_matched"] is True
    assert "has_headline" in candidate["conditions"]["passed"]
    assert candidate["button_labels"] == ["continue"]
    assert "account=secret" not in (candidate["frame_url"] or "")
    assert len(candidate["text_snippet"]) <= 300


def test_sanitize_snippet_rejects_unrelated_text():
    assert sanitize_expiration_snippet("Available credit and statement balance") is None
    assert sanitize_expiration_snippet(LIVE_AMEX_DIALOG_TEXT) is not None
