"""Tests for Truth Dashboard home page rendering (PR #97)."""

import html
from datetime import datetime, timezone

from mighty.account_readiness import AccountReadiness, READY, SIGNED_OUT
from mighty.account_status import AccountStatus
from mighty.capability_state import CapabilityState
from mighty.customer_account_access import (
    DISCOVERED_MANUAL,
    build_customer_account_access_view,
)
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_home_page
from mighty import user_copy


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


def _readiness(provider: str, state: str, **kwargs) -> AccountReadiness:
    labels = {
        READY: ("Connected", user_copy.READINESS_COPY_READY, "ready", "up_to_date"),
        SIGNED_OUT: (
            "Sign in required",
            user_copy.READINESS_COPY_SIGNED_OUT,
            "needs_sign_in",
            "needs_login",
        ),
    }
    label, copy, presentation, canonical = labels[state]
    defaults = dict(
        provider=provider,
        state=state,
        status_label=label,
        status_copy=copy,
        presentation_key=presentation,
        canonical_status=canonical,
        login_required=state == SIGNED_OUT,
        session_state="connected" if state == READY else "signed_out",
        access_cycle_id=None,
        session_evidence_at=None,
        extraction_at=None,
        extraction_ok=state == READY,
        extraction_correlated=state == READY,
        verification_id=None,
        cached_data_label=None,
        last_confirmed_ready_at=(
            datetime.now(timezone.utc).isoformat() if state == READY else None
        ),
        last_confirmed_access_cycle_id="cycle-1" if state == READY else None,
        background_verification=False,
        secondary_label=None,
    )
    defaults.update(kwargs)
    return AccountReadiness(**defaults)  # type: ignore[arg-type]


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
        customer_access=view,
    )


class TestTruthDashboardHomeUi:
    def test_empty_shows_amex_login_unknown(self):
        result = resolve_home_state(accounts=[])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert "American Express" in rendered
        assert "could not determine your login state during the latest check" in rendered.lower()
        assert "cannot determine whether you are logged in" not in rendered.lower()
        assert "Open American Express" not in rendered
        assert "Summary" not in rendered
        assert "System Health" not in rendered
        assert "Connect Gmail" not in rendered

    def test_ready_shows_capability_success(self):
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=_readiness("amex", READY),
            discovered_from=DISCOVERED_MANUAL,
        )
        result = resolve_home_state(
            accounts=[_status_from_view(view)],
            extracted_items=[
                {"label": "Membership Rewards", "value": "125,000"},
                {"label": "Statement balance", "value": "$42.00"},
            ],
            session_confidence="high",
        )
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            last_checked="2h ago",
            escape=_escape,
        )
        assert result.capability is not None
        assert result.capability.state == CapabilityState.EXTRACTION_SUCCESS
        assert "can see and extract" in rendered.lower()
        assert "Membership Rewards" in rendered
        assert "Statement balance" in rendered
        assert "Confidence: High" in rendered
        assert "Technical Details" in rendered
        assert "Truth Timeline" in rendered
        assert "Summary" not in rendered
        assert "System Health" not in rendered
        assert "Last checked: 2h ago" in rendered

    def test_hides_non_amex_providers(self):
        amex = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=_readiness("amex", READY),
            discovered_from=DISCOVERED_MANUAL,
        )
        delta = AccountStatus(
            source="delta",
            display_name="Delta",
            status="up_to_date",
            presentation_key="ready",
            presentation_label="Connected",
            last_successful_sync_at=None,
            current_attempt_at=None,
            last_error=None,
            user_action_label=None,
            user_action_url=None,
        )
        result = resolve_home_state(accounts=[_status_from_view(amex), delta])
        rendered = render_home_page(
            result, first_name="Alex", today_label="Friday, July 3", escape=_escape,
        )
        assert "American Express" in rendered
        assert "Delta" not in rendered
        assert len(result.access_views) == 1

    def test_signed_out_cta(self):
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=_readiness("amex", SIGNED_OUT),
            discovered_from=DISCOVERED_MANUAL,
            user_action_text="Sign in",
            user_action_url="https://www.americanexpress.com/en-us/account/login",
        )
        result = resolve_home_state(
            accounts=[_status_from_view(view, canonical="needs_login")],
        )
        rendered = render_home_page(
            result, first_name="Alex", today_label="Friday, July 3", escape=_escape,
        )
        assert "You are signed out" in rendered
        assert "Open American Express" in rendered
        assert "Please sign in to American Express" in rendered
        assert "Needs you" not in rendered
