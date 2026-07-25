"""Tests for Home V1A briefing projection composition."""

from mighty.account_status import AccountStatus
from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
    REASON_LOGIN,
)
from mighty.attention_state import ATTENTION_STATE_SCHEMA_VERSION, AttentionState
from mighty.attention_view import build_attention_view
from mighty.home_projection import project_home
from mighty.home_state import HomeState, resolve_home_state


def _acct(source: str, display_name: str, status: str) -> AccountStatus:
    presentation_key = {
        "needs_login": "needs_sign_in",
        "updating": "updating",
        "up_to_date": "ready",
        "waiting_for_extension": "updating",
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
        user_action_label=None,
        user_action_url=None,
    )


def _blocker() -> AttentionItem:
    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id="att_user1_auth_blocker_amex_needs_human",
        user_id="user-1",
        attention_class=AttentionClass.AUTH_BLOCKER,
        urgency=AttentionUrgency.BLOCKER,
        provider="amex",
        fingerprint="auth:amex:needs_human",
        reason=AttentionReason(code=REASON_LOGIN),
        cta_key=AttentionCtaKey.START_PROVIDER_LOGIN,
        source_kind=AttentionSourceKind.AUTH,
        source_ref="auth_truth:user-1:amex",
        observed_at="2026-07-21T11:00:00+00:00",
        becomes_stale_at=None,
        interruption_expected=True,
    )


class TestHomeBriefingProjection:
    def test_empty_uses_enrollment_featured(self):
        result = resolve_home_state(accounts=[])
        projection = project_home(
            result, first_name="Pat", today_label="Tue", use_attention=False,
        )
        assert projection.enrollment_state == HomeState.EMPTY
        assert projection.story_kind == "empty"
        assert projection.featured is not None
        assert "watched quietly" in projection.featured.title.lower()
        assert projection.ops_notes == ()

    def test_attention_primary_is_the_story(self):
        result = resolve_home_state(accounts=[_acct("amex", "American Express", "up_to_date")])
        state = AttentionState(
            schema_version=ATTENTION_STATE_SCHEMA_VERSION,
            primary=_blocker(),
            remaining=(),
            silence=None,
        )
        attention = build_attention_view(state, surface="home")
        projection = project_home(
            result,
            first_name="Pat",
            today_label="Tue",
            attention=attention,
            use_attention=True,
        )
        assert projection.story_kind == "attention"
        assert projection.visual_state == "attention"
        assert projection.featured is not None
        assert projection.featured.attention_id == attention.primary.attention_id
        assert projection.answer == attention.primary.title
        assert projection.secondary == ()

    def test_waiting_owns_handoff_hero_when_attention_silent(self):
        result = resolve_home_state(
            accounts=[_acct("amex", "American Express", "waiting_for_extension")],
        )
        assert result.state == HomeState.WAITING
        projection = project_home(result, first_name="Pat", today_label="Tue")
        assert projection.story_kind == "handoff"
        assert projection.visual_state == "handoff"
        assert projection.featured is not None
        assert "American Express" in projection.featured.title
        assert projection.answer == projection.featured.title
        assert projection.featured.cta_label is not None
        assert "You're good." not in projection.answer
        assert projection.ops_notes == ()

    def test_healthy_projects_evidence(self):
        result = resolve_home_state(accounts=[_acct("amex", "American Express", "up_to_date")])
        projection = project_home(
            result,
            first_name="Pat",
            today_label="Tue",
            last_checked="2 minutes ago",
            gmail_connected=True,
            chrome_active=True,
        )
        assert projection.visual_state == "healthy"
        assert projection.answer == "You're good."
        labels = [item.label for item in projection.evidence]
        assert any("Watching" in label for label in labels)
        assert any("Gmail connected" == label for label in labels)
        assert any("Chrome active" == label for label in labels)
        assert any("2 minutes ago" in label for label in labels)

    def test_recent_wins_projected_without_ranking(self):
        result = resolve_home_state(accounts=[_acct("amex", "American Express", "up_to_date")])
        projection = project_home(
            result,
            first_name="Pat",
            today_label="Tue",
            recent_wins=[
                {"message": "Membership Rewards increased", "source": "amex"},
                {"message": "", "source": "delta"},
            ],
        )
        assert len(projection.recent_wins) == 1
        assert projection.recent_wins[0].message == "Membership Rewards increased"
        assert projection.activity_preview == projection.recent_wins

    def test_no_health_chip_section(self):
        result = resolve_home_state(
            accounts=[
                _acct("amex", "American Express", "up_to_date"),
                _acct("chase", "Chase", "needs_login"),
            ]
        )
        projection = project_home(result, first_name="Pat", today_label="Tue")
        assert projection.show_health is False
        assert projection.health_chips == ()
