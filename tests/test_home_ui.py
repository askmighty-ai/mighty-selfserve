"""Tests for Home page rendering."""

import html

from mighty.account_status import AccountStatus
from mighty.home_state import HomeFeatured, HomeState, HomeStateResult, resolve_home_state
from mighty.home_ui import render_home_page


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


class TestHomeUi:
    def test_empty_state_single_primary_cta(self):
        result = resolve_home_state(accounts=[])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert "dash-brief-featured-cta" in rendered
        assert rendered.count("dash-brief-featured-cta") == 1
        assert "Connect Gmail" in rendered
        assert "acct-card" not in rendered
        assert "dash-brief-row" not in rendered

    def test_all_clear_shows_health_strip(self):
        accounts = [
            AccountStatus(
                source="amex",
                display_name="American Express",
                status="up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
                current_attempt_at=None,
                last_error=None,
                user_action_label=None,
                user_action_url=None,
            )
        ]
        result = resolve_home_state(accounts=accounts, actions=[])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            last_checked="2h ago",
            escape=_escape,
        )
        assert "Account health" in rendered
        assert "1 up to date" in rendered
        assert "all set" in rendered or "You&#x27;re all set." in rendered
        assert "Mighty runs in Chrome" in rendered

    def test_update_state_disabled_cta(self):
        result = HomeStateResult(
            state=HomeState.UPDATE,
            priority_summary="Almost there.",
            featured=HomeFeatured(
                headline="Updating American Express…",
                body="Please wait.",
                disabled_cta_label="Updating…",
            ),
            health=resolve_home_state(accounts=[]).health,
        )
        rendered = render_home_page(result, first_name="Alex", today_label="Friday, July 3", escape=_escape)
        assert "dash-brief-featured-cta--disabled" in rendered
        assert "Updating…" in rendered
