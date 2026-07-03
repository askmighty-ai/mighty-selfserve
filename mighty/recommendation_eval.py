"""
mighty.recommendation_eval
──────────────────────────
Admin-side comparison of recommendation engines per connected account.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from mighty.advisors.benefit_advisor import evaluate as evaluate_benefit_advisor
from mighty.advisors.email_advisor import evaluate as evaluate_email_advisor
from mighty.decision_engine import DecisionContext, Recommendation, get_recommendations
from mighty.scoring import urgency_from_score


_PROGRAM_ALIASES: dict[str, str] = {
    "bonvoy": "marriott",
    "world of hyatt": "hyatt",
    "ultimate rewards": "chase",
    "rapid rewards": "southwest",
    "skymiles": "delta",
    "mileageplus": "united",
    "hilton honors": "hilton",
}

_EMAIL_PROGRAM_KEYS: dict[str, str] = {
    "email_marriott": "marriott",
    "email_hyatt": "hyatt",
    "email_hilton": "hilton",
    "email_united": "united",
    "email_delta": "delta",
    "email_southwest": "southwest",
    "email_chase": "chase",
    "email_amex": "amex",
    "email_airbnb": "airbnb",
}


@dataclass
class EvaluatedRecommendation:
    title: str = "—"
    score: int | None = None
    confidence: str = "—"
    rationale: str = "—"
    urgency: str = "—"
    summary: str = ""
    id: str = ""
    empty: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AccountEvaluation:
    source: str
    display_name: str
    current_engine: EvaluatedRecommendation = field(default_factory=EvaluatedRecommendation)
    benefit_advisor: EvaluatedRecommendation = field(default_factory=EvaluatedRecommendation)
    generic_recommendation: EvaluatedRecommendation = field(default_factory=EvaluatedRecommendation)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "display_name": self.display_name,
            "current_engine": self.current_engine.to_dict(),
            "benefit_advisor": self.benefit_advisor.to_dict(),
            "generic_recommendation": self.generic_recommendation.to_dict(),
        }


def _normalize_program(text: str) -> str | None:
    combined = text.lower().replace("_", " ")
    for alias, key in sorted(_PROGRAM_ALIASES.items(), key=len, reverse=True):
        if alias in combined:
            return key
    for key in (
        "marriott", "hyatt", "hilton", "united", "delta", "southwest",
        "chase", "amex", "airbnb", "ihg", "wyndham", "alaska", "jetblue",
    ):
        if key in combined:
            return key
    return None


def _account_program(source: str, display_name: str) -> str | None:
    return _normalize_program(f"{source} {display_name}")


def _rec_program(rec: Recommendation) -> str | None:
    rec_id = (rec.id or "").lower()
    for email_id, program in _EMAIL_PROGRAM_KEYS.items():
        if email_id in rec_id or program in rec_id:
            return program
    return _normalize_program(f"{rec.id} {rec.title}")


def _rec_matches_account(rec: Recommendation, source: str, display_name: str) -> bool:
    account_program = _account_program(source, display_name)
    rec_program = _rec_program(rec)
    if rec_program and account_program:
        return rec_program == account_program

    title_lc = rec.title.lower()
    display_lc = display_name.lower().replace("_", " ")
    source_lc = source.lower().replace("_", " ")
    if display_lc and display_lc in title_lc:
        return True
    if source_lc and len(source_lc) > 3 and source_lc in title_lc:
        return True

    rec_id = (rec.id or "").lower()
    if rec_id.startswith("cross_"):
        if account_program and account_program in rec_id:
            return True
        if account_program == "chase" and "chase" in rec_id:
            return True

    return False


def _benefit_matches_account(benefit: dict[str, Any], source: str, display_name: str) -> bool:
    b_source = str(benefit.get("source") or "").strip()
    if b_source in {source, display_name}:
        return True
    benefit_program = _normalize_program(f"{b_source} {benefit.get('label', '')}")
    account_program = _account_program(source, display_name)
    return bool(benefit_program and account_program and benefit_program == account_program)


def _from_recommendation(rec: Recommendation | None) -> EvaluatedRecommendation:
    if rec is None:
        return EvaluatedRecommendation()
    score = int(rec.score) if rec.score else None
    return EvaluatedRecommendation(
        title=rec.title,
        score=score,
        confidence=rec.confidence or "—",
        rationale=rec.rationale or "—",
        urgency=rec.urgency or urgency_from_score(score or 0),
        summary=rec.summary or "",
        id=rec.id or "",
        empty=False,
    )


def _from_opportunity(opp: Any) -> EvaluatedRecommendation:
    if opp is None:
        return EvaluatedRecommendation()
    score = int(getattr(opp, "score", 0) or 0) or None
    confidence = str(getattr(opp, "confidence", "") or "—")
    return EvaluatedRecommendation(
        title=str(getattr(opp, "title", "") or "—"),
        score=score,
        confidence=confidence,
        rationale=str(getattr(opp, "rationale", "") or "—"),
        urgency=urgency_from_score(score or 0),
        summary=str(getattr(opp, "summary", "") or ""),
        id=str(getattr(opp, "id", "") or ""),
        empty=False,
    )


def _top_for_account(
    recs: list[Recommendation],
    source: str,
    display_name: str,
) -> EvaluatedRecommendation:
    matched = [r for r in recs if _rec_matches_account(r, source, display_name)]
    if not matched:
        return EvaluatedRecommendation()
    best = max(matched, key=lambda r: (r.score, r.title))
    return _from_recommendation(best)


def _top_benefit_for_account(
    opportunities: list[Any],
    source: str,
    display_name: str,
) -> EvaluatedRecommendation:
    matched = [
        opp for opp in opportunities
        if _rec_matches_account(
            Recommendation(
                id=str(getattr(opp, "id", "") or ""),
                title=str(getattr(opp, "title", "") or ""),
                summary=str(getattr(opp, "summary", "") or ""),
            ),
            source,
            display_name,
        )
    ]
    if not matched:
        return EvaluatedRecommendation()
    best = max(matched, key=lambda o: (int(getattr(o, "score", 0) or 0), getattr(o, "title", "")))
    return _from_opportunity(best)


def _top_generic_for_account(
    opportunities: list[Any],
    source: str,
    display_name: str,
) -> EvaluatedRecommendation:
    account_program = _account_program(source, display_name)
    matched: list[Any] = []
    for opp in opportunities:
        opp_id = str(getattr(opp, "id", "") or "").lower()
        opp_program = _EMAIL_PROGRAM_KEYS.get(opp_id)
        if account_program and opp_program == account_program:
            matched.append(opp)
    if not matched:
        return EvaluatedRecommendation()
    best = max(matched, key=lambda o: (int(getattr(o, "score", 0) or 0), getattr(o, "title", "")))
    return _from_opportunity(best)


def evaluate_accounts(
    accounts: list[dict[str, str]],
    user_memory: dict[str, Any],
    *,
    email_subjects: list[str] | None = None,
) -> list[AccountEvaluation]:
    """Compare recommendation engines for each connected account."""
    subjects = email_subjects if email_subjects is not None else list(user_memory.get("email_subjects") or [])
    context = DecisionContext(
        url="",
        page_title="",
        page_text="",
        source="dashboard",
        metadata={"email_subjects": subjects},
    )
    memory = dict(user_memory)
    memory["email_subjects"] = subjects
    memory.setdefault("suppress_demo_content", True)

    current_recs = get_recommendations(context, memory)
    generic_opps = evaluate_email_advisor(context, memory)

    results: list[AccountEvaluation] = []
    for account in accounts:
        source = account["source"]
        display_name = account.get("display_name") or source
        account_benefits = [
            b for b in (memory.get("available_benefits") or [])
            if isinstance(b, dict) and _benefit_matches_account(b, source, display_name)
        ]
        account_memory = {**memory, "available_benefits": account_benefits}
        account_benefit_opps = evaluate_benefit_advisor(context, account_memory)

        results.append(
            AccountEvaluation(
                source=source,
                display_name=display_name,
                current_engine=_top_for_account(current_recs, source, display_name),
                benefit_advisor=_top_benefit_for_account(account_benefit_opps, source, display_name),
                generic_recommendation=_top_generic_for_account(generic_opps, source, display_name),
            )
        )
    return results
