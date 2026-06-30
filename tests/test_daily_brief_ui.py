"""Tests for executive Daily Brief hero UI."""

import html

from mighty.action import Action, ActionCategory, ActionPriority
from mighty.action_builders import build_dashboard_actions
from mighty.daily_brief import build_daily_brief
from mighty.daily_brief_ui import build_executive_briefing, render_executive_briefing_hero
from mighty.demo_mode import get_demo_daily_brief, render_demo_daily_brief_hero


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


class TestExecutiveBriefing:
    def test_builds_three_priority_actions_from_actions(self):
        actions = build_dashboard_actions(
            action_items=[
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
            ],
            hero_candidates=[
                (95, 14, "Marriott Bonvoy", "Free Night Certificate", "1 certificate", 14, "certificate"),
            ],
        )
        brief = build_daily_brief(actions=actions, account_count=3, benefit_count=4)
        exec_brief = build_executive_briefing(
            brief,
            account_count=3,
            benefit_count=4,
            expiring_count=2,
            use_demo_when_empty=False,
        )

        assert len(exec_brief.priority_actions) == 3
        assert exec_brief.metrics.accounts_monitored == 3
        assert exec_brief.metrics.benefits_tracked == 4
        assert exec_brief.metrics.items_needing_attention >= 1
        assert "attention today" in exec_brief.priority_summary

    def test_demo_fallback_when_empty(self):
        exec_brief = build_executive_briefing(
            None,
            account_count=0,
            benefit_count=0,
            expiring_count=0,
        )

        assert len(exec_brief.priority_actions) == 3
        assert exec_brief.show_onboard_cta is True
        assert exec_brief.is_demo is True
        assert exec_brief.metrics.accounts_monitored == 5

    def test_render_two_column_layout(self):
        exec_brief = build_executive_briefing(get_demo_daily_brief(), account_count=5, benefit_count=12, expiring_count=3)
        rendered = render_executive_briefing_hero(
            exec_brief,
            first_name="Ryan",
            today_label="Monday, June 30",
            escape=_escape,
        )

        assert "dash-brief-exec" in rendered
        assert "dash-brief-priority-item" in rendered
        assert "dash-brief-metric" in rendered
        assert "Accounts monitored" in rendered
        assert "Benefits tracked" in rendered
        assert "Total estimated value found" in rendered
        assert "Items needing attention" in rendered
        assert "hero-greeting" in rendered
        assert "Good morning" in rendered

    def test_priority_action_includes_cta_and_value(self):
        action = Action(
            title="Use Marriott free night before it expires",
            summary="Bonvoy · 14 days left",
            priority=ActionPriority.URGENT,
            category=ActionCategory.EXPIRING_BENEFIT,
            estimated_value="$400 value",
            recommended_next_step="Book now",
            action_url="https://marriott.com/",
            days_until_due=14,
            display_name="Marriott Bonvoy",
        )
        brief = build_daily_brief(actions=[action], account_count=2)
        exec_brief = build_executive_briefing(brief, account_count=2, use_demo_when_empty=False)

        assert exec_brief.priority_actions[0].cta_label == "Book now"
        assert exec_brief.priority_actions[0].value == "$400 value"
        assert exec_brief.priority_actions[0].urgency == "urgent"


class TestDemoHeroRendering:
    def test_demo_hero_renders_executive_brief(self):
        html_out = render_demo_daily_brief_hero(get_demo_daily_brief(), "Alex", "Monday, June 30")
        assert "Demo data" in html_out
        assert "dash-brief-exec" in html_out
        assert "dash-brief-metric" in html_out
