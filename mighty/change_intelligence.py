"""Pure snapshot change intelligence (Milestone 9).

Diffs Account Snapshots into meaningful field deltas and concise summaries.
Provider-independent: significance uses field ``_type`` / ``type`` buckets only.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from mighty.freshness_policy import (
    STATE_MATERIALLY_CHANGED,
    STATE_NEWLY_DISCOVERED,
    STATE_REFRESHED_NO_MEANINGFUL,
    STATE_UNCHANGED,
)

# Types whose value changes are user-meaningful (provider-independent).
MEANINGFUL_FIELD_TYPES: frozenset[str] = frozenset(
    {
        "points_balance",
        "cash_credit",
        "travel_credit",
        "certificate",
        "elite_status",
        "membership",
        "payment_due",
        "renewal",
        "expiry_date",
        "partner_benefit",
    }
)

KIND_ADDED = "added"
KIND_REMOVED = "removed"
KIND_CHANGED = "changed"

_EMPTY = frozenset({"", "—", "–", "-", "n/a", "none", "no data"})
_NUM_RE = re.compile(r"[\$£€]?\s*([\d]+(?:\.\d+)?)")


@dataclass(frozen=True)
class FieldDelta:
    field_key: str
    field_label: str
    field_type: str
    old_value: str | None
    new_value: str | None
    kind: str
    meaningful: bool
    fingerprint: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_key": self.field_key,
            "field_label": self.field_label,
            "field_type": self.field_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "kind": self.kind,
            "meaningful": self.meaningful,
            "fingerprint": self.fingerprint,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class ChangeVerdict:
    outcome: str
    deltas: tuple[FieldDelta, ...]
    summary: str
    meaningful_count: int
    change_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "summary": self.summary,
            "meaningful_count": self.meaningful_count,
            "change_fingerprint": self.change_fingerprint,
            "deltas": [d.to_dict() for d in self.deltas],
        }


def _norm_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in _EMPTY:
        return None
    return text


def _field_type(item: Mapping[str, Any]) -> str:
    return str(item.get("_type") or item.get("type") or "other").strip() or "other"


def _field_map(fields: Sequence[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in fields or ():
        if not isinstance(raw, Mapping):
            continue
        key = str(raw.get("key") or "").strip()
        label = str(raw.get("label") or "").strip()
        if not key and not label:
            continue
        fkey = key or label.lower().replace(" ", "_")
        value = _norm_value(raw.get("value"))
        if value is None:
            continue
        out[fkey] = {
            "key": fkey,
            "label": label or fkey,
            "value": value,
            "_type": _field_type(raw),
            "confidence": raw.get("confidence"),
        }
    return out


def _numeric(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace(",", "")
    m = _NUM_RE.search(cleaned)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def values_equivalent(old: str | None, new: str | None) -> bool:
    if old == new:
        return True
    if old is None or new is None:
        return False
    if old.strip().lower() == new.strip().lower():
        return True
    o_n, n_n = _numeric(old), _numeric(new)
    if o_n is not None and n_n is not None:
        return abs(o_n - n_n) < 1e-9
    return False


def field_delta_fingerprint(
    *,
    provider: str,
    field_key: str,
    old_value: str | None,
    new_value: str | None,
) -> str:
    payload = f"{provider}|{field_key}|{old_value or ''}|{new_value or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def is_meaningful_type(field_type: str) -> bool:
    return str(field_type or "").strip().lower() in MEANINGFUL_FIELD_TYPES


def _snapshot_fields(snapshot: Any) -> Sequence[Mapping[str, Any]]:
    if snapshot is None:
        return ()
    fields = getattr(snapshot, "normalized_fields", None)
    if fields is None and isinstance(snapshot, Mapping):
        fields = snapshot.get("normalized_fields")
    return tuple(fields or ())


def _evidence_provenance(snapshot: Any) -> dict[str, Any]:
    if snapshot is None:
        return {}
    refs = getattr(snapshot, "evidence_refs", ()) or ()
    first = refs[0] if refs else None
    prov: dict[str, Any] = {
        "snapshot_id": getattr(snapshot, "snapshot_id", None),
        "verified_at": getattr(snapshot, "verified_at", None),
        "access_cycle_id": getattr(snapshot, "access_cycle_id", None),
    }
    if first is not None:
        to_dict = getattr(first, "to_dict", None)
        if callable(to_dict):
            prov["evidence"] = to_dict()
        elif isinstance(first, Mapping):
            prov["evidence"] = dict(first)
    return {k: v for k, v in prov.items() if v is not None}


def diff_snapshots(
    prev: Any | None,
    new: Any,
    *,
    provider: str | None = None,
) -> ChangeVerdict:
    """Diff previous vs new successful snapshots into a change verdict."""
    if new is None:
        return ChangeVerdict(
            outcome=STATE_UNCHANGED,
            deltas=(),
            summary="",
            meaningful_count=0,
            change_fingerprint="",
        )

    provider_key = (
        str(provider or getattr(new, "provider", "") or "").strip().lower()
    )
    new_map = _field_map(_snapshot_fields(new))
    prov = _evidence_provenance(new)

    if prev is None:
        deltas: list[FieldDelta] = []
        for key, item in sorted(new_map.items()):
            meaningful = is_meaningful_type(item["_type"])
            fp = field_delta_fingerprint(
                provider=provider_key,
                field_key=key,
                old_value=None,
                new_value=item["value"],
            )
            deltas.append(
                FieldDelta(
                    field_key=key,
                    field_label=item["label"],
                    field_type=item["_type"],
                    old_value=None,
                    new_value=item["value"],
                    kind=KIND_ADDED,
                    meaningful=meaningful,
                    fingerprint=fp,
                    provenance=prov,
                )
            )
        meaningful_deltas = tuple(d for d in deltas if d.meaningful)
        summary = summarize_meaningful_deltas(
            provider_key, STATE_NEWLY_DISCOVERED, meaningful_deltas
        )
        return ChangeVerdict(
            outcome=STATE_NEWLY_DISCOVERED,
            deltas=tuple(deltas),
            summary=summary,
            meaningful_count=len(meaningful_deltas),
            change_fingerprint=_set_fingerprint(meaningful_deltas or deltas),
        )

    prev_map = _field_map(_snapshot_fields(prev))
    keys = sorted(set(prev_map) | set(new_map))
    deltas = []
    for key in keys:
        before = prev_map.get(key)
        after = new_map.get(key)
        if before and after and values_equivalent(before["value"], after["value"]):
            continue
        if before is None and after is not None:
            kind = KIND_ADDED
            old_v, new_v = None, after["value"]
            label, ftype = after["label"], after["_type"]
        elif before is not None and after is None:
            kind = KIND_REMOVED
            old_v, new_v = before["value"], None
            label, ftype = before["label"], before["_type"]
        else:
            kind = KIND_CHANGED
            old_v, new_v = before["value"], after["value"]
            label, ftype = after["label"], after["_type"]
        meaningful = is_meaningful_type(ftype)
        fp = field_delta_fingerprint(
            provider=provider_key,
            field_key=key,
            old_value=old_v,
            new_value=new_v,
        )
        deltas.append(
            FieldDelta(
                field_key=key,
                field_label=label,
                field_type=ftype,
                old_value=old_v,
                new_value=new_v,
                kind=kind,
                meaningful=meaningful,
                fingerprint=fp,
                provenance=prov,
            )
        )

    meaningful_deltas = tuple(d for d in deltas if d.meaningful)
    if meaningful_deltas:
        outcome = STATE_MATERIALLY_CHANGED
    elif deltas:
        # Non-meaningful churn only — treat as quiet refresh.
        outcome = STATE_REFRESHED_NO_MEANINGFUL
    else:
        outcome = STATE_REFRESHED_NO_MEANINGFUL

    summary = summarize_meaningful_deltas(provider_key, outcome, meaningful_deltas)
    return ChangeVerdict(
        outcome=outcome,
        deltas=tuple(deltas),
        summary=summary,
        meaningful_count=len(meaningful_deltas),
        change_fingerprint=_set_fingerprint(meaningful_deltas),
    )


def _set_fingerprint(deltas: Iterable[FieldDelta]) -> str:
    parts = sorted(d.fingerprint for d in deltas)
    if not parts:
        return ""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _provider_display(provider: str) -> str:
    key = (provider or "").strip()
    if not key:
        return "Account"
    return key[:1].upper() + key[1:]


def summarize_meaningful_deltas(
    provider: str,
    outcome: str,
    meaningful: Sequence[FieldDelta],
) -> str:
    """Concise summary suitable for Home, Account Detail, Briefs, notifications."""
    name = _provider_display(provider)
    if outcome == STATE_NEWLY_DISCOVERED:
        if not meaningful:
            return f"{name} data is available."
        labels = ", ".join(d.field_label for d in meaningful[:3])
        return f"{name} connected — {labels}."
    if outcome != STATE_MATERIALLY_CHANGED or not meaningful:
        return ""

    parts: list[str] = []
    for delta in meaningful[:3]:
        if delta.kind == KIND_ADDED:
            parts.append(f"{delta.field_label} is {delta.new_value}")
        elif delta.kind == KIND_REMOVED:
            parts.append(f"{delta.field_label} removed")
        else:
            parts.append(
                f"{delta.field_label} {delta.old_value} → {delta.new_value}"
            )
    body = "; ".join(parts)
    more = len(meaningful) - 3
    if more > 0:
        body = f"{body}; +{more} more"
    return f"{name}: {body}."
