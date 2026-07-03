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


def test_dashboard_uses_actionable_email_not_generic():
    recs = get_recommendations(
        _dashboard_ctx(subjects=["World of Hyatt: 2x points this week"]),
        user_memory={"email_subjects": ["World of Hyatt: 2x points this week"]},
    )
    assert len(recs) == 1
    assert recs[0].id == "email_hyatt"
    assert "Review your" not in recs[0].title
    assert recs[0].rationale
    assert "2x" in recs[0].rationale or "promo" in recs[0].title.lower()


def test_dashboard_skips_demo_when_live_recommendations_exist():
    recs = get_recommendations(
        _dashboard_ctx(subjects=["World of Hyatt: 2x points this week"]),
        user_memory={"email_subjects": ["World of Hyatt: 2x points this week"]},
    )
    assert not any(r.rationale == "Demo recommendation." for r in recs)


def test_dashboard_accepts_subjects_from_user_memory_only():
    ctx = DecisionContext(url="", source="dashboard", metadata={})
    recs = get_recommendations(
        ctx,
        user_memory={"email_subjects": ["Marriott Bonvoy offer inside"]},
    )
    assert len(recs) == 1
    assert recs[0].id == "email_marriott"
    assert "Review your" not in recs[0].title


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
            summary="Has rationale.",
            rationale="Based on synced Marriott points.",
            score=40,
            recommendation_type="hotel",
            action_label="Book",
        ),
        Recommendation(
            id="benefit_points_marriott",
            title="Duplicate",
            summary="Has rationale.",
            rationale="Based on synced Marriott points.",
            score=80,
            recommendation_type="hotel",
            action_label="Book",
        ),
        Recommendation(
            id="benefit_cert_marriott",
            title="Certificate",
            summary="Has rationale.",
            rationale="Certificate expiring soon.",
            score=70,
            recommendation_type="hotel",
            action_label="Redeem",
        ),
    ])
    assert len(recs) == 2
    assert {r.id for r in recs} == {"benefit_points_marriott", "benefit_cert_marriott"}


def test_dedupe_filters_generic_review_recommendations():
    from mighty.decision_engine import _dedupe_and_rank

    recs = _dedupe_and_rank([
        Recommendation(
            id="email_hyatt",
            title="Review your Hyatt emails",
            summary="Generic.",
            rationale="A recent email subject mentioned Hyatt.",
            action_label="Open",
        ),
    ])
    assert recs == []


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


def test_cross_account_chase_hyatt_transfer():
    recs = get_recommendations(
        _dashboard_ctx(),
        user_memory={
            "suppress_demo_content": True,
            "available_benefits": [
                {
                    "label": "Ultimate Rewards",
                    "value": "120,000 points",
                    "source": "Chase Sapphire",
                    "btype": "points_balance",
                },
                {
                    "label": "World of Hyatt Points",
                    "value": "15,000 points",
                    "source": "Hyatt",
                    "btype": "points_balance",
                },
            ],
            "intent": {"hotel": 4},
        },
    )
    titles = [r.title for r in recs]
    assert any("transfer" in t.lower() and "hyatt" in t.lower() for t in titles)
    cross = next(r for r in recs if r.id == "cross_chase_hyatt_transfer")
    assert cross.rationale
    assert cross.action_label


def test_every_recommendation_has_rationale():
    recs = get_recommendations(
        _dashboard_ctx(subjects=["Delta SkyMiles bonus offer"]),
        user_memory={
            "email_subjects": ["Delta SkyMiles bonus offer"],
            "available_benefits": [
                {
                    "label": "SkyMiles",
                    "value": "45,000 miles",
                    "source": "Delta",
                    "btype": "points_balance",
                }
            ],
            "intent": {"flight": 2},
        },
    )
    assert recs
    for rec in recs:
        assert rec.rationale.strip()
        assert rec.rationale != rec.summary or "email" in rec.rationale.lower() or "synced" in rec.rationale.lower()
