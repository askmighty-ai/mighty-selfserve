"""Tests for Home page rendering."""

import html

from mighty.account_status import AccountStatus
from mighty.home_state import HomeFeatured, HomeState, HomeStateResult, resolve_home_state
from mighty.home_ui import render_home_page


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


def _status(source, display_name, status, **kwargs):
    presentation_key = {
        "needs_login": "needs_sign_in",
        "up_to_date": "ready",
        "updating": "updating",
        "checking": "checking",
        "waiting_for_extension": "updating",
        "error": "needs_attention",
    }.get(status, "ready")
    defaults = dict(
        source=source,
        display_name=display_name,
        status=status,
        presentation_key=presentation_key,
        presentation_label=presentation_key.replace("_", " ").title(),
        last_successful_sync_at=None,
        current_attempt_at=None,
        last_error=None,
        user_action_label=None,
        user_action_url=None,
    )
    defaults.update(kwargs)
    return AccountStatus(**defaults)


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

    def test_all_clear_shows_summary_and_system_health(self):
        accounts = [
            _status(
                "amex",
                "American Express",
                "up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
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
        assert "Summary" in rendered
        assert "System Health" in rendered
        assert "American Express" in rendered
        assert "watching your accounts" in rendered.lower()
        assert "No action needed." in rendered
        assert "all set" not in rendered.lower()
        assert "Mighty runs in Chrome" in rendered

    def test_update_state_shows_refreshing_activity(self):
        accounts = [
            _status(
                "amex",
                "American Express",
                "up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
            )
        ]
        result = resolve_home_state(
            accounts=accounts,
            sync_running=True,
            updating_display_name="American Express",
        )
        rendered = render_home_page(result, first_name="Alex", today_label="Friday, July 3", escape=_escape)
        assert "Current activity: Refreshing account" in rendered
        assert "watching your accounts" in rendered.lower()

    def test_health_summary_needs_you(self):
        accounts = [
            _status(
                "amex",
                "American Express",
                "needs_login",
                user_action_label="Log in to American Express",
                user_action_url="https://example.com/login",
            ),
            _status(
                "delta",
                "Delta",
                "up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
            ),
        ]
        result = resolve_home_state(accounts=accounts)
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert "Needs you" in rendered
        assert "Needs your attention" in rendered or "needs your attention" in rendered.lower()
        assert "No action needed." not in rendered
        assert "Dismiss for now" not in rendered

    def test_mixed_setup_monitoring_copy(self):
        accounts = [
            _status(
                "amex",
                "American Express",
                "up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
            ),
            _status("hilton", "Hilton", "waiting_for_extension"),
            _status("united", "United", "checking"),
        ]
        result = resolve_home_state(accounts=accounts, actions=[])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert "all set" not in rendered.lower()
        assert "No action needed." in rendered
        assert "watching" in rendered.lower() or "monitoring" in rendered.lower()
        assert "Log in" not in rendered or "Log in" in (result.featured.cta_label or "")

    def test_hero_agrees_with_summary_when_attention_required(self):
        accounts = [
            _status(
                "amex",
                "American Express",
                "up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
            ),
            _status("hilton", "Hilton", "waiting_for_extension"),
            _status("delta", "Delta", "error"),
        ]
        result = resolve_home_state(accounts=accounts, actions=[])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert result.health.attention_required == 1
        assert result.tower.needs_you_count == 1
        assert "Needs your attention" in rendered or "needs your attention" in rendered.lower()
        assert "No action needed." not in rendered

    def test_zero_attention_hero_says_no_action_needed(self):
        accounts = [
            _status(
                "amex",
                "American Express",
                "up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
            ),
            _status("hilton", "Hilton", "waiting_for_extension"),
        ]
        result = resolve_home_state(accounts=accounts, actions=[])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert result.health.attention_required == 0
        assert "No action needed." in rendered
        assert "Needs your attention." not in rendered

    def test_all_up_to_date_watching_copy(self):
        accounts = [
            _status(
                "amex",
                "American Express",
                "up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
            ),
            _status(
                "delta",
                "Delta",
                "up_to_date",
                last_successful_sync_at="2026-07-03T12:00:00",
            ),
        ]
        result = resolve_home_state(accounts=accounts, actions=[])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert "watching your accounts" in rendered.lower()
        assert "all set" not in rendered.lower()
        assert "No action needed." in rendered
