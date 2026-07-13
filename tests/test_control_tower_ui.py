"""UI regression tests for Dashboard Control Tower presentation (PR #96)."""

from __future__ import annotations

import html

from mighty.account_readiness import AccountReadiness, READY, CHECKING, SIGNED_OUT, UNVERIFIED
from mighty.account_status import AccountStatus
from mighty.customer_account_access import (
    BG_AWAITING_FIRST,
    DISCOVERED_GMAIL,
    LIVE_UNKNOWN,
    CustomerAccountAccessView,
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
        last_confirmed_ready_at="2026-07-13T15:48:00+00:00" if state == READY else None,
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


def _render(accounts, **kwargs):
    result = resolve_home_state(accounts=accounts, actions=[], **kwargs)
    html_out = render_home_page(
        result,
        first_name="Jonathan",
        today_label="Monday, July 13",
        escape=_escape,
    )
    return result, html_out


def _visible_without_why(rendered: str) -> str:
    """Strip Why? expandable content so we can assert product language only."""
    import re
    return re.sub(
        r'<details class="dash-access-why">.*?</details>',
        "",
        rendered,
        flags=re.DOTALL,
    )


class TestControlTowerHero:
    def test_one_connected_account_watching_no_action(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", READY),
        )
        result, rendered = _render([_status_from_view(view)])
        assert "watching your accounts" in rendered.lower()
        assert "No action needed." in rendered
        assert "all set" not in rendered.lower()
        assert result.tower.watching_count == 1
        assert result.tower.needs_you_count == 0

    def test_connected_while_background_refresh_running(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", READY, background_verification=True),
            verification_lifecycle="extracting",
        )
        result, rendered = _render(
            [_status_from_view(view)],
            sync_running=True,
            updating_display_name="American Express",
        )
        assert "watching your accounts" in rendered.lower()
        assert "Current activity: Refreshing account" in rendered
        assert "all set" not in rendered.lower()
        assert result.tower.refreshing_count >= 1 or result.updating_display_name

    def test_one_signed_out_account_needs_attention(self):
        view = _view_from_readiness(
            "United",
            _readiness("united", SIGNED_OUT, session_state="signed_out"),
            user_action_text="Sign in",
            user_action_url="https://example.com/login",
        )
        result, rendered = _render([_status_from_view(view, canonical="needs_login")])
        assert "Needs your attention" in rendered or "needs your attention" in rendered.lower()
        assert "No action needed." not in rendered
        assert result.tower.needs_you_count == 1

    def test_multiple_waiting_accounts_system_health(self):
        accounts = [
            _status_from_view(
                _view_from_readiness("American Express", _readiness("amex", READY)),
            ),
            _status_from_view(
                _view_from_readiness(
                    "Delta",
                    _readiness("delta", CHECKING, session_state="checking"),
                    verification_lifecycle="visiting",
                ),
                canonical="checking",
            ),
            _status_from_view(
                _view_from_readiness(
                    "Hilton",
                    _readiness("hilton", UNVERIFIED),
                ),
                canonical="waiting_for_extension",
            ),
            _status_from_view(
                _view_from_readiness(
                    "United",
                    _readiness("united", UNVERIFIED),
                ),
                canonical="unverified",
            ),
        ]
        result, rendered = _render(accounts)
        assert "System Health" in rendered
        assert "Watching" in rendered
        assert "Refreshing" in rendered
        assert "Waiting" in rendered
        assert "Needs your help" in rendered
        assert result.tower.watching_count == 1
        assert result.tower.refreshing_count >= 1
        assert result.tower.waiting_count >= 1


class TestControlTowerConsistency:
    def test_no_contradictory_hero_and_account_messaging(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", READY, background_verification=True),
            verification_lifecycle="extracting",
        )
        waiting = _view_from_readiness(
            "Hilton",
            _readiness("hilton", UNVERIFIED),
        )
        result, rendered = _render([
            _status_from_view(view),
            _status_from_view(waiting, canonical="waiting_for_extension"),
        ])
        assert "all set" not in rendered.lower()
        assert "No action needed." in rendered
        # Hero should acknowledge background work, not claim idle all-clear.
        assert (
            "being verified" in rendered.lower()
            or "waiting" in rendered.lower()
            or "monitoring" in rendered.lower()
            or "watching" in rendered.lower()
        )
        assert result.tower.has_background_work

    def test_no_developer_terminology_outside_why(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", READY),
            discovered_from=DISCOVERED_GMAIL,
        )
        _, rendered = _render([_status_from_view(view)])
        visible = _visible_without_why(rendered)
        assert "Discovered from Gmail" not in visible
        assert "Private data" not in visible
        assert "Live access" not in visible
        assert ">Background<" not in visible and "Background None" not in visible
        assert "Current activity" in visible
        assert "Watching" in visible
        # Why? still holds discovery / private data for expansion.
        assert "Why?" in rendered
        assert "Discovered from" in rendered


class TestControlTowerAccountCards:
    def test_watching_card_product_language(self):
        view = _view_from_readiness(
            "American Express",
            _readiness("amex", READY),
        )
        _, rendered = _render([_status_from_view(view)])
        assert "✓ Watching" in rendered
        assert "Current activity" in rendered
        assert "No action required" in rendered
        assert user_copy.TOWER_MEANING_WATCHING in rendered

    def test_sign_in_card(self):
        view = _view_from_readiness(
            "United",
            _readiness("united", SIGNED_OUT, session_state="signed_out"),
            user_action_text="Sign in",
            user_action_url="https://example.com",
        )
        _, rendered = _render([_status_from_view(view, canonical="needs_login")])
        assert "⚠ Sign in required" in rendered
        assert "Waiting for you" in rendered

    def test_waiting_first_verification_card(self):
        view = _view_from_readiness(
            "Hilton",
            _readiness("hilton", UNVERIFIED),
        )
        assert view.background_work == BG_AWAITING_FIRST or view.live_access == LIVE_UNKNOWN
        _, rendered = _render([_status_from_view(view, canonical="unverified")])
        assert "Waiting for first verification" in rendered
        assert user_copy.TOWER_MEANING_WAITING_FIRST in rendered


class TestControlTowerSummaryBuckets:
    def test_summary_watching_working_needs_you(self):
        accounts = [
            _status_from_view(_view_from_readiness("American Express", _readiness("amex", READY))),
            _status_from_view(
                _view_from_readiness(
                    "Delta",
                    _readiness("delta", CHECKING, session_state="checking"),
                    verification_lifecycle="visiting",
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
        _, rendered = _render(accounts)
        assert "Summary" in rendered
        assert "Watching" in rendered
        assert "Working" in rendered
        assert "Needs you" in rendered
        assert "American Express" in rendered
        assert "United" in rendered
        assert "Sign in required" in rendered
