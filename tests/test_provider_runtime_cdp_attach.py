"""Tests for robust Playwright CDP attach to managed Amex Chrome."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from mighty.provider_runtime import (
    CDP_ATTACH_FAILED_MESSAGE,
    CDP_ATTACH_NO_TARGETS_MESSAGE,
    connect_chromium_over_cdp,
    ensure_cdp_page_targets_available,
    playwright_supports_connect_over_cdp_no_defaults,
)


def test_installed_playwright_supports_no_defaults():
    assert playwright_supports_connect_over_cdp_no_defaults() is True


def test_connect_over_cdp_called_with_no_defaults_true():
    browser = MagicMock(name="browser")
    connect = MagicMock(return_value=browser)
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = connect

    with patch(
        "mighty.provider_runtime.ensure_cdp_page_targets_available",
        return_value=None,
    ), patch(
        "mighty.provider_runtime.playwright_supports_connect_over_cdp_no_defaults",
        return_value=True,
    ):
        result = connect_chromium_over_cdp(playwright, "http://127.0.0.1:9223")

    assert result is browser
    connect.assert_called_once_with("http://127.0.0.1:9223", no_defaults=True)


def test_connect_over_cdp_omits_no_defaults_when_unsupported():
    browser = MagicMock(name="browser")
    connect = MagicMock(return_value=browser)
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = connect

    with patch(
        "mighty.provider_runtime.ensure_cdp_page_targets_available",
        return_value=None,
    ), patch(
        "mighty.provider_runtime.playwright_supports_connect_over_cdp_no_defaults",
        return_value=False,
    ):
        result = connect_chromium_over_cdp(playwright, "http://127.0.0.1:9223")

    assert result is browser
    connect.assert_called_once_with("http://127.0.0.1:9223")


def test_zero_target_browser_raises_friendly_runtime_error():
    version = {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/abc"}

    def fake_fetch(url: str, *, timeout: float = 1.0):
        if url.endswith("/json/version"):
            return version
        if url.endswith("/json/list"):
            return []
        raise AssertionError(f"unexpected CDP URL {url}")

    with patch("mighty.provider_runtime.fetch_cdp_json", side_effect=fake_fetch):
        with pytest.raises(RuntimeError, match="no page targets") as exc_info:
            ensure_cdp_page_targets_available("http://127.0.0.1:9223")

    message = str(exc_info.value)
    assert message == CDP_ATTACH_NO_TARGETS_MESSAGE
    assert "bootstrap amex" in message
    assert "Browser.setDownloadBehavior" not in message


def test_zero_target_blocks_connect_before_playwright():
    connect = MagicMock(side_effect=AssertionError("connect should not run"))
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = connect

    with patch(
        "mighty.provider_runtime.ensure_cdp_page_targets_available",
        side_effect=RuntimeError(CDP_ATTACH_NO_TARGETS_MESSAGE),
    ):
        with pytest.raises(RuntimeError, match="no page targets"):
            connect_chromium_over_cdp(playwright, "http://127.0.0.1:9223")

    connect.assert_not_called()


def test_normal_browser_still_attaches_successfully():
    browser = MagicMock(name="browser")
    connect = MagicMock(return_value=browser)
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = connect

    version = {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/abc"}
    targets = [{"type": "page", "url": "https://www.americanexpress.com/"}]

    def fake_fetch(url: str, *, timeout: float = 1.0):
        if url.endswith("/json/version"):
            return version
        if url.endswith("/json/list"):
            return targets
        raise AssertionError(f"unexpected CDP URL {url}")

    with patch("mighty.provider_runtime.fetch_cdp_json", side_effect=fake_fetch), patch(
        "mighty.provider_runtime.playwright_supports_connect_over_cdp_no_defaults",
        return_value=True,
    ):
        result = connect_chromium_over_cdp(playwright, "http://127.0.0.1:9223")

    assert result is browser
    connect.assert_called_once_with("http://127.0.0.1:9223", no_defaults=True)


def test_original_exception_is_chained_as_cause():
    original = RuntimeError(
        "BrowserType.connect_over_cdp: Protocol error "
        "(Browser.setDownloadBehavior): Browser context management is not supported."
    )
    connect = MagicMock(side_effect=original)
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = connect

    with patch(
        "mighty.provider_runtime.ensure_cdp_page_targets_available",
        return_value=None,
    ), patch(
        "mighty.provider_runtime.playwright_supports_connect_over_cdp_no_defaults",
        return_value=True,
    ):
        with pytest.raises(RuntimeError) as exc_info:
            connect_chromium_over_cdp(playwright, "http://127.0.0.1:9223")

    wrapped = exc_info.value
    assert str(wrapped) == CDP_ATTACH_FAILED_MESSAGE
    assert wrapped.__cause__ is original
    assert "Browser.setDownloadBehavior" not in str(wrapped)
    assert "Browser.setDownloadBehavior" in str(wrapped.__cause__)


def test_unreachable_cdp_diagnostics_soft_skip_then_connect():
    connect = MagicMock(return_value=MagicMock(name="browser"))
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp = connect

    with patch(
        "mighty.provider_runtime.fetch_cdp_json",
        side_effect=URLError("connection refused"),
    ), patch(
        "mighty.provider_runtime.playwright_supports_connect_over_cdp_no_defaults",
        return_value=True,
    ):
        connect_chromium_over_cdp(playwright, "http://127.0.0.1:9223")

    connect.assert_called_once_with("http://127.0.0.1:9223", no_defaults=True)
