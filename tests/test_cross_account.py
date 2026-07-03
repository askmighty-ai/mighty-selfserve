"""Tests for cross-account recommendation synthesis."""

from mighty.advisors.cross_account import synthesize_cross_account
from mighty.decision_engine import Recommendation


def _rec(**kwargs):
    defaults = {
        "title": "Test",
        "summary": "Summary",
        "rationale": "Because your accounts show this opportunity.",
        "action_label": "Go",
        "action_url": "https://example.com",
        "score": 50,
    }
    defaults.update(kwargs)
    return Recommendation(**defaults)


def test_chase_hyatt_transfer_when_both_accounts_and_hotel_intent():
    recs = synthesize_cross_account(
        [],
        {
            "available_benefits": [
                {"label": "Ultimate Rewards", "value": "80,000", "source": "Chase", "btype": "points_balance"},
                {"label": "Points", "value": "10,000", "source": "Hyatt", "btype": "points_balance"},
            ],
            "intent": {"hotel": 3},
        },
    )
    assert any(r.id == "cross_chase_hyatt_transfer" for r in recs)
    cross = next(r for r in recs if r.id == "cross_chase_hyatt_transfer")
    assert "1:1" in cross.rationale or "transfer" in cross.rationale.lower()


def test_no_transfer_without_hotel_intent():
    recs = synthesize_cross_account(
        [],
        {
            "available_benefits": [
                {"label": "Ultimate Rewards", "value": "80,000", "source": "Chase", "btype": "points_balance"},
                {"label": "Points", "value": "10,000", "source": "Hyatt", "btype": "points_balance"},
            ],
            "intent": {},
        },
    )
    assert not any(r.id == "cross_chase_hyatt_transfer" for r in recs)


def test_enriches_benefit_rec_with_email_promo():
    base = _rec(
        id="benefit_points_hyatt",
        title="You have enough World of Hyatt points for a free night in Boston.",
        rationale="Your synced balance meets the typical threshold.",
        score=60,
    )
    recs = synthesize_cross_account(
        [base],
        {
            "email_subjects": ["World of Hyatt: 2x points this week"],
            "available_benefits": [
                {"label": "Points", "value": "30,000", "source": "Hyatt", "btype": "points_balance"},
            ],
        },
    )
    enriched = next(r for r in recs if r.id == "benefit_points_hyatt")
    assert "2x points" in enriched.rationale
    assert enriched.score > base.score


def test_skips_duplicate_transfer_when_already_present():
    existing = _rec(
        id="cross_chase_hyatt_transfer",
        title="Transfer Chase points to Hyatt for your hotel booking",
        rationale="Already suggested.",
        score=80,
    )
    recs = synthesize_cross_account(
        [existing],
        {
            "available_benefits": [
                {"label": "Ultimate Rewards", "value": "80,000", "source": "Chase", "btype": "points_balance"},
                {"label": "Points", "value": "10,000", "source": "Hyatt", "btype": "points_balance"},
            ],
            "intent": {"hotel": 3},
        },
    )
    transfer_recs = [r for r in recs if "transfer" in r.title.lower() and "hyatt" in r.title.lower()]
    assert len(transfer_recs) == 1
