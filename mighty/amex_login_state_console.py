"""Amex login-state diagnostic console — Phase 1 reliability (auth only).

Admin/local view of the latest Amex authentication verification.
Never exposes balances, account data, cookies, tokens, credentials, DOM,
or request/response bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mighty.authentication_state import (
    AuthenticationState,
    authentication_from_terminal_reason,
    resolve_authentication_state,
)
from mighty.provider_session_state import get_provider_session_state
from mighty.session_verification import (
    SessionVerification,
    get_latest_session_verification,
    verification_duration_ms,
)


@dataclass(frozen=True)
class AmexLoginStateDiagnostic:
    """Sanitized latest Amex authentication verification snapshot."""

    authentication_state: str
    confidence: str | None
    terminal_reason: str | None
    terminal_source: str | None
    verification_id: str | None
    access_cycle_id: str | None
    lifecycle: str | None
    duration_ms: int | None
    started_at: str | None
    completed_at: str | None
    requested_at: str | None
    trigger_source: str | None
    evidence_summary: str | None
    evidence_type: str | None
    pss_state: str | None
    pss_source: str | None
    extension_version: str | None
    deployment_sha: str | None
    evidence_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "authentication_state": self.authentication_state,
            "confidence": self.confidence,
            "terminal_reason": self.terminal_reason,
            "terminal_source": self.terminal_source,
            "verification_id": self.verification_id,
            "access_cycle_id": self.access_cycle_id,
            "lifecycle": self.lifecycle,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "requested_at": self.requested_at,
            "trigger_source": self.trigger_source,
            "evidence_summary": self.evidence_summary,
            "evidence_type": self.evidence_type,
            "pss_state": self.pss_state,
            "pss_source": self.pss_source,
            "extension_version": self.extension_version,
            "deployment_sha": self.deployment_sha,
            "evidence_flags": list(self.evidence_flags),
        }


def _deployment_sha() -> str | None:
    import os

    for key in ("RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT", "COMMIT_SHA", "SOURCE_VERSION"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value[:40]
    return None


def _evidence_flags(
    *,
    evidence_type: str | None,
    evidence_summary: str | None,
    terminal_reason: str | None,
) -> tuple[str, ...]:
    flags: list[str] = []
    et = (evidence_type or "").strip()
    if et:
        flags.append(f"evidence_type:{et}")
    blob = f"{evidence_summary or ''} {terminal_reason or ''}".lower()
    checks = (
        ("session_api", ("readusersession", "session api", "session_api")),
        ("login_url", ("login page", "login_page")),
        ("login_form", ("login form",)),
        ("authenticated_page", ("authenticated page", "authenticated_page")),
        ("timeout", ("timeout",)),
        ("blank_or_unloaded", ("blank", "unloaded")),
        ("navigation_failure", ("navigation",)),
        ("cancelled", ("cancelled", "canceled", "tab closed")),
        ("inconclusive", ("inconclusive", "insufficient", "conflict")),
    )
    for name, needles in checks:
        if any(n in blob for n in needles):
            flags.append(name)
    seen: set[str] = set()
    out: list[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            out.append(flag)
    return tuple(out)


def resolve_amex_login_state_diagnostic(
    db: Any,
    user_id: str,
    *,
    extension_version: str | None = None,
    verification: SessionVerification | None = None,
) -> AmexLoginStateDiagnostic:
    """Build the latest Amex auth diagnostic for the current user."""
    latest = verification or get_latest_session_verification(db, user_id, "amex")
    pss = get_provider_session_state(db, user_id, "amex")

    terminal_reason = latest.terminal_reason if latest else None
    lifecycle = latest.lifecycle if latest else None

    if lifecycle in {"session_verified", "extracting"}:
        # Auth already terminalized as SIGNED_IN; extraction must not revise it.
        auth = AuthenticationState.SIGNED_IN
    elif terminal_reason:
        auth = authentication_from_terminal_reason(terminal_reason)
    elif lifecycle in {"requested", "running"}:
        auth = AuthenticationState.LOGIN_UNKNOWN
    elif pss is not None:
        auth = resolve_authentication_state(session_state=pss.state)
    else:
        auth = AuthenticationState.LOGIN_UNKNOWN

    duration = None
    if latest is not None:
        duration = verification_duration_ms(
            requested_at=latest.requested_at,
            started_at=latest.started_at,
            completed_at=latest.completed_at,
        )

    evidence_type = pss.evidence_type if pss else None
    evidence_summary = pss.evidence_summary if pss else None
    confidence = pss.confidence if pss else None
    if auth == AuthenticationState.LOGIN_UNKNOWN:
        confidence = confidence or "n/a"
    elif confidence is None:
        confidence = "high" if evidence_type == "session_api" else "medium"

    vid = latest.verification_id if latest else None
    return AmexLoginStateDiagnostic(
        authentication_state=auth.value,
        confidence=confidence,
        terminal_reason=terminal_reason,
        terminal_source=latest.terminal_source if latest else None,
        verification_id=vid,
        access_cycle_id=vid,
        lifecycle=lifecycle,
        duration_ms=duration,
        started_at=latest.started_at if latest else None,
        completed_at=latest.completed_at if latest else None,
        requested_at=latest.requested_at if latest else None,
        trigger_source=latest.trigger_source if latest else None,
        evidence_summary=evidence_summary,
        evidence_type=evidence_type,
        pss_state=pss.state if pss else None,
        pss_source=pss.source if pss else None,
        extension_version=extension_version,
        deployment_sha=_deployment_sha(),
        evidence_flags=_evidence_flags(
            evidence_type=evidence_type,
            evidence_summary=evidence_summary,
            terminal_reason=terminal_reason,
        ),
    )
