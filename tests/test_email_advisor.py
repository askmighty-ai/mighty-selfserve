"""Tests for the email subject advisor."""

from mighty.decision_engine import DecisionContext
from mighty.advisors.email_advisor import evaluate


def _ctx(*, source="email", subjects=None):
    metadata = {}
    if subjects is not None:
        metadata["email_subjects"] = subjects
    return DecisionContext(url="", source=source, metadata=metadata)


def test_no_subjects_returns_empty():
    assert evaluate(_ctx(subjects=[])) == []


def test_unrelated_subjects_return_empty():
    assert evaluate(_ctx(subjects=["Your weekly newsletter", "Password reset"])) == []


def test_brand_only_subject_returns_empty():
    """Generic brand mentions without actionable signals are skipped."""
    assert evaluate(_ctx(subjects=["Hello from Hyatt"])) == []


def test_hyatt_promo_subject_returns_actionable_recommendation():
    recs = evaluate(_ctx(subjects=["World of Hyatt: 2x points this week"]))
    assert len(recs) == 1
    assert recs[0].id == "email_hyatt"
    assert "2x" in recs[0].title.lower() or "promo" in recs[0].title.lower()
    assert recs[0].action_url == "https://www.hyatt.com/"
    assert "World of Hyatt: 2x points" in recs[0].rationale


def test_multiple_brands_deduplicated():
    recs = evaluate(_ctx(subjects=[
        "Marriott Bonvoy offer inside",
        "Your Delta SkyMiles statement",
        "Another Marriott promotion",
    ]))
    ids = {r.id for r in recs}
    assert ids == {"email_marriott", "email_delta"}


def test_keyword_matching_is_case_insensitive():
    recs = evaluate(_ctx(subjects=["SOUTHWEST Companion Pass update"]))
    assert len(recs) == 1
    assert recs[0].id == "email_southwest"
    assert "companion" in recs[0].title.lower()


def test_subjects_from_user_memory():
    ctx = DecisionContext(url="", source="email", metadata={})
    recs = evaluate(ctx, user_memory={"email_subjects": ["Amex Offer: $50 back"]})
    assert len(recs) == 1
    assert recs[0].id == "email_amex"


def test_chase_subject_with_offer_returns_recommendation():
    recs = evaluate(_ctx(subjects=["Chase Ultimate Rewards: 80k bonus offer"]))
    assert len(recs) == 1
    assert recs[0].id == "email_chase"
    assert recs[0].rationale


def test_metadata_subjects_work_for_non_email_source():
    ctx = DecisionContext(
        url="https://example.com",
        source="browser",
        metadata={"email_subjects": ["Hilton Honors weekend sale"]},
    )
    recs = evaluate(ctx)
    assert len(recs) == 1
    assert recs[0].id == "email_hilton"


def test_browser_source_without_subjects_returns_empty():
    ctx = DecisionContext(url="https://example.com", source="browser", metadata={})
    assert evaluate(ctx) == []


def test_expiration_subject_is_actionable():
    recs = evaluate(_ctx(subjects=["Your Marriott free night award expires soon"]))
    assert len(recs) == 1
    title_lc = recs[0].title.lower()
    assert any(k in title_lc for k in ("expir", "before", "redeem", "free night"))
    assert recs[0].rationale
