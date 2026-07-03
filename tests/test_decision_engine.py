"""Tests for recommendation ranking, deduplication, and integration."""

from mighty.decision_engine import (
    DEFAULT_MAX_RECOMMENDATIONS,
    DecisionContext,
    Recommendation,
    get_recommendations,
)


def _dashboard_ctx(*, subjects=None):
    metadata = {}
    if subjects is not None:
        metadata["email_subjects"] = subjects
    return DecisionContext(url="", source="dashboard", metadata=metadata)


def test_dashboard_falls_back_to_demo_without_subjects():
    recs = get_recommendations(_dashboard_ctx(subjects=[]))
    assert len(recs) == 3
    assert recs[0].rationale == "Demo recommendation."
    assert recs[0].score > 0
    assert recs[0].urgency in {"urgent", "soon", "info"}


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


def test_dashboard_accepts_subjects_from_user_memory_only():
    ctx = DecisionContext(url="", source="dashboard", metadata={})
    recs = get_recommendations(
        ctx,
        user_memory={"email_subjects": ["Marriott Bonvoy offer inside"]},
    )
    assert len(recs) == 4
    assert recs[0].rationale == "Demo recommendation."
    assert recs[-1].title == "Review your Marriott emails"


def test_benefit_recommendations_replace_email_duplicates():
    recs = get_recommendations(
        _dashboard_ctx(subjects=["Marriott Bonvoy offer inside"]),
        user_memory={
            "suppress_demo_content": True,
            "email_subjects": ["Marriott Bonvoy offer inside"],
            "available_benefits": [
                {
                    "label": "Bonvoy Points",
                    "value": "85,000 points",
                    "source": "Marriott Bonvoy",
                    "btype": "points_balance",
                }
            ],
            "intent": {"hotel": 3},
        },
    )
    titles = [r.title for r in recs]
    assert any("free night" in t for t in titles)
    assert not any("Review your Marriott emails" in t for t in titles)


def test_benefit_recommendations_ranked_by_urgency():
    recs = get_recommendations(
        _dashboard_ctx(),
        user_memory={
            "suppress_demo_content": True,
            "available_benefits": [
                {
                    "label": "Bonvoy Points",
                    "value": "85,000 points",
                    "source": "Marriott Bonvoy",
                    "btype": "points_balance",
                },
                {
                    "label": "Free Night Award",
                    "value": "1 certificate",
                    "source": "Marriott Bonvoy",
                    "btype": "certificate",
                    "days_left": 5,
                },
            ],
            "intent": {"hotel": 5},
        },
    )
    assert len(recs) == 2
    assert recs[0].score >= recs[1].score
    assert any("expires" in r.title.lower() for r in recs)
    assert any("free night" in r.title.lower() for r in recs)


def test_dedupe_and_rank_keeps_highest_score_per_id():
    from mighty.decision_engine import _dedupe_and_rank

    recs = _dedupe_and_rank([
        Recommendation(
            id="benefit_points_marriott",
            title="First",
            summary="",
            score=40,
            recommendation_type="hotel",
        ),
        Recommendation(
            id="benefit_points_marriott",
            title="Duplicate",
            summary="",
            score=80,
            recommendation_type="hotel",
        ),
        Recommendation(
            id="benefit_cert_marriott",
            title="Certificate",
            summary="",
            score=70,
            recommendation_type="hotel",
        ),
    ])
    assert len(recs) == 2
    assert {r.id for r in recs} == {"benefit_points_marriott", "benefit_cert_marriott"}
    assert recs[0].title == "Duplicate"


def test_dedupe_suppresses_demo_when_live_benefit_covers_program():
    recs = get_recommendations(
        _dashboard_ctx(),
        user_memory={
            "available_benefits": [
                {
                    "label": "World of Hyatt Points",
                    "value": "30,000 points",
                    "source": "Hyatt",
                    "btype": "points_balance",
                }
            ],
            "intent": {"hotel": 3},
        },
    )
    titles = [r.title for r in recs]
    assert any("Hyatt" in t for t in titles)
    assert not any("Transfer Chase Ultimate Rewards to Hyatt" in t for t in titles)


def test_dedupe_collapses_same_opportunity_slot():
    from mighty.decision_engine import _dedupe_and_rank

    recs = _dedupe_and_rank([
        Recommendation(
            id="benefit_points_marriott",
            title="You have enough Marriott Bonvoy points for a free night.",
            summary="",
            score=50,
            recommendation_type="hotel",
        ),
        Recommendation(
            id="email_marriott",
            title="Review your Marriott emails",
            summary="",
            score=30,
            recommendation_type="hotel",
        ),
    ])
    assert len(recs) == 1
    assert recs[0].id == "benefit_points_marriott"


def test_output_cap_limits_recommendations():
    from mighty.decision_engine import _dedupe_and_rank

    recs = _dedupe_and_rank([
        Recommendation(
            id=f"benefit_points_prog{i}",
            title=f"Recommendation {i}",
            summary="",
            score=100 - i,
            recommendation_type="hotel",
        )
        for i in range(8)
    ])
    assert len(recs) == DEFAULT_MAX_RECOMMENDATIONS


def test_output_cap_is_configurable():
    recs = get_recommendations(
        _dashboard_ctx(),
        user_memory={
            "suppress_demo_content": True,
            "max_recommendations": 2,
            "available_benefits": [
                {
                    "label": "Bonvoy Points",
                    "value": "85,000 points",
                    "source": "Marriott Bonvoy",
                    "btype": "points_balance",
                },
                {
                    "label": "Free Night Award",
                    "value": "1 certificate",
                    "source": "Marriott Bonvoy",
                    "btype": "certificate",
                    "days_left": 5,
                },
                {
                    "label": "SkyMiles",
                    "value": "45,000 miles",
                    "source": "Delta",
                    "btype": "points_balance",
                },
            ],
            "intent": {"hotel": 5, "flight": 2},
        },
    )
    assert len(recs) == 2


def test_united_status_retention_via_dashboard():
    recs = get_recommendations(
        _dashboard_ctx(),
        user_memory={
            "suppress_demo_content": True,
            "available_benefits": [
                {
                    "label": "MileagePlus Status",
                    "value": "Silver",
                    "source": "United",
                    "btype": "elite_status",
                },
                {
                    "label": "Premier Qualifying Flights",
                    "value": "5 of 6",
                    "source": "United",
                    "btype": "progress_toward",
                },
            ],
            "intent": {"flight": 2},
        },
    )
    assert len(recs) == 1
    assert recs[0].title == "One more round trip keeps your United Silver status."
    assert recs[0].rationale
    assert recs[0].confidence in {"high", "medium", "low"}
