"""Tests for mighty.daily_brief Action-based composition."""

from mighty.action_builders import build_dashboard_actions
from mighty.daily_brief import build_daily_brief
from mighty.decision_engine import Recommendation


def _sample_recommendations():
    return [
        Recommendation(
            title="Book Park Hyatt Tokyo via Amex FHR",
            summary="Platinum · Breakfast + $300 hotel credit",
            rationale="Matches your Tokyo trip.",
            recommendation_type="hotel",
            confidence="high",
        ),
        Recommendation(
            title="Demo only",
            summary="Should be filtered",
            rationale="Demo recommendation.",
            recommendation_type="hotel",
        ),
    ]


def _sample_action_items():
    return [
        {
            "source": "amex",
            "label": "Amex $40 dining offer expires soon",
            "value": "Platinum · 5 days left",
            "btype": "cash_credit",
            "urgency": "urgent",
            "days_left": 5,
        },
        {
            "source": "chase",
            "label": "Chase 5× dining multiplier ending soon",
            "value": "Sapphire Reserve · 12 days left",
            "btype": "travel_credit",
            "urgency": "soon",
            "days_left": 12,
        },
    ]


def _sample_hero_candidates():
    return [
        (95, 14, "Marriott Bonvoy", "Free Night Certificate", "1 certificate", 14, "certificate"),
        (88, 21, "Delta SkyMiles", "Regional Upgrade Certificate", "1 available", 21, "certificate"),
    ]


class TestDailyBriefFromActions:
    def test_actions_path_matches_legacy_shapes(self):
        kwargs = dict(
            account_count=5,
            expiring_count=3,
            global_sync_label="2h ago",
            action_items=_sample_action_items(),
            hero_candidates=_sample_hero_candidates(),
            email_suggestion_count=0,
            recommendations=_sample_recommendations(),
            acct_rows=[{"synced_at": "2026-06-30T12:00:00"}] * 5,
        )
        legacy = build_daily_brief(**kwargs)
        actions = build_dashboard_actions(
            action_items=kwargs["action_items"],
            hero_candidates=kwargs["hero_candidates"],
            recommendations=kwargs["recommendations"],
            email_suggestion_count=kwargs["email_suggestion_count"],
        )
        unified = build_daily_brief(actions=actions, **{k: v for k, v in kwargs.items() if k not in {
            "action_items", "hero_candidates", "email_suggestion_count", "recommendations",
        }})

        assert unified.headline == legacy.headline
        assert unified.summary == legacy.summary
        assert [item.title for item in unified.attention] == [item.title for item in legacy.attention]
        assert [item.title for item in unified.discoveries] == [item.title for item in legacy.discoveries]
        assert [item.title for item in unified.recommendations] == [item.title for item in legacy.recommendations]
        assert len(unified.actions) == len(actions)
        assert len(unified.recommendations) == 1

    def test_email_discovery_added_when_room(self):
        brief = build_daily_brief(
            account_count=2,
            action_items=[],
            hero_candidates=[],
            email_suggestion_count=3,
            recommendations=[],
        )
        assert any("email" in item.title.lower() for item in brief.discoveries)

    def test_daily_brief_exposes_actions(self):
        actions = build_dashboard_actions(action_items=_sample_action_items())
        brief = build_daily_brief(actions=actions, account_count=2)
        assert brief.actions is actions
