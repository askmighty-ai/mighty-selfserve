"""Tests for mighty.action and dashboard action builders."""

from datetime import date, timedelta

import pytest

from mighty.action import (
    Action,
    ActionCategory,
    ActionPriority,
    CompletionState,
    parse_due_date,
    priority_from_urgency,
)
from mighty.action_builders import (
    action_from_action_item,
    action_from_hero_candidate,
    action_from_recommendation,
    attention_actions,
    build_dashboard_actions,
    discovery_actions,
    recommendation_actions,
)
from mighty.decision_engine import Recommendation
from mighty.user_copy import (
    NEEDS_LOGIN_ACTION_CTA,
    NEEDS_LOGIN_ACTION_LABEL,
    login_required_action_value,
)


class TestActionModel:
    def test_core_fields(self):
        action = Action(
            title=NEEDS_LOGIN_ACTION_LABEL,
            summary=login_required_action_value("Marriott Bonvoy"),
            priority=ActionPriority.URGENT,
            category=ActionCategory.LOGIN_ISSUE,
            estimated_value=login_required_action_value("Marriott Bonvoy"),
            due_date=date.today() + timedelta(days=14),
            days_until_due=14,
            confidence="high",
            reasoning="Session expired during update.",
            source_accounts=["marriott"],
            recommended_next_step=NEEDS_LOGIN_ACTION_CTA,
            completion_state=CompletionState.OPEN,
        )
        assert action.title == NEEDS_LOGIN_ACTION_LABEL
        assert action.category == ActionCategory.LOGIN_ISSUE
        assert action.detail_line() == login_required_action_value("Marriott Bonvoy")
        assert action.expiry_phrase() == "expires in 14 days"

    def test_recommendation_renderer_compat(self):
        action = Action(
            title="Transfer to Hyatt",
            summary="Strong value in Tokyo.",
            reasoning="Matches your trip.",
            recommended_next_step="Transfer to Hyatt",
            action_url="https://www.hyatt.com/",
            subcategory="hotel",
            bullets=["1:1 transfer"],
            confidence="high",
        )
        assert action.recommendation_type == "hotel"
        assert action.rationale == "Matches your trip."
        assert action.action_label == "Transfer to Hyatt"

    def test_demo_detection(self):
        action = Action(title="Demo", reasoning="Demo recommendation.")
        assert action.is_demo is True


class TestActionBuilders:
    def test_action_from_login_item(self):
        action = action_from_action_item(
            {
                "source": "marriott",
                "label": NEEDS_LOGIN_ACTION_LABEL,
                "value": login_required_action_value("Marriott Bonvoy"),
                "btype": "login_required",
                "urgency": "urgent",
            }
        )
        assert action.category == ActionCategory.LOGIN_ISSUE
        assert action.priority == ActionPriority.URGENT
        assert action.recommended_next_step == NEEDS_LOGIN_ACTION_CTA

    def test_action_from_hero_candidate(self):
        action = action_from_hero_candidate(
            (88, 14, "Marriott Bonvoy", "Free Night Certificate", "1 certificate", 14, "certificate")
        )
        assert action is not None
        assert action.category == ActionCategory.DISCOVERY
        assert action.score == 88
        assert action.days_until_due == 14

    def test_action_from_recommendation(self):
        rec = Recommendation(
            title="Book via Amex Travel",
            summary="Use FHR benefits.",
            rationale="Live match.",
            recommendation_type="hotel",
            confidence="high",
            action_label="Open Amex Travel",
            action_url="https://example.com",
            bullets=["Breakfast included"],
        )
        action = action_from_recommendation(rec)
        assert action.category == ActionCategory.SAVINGS_OPPORTUNITY
        assert action.subcategory == "hotel"
        assert action.is_demo is False

    def test_build_dashboard_actions_filters_demo_recommendations_for_cards(self):
        actions = build_dashboard_actions(
            action_items=[
                {
                    "source": "amex",
                    "label": "Amex Offer",
                    "value": "$40 dining",
                    "btype": "cash_credit",
                    "urgency": "urgent",
                    "days_left": 5,
                }
            ],
            hero_candidates=[
                (90, 5, "American Express", "Amex Offer", "$40 dining", 5, "cash_credit"),
            ],
            recommendations=[
                Recommendation(
                    title="Demo card",
                    summary="Demo",
                    rationale="Demo recommendation.",
                    recommendation_type="hotel",
                ),
                Recommendation(
                    title="Live card",
                    summary="Use this perk.",
                    rationale="Email advisor match.",
                    recommendation_type="hotel",
                ),
            ],
            email_suggestion_count=2,
        )
        assert len(attention_actions(actions)) >= 1
        assert len(discovery_actions(actions)) >= 1
        assert len(recommendation_actions(actions)) == 1
        assert recommendation_actions(actions)[0].title == "Live card"


class TestHelpers:
    def test_parse_due_date(self):
        assert parse_due_date("2026-08-15") == date(2026, 8, 15)
        assert parse_due_date(None) is None

    def test_priority_from_urgency(self):
        assert priority_from_urgency("urgent") == ActionPriority.URGENT
        assert priority_from_urgency("unknown") == ActionPriority.INFO
