"""Tests for dashboard recommendation integration."""

from mighty.decision_engine import DecisionContext, get_recommendations


def _dashboard_ctx(*, subjects=None):
    metadata = {}
    if subjects is not None:
        metadata["email_subjects"] = subjects
    return DecisionContext(url="", source="dashboard", metadata=metadata)


def test_dashboard_falls_back_to_demo_without_subjects():
    recs = get_recommendations(_dashboard_ctx(subjects=[]))
    assert len(recs) == 3
    assert recs[0].rationale == "Demo recommendation."


def test_dashboard_uses_email_advisor_when_subjects_match():
    recs = get_recommendations(
        _dashboard_ctx(subjects=["World of Hyatt: 2x points this week"]),
        user_memory={"email_subjects": ["World of Hyatt: 2x points this week"]},
    )
    assert len(recs) == 1
    assert recs[0].title == "Review your Hyatt emails"
    assert recs[0].recommendation_type == "hotel"
    assert recs[0].rationale != "Demo recommendation."


def test_dashboard_accepts_subjects_from_user_memory_only():
    ctx = DecisionContext(url="", source="dashboard", metadata={})
    recs = get_recommendations(
        ctx,
        user_memory={"email_subjects": ["Marriott Bonvoy offer inside"]},
    )
    assert len(recs) == 1
    assert recs[0].title == "Review your Marriott emails"
