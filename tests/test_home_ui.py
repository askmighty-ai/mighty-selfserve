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

    def test_all_clear_shows_health_strip(self):
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
        assert "Account health" in rendered
        assert "1 up to date" in rendered
        assert "need login" not in rendered.lower()
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

    def test_health_strip_needs_attention_copy(self):
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
        assert "1 needs attention" in rendered
        assert 'filter=needs_attention' in rendered
        assert "need login" not in rendered.lower()
        assert "Dismiss for now" not in rendered

    def test_mixed_setup_all_clear_copy(self):
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
        assert "You&#x27;re all set for now." in rendered or "You're all set for now." in rendered
        assert "Nothing needs your attention right now." in rendered
        assert "still setting up 2 accounts" in rendered
        assert "2 still setting up" in rendered
        assert "watching" not in rendered.lower()
        assert "Log in" not in rendered

    def test_hero_agrees_with_health_when_attention_required(self):
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
        assert "One account needs your attention." in rendered
        assert "1 needs attention" in rendered
        assert "Nothing needs your attention" not in rendered
        assert "still setting up 1 account" in rendered
        assert "1 still setting up" in rendered

    def test_zero_attention_hero_says_nothing_needs_attention(self):
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
        assert "Nothing needs your attention right now." in rendered
        assert "1 needs attention" not in rendered
        assert "needs attention" not in rendered.replace(
            "Nothing needs your attention right now.", ""
        )

    def test_all_up_to_date_monitoring_copy(self):
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
        assert "You&#x27;re all set." in rendered or "You're all set." in rendered
        assert "all set for now" not in rendered
        assert "monitoring all 2 accounts" in rendered
        assert "watching" not in rendered.lower()
