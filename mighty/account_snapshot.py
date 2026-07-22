"""
mighty.account_snapshot
───────────────────────
Canonical immutable Account Snapshot layer.

Flow:
  Verification → Extraction → Normalization → Account Snapshot (persist) → Customer UI

Customer-facing surfaces must render account/rewards data from the latest
successful snapshot — never from in-progress extraction, verification
lifecycle, or temporary provider state.

Snapshots are append-only. Failed / partial / running extractions never
replace a good snapshot.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from mighty.provider_account import (
    EXTRACTION_COMPLETE,
    has_normalized_data,
    infer_extraction_status,
)

SNAPSHOT_SCHEMA_VERSION = 1
PROVIDER_VERSION_DEFAULT = "1"

# Benefit-type → snapshot bucket mapping (provider-independent).
_TYPE_TO_BUCKET: dict[str, str] = {
    "points_balance": "rewards",
    "elite_status": "benefits",
    "certificate": "benefits",
    "membership": "benefits",
    "partner_benefit": "benefits",
    "progress_toward": "benefits",
    "cash_credit": "credits",
    "travel_credit": "credits",
    "upcoming_event": "travel",
    "reservation": "travel",
    "payment_due": "warnings",
    "renewal": "warnings",
    "expiry_date": "warnings",
    "other": "benefits",
}

_BUCKET_KEYS = ("accounts", "benefits", "rewards", "credits", "offers", "travel", "warnings")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_snapshot_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class SnapshotEvidenceRef:
    """Pointer to extraction evidence — never embeds raw payloads."""

    kind: str
    provider: str
    synced_at: str | None = None
    field_keys: tuple[str, ...] = ()
    pipeline_run_id: str | None = None
    access_cycle_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "provider": self.provider,
        }
        if self.synced_at:
            payload["synced_at"] = self.synced_at
        if self.field_keys:
            payload["field_keys"] = list(self.field_keys)
        if self.pipeline_run_id:
            payload["pipeline_run_id"] = self.pipeline_run_id
        if self.access_cycle_id:
            payload["access_cycle_id"] = self.access_cycle_id
        return payload


@dataclass(frozen=True)
class AccountSnapshot:
    """Immutable normalized account snapshot — source of truth for customer UI."""

    snapshot_id: str
    user_id: str
    provider: str
    account_identifier: str
    verified_at: str
    created_at: str
    schema_version: int
    provider_version: str
    confidence: float | None
    correlation_id: str | None
    access_cycle_id: str | None
    accounts: tuple[dict[str, Any], ...] = ()
    benefits: tuple[dict[str, Any], ...] = ()
    rewards: tuple[dict[str, Any], ...] = ()
    credits: tuple[dict[str, Any], ...] = ()
    offers: tuple[dict[str, Any], ...] = ()
    travel: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[SnapshotEvidenceRef, ...] = ()
    normalized_fields: tuple[dict[str, Any], ...] = ()

    @property
    def field_count(self) -> int:
        return len(self.normalized_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "user_id": self.user_id,
            "provider": self.provider,
            "account_identifier": self.account_identifier,
            "verified_at": self.verified_at,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "provider_version": self.provider_version,
            "confidence": self.confidence,
            "correlation_id": self.correlation_id,
            "access_cycle_id": self.access_cycle_id,
            "accounts": list(self.accounts),
            "benefits": list(self.benefits),
            "rewards": list(self.rewards),
            "credits": list(self.credits),
            "offers": list(self.offers),
            "travel": list(self.travel),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "normalized_fields": list(self.normalized_fields),
            "field_count": self.field_count,
        }

    def to_metadata_dict(self) -> dict[str, Any]:
        """Internal/API metadata — no field payloads."""
        return {
            "snapshot_id": self.snapshot_id,
            "provider": self.provider,
            "account_identifier": self.account_identifier,
            "verified_at": self.verified_at,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "provider_version": self.provider_version,
            "confidence": self.confidence,
            "correlation_id": self.correlation_id,
            "access_cycle_id": self.access_cycle_id,
            "field_count": self.field_count,
            "evidence_ref_count": len(self.evidence_refs),
        }

    def display_items(self) -> list[dict[str, Any]]:
        """Provider-independent field list for product surfaces (Dashboard/Home)."""
        return [dict(item) for item in self.normalized_fields]


def ensure_account_snapshot_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS account_snapshots (
            snapshot_id         TEXT PRIMARY KEY,
            user_id             TEXT NOT NULL,
            provider            TEXT NOT NULL,
            account_identifier  TEXT NOT NULL,
            verified_at         TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            schema_version      INTEGER NOT NULL,
            provider_version    TEXT NOT NULL,
            confidence          REAL,
            correlation_id      TEXT,
            access_cycle_id     TEXT,
            payload_json        TEXT NOT NULL,
            is_successful       INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_snapshots_user_provider "
        "ON account_snapshots(user_id, provider, created_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_snapshots_active "
        "ON account_snapshots(user_id, provider, is_successful, created_at DESC)"
    )
    db.commit()


def _normalize_field_item(raw: dict[str, Any], *, provider: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or "").strip()
    label = str(raw.get("label") or "").strip()
    value = str(raw.get("value") or "").strip()
    if not label and not key:
        return None
    if not value or value.lower() in {"", "—", "–", "-", "n/a", "none", "no data"}:
        return None

    btype = str(raw.get("_type") or "other").strip() or "other"
    if btype == "other" or not btype:
        try:
            from mighty.classify import classify_benefit

            btype = classify_benefit(label, value, provider) or "other"
        except Exception:
            btype = "other"

    item: dict[str, Any] = {
        "key": key or label.lower().replace(" ", "_"),
        "label": label or key,
        "value": value,
        "_type": btype,
    }
    if raw.get("confidence") is not None:
        try:
            item["confidence"] = float(raw["confidence"])
        except (TypeError, ValueError):
            pass
    return item


def _bucket_for_type(btype: str) -> str:
    return _TYPE_TO_BUCKET.get(btype, "benefits")


def normalize_fields_to_snapshot_payload(
    fields: list | None,
    *,
    provider: str,
    account_identifier: str | None = None,
) -> dict[str, Any]:
    """Convert extraction items into a provider-independent snapshot payload."""
    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in _BUCKET_KEYS}
    normalized: list[dict[str, Any]] = []

    for raw in fields or []:
        item = _normalize_field_item(raw, provider=provider)
        if not item:
            continue
        normalized.append(item)
        bucket = _bucket_for_type(item["_type"])
        buckets[bucket].append(
            {
                "key": item["key"],
                "label": item["label"],
                "value": item["value"],
                "type": item["_type"],
                **({"confidence": item["confidence"]} if "confidence" in item else {}),
            }
        )

    identifier = (account_identifier or provider).strip() or provider
    buckets["accounts"] = [
        {
            "provider": provider,
            "account_identifier": identifier,
        }
    ]

    return {
        "accounts": buckets["accounts"],
        "benefits": buckets["benefits"],
        "rewards": buckets["rewards"],
        "credits": buckets["credits"],
        "offers": buckets["offers"],
        "travel": buckets["travel"],
        "warnings": buckets["warnings"],
        "normalized_fields": normalized,
    }


def _mean_confidence(fields: list[dict[str, Any]]) -> float | None:
    values = []
    for item in fields:
        conf = item.get("confidence")
        if conf is None:
            continue
        try:
            values.append(float(conf))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def build_account_snapshot(
    *,
    user_id: str,
    provider: str,
    fields: list | None,
    verified_at: str | None = None,
    created_at: str | None = None,
    account_identifier: str | None = None,
    schema_version: int = SNAPSHOT_SCHEMA_VERSION,
    provider_version: str | None = None,
    confidence: float | None = None,
    correlation_id: str | None = None,
    access_cycle_id: str | None = None,
    evidence_refs: list[SnapshotEvidenceRef] | None = None,
    metadata: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
) -> AccountSnapshot | None:
    """Build an immutable snapshot from normalized extraction fields.

    Returns None when fields are empty / not meaningful (do not persist).
    """
    payload = normalize_fields_to_snapshot_payload(
        fields,
        provider=provider,
        account_identifier=account_identifier,
    )
    normalized = payload["normalized_fields"]
    if not has_normalized_data(normalized):
        return None

    now = utc_now_iso()
    verified = verified_at or now
    created = created_at or now
    identifier = (account_identifier or provider).strip() or provider
    refs = tuple(evidence_refs or ())
    conf = confidence if confidence is not None else _mean_confidence(normalized)

    return AccountSnapshot(
        snapshot_id=snapshot_id or new_snapshot_id(),
        user_id=user_id,
        provider=provider,
        account_identifier=identifier,
        verified_at=verified,
        created_at=created,
        schema_version=schema_version,
        provider_version=(provider_version or PROVIDER_VERSION_DEFAULT).strip()
        or PROVIDER_VERSION_DEFAULT,
        confidence=conf,
        correlation_id=correlation_id,
        access_cycle_id=access_cycle_id,
        accounts=tuple(payload["accounts"]),
        benefits=tuple(payload["benefits"]),
        rewards=tuple(payload["rewards"]),
        credits=tuple(payload["credits"]),
        offers=tuple(payload["offers"]),
        travel=tuple(payload["travel"]),
        warnings=tuple(payload["warnings"]),
        metadata=dict(metadata or {}),
        evidence_refs=refs,
        normalized_fields=tuple(normalized),
    )


def _row_to_snapshot(row: Any) -> AccountSnapshot:
    payload = json.loads(row["payload_json"] or "{}")
    refs_raw = payload.get("evidence_refs") or []
    refs: list[SnapshotEvidenceRef] = []
    for ref in refs_raw:
        if not isinstance(ref, dict):
            continue
        refs.append(
            SnapshotEvidenceRef(
                kind=str(ref.get("kind") or "account_data"),
                provider=str(ref.get("provider") or row["provider"]),
                synced_at=ref.get("synced_at"),
                field_keys=tuple(ref.get("field_keys") or ()),
                pipeline_run_id=ref.get("pipeline_run_id"),
                access_cycle_id=ref.get("access_cycle_id"),
            )
        )

    def _tuple_dicts(key: str) -> tuple[dict[str, Any], ...]:
        items = payload.get(key) or []
        return tuple(item for item in items if isinstance(item, dict))

    return AccountSnapshot(
        snapshot_id=row["snapshot_id"],
        user_id=row["user_id"],
        provider=row["provider"],
        account_identifier=row["account_identifier"],
        verified_at=row["verified_at"],
        created_at=row["created_at"],
        schema_version=int(row["schema_version"] or SNAPSHOT_SCHEMA_VERSION),
        provider_version=row["provider_version"] or PROVIDER_VERSION_DEFAULT,
        confidence=row["confidence"],
        correlation_id=row["correlation_id"],
        access_cycle_id=row["access_cycle_id"],
        accounts=_tuple_dicts("accounts"),
        benefits=_tuple_dicts("benefits"),
        rewards=_tuple_dicts("rewards"),
        credits=_tuple_dicts("credits"),
        offers=_tuple_dicts("offers"),
        travel=_tuple_dicts("travel"),
        warnings=_tuple_dicts("warnings"),
        metadata=dict(payload.get("metadata") or {}),
        evidence_refs=tuple(refs),
        normalized_fields=_tuple_dicts("normalized_fields"),
    )


def persist_account_snapshot(db: Any, snapshot: AccountSnapshot) -> AccountSnapshot:
    """Insert a new immutable snapshot. Never updates an existing row."""
    ensure_account_snapshot_tables(db)
    payload = {
        "accounts": list(snapshot.accounts),
        "benefits": list(snapshot.benefits),
        "rewards": list(snapshot.rewards),
        "credits": list(snapshot.credits),
        "offers": list(snapshot.offers),
        "travel": list(snapshot.travel),
        "warnings": list(snapshot.warnings),
        "metadata": dict(snapshot.metadata),
        "evidence_refs": [ref.to_dict() for ref in snapshot.evidence_refs],
        "normalized_fields": list(snapshot.normalized_fields),
    }
    db.execute(
        """
        INSERT INTO account_snapshots (
            snapshot_id, user_id, provider, account_identifier,
            verified_at, created_at, schema_version, provider_version,
            confidence, correlation_id, access_cycle_id, payload_json, is_successful
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)
        """,
        (
            snapshot.snapshot_id,
            snapshot.user_id,
            snapshot.provider,
            snapshot.account_identifier,
            snapshot.verified_at,
            snapshot.created_at,
            snapshot.schema_version,
            snapshot.provider_version,
            snapshot.confidence,
            snapshot.correlation_id,
            snapshot.access_cycle_id,
            json.dumps(payload, default=str),
        ),
    )
    db.commit()
    return snapshot


def create_account_snapshot_from_extraction(
    db: Any,
    *,
    user_id: str,
    provider: str,
    fields: list | None,
    verified_at: str | None = None,
    account_identifier: str | None = None,
    provider_version: str | None = None,
    confidence: float | None = None,
    correlation_id: str | None = None,
    access_cycle_id: str | None = None,
    pipeline_run_id: str | None = None,
    data_source: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AccountSnapshot | None:
    """Create + persist a snapshot from a successful extraction.

    Returns None (and writes nothing) when fields are incomplete.
    Amex snapshots require a correlated ``access_cycle_id`` — late / uncorrelated
    writes must fail closed.
    """
    if not has_normalized_data(fields):
        return None

    provider_key = str(provider or "").strip().lower()
    cycle_id = (access_cycle_id or correlation_id or "").strip() or None
    if provider_key == "amex" and not cycle_id:
        print(
            "ARCHITECTURE VIOLATION: uncorrelated extraction"
            f" snapshot_refused provider={provider_key}"
            f" access_cycle_id={access_cycle_id or ''}",
            flush=True,
        )
        return None

    field_keys = tuple(
        str(item.get("key") or "").strip()
        for item in (fields or [])
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    )
    refs = [
        SnapshotEvidenceRef(
            kind="account_data",
            provider=provider,
            synced_at=verified_at,
            field_keys=field_keys,
            pipeline_run_id=pipeline_run_id,
            access_cycle_id=access_cycle_id,
        )
    ]
    meta = dict(metadata or {})
    if data_source:
        meta.setdefault("data_source", data_source)

    snapshot = build_account_snapshot(
        user_id=user_id,
        provider=provider,
        fields=fields,
        verified_at=verified_at,
        account_identifier=account_identifier,
        provider_version=provider_version,
        confidence=confidence,
        correlation_id=correlation_id or access_cycle_id,
        access_cycle_id=access_cycle_id,
        evidence_refs=refs,
        metadata=meta,
    )
    if snapshot is None:
        return None
    # Milestone 9 — capture previous successful snapshot before append.
    prev = get_latest_successful_snapshot(db, user_id, provider)
    persisted = persist_account_snapshot(db, snapshot)
    try:
        from mighty.freshness_change import safe_observe_snapshot_refresh

        safe_observe_snapshot_refresh(db, prev=prev, new=persisted)
    except Exception:
        # Failure isolation: snapshot persist must succeed even if change
        # intelligence fails. safe_observe already swallows; this is belt/suspenders.
        pass
    return persisted


def get_snapshot_by_id(db: Any, snapshot_id: str) -> AccountSnapshot | None:
    ensure_account_snapshot_tables(db)
    row = db.execute(
        "SELECT * FROM account_snapshots WHERE snapshot_id=?",
        (snapshot_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_snapshot(row)


def get_latest_successful_snapshot(
    db: Any,
    user_id: str,
    provider: str,
) -> AccountSnapshot | None:
    """Active snapshot for customer UI — newest successful only."""
    ensure_account_snapshot_tables(db)
    row = db.execute(
        """
        SELECT * FROM account_snapshots
        WHERE user_id=? AND provider=? AND is_successful=1
        ORDER BY created_at DESC, snapshot_id DESC
        LIMIT 1
        """,
        (user_id, provider),
    ).fetchone()
    if not row:
        return None
    return _row_to_snapshot(row)


def list_account_snapshots(
    db: Any,
    user_id: str,
    provider: str | None = None,
    *,
    limit: int = 50,
    successful_only: bool = True,
) -> list[AccountSnapshot]:
    """Historical snapshots (newest first). Older rows remain queryable."""
    ensure_account_snapshot_tables(db)
    clauses = ["user_id=?"]
    params: list[Any] = [user_id]
    if provider:
        clauses.append("provider=?")
        params.append(provider)
    if successful_only:
        clauses.append("is_successful=1")
    where = " AND ".join(clauses)
    rows = db.execute(
        f"""
        SELECT * FROM account_snapshots
        WHERE {where}
        ORDER BY created_at DESC, snapshot_id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [_row_to_snapshot(row) for row in rows]


def load_latest_snapshots_by_provider(
    db: Any,
    user_id: str,
    providers: list[str] | None = None,
) -> dict[str, AccountSnapshot]:
    """Map provider → latest successful snapshot for a user."""
    ensure_account_snapshot_tables(db)
    if providers is not None and not providers:
        return {}

    if providers is None:
        rows = db.execute(
            """
            SELECT * FROM account_snapshots
            WHERE user_id=? AND is_successful=1
            ORDER BY created_at DESC, snapshot_id DESC
            """,
            (user_id,),
        ).fetchall()
    else:
        placeholders = ",".join("?" for _ in providers)
        rows = db.execute(
            f"""
            SELECT * FROM account_snapshots
            WHERE user_id=? AND is_successful=1 AND provider IN ({placeholders})
            ORDER BY created_at DESC, snapshot_id DESC
            """,
            (user_id, *providers),
        ).fetchall()

    result: dict[str, AccountSnapshot] = {}
    for row in rows:
        provider = row["provider"]
        if provider in result:
            continue
        result[provider] = _row_to_snapshot(row)
    return result


def load_snapshot_display_items(
    db: Any,
    user_id: str,
    provider: str,
) -> list[dict[str, Any]]:
    """Canonical field list for customer UI — empty if no successful snapshot."""
    snapshot = get_latest_successful_snapshot(db, user_id, provider)
    if not snapshot:
        return []
    return snapshot.display_items()


def maybe_backfill_snapshot_from_account_data(
    db: Any,
    user_id: str,
    provider: str,
    *,
    decrypt_fn: Callable[[str, str], dict],
) -> AccountSnapshot | None:
    """One-time migration: if account_data has successful extraction but no snapshot."""
    existing = get_latest_successful_snapshot(db, user_id, provider)
    if existing:
        return existing

    row = db.execute(
        "SELECT data_enc, synced_at, extraction_status FROM account_data "
        "WHERE user_id=? AND source=?",
        (user_id, provider),
    ).fetchone()
    if not row:
        return None

    ad_data = decrypt_fn(user_id, row["data_enc"] or "")
    fields = ad_data.get("items") or ad_data.get("ai_items") or []
    extraction = infer_extraction_status(
        fields,
        explicit=(row["extraction_status"] or ad_data.get("extraction_status") or None),
        sync_status=ad_data.get("sync_status", "ok"),
    )
    if extraction != EXTRACTION_COMPLETE or not has_normalized_data(fields):
        return None

    return create_account_snapshot_from_extraction(
        db,
        user_id=user_id,
        provider=provider,
        fields=fields,
        verified_at=row["synced_at"] or ad_data.get("synced_at") or utc_now_iso(),
        access_cycle_id=ad_data.get("access_cycle_id")
        or ad_data.get("extraction_access_cycle_id"),
        correlation_id=ad_data.get("access_cycle_id")
        or ad_data.get("extraction_access_cycle_id"),
        data_source=ad_data.get("data_source") or ad_data.get("sync_source"),
        metadata={"backfilled": True},
    )


def load_customer_snapshot_items(
    db: Any,
    user_id: str,
    provider: str,
    *,
    decrypt_fn: Callable[[str, str], dict] | None = None,
    allow_backfill: bool = True,
) -> tuple[list[dict[str, Any]], AccountSnapshot | None]:
    """Customer-facing read: latest successful snapshot items (+ optional backfill)."""
    snapshot = get_latest_successful_snapshot(db, user_id, provider)
    if snapshot is None and allow_backfill and decrypt_fn is not None:
        snapshot = maybe_backfill_snapshot_from_account_data(
            db, user_id, provider, decrypt_fn=decrypt_fn,
        )
    if snapshot is None:
        return [], None
    return snapshot.display_items(), snapshot
