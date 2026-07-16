"""Canonical authentication truth — independent of extraction and capability.

Phase 1 vocabulary (exactly three terminal values):

  SIGNED_IN
  SIGNED_OUT
  LOGIN_UNKNOWN

Authentication is resolved from definitive login evidence only. Extraction
success / failure / no-account-data must never revise a terminal auth result.
Capability and readiness may *consume* this value; they must not reinterpret
transport labels (connected, error, inconclusive, needs_login, …) as auth truth.

Transport / lifecycle mapping (not authentication truth):

  VerificationFinalDecision.connected     → SIGNED_IN
  VerificationFinalDecision.signed_out    → SIGNED_OUT
  VerificationFinalDecision.inconclusive  → LOGIN_UNKNOWN

  terminal_reason authenticated           → SIGNED_IN
  terminal_reason signed_out              → SIGNED_OUT
  terminal_reason timeout|cancelled|
    navigation_failed|unknown             → LOGIN_UNKNOWN

  CurrentAccess connected_now             → SIGNED_IN
  CurrentAccess signed_out                → SIGNED_OUT
  CurrentAccess checking|unknown|error    → LOGIN_UNKNOWN

  PSS connected (fresh)                   → SIGNED_IN
  PSS signed_out (fresh)                  → SIGNED_OUT
  PSS unknown|error                       → LOGIN_UNKNOWN

  ProductSessionState connected           → SIGNED_IN
  ProductSessionState signed_out          → SIGNED_OUT
  ProductSessionState checking|unknown    → LOGIN_UNKNOWN
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class AuthenticationState(str, Enum):
    """Immutable customer authentication truth for one verification cycle."""

    SIGNED_IN = "signed_in"
    SIGNED_OUT = "signed_out"
    LOGIN_UNKNOWN = "login_unknown"


# Transport labels that may appear in probes, PSS, Current Access, or product
# session bridges. Documented here so callers do not treat them as auth truth.
TRANSPORT_TO_AUTHENTICATION: dict[str, AuthenticationState] = {
    # VerificationFinalDecision / Amex resolver
    "connected": AuthenticationState.SIGNED_IN,
    "signed_out": AuthenticationState.SIGNED_OUT,
    "inconclusive": AuthenticationState.LOGIN_UNKNOWN,
    # Current Access
    "connected_now": AuthenticationState.SIGNED_IN,
    "checking": AuthenticationState.LOGIN_UNKNOWN,
    "unknown": AuthenticationState.LOGIN_UNKNOWN,
    "error": AuthenticationState.LOGIN_UNKNOWN,
    # Verification terminal_reason
    "authenticated": AuthenticationState.SIGNED_IN,
    "timeout": AuthenticationState.LOGIN_UNKNOWN,
    "cancelled": AuthenticationState.LOGIN_UNKNOWN,
    "navigation_failed": AuthenticationState.LOGIN_UNKNOWN,
    # Product session / PSS error (never SIGNED_OUT)
    "probe_error": AuthenticationState.LOGIN_UNKNOWN,
    "failed": AuthenticationState.LOGIN_UNKNOWN,
    "timed_out": AuthenticationState.LOGIN_UNKNOWN,
    # Failure reasons that must never invent SIGNED_OUT
    "blank_or_unloaded_page": AuthenticationState.LOGIN_UNKNOWN,
    "probe_navigation_error": AuthenticationState.LOGIN_UNKNOWN,
    "network_issue": AuthenticationState.LOGIN_UNKNOWN,
    "static_assets_only": AuthenticationState.LOGIN_UNKNOWN,
    "insufficient_evidence": AuthenticationState.LOGIN_UNKNOWN,
    "conflicting_evidence_unordered": AuthenticationState.LOGIN_UNKNOWN,
    "login_chrome_on_account_page": AuthenticationState.LOGIN_UNKNOWN,
}


def normalize_authentication_state(
    value: AuthenticationState | str | None,
) -> AuthenticationState | None:
    """Parse a stored/API value into AuthenticationState, or None if absent."""
    if value is None:
        return None
    if isinstance(value, AuthenticationState):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return AuthenticationState(text)
    except ValueError:
        return TRANSPORT_TO_AUTHENTICATION.get(text)


def authentication_from_transport(value: str | None) -> AuthenticationState:
    """Map a transport/lifecycle label to authentication truth.

    Unknown labels resolve to LOGIN_UNKNOWN — never invent SIGNED_OUT.
    """
    if value is None:
        return AuthenticationState.LOGIN_UNKNOWN
    text = str(value).strip().lower()
    if not text:
        return AuthenticationState.LOGIN_UNKNOWN
    mapped = TRANSPORT_TO_AUTHENTICATION.get(text)
    if mapped is not None:
        return mapped
    try:
        return AuthenticationState(text)
    except ValueError:
        return AuthenticationState.LOGIN_UNKNOWN


def authentication_from_current_access(current_access: str | None) -> AuthenticationState:
    """Current Access → canonical authentication (error is LOGIN_UNKNOWN)."""
    return authentication_from_transport(current_access)


def authentication_from_verification_decision(
    final_decision: str | None,
) -> AuthenticationState:
    """Amex VerificationFinalDecision → canonical authentication."""
    return authentication_from_transport(final_decision)


def authentication_from_terminal_reason(
    terminal_reason: str | None,
) -> AuthenticationState:
    """Stored verification terminal_reason → canonical authentication."""
    return authentication_from_transport(terminal_reason)


def authentication_from_product_session(
    session_state: str | None,
) -> AuthenticationState:
    """Product session_state → authentication.

    Product ``signed_out`` is only emitted for definitive Current Access
    signed_out after Fix 2; ``error`` maps to product ``unknown``.
    """
    return authentication_from_transport(session_state)


def is_definitive_signed_out(auth: AuthenticationState | str | None) -> bool:
    return normalize_authentication_state(auth) == AuthenticationState.SIGNED_OUT


def is_signed_in(auth: AuthenticationState | str | None) -> bool:
    return normalize_authentication_state(auth) == AuthenticationState.SIGNED_IN


def resolve_authentication_state(
    *,
    current_access: str | None = None,
    verification_decision: str | None = None,
    terminal_reason: str | None = None,
    session_state: str | None = None,
    authentication_state: AuthenticationState | str | None = None,
) -> AuthenticationState:
    """Resolve canonical authentication with precedence for explicit values.

    Precedence (first non-None wins among explicit auth / decision / terminal):
      1. authentication_state (already terminalized)
      2. verification_decision (Amex cycle resolver)
      3. terminal_reason (verification row)
      4. current_access (freshness-aware Current Access)
      5. session_state (product / PSS projection — last resort)

    Extraction outcomes are intentionally not inputs.
    """
    explicit = normalize_authentication_state(authentication_state)
    if explicit is not None:
        return explicit
    if verification_decision is not None:
        return authentication_from_verification_decision(verification_decision)
    if terminal_reason is not None:
        return authentication_from_terminal_reason(terminal_reason)
    if current_access is not None:
        return authentication_from_current_access(current_access)
    if session_state is not None:
        return authentication_from_product_session(session_state)
    return AuthenticationState.LOGIN_UNKNOWN


def authentication_state_value(auth: AuthenticationState | str | None) -> str:
    resolved = normalize_authentication_state(auth) or AuthenticationState.LOGIN_UNKNOWN
    return resolved.value


def attach_authentication_fields(payload: dict[str, Any], auth: AuthenticationState) -> dict[str, Any]:
    """Add canonical auth fields to an API/dict payload (no secrets)."""
    payload["authentication_state"] = auth.value
    return payload
