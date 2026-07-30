"""Extension setup handshake diagnostics (beta Learning Blocker instrumentation).

Stages are recorded so Founder testing can distinguish install vs communication
vs account-association failures. Not a product philosophy surface.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from mighty.admin_local_time import parse_admin_timestamp, to_utc_iso_z

# Ordered handshake stages for UI + API.
HANDSHAKE_STAGES: tuple[str, ...] = (
    "page_meta_present",
    "service_worker_alive",
    "extension_saw_setup_page",
    "api_key_captured",
    "api_key_stored",
    "heartbeat_request",
    "heartbeat_accepted",
    "account_associated",
    "ui_connected",
)

STAGE_LABELS: dict[str, str] = {
    "page_meta_present": "1. Page exposes API key meta",
    "service_worker_alive": "2. Extension service worker alive",
    "extension_saw_setup_page": "3. Extension saw /extension-setup",
    "api_key_captured": "4. Extension captured API key from page",
    "api_key_stored": "5. Extension stored API key locally",
    "heartbeat_request": "6. Heartbeat request sent to Mighty",
    "heartbeat_accepted": "7. Mighty accepted heartbeat",
    "account_associated": "8. Heartbeat associated to this account",
    "ui_connected": "9. UI detection state = connected",
}


def ensure_handshake_table(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS extension_handshake_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            ok INTEGER NOT NULL DEFAULT 1,
            detail TEXT,
            source TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_handshake_user_created "
        "ON extension_handshake_events(user_id, created_at)"
    )
    try:
        db.commit()
    except Exception:
        pass


def _utc_now() -> str:
    return to_utc_iso_z(datetime.now(timezone.utc))


def record_handshake_event(
    db: Any,
    user_id: str,
    stage: str,
    *,
    ok: bool = True,
    detail: str | None = None,
    source: str = "unknown",
) -> dict[str, Any]:
    ensure_handshake_table(db)
    stage = (stage or "").strip()[:80]
    if stage not in HANDSHAKE_STAGES and stage != "note":
        # Allow unknown stages for forward-compat, but keep bounded.
        stage = stage[:80] or "note"
    created = _utc_now()
    db.execute(
        "INSERT INTO extension_handshake_events"
        "(user_id, stage, ok, detail, source, created_at) VALUES (?,?,?,?,?,?)",
        (
            user_id,
            stage,
            1 if ok else 0,
            (detail or "")[:500],
            (source or "unknown")[:40],
            created,
        ),
    )
    db.commit()
    return {
        "stage": stage,
        "ok": ok,
        "detail": detail,
        "source": source,
        "created_at": created,
    }


def recent_handshake_events(
    db: Any,
    user_id: str,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    ensure_handshake_table(db)
    rows = db.execute(
        "SELECT stage, ok, detail, source, created_at FROM extension_handshake_events "
        "WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, max(1, min(limit, 100))),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "stage": r["stage"],
                "ok": bool(r["ok"]),
                "detail": r["detail"] or "",
                "source": r["source"] or "",
                "created_at": r["created_at"],
            }
        )
    return out


def latest_stage_map(
    db: Any,
    user_id: str,
    *,
    within_minutes: int = 60,
) -> dict[str, dict[str, Any] | None]:
    """Latest event per known stage within the window."""
    ensure_handshake_table(db)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    cutoff_iso = to_utc_iso_z(cutoff)
    rows = db.execute(
        "SELECT stage, ok, detail, source, created_at FROM extension_handshake_events "
        "WHERE user_id=? AND created_at>=? ORDER BY id DESC",
        (user_id, cutoff_iso),
    ).fetchall()
    latest: dict[str, dict[str, Any] | None] = {s: None for s in HANDSHAKE_STAGES}
    for r in rows:
        stage = r["stage"]
        if stage not in latest or latest[stage] is not None:
            continue
        latest[stage] = {
            "ok": bool(r["ok"]),
            "detail": r["detail"] or "",
            "source": r["source"] or "",
            "created_at": r["created_at"],
        }
    return latest


def build_diagnostics_payload(
    db: Any,
    user_id: str,
    *,
    api_key: str | None,
    session_email: str | None = None,
) -> dict[str, Any]:
    """Full handshake diagnostic snapshot for the logged-in user."""
    from mighty.extension_version import get_extension_version_status

    ensure_handshake_table(db)
    status = get_extension_version_status(db, user_id)
    raw_seen = status.get("extension_last_seen_at")
    connected = False
    age_seconds = None
    if raw_seen:
        dt = parse_admin_timestamp(raw_seen)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - dt
            age_seconds = int(age.total_seconds())
            connected = age <= timedelta(minutes=30)

    key = (api_key or "").strip()
    key_prefix = (key[:10] + "…") if len(key) > 10 else (key or None)

    latest = latest_stage_map(db, user_id)
    # Derive account_associated / ui_connected from server truth if events missing.
    if connected and latest.get("heartbeat_accepted") is None:
        latest["heartbeat_accepted"] = {
            "ok": True,
            "detail": "inferred from extension_last_seen_at",
            "source": "server",
            "created_at": raw_seen,
        }
    if connected:
        latest["account_associated"] = {
            "ok": True,
            "detail": f"user_id heartbeat maps to session user",
            "source": "server",
            "created_at": raw_seen,
        }
        latest["ui_connected"] = {
            "ok": True,
            "detail": "setup-status connected=true",
            "source": "server",
            "created_at": _utc_now(),
        }

    stages = []
    first_fail = None
    for stage in HANDSHAKE_STAGES:
        ev = latest.get(stage)
        if ev is None:
            state = "unknown"
        elif ev.get("ok"):
            state = "pass"
        else:
            state = "fail"
            if first_fail is None:
                first_fail = stage
        stages.append(
            {
                "id": stage,
                "label": STAGE_LABELS.get(stage, stage),
                "state": state,
                "event": ev,
            }
        )

    # If not connected, locate first unknown after last pass as failure hint.
    if not connected and first_fail is None:
        last_pass_idx = -1
        for i, s in enumerate(stages):
            if s["state"] == "pass":
                last_pass_idx = i
        hint_idx = last_pass_idx + 1
        if 0 <= hint_idx < len(stages) and stages[hint_idx]["state"] == "unknown":
            first_fail = stages[hint_idx]["id"]

    return {
        "connected": connected,
        "extension_version": status.get("extension_version"),
        "extension_expected_version": status.get("extension_expected_version"),
        "extension_build_id": status.get("extension_build_id"),
        "extension_expected_build_id": status.get("extension_expected_build_id"),
        "extension_build_match": status.get("extension_build_match"),
        "extension_last_seen_at": raw_seen,
        "extension_last_seen_age_seconds": age_seconds,
        "api_key_prefix": key_prefix,
        "session_email": session_email,
        "user_id": user_id,
        "stages": stages,
        "first_failure_stage": first_fail,
        "recent_events": recent_handshake_events(db, user_id, limit=25),
        "mighty_url_hint": "Extension MIGHTY_URL must match this site origin",
    }
