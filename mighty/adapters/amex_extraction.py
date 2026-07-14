"""
Amex account-data extraction — normalized field storage.

The extension extractor is the sole authority for whether publishable account
data exists. This adapter persists extractor-supplied fields.
"""

from __future__ import annotations

import re
from typing import Any

from mighty.connection_state import AMEX_SOURCE
from mighty.provider_account import (
    DATA_SOURCE_EXTENSION,
    EXTRACTION_COMPLETE,
    has_normalized_data,
    persist_provider_state,
)

AMEX_MR_KEY = "points_balance"
AMEX_MR_LABEL = "Membership Rewards Points"
AMEX_MR_TYPE = "points_balance"


def normalize_points_value(raw: str) -> str | None:
    """Return a display-ready points string or None if invalid."""
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits or int(digits) <= 0:
        return None
    return f"{int(digits):,}"


def normalize_money_value(raw: str) -> str | None:
    if raw is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    if not cleaned:
        return None
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    if amount < 0:
        return None
    return f"{amount:,.2f}"


def build_amex_mr_item(value: str) -> dict:
    display = normalize_points_value(value)
    if not display:
        raise ValueError("invalid Membership Rewards value")
    return {
        "key": AMEX_MR_KEY,
        "label": AMEX_MR_LABEL,
        "value": display,
        "_type": AMEX_MR_TYPE,
    }


def normalize_extracted_fields(raw_fields: list[dict[str, Any]] | None, raw_value: str | None = None) -> list[dict]:
    """Normalize extractor fields; fall back to a single MR value."""
    items: list[dict] = []
    seen: set[str] = set()
    for raw in raw_fields or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        label = str(raw.get("label") or key).strip()
        value = raw.get("value")
        ftype = str(raw.get("_type") or "").strip()
        if not key or value is None:
            continue
        if key in seen:
            continue
        if key == AMEX_MR_KEY or ftype == "points_balance" or "points" in key:
            display = normalize_points_value(str(value))
            if not display:
                continue
            item = {
                "key": AMEX_MR_KEY if key == AMEX_MR_KEY else key,
                "label": label or AMEX_MR_LABEL,
                "value": display,
                "_type": "points_balance",
            }
        elif "statement_balance" in key or ftype == "currency":
            display = normalize_money_value(str(value))
            if not display:
                continue
            item = {
                "key": key,
                "label": label or "Statement Balance",
                "value": display,
                "_type": "currency",
            }
        elif "card_ending" in key or ftype == "card_ending":
            ending = re.sub(r"[^\d*]", "", str(value))[-4:]
            if len(ending) < 4:
                continue
            item = {
                "key": key,
                "label": label or "Card Ending",
                "value": ending,
                "_type": "card_ending",
            }
        else:
            text = str(value).strip()
            if not text:
                continue
            item = {
                "key": key,
                "label": label or key,
                "value": text,
                "_type": ftype or "text",
            }
        seen.add(item["key"])
        items.append(item)
    if not items and raw_value:
        items.append(build_amex_mr_item(raw_value))
    return items


def apply_amex_membership_rewards_extraction(
    db,
    uid: str,
    raw_value: str,
    *,
    iso_fn,
    encrypt_fn,
    decrypt_fn,
    data_source: str = DATA_SOURCE_EXTENSION,
    access_cycle_id: str | None = None,
    verification_id: str | None = None,
    fields: list[dict[str, Any]] | None = None,
) -> dict:
    """Persist extractor publishable fields for one access cycle.

    Requires ``verification_id`` / ``access_cycle_id`` so every extraction is
    correlated to exactly one access cycle. Uncorrelated writes are rejected.
    """
    from mighty.pipeline_inspector import record_adapter_extraction_run

    cycle_id = (access_cycle_id or verification_id or "").strip() or None
    verification_id = (verification_id or access_cycle_id or "").strip() or None
    if not cycle_id or not verification_id:
        print(
            "ARCHITECTURE VIOLATION: uncorrelated extraction"
            f" verification_id={verification_id or ''}"
            f" access_cycle_id={access_cycle_id or ''}",
            flush=True,
        )
        raise ValueError("active_verification_required")

    invalid_value = False
    try:
        items = normalize_extracted_fields(fields, raw_value)
        if not items:
            raise ValueError("no publishable fields")
    except ValueError:
        invalid_value = True
        items = []
    item = items[0] if items else None
    now = iso_fn()

    row = db.execute(
        "SELECT data_enc, connection_status FROM account_data WHERE user_id=? AND source=?",
        (uid, AMEX_SOURCE),
    ).fetchone()
    if not row:
        raise ValueError("amex account not found")

    if invalid_value or item is None:
        record_adapter_extraction_run(
            db,
            user_id=uid,
            source=AMEX_SOURCE,
            data_source=data_source,
            structured_item=None,
            extraction_status="failed",
            invalid_value=True,
        )
        raise ValueError("invalid Membership Rewards value")

    ad_data = decrypt_fn(uid, row["data_enc"] or "")
    ad_data["items"] = items
    ad_data["sync_status"] = "ok"
    ad_data.pop("sync_failure_reason", None)

    persist_provider_state(
        db,
        uid,
        AMEX_SOURCE,
        ad_data,
        encrypt_fn=encrypt_fn,
        extraction_status=EXTRACTION_COMPLETE,
        data_source=data_source,
        synced_at=now,
        access_cycle_id=cycle_id,
        iso_fn=iso_fn,
    )
    db.commit()

    try:
        from mighty.account_snapshot import create_account_snapshot_from_extraction

        create_account_snapshot_from_extraction(
            db,
            user_id=uid,
            provider=AMEX_SOURCE,
            fields=items,
            verified_at=now,
            access_cycle_id=cycle_id,
            correlation_id=cycle_id,
            data_source=data_source,
        )
    except Exception:
        pass

    record_adapter_extraction_run(
        db,
        user_id=uid,
        source=AMEX_SOURCE,
        data_source=data_source,
        structured_item=item,
        extraction_status=EXTRACTION_COMPLETE,
    )

    return {
        "source": AMEX_SOURCE,
        "field": item,
        "fields": items,
        "extraction_status": EXTRACTION_COMPLETE,
        "is_synced": has_normalized_data(items),
        "synced_at": now,
        "data_source": data_source,
        "access_cycle_id": cycle_id,
        "verification_id": verification_id,
    }
