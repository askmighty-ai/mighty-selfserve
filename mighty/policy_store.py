"""Durable User Policy store (Milestone 12).

Bridges existing ``users`` settings columns — does not create a parallel
settings system. Settings writers sync here; Authorization reads Policy.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from mighty.user_policy import (
    UserPolicy,
    default_user_policy,
    merge_policies,
    policy_from_dict,
    policy_from_user_row,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_policy_tables(db: Any, *, commit: bool = True) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_policies (
            user_id TEXT PRIMARY KEY,
            policy_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    if commit:
        db.commit()


def _load_store_row(db: Any, user_id: str) -> UserPolicy | None:
    ensure_policy_tables(db, commit=False)
    row = db.execute(
        "SELECT policy_json, updated_at, version FROM user_policies WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["policy_json"] or "{}")
    except Exception:
        return None
    payload["updated_at"] = row["updated_at"]
    payload["version"] = row["version"]
    payload["source"] = "store"
    return policy_from_dict(payload, user_id=user_id)


def _load_user_row(db: Any, user_id: str) -> Mapping[str, Any] | None:
    try:
        row = db.execute(
            """
            SELECT minimal_logging, delete_raw_after_extract, notify_email,
                   notify_push, notify_ntfy, alert_expiry_emails, notification_pref
            FROM users WHERE id=?
            """,
            (user_id,),
        ).fetchone()
    except Exception:
        try:
            row = db.execute(
                "SELECT minimal_logging, delete_raw_after_extract FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
        except Exception:
            return None
    return row


def load_user_policy(db: Any, user_id: str) -> UserPolicy:
    """Load effective Policy: users projection merged with store overlay."""
    uid = str(user_id).strip()
    if not uid:
        return default_user_policy("")
    users_proj = policy_from_user_row(uid, _load_user_row(db, uid))
    store = _load_store_row(db, uid)
    return merge_policies(users_proj, store)


def save_user_policy(
    db: Any,
    policy: UserPolicy,
    *,
    sync_users: bool = True,
    commit: bool = True,
) -> UserPolicy:
    """Persist Policy and optionally sync legacy ``users`` columns."""
    ensure_policy_tables(db, commit=False)
    uid = str(policy.user_id).strip()
    stamp = utc_now_iso()
    payload = policy.to_dict()
    payload["source"] = "store"
    payload["updated_at"] = stamp
    db.execute(
        """
        INSERT INTO user_policies (user_id, policy_json, updated_at, version)
        VALUES (?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            policy_json=excluded.policy_json,
            updated_at=excluded.updated_at,
            version=excluded.version
        """,
        (uid, json.dumps(payload, default=str), stamp, int(policy.version)),
    )
    if sync_users:
        try:
            db.execute(
                """
                UPDATE users SET
                    minimal_logging=?,
                    delete_raw_after_extract=?,
                    notify_email=?,
                    notify_push=?,
                    notify_ntfy=?,
                    alert_expiry_emails=?,
                    notification_pref=?
                WHERE id=?
                """,
                (
                    1 if policy.minimal_logging else 0,
                    1 if policy.delete_raw_after_extract else 0,
                    1 if policy.notify_email else 0,
                    1 if policy.notify_push else 0,
                    1 if policy.notify_ntfy else 0,
                    1 if policy.alert_expiry_emails else 0,
                    policy.notification_pref,
                    uid,
                ),
            )
        except Exception:
            # Narrow column set for older schemas
            try:
                db.execute(
                    """
                    UPDATE users SET minimal_logging=?, delete_raw_after_extract=?
                    WHERE id=?
                    """,
                    (
                        1 if policy.minimal_logging else 0,
                        1 if policy.delete_raw_after_extract else 0,
                        uid,
                    ),
                )
            except Exception:
                pass
    if commit:
        db.commit()
    return load_user_policy(db, uid)


def sync_policy_from_users(db: Any, user_id: str, *, commit: bool = True) -> UserPolicy:
    """Upsert Policy from current ``users`` row (Settings bridge)."""
    uid = str(user_id).strip()
    projected = policy_from_user_row(uid, _load_user_row(db, uid))
    existing = _load_store_row(db, uid)
    if existing is None:
        return save_user_policy(db, projected, sync_users=False, commit=commit)
    # Preserve governance knobs; refresh privacy/notify from users.
    merged = UserPolicy(
        user_id=uid,
        require_human_at_or_above=existing.require_human_at_or_above,
        auto_execute_informational=existing.auto_execute_informational,
        auto_execute_routine=existing.auto_execute_routine,
        monitor_providers=existing.monitor_providers,
        suppress_opportunity_kinds=existing.suppress_opportunity_kinds,
        minimal_logging=projected.minimal_logging,
        delete_raw_after_extract=projected.delete_raw_after_extract,
        retention_days=existing.retention_days,
        notify_email=projected.notify_email,
        notify_push=projected.notify_push,
        notify_ntfy=projected.notify_ntfy,
        alert_expiry_emails=projected.alert_expiry_emails,
        notification_pref=projected.notification_pref,
        provider_overrides=existing.provider_overrides,
        version=existing.version,
        source="store",
    )
    return save_user_policy(db, merged, sync_users=False, commit=commit)
