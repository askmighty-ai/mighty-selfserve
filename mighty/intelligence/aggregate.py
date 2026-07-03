"""
mighty.intelligence.aggregate
─────────────────────────────
Flatten provider accounts into a provider-agnostic snapshot for inference.

Pure functions only — no database, AI, or network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mighty.provider_account import ProviderAccount

from mighty.intelligence.models import IntelligenceInput, ProviderAccountSnapshot

_TRAVEL_LOYALTY_SOURCES = frozenset({
    "delta", "southwest", "united", "american_air", "alaska_air",
    "marriott", "hilton", "hyatt", "ihg", "wyndham",
    "british_airways", "air_france", "jetblue", "frontier", "spirit",
    "accor", "choice_hotels", "best_western",
})

_CREDIT_CARD_SOURCES = frozenset({
    "amex", "chase", "citi", "capital_one", "discover",
    "bank_of_america", "barclays", "apple_card",
})

_HOTEL_SOURCES = frozenset({
    "marriott", "hilton", "hyatt", "ihg", "wyndham",
    "accor", "choice_hotels", "best_western",
})

_AIRLINE_SOURCES = frozenset({
    "delta", "southwest", "united", "american_air", "alaska_air",
    "british_airways", "air_france", "jetblue", "frontier", "spirit",
})

_CAR_SOURCES = frozenset({
    "hertz", "avis", "enterprise", "national", "budget", "alamo", "thrifty",
})

_NUMERIC_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def source_category(source: str) -> str:
    key = (source or "").strip().lower()
    if key in _TRAVEL_LOYALTY_SOURCES:
        return "travel_loyalty"
    if key in _CREDIT_CARD_SOURCES:
        return "credit_card"
    return "other"


def source_domain(source: str) -> str:
    key = (source or "").strip().lower()
    if key in _HOTEL_SOURCES:
        return "hotel"
    if key in _AIRLINE_SOURCES:
        return "flight"
    if key in _CAR_SOURCES:
        return "car"
    if key in _CREDIT_CARD_SOURCES:
        return "credit_card"
    return "other"


def parse_numeric(value: str | None) -> float | None:
    if not value:
        return None
    match = _NUMERIC_RE.search(str(value).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def field_text(field: dict) -> str:
    return " ".join(
        str(field.get(part, "") or "")
        for part in ("label", "key", "value")
    ).lower()


@dataclass
class AggregatedSnapshot:
    accounts: list[ProviderAccountSnapshot]
    by_type: dict[str, list[dict]] = field(default_factory=dict)
    by_domain: dict[str, list[dict]] = field(default_factory=dict)
    intent_summary: dict[str, int] = field(default_factory=dict)
    type_affinity: dict[str, float] = field(default_factory=dict)
    email_subjects: list[str] = field(default_factory=list)
    connected_sources: list[str] = field(default_factory=list)
    synced_sources: list[str] = field(default_factory=list)

    def fields_for_type(self, btype: str) -> list[dict]:
        return list(self.by_type.get(btype, []))

    def fields_for_domain(self, domain: str) -> list[dict]:
        return list(self.by_domain.get(domain, []))


def snapshot_from_accounts(
    accounts: list[ProviderAccount],
    *,
    intent_summary: dict[str, int] | None = None,
    type_affinity: dict[str, float] | None = None,
    email_subjects: list[str] | None = None,
) -> AggregatedSnapshot:
    """Build an aggregated snapshot from canonical provider accounts."""
    snapshots: list[ProviderAccountSnapshot] = []
    by_type: dict[str, list[dict]] = {}
    by_domain: dict[str, list[dict]] = {}

    for acct in accounts:
        if not acct.is_synced:
            continue
        snap = ProviderAccountSnapshot(
            source=acct.source,
            category=source_category(acct.source),
            fields=list(acct.normalized_fields or []),
            is_synced=True,
        )
        snapshots.append(snap)
        domain = source_domain(acct.source)
        for item in snap.fields:
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            enriched["_source"] = acct.source
            enriched["_domain"] = domain
            btype = str(enriched.get("_type") or "other")
            by_type.setdefault(btype, []).append(enriched)
            by_domain.setdefault(domain, []).append(enriched)

    synced_sources = sorted({s.source for s in snapshots})
    return AggregatedSnapshot(
        accounts=snapshots,
        by_type=by_type,
        by_domain=by_domain,
        intent_summary=dict(intent_summary or {}),
        type_affinity=dict(type_affinity or {}),
        email_subjects=list(email_subjects or []),
        connected_sources=sorted({acct.source for acct in accounts}),
        synced_sources=synced_sources,
    )


def build_intelligence_input(
    accounts: list[ProviderAccount],
    *,
    intent_summary: dict[str, int] | None = None,
    type_affinity: dict[str, float] | None = None,
    email_subjects: list[str] | None = None,
) -> IntelligenceInput:
    """Convert provider accounts into the intelligence layer input contract."""
    snapshots: list[ProviderAccountSnapshot] = []
    for acct in accounts:
        snapshots.append(
            ProviderAccountSnapshot(
                source=acct.source,
                category=source_category(acct.source),
                fields=list(acct.normalized_fields or []),
                is_synced=acct.is_synced,
            )
        )
    return IntelligenceInput(
        accounts=snapshots,
        intent_summary=dict(intent_summary or {}),
        type_affinity=dict(type_affinity or {}),
        email_subjects=list(email_subjects or []),
    )


def aggregate_input(input_data: IntelligenceInput) -> AggregatedSnapshot:
    """Aggregate a pre-built intelligence input."""
    accounts = [
        ProviderAccount(
            source=acct.source,
            normalized_fields=acct.fields,
            extraction_status="complete" if acct.is_synced else "not_started",
        )
        for acct in input_data.accounts
        if acct.is_synced
    ]
    return snapshot_from_accounts(
        accounts,
        intent_summary=input_data.intent_summary,
        type_affinity=input_data.type_affinity,
        email_subjects=input_data.email_subjects,
    )
