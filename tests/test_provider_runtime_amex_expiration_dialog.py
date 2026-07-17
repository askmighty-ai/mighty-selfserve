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
from tests.test_provider_runtime_browser_inspector import (
    CdpSessionMock,
    PAGE_URL,
    _amex_page,
    _bind_cdp,
    _dialog_tree,
)


LIVE_AMEX_DIALOG_TEXT = (
    "Your session is about to expire. "
    "You will be signed out due to inactivity. "
    "Select Continue to stay signed in."
)


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
    joined = ",".join(EXPIRATION_DIALOG_CONTAINER_SELECTORS)
    assert '[role="dialog"]' in joined
    assert '[class*="modal"]' in joined or ".modal" in joined
    assert '[class*="session"]' in joined or '[class*="timeout"]' in joined
    assert "retired" in INSPECT_EXPIRATION_DIALOG_IN_DOCUMENT_JS

    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree(role=None))
    _bind_cdp(page, session)

    info = inspect_amex_expiration_dialog(page)
    assert info["detected"] is True
    assert info["continue_token"]
    assert info["continue_token"].startswith("cdp-backend:")
    assert "session is about to expire" in (info["dialog_text"] or "")
    assert page.evaluate.call_count == 0


def test_modal_in_iframe_is_detected():
    main = MagicMock(name="main")
    main.url = PAGE_URL
    main.is_detached.return_value = False
    main.parent_frame = None

    iframe = MagicMock(name="iframe")
    iframe.url = "https://functions.americanexpress.com/session-timeout"
    iframe.is_detached.return_value = False
    iframe.parent_frame = main

    page = _amex_page(frames=[main, iframe])
    page.main_frame = main
    session = CdpSessionMock(
        document=_dialog_tree(source="iframe"),
        frame_tree={
            "frameTree": {
                "frame": {"id": "main", "url": PAGE_URL},
                "childFrames": [
                    {
                        "frame": {
                            "id": "child",
                            "url": "https://functions.americanexpress.com/session-timeout",
                        },
                        "childFrames": [],
                    }
                ],
            }
        },
    )
    _bind_cdp(page, session)

    info = inspect_amex_expiration_dialog(page)
    assert info["detected"] is True
    assert info["source_type"] == "IFRAME"
    assert info["continue_token"]


def test_modal_in_open_shadow_root_is_detected():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree(source="shadow"))
    _bind_cdp(page, session)

    info = inspect_amex_expiration_dialog(page)
    assert info["detected"] is True
    assert info["source_type"] == "SHADOW_DOM"


def test_diagnose_reports_candidates_and_conditions():
    page = _amex_page(url="https://global.americanexpress.com/overview?account=secret")
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)

    report = diagnose_amex_expiration_dialog_on_page(page)
    assert report["frame_count"] >= 1
    assert report["candidate_count"] >= 1
    assert report["detector_matched"] is True
    assert report["page_url"] == PAGE_URL
    candidate = report["candidates"][0]
    assert candidate["source_type"] in {"DOM", "IFRAME", "SHADOW_DOM"}
    assert candidate["detector_matched"] is True
    assert "has_headline" in candidate["conditions"]["passed"]
    assert "continue" in candidate["button_labels"]
    assert "account=secret" not in (candidate["frame_url"] or "")
    assert len(candidate["text_snippet"]) <= 300


def test_sanitize_snippet_rejects_unrelated_text():
    assert sanitize_expiration_snippet("Available credit and statement balance") is None
    assert sanitize_expiration_snippet(LIVE_AMEX_DIALOG_TEXT) is not None


def test_unrelated_panel_with_continue_is_not_expiration_dialog():
    page = _amex_page()
    # Class matches container selectors, but wording is unrelated.
    session = CdpSessionMock(
        document=_dialog_tree(
            text="Continue to view your card benefits and offers.",
            class_name="sessionTimeoutPanel",
        )
    )
    _bind_cdp(page, session)
    info = inspect_amex_expiration_dialog(page)
    assert info["detected"] is False
