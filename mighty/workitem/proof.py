"""ProofItem — earned material outcomes for Home disclosure.

Proof is not a Work Item and cannot create Work Items.
See docs/HOME_OS_DOMAIN_MODEL.md §3 and docs/HOME_OS_BEHAVIOR.md §6.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


class ProofValidationError(ValueError):
    """Raised when a ProofItem payload is invalid."""


@dataclass(frozen=True)
class ProofItem:
    """Retained evidence of a material, true outcome."""

    id: str
    outcome_at: datetime
    summary: str
    provider: str | None = None
    outcome_class: str = "general"
    work_item_id: str | None = None
    impact: str = "normal"  # "low" | "normal" | "high" — collapse uses low

    def __post_init__(self) -> None:
        proof_id = str(self.id or "").strip()
        if not proof_id:
            raise ProofValidationError("id must be a non-empty string")
        object.__setattr__(self, "id", proof_id)

        if not isinstance(self.outcome_at, datetime):
            raise ProofValidationError("outcome_at must be a datetime")
        if self.outcome_at.tzinfo is None:
            object.__setattr__(
                self, "outcome_at", self.outcome_at.replace(tzinfo=timezone.utc)
            )

        summary = str(self.summary or "").strip()
        if not summary:
            raise ProofValidationError("summary must be a non-empty string")
        object.__setattr__(self, "summary", summary)

        provider = None if self.provider is None else str(self.provider).strip() or None
        object.__setattr__(self, "provider", provider)

        outcome_class = str(self.outcome_class or "general").strip().lower()
        if not outcome_class:
            raise ProofValidationError("outcome_class must be non-empty")
        object.__setattr__(self, "outcome_class", outcome_class)

        work_item_id = (
            None
            if self.work_item_id is None
            else str(self.work_item_id).strip() or None
        )
        object.__setattr__(self, "work_item_id", work_item_id)

        impact = str(self.impact or "normal").strip().lower()
        if impact not in {"low", "normal", "high"}:
            raise ProofValidationError(
                "impact must be one of low, normal, high"
            )
        object.__setattr__(self, "impact", impact)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "outcome_at": self.outcome_at.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "summary": self.summary,
            "provider": self.provider,
            "outcome_class": self.outcome_class,
            "work_item_id": self.work_item_id,
            "impact": self.impact,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProofItem:
        if not isinstance(payload, Mapping):
            raise ProofValidationError("ProofItem payload must be a mapping")
        raw_at = payload.get("outcome_at")
        if isinstance(raw_at, datetime):
            outcome_at = raw_at
        else:
            text = str(raw_at or "").replace("Z", "+00:00")
            outcome_at = datetime.fromisoformat(text)
        return cls(
            id=str(payload.get("id") or ""),
            outcome_at=outcome_at,
            summary=str(payload.get("summary") or ""),
            provider=payload.get("provider"),
            outcome_class=str(payload.get("outcome_class") or "general"),
            work_item_id=payload.get("work_item_id"),
            impact=str(payload.get("impact") or "normal"),
        )


@dataclass(frozen=True)
class ProofDisclosure:
    """One row in HomeState.proof after ordering / collapse."""

    id: str
    summary: str
    outcome_at: datetime
    provider: str | None
    outcome_class: str
    member_ids: tuple[str, ...]
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "summary": self.summary,
            "outcome_at": self.outcome_at.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "provider": self.provider,
            "outcome_class": self.outcome_class,
            "member_ids": list(self.member_ids),
            "count": self.count,
        }


def order_proof_items(items: Sequence[ProofItem]) -> tuple[ProofItem, ...]:
    """Newest outcome first; tie-break by id ascending."""
    return tuple(
        sorted(
            items,
            key=lambda p: (-p.outcome_at.timestamp(), p.id),
        )
    )


def collapse_proof_items(
    items: Sequence[ProofItem],
    *,
    as_of: datetime | None = None,
) -> tuple[ProofDisclosure, ...]:
    """Order and collapse low-impact similar events for Home disclosure.

    Grouping key: provider + outcome_class + UTC calendar day.
    Only ``impact=low`` items collapse; normal/high remain individual rows.
    Collapse never fabricates outcomes.
    """
    del as_of  # reserved for retention windows; retention is owner-side input
    ordered = order_proof_items(items)
    if not ordered:
        return ()

    disclosures: list[ProofDisclosure] = []
    # Collapse bucket: key → list of low-impact proofs
    buckets: dict[tuple[str, str, str], list[ProofItem]] = {}
    bucket_order: list[tuple[str, str, str]] = []

    for proof in ordered:
        if proof.impact != "low":
            # Flush any pending? Keep low buckets separate; emit high/normal immediately
            # but preserve overall newest-first by emitting in pass order with lazy flush.
            disclosures.append(
                ProofDisclosure(
                    id=proof.id,
                    summary=proof.summary,
                    outcome_at=proof.outcome_at,
                    provider=proof.provider,
                    outcome_class=proof.outcome_class,
                    member_ids=(proof.id,),
                    count=1,
                )
            )
            continue
        day = proof.outcome_at.astimezone(timezone.utc).date().isoformat()
        key = (proof.provider or "", proof.outcome_class, day)
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(proof)

    # Merge collapsed lows into the disclosure stream by representative time.
    low_rows: list[ProofDisclosure] = []
    for key in bucket_order:
        members = buckets[key]
        if not members:
            continue
        head = members[0]
        if len(members) == 1:
            low_rows.append(
                ProofDisclosure(
                    id=head.id,
                    summary=head.summary,
                    outcome_at=head.outcome_at,
                    provider=head.provider,
                    outcome_class=head.outcome_class,
                    member_ids=(head.id,),
                    count=1,
                )
            )
        else:
            member_ids = tuple(m.id for m in members)
            low_rows.append(
                ProofDisclosure(
                    id=f"group:{key[0]}:{key[1]}:{key[2]}",
                    summary=f"{head.summary} (+{len(members) - 1} more)",
                    outcome_at=head.outcome_at,
                    provider=head.provider,
                    outcome_class=head.outcome_class,
                    member_ids=member_ids,
                    count=len(members),
                )
            )

    combined = list(disclosures) + low_rows
    combined.sort(key=lambda d: (-d.outcome_at.timestamp(), d.id))
    return tuple(combined)
