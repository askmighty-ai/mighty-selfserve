"""
mighty.provider_account
───────────────────────
Canonical provider account model.

A provider account has:
  - source
  - connection_status  — auth / linking (provider-specific values)
  - extraction_status  — normalized-field pipeline state
  - normalized_fields  — display-ready field dicts (items)
  - data_source        — last adapter that wrote data (extension, api, …)

Synced means normalized data exists, regardless of which adapter supplied it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# ── Data-source adapters ──────────────────────────────────────────────────────
DATA_SOURCE_EXTENSION = "extension"
DATA_SOURCE_API = "api"
DATA_SOURCE_RAILWAY = "railway"
DATA_SOURCE_MANUAL = "manual"
DATA_SOURCE_EMAIL = "email"

# ── Extraction pipeline ───────────────────────────────────────────────────────
EXTRACTION_NOT_STARTED = "not_started"
EXTRACTION_PENDING = "pending"
EXTRACTION_COMPLETE = "complete"
EXTRACTION_FAILED = "failed"

EXTRACTION_STATUSES = (
    EXTRACTION_NOT_STARTED,
    EXTRACTION_PENDING,
    EXTRACTION_COMPLETE,
    EXTRACTION_FAILED,
)

_EMPTY_FIELD_VALUES = frozenset({"", "—", "–", "-", "n/a", "none", "0", "no data"})


def has_normalized_data(fields: list | None) -> bool:
    """True when at least one normalized field has a meaningful value."""
    for item in fields or []:
        if not isinstance(item, dict):
            continue
        val = str(item.get("value", "")).strip().lower()
        if val and val not in _EMPTY_FIELD_VALUES:
            return True
    return False


def is_synced(
    normalized_fields: list | None,
    *,
    extraction_status: str | None = None,
) -> bool:
    """Synced = normalized data exists (any adapter)."""
    if extraction_status == EXTRACTION_COMPLETE:
        return has_normalized_data(normalized_fields)
    return has_normalized_data(normalized_fields)


def infer_extraction_status(
    normalized_fields: list | None,
    *,
    explicit: str | None = None,
    sync_status: str = "ok",
) -> str:
    if explicit and explicit in EXTRACTION_STATUSES:
        if explicit == EXTRACTION_COMPLETE and not has_normalized_data(normalized_fields):
            return EXTRACTION_PENDING
        return explicit
    if has_normalized_data(normalized_fields):
        return EXTRACTION_COMPLETE
    if sync_status in ("no_data", "login_required"):
        return EXTRACTION_FAILED
    return EXTRACTION_NOT_STARTED


def normalize_data_source(raw: str | None) -> str | None:
    """Map legacy sync_source values to adapter ids."""
    if not raw:
        return None
    src = raw.strip().lower()
    if src in (DATA_SOURCE_EXTENSION, DATA_SOURCE_API, DATA_SOURCE_RAILWAY,
               DATA_SOURCE_MANUAL, DATA_SOURCE_EMAIL):
        return src
    return src


@dataclass
class ProviderAccount:
    source: str
    connection_status: str | None = None
    extraction_status: str = EXTRACTION_NOT_STARTED
    normalized_fields: list = field(default_factory=list)
    data_source: str | None = None
    synced_at: str | None = None
    sync_status: str = "ok"

    @property
    def is_synced(self) -> bool:
        return is_synced(self.normalized_fields, extraction_status=self.extraction_status)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "connection_status": self.connection_status,
            "extraction_status": self.extraction_status,
            "data_source": self.data_source,
            "normalized_fields": self.normalized_fields,
            "normalized_field_count": len(self.normalized_fields),
            "is_synced": self.is_synced,
            "synced_at": self.synced_at,
            "sync_status": self.sync_status,
        }


def _read_blob_connection(ad_data: dict) -> str | None:
    return ad_data.get("connection_status") or None


def load_provider_account(
    uid: str,
    row: dict | None,
    *,
    decrypt_fn: Callable,
) -> ProviderAccount | None:
    """Build a ProviderAccount from an account_data row."""
    if not row:
        return None

    ad_data = decrypt_fn(uid, row.get("data_enc") or "")
    normalized = ad_data.get("items") or ad_data.get("ai_items") or []
    sync_status = (
        (row.get("sync_status") or "")
        or ad_data.get("sync_status")
        or "ok"
    )
    col_extraction = row.get("extraction_status") or ""
    blob_extraction = ad_data.get("extraction_status") or ""
    extraction = infer_extraction_status(
        normalized,
        explicit=col_extraction or blob_extraction or None,
        sync_status=sync_status,
    )
    data_source = normalize_data_source(
        ad_data.get("data_source") or ad_data.get("sync_source")
    )
    connection = (row.get("connection_status") or "") or _read_blob_connection(ad_data)

    return ProviderAccount(
        source=row["source"],
        connection_status=connection or None,
        extraction_status=extraction,
        normalized_fields=normalized,
        data_source=data_source,
        synced_at=row.get("synced_at") or None,
        sync_status=sync_status,
    )


def persist_provider_state(
    db,
    uid: str,
    source: str,
    ad_data: dict,
    *,
    encrypt_fn: Callable,
    connection_status: str | None = None,
    extraction_status: str | None = None,
    data_source: str | None = None,
    synced_at: str | None = None,
    access_cycle_id: str | None = None,
    iso_fn: Callable | None = None,
) -> None:
    """Write provider account state to encrypted blob + indexed columns."""
    clear_login_sync = False
    if connection_status is not None:
        ad_data["connection_status"] = connection_status
        if iso_fn:
            ad_data["connection_status_at"] = iso_fn()
        if connection_status == "connected" and ad_data.get("sync_status") in {
            "login_required", "needs_first_visit",
        }:
            ad_data["sync_status"] = "ok"
            clear_login_sync = True
    if extraction_status is not None:
        ad_data["extraction_status"] = extraction_status
    if data_source is not None:
        ad_data["data_source"] = data_source
        ad_data["sync_source"] = data_source  # legacy alias for sync pipeline
    if access_cycle_id:
        ad_data["access_cycle_id"] = access_cycle_id
        ad_data["extraction_access_cycle_id"] = access_cycle_id

    sets = ["data_enc=?"]
    params: list = [encrypt_fn(uid, ad_data)]

    if connection_status is not None:
        sets.append("connection_status=?")
        params.append(connection_status)
    if clear_login_sync:
        sets.append("sync_status=?")
        params.append("ok")
    if extraction_status is not None:
        sets.append("extraction_status=?")
        params.append(extraction_status)
    if synced_at is not None:
        sets.append("synced_at=?")
        params.append(synced_at)

    params.extend([uid, source])
    db.execute(
        f"UPDATE account_data SET {', '.join(sets)} WHERE user_id=? AND source=?",
        params,
    )


def mark_extraction_pending(
    db,
    uid: str,
    source: str,
    *,
    encrypt_fn: Callable,
    decrypt_fn: Callable,
    iso_fn: Callable,
) -> None:
    """Connection verified — awaiting normalized extraction from any adapter."""
    row = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
        (uid, source),
    ).fetchone()
    if not row:
        return
    ad_data = decrypt_fn(uid, row["data_enc"] or "")
    if has_normalized_data(ad_data.get("items") or ad_data.get("ai_items")):
        return
    persist_provider_state(
        db, uid, source, ad_data,
        encrypt_fn=encrypt_fn,
        extraction_status=EXTRACTION_PENDING,
        iso_fn=iso_fn,
    )
    db.commit()


def apply_adapter_payload(
    db,
    uid: str,
    source: str,
    payload: dict,
    *,
    data_source: str,
    synced_at: str,
    encrypt_fn: Callable,
    decrypt_fn: Callable,
    access_cycle_id: str | None = None,
) -> ProviderAccount:
    """Merge an adapter sync payload and update extraction / data_source columns."""
    row = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
        (uid, source),
    ).fetchone()
    ad_data = decrypt_fn(uid, row["data_enc"] or "") if row else {}
    ad_data.update(payload)

    normalized = ad_data.get("items") or ad_data.get("ai_items") or []
    sync_status = ad_data.get("sync_status", "ok")
    extraction = infer_extraction_status(normalized, sync_status=sync_status)
    adapter = normalize_data_source(data_source)

    cycle_id = access_cycle_id
    if cycle_id is None and extraction == EXTRACTION_COMPLETE:
        try:
            from mighty.account_readiness import make_access_cycle_id
            from mighty.provider_session_state import get_provider_session_states
            from mighty.session_verification import get_session_verifications

            pss = get_provider_session_states(db, uid, providers=[source]).get(source)
            verifications = get_session_verifications(db, uid, providers=[source])
            verification = verifications.get(source)
            cycle_id = make_access_cycle_id(
                provider=source,
                verification_id=verification.verification_id if verification else None,
                session_evidence_at=pss.observed_at if pss else None,
            )
        except Exception:
            cycle_id = None

    persist_provider_state(
        db, uid, source, ad_data,
        encrypt_fn=encrypt_fn,
        extraction_status=extraction,
        data_source=adapter,
        synced_at=synced_at if extraction == EXTRACTION_COMPLETE else None,
        access_cycle_id=cycle_id if extraction == EXTRACTION_COMPLETE else None,
    )
    db.commit()

    return ProviderAccount(
        source=source,
        connection_status=ad_data.get("connection_status"),
        extraction_status=extraction,
        normalized_fields=normalized,
        data_source=adapter,
        synced_at=synced_at if extraction == EXTRACTION_COMPLETE else ad_data.get("synced_at"),
        sync_status=sync_status,
    )
