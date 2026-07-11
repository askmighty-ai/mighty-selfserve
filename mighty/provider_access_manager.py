"""Provider Access Manager — canonical production boundary for active access checks.

This is the **only** production entry point for an active provider session / access
check. New production code must not schedule verification, complete verification
probe results, or write ``provider_session_state`` outside this module.

Orchestrates existing components without duplicating them:

- scheduling: ``mighty.session_verification``
- execution result classification: ``mighty.provider_access_probe``
- canonical state persistence: ``mighty.provider_session_state``
- recovery / escalation: callers above this layer (e.g. Recovery Planner)

Passive definitive evidence (authenticated session, login page, session API
200 / 401 / 403) may also enter through the evidence helpers here. Cached
private account data alone must never set current session state.

See ``docs/ACCESS_FLOW.md``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mighty.provider_access_probe import (
    PROBE_LIFECYCLE_DONE,
    PROBE_LIFECYCLE_ERROR,
    PROBE_PROVIDERS,
    complete_manual_probe,
    record_probe_run,
)
from mighty.provider_session_state import (
    ProviderSessionState,
    SessionEvidence,
    derive_session_evidence_from_probe,
    upsert_provider_session_state,
)
from mighty.session_verification import (
    CURRENT_SESSION_FRESHNESS_SECONDS,
    SessionVerification,
    VerificationLifecycle,
    complete_session_verification,
    ensure_provider_session_verification_if_stale,
    ensure_stale_session_verifications_for_user,
    mark_session_verification_running,
    request_session_verification,
)

# Modules allowed to call upsert_provider_session_state in production code.
# Tests may call upsert directly. Do not expand this set without an ACCESS_FLOW update.
APPROVED_PSS_UPSERT_MODULES: frozenset[str] = frozenset(
    {
        "mighty.provider_access_manager",
        "mighty.provider_session_state",
    }
)

# Production Python packages/modules that must not call upsert directly.
# Static guardrail tests enumerate call sites against this policy.
PSS_UPSERT_GUARDRAIL_ROOTS: tuple[str, ...] = (
    "mighty/",
    "app.py",
    "scrape.py",
    "adapters/",
)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _observed_at(value: datetime | str | None) -> datetime:
    if isinstance(value, str):
        return _parse_iso(value) or datetime.now(timezone.utc)
    return value or datetime.now(timezone.utc)


# ── Active verification scheduling ────────────────────────────────────────────


def request_provider_access_check(
    db: Any,
    user_id: str,
    provider: str,
    *,
    now: datetime | None = None,
    throttle_seconds: int | None = None,
) -> SessionVerification | None:
    """Enqueue an active access check (session verification job).

    Preserves session_verification throttle / active-job reuse semantics.
    """
    kwargs: dict[str, Any] = {"now": now}
    if throttle_seconds is not None:
        kwargs["throttle_seconds"] = throttle_seconds
    return request_session_verification(db, user_id, provider, **kwargs)


def ensure_provider_access_check_if_stale(
    db: Any,
    user_id: str,
    provider: str,
    *,
    session_state: ProviderSessionState | None = None,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
) -> SessionVerification | None:
    """Request an access check only when current session evidence is stale."""
    return ensure_provider_session_verification_if_stale(
        db,
        user_id,
        provider,
        session_state=session_state,
        now=now,
        freshness_seconds=freshness_seconds,
    )


def ensure_stale_provider_access_checks(
    db: Any,
    user_id: str,
    *,
    providers: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
) -> dict[str, SessionVerification]:
    """Product trigger: enqueue access checks for all stale probe providers."""
    return ensure_stale_session_verifications_for_user(
        db,
        user_id,
        providers=providers,
        now=now,
        freshness_seconds=freshness_seconds,
    )


def mark_provider_access_check_running(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    now: datetime | None = None,
) -> SessionVerification | None:
    """Mark a pending access check as running (extension claimed the job)."""
    return mark_session_verification_running(
        db, user_id, verification_id, now=now
    )


def finish_provider_access_check(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    lifecycle: VerificationLifecycle = "completed",
    error_message: str | None = None,
    now: datetime | None = None,
) -> None:
    """Terminal lifecycle update for an access check (no PSS write by itself)."""
    complete_session_verification(
        db,
        user_id,
        verification_id,
        lifecycle=lifecycle,
        error_message=error_message,
        now=now,
    )


# ── Probe completion (active verification + debug manual probe) ───────────────


def complete_provider_access_check(
    db: Any,
    user_id: str,
    result: dict[str, Any],
    *,
    verification_id: str | None = None,
    manual_run_id: str | None = None,
) -> dict[str, Any]:
    """Record a successful probe payload and finish related jobs.

    Active session verification (``verification_id`` set) writes PSS only on
    definitive ``connected`` / ``signed_out`` evidence. Manual / automatic probes
    keep their existing write behavior (debug / legacy paths).
    """
    write_session_state = True
    if verification_id:
        evidence = derive_session_evidence_from_probe(result)
        write_session_state = bool(
            evidence is not None and evidence.state in {"connected", "signed_out"}
        )

    run_id = record_probe_run(
        db, user_id, result, write_session_state=write_session_state
    )
    result = dict(result)
    result["run_id"] = run_id

    if manual_run_id:
        lifecycle = (
            PROBE_LIFECYCLE_ERROR
            if result.get("status") == "error"
            else PROBE_LIFECYCLE_DONE
        )
        complete_manual_probe(
            db,
            user_id,
            manual_run_id,
            lifecycle=lifecycle,
            probe_run_id=run_id,
            error_message=(
                result.get("failure_reason") if lifecycle == PROBE_LIFECYCLE_ERROR else None
            ),
        )

    if verification_id:
        if result.get("status") == "error" and not write_session_state:
            finish_provider_access_check(
                db,
                user_id,
                verification_id,
                lifecycle="failed",
                error_message=result.get("failure_reason") or "probe error",
            )
        else:
            finish_provider_access_check(
                db,
                user_id,
                verification_id,
                lifecycle="completed",
            )

    return result


def fail_provider_access_check(
    db: Any,
    user_id: str,
    *,
    error_message: str,
    verification_id: str | None = None,
    manual_run_id: str | None = None,
) -> None:
    """Finish related jobs when probe payload evaluation fails."""
    if manual_run_id:
        complete_manual_probe(
            db,
            user_id,
            manual_run_id,
            lifecycle=PROBE_LIFECYCLE_ERROR,
            error_message=error_message,
        )
    if verification_id:
        finish_provider_access_check(
            db,
            user_id,
            verification_id,
            lifecycle="failed",
            error_message=error_message,
        )


# ── Canonical PSS evidence writes ─────────────────────────────────────────────


def record_provider_access_evidence(
    db: Any,
    user_id: str,
    evidence: SessionEvidence,
) -> ProviderSessionState:
    """Persist session evidence through the Access Manager boundary."""
    return upsert_provider_session_state(db, user_id, evidence)


def record_session_evidence_from_probe(
    db: Any,
    user_id: str,
    result: dict[str, Any],
) -> ProviderSessionState | None:
    """Map probe result → PSS. Inconclusive probes return None (no write)."""
    evidence = derive_session_evidence_from_probe(result)
    if evidence is None:
        return None
    return record_provider_access_evidence(db, user_id, evidence)


def record_amex_extension_connected(
    db: Any,
    user_id: str,
    *,
    observed_at: datetime | str | None = None,
    evidence_type: str = "session_verified",
    evidence_summary: str = "Amex extension reported verified authenticated session",
    source: str = "extension_amex_connected",
) -> ProviderSessionState:
    """Record definitive Amex connected evidence (extension / extract path)."""
    return record_provider_access_evidence(
        db,
        user_id,
        SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type=evidence_type,
            evidence_summary=evidence_summary,
            observed_at=_observed_at(observed_at),
            source=source,
            confidence="high",
        ),
    )


def record_amex_extension_needs_login(
    db: Any,
    user_id: str,
    *,
    observed_at: datetime | str | None = None,
) -> ProviderSessionState:
    """Record definitive Amex signed_out / login-required evidence."""
    return record_provider_access_evidence(
        db,
        user_id,
        SessionEvidence(
            provider="amex",
            state="signed_out",
            evidence_type="login_required",
            evidence_summary="Amex extension reported login required",
            observed_at=_observed_at(observed_at),
            source="extension_amex_needs_login",
            confidence="high",
        ),
    )


def record_extension_login_required(
    db: Any,
    user_id: str,
    provider: str,
    *,
    observed_at: datetime | str | None = None,
    source: str = "extension_sync_failure",
) -> ProviderSessionState | None:
    """Record signed_out evidence for a probe provider (passive / sync failure)."""
    if provider not in PROBE_PROVIDERS:
        return None
    return record_provider_access_evidence(
        db,
        user_id,
        SessionEvidence(
            provider=provider,
            state="signed_out",
            evidence_type="login_required",
            evidence_summary=f"{provider} extension reported login required",
            observed_at=_observed_at(observed_at),
            source=source,
            confidence="high",
        ),
    )


def record_extension_session_connected(
    db: Any,
    user_id: str,
    provider: str,
    *,
    observed_at: datetime | str | None = None,
    evidence_type: str = "session_verified",
    evidence_summary: str | None = None,
    source: str = "extension_login_cleared",
) -> ProviderSessionState | None:
    """Record connected evidence for a probe provider (passive / login-cleared)."""
    if provider not in PROBE_PROVIDERS:
        return None
    return record_provider_access_evidence(
        db,
        user_id,
        SessionEvidence(
            provider=provider,
            state="connected",
            evidence_type=evidence_type,
            evidence_summary=evidence_summary
            or f"{provider} extension reported verified authenticated session",
            observed_at=_observed_at(observed_at),
            source=source,
            confidence="high",
        ),
    )
