"""Regression tests for customer-facing account access view model (PR #94A)."""

from __future__ import annotations

import html
from datetime import datetime, timezone

from mighty.account_lifecycle import resolve_account_lifecycle
from mighty.account_readiness import (
    AccountReadiness,
    READY,
    CHECKING,
    SIGNED_OUT,
    UNVERIFIED,
)
from mighty.account_status import AccountStatus, build_account_status
from mighty.accounts_ui import (
    SECTION_NEEDS_LOGIN,
    SECTION_UP_TO_DATE,
    SECTION_WAITING,
    apply_access_view_to_row,
    AccountsRow,
    section_for_view,
)
from mighty.customer_account_access import (
    BG_AWAITING_FIRST,
    BG_NONE,
    BG_VERIFYING,
    DISCOVERED_GMAIL,
    DISCOVERED_MANUAL,
    LIVE_CONNECTED,
    LIVE_SIGNED_OUT,
    LIVE_UNKNOWN,
    PRIVATE_SEEN,
    build_customer_account_access_view,
    connected_summary_label,
    resolve_discovered_from,
)
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_home_page
from mighty.login_truth import CurrentAccountAccess
from mighty.provider_account import EXTRACTION_COMPLETE, ProviderAccount
from mighty import user_copy


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


def _readiness(
    provider: str,
    state: str,
    *,
    session_state: str = "unknown",
    extraction_ok: bool = False,
    extraction_correlated: bool = False,
    background_verification: bool = False,
    last_confirmed_ready_at: str | None = None,
    last_confirmed_access_cycle_id: str | None = None,
    access_cycle_id: str | None = None,
    cached_data_label: str | None = None,
    secondary_label: str | None = None,
    extraction_at: str | None = None,
) -> AccountReadiness:
    labels = {
        READY: ("Connected", user_copy.READINESS_COPY_READY, "ready", "up_to_date"),
        CHECKING: ("Checking", user_copy.READINESS_COPY_CHECKING, "checking", "checking"),
        SIGNED_OUT: (
            "Sign in required",
            user_copy.READINESS_COPY_SIGNED_OUT,
            "needs_sign_in",
            "needs_login",
        ),
        UNVERIFIED: (
            "Unable to verify",
            user_copy.READINESS_COPY_UNVERIFIED,
            "unknown",
            "unverified",
        ),
    }
    label, copy, presentation, canonical = labels[state]
    return AccountReadiness(
        provider=provider,
        state=state,  # type: ignore[arg-type]
        status_label=label,
        status_copy=copy,
        presentation_key=presentation,
        canonical_status=canonical,
        login_required=state == SIGNED_OUT,
        session_state=session_state,  # type: ignore[arg-type]
        access_cycle_id=access_cycle_id,
        session_evidence_at=None,
        extraction_at=extraction_at,
        extraction_ok=extraction_ok,
        extraction_correlated=extraction_correlated,
        verification_id=None,
        cached_data_label=cached_data_label,
        last_confirmed_ready_at=last_confirmed_ready_at,
        last_confirmed_access_cycle_id=last_confirmed_access_cycle_id,
        background_verification=background_verification,
        secondary_label=secondary_label,
    )


def _status_from_view(view, *, canonical: str | None = None) -> AccountStatus:
    canonical = canonical or view.canonical_status or "unverified"
    presentation_key = {
        "up_to_date": "ready",
        "needs_login": "needs_sign_in",
        "checking": "checking",
        "waiting_for_extension": "updating",
        "error": "needs_attention",
        "unverified": "unknown",
    }.get(canonical, "unknown")
    return AccountStatus(
        source=view.provider,
        display_name=view.display_name,
        status=canonical,
        presentation_key=presentation_key,
        presentation_label=view.status_label,
        last_successful_sync_at=view.last_confirmed_at,
        current_attempt_at=None,
        last_error=None,
        user_action_label=view.user_action_text,
        user_action_url=view.user_action_url,
        session_state=view.session_state,
        readiness=view.readiness,
        login_required=view.user_action_required,
        customer_access=view,
        discovered_from=view.discovered_from,
        background_verification=view.background_verification,
        cached_data_label=view.cached_data_label,
    )


class TestCustomerAccountAccessView:
    def test_ready_amex_dashboard_names_provider_and_private_data(self):
        readiness = _readiness(
            "amex",
            READY,
            session_state="connected",
            extraction_ok=True,
            extraction_correlated=True,
            last_confirmed_ready_at=datetime.now(timezone.utc).isoformat(),
            access_cycle_id="cycle-1",
            last_confirmed_access_cycle_id="cycle-1",
        )
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_MANUAL,
            verification_lifecycle="completed",
        )
        assert view.live_access == LIVE_CONNECTED
        assert view.private_data_label == PRIVATE_SEEN
        assert view.background_work == BG_NONE
        assert view.meaning == user_copy.ACCESS_MEANING_CONNECTED_SEEN
        assert "Connected" == view.status_label
        assert view.discovered_from == DISCOVERED_MANUAL

        status = _status_from_view(view, canonical="up_to_date")
        result = resolve_home_state(accounts=[status])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Monday, July 13",
            escape=_escape,
        )
        assert "American Express" in rendered
        assert "can see and extract" in rendered.lower()
        assert 'data-capability="extraction_success"' in rendered
        assert "Technical Details" in rendered
        assert "Summary" not in rendered
        assert "✓ Watching" not in rendered

    def test_gmail_discovered_never_connected(self):
        readiness = _readiness("amex", UNVERIFIED, session_state="unknown")
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_GMAIL,
        )
        assert view.live_access == LIVE_UNKNOWN
        assert view.status_label != "Connected"
        assert view.discovered_from == DISCOVERED_GMAIL
        assert view.background_work == BG_AWAITING_FIRST
        assert view.user_action_required is False
        assert view.user_action_text is None

        status = _status_from_view(view, canonical="waiting_for_extension")
        result = resolve_home_state(accounts=[status])
        rendered = render_home_page(
            result, first_name="Alex", today_label="Monday, July 13", escape=_escape,
        )
        assert "could not determine your login state during the latest check" in rendered.lower()
        assert "cannot determine whether you are logged in" not in rendered.lower()
        assert 'data-capability="login_unknown"' in rendered
        assert 'data-capability="extraction_success"' not in rendered
        assert "✓ Watching" not in rendered

    def test_ready_with_background_verifying_stays_connected(self):
        readiness = _readiness(
            "amex",
            READY,
            session_state="checking",
            extraction_ok=True,
            extraction_correlated=True,
            background_verification=True,
            last_confirmed_ready_at="2026-07-13T14:00:00+00:00",
            last_confirmed_access_cycle_id="cycle-ready",
            access_cycle_id="cycle-ready",
            secondary_label=user_copy.READINESS_SECONDARY_BACKGROUND,
        )
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_MANUAL,
            verification_lifecycle="running",
        )
        assert view.status_label == "Connected"
        assert view.live_access == LIVE_CONNECTED
        assert view.background_work == BG_VERIFYING
        assert view.background_work != BG_AWAITING_FIRST
        assert view.private_data_label == PRIVATE_SEEN

        status = _status_from_view(view, canonical="up_to_date")
        rendered = render_home_page(
            resolve_home_state(accounts=[status]),
            first_name="Alex",
            today_label="Monday, July 13",
            escape=_escape,
        )
        # Active verification → determining; prior extraction is historical only.
        assert "refreshing current status" in rendered.lower()
        assert "last confirmed:" in rendered.lower()
        assert "could access and extract" in rendered.lower()
        assert "mighty can see and extract your logged-in account data" not in rendered.lower()
        assert 'data-presentation-phase="determining"' in rendered
        assert 'data-capability="determining"' in rendered
        assert "Waiting for first verification" not in rendered
        assert "Awaiting first check" not in rendered
        assert "freshness window" not in rendered.lower()

    def test_dashboard_and_accounts_compatible(self):
        readiness = _readiness(
            "amex",
            READY,
            session_state="connected",
            extraction_ok=True,
            extraction_correlated=True,
            last_confirmed_ready_at=datetime.now(timezone.utc).isoformat(),
        )
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_GMAIL,
            verification_lifecycle="completed",
        )
        # Discovery remains visible but does not decide Connected.
        assert view.discovered_from == DISCOVERED_GMAIL
        assert view.status_label == "Connected"
        assert section_for_view(view) == SECTION_UP_TO_DATE

        status = _status_from_view(view, canonical="up_to_date")
        home = resolve_home_state(accounts=[status])
        home_html = render_home_page(
            home, first_name="Alex", today_label="Monday, July 13", escape=_escape,
        )
        assert "can see and extract" in home_html.lower()
        assert "Technical Details" in home_html

        lc = resolve_account_lifecycle("amex", in_credentials=True, from_email=True)
        row = AccountsRow(
            source="amex",
            display_name="American Express",
            icon="💳",
            color="#eee",
            section=SECTION_WAITING,
            status_label="Awaiting first check",
            subline="",
            source_label="Found from Gmail",
            lifecycle=lc,
            synced_fmt="1 minute ago",
        )
        apply_access_view_to_row(row, view)
        assert row.section == SECTION_UP_TO_DATE
        assert row.status_label == "Extraction success"
        assert "Updated" in row.subline or "extract" in row.subline.lower()
        assert "Discovered from Gmail" in row.source_label
        assert row.status_label != "Awaiting first check"

    def test_signed_out_shows_sign_in_cached_secondary(self):
        readiness = _readiness(
            "amex",
            SIGNED_OUT,
            session_state="signed_out",
            cached_data_label="Last saved data: 2 hours ago",
        )
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_MANUAL,
            user_action_text="Sign in",
            user_action_url="https://example.com/login",
        )
        assert view.live_access == LIVE_SIGNED_OUT
        assert view.status_label == "Sign in required"
        assert view.user_action_required is True
        assert view.user_action_text == "Sign in"
        assert view.cached_data_label == "Last saved data: 2 hours ago"
        assert section_for_view(view) == SECTION_NEEDS_LOGIN

        rendered = render_home_page(
            resolve_home_state(accounts=[_status_from_view(view, canonical="needs_login")]),
            first_name="Alex",
            today_label="Monday, July 13",
            escape=_escape,
        )
        assert "You are signed out" in rendered
        assert "Open American Express" in rendered

    def test_unknown_no_sign_in_cta(self):
        readiness = _readiness("amex", UNVERIFIED, session_state="unknown")
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_MANUAL,
            user_action_text="Sign in",  # must be ignored unless signed_out
            user_action_url="https://example.com/login",
        )
        assert view.user_action_required is False
        assert view.user_action_text is None
        assert view.user_action_url is None
        assert view.live_access == LIVE_UNKNOWN
        assert "not" in view.meaning.lower() or "has not confirmed" in view.meaning

        rendered = render_home_page(
            resolve_home_state(
                accounts=[_status_from_view(view, canonical="waiting_for_extension")]
            ),
            first_name="Alex",
            today_label="Monday, July 13",
            escape=_escape,
        )
        assert "Open American Express" not in rendered
        assert "could not determine your login state during the latest check" in rendered.lower()
        assert "cannot determine whether you are logged in" not in rendered.lower()
        assert (
            "No definitive current login evidence" in rendered
            or "Verification" in rendered
        )

    def test_labels_not_from_legacy_sync_or_gmail(self):
        # Gmail discovery alone must not produce Connected via build_account_status.
        lc = resolve_account_lifecycle(
            "amex",
            in_credentials=True,
            from_email=True,
            account=ProviderAccount(source="amex", sync_status="ok"),
        )
        status = build_account_status(
            "amex",
            "American Express",
            lc,
            ProviderAccount(
                source="amex",
                sync_status="ok",
                connection_status="connected",
                extraction_status=EXTRACTION_COMPLETE,
                normalized_fields=[{"key": "balance", "value": "100"}],
            ),
            sync_status="ok",
            updating_source=None,
            connection_status="connected",
            session_access=CurrentAccountAccess(
                provider="amex",
                current_access="unknown",
                cached_data_state="fresh",
                last_verified=None,
                last_private_data="2026-07-01T00:00:00+00:00",
                evidence="test",
                source="test",
                next_action_type="none",
                next_action_text="",
            ),
        )
        assert status.customer_access is not None
        assert status.customer_access.discovered_from == DISCOVERED_GMAIL
        assert status.customer_access.status_label != "Connected"
        assert status.readiness != READY
        # Legacy connection_status / sync_status must not drive Connected.
        assert status.status != "up_to_date"

    def test_connected_summary_names_provider(self):
        readiness = _readiness(
            "amex", READY, session_state="connected",
            extraction_ok=True, extraction_correlated=True,
        )
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_MANUAL,
        )
        assert connected_summary_label([view]) == "Connected: American Express"

    def test_discovered_from_resolver(self):
        assert resolve_discovered_from(from_email=True) == DISCOVERED_GMAIL
        assert resolve_discovered_from(data_source="extension") == "Extension visit"
        assert resolve_discovered_from() == DISCOVERED_MANUAL

    def test_debug_rows_have_no_secrets(self):
        readiness = _readiness("amex", READY, session_state="connected",
                               extraction_ok=True, extraction_correlated=True)
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_MANUAL,
            evidence_source="extension",
            verification_lifecycle="completed",
        )
        blob = " ".join(f"{k}={v}" for k, v in view.debug_rows()).lower()
        for banned in ("token", "cookie", "password", "secret"):
            assert banned not in blob
