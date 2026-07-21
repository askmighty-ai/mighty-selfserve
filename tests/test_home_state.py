"""Tests for Home state preparation (capability / enrollment — not attention)."""

from mighty.account_status import AccountStatus
from mighty.action import Action, ActionCategory, ActionPriority
from mighty.capability_state import CapabilityState
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


class TestHomeStateNoAttentionRanking:
    def test_empty_when_no_accounts(self):
        result = resolve_home_state(accounts=[])
        assert result.state == HomeState.EMPTY
        assert result.show_health is False
        assert result.capability is not None
        assert result.capability.state == CapabilityState.LOGIN_UNKNOWN

    def test_signed_out_builds_capability_without_login_ranking(self):
        accounts = [
            _acct("amex", "American Express", "needs_login"),
        ]
        result = resolve_home_state(
            accounts=accounts,
            sync_running=True,
            updating_source="amex",
            updating_display_name="American Express",
        )
        # AttentionView owns login interrupt; HomeState must not select LOGIN.
        assert result.state != HomeState.LOGIN
        assert result.capability.state == CapabilityState.SIGNED_OUT
        assert result.capability.action_required is True

    def test_update_when_refreshing_healthy_account(self):
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
        assert result.show_health is False
        assert result.secondary_recommendations == []

    def test_waiting_when_no_fresh_data(self):
        accounts = [
            _acct("amex", "American Express", "waiting_for_extension"),
        ]
        result = resolve_home_state(accounts=accounts)
        assert result.state == HomeState.WAITING
        assert result.waiting_rows
        assert "Open American Express" in (result.featured.cta_label or "")

    def test_actions_do_not_produce_recommendation_ranking(self):
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
        assert result.state != HomeState.RECOMMENDATION
        assert result.state == HomeState.ALL_CLEAR
        assert result.secondary_recommendations == []

    def test_all_clear_when_healthy(self):
        accounts = [
            _acct("amex", "American Express", "up_to_date"),
            _acct("delta", "Delta", "up_to_date"),
        ]
        result = resolve_home_state(accounts=accounts, actions=[])
        assert result.state == HomeState.ALL_CLEAR
        assert result.health.up_to_date == 2
        assert result.show_health is False

    def test_health_buckets_still_counted(self):
        accounts = [
            _acct("amex", "American Express", "up_to_date"),
            _acct("hilton", "Hilton", "waiting_for_extension"),
            _acct("delta", "Delta", "error"),
            _acct("chase", "Chase", "needs_login"),
        ]
        result = resolve_home_state(accounts=accounts, actions=[])
        assert result.health.up_to_date == 1
        assert result.health.waiting == 1
        assert result.health.needs_attention == 1
        assert result.health.needs_login == 1
        assert result.state != HomeState.LOGIN
        assert result.state != HomeState.RECOMMENDATION

    def test_amex_only_access_views_for_truth(self):
        accounts = [
            _acct("amex", "American Express", "needs_login"),
            _acct("delta", "Delta", "up_to_date"),
        ]
        result = resolve_home_state(accounts=accounts)
        assert all(v.provider == "amex" for v in result.access_views)
        assert result.capability.state == CapabilityState.SIGNED_OUT

    def test_setup_states_never_counted_as_needs_login(self):
        for status in ("checking", "waiting_for_extension", "updating"):
            accounts = [_acct("amex", "American Express", status)]
            result = resolve_home_state(accounts=accounts)
            assert result.health.needs_login == 0
            assert result.health.waiting == 1
            assert result.state != HomeState.LOGIN
