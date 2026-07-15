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
    decide_amex_verification_session,
    derive_session_evidence_from_probe,
    upsert_provider_session_state,
    verification_decision_to_evidence,
)
from mighty.session_verification import (
    CURRENT_SESSION_FRESHNESS_SECONDS,
    READY_REVALIDATION_INTERVAL_SECONDS,
    READY_RESULT_GRACE_SECONDS,
    SessionVerification,
    TERMINAL_VERIFICATION_LIFECYCLES,
    VerificationLifecycle,
    VerificationTerminalReason,
    advance_session_verification,
    complete_session_verification,
    ensure_provider_session_verification_if_stale,
    ensure_session_verification_tables,
    ensure_stale_session_verifications_for_user,
    log_access_cycle_event,
    mark_session_verification_running,
    normalize_trigger_source,
    request_session_verification,
    terminal_reason_from_error_message,
)

# Providers whose authenticated access cycle must also extract private data
# before the verification job may complete successfully.
ACCESS_CYCLE_EXTRACTION_PROVIDERS: frozenset[str] = frozenset({"amex"})

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


def request_provider_verification(
    db: Any,
    user_id: str,
    provider: str,
    trigger_source: str,
    requested_by: str | None = None,
    *,
    now: datetime | None = None,
    throttle_seconds: int | None = None,
) -> SessionVerification | None:
    """Canonical command-side verification enqueue.

    Reuses the current active verification when one exists; otherwise creates
    exactly one. Records trigger_source. Idempotent under concurrent requests.

    Allowed trigger_source values:
      user_check_now, scheduled_recheck, extension_startup,
      provider_page_observed, internal_recovery, admin_debug

    Forbidden: dashboard_reload, account_status_poll (and other GET-implied sources).
    """
    source = normalize_trigger_source(trigger_source)
    kwargs: dict[str, Any] = {
        "now": now,
        "trigger_source": source,
        "requested_by": requested_by,
    }
    if throttle_seconds is not None:
        kwargs["throttle_seconds"] = throttle_seconds
    return request_session_verification(db, user_id, provider, **kwargs)


def request_provider_access_check(
    db: Any,
    user_id: str,
    provider: str,
    *,
    now: datetime | None = None,
    throttle_seconds: int | None = None,
    trigger_source: str = "internal_recovery",
    requested_by: str | None = None,
) -> SessionVerification | None:
    """Enqueue an active access check (session verification job).

    Compatibility wrapper around ``request_provider_verification``.
    """
    return request_provider_verification(
        db,
        user_id,
        provider,
        trigger_source=trigger_source,
        requested_by=requested_by,
        now=now,
        throttle_seconds=throttle_seconds,
    )


def ensure_provider_access_check_if_stale(
    db: Any,
    user_id: str,
    provider: str,
    *,
    session_state: ProviderSessionState | None = None,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
    trigger_source: str = "scheduled_recheck",
    requested_by: str | None = None,
) -> SessionVerification | None:
    """Request an access check only when current session evidence is stale."""
    return ensure_provider_session_verification_if_stale(
        db,
        user_id,
        provider,
        session_state=session_state,
        now=now,
        freshness_seconds=freshness_seconds,
        trigger_source=trigger_source,
        requested_by=requested_by,
    )


def ensure_stale_provider_access_checks(
    db: Any,
    user_id: str,
    *,
    providers: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
    trigger_source: str = "scheduled_recheck",
    requested_by: str | None = None,
) -> dict[str, SessionVerification]:
    """Scheduled/command trigger: enqueue access checks for stale probe providers.

    Must not be called from customer-facing GET endpoints.
    """
    return ensure_stale_session_verifications_for_user(
        db,
        user_id,
        providers=providers,
        now=now,
        freshness_seconds=freshness_seconds,
        trigger_source=trigger_source,
        requested_by=requested_by,
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


def advance_provider_access_check(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    lifecycle: VerificationLifecycle,
    error_message: str | None = None,
    now: datetime | None = None,
) -> SessionVerification | None:
    """Advance an access cycle to session_verified or extracting."""
    before = db.execute(
        """
        SELECT lifecycle FROM provider_session_verification
        WHERE verification_id = ? AND user_id = ?
        """,
        (verification_id, user_id),
    ).fetchone()
    before_lifecycle = before["lifecycle"] if before else None
    verification = advance_session_verification(
        db,
        user_id,
        verification_id,
        lifecycle=lifecycle,
        error_message=error_message,
        now=now,
    )
    if verification is not None and verification.lifecycle != before_lifecycle:
        event = (
            "session_verified"
            if lifecycle == "session_verified"
            else "extraction_started"
            if lifecycle == "extracting"
            else f"lifecycle_{lifecycle}"
        )
        log_access_cycle_event(
            event,
            provider=verification.provider,
            verification_id=verification.verification_id,
            access_cycle_id=verification.verification_id,
            verification_state=verification.lifecycle,
            requested_at=verification.requested_at,
        )
        if lifecycle == "session_verified":
            log_access_cycle_event(
                "verification authenticated",
                provider=verification.provider,
                verification_id=verification.verification_id,
                access_cycle_id=verification.verification_id,
                verification_state="session_verified",
                requested_at=verification.requested_at,
            )
    return verification


def finish_provider_access_check(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    lifecycle: VerificationLifecycle | None = None,
    error_message: str | None = None,
    terminal_reason: VerificationTerminalReason | str | None = None,
    terminal_source: str | None = None,
    now: datetime | None = None,
) -> SessionVerification | None:
    """Terminal lifecycle update for an access check (no PSS write by itself)."""
    verification = complete_session_verification(
        db,
        user_id,
        verification_id,
        lifecycle=lifecycle,
        error_message=error_message,
        terminal_reason=terminal_reason,
        terminal_source=terminal_source or "provider_access_manager",
        now=now,
    )
    if (
        verification is not None
        and verification.lifecycle in TERMINAL_VERIFICATION_LIFECYCLES
    ):
        try:
            from mighty.account_status import persist_customer_presentation_for_provider

            persist_customer_presentation_for_provider(
                db, user_id, verification.provider,
            )
        except Exception:
            pass
    return verification


def mark_access_check_extracting(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    now: datetime | None = None,
) -> SessionVerification | None:
    """Mark authenticated access cycle as extracting private data."""
    return advance_provider_access_check(
        db,
        user_id,
        verification_id,
        lifecycle="extracting",
        now=now,
    )


def complete_access_check_after_extraction(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    success: bool,
    error_message: str | None = None,
    now: datetime | None = None,
) -> None:
    """Terminal result after correlated private-data extraction attempt."""
    row = db.execute(
        """
        SELECT provider FROM provider_session_verification
        WHERE verification_id = ? AND user_id = ?
        """,
        (verification_id, user_id),
    ).fetchone()
    provider = str(row["provider"]) if row else "amex"
    if success:
        log_access_cycle_event(
            "extraction_succeeded",
            provider=provider,
            verification_id=verification_id,
            access_cycle_id=verification_id,
        )
        finish_provider_access_check(
            db,
            user_id,
            verification_id,
            lifecycle="completed",
            terminal_reason="authenticated",
            terminal_source="extraction_success",
            now=now,
        )
    else:
        log_access_cycle_event(
            "extraction_failed",
            provider=provider,
            verification_id=verification_id,
            access_cycle_id=verification_id,
            error=error_message or "extraction_failed",
        )
        finish_provider_access_check(
            db,
            user_id,
            verification_id,
            lifecycle="failed",
            error_message=error_message or "extraction_failed",
            terminal_reason="unknown",
            terminal_source="extraction_failed",
            now=now,
        )


def complete_amex_cycle_no_qualifying_private_data(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    private_api_count: int = 0,
    qualifying_dom_count: int = 0,
    candidate_payload_count: int = 0,
    rejection_reason: str = "no_publishable_widgets",
    extraction_attempted: bool = True,
    extraction_reason: str = "no_publishable_widgets",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Authenticated Amex cycle: extractor returned NO_ACCOUNT_DATA.

    Cycle completes (not failed) so Truth Validation shows LOGGED_IN_NO_ACCOUNT_DATA
    rather than a parser failure. Snapshot is not published.

    Ownership / idempotency:
      - verification must exist for this user
      - provider must be amex
      - mid-cycle (session_verified / extracting / running) → complete once
      - already completed with no_qualifying_private_data → idempotent ok
      - other terminal / missing / wrong provider → rejected (no overwrite)
    """
    ensure_session_verification_tables(db)

    row = db.execute(
        """
        SELECT verification_id, provider, lifecycle, error_message
        FROM provider_session_verification
        WHERE verification_id = ? AND user_id = ?
        """,
        (verification_id, user_id),
    ).fetchone()
    if row is None:
        return {
            "ok": False,
            "error": "verification_not_found",
            "status_code": 404,
        }
    provider = str(row["provider"] or "").strip().lower()
    lifecycle = str(row["lifecycle"] or "").strip().lower()
    error_message = str(row["error_message"] or "").strip()
    if provider != "amex":
        return {
            "ok": False,
            "error": "provider_mismatch",
            "status_code": 409,
            "provider": provider,
            "lifecycle": lifecycle,
        }
    if lifecycle in TERMINAL_VERIFICATION_LIFECYCLES:
        if (
            lifecycle == "completed"
            and error_message == "no_qualifying_private_data"
        ):
            return {
                "ok": True,
                "idempotent": True,
                "extraction": "no_account_data",
                "lifecycle": lifecycle,
                "verification_id": verification_id,
                "access_cycle_id": verification_id,
                "reason": extraction_reason or rejection_reason,
            }
        return {
            "ok": False,
            "error": "cycle_already_terminal",
            "status_code": 409,
            "lifecycle": lifecycle,
            "error_message": error_message or None,
        }
    if lifecycle not in {"running", "session_verified", "extracting"}:
        return {
            "ok": False,
            "error": "cycle_not_ready_for_observation_complete",
            "status_code": 409,
            "lifecycle": lifecycle,
        }

    reason = str(extraction_reason or rejection_reason or "no_publishable_widgets")
    log_access_cycle_event(
        "observation_summary",
        provider="amex",
        verification_id=verification_id,
        access_cycle_id=verification_id,
        authenticated_private_api_responses=int(private_api_count),
        qualifying_dom_observations=int(qualifying_dom_count),
        candidate_payloads=int(candidate_payload_count),
        rejection_reason=reason,
    )
    # Exactly one terminal extraction_result — extractor decided NO_ACCOUNT_DATA.
    log_access_cycle_event(
        "extraction_result",
        provider="amex",
        verification_id=verification_id,
        access_cycle_id=verification_id,
        attempted=bool(extraction_attempted),
        outcome="no_account_data",
        status="NO_ACCOUNT_DATA",
        reason=reason,
        non_empty_field_count=0,
        failure_code=reason,
    )
    log_access_cycle_event(
        "snapshot_result",
        provider="amex",
        verification_id=verification_id,
        access_cycle_id=verification_id,
        attempted=False,
        published=False,
        reason=reason,
    )
    finish_provider_access_check(
        db,
        user_id,
        verification_id,
        lifecycle="completed",
        error_message="no_qualifying_private_data",
        terminal_reason="authenticated",
        terminal_source="no_qualifying_private_data",
        now=now,
    )
    log_access_cycle_event(
        "readiness_result",
        provider="amex",
        verification_id=verification_id,
        access_cycle_id=verification_id,
        readiness="unverified",
        reason="logged_in_no_account_data",
    )
    return {
        "ok": True,
        "idempotent": False,
        "extraction": "no_account_data",
        "lifecycle": "completed",
        "verification_id": verification_id,
        "access_cycle_id": verification_id,
        "capability_hint": "logged_in_no_account_data",
        "reason": reason,
        "status": "NO_ACCOUNT_DATA",
    }


def _sanitized_observation_counts(result: dict[str, Any]) -> dict[str, int | str]:
    """Counts only — never bodies, balances, cookies, or tokens.

    Observation counts are informational. Account-data presence is decided by
    the extractor, not by these counters.
    """
    deep = result.get("deep_inspect") if isinstance(result.get("deep_inspect"), dict) else {}
    auth_trace = deep.get("auth_network_trace") if isinstance(deep, dict) else {}
    if not isinstance(auth_trace, dict):
        auth_trace = {}
    private_api = 0
    for key in (
        "highlighted_requests",
        "auth_session_requests",
        "requests",
    ):
        bucket = auth_trace.get(key) or []
        if isinstance(bucket, list):
            private_api += len(bucket)
    # Do not treat probe private_data_detected as authoritative account data.
    return {
        "authenticated_private_api_responses": private_api,
        "qualifying_dom_observations": 0,
        "candidate_payloads": 1 if private_api else 0,
        "rejection_reason": "",
        "extraction_dispatch": "authenticated_attempt_extraction",
    }


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

    For Amex authenticated evidence, the access cycle advances to
    ``session_verified`` and does **not** complete until correlated private-data
    extraction succeeds. Signed-out evidence completes the cycle without extraction.
    """
    write_session_state = True
    evidence = None
    decision = None
    provider = str(result.get("provider") or "").strip().lower()

    if verification_id and provider == "amex":
        # Active Amex verification owns classification from explicit cycle evidence.
        decision = decide_amex_verification_session(
            result,
            verification_id=verification_id,
            access_cycle_id=verification_id,
            passive_needs_login_seen=bool(result.get("passive_needs_login_seen")),
        )
        log_access_cycle_event(
            "verification_decision",
            provider="amex",
            verification_id=verification_id,
            access_cycle_id=verification_id,
            **decision.to_log_fields(),
        )
        evidence = verification_decision_to_evidence(decision, result)
        write_session_state = bool(
            evidence is not None and evidence.state in {"connected", "signed_out"}
        )
        # Stash decision on the result for extension / tests (sanitized only).
        result = dict(result)
        result["verification_decision"] = decision.final_decision
        result["verification_decision_reason"] = decision.decision_reason
        # Do not let record_probe_run re-derive via the legacy probe mapper —
        # that path can treat login chrome as signed_out and override this decision.
        run_id = record_probe_run(
            db, user_id, result, write_session_state=False
        )
        if write_session_state and evidence is not None:
            record_provider_access_evidence(db, user_id, evidence)
    elif verification_id:
        evidence = derive_session_evidence_from_probe(result)
        write_session_state = bool(
            evidence is not None and evidence.state in {"connected", "signed_out"}
        )
        run_id = record_probe_run(
            db, user_id, result, write_session_state=write_session_state
        )
    else:
        run_id = record_probe_run(
            db, user_id, result, write_session_state=True
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
            failure = str(result.get("failure_reason") or "probe error")
            finish_provider_access_check(
                db,
                user_id,
                verification_id,
                lifecycle="failed",
                error_message=failure,
                terminal_reason=terminal_reason_from_error_message(
                    failure, default="unknown"
                ),
                terminal_source="probe_error",
            )
        elif (
            decision is not None
            and decision.final_decision == "inconclusive"
        ):
            finish_provider_access_check(
                db,
                user_id,
                verification_id,
                lifecycle="failed",
                error_message=decision.decision_reason or "inconclusive",
                terminal_reason="unknown",
                terminal_source="inconclusive_decision",
            )
        elif (
            evidence is not None
            and evidence.state == "connected"
            and provider in ACCESS_CYCLE_EXTRACTION_PROVIDERS
        ):
            # Authenticated Amex: hold cycle open for private-data extraction.
            advance_provider_access_check(
                db,
                user_id,
                verification_id,
                lifecycle="session_verified",
            )
            result["access_cycle_lifecycle"] = "session_verified"
            result["extraction_required"] = True
            # Observation may report chrome signals; account-data authority is
            # the extractor. Always dispatch extraction after authentication.
            obs = _sanitized_observation_counts(result)
            log_access_cycle_event(
                "observation_summary",
                provider="amex",
                verification_id=verification_id,
                access_cycle_id=verification_id,
                **obs,
            )
            log_access_cycle_event(
                "extraction_dispatch_decision",
                provider="amex",
                verification_id=verification_id,
                access_cycle_id=verification_id,
                extraction_required=True,
                reason="authenticated_attempt_extraction",
            )
            log_access_cycle_event(
                "extraction dispatched",
                provider="amex",
                verification_id=verification_id,
                access_cycle_id=verification_id,
                verification_state="session_verified",
                reason="authenticated_attempt_extraction",
            )
            result["observation_counts"] = obs
        else:
            # Signed-out, non-Amex auth, or definitive terminal without extraction.
            signed_out = evidence is not None and evidence.state == "signed_out"
            connected = evidence is not None and evidence.state == "connected"
            if signed_out:
                reason: VerificationTerminalReason = "signed_out"
                source = "signed_out_evidence"
            elif connected:
                reason = "authenticated"
                source = "authenticated_evidence"
            else:
                reason = "unknown"
                source = "probe_complete_without_definitive_evidence"
            finish_provider_access_check(
                db,
                user_id,
                verification_id,
                lifecycle="completed" if reason != "unknown" else "failed",
                terminal_reason=reason,
                terminal_source=source,
            )
            if signed_out:
                log_access_cycle_event(
                    "session_verified",
                    provider=provider or "unknown",
                    verification_id=verification_id,
                    access_cycle_id=verification_id,
                    session_state="signed_out",
                )
                log_access_cycle_event(
                    "readiness_result",
                    provider=provider or "unknown",
                    verification_id=verification_id,
                    access_cycle_id=verification_id,
                    readiness="signed_out",
                )

    return result


def fail_provider_access_check(
    db: Any,
    user_id: str,
    *,
    error_message: str,
    verification_id: str | None = None,
    manual_run_id: str | None = None,
    terminal_reason: VerificationTerminalReason | str | None = None,
    terminal_source: str | None = None,
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
            terminal_reason=terminal_reason
            or terminal_reason_from_error_message(error_message),
            terminal_source=terminal_source or "fail_provider_access_check",
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
