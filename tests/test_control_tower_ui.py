"""UI regression tests for Capability/Truth panels (debug instrument).

Home V1 is a pure projection surface; Truth Dashboard copy lives on
``render_capability_panel`` and appears on Home only when
``show_access_debug`` is enabled (see docs/HOME_V1.md).
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from mighty.account_readiness import AccountReadiness, READY, CHECKING, SIGNED_OUT, UNVERIFIED
from mighty.account_status import AccountStatus
from mighty.capability_state import CapabilityState
from mighty.customer_account_access import (
    DISCOVERED_GMAIL,
    DISCOVERED_MANUAL,
    CustomerAccountAccessView,
    build_customer_account_access_view,
)
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_capability_panel, render_home_page
from mighty.provider_account import EXTRACTION_COMPLETE, EXTRACTION_FAILED
from mighty import user_copy


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


def _readiness(provider: str, state: str, **kwargs) -> AccountReadiness:
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
    defaults = dict(
        provider=provider,
        state=state,
        status_label=label,
        status_copy=copy,
        presentation_key=presentation,
        canonical_status=canonical,
        login_required=state == SIGNED_OUT,
        session_state="connected" if state == READY else (
            "signed_out" if state == SIGNED_OUT else "unknown"
        ),
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


def _view_from_readiness(display_name: str, readiness: AccountReadiness, **kwargs):
    return build_customer_account_access_view(
        provider=readiness.provider,
        display_name=display_name,
        readiness=readiness,
        discovered_from=kwargs.pop("discovered_from", DISCOVERED_GMAIL),
        verification_lifecycle=kwargs.pop("verification_lifecycle", None),
        **kwargs,
    )


def _status_from_view(view: CustomerAccountAccessView, *, canonical: str | None = None) -> AccountStatus:
    canonical = canonical or view.canonical_status or "unverified"
    presentation_key = {
        "up_to_date": "ready",
        "needs_login": "needs_sign_in",
        "checking": "checking",
        "waiting_for_extension": "updating",
        "error": "needs_attention",
        "unverified": "unknown",
        "updating": "updating",
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


def _resolve(accounts, **kwargs):
    return resolve_home_state(accounts=accounts, actions=[], **kwargs)


def _render_capability(result):
    assert result.capability is not None
    return render_capability_panel(result.capability, escape=_escape)


def _without_tech(rendered: str) -> str:
    return re.sub(
        r'<details class="dash-truth-tech">.*?</details>',
        "",
        rendered,
        flags=re.DOTALL,
    )


class TestTruthDashboardStates:
    def test_extraction_success(self):
        view = _view_from_readiness("American Express", _readiness("amex", READY))
        result = _resolve(
            [_status_from_view(view)],
            extracted_items=[{"label": "Membership Rewards", "value": "50,000"}],
            session_confidence="high",
        )
        rendered = _render_capability(result)
        assert result.capability.state == CapabilityState.EXTRACTION_SUCCESS
        assert "can see and extract" in rendered.lower()
        assert "Membership Rewards" in rendered
        assert "Confidence: High" in rendered
        assert "Technical Details" in rendered

    def test_signed_out(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", SIGNED_OUT, session_state="signed_out"),
            user_action_text="Sign in",
            user_action_url="https://example.com/login",
        )
        result = _resolve([_status_from_view(view, canonical="needs_login")])
        rendered = _render_capability(result)
        assert result.capability.state == CapabilityState.SIGNED_OUT
        assert "You are signed out" in rendered
        assert "Open American Express" in rendered
        assert "Definitive login page" in rendered or "signed-out" in rendered.lower()

    def test_extraction_failed(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", UNVERIFIED, session_state="connected"),
            extraction_status=EXTRACTION_FAILED,
        )
        result = _resolve(
            [_status_from_view(view, canonical="unverified")],
            extraction_status=EXTRACTION_FAILED,
        )
        rendered = _render_capability(result)
        assert result.capability.state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
        assert "could not extract" in rendered.lower()
        assert "No customer action required." in rendered
        assert "Extraction failed" in rendered

    def test_logged_in_no_data(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", UNVERIFIED, session_state="connected"),
        )
        result = _resolve(
            [_status_from_view(view, canonical="unverified")],
            extraction_status=EXTRACTION_COMPLETE,
            extracted_items=[],
        )
        rendered = _render_capability(result)
        assert result.capability.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        assert "cannot see your account information" in rendered.lower()

    def test_login_unknown(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", CHECKING, session_state="checking"),
            verification_lifecycle="running",
        )
        result = _resolve([_status_from_view(view, canonical="checking")])
        rendered = _render_capability(result)
        assert result.capability.state == CapabilityState.LOGIN_UNKNOWN
        headline = (result.capability.primary_headline or result.capability.headline or "").lower()
        assert "login state" in headline
        assert "cannot determine whether you are logged in" not in rendered.lower()
        assert "could not determine your login state during the latest check" not in rendered.lower()
        assert result.capability.presentation_phase == "determining"
        assert not any(
            "in progress" in e.text.lower() for e in result.capability.evidence
        )
        assert result.capability.is_refreshing is True


class TestTruthDashboardPresentation:
    def test_capability_access_views_remain_amex_only(self):
        accounts = [
            _status_from_view(_view_from_readiness("American Express", _readiness("amex", READY))),
            _status_from_view(
                _view_from_readiness(
                    "Delta",
                    _readiness("delta", CHECKING, session_state="checking"),
                ),
                canonical="checking",
            ),
            _status_from_view(
                _view_from_readiness(
                    "United",
                    _readiness("united", SIGNED_OUT, session_state="signed_out"),
                    user_action_text="Sign in",
                    user_action_url="https://example.com",
                ),
                canonical="needs_login",
            ),
        ]
        result = _resolve(accounts)
        assert len(result.access_views) == 1
        assert result.access_views[0].provider == "amex"
        # Home V1 health may mention portfolio counts; capability stays Amex-scoped.
        home = render_home_page(
            result, first_name="Jonathan", today_label="Monday, July 13", escape=_escape,
        )
        assert "home-v2" in home
        assert "System Health" not in home
        assert "Capability debug" not in home

    def test_no_discovery_jargon_outside_tech_details(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", READY),
            discovered_from=DISCOVERED_MANUAL,
        )
        result = _resolve([_status_from_view(view)])
        rendered = _render_capability(result)
        visible = _without_tech(rendered)
        assert "Discovered from" not in visible
        assert "Live access" not in visible
        assert "readiness" not in visible.lower()
        assert "Technical Details" in rendered
        assert "Session Evidence" in rendered
        assert "Verification" in rendered
        assert "Extraction" in rendered
        assert "Snapshot" in rendered

    def test_home_debug_surfaces_capability_panel(self):
        view = _view_from_readiness("American Express", _readiness("amex", READY))
        result = _resolve(
            [_status_from_view(view)],
            show_access_debug=True,
            extracted_items=[{"label": "Membership Rewards", "value": "50,000"}],
            session_confidence="high",
        )
        home = render_home_page(
            result, first_name="Jonathan", today_label="Monday, July 13", escape=_escape,
        )
        assert "Capability debug" in home
        assert "can see and extract" in home.lower()
