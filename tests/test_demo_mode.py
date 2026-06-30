"""Tests for mighty.demo_mode — dashboard demo data and toggles."""

import os
from unittest.mock import MagicMock

import pytest

from mighty.demo_mode import (
    account_count,
    expiring_count,
    get_demo_daily_brief,
    get_demo_recommendations,
    get_demo_reminders,
    get_demo_reminders_summary,
    handle_demo_query_param,
    is_demo_mode_enabled,
    render_demo_account_cards,
    render_demo_benefits_row,
    render_demo_daily_brief_hero,
    render_demo_recommendations,
    set_demo_mode,
)


class TestDemoModeToggle:
    def test_env_var_enables_demo(self, monkeypatch):
        monkeypatch.setenv("DEMO_MODE", "true")
        assert is_demo_mode_enabled() is True

    def test_session_enables_demo(self):
        session = {}
        set_demo_mode(session, True)
        assert is_demo_mode_enabled(session=session) is True

    def test_query_param_sets_session(self):
        session = {}
        req = MagicMock()
        req.args = {"demo": "1"}
        assert handle_demo_query_param(req, session) is True
        assert session.get("demo_mode") is True

    def test_query_param_disables_session(self):
        session = {"demo_mode": True}
        req = MagicMock()
        req.args = {"demo": "0"}
        assert handle_demo_query_param(req, session) is False
        assert "demo_mode" not in session


class TestDemoData:
    def test_daily_brief_has_coherent_story(self):
        brief = get_demo_daily_brief()
        assert "Tokyo" in brief.headline
        assert len(brief.attention) >= 1
        assert len(brief.discoveries) >= 2
        assert len(brief.insights) >= 3

    def test_recommendations_are_not_hidden_demo_fallback(self):
        recs = get_demo_recommendations()
        assert len(recs) == 3
        for rec in recs:
            assert rec.rationale.lower() != "demo recommendation."

    def test_reminders_include_expiring_and_credits(self):
        reminders = get_demo_reminders()
        urgencies = {r["urgency"] for r in reminders}
        assert "urgent" in urgencies
        assert "soon" in urgencies
        assert "info" in urgencies

    def test_account_count_matches_cards(self):
        assert account_count() == 5
        html = render_demo_account_cards()
        assert "Marriott Bonvoy" in html
        assert "Delta SkyMiles" in html
        assert "Demo account" in html

    def test_expiring_count(self):
        assert expiring_count() == 3


class TestDemoRendering:
    def test_daily_brief_renders_demo_tag(self):
        brief = get_demo_daily_brief()
        html = render_demo_daily_brief_hero(brief, "Alex", "Monday, June 30")
        assert "Demo data" in html
        assert "Tokyo" in html

    def test_recommendations_render(self):
        html = render_demo_recommendations()
        assert "Recommendations" in html
        assert "Park Hyatt Tokyo" in html

    def test_benefits_row_renders(self):
        html = render_demo_benefits_row()
        assert "Benefits available now" in html
        assert "Free Night Certificate" in html

    def test_reminders_summary(self):
        summary = get_demo_reminders_summary()
        assert summary["total"] >= 5
        assert summary["urgent"] >= 1
        assert len(summary["themes"]) >= 1
