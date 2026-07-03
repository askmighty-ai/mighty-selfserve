"""Tests for the benefit-driven recommendation advisor."""

from mighty.advisors.benefit_advisor import evaluate
from mighty.decision_engine import DecisionContext


def _ctx():
    return DecisionContext(url="", source="dashboard")


def _memory(**kwargs):
    base = {
        "available_benefits": kwargs.pop("benefits", []),
        "intent": kwargs.pop("intent", {}),
    }
    base.update(kwargs)
    return base


def test_no_benefits_returns_empty():
    assert evaluate(_ctx(), _memory()) == []


def test_marriott_points_free_night_without_invented_destination():
    recs = evaluate(
        _ctx(),
        _memory(
            benefits=[
                {
                    "label": "Bonvoy Points",
                    "value": "85,000 points",
                    "source": "Marriott Bonvoy",
                    "btype": "points_balance",
                }
            ],
            intent={"hotel": 3},
        ),
    )
    assert len(recs) == 1
    assert recs[0].id == "benefit_points_marriott"
    assert "free night" in recs[0].title
    assert "Boston" not in recs[0].title
    assert recs[0].rationale
    assert recs[0].confidence in {"high", "medium", "low"}
    assert recs[0].score > 0


def test_marriott_points_uses_email_destination():
    recs = evaluate(
        _ctx(),
        _memory(
            benefits=[
                {
                    "label": "Bonvoy Points",
                    "value": "50,000",
                    "source": "marriott",
                    "btype": "points_balance",
                }
            ],
            email_subjects=["Your upcoming trip to Seattle"],
        ),
    )
    assert len(recs) == 1
    assert "Seattle" in recs[0].title


def test_united_silver_status_retention():
    recs = evaluate(
        _ctx(),
        _memory(
            benefits=[
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
            intent={"flight": 2},
        ),
    )
    assert len(recs) == 1
    assert recs[0].id == "benefit_status_united"
    assert recs[0].title == "One more round trip keeps your United Silver status."
    assert "lounge access" in recs[0].rationale.lower()
    assert recs[0].bullets


def test_certificate_expiring_soon_is_high_urgency():
    recs = evaluate(
        _ctx(),
        _memory(
            benefits=[
                {
                    "label": "Free Night Award",
                    "value": "1 certificate",
                    "source": "Marriott Bonvoy",
                    "btype": "certificate",
                    "days_left": 14,
                }
            ],
        ),
    )
    assert len(recs) == 1
    assert "expires in 14 days" in recs[0].title
    assert recs[0].score >= 35


def test_progress_not_near_goal_is_skipped():
    recs = evaluate(
        _ctx(),
        _memory(
            benefits=[
                {
                    "label": "Qualifying Segments",
                    "value": "2 of 10",
                    "source": "United",
                    "btype": "progress_toward",
                }
            ],
        ),
    )
    assert recs == []


def test_insufficient_points_skipped():
    recs = evaluate(
        _ctx(),
        _memory(
            benefits=[
                {
                    "label": "Bonvoy Points",
                    "value": "5,000 points",
                    "source": "Marriott Bonvoy",
                    "btype": "points_balance",
                }
            ],
        ),
    )
    assert recs == []


def test_recommendations_sorted_by_score():
    recs = evaluate(
        _ctx(),
        _memory(
            benefits=[
                {
                    "label": "Bonvoy Points",
                    "value": "40,000 points",
                    "source": "Marriott Bonvoy",
                    "btype": "points_balance",
                },
                {
                    "label": "Free Night Award",
                    "value": "1 certificate",
                    "source": "Marriott Bonvoy",
                    "btype": "certificate",
                    "days_left": 7,
                },
            ],
            intent={"hotel": 5},
        ),
    )
    assert len(recs) == 2
    assert recs[0].score >= recs[1].score
