"""Tests for minimal background-only Chrome extension popup."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
POPUP_HTML = REPO_ROOT / "extension" / "popup.html"
POPUP_JS = REPO_ROOT / "extension" / "popup.js"


def test_extension_popup_does_not_render_per_account_rows():
    js = POPUP_JS.read_text()
    html = POPUP_HTML.read_text()
    assert "needs sign in" not in html.lower()
    assert "needs login" not in html.lower()
    assert "account-row" not in html.lower()
    assert "renderAccessLoop" not in js
    assert "loop.headline" not in js
    assert "detail_lines" not in js
    assert "progress-wrap" not in html


def test_extension_popup_background_copy():
    html = POPUP_HTML.read_text()
    js = POPUP_JS.read_text()
    assert "Working in the background" in html
    assert "Open Account Center" in html
    assert "Working in the background" in js
    assert "Open Account Center" in js
    assert "Keeping your accounts up to date" in html
    assert "status_keeping_updated" in js
