"""Attention input loaders — DB facts → compiler inputs.

No ranking, overlay, or producer policy. Maps existing stores into the
shapes AttentionCompiler already understands.

See docs/ATTENTION_ENGINE.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from mighty.account_state import AccountState, list_account_states
from mighty.admin_local_time import parse_admin_timestamp
from mighty.attention_compiler import (
    WORKER_REACHABLE_SLA_SECONDS,
    AuthorizeRow,
    WorkerSignal,
)
from mighty.auth_truth import AuthTruth, project_auth_truth
from mighty.extension_version import (
    extension_update_required,
    read_expected_extension_version,
)


def load_authorize_rows(
    db: Any,
    user_id: str,
    *,
    statuses: Sequence[str] = ("pending",),
) -> list[AuthorizeRow]:
    """Load authorize-store rows as ``AuthorizeRow`` compiler inputs.

    Defaults to pending actions only. Terminal rows are omitted here; the
    compiler would also emit ``None`` for non-pending statuses.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return []
    wanted = tuple(str(s).strip().lower() for s in statuses if str(s).strip())
    if not wanted:
        return []
    placeholders = ",".join("?" for _ in wanted)
    try:
        rows = db.execute(
            f"""
            SELECT id, user_id, status, created_at, expires_at
            FROM actions
            WHERE user_id = ? AND lower(status) IN ({placeholders})
            ORDER BY created_at ASC, id ASC
            """,
            (uid, *wanted),
        ).fetchall()
    except Exception:
        # Table may be absent in minimal fixtures.
        return []

    result: list[AuthorizeRow] = []
    for row in rows:
        mapping = _row_mapping(row)
        try:
            result.append(
                AuthorizeRow(
                    action_id=str(mapping.get("id") or ""),
                    user_id=str(mapping.get("user_id") or uid),
                    status=str(mapping.get("status") or ""),
                    created_at=_optional_str(mapping.get("created_at")),
                    expires_at=_optional_str(mapping.get("expires_at")),
                    provider=None,
                )
            )
        except Exception:
            continue
    return result


def load_account_states_for_attention(db: Any, user_id: str) -> list[AccountState]:
    """Load AccountState rows for data_gap compilation.

    Enrollment owns the provider list. No producer policy here.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return []
    try:
        return list(list_account_states(db, uid))
    except Exception:
        return []


def load_worker_signal(
    db: Any,
    user_id: str,
    *,
    now: datetime,
    enrolled_account_count: int | None = None,
) -> WorkerSignal | None:
    """Load extension heartbeat facts as a WorkerSignal compiler input.

    Reachability is age of ``extension_last_seen_at`` vs
    ``WORKER_REACHABLE_SLA_SECONDS``. No ranking policy here.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return None
    now = _ensure_aware(now)
    if enrolled_account_count is None:
        enrolled_account_count = len(load_account_states_for_attention(db, uid))

    # Unknown user / missing table → no WorkerSignal (do not invent SYSTEM).
    try:
        row = db.execute(
            "SELECT extension_version, extension_last_seen_at FROM users WHERE id=?",
            (uid,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None

    mapping = _row_mapping(row)
    version = _optional_str(mapping.get("extension_version"))
    last_seen_raw = _optional_str(mapping.get("extension_last_seen_at"))
    installed = bool(version or last_seen_raw)
    reachable = False
    if last_seen_raw:
        seen_dt = parse_admin_timestamp(last_seen_raw)
        if seen_dt is not None:
            if seen_dt.tzinfo is None:
                seen_dt = seen_dt.replace(tzinfo=timezone.utc)
            age = (now - seen_dt).total_seconds()
            reachable = age <= WORKER_REACHABLE_SLA_SECONDS

    update_required = False
    try:
        expected = read_expected_extension_version()
        update_required = extension_update_required(version, expected)
    except Exception:
        update_required = False

    return WorkerSignal(
        user_id=uid,
        installed=installed,
        reachable=reachable,
        last_seen_at=last_seen_raw,
        version=version,
        update_required=update_required,
        enrolled_account_count=int(enrolled_account_count or 0),
    )


def load_auth_truths(
    db: Any,
    user_id: str,
    *,
    now: datetime,
    projected_at: str | None = None,
    accounts: Sequence[AccountState] | None = None,
) -> list[AuthTruth]:
    """Project AuthTruth for each enrolled account (account_state providers).

    Enrollment owns the provider list. Projection uses the existing AuthTruth
    projector (primary access_method only). Pass ``accounts`` to reuse a
    single AccountState load with the data_gap path.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return []
    now = _ensure_aware(now)
    projected = projected_at or now.replace(microsecond=0).isoformat()
    if accounts is None:
        try:
            accounts = list_account_states(db, uid)
        except Exception:
            accounts = []

    truths: list[AuthTruth] = []
    for account in accounts:
        provider = str(getattr(account, "provider", "") or "").strip().lower()
        if not provider:
            continue
        access_method = getattr(account, "access_method", None)
        try:
            truths.append(
                project_auth_truth(
                    db,
                    uid,
                    provider,
                    access_method=access_method,
                    now=now,
                    projected_at=projected,
                )
            )
        except Exception:
            continue
    return truths


def _row_mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        pass
    # sqlite3.Row without row_factory / tuple fallback
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return {
        "id": row[0],
        "user_id": row[1],
        "status": row[2],
        "created_at": row[3] if len(row) > 3 else None,
        "expires_at": row[4] if len(row) > 4 else None,
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
