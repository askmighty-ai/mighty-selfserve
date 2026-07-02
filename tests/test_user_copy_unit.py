"""Tests for mighty.user_copy helpers."""

from mighty.user_copy import HOW_UPDATES_TITLE, how_updates_html


def test_how_updates_html_structure():
    html = how_updates_html()
    assert HOW_UPDATES_TITLE in html
    assert 'class="sync-howto"' in html
    assert "Found from Gmail" in html
    assert "Chrome extension" in html
    assert "The Dashboard does not log into provider sites" in html
