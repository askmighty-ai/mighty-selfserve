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
    BenefitSignal,
    TrustSignal,
    WorkerSignal,
)
from mighty.auth_truth import (
    ACCESS_MANAGED_RUNTIME,
    AuthTruth,
    normalize_access_method,
    project_auth_truth,
)
from mighty.extension_version import (
    extension_update_required,
    read_expected_extension_version,
)
from mighty.runtime_access_state import (
    compute_presentation_status,
    get_runtime_access_state,
)


def load_authorize_rows(
    db: Any,
    user_id: str,
    *,
    statuses: Sequence[str] = ("pending", "awaiting_authorization"),
) -> list[AuthorizeRow]:
    """Load authorize-store rows as ``AuthorizeRow`` compiler inputs.

    Defaults to awaiting-authorization actions (legacy ``pending`` + M11
    ``awaiting_authorization``). Terminal rows are omitted here.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return []
    wanted = tuple(str(s).strip().lower() for s in statuses if str(s).strip())
    if not wanted:
        return []
    placeholders = ",".join("?" for _ in wanted)
    try:
        # Match legacy status and/or M11 lifecycle_state.
        rows = db.execute(
            f"""
            SELECT id, user_id, status, created_at, expires_at, provider,
                   lifecycle_state
            FROM actions
            WHERE user_id = ?
              AND (
                lower(status) IN ({placeholders})
                OR lower(COALESCE(lifecycle_state, '')) IN ({placeholders})
              )
            ORDER BY created_at ASC, id ASC
            """,
            (uid, *wanted, *wanted),
        ).fetchall()
    except Exception:
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
            return []

    result: list[AuthorizeRow] = []
    for row in rows:
        mapping = _row_mapping(row)
        try:
            lifecycle = str(mapping.get("lifecycle_state") or "").strip().lower()
            status = str(mapping.get("status") or "").strip().lower()
            # Present as pending so Attention compiler emits interruption.
            emit_status = "pending"
            if lifecycle and lifecycle not in {
                "pending",
                "awaiting_authorization",
                "proposed",
            }:
                emit_status = lifecycle
            elif status and status not in {"pending", "awaiting_authorization"}:
                emit_status = status
            provider = mapping.get("provider")
            result.append(
                AuthorizeRow(
                    action_id=str(mapping.get("id") or ""),
                    user_id=str(mapping.get("user_id") or uid),
                    status=emit_status,
                    created_at=_optional_str(mapping.get("created_at")),
                    expires_at=_optional_str(mapping.get("expires_at")),
                    provider=str(provider).lower() if provider else None,
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


def load_trust_signals(
    db: Any,
    user_id: str,
    *,
    now: datetime,
    accounts: Sequence[AccountState] | None = None,
) -> list[TrustSignal]:
    """Load TrustSignal inputs for managed_runtime enrolled accounts.

    Soft-fails when runtime_access_state is unavailable. No emit policy here.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return []
    now = _ensure_aware(now)
    if accounts is None:
        accounts = load_account_states_for_attention(db, uid)

    signals: list[TrustSignal] = []
    for account in accounts:
        provider = str(getattr(account, "provider", "") or "").strip().lower()
        if not provider:
            continue
        method = normalize_access_method(getattr(account, "access_method", None))
        if method != ACCESS_MANAGED_RUNTIME:
            continue
        try:
            row = get_runtime_access_state(db, uid, provider)
        except Exception:
            row = None
        payload = dict((row or {}).get("payload") or {})
        status = compute_presentation_status(row, now=now)
        observed = (
            payload.get("authentication_state_changed_at")
            or payload.get("updated_at")
            or (row or {}).get("updated_at")
        )
        needs_human = (
            bool(payload.get("needs_human")) if "needs_human" in payload else False
        )
        try:
            signals.append(
                TrustSignal(
                    user_id=uid,
                    provider=provider,
                    access_method=method,
                    presentation_status=status,
                    authentication_state=_optional_str(
                        payload.get("authentication_state")
                    ),
                    access_health=_optional_str(payload.get("access_health")),
                    recovery_state=_optional_str(payload.get("recovery_state")),
                    runtime_state=_optional_str(payload.get("runtime_state")),
                    escalation_reason=_optional_str(payload.get("escalation_reason")),
                    observed_at=_optional_str(observed),
                    needs_human=needs_human,
                    interruption_expected=bool(
                        payload.get("interruption_expected") or False
                    ),
                )
            )
        except Exception:
            continue
    return signals


def load_benefit_signals(
    db: Any,
    user_id: str,
    *,
    now: datetime,
) -> list[BenefitSignal]:
    """Load open action_items as BenefitSignal compiler inputs.

    Mirrors open-item filtering (not dismissed/completed/snoozed). Does not
    decide value_at_risk vs opportunity — that is producer policy.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return []
    now = _ensure_aware(now)
    now_iso = now.replace(microsecond=0).isoformat()
    try:
        rows = db.execute(
            """
            SELECT id, source, field_key, label, value, btype, urgency,
                   days_left, exp_date, created_at
            FROM action_items
            WHERE user_id = ?
              AND dismissed_at IS NULL
              AND completed_at IS NULL
              AND (snoozed_until IS NULL OR snoozed_until < ?)
            ORDER BY
                CASE urgency WHEN 'urgent' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END,
                CASE WHEN days_left IS NULL THEN 9999 ELSE days_left END,
                id ASC
            """,
            (uid, now_iso),
        ).fetchall()
    except Exception:
        return []

    signals: list[BenefitSignal] = []
    for row in rows:
        mapping = _row_mapping(row)
        provider = _optional_str(mapping.get("source"))
        field_key = _optional_str(mapping.get("field_key"))
        if not provider or not field_key:
            continue
        days_left = mapping.get("days_left")
        try:
            days_left_int = int(days_left) if days_left is not None else None
        except (TypeError, ValueError):
            days_left_int = None
        urgency = (_optional_str(mapping.get("urgency")) or "info").lower()
        kind = "expiring" if urgency in {"urgent", "soon"} else "opportunity"
        try:
            signals.append(
                BenefitSignal(
                    user_id=uid,
                    provider=provider,
                    field_key=field_key,
                    btype=_optional_str(mapping.get("btype")) or "other",
                    urgency=urgency,
                    days_left=days_left_int,
                    exp_date=_optional_str(mapping.get("exp_date")),
                    label=_optional_str(mapping.get("label")),
                    value=_optional_str(mapping.get("value")),
                    kind=kind,
                    observed_at=_optional_str(mapping.get("created_at")),
                    source_item_id=_optional_str(mapping.get("id")),
                )
            )
        except Exception:
            continue
    return signals


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
