"""Tests for recommendation ranking, deduplication, and integration."""

from mighty.decision_engine import DecisionContext, Recommendation, get_recommendations


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
