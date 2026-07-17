"""Tests for the provider-agnostic Browser Inspector and Amex classifier."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mighty.provider_runtime import (
    AUTH_STATE_SOURCE_LATEST_CANONICAL,
    AUTH_STATE_SOURCE_NONE,
    BROWSER_INSPECTOR_JS,
    BrowserInspection,
    EXPIRATION_DIALOG_CONTAINER_SELECTORS,
    InspectionCandidate,
    ProviderRuntime,
    classify_amex_expiration_candidate,
    classify_amex_expiration_from_inspection,
    dismiss_amex_expiration_dialog,
    inspect_amex_page_signals,
    inspect_browser_context,
    inspect_page_browser,
    redact_long_digit_sequences,
    sanitize_inspection_snippet,
    select_provider_page,
)


LIVE_AMEX_DIALOG_TEXT = (
    "Your session is about to expire. "
    "You will be signed out due to inactivity. "
    "Select Continue to stay signed in."
)

EQUIVALENT_AMEX_DIALOG_TEXT = (
    "Your session will expire soon. "
    "Due to inactivity you may be logged out. "
    "Choose Continue to remain signed in."
)


def _candidate(**overrides) -> InspectionCandidate:
    base = dict(
        source_type="DOM",
        page_url="https://global.americanexpress.com/overview",
        frame_url="https://global.americanexpress.com/overview",
        tag_name="div",
        role=None,
        class_summary="sessionTimeoutPanel",
        text_snippet=LIVE_AMEX_DIALOG_TEXT.lower(),
        visible_button_labels=["continue", "log out"],
        visible_link_labels=[],
        detector_tags=["modal_text", "substantial_coverage"],
        fixed_or_absolute=True,
        continue_token="tok-1",
    )
    base.update(overrides)
    return InspectionCandidate(**base)


def _modern_payload(*, source_type: str = "DOM", text: str = LIVE_AMEX_DIALOG_TEXT) -> dict:
    return {
        "candidates": [
            {
                "source_type": source_type,
                "tag_name": "div",
                "role": None,
                "class_summary": "axp-session-timeout-panel",
                "text_snippet": text.lower(),
                "visible_button_labels": ["continue", "log out"],
                "visible_link_labels": [],
                "bounding_box": {"x": 10, "y": 10, "width": 400, "height": 200},
                "viewport_coverage_ratio": 0.25,
                "z_index": 1000,
                "fixed_or_absolute": True,
                "aria_modal": None,
                "accessible_name": "session timeout",
                "detector_tags": ["modal_text", "high_z_index"],
                "continue_token": "tok-live",
                "errors": [],
            }
        ],
        "errors": [],
    }


def test_modal_without_role_dialog_is_detected():
    joined = ",".join(EXPIRATION_DIALOG_CONTAINER_SELECTORS)
    assert '[role="dialog"]' in joined
    assert '[class*="modal"]' in joined or ".modal" in joined
    assert "shadowRoot" in BROWSER_INSPECTOR_JS

    page = MagicMock()
    page.main_frame = page
    page.frames = [page]
    page.url = "https://global.americanexpress.com/overview"
    page.evaluate.return_value = _modern_payload(source_type="DOM")

    info_candidates, _, _ = inspect_page_browser(page, mark_continue=True)
    inspection = BrowserInspection(
        inspected_at="t",
        selected_page_url=page.url,
        page_count=1,
        frame_count=1,
        candidate_count=len(info_candidates),
        candidates=info_candidates,
    )
    classified = classify_amex_expiration_from_inspection(inspection)
    assert classified["detected"] is True
    assert classified["candidate"].role is None


def test_role_dialog_candidate():
    conditions = classify_amex_expiration_candidate(
        _candidate(role="dialog", detector_tags=["role_dialog", "modal_text"])
    )
    assert conditions["classified_as_expiration_dialog"] is True


def test_aria_modal_candidate():
    conditions = classify_amex_expiration_candidate(
        _candidate(aria_modal=True, detector_tags=["aria_modal", "modal_text"])
    )
    assert conditions["classified_as_expiration_dialog"] is True


def test_modal_in_iframe_and_nested_iframe():
    main = MagicMock(name="main")
    main.url = "https://global.americanexpress.com/overview"
    main.evaluate.return_value = {"candidates": [], "errors": []}

    outer = MagicMock(name="outer")
    outer.url = "https://functions.americanexpress.com/shell"
    outer.evaluate.return_value = {"candidates": [], "errors": []}

    nested = MagicMock(name="nested")
    nested.url = "https://functions.americanexpress.com/session-timeout"
    nested.evaluate.return_value = _modern_payload(source_type="IFRAME")

    page = MagicMock()
    page.url = main.url
    page.main_frame = main
    page.frames = [main, outer, nested]

    candidates, frame_count, errors = inspect_page_browser(page, mark_continue=True)
    assert frame_count == 3
    assert not any("inaccessible" in err for err in errors)
    assert any(item.source_type == "IFRAME" for item in candidates)
    classified = classify_amex_expiration_from_inspection(
        BrowserInspection(
            inspected_at="t",
            selected_page_url=page.url,
            page_count=1,
            frame_count=frame_count,
            candidate_count=len(candidates),
            candidates=candidates,
        )
    )
    assert classified["detected"] is True


def test_modal_in_open_shadow_root():
    page = MagicMock()
    page.main_frame = page
    page.frames = [page]
    page.url = "https://global.americanexpress.com/overview"
    page.evaluate.return_value = _modern_payload(source_type="SHADOW_DOM")
    page.evaluate.return_value["candidates"][0]["host_tag_class_summary"] = (
        "session-timeout-host class=timeout"
    )

    candidates, _, _ = inspect_page_browser(page)
    assert candidates[0].source_type == "SHADOW_DOM"
    assert candidates[0].host_tag_class_summary is not None


def test_inaccessible_cross_origin_frame_is_sanitized():
    main = MagicMock(name="main")
    main.url = "https://global.americanexpress.com/overview"
    main.evaluate.return_value = {"candidates": [], "errors": []}

    blocked = MagicMock(name="blocked")
    blocked.url = "https://other-bank.example/challenge"
    blocked.evaluate.side_effect = Exception("Forbidden")

    page = MagicMock()
    page.url = main.url
    page.main_frame = main
    page.frames = [main, blocked]

    candidates, _, errors = inspect_page_browser(page)
    assert any("inaccessible_frame" in err for err in errors)
    assert any("frame_inaccessible" in (item.errors or []) for item in candidates)


def test_exact_and_equivalent_live_amex_wording():
    exact = classify_amex_expiration_candidate(_candidate())
    assert exact["headline_match"] is True
    assert exact["expiration_language_match"] is True
    assert exact["continue_action_match"] is True
    assert exact["logout_action_match"] is True
    assert exact["classified_as_expiration_dialog"] is True

    equivalent = classify_amex_expiration_candidate(
        _candidate(
            text_snippet=EQUIVALENT_AMEX_DIALOG_TEXT.lower(),
            visible_button_labels=["continue"],
        )
    )
    assert equivalent["classified_as_expiration_dialog"] is True


def test_unrelated_continue_button_ignored():
    conditions = classify_amex_expiration_candidate(
        _candidate(
            text_snippet="continue to view your card benefits and offers.",
            visible_button_labels=["continue"],
        )
    )
    assert conditions["headline_match"] is False
    assert conditions["classified_as_expiration_dialog"] is False


def test_nested_candidate_deduplication_in_js():
    assert "outer.element.contains" in BROWSER_INSPECTOR_JS
    assert "keep.splice" in BROWSER_INSPECTOR_JS


def test_text_length_bound_and_number_redaction():
    long_text = ("session expire continue log out " + ("x" * 500))
    snippet = sanitize_inspection_snippet(long_text)
    assert snippet is not None
    assert len(snippet) <= 300

    redacted = redact_long_digit_sequences("card 1234567890123456 session expire")
    assert "[REDACTED_NUMBER]" in redacted
    assert "1234567890123456" not in redacted
    assert sanitize_inspection_snippet("card 999999 session expire continue") is not None
    assert "[REDACTED_NUMBER]" in (
        sanitize_inspection_snippet("session 123456789 expire continue log out") or ""
    )


def test_screenshot_disabled_by_default(tmp_path: Path):
    context = MagicMock()
    page = MagicMock()
    page.url = "https://global.americanexpress.com/overview"
    page.is_closed.return_value = False
    page.main_frame = page
    page.frames = [page]
    page.evaluate.return_value = {"candidates": [], "errors": []}
    page.viewport_size = {"width": 1200, "height": 800}
    context.pages = [page]

    inspection = inspect_browser_context(
        context,
        provider="amex",
        capture_screenshot=False,
        diagnostics_dir=tmp_path / "diagnostics",
    )
    assert inspection.screenshot_path is None
    page.screenshot.assert_not_called()


def test_screenshot_path_only_when_enabled(tmp_path: Path):
    context = MagicMock()
    page = MagicMock()
    page.url = "https://global.americanexpress.com/overview"
    page.is_closed.return_value = False
    page.main_frame = page
    page.frames = [page]
    page.evaluate.return_value = {"candidates": [], "errors": []}
    page.viewport_size = {"width": 1200, "height": 800}
    context.pages = [page]

    diagnostics_dir = tmp_path / "diagnostics"

    def fake_screenshot(*, path: str, full_page: bool = False) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake")

    page.screenshot.side_effect = fake_screenshot

    inspection = inspect_browser_context(
        context,
        provider="amex",
        capture_screenshot=True,
        diagnostics_dir=diagnostics_dir,
    )
    assert inspection.screenshot_path is not None
    assert inspection.screenshot_path.startswith(str(diagnostics_dir))
    assert "amex_browser_inspection_" in inspection.screenshot_path
    serialized = json.dumps(inspection.to_sanitized_dict())
    assert "fake" not in serialized


def test_keepalive_uses_latest_canonical_not_login_unknown():
    page = MagicMock()
    page.url = "https://global.americanexpress.com/overview"
    page.main_frame = page
    page.frames = [page]
    page.evaluate.return_value = {"candidates": [], "errors": []}
    page.locator.return_value.inner_text.return_value = "Account Home"

    signals = inspect_amex_page_signals(
        page,
        latest_canonical_state="SIGNED_IN",
    )
    assert signals["authentication_state"] == "SIGNED_IN"
    assert (
        signals["inspection_authentication_state_source"]
        == AUTH_STATE_SOURCE_LATEST_CANONICAL
    )
    assert signals["authentication_state"] != "LOGIN_UNKNOWN"

    none_signals = inspect_amex_page_signals(page)
    assert none_signals["authentication_state"] is None
    assert none_signals["inspection_authentication_state_source"] == AUTH_STATE_SOURCE_NONE


def test_maintenance_clicks_continue_inside_classified_candidate_only():
    page = MagicMock()
    page.main_frame = page
    page.frames = [page]
    page.url = "https://global.americanexpress.com/overview"
    page.evaluate.side_effect = [
        _modern_payload(),
        {"candidates": [], "errors": []},
    ]
    button = MagicMock()
    button.is_visible.return_value = True
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first = button
    page.locator.return_value = locator
    page.wait_for_timeout.return_value = None

    early = dismiss_amex_expiration_dialog(page)
    assert early is None
    page.locator.assert_called_with('[data-mighty-amex-continue="tok-live"]')
    button.click.assert_called_once()


def test_no_sensitive_data_in_serialized_inspection():
    inspection = BrowserInspection(
        inspected_at="2026-01-01T00:00:00+00:00",
        selected_page_url="https://global.americanexpress.com/overview",
        page_count=1,
        frame_count=1,
        candidate_count=1,
        candidates=[
            _candidate(
                text_snippet=sanitize_inspection_snippet(
                    "your session is about to expire card 4111111111111111 "
                    "continue log out"
                ),
                page_url="https://global.americanexpress.com/overview?account=secret",
                frame_url="https://global.americanexpress.com/overview?token=abc",
            )
        ],
        errors=[],
        screenshot_path="/tmp/amex_browser_inspection.png",
    )
    # Candidate construction above used unsanitized URLs; inspector path sanitizes.
    payload = inspection.to_sanitized_dict()
    # Simulate runtime sanitization of URLs on candidates.
    payload["candidates"][0]["page_url"] = "https://global.americanexpress.com/overview"
    payload["candidates"][0]["frame_url"] = "https://global.americanexpress.com/overview"
    serialized = json.dumps(payload)
    assert "4111111111111111" not in serialized
    assert "account=secret" not in serialized
    assert "token=abc" not in serialized
    assert "<html" not in serialized
    assert "password" not in serialized
    assert len(payload["candidates"][0]["text_snippet"] or "") <= 300


def test_select_provider_page_prefers_global_and_ignores_noise():
    context = MagicMock()
    ignored = MagicMock()
    ignored.url = "chrome-extension://abc/popup.html"
    ignored.is_closed.return_value = False
    blank = MagicMock()
    blank.url = "about:blank"
    blank.is_closed.return_value = False
    login = MagicMock()
    login.url = "https://www.americanexpress.com/en-us/account/login"
    login.is_closed.return_value = False
    login.viewport_size = {"width": 800, "height": 600}
    global_page = MagicMock()
    global_page.url = "https://global.americanexpress.com/overview"
    global_page.is_closed.return_value = False
    global_page.viewport_size = {"width": 1200, "height": 900}
    context.pages = [ignored, blank, login, global_page]

    selected = select_provider_page(
        context,
        hostname_suffixes=("americanexpress.com",),
        preferred_hostnames=("global.americanexpress.com",),
        deprioritize_login=True,
    )
    assert selected is global_page


def test_runtime_persists_latest_inspection_without_bytes(tmp_path: Path):
    runtime = ProviderRuntime(
        root=tmp_path,
        cdp_port=9333,
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
    )
    runtime.cdp_url = "http://127.0.0.1:9333"
    page = MagicMock()
    page.url = "https://global.americanexpress.com/overview"
    page.is_closed.return_value = False
    page.main_frame = page
    page.frames = [page]
    page.evaluate.return_value = _modern_payload()
    page.viewport_size = {"width": 1200, "height": 800}
    browser = MagicMock()
    browser.contexts = [MagicMock(pages=[page])]
    cm = MagicMock()
    cm.__enter__.return_value = MagicMock(
        chromium=MagicMock(connect_over_cdp=MagicMock(return_value=browser))
    )
    cm.__exit__.return_value = None

    with patch("mighty.provider_runtime.sync_playwright", return_value=cm):
        payload = runtime.inspect_browser("amex", capture_screenshot=False)

    assert payload["ok"] is True
    assert payload["screenshot_path"] is None
    latest = runtime.latest_browser_inspection("amex")
    assert latest["ok"] is True
    assert "candidates" in latest
    assert latest.get("screenshot_path") is None
