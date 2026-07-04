"""
Amex Membership Rewards extraction — normalized field storage.
"""

from __future__ import annotations

import re

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


def apply_amex_membership_rewards_extraction(
    db,
    uid: str,
    raw_value: str,
    *,
    iso_fn,
    encrypt_fn,
    decrypt_fn,
    data_source: str = DATA_SOURCE_EXTENSION,
) -> dict:
    """Persist a single Membership Rewards balance as the normalized provider field."""
    from mighty.pipeline_inspector import record_adapter_extraction_run

    invalid_value = False
    try:
        item = build_amex_mr_item(raw_value)
    except ValueError:
        invalid_value = True
        item = None
    now = iso_fn()

    row = db.execute(
        "SELECT data_enc, connection_status FROM account_data WHERE user_id=? AND source=?",
        (uid, AMEX_SOURCE),
    ).fetchone()
    if not row:
        raise ValueError("amex account not found")

    if invalid_value:
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
    ad_data["items"] = [item]
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
        iso_fn=iso_fn,
    )
    db.commit()

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
        "extraction_status": EXTRACTION_COMPLETE,
        "is_synced": has_normalized_data([item]),
        "synced_at": now,
        "data_source": data_source,
    }
