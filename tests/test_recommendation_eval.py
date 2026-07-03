"""Tests for recommendation engine evaluation (admin comparison)."""

from mighty.recommendation_eval import evaluate_accounts


def _memory(**kwargs):
    base = {
        "available_benefits": kwargs.pop("benefits", []),
        "intent": kwargs.pop("intent", {}),
        "suppress_demo_content": True,
    }
    base.update(kwargs)
    return base


def test_evaluate_marriott_account_benefit_advisor():
    accounts = [{"source": "marriott", "display_name": "Marriott Bonvoy"}]
    memory = _memory(
        benefits=[
            {
                "label": "Bonvoy Points",
                "value": "85,000 points",
                "source": "Marriott Bonvoy",
                "btype": "points_balance",
            }
        ],
        intent={"hotel": 3},
    )
    results = evaluate_accounts(accounts, memory)
    assert len(results) == 1
    row = results[0]
    assert not row.benefit_advisor.empty
    assert "free night" in row.benefit_advisor.title.lower()
    assert row.benefit_advisor.score is not None
    assert row.benefit_advisor.confidence in {"high", "medium", "low"}
    assert row.benefit_advisor.rationale
    assert row.benefit_advisor.urgency in {"urgent", "soon", "info"}


def test_evaluate_current_engine_includes_benefit_rec():
    accounts = [{"source": "marriott", "display_name": "Marriott Bonvoy"}]
    memory = _memory(
        benefits=[
            {
                "label": "Bonvoy Points",
                "value": "85,000 points",
                "source": "Marriott Bonvoy",
                "btype": "points_balance",
            }
        ],
        intent={"hotel": 3},
    )
    results = evaluate_accounts(accounts, memory)
    assert not results[0].current_engine.empty
    assert results[0].current_engine.title == results[0].benefit_advisor.title


def test_evaluate_generic_email_recommendation():
    accounts = [{"source": "hyatt", "display_name": "World of Hyatt"}]
    memory = _memory(
        benefits=[],
        email_subjects=["Your World of Hyatt free night certificate expires soon"],
    )
    results = evaluate_accounts(accounts, memory, email_subjects=memory["email_subjects"])
    assert not results[0].generic_recommendation.empty
    assert "hyatt" in results[0].generic_recommendation.title.lower()
    assert results[0].benefit_advisor.empty


def test_evaluate_empty_account():
    accounts = [{"source": "delta", "display_name": "Delta SkyMiles"}]
    memory = _memory(benefits=[], email_subjects=[])
    results = evaluate_accounts(accounts, memory)
    assert results[0].current_engine.empty
    assert results[0].benefit_advisor.empty
    assert results[0].generic_recommendation.empty
