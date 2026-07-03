"""Tests for mighty.user_copy helpers."""

from mighty.user_copy import (
    HOW_UPDATES_TITLE,
    ROLE_DASHBOARD,
    ROLE_EXTENSION,
    STATUS_LABEL_UPDATED,
    STATUS_LABEL_WAITING,
    api_copy_bundle,
    how_updates_html,
    summary_updating,
)


def test_how_updates_html_structure():
    html = how_updates_html()
    assert HOW_UPDATES_TITLE in html
    assert 'class="sync-howto"' in html
    assert "Control center" in html
    assert "Worker in Chrome" in html
    assert "Login is the only thing you do manually" in html


def test_shared_roles_and_activity_model():
    bundle = api_copy_bundle()
    assert bundle["roles"]["dashboard"] == ROLE_DASHBOARD
    assert bundle["roles"]["extension"] == ROLE_EXTENSION
    assert bundle["status_labels"]["up_to_date"] == STATUS_LABEL_UPDATED
    assert bundle["status_labels"]["waiting_for_extension"] == STATUS_LABEL_WAITING


def test_summary_headlines_use_updating_verb():
    assert summary_updating("Delta") == "Updating Delta"
