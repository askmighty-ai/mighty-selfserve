"""
Provider-independent snapshot persistence.

Connector AccountSnapshot → StoredSnapshotRecord (append-only) → later Postgres.

This layer is observational only. It never mutates provider accounts, never
overwrites prior snapshots, and never produces advice.
"""

from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from mighty.provider_connector import (
    AccountSnapshot,
    AccountType,
    Completeness,
    FinancialAccount,
    MoneyAmount,
    RewardsBalance,
    utc_now_iso,
)


DEFAULT_CONNECTOR_VERSION = "1"


def _customer_key(provider_customer_id: str | None) -> str:
    text = str(provider_customer_id or "").strip()
    return text if text else "_unknown"


@dataclass(frozen=True)
class StoredSnapshotRecord:
    """Immutable persisted envelope around a canonical AccountSnapshot."""

    snapshot_id: str
    provider: str
    provider_customer_id: str | None
    observed_at: str
    verified_at: str | None
    connector_version: str
    extraction_summary: dict[str, Any]
    snapshot: AccountSnapshot
    stored_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "provider": self.provider,
            "provider_customer_id": self.provider_customer_id,
            "observed_at": self.observed_at,
            "verified_at": self.verified_at,
            "connector_version": self.connector_version,
            "extraction_summary": dict(self.extraction_summary),
            "snapshot": self.snapshot.to_dict(),
            "stored_at": self.stored_at,
        }


@dataclass(frozen=True)
class SnapshotPersistTelemetry:
    """Sanitized persist/diff telemetry — no sensitive account values."""

    snapshot_duration_ms: int
    snapshot_size_bytes: int
    facts_generated: int
    previous_snapshot_found: bool
    diff_duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_duration": self.snapshot_duration_ms,
            "snapshot_size": self.snapshot_size_bytes,
            "facts_generated": self.facts_generated,
            "previous_snapshot_found": self.previous_snapshot_found,
            "diff_duration": self.diff_duration_ms,
        }


class SnapshotStore(ABC):
    """Abstract append-only snapshot store (local JSON today, Postgres later)."""

    @abstractmethod
    def append(self, record: StoredSnapshotRecord) -> StoredSnapshotRecord:
        """Persist a new immutable snapshot. Never overwrites existing rows."""

    @abstractmethod
    def get(self, snapshot_id: str) -> StoredSnapshotRecord | None:
        """Load one snapshot by id."""

    @abstractmethod
    def get_latest(
        self,
        *,
        provider: str,
        provider_customer_id: str | None,
    ) -> StoredSnapshotRecord | None:
        """Return the most recent snapshot for provider + opaque customer id."""

    @abstractmethod
    def list_snapshots(
        self,
        *,
        provider: str,
        provider_customer_id: str | None,
        limit: int = 50,
    ) -> list[StoredSnapshotRecord]:
        """List snapshots newest-first for provider + customer."""


class LocalFileSnapshotStore(SnapshotStore):
    """Append-only JSON files under ``{root}/snapshots/``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.snapshots_root = self.root / "snapshots"
        self.snapshots_root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.snapshots_root / "index.json"
        if not self._index_path.exists():
            self._write_index({"entries": []})

    def append(self, record: StoredSnapshotRecord) -> StoredSnapshotRecord:
        if self.get(record.snapshot_id) is not None:
            raise ValueError(f"snapshot_already_exists:{record.snapshot_id}")
        path = self._record_path(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ValueError(f"snapshot_file_exists:{path}")
        payload = record.to_dict()
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        index = self._read_index()
        entries = list(index.get("entries") or [])
        entries.append(
            {
                "snapshot_id": record.snapshot_id,
                "provider": record.provider,
                "provider_customer_id": record.provider_customer_id,
                "observed_at": record.observed_at,
                "stored_at": record.stored_at,
                "path": str(path.relative_to(self.snapshots_root)),
            }
        )
        self._write_index({"entries": entries})
        return record

    def get(self, snapshot_id: str) -> StoredSnapshotRecord | None:
        for entry in self._read_index().get("entries") or []:
            if entry.get("snapshot_id") == snapshot_id:
                path = self.snapshots_root / str(entry["path"])
                return self._load_path(path)
        # Fallback scan for robustness if index is stale.
        for path in self.snapshots_root.rglob("*.json"):
            if path.name == "index.json":
                continue
            if path.stem == snapshot_id or path.name.startswith(f"{snapshot_id}."):
                loaded = self._load_path(path)
                if loaded and loaded.snapshot_id == snapshot_id:
                    return loaded
        return None

    def get_latest(
        self,
        *,
        provider: str,
        provider_customer_id: str | None,
    ) -> StoredSnapshotRecord | None:
        records = self.list_snapshots(
            provider=provider,
            provider_customer_id=provider_customer_id,
            limit=1,
        )
        return records[0] if records else None

    def list_snapshots(
        self,
        *,
        provider: str,
        provider_customer_id: str | None,
        limit: int = 50,
    ) -> list[StoredSnapshotRecord]:
        customer = provider_customer_id
        matched: list[tuple[str, Path]] = []
        for entry in self._read_index().get("entries") or []:
            if str(entry.get("provider") or "") != provider:
                continue
            if entry.get("provider_customer_id") != customer:
                # Treat missing/None and "_unknown" as equivalent for lookup.
                left = entry.get("provider_customer_id")
                if not (left is None and customer is None):
                    if _customer_key(left) != _customer_key(customer):
                        continue
            observed = str(entry.get("observed_at") or entry.get("stored_at") or "")
            matched.append((observed, self.snapshots_root / str(entry["path"])))
        matched.sort(key=lambda item: item[0], reverse=True)
        records: list[StoredSnapshotRecord] = []
        for _, path in matched[: max(0, int(limit))]:
            loaded = self._load_path(path)
            if loaded is not None:
                records.append(loaded)
        return records

    def _record_path(self, record: StoredSnapshotRecord) -> Path:
        customer = _customer_key(record.provider_customer_id)
        return (
            self.snapshots_root
            / record.provider
            / customer
            / f"{record.snapshot_id}.json"
        )

    def _load_path(self, path: Path) -> StoredSnapshotRecord | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return stored_snapshot_from_dict(payload)

    def _read_index(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"entries": []}
        if not isinstance(payload, dict):
            return {"entries": []}
        if not isinstance(payload.get("entries"), list):
            return {"entries": []}
        return payload

    def _write_index(self, payload: dict[str, Any]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


def build_extraction_summary(
    snapshot: AccountSnapshot,
    *,
    refresh_status: str | None = None,
    field_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compact non-sensitive summary of what a refresh extracted."""
    counts = field_counts or {}
    return {
        "account_count": len(snapshot.accounts),
        "rewards_program_count": len(snapshot.rewards),
        "completeness": snapshot.completeness.value
        if hasattr(snapshot.completeness, "value")
        else str(snapshot.completeness),
        "warning_count": len(snapshot.warnings),
        "refresh_status": refresh_status,
        "fields_succeeded": int(counts.get("fields_succeeded") or 0),
        "fields_unavailable": int(counts.get("fields_unavailable") or 0),
        "fields_failed": int(counts.get("fields_failed") or 0),
    }


def build_stored_snapshot(
    snapshot: AccountSnapshot,
    *,
    connector_version: str | None = None,
    extraction_summary: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
    stored_at: str | None = None,
) -> StoredSnapshotRecord:
    """Wrap a canonical connector snapshot for append-only persistence."""
    return StoredSnapshotRecord(
        snapshot_id=snapshot_id or str(uuid.uuid4()),
        provider=snapshot.provider,
        provider_customer_id=snapshot.provider_customer_id,
        observed_at=snapshot.observed_at,
        verified_at=snapshot.verified_at,
        connector_version=connector_version or DEFAULT_CONNECTOR_VERSION,
        extraction_summary=dict(extraction_summary or {}),
        snapshot=snapshot,
        stored_at=stored_at or utc_now_iso(),
    )


def money_from_dict(payload: Any) -> MoneyAmount | None:
    if payload is None:
        return None
    if isinstance(payload, MoneyAmount):
        return payload
    if not isinstance(payload, dict):
        return None
    raw_amount = payload.get("amount")
    currency = str(payload.get("currency") or "USD")
    if raw_amount is None:
        return None
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, ValueError):
        return None
    return MoneyAmount(amount=amount, currency=currency)


def _parse_date(raw: Any) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def account_from_dict(payload: dict[str, Any]) -> FinancialAccount:
    account_type_raw = str(payload.get("account_type") or AccountType.UNKNOWN.value)
    try:
        account_type = AccountType(account_type_raw)
    except ValueError:
        account_type = AccountType.UNKNOWN
    return FinancialAccount(
        provider_account_id=str(payload.get("provider_account_id") or ""),
        display_name=str(payload.get("display_name") or ""),
        account_type=account_type,
        currency=str(payload.get("currency") or "USD"),
        observed_at=str(payload.get("observed_at") or ""),
        product_name=payload.get("product_name"),
        last_four=payload.get("last_four"),
        current_balance=money_from_dict(payload.get("current_balance")),
        available_credit=money_from_dict(payload.get("available_credit")),
        payment_due_amount=money_from_dict(payload.get("payment_due_amount")),
        payment_due_date=_parse_date(payload.get("payment_due_date")),
    )


def rewards_from_dict(payload: dict[str, Any]) -> RewardsBalance:
    raw_balance = payload.get("balance")
    try:
        balance = Decimal(str(raw_balance)) if raw_balance is not None else Decimal("0")
    except (InvalidOperation, ValueError):
        balance = Decimal("0")
    return RewardsBalance(
        program_name=str(payload.get("program_name") or ""),
        balance=balance,
        unit=str(payload.get("unit") or "points"),
        observed_at=str(payload.get("observed_at") or ""),
    )


def account_snapshot_from_dict(payload: dict[str, Any]) -> AccountSnapshot:
    """Deserialize a canonical connector AccountSnapshot from JSON."""
    completeness_raw = str(payload.get("completeness") or Completeness.EMPTY.value)
    try:
        completeness = Completeness(completeness_raw)
    except ValueError:
        completeness = Completeness.EMPTY
    accounts = tuple(
        account_from_dict(item)
        for item in (payload.get("accounts") or [])
        if isinstance(item, dict)
    )
    rewards = tuple(
        rewards_from_dict(item)
        for item in (payload.get("rewards") or [])
        if isinstance(item, dict)
    )
    warnings = tuple(
        str(item) for item in (payload.get("warnings") or []) if item is not None
    )
    metadata = payload.get("provider_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return AccountSnapshot(
        provider=str(payload.get("provider") or ""),
        accounts=accounts,
        rewards=rewards,
        observed_at=str(payload.get("observed_at") or ""),
        verified_at=payload.get("verified_at"),
        completeness=completeness,
        warnings=warnings,
        provider_customer_id=payload.get("provider_customer_id"),
        provider_metadata=dict(metadata),
    )


def stored_snapshot_from_dict(payload: dict[str, Any]) -> StoredSnapshotRecord:
    snapshot_payload = payload.get("snapshot")
    if not isinstance(snapshot_payload, dict):
        raise ValueError("stored_snapshot_missing_snapshot")
    summary = payload.get("extraction_summary")
    if not isinstance(summary, dict):
        summary = {}
    return StoredSnapshotRecord(
        snapshot_id=str(payload.get("snapshot_id") or ""),
        provider=str(payload.get("provider") or snapshot_payload.get("provider") or ""),
        provider_customer_id=payload.get("provider_customer_id"),
        observed_at=str(
            payload.get("observed_at") or snapshot_payload.get("observed_at") or ""
        ),
        verified_at=payload.get("verified_at", snapshot_payload.get("verified_at")),
        connector_version=str(payload.get("connector_version") or DEFAULT_CONNECTOR_VERSION),
        extraction_summary=dict(summary),
        snapshot=account_snapshot_from_dict(snapshot_payload),
        stored_at=str(payload.get("stored_at") or utc_now_iso()),
    )


def measure_snapshot_size_bytes(record: StoredSnapshotRecord) -> int:
    return len(json.dumps(record.to_dict(), separators=(",", ":")).encode("utf-8"))


@dataclass(frozen=True)
class PersistAndDiffResult:
    """Outcome of persisting a refresh snapshot and computing facts."""

    stored: StoredSnapshotRecord
    previous: StoredSnapshotRecord | None
    facts: tuple[Any, ...]
    summary: str
    telemetry: SnapshotPersistTelemetry
    first_snapshot: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "stored_snapshot_id": self.stored.snapshot_id,
            "previous_snapshot_id": self.previous.snapshot_id if self.previous else None,
            "first_snapshot": self.first_snapshot,
            "facts": [fact.to_dict() for fact in self.facts],
            "summary": self.summary,
            "telemetry": self.telemetry.to_dict(),
            "stored": self.stored.to_dict(),
        }


def persist_refresh_snapshot(
    snapshot: AccountSnapshot,
    *,
    store: SnapshotStore,
    connector_version: str | None = None,
    extraction_summary: dict[str, Any] | None = None,
    diff_fn: Any = None,
    summarize_fn: Any = None,
) -> PersistAndDiffResult:
    """
    Append snapshot, diff against previous, and build a factual summary.

    Provider-independent: operates only on canonical AccountSnapshot models.
    """
    from mighty.fact_generator import format_facts_summary
    from mighty.snapshot_diff import diff_snapshots

    diff = diff_fn or diff_snapshots
    summarize = summarize_fn or format_facts_summary

    previous = store.get_latest(
        provider=snapshot.provider,
        provider_customer_id=snapshot.provider_customer_id,
    )

    started = time.perf_counter()
    record = build_stored_snapshot(
        snapshot,
        connector_version=connector_version,
        extraction_summary=extraction_summary,
    )
    stored = store.append(record)
    snapshot_duration_ms = int((time.perf_counter() - started) * 1000)
    snapshot_size = measure_snapshot_size_bytes(stored)

    diff_started = time.perf_counter()
    if previous is None:
        facts: tuple[Any, ...] = ()
        summary = "First snapshot recorded."
        first = True
    else:
        facts = tuple(diff(previous.snapshot, stored.snapshot, previous_id=previous.snapshot_id, after_id=stored.snapshot_id))
        summary = summarize(facts)
        first = False
    diff_duration_ms = int((time.perf_counter() - diff_started) * 1000)

    telemetry = SnapshotPersistTelemetry(
        snapshot_duration_ms=snapshot_duration_ms,
        snapshot_size_bytes=snapshot_size,
        facts_generated=len(facts),
        previous_snapshot_found=previous is not None,
        diff_duration_ms=diff_duration_ms,
    )
    return PersistAndDiffResult(
        stored=stored,
        previous=previous,
        facts=facts,
        summary=summary,
        telemetry=telemetry,
        first_snapshot=first,
    )
