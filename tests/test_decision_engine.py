"""Tests for dashboard recommendation integration."""

from mighty.decision_engine import (
    DecisionContext,
    get_recommendations,
    recommendation_contract_violations,
)
from mighty.demo_mode import get_demo_recommendations


def _assert_complete_recommendation(rec) -> None:
    missing = recommendation_contract_violations(rec)
    assert not missing, f"{getattr(rec, 'title', rec)} missing: {missing}"


def _dashboard_ctx(*, subjects=None):
    metadata = {}
    if subjects is not None:
        metadata["email_subjects"] = subjects
    return DecisionContext(url="", source="dashboard", metadata=metadata)


def test_dashboard_falls_back_to_demo_without_subjects():
    recs = get_recommendations(_dashboard_ctx(subjects=[]))
    assert len(recs) == 3
    assert recs[0].rationale == "Demo recommendation."
    for rec in recs:
        _assert_complete_recommendation(rec)


def test_dashboard_appends_email_advisor_when_subjects_match():
    recs = get_recommendations(
        _dashboard_ctx(subjects=["World of Hyatt: 2x points this week"]),
        user_memory={"email_subjects": ["World of Hyatt: 2x points this week"]},
    )
    assert len(recs) == 4
    assert recs[0].rationale == "Demo recommendation."
    assert recs[-1].title == "Review your Hyatt emails"
    assert recs[-1].recommendation_type == "hotel"
    assert recs[-1].rationale != "Demo recommendation."
    assert recs[-1].evidence == ["Email subject: World of Hyatt: 2x points this week"]
    for rec in recs:
        _assert_complete_recommendation(rec)


def test_dashboard_accepts_subjects_from_user_memory_only():
    ctx = DecisionContext(url="", source="dashboard", metadata={})
    recs = get_recommendations(
        ctx,
        user_memory={"email_subjects": ["Marriott Bonvoy offer inside"]},
    )
    assert len(recs) == 4
    assert recs[0].rationale == "Demo recommendation."
    assert recs[-1].title == "Review your Marriott emails"
    for rec in recs:
        _assert_complete_recommendation(rec)


def test_demo_mode_recommendations_satisfy_contract():
    for rec in get_demo_recommendations():
        _assert_complete_recommendation(rec)


def test_hotel_advisor_recommendations_satisfy_contract():
    ctx = DecisionContext(
        url="https://example.com/hotel",
        source="browser",
        user_intent="hotel_booking",
        metadata={
            "available_benefits": [{"label": "Fine Hotels + Resorts"}],
        },
    )
    recs = get_recommendations(ctx)
    assert len(recs) == 1
    _assert_complete_recommendation(recs[0])


def test_email_advisor_recommendations_satisfy_contract():
    recs = get_recommendations(
        _dashboard_ctx(subjects=["Chase Ultimate Rewards: 80k bonus offer"]),
    )
    live = [rec for rec in recs if rec.rationale != "Demo recommendation."]
    assert len(live) == 1
    assert live[0].title == "Review your Chase emails"
    assert live[0].evidence == ["Email subject: Chase Ultimate Rewards: 80k bonus offer"]
    _assert_complete_recommendation(live[0])
