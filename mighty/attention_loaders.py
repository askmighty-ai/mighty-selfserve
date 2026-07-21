"""Attention input loaders — DB facts → compiler inputs.

No ranking, overlay, or producer policy. Maps existing stores into the
shapes AttentionCompiler already understands.

See docs/ATTENTION_ENGINE.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from mighty.account_state import AccountState, list_account_states
from mighty.attention_compiler import AuthorizeRow
from mighty.auth_truth import AuthTruth, project_auth_truth


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
