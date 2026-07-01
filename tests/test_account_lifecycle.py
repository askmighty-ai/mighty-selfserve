"""Tests for unified account lifecycle."""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_lifecycle import (
    ADDED,
    CONNECTED,
    DISCOVERED,
    NEEDS_LOGIN,
    SYNCED,
    WAITING_FOR_EXTENSION,
    resolve_account_lifecycle,
)
from mighty.connection_state import CONNECTED as CONN_CONNECTED, NEEDS_LOGIN as CONN_NEEDS_LOGIN
from mighty.connection_state import WAITING_FOR_EXTENSION as CONN_WAITING
from mighty.provider_account import (
    EXTRACTION_COMPLETE,
    ProviderAccount,
)


def _acct(**kwargs) -> ProviderAccount:
    defaults = dict(source="delta", sync_status="ok")
    defaults.update(kwargs)
    return ProviderAccount(**defaults)


def test_discovered_from_email():
    lc = resolve_account_lifecycle("delta", from_email=True)
    assert lc.state == DISCOVERED
    assert lc.cta_label == "Add to Mighty"
    assert lc.source_label == "Found from Gmail"


def test_added_not_registered():
    lc = resolve_account_lifecycle("delta", email_added=True, from_email=True)
    assert lc.state == ADDED
    assert lc.cta_label == "Connect"


def test_waiting_for_extension_needs_first_visit():
    lc = resolve_account_lifecycle(
        "delta",
        in_credentials=True,
        account=_acct(sync_status="needs_first_visit"),
    )
    assert lc.state == WAITING_FOR_EXTENSION
    assert lc.secondary_cta_label == "I installed the extension / Retry"


def test_waiting_for_extension_connection_status():
    lc = resolve_account_lifecycle(
        "amex",
        in_credentials=True,
        account=_acct(connection_status=CONN_WAITING, sync_status="needs_first_visit"),
    )
    assert lc.state == WAITING_FOR_EXTENSION


def test_needs_login_from_sync_status():
    lc = resolve_account_lifecycle(
        "delta",
        in_credentials=True,
        account=_acct(sync_status="login_required"),
    )
    assert lc.state == NEEDS_LOGIN
    assert lc.cta_label == "Log in"


def test_needs_login_from_connection_status():
    lc = resolve_account_lifecycle(
        "amex",
        in_credentials=True,
        account=_acct(connection_status=CONN_NEEDS_LOGIN),
    )
    assert lc.state == NEEDS_LOGIN


def test_connected_session_verified_no_fields():
    lc = resolve_account_lifecycle(
        "amex",
        in_credentials=True,
        account=_acct(connection_status=CONN_CONNECTED, sync_status="ok"),
    )
    assert lc.state == CONNECTED
    assert lc.cta_label == "Sync now"
    assert lc.show_last_sync is False


def test_connected_session_verified_only_with_connection_status():
    lc = resolve_account_lifecycle(
        "delta",
        in_credentials=True,
        account=_acct(connection_status=CONN_CONNECTED, sync_status="ok"),
    )
    assert lc.state == CONNECTED


def test_ok_sync_status_alone_is_not_connected():
    """Visiting a provider or stale ok status must not imply Connected."""
    lc = resolve_account_lifecycle(
        "delta",
        in_credentials=True,
        account=_acct(sync_status="ok"),
    )
    assert lc.state == WAITING_FOR_EXTENSION
    assert lc.state != CONNECTED


def test_never_connected_on_domain_visit_only():
    """needs_first_visit alone must not show Connected."""
    lc = resolve_account_lifecycle(
        "delta",
        in_credentials=True,
        account=_acct(sync_status="needs_first_visit"),
    )
    assert lc.state != CONNECTED


def test_synced_requires_real_fields():
    fields = [{"label": "Miles", "value": "42,000"}]
    lc = resolve_account_lifecycle(
        "delta",
        in_credentials=True,
        account=_acct(
            normalized_fields=fields,
            extraction_status=EXTRACTION_COMPLETE,
            synced_at="2026-01-01T12:00:00",
        ),
    )
    assert lc.state == SYNCED
    assert lc.show_last_sync is True
    assert lc.extracted_field_count == 1


def test_never_synced_without_fields():
    lc = resolve_account_lifecycle(
        "delta",
        in_credentials=True,
        account=_acct(
            normalized_fields=[],
            extraction_status=EXTRACTION_COMPLETE,
            synced_at="2026-01-01T12:00:00",
        ),
    )
    assert lc.state != SYNCED


def test_synced_beats_connected():
    fields = [{"label": "Points", "value": "1,200"}]
    lc = resolve_account_lifecycle(
        "amex",
        in_credentials=True,
        account=_acct(
            connection_status=CONN_CONNECTED,
            normalized_fields=fields,
            extraction_status=EXTRACTION_COMPLETE,
            synced_at="2026-01-01T12:00:00",
        ),
    )
    assert lc.state == SYNCED
