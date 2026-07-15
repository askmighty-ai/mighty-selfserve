"""Temporary Amex verification timeline diagnostics (admin observability).

Read-only helpers for:
  GET /api/admin/debug/amex-verification-timeline
  scripts/dump_amex_verification_timeline.py

Does not change selection, persistence, lifecycle, timestamps, or customer copy.
Enable the HTTP endpoint with MIGHTY_AMEX_TIMELINE_DEBUG=1.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from mighty.session_verification import (
    ACTIVE_VERIFICATION_LIFECYCLES,
    TERMINAL_VERIFICATION_LIFECYCLES,
)

PresentationPhase = Literal["determining", "terminal"]


def diagnostics_enabled() -> bool:
    """Stdout candidate dumps for the dump script / explicit admin calls."""
    flag = (os.environ.get("MIGHTY_VERIFICATION_TIMELINE_DIAG") or "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _log(event: str, **fields: Any) -> None:
    if not diagnostics_enabled():
        return
    parts = [f"[verification_timeline] event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            parts.append(f"{key}={json.dumps(value, default=str, separators=(',', ':'))}")
        else:
            parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


@dataclass(frozen=True)
class VerificationCandidate:
    verification_id: str
    access_cycle_id: str | None
    lifecycle: str | None
    capability_state: str | None
    requested_at: str | None
    started_at: str | None
    completed_at: str | None
    customer_published_at: str | None
    created_at: str | None
    is_active: bool
    is_terminal: bool
    include_exclude_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "access_cycle_id": self.access_cycle_id,
            "lifecycle": self.lifecycle,
            "capability_state": self.capability_state,
            "requested_at": self.requested_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "customer_published_at": self.customer_published_at,
            "creation_timestamp": self.created_at or self.requested_at,
            "is_active": self.is_active,
            "is_terminal": self.is_terminal,
            "why": self.include_exclude_reason,
        }


def _row_get(row: Any, key: str, idx: int | None = None) -> Any:
    if row is None:
        return None
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None
    if idx is not None:
        try:
            return row[idx]
        except (IndexError, TypeError):
            return None
    return None


def _sort_key(candidate: VerificationCandidate) -> tuple:
    return (
        candidate.completed_at or "",
        candidate.started_at or "",
        candidate.requested_at or "",
        candidate.verification_id or "",
    )


def select_presentation_cycles(
    candidates: Sequence[VerificationCandidate],
    *,
    provider: str,
    published_verification_id: str | None = None,
) -> dict[str, Any]:
    """Deterministic diagnostic selector mirroring intended presentation choice.

    Returns presentation_selection with active / terminal / historical picks and
    reasons. Does not mutate state.
    """
    active = None
    for cand in candidates:
        if cand.is_active:
            active = cand
            break

    terminals = sorted(
        [c for c in candidates if c.is_terminal],
        key=_sort_key,
        reverse=True,
    )

    historical = None
    terminal = None
    phase: PresentationPhase
    reasons: dict[str, str] = {}

    if active is not None:
        phase = "determining"
        reasons["active_verification"] = (
            f"newest active lifecycle={active.lifecycle} "
            f"verification_id={active.verification_id}"
        )
        for cand in terminals:
            if cand.verification_id == active.verification_id:
                continue
            historical = cand
            reasons["historical_verification"] = (
                f"prior terminal for determining face "
                f"verification_id={cand.verification_id} "
                f"completed_at={cand.completed_at}"
            )
            break
        if historical is None and published_verification_id:
            for cand in candidates:
                if cand.verification_id == published_verification_id:
                    historical = cand
                    reasons["historical_verification"] = (
                        "fallback to customer_capability_presentation "
                        f"verification_id={published_verification_id}"
                    )
                    break
        reasons["terminal_verification"] = "none while active"
    else:
        phase = "terminal"
        reasons["active_verification"] = "none"
        if terminals:
            terminal = terminals[0]
            reasons["terminal_verification"] = (
                f"newest terminal by completed_at/started_at/requested_at "
                f"verification_id={terminal.verification_id} "
                f"completed_at={terminal.completed_at}"
            )
            if len(terminals) > 1:
                historical = terminals[1]
                reasons["historical_verification"] = (
                    f"second-newest terminal "
                    f"verification_id={historical.verification_id}"
                )
            else:
                reasons["historical_verification"] = "none"
        else:
            reasons["terminal_verification"] = "no terminal rows"
            reasons["historical_verification"] = "none"
            if published_verification_id:
                for cand in candidates:
                    if cand.verification_id == published_verification_id:
                        terminal = cand
                        reasons["terminal_verification"] = (
                            "only published stable card available "
                            f"verification_id={published_verification_id}"
                        )
                        break

    selection = {
        "provider": provider,
        "active_verification": active.to_dict() if active else None,
        "terminal_verification": terminal.to_dict() if terminal else None,
        "historical_verification": historical.to_dict() if historical else None,
        "presentation_phase": phase,
        "reason_for_each_choice": reasons,
    }
    _log("presentation_selection", **selection)
    return selection


def load_amex_verification_candidates(
    db: Any,
    user_id: str,
    *,
    provider: str = "amex",
) -> list[VerificationCandidate]:
    """Load every verification row considered for presentation selection."""
    published_by_vid: dict[str, str] = {}
    published_vid = None
    published_at = None
    published_state = None
    try:
        prow = db.execute(
            """
            SELECT verification_id, updated_at, capability_state,
                   verification_completed_at
            FROM customer_capability_presentation
            WHERE user_id = ? AND provider = ?
            """,
            (user_id, provider),
        ).fetchone()
        if prow is not None:
            published_vid = _row_get(prow, "verification_id", 0)
            published_at = _row_get(prow, "updated_at", 1)
            published_state = _row_get(prow, "capability_state", 2)
            if published_vid:
                published_by_vid[str(published_vid)] = str(published_at or "")
    except Exception:  # noqa: BLE001 — table may be absent
        pass

    rows: list[Any] = []
    try:
        rows = db.execute(
            """
            SELECT verification_id, lifecycle, requested_at, started_at,
                   completed_at, terminal_reason, error_message
            FROM provider_session_verification
            WHERE user_id = ? AND provider = ?
            ORDER BY COALESCE(requested_at, '') ASC,
                     COALESCE(started_at, '') ASC,
                     COALESCE(completed_at, '') ASC
            """,
            (user_id, provider),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        _log(
            "verification_table_unavailable",
            provider=provider,
            user_id=user_id,
            error=str(exc),
        )
        return []

    candidates: list[VerificationCandidate] = []
    newest_active_id = None
    for row in rows:
        lifecycle = str(_row_get(row, "lifecycle", 1) or "")
        if lifecycle in ACTIVE_VERIFICATION_LIFECYCLES:
            newest_active_id = str(_row_get(row, "verification_id", 0))

    for row in rows:
        vid = str(_row_get(row, "verification_id", 0) or "")
        lifecycle = str(_row_get(row, "lifecycle", 1) or "")
        requested_at = _row_get(row, "requested_at", 2)
        started_at = _row_get(row, "started_at", 3)
        completed_at = _row_get(row, "completed_at", 4)
        is_active = lifecycle in ACTIVE_VERIFICATION_LIFECYCLES
        is_terminal = lifecycle in TERMINAL_VERIFICATION_LIFECYCLES
        why = "included_as_candidate"
        if is_active and newest_active_id and vid != newest_active_id:
            why = "excluded_duplicate_active_not_newest"
            is_active = False
        elif is_active:
            why = "included_as_active"
        elif is_terminal:
            why = "included_as_terminal"
        else:
            why = f"included_unknown_lifecycle:{lifecycle or 'empty'}"

        if published_vid and vid == published_vid:
            why = f"{why};matches_published_stable_card"

        candidates.append(
            VerificationCandidate(
                verification_id=vid,
                access_cycle_id=vid,
                lifecycle=lifecycle or None,
                capability_state=(
                    published_state if published_vid == vid else None
                ),
                requested_at=str(requested_at) if requested_at else None,
                started_at=str(started_at) if started_at else None,
                completed_at=str(completed_at) if completed_at else None,
                customer_published_at=published_by_vid.get(vid) or None,
                created_at=str(requested_at) if requested_at else None,
                is_active=is_active,
                is_terminal=is_terminal,
                include_exclude_reason=why,
            )
        )

    for cand in candidates:
        _log("verification_candidate", provider=provider, user_id=user_id, **cand.to_dict())

    return candidates


def log_select_presentation_cycles(
    db: Any,
    user_id: str,
    *,
    provider: str = "amex",
) -> dict[str, Any]:
    """Log every Amex verification considered and the selector decision."""
    candidates = load_amex_verification_candidates(db, user_id, provider=provider)
    published_vid = None
    try:
        prow = db.execute(
            """
            SELECT verification_id FROM customer_capability_presentation
            WHERE user_id = ? AND provider = ?
            """,
            (user_id, provider),
        ).fetchone()
        if prow is not None:
            published_vid = _row_get(prow, "verification_id", 0)
    except Exception:  # noqa: BLE001
        published_vid = None
    return select_presentation_cycles(
        candidates,
        provider=provider,
        published_verification_id=str(published_vid) if published_vid else None,
    )


def log_lifecycle_transition(
    *,
    verification_id: str,
    previous_lifecycle: str | None,
    new_lifecycle: str,
    access_cycle_id: str | None = None,
    provider: str | None = None,
    timestamp: str | None = None,
    **extra: Any,
) -> None:
    """Log one verification lifecycle transition (investigation only)."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    event_name = {
        "requested": "verification_requested",
        "running": "verification_started",
        "session_verified": "session_verified",
        "extracting": "extracting",
        "completed": "completed",
        "failed": "failed",
        "timed_out": "timed_out",
        "cancelled": "cancelled",
    }.get(new_lifecycle, f"lifecycle_{new_lifecycle}")
    # customer_published is presentation-side; callers may pass new_lifecycle alias.
    if new_lifecycle == "customer_published":
        event_name = "customer_published"
    _log(
        event_name,
        verification_id=verification_id,
        previous_lifecycle=previous_lifecycle,
        new_lifecycle=new_lifecycle,
        timestamp=ts,
        access_cycle_id=access_cycle_id or verification_id,
        provider=provider,
        **extra,
    )


def dump_verification_table(
    db: Any,
    user_id: str,
    *,
    provider: str = "amex",
) -> list[dict[str, Any]]:
    """Dump verification rows ordered by requested_at, started_at, completed_at."""
    rows = db.execute(
        """
        SELECT verification_id, lifecycle, requested_at, started_at, completed_at,
               terminal_reason, terminal_source, error_message
        FROM provider_session_verification
        WHERE user_id = ? AND provider = ?
        ORDER BY COALESCE(requested_at, '') ASC,
                 COALESCE(started_at, '') ASC,
                 COALESCE(completed_at, '') ASC
        """,
        (user_id, provider),
    ).fetchall()
    dump: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "verification_id": _row_get(row, "verification_id", 0),
            "lifecycle": _row_get(row, "lifecycle", 1),
            "requested_at": _row_get(row, "requested_at", 2),
            "started_at": _row_get(row, "started_at", 3),
            "completed_at": _row_get(row, "completed_at", 4),
            "terminal_reason": _row_get(row, "terminal_reason", 5),
            "terminal_source": _row_get(row, "terminal_source", 6),
            "error_message": _row_get(row, "error_message", 7),
        }
        dump.append(item)
    _log(
        "verification_table_dump",
        provider=provider,
        user_id=user_id,
        order="requested_at,started_at,completed_at",
        rows=dump,
    )

    try:
        prow = db.execute(
            """
            SELECT verification_id, access_cycle_id, capability_state,
                   verification_completed_at, updated_at, lifecycle,
                   payload_json
            FROM customer_capability_presentation
            WHERE user_id = ? AND provider = ?
            """,
            (user_id, provider),
        ).fetchone()
    except Exception:  # noqa: BLE001
        prow = None
    if prow is not None:
        payload_raw = _row_get(prow, "payload_json", 6)
        payload_last_verified = None
        payload_phase = None
        payload_summary = None
        if payload_raw:
            try:
                payload = json.loads(payload_raw)
                payload_last_verified = payload.get("last_verified")
                payload_phase = payload.get("presentation_phase")
                payload_summary = payload.get("historical_summary") or payload.get(
                    "headline"
                )
            except (TypeError, json.JSONDecodeError):
                pass
        _log(
            "published_presentation_dump",
            provider=provider,
            user_id=user_id,
            verification_id=_row_get(prow, "verification_id", 0),
            access_cycle_id=_row_get(prow, "access_cycle_id", 1),
            capability_state=_row_get(prow, "capability_state", 2),
            verification_completed_at=_row_get(prow, "verification_completed_at", 3),
            customer_published_at=_row_get(prow, "updated_at", 4),
            lifecycle=_row_get(prow, "lifecycle", 5),
            payload_last_verified=payload_last_verified,
            payload_presentation_phase=payload_phase,
            payload_headline_or_summary=payload_summary,
        )
    return dump


# ── Production investigation report (sanitized, read-only) ───────────────────

# July 14, 2026 10:30 AM – 4:00 PM America/Los_Angeles (PDT, UTC-7).
WINDOW_START_UTC = "2026-07-14T17:30:00+00:00"
WINDOW_END_UTC = "2026-07-14T23:00:00+00:00"
DISPLAY_1129_UTC = "2026-07-14T18:29:00+00:00"


def timeline_debug_enabled() -> bool:
    """Explicit opt-in for the temporary admin timeline endpoint."""
    flag = (os.environ.get("MIGHTY_AMEX_TIMELINE_DEBUG") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def deployment_sha() -> str:
    """Deployment identity from safe env metadata only (never git in-container).

    Preference order:
      1. RAILWAY_GIT_COMMIT_SHA
      2. SOURCE_VERSION
      3. COMMIT_SHA
      4. \"unknown\"
    """
    for key in (
        "RAILWAY_GIT_COMMIT_SHA",
        "SOURCE_VERSION",
        "COMMIT_SHA",
    ):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value[:12] if len(value) > 12 else value
    return "unknown"


def sanitized_datastore_metadata() -> dict[str, Any]:
    """Connection metadata only — no credentials or full paths with secrets."""
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    database_path = (os.environ.get("DATABASE_PATH") or "").strip()
    if database_url:
        engine = "postgres" if database_url.lower().startswith("postgres") else "url"
        host_class = "managed_url"
        if "@" in database_url:
            after_at = database_url.split("@", 1)[-1]
            host = after_at.split("/", 1)[0].split(":", 1)[0]
            if "railway" in host.lower():
                host_class = "railway_postgres_host"
            elif "localhost" in host.lower() or host in {"127.0.0.1", "::1"}:
                host_class = "localhost"
            else:
                host_class = "external_host"
        basename = None
    else:
        engine = "sqlite"
        host_class = "sqlite_file"
        path = database_path or "/app/data/mighty.db"
        basename = os.path.basename(path) or "mighty.db"
    volume_hint = None
    if database_path:
        if database_path.startswith("/app/data"):
            volume_hint = "likely_persistent_volume_/app/data"
        elif database_path.startswith("/app/"):
            volume_hint = "app_filesystem_may_be_ephemeral"
    return {
        "engine": engine,
        "database_path_basename": basename,
        "database_host_class": host_class,
        "railway_environment": os.environ.get("RAILWAY_ENVIRONMENT") or None,
        "railway_service_name": os.environ.get("RAILWAY_SERVICE_NAME")
        or os.environ.get("RAILWAY_SERVICE")
        or None,
        "railway_deployment_id_prefix": (
            (os.environ.get("RAILWAY_DEPLOYMENT_ID") or "")[:8] or None
        ),
        "storage_hint": volume_hint,
        "deployment_sha": deployment_sha(),
    }


def error_message_code_only(error_message: str | None) -> str | None:
    """Strip free-text / PII; keep a short machine code when present."""
    if not error_message:
        return None
    text = str(error_message).strip()
    if not text:
        return None
    # Prefer underscore/kebab machine codes at the start.
    token = text.split()[0].strip(",:;.")
    if re.fullmatch(r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+", token, flags=re.I):
        return token[:64]
    if re.fullmatch(r"[a-z][a-z0-9_]{2,64}", token, flags=re.I) and "_" in token:
        return token[:64]
    lowered = text.lower()
    for code in (
        "timeout",
        "timed_out",
        "signed_out",
        "login_required",
        "needs_login",
        "navigation_failed",
        "cancelled",
        "canceled",
        "tab_creation_blocked",
        "probe_navigation_error",
        "no_entry_url",
        "rate_limited",
        "captcha",
        "mfa",
        "unknown",
    ):
        if code in lowered:
            return code
    return "redacted_non_code"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _in_window(
    *timestamps: str | None,
    start: datetime,
    end: datetime,
) -> bool:
    for ts in timestamps:
        dt = _parse_ts(ts)
        if dt is not None and start <= dt <= end:
            return True
    return False


def _duration_ms(
    requested_at: str | None,
    completed_at: str | None,
) -> int | None:
    start = _parse_ts(requested_at)
    end = _parse_ts(completed_at)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _fingerprint_prefix(value: str | None, *, n: int = 8) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:n]


def load_persisted_presentation_sanitized(
    db: Any,
    user_id: str,
    *,
    provider: str = "amex",
) -> dict[str, Any] | None:
    """Customer presentation row — metadata only, no private account values."""
    try:
        row = db.execute(
            """
            SELECT verification_id, access_cycle_id, capability_state, lifecycle,
                   terminal_reason, verification_completed_at, updated_at,
                   account_identity, payload_json
            FROM customer_capability_presentation
            WHERE user_id = ? AND provider = ?
            """,
            (user_id, provider),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None

    payload_last_verified = None
    payload_phase = None
    payload_summary_kind = None
    payload_timestamp_label = None
    verification_started_at = None
    if _row_get(row, "payload_json", 8):
        try:
            payload = json.loads(_row_get(row, "payload_json", 8))
            if isinstance(payload, dict):
                payload_last_verified = payload.get("last_verified")
                payload_phase = payload.get("presentation_phase")
                hist = payload.get("historical_summary") or ""
                head = payload.get("headline") or ""
                # Kind only — never return free-form extracted account text.
                if "Signed out" in str(hist) or "Signed out" in str(head):
                    payload_summary_kind = "signed_out_last_confirmed"
                elif hist or head:
                    payload_summary_kind = "other_customer_copy"
                payload_timestamp_label = payload.get("timestamp_label")
                verification_started_at = (
                    payload.get("current_check_started_at")
                    or payload.get("verification_started_at")
                )
        except (TypeError, json.JSONDecodeError):
            pass

    return {
        "verification_id": _row_get(row, "verification_id", 0),
        "access_cycle_id": _row_get(row, "access_cycle_id", 1),
        "capability_state": _row_get(row, "capability_state", 2),
        "lifecycle": _row_get(row, "lifecycle", 3),
        "terminal_reason": _row_get(row, "terminal_reason", 4),
        "verification_started_at": verification_started_at,
        "verification_completed_at": _row_get(row, "verification_completed_at", 5),
        "updated_at": _row_get(row, "updated_at", 6),
        "account_identity_fingerprint_prefix": _fingerprint_prefix(
            _row_get(row, "account_identity", 7)
        ),
        "payload_last_verified": payload_last_verified,
        "payload_presentation_phase": payload_phase,
        "payload_timestamp_label": payload_timestamp_label,
        "payload_summary_kind": payload_summary_kind,
    }


def build_presentation_selection_record(
    db: Any,
    user_id: str,
    *,
    provider: str = "amex",
    surface: str,
    request_timestamp: str | None = None,
) -> dict[str, Any]:
    """Sanitized selector snapshot for Dashboard or /api/account-status."""
    candidates = load_amex_verification_candidates(db, user_id, provider=provider)
    published = load_persisted_presentation_sanitized(db, user_id, provider=provider)
    published_vid = (published or {}).get("verification_id")
    selection = select_presentation_cycles(
        candidates,
        provider=provider,
        published_verification_id=str(published_vid) if published_vid else None,
    )
    active = selection.get("active_verification") or {}
    terminal = selection.get("terminal_verification") or {}
    historical = selection.get("historical_verification") or {}

    selected_completed_at = (
        terminal.get("completed_at")
        or historical.get("completed_at")
        or (published or {}).get("payload_last_verified")
        or (published or {}).get("verification_completed_at")
    )
    selected_capability = (
        terminal.get("capability_state")
        or historical.get("capability_state")
        or (published or {}).get("capability_state")
    )
    timestamp_source = None
    if terminal.get("completed_at"):
        timestamp_source = "selector_terminal.completed_at"
    elif historical.get("completed_at"):
        timestamp_source = "selector_historical.completed_at"
    elif (published or {}).get("payload_last_verified"):
        timestamp_source = "persisted_presentation.payload_last_verified"
    elif (published or {}).get("verification_completed_at"):
        timestamp_source = "persisted_presentation.verification_completed_at"

    return {
        "surface": surface,
        "deployment_sha": deployment_sha(),
        "request_timestamp": request_timestamp
        or datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "candidate_verification_ids": [c.verification_id for c in candidates],
        "candidates": [
            {
                "verification_id": c.verification_id,
                "lifecycle": c.lifecycle,
                "completed_at": c.completed_at,
                "is_active": c.is_active,
                "is_terminal": c.is_terminal,
                "why": c.include_exclude_reason,
            }
            for c in candidates
        ],
        "active_verification_id": active.get("verification_id"),
        "selected_terminal_verification_id": terminal.get("verification_id"),
        "previous_verification_id": historical.get("verification_id"),
        "presentation_phase": selection.get("presentation_phase"),
        "selected_capability": selected_capability,
        "selected_timestamp_source": timestamp_source,
        "selected_completed_at": selected_completed_at,
        "persisted_presentation_verification_id": published_vid,
        "reason_for_each_choice": selection.get("reason_for_each_choice"),
    }


def build_enriched_verification_rows(
    db: Any,
    user_id: str,
    *,
    provider: str = "amex",
    window_start: str = WINDOW_START_UTC,
    window_end: str = WINDOW_END_UTC,
) -> dict[str, Any]:
    """Verification rows with selector flags; window-filtered + full count."""
    from mighty.session_verification import (
        ACTIVE_VERIFICATION_LIFECYCLES,
        TERMINAL_VERIFICATION_LIFECYCLES,
    )

    selection = log_select_presentation_cycles(db, user_id, provider=provider)
    active_id = ((selection.get("active_verification") or {}) or {}).get(
        "verification_id"
    )
    terminal_id = ((selection.get("terminal_verification") or {}) or {}).get(
        "verification_id"
    )
    published = load_persisted_presentation_sanitized(db, user_id, provider=provider)
    published_vid = (published or {}).get("verification_id")

    try:
        rows = db.execute(
            """
            SELECT verification_id, lifecycle, requested_at, started_at, completed_at,
                   terminal_reason, terminal_source, error_message
            FROM provider_session_verification
            WHERE user_id = ? AND provider = ?
            ORDER BY COALESCE(requested_at, '') ASC,
                     COALESCE(started_at, '') ASC,
                     COALESCE(completed_at, '') ASC
            """,
            (user_id, provider),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return {
            "error": f"verification_table_unavailable:{type(exc).__name__}",
            "window_rows": [],
            "all_row_count": 0,
        }

    start = _parse_ts(window_start) or datetime(2026, 7, 14, 17, 30, tzinfo=timezone.utc)
    end = _parse_ts(window_end) or datetime(2026, 7, 14, 23, 0, tzinfo=timezone.utc)

    enriched: list[dict[str, Any]] = []
    for row in rows:
        vid = str(_row_get(row, "verification_id", 0) or "")
        lifecycle = str(_row_get(row, "lifecycle", 1) or "")
        requested_at = _row_get(row, "requested_at", 2)
        started_at = _row_get(row, "started_at", 3)
        completed_at = _row_get(row, "completed_at", 4)
        item = {
            "verification_id": vid,
            "access_cycle_id": vid,  # cycle id == verification id in this schema
            "lifecycle": lifecycle or None,
            "terminal_reason": _row_get(row, "terminal_reason", 5),
            "terminal_source": _row_get(row, "terminal_source", 6),
            "requested_at": requested_at,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": _duration_ms(
                str(requested_at) if requested_at else None,
                str(completed_at) if completed_at else None,
            ),
            "error_message_code": error_message_code_only(
                _row_get(row, "error_message", 7)
            ),
            "selected_as_active": bool(active_id and vid == active_id),
            "selected_as_latest_terminal": bool(terminal_id and vid == terminal_id),
            "customer_presentation_exists_for_it": bool(
                published_vid and vid == published_vid
            ),
            "is_active_lifecycle": lifecycle in ACTIVE_VERIFICATION_LIFECYCLES,
            "is_terminal_lifecycle": lifecycle in TERMINAL_VERIFICATION_LIFECYCLES,
        }
        enriched.append(item)

    window_rows = [
        r
        for r in enriched
        if _in_window(
            r.get("requested_at"),
            r.get("started_at"),
            r.get("completed_at"),
            start=start,
            end=end,
        )
    ]
    return {
        "window_start_utc": window_start,
        "window_end_utc": window_end,
        "window_rows": window_rows,
        "all_rows": enriched,
        "all_row_count": len(enriched),
        "window_row_count": len(window_rows),
    }


def compare_clocks_for_1129_display(
    *,
    verifications: list[dict[str, Any]],
    presentation: dict[str, Any] | None,
    extension: dict[str, Any] | None,
    server_time: str,
    deployment: str,
) -> dict[str, Any]:
    """Classify the source of a displayed 11:29 AM card at ~3:36 PM."""
    display_target = _parse_ts(DISPLAY_1129_UTC)
    matching = []
    for row in verifications:
        completed = _parse_ts(row.get("completed_at"))
        if completed is None or display_target is None:
            continue
        if abs((completed - display_target).total_seconds()) <= 60:
            matching.append(row)

    newest_terminal = None
    for row in reversed(verifications):
        if row.get("is_terminal_lifecycle") and row.get("completed_at"):
            newest_terminal = row
            break

    newest_any = verifications[-1] if verifications else None
    after_1129 = [
        r
        for r in verifications
        if (_parse_ts(r.get("requested_at")) or _parse_ts(r.get("completed_at")))
        and (
            (_parse_ts(r.get("requested_at")) or _parse_ts("1970-01-01T00:00:00+00:00"))
            > display_target
            if display_target and _parse_ts(r.get("requested_at"))
            else (
                _parse_ts(r.get("completed_at")) > display_target
                if display_target and _parse_ts(r.get("completed_at"))
                else False
            )
        )
    ]

    payload_lv = (presentation or {}).get("payload_last_verified")
    payload_dt = _parse_ts(payload_lv)
    hypotheses: list[str] = []

    if (
        newest_terminal
        and display_target
        and _parse_ts(newest_terminal.get("completed_at"))
        and abs(
            (
                _parse_ts(newest_terminal.get("completed_at")) - display_target
            ).total_seconds()
        )
        <= 60
    ):
        hypotheses.append("A_true_completion_of_newest_verification")
    if matching and newest_terminal and matching[0].get("verification_id") != (
        newest_terminal or {}
    ).get("verification_id"):
        hypotheses.append("B_older_verification_incorrectly_selected")
    if (
        payload_dt
        and display_target
        and abs((payload_dt - display_target).total_seconds()) <= 60
        and newest_terminal
        and _parse_ts(newest_terminal.get("completed_at"))
        and _parse_ts(newest_terminal.get("completed_at")) > display_target
    ):
        hypotheses.append("C_stale_persisted_presentation_timestamp")
    if payload_lv and "18:29" in str(payload_lv) and "11:29" not in str(payload_lv):
        # Displaying local 11:29 from UTC 18:29 is correct TZ conversion, not an error.
        pass
    elif payload_lv and display_target is None:
        hypotheses.append("D_timezone_conversion_error")
    if not after_1129:
        hypotheses.append("E_no_newer_verification_created")
    if after_1129 and any(r.get("is_active_lifecycle") for r in after_1129):
        hypotheses.append("F_newer_verifications_still_active")
    if after_1129 and any(
        (r.get("lifecycle") or "") in {"timed_out", "failed", "cancelled"}
        for r in after_1129
    ):
        hypotheses.append("F_newer_verifications_timed_out_or_failed")
    # G requires comparing request SHA to known good — surface for operator.
    hypotheses.append("G_check_deployment_sha_against_expected")

    primary = None
    for preferred in (
        "C_stale_persisted_presentation_timestamp",
        "B_older_verification_incorrectly_selected",
        "A_true_completion_of_newest_verification",
        "E_no_newer_verification_created",
        "F_newer_verifications_still_active",
        "F_newer_verifications_timed_out_or_failed",
    ):
        if preferred in hypotheses:
            primary = preferred
            break

    return {
        "browser_local_display_time_observed": "11:29 AM (operator report)",
        "canonical_matching_verification_completed_at": [
            {
                "verification_id": m.get("verification_id"),
                "completed_at": m.get("completed_at"),
                "lifecycle": m.get("lifecycle"),
                "terminal_reason": m.get("terminal_reason"),
            }
            for m in matching
        ],
        "presentation_row_updated_at": (presentation or {}).get("updated_at"),
        "presentation_payload_last_verified": payload_lv,
        "presentation_verification_completed_at": (presentation or {}).get(
            "verification_completed_at"
        ),
        "latest_extension_heartbeat": (extension or {}).get("extension_last_seen_at"),
        "latest_extension_version": (extension or {}).get("extension_version"),
        "latest_verification_requested_at": (newest_any or {}).get("requested_at"),
        "latest_verification_started_at": (newest_any or {}).get("started_at"),
        "latest_verification_completed_at": (newest_any or {}).get("completed_at"),
        "newest_terminal_verification_id": (newest_terminal or {}).get(
            "verification_id"
        ),
        "newest_terminal_completed_at": (newest_terminal or {}).get("completed_at"),
        "verifications_after_1129_count": len(after_1129),
        "verifications_after_1129_ids": [
            r.get("verification_id") for r in after_1129
        ],
        "server_request_time": server_time,
        "deployment_sha": deployment,
        "hypothesis_candidates": hypotheses,
        "primary_hypothesis": primary,
    }


def build_amex_verification_timeline_report(
    db: Any,
    user_id: str,
    *,
    provider: str = "amex",
    request_timestamp: str | None = None,
) -> dict[str, Any]:
    """Full sanitized production investigation payload (read-only)."""
    server_time = request_timestamp or datetime.now(timezone.utc).isoformat()
    sha = deployment_sha()
    datastore = sanitized_datastore_metadata()

    verification_block = build_enriched_verification_rows(
        db, user_id, provider=provider
    )
    presentation = load_persisted_presentation_sanitized(
        db, user_id, provider=provider
    )

    dashboard_selection = build_presentation_selection_record(
        db,
        user_id,
        provider=provider,
        surface="dashboard",
        request_timestamp=server_time,
    )
    api_selection = build_presentation_selection_record(
        db,
        user_id,
        provider=provider,
        surface="api_account_status",
        request_timestamp=server_time,
    )
    selections_match = (
        dashboard_selection.get("active_verification_id")
        == api_selection.get("active_verification_id")
        and dashboard_selection.get("selected_terminal_verification_id")
        == api_selection.get("selected_terminal_verification_id")
        and dashboard_selection.get("previous_verification_id")
        == api_selection.get("previous_verification_id")
        and dashboard_selection.get("presentation_phase")
        == api_selection.get("presentation_phase")
        and dashboard_selection.get("persisted_presentation_verification_id")
        == api_selection.get("persisted_presentation_verification_id")
        and dashboard_selection.get("selected_completed_at")
        == api_selection.get("selected_completed_at")
    )

    extension: dict[str, Any] | None
    try:
        from mighty.extension_version import get_extension_version_status

        extension = get_extension_version_status(db, user_id)
    except Exception:  # noqa: BLE001
        extension = {
            "extension_version": None,
            "extension_last_seen_at": None,
            "error": "extension_status_unavailable",
        }

    clocks = compare_clocks_for_1129_display(
        verifications=verification_block.get("all_rows") or [],
        presentation=presentation,
        extension=extension,
        server_time=server_time,
        deployment=sha,
    )

    return {
        "ok": True,
        "investigation": "amex_verification_timeline",
        "sanitized": True,
        "user_id": user_id,
        "provider": provider,
        "server_time": server_time,
        "deployment_sha": sha,
        "datastore": datastore,
        "verifications": verification_block,
        "persisted_presentation": presentation,
        "presentation_selection": {
            "dashboard": dashboard_selection,
            "api_account_status": api_selection,
            "dashboard_and_api_select_same_records": selections_match,
            "note": (
                "Dashboard HTML and /api/account-status both apply presentation "
                "via load_all_account_statuses → _apply_stable_customer_capability; "
                "this diagnostic selector reads the same verification + presentation "
                "tables without mutating state."
            ),
        },
        "extension": extension,
        "clock_comparison": clocks,
    }
