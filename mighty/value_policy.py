"""Pure Value Intelligence policy (Milestone 10).

Converts normalized snapshot fields into OpportunityCandidate facts.
Provider-independent: kinds key off field types + expiry + thresholds.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from mighty.classify import is_actionable, is_needs_attention
from mighty.expiry import exp_date_iso, parse_expiry_days
from mighty.scoring import score_opportunity, urgency_for_attention, urgency_from_score
from mighty.value_capability_registry import (
    KIND_DUPLICATED_BENEFIT,
    KIND_ELITE_QUALIFICATION_RISK,
    KIND_EXPIRING_CERTIFICATE,
    KIND_EXPIRING_CREDIT,
    KIND_EXPIRING_POINTS,
    KIND_PAYMENT_DUE,
    KIND_RENEWAL,
    KIND_UNUSED_BENEFIT,
    KIND_UPGRADE_OPPORTUNITY,
    provider_supports_kind,
)

ENTRY_SCORE_THRESHOLD = 30
EXPIRY_WINDOW_DAYS = 90
ELITE_PROGRESS_RATIO = 0.8

_PROGRESS_RE = re.compile(r"([\d,]+)\s*(?:of|/)\s*([\d,]+)", re.I)
_UPGRADE_RE = re.compile(r"\bupgrade\b", re.I)
_EMPTY = frozenset({"", "—", "–", "-", "n/a", "none", "no data", "tbd", "0"})


@dataclass(frozen=True)
class OpportunityCandidate:
    kind: str
    field_key: str
    label: str
    value: str
    field_type: str
    score: int
    urgency: str
    days_left: int | None
    exp_date: str | None
    value_estimate: float | None
    fingerprint: str
    summary: str
    suppressed: bool = False
    suppress_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "field_key": self.field_key,
            "label": self.label,
            "value": self.value,
            "field_type": self.field_type,
            "score": self.score,
            "urgency": self.urgency,
            "days_left": self.days_left,
            "exp_date": self.exp_date,
            "value_estimate": self.value_estimate,
            "fingerprint": self.fingerprint,
            "summary": self.summary,
            "suppressed": self.suppressed,
            "suppress_reason": self.suppress_reason,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ValuePolicyResult:
    candidates: tuple[OpportunityCandidate, ...]
    generated: int
    suppressed: int

    @property
    def active_candidates(self) -> tuple[OpportunityCandidate, ...]:
        return tuple(c for c in self.candidates if not c.suppressed)


def opportunity_fingerprint(
    *,
    provider: str,
    kind: str,
    field_key: str,
    exp_date: str | None = None,
) -> str:
    payload = f"{provider}|{kind}|{field_key}|{exp_date or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _norm_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() in _EMPTY:
        return None
    return text


def _field_type(item: Mapping[str, Any]) -> str:
    return str(item.get("_type") or item.get("type") or "other").strip() or "other"


def _field_key(provider: str, item: Mapping[str, Any]) -> str:
    key = str(item.get("key") or "").strip()
    label = str(item.get("label") or "").strip()
    if key:
        return f"{provider}::{key}"
    return f"{provider}::{label[:80]}"


def _progress_ratio(label: str, value: str) -> float | None:
    for text in (value, label):
        m = _PROGRESS_RE.search(text or "")
        if not m:
            continue
        try:
            cur = float(m.group(1).replace(",", ""))
            tot = float(m.group(2).replace(",", ""))
        except ValueError:
            continue
        if tot <= 0:
            continue
        return cur / tot
    return None


_DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_INTRINSIC_DOLLARS = {
    "certificate": 300.0,
    "travel_credit": 100.0,
    "partner_benefit": 150.0,
    "membership": 80.0,
    "elite_status": 200.0,
    "cash_credit": None,  # prefer literal
}


def _value_estimate(item: Mapping[str, Any], btype: str) -> float | None:
    m = _DOLLAR_RE.search(str(item.get("value") or ""))
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    intrinsic = _INTRINSIC_DOLLARS.get(btype)
    return float(intrinsic) if intrinsic is not None else None


def _summary(kind: str, label: str, value: str, days_left: int | None) -> str:
    if kind == KIND_DUPLICATED_BENEFIT:
        return f"Duplicate benefit: {label}."
    if kind == KIND_ELITE_QUALIFICATION_RISK:
        return f"Elite qualification at risk — {label}: {value}."
    if days_left is not None and days_left >= 0:
        return f"{label} ({value}) — {days_left}d left."
    return f"{label}: {value}."


def _candidate(
    *,
    provider: str,
    kind: str,
    item: Mapping[str, Any],
    btype: str,
    days_left: int | None,
    today: date,
    score: int,
    urgency: str,
    suppressed: bool = False,
    suppress_reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> OpportunityCandidate:
    label = str(item.get("label") or "").strip()
    value = str(item.get("value") or "").strip()
    fkey = _field_key(provider, item)
    exp = exp_date_iso(days_left, today=today)
    return OpportunityCandidate(
        kind=kind,
        field_key=fkey,
        label=label,
        value=value,
        field_type=btype,
        score=score,
        urgency=urgency,
        days_left=days_left,
        exp_date=exp,
        value_estimate=_value_estimate(item, btype),
        fingerprint=opportunity_fingerprint(
            provider=provider, kind=kind, field_key=fkey, exp_date=exp
        ),
        summary=_summary(kind, label, value, days_left),
        suppressed=suppressed,
        suppress_reason=suppress_reason,
        metadata=dict(metadata or {}),
    )


def compute_opportunity_candidates(
    fields: Sequence[Mapping[str, Any]] | None,
    *,
    provider: str,
    today: date | datetime | None = None,
    user_intent: Mapping[str, Any] | None = None,
    user_type_affinity: Mapping[str, Any] | None = None,
    entry_threshold: int = ENTRY_SCORE_THRESHOLD,
    expiry_window_days: int = EXPIRY_WINDOW_DAYS,
    elite_progress_ratio: float = ELITE_PROGRESS_RATIO,
) -> ValuePolicyResult:
    """Compute opportunity candidates from normalized snapshot fields."""
    if isinstance(today, datetime):
        today_d = today.date()
    else:
        today_d = today or date.today()

    provider_key = str(provider or "").strip().lower()
    intent = dict(user_intent or {})
    affinity = dict(user_type_affinity or {})
    out: list[OpportunityCandidate] = []
    seen_type_label: dict[tuple[str, str], list[Mapping[str, Any]]] = {}

    for raw in fields or ():
        if not isinstance(raw, Mapping):
            continue
        label = str(raw.get("label") or "").strip()
        value = _norm_value(raw.get("value"))
        if not label or value is None:
            continue
        btype = _field_type(raw)
        days_left = parse_expiry_days(label, value, today=today_d)
        item = {
            "label": label,
            "value": value,
            "btype": btype,
            "days_left": days_left,
            "key": raw.get("key"),
        }
        score = score_opportunity(
            item,
            user_intent=intent,
            source=provider_key,
            user_type_affinity=affinity,
        )
        norm_label = label.strip().lower()
        seen_type_label.setdefault((btype, norm_label), []).append(raw)

        def _emit(kind: str, *, urg: str | None = None, sc: int | None = None) -> None:
            if not provider_supports_kind(provider_key, kind):
                out.append(
                    _candidate(
                        provider=provider_key,
                        kind=kind,
                        item=raw,
                        btype=btype,
                        days_left=days_left,
                        today=today_d,
                        score=sc if sc is not None else score,
                        urgency=urg or urgency_from_score(score),
                        suppressed=True,
                        suppress_reason="provider_capability",
                    )
                )
                return
            out.append(
                _candidate(
                    provider=provider_key,
                    kind=kind,
                    item=raw,
                    btype=btype,
                    days_left=days_left,
                    today=today_d,
                    score=sc if sc is not None else score,
                    urgency=urg or urgency_from_score(score),
                )
            )

        # Bills / renewals — always durable value facts.
        if btype == "payment_due":
            _emit(
                KIND_PAYMENT_DUE,
                urg=urgency_for_attention(days_left),
                sc=max(score, 40),
            )
            continue
        if btype == "renewal":
            _emit(
                KIND_RENEWAL,
                urg=urgency_for_attention(days_left),
                sc=max(score, 40),
            )
            continue

        # Elite qualification risk from progress.
        if btype == "progress_toward":
            ratio = _progress_ratio(label, value)
            if ratio is not None and ratio >= elite_progress_ratio:
                _emit(KIND_ELITE_QUALIFICATION_RISK, urg="soon", sc=max(score, 45))
            else:
                out.append(
                    _candidate(
                        provider=provider_key,
                        kind=KIND_ELITE_QUALIFICATION_RISK,
                        item=raw,
                        btype=btype,
                        days_left=days_left,
                        today=today_d,
                        score=score,
                        urgency="info",
                        suppressed=True,
                        suppress_reason="below_progress_threshold",
                        metadata={"ratio": ratio},
                    )
                )
            continue

        # Points with expiry.
        if btype == "points_balance":
            if (
                days_left is not None
                and 0 <= days_left <= expiry_window_days
            ):
                _emit(KIND_EXPIRING_POINTS)
            else:
                out.append(
                    _candidate(
                        provider=provider_key,
                        kind=KIND_EXPIRING_POINTS,
                        item=raw,
                        btype=btype,
                        days_left=days_left,
                        today=today_d,
                        score=score,
                        urgency=urgency_from_score(score),
                        suppressed=True,
                        suppress_reason="no_expiry_in_window",
                    )
                )
            continue

        if btype == "certificate":
            if _UPGRADE_RE.search(label):
                if score >= entry_threshold or (
                    days_left is not None and 0 <= days_left <= expiry_window_days
                ):
                    _emit(KIND_UPGRADE_OPPORTUNITY)
                else:
                    out.append(
                        _candidate(
                            provider=provider_key,
                            kind=KIND_UPGRADE_OPPORTUNITY,
                            item=raw,
                            btype=btype,
                            days_left=days_left,
                            today=today_d,
                            score=score,
                            urgency=urgency_from_score(score),
                            suppressed=True,
                            suppress_reason="below_score_threshold",
                        )
                    )
            if days_left is not None and 0 <= days_left <= expiry_window_days:
                _emit(KIND_EXPIRING_CERTIFICATE)
            elif score >= entry_threshold:
                _emit(KIND_UNUSED_BENEFIT)
            elif is_actionable(btype):
                out.append(
                    _candidate(
                        provider=provider_key,
                        kind=KIND_UNUSED_BENEFIT,
                        item=raw,
                        btype=btype,
                        days_left=days_left,
                        today=today_d,
                        score=score,
                        urgency=urgency_from_score(score),
                        suppressed=True,
                        suppress_reason="below_score_threshold",
                    )
                )
            continue

        if btype in {"cash_credit", "travel_credit"}:
            if days_left is not None and 0 <= days_left <= expiry_window_days:
                _emit(KIND_EXPIRING_CREDIT)
            elif score >= entry_threshold:
                _emit(KIND_UNUSED_BENEFIT)
            elif is_actionable(btype):
                out.append(
                    _candidate(
                        provider=provider_key,
                        kind=KIND_UNUSED_BENEFIT,
                        item=raw,
                        btype=btype,
                        days_left=days_left,
                        today=today_d,
                        score=score,
                        urgency=urgency_from_score(score),
                        suppressed=True,
                        suppress_reason="below_score_threshold",
                    )
                )
            continue

        if is_needs_attention(btype) or is_actionable(btype):
            out.append(
                _candidate(
                    provider=provider_key,
                    kind=KIND_UNUSED_BENEFIT,
                    item=raw,
                    btype=btype,
                    days_left=days_left,
                    today=today_d,
                    score=score,
                    urgency=urgency_from_score(score),
                    suppressed=True,
                    suppress_reason="unhandled_type",
                )
            )

    # Duplicated benefits (same type + label more than once).
    for (btype, _nl), items in seen_type_label.items():
        if len(items) < 2:
            continue
        if btype in {"other", "expiry_date", "reservation"}:
            continue
        kind = KIND_DUPLICATED_BENEFIT
        if not provider_supports_kind(provider_key, kind):
            continue
        # One opportunity for the group, keyed by first field.
        first = items[0]
        label = str(first.get("label") or "").strip()
        value = str(first.get("value") or "").strip()
        days_left = parse_expiry_days(label, value, today=today_d)
        item = {
            "label": label,
            "value": value,
            "btype": btype,
            "days_left": days_left,
            "key": first.get("key") or f"dup::{btype}::{_nl}",
        }
        score = score_opportunity(
            item, user_intent=intent, source=provider_key, user_type_affinity=affinity
        )
        out.append(
            _candidate(
                provider=provider_key,
                kind=kind,
                item={**first, "key": item["key"]},
                btype=btype,
                days_left=days_left,
                today=today_d,
                score=max(score, 35),
                urgency="info",
                metadata={"duplicate_count": len(items)},
            )
        )

    # Deterministic order
    out.sort(key=lambda c: (c.kind, c.field_key, c.fingerprint))
    generated = sum(1 for c in out if not c.suppressed)
    suppressed = sum(1 for c in out if c.suppressed)
    return ValuePolicyResult(
        candidates=tuple(out),
        generated=generated,
        suppressed=suppressed,
    )
