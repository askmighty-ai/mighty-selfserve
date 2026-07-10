"""Tests for Home state resolution."""

from mighty.account_status import AccountStatus
from mighty.action import Action, ActionCategory, ActionPriority
from mighty.home_state import HomeState, resolve_home_state


def _acct(
    source: str,
    display_name: str,
    status: str,
    *,
    action_url: str = "https://example.com/login",
) -> AccountStatus:
    presentation_key = {
        "needs_login": "needs_sign_in",
        "updating": "updating",
        "checking": "checking",
        "up_to_date": "ready",
        "waiting_for_extension": "updating",
        "error": "needs_attention",
    }.get(status, "ready")
    return AccountStatus(
        source=source,
        display_name=display_name,
        status=status,
        presentation_key=presentation_key,
        presentation_label=presentation_key.replace("_", " ").title(),
        last_successful_sync_at=None,
        current_attempt_at=None,
        last_error=None,
        user_action_label=f"Log in to {display_name}",
        user_action_url=action_url,
    )


class TestHomeStatePriority:
    def test_empty_when_no_accounts(self):
        result = resolve_home_state(accounts=[])
        assert result.state == HomeState.EMPTY
        assert result.show_health is False
        assert result.featured.cta_url == "/email-scan"

    def test_login_wins_while_updating(self):
        accounts = [
            _acct("amex", "American Express", "needs_login"),
        ]
        result = resolve_home_state(
            accounts=accounts,
            sync_running=True,
            updating_source="amex",
            updating_display_name="American Express",
        )
        assert result.state == HomeState.LOGIN
        assert result.featured.cta_label == "Log in to American Express"
        assert result.updating_display_name == "American Express"

    def test_update_when_no_blockers(self):
        accounts = [
            _acct("amex", "American Express", "up_to_date"),
        ]
        result = resolve_home_state(
            accounts=accounts,
            sync_running=True,
            updating_source="amex",
            updating_display_name="American Express",
        )
        assert result.state == HomeState.UPDATE
        assert result.featured.disabled_cta_label
        assert "American Express" in result.featured.headline

    def test_login_when_session_blocked(self):
        accounts = [
            _acct("amex", "American Express", "needs_login"),
            _acct("delta", "Delta", "up_to_date"),
        ]
        result = resolve_home_state(accounts=accounts)
        assert result.state == HomeState.LOGIN
        assert result.health.needs_login == 1
        assert result.health.up_to_date == 1
        assert result.featured.cta_label == "Log in to American Express"

    def test_waiting_when_no_fresh_data(self):
        accounts = [
            _acct("amex", "American Express", "waiting_for_extension"),
        ]
        result = resolve_home_state(accounts=accounts)
        assert result.state == HomeState.WAITING
        assert result.waiting_rows
        assert "Open American Express" in result.featured.cta_label

    def test_recommendation_when_urgent_benefit(self):
        accounts = [_acct("amex", "American Express", "up_to_date")]
        actions = [
            Action(
                title="Use your $40 dining credit before Friday",
                summary="Platinum card",
                priority=ActionPriority.URGENT,
                category=ActionCategory.EXPIRING_BENEFIT,
                recommended_next_step="View Amex offers",
                action_url="https://americanexpress.com/",
                display_name="American Express",
            )
        ]
        result = resolve_home_state(accounts=accounts, actions=actions)
        assert result.state == HomeState.RECOMMENDATION
        assert result.featured.headline == actions[0].title

    def test_all_clear_when_healthy(self):
        accounts = [
            _acct("amex", "American Express", "up_to_date"),
            _acct("delta", "Delta", "up_to_date"),
        ]
        result = resolve_home_state(accounts=accounts, actions=[])
        assert result.state == HomeState.ALL_CLEAR
        assert result.health.up_to_date == 2
