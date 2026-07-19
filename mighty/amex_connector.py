"""
Production Amex connector — thin orchestration over Provider Runtime.

Owns: request usable session, invoke extraction, normalize, return structured
refresh result.

Does not own: Chrome lifecycle, MFA, CDP attach, keepalive, campaign helpers.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from mighty.amex_normalizer import normalize_amex_observation
from mighty.provider_connector import (
    ConnectorCapabilities,
    ConnectorErrorReason,
    ConnectorRefreshResult,
    ConnectorTelemetry,
    ConnectorVerificationResult,
    ProviderConnector,
    RefreshStatus,
    assert_no_provider_raw_objects,
    classify_refresh_status,
    summarize_field_observations,
    utc_now_iso,
)

AMEX_INITIAL_FIELDS = (
    "rewards_balance",
    "current_balance",
    "available_credit",
    "payment_due_amount",
    "payment_due_date",
    "last_four",
    "last_verified_timestamp",
)


@dataclass
class RuntimeSessionHandle:
    """Opaque runtime session result — never carries Playwright Page objects."""

    ok: bool
    authentication_state: str | None = None
    reason: str | None = None
    user_interrupted: bool = False
    interruption_type: str | None = None
    recovery_attempts: int = 0
    surface_ready: bool = False
    error: str | None = None
    payload: dict[str, Any] | None = None


class AmexConnector(ProviderConnector):
    """Read-only Amex connector implementing the generic ProviderConnector contract."""

    provider = "amex"

    def __init__(
        self,
        *,
        ensure_usable_session_fn: Callable[[str], dict[str, Any]],
        ensure_provider_surface_fn: Callable[[str, str], dict[str, Any]],
        execute_readonly_extraction_fn: Callable[[str, str], dict[str, Any]],
        verify_fn: Callable[[str], Any] | None = None,
        timeline_fn: Callable[..., None] | None = None,
    ) -> None:
        self._ensure_usable_session = ensure_usable_session_fn
        self._ensure_provider_surface = ensure_provider_surface_fn
        self._execute_readonly_extraction = execute_readonly_extraction_fn
        self._verify_fn = verify_fn
        self._timeline = timeline_fn

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            provider=self.provider,
            read_only=True,
            supports_verify=True,
            supports_refresh=True,
            supports_transactions=False,
            supports_payments=False,
            supports_offers=False,
            supports_statements=False,
            supports_transfers=False,
            supports_mutations=False,
            initial_fields=AMEX_INITIAL_FIELDS,
        )

    def verify(self) -> ConnectorVerificationResult:
        if self._verify_fn is not None:
            raw = self._verify_fn(self.provider)
        else:
            raw = self._ensure_usable_session(self.provider)
        return self._verification_from_payload(raw)

    def refresh(self) -> ConnectorRefreshResult:
        refresh_id = str(uuid.uuid4())
        started = utc_now_iso()
        started_mono = time.monotonic()
        auth_initial: str | None = None
        auth_final: str | None = None
        recovery_attempts = 0
        user_interrupted = False
        interruption_type: str | None = None
        warnings: list[str] = []
        field_observations: list = []
        snapshot = None
        runtime_error: str | None = None
        extraction_method_counts: dict[str, int] = {}

        self._record_timeline(
            "connector_refresh_started",
            payload={"refresh_id": refresh_id},
        )

        try:
            session_payload = self._ensure_usable_session(self.provider)
        except Exception as exc:  # noqa: BLE001 - map to sanitized result
            session_payload = {
                "ok": False,
                "authentication_state": "LOGIN_UNKNOWN",
                "error": "runtime_unavailable",
                "reason": f"{type(exc).__name__}",
            }

        auth_initial = self._auth_state(session_payload)
        recovery_attempts = int(session_payload.get("recovery_attempts") or 0)
        user_interrupted = bool(session_payload.get("user_interrupted"))
        interruption_type = session_payload.get("interruption_type")
        auth_final = auth_initial

        if user_interrupted:
            return self._finalize(
                refresh_id=refresh_id,
                started=started,
                started_mono=started_mono,
                auth_initial=auth_initial,
                auth_final=auth_final,
                recovery_attempts=recovery_attempts,
                user_interrupted=True,
                interruption_type=interruption_type or "mfa_or_login",
                field_observations=[],
                snapshot=None,
                warnings=["authentication_required"],
                runtime_error=None,
                extraction_method_counts={},
                error="authentication_required",
                error_reason=ConnectorErrorReason.AUTHENTICATION_REQUIRED,
                status=RefreshStatus.AUTHENTICATION_REQUIRED,
            )

        if not session_payload.get("ok") or auth_initial in {
            "SIGNED_OUT",
            "LOGIN_UNKNOWN",
            None,
            "",
        }:
            if session_payload.get("error") == "runtime_unavailable":
                runtime_error = "runtime_unavailable"
            status, error_reason, error = classify_refresh_status(
                authentication_state=auth_initial or "LOGIN_UNKNOWN",
                snapshot=None,
                field_observations=[],
                user_interrupted=False,
                runtime_error=runtime_error,
            )
            return self._finalize(
                refresh_id=refresh_id,
                started=started,
                started_mono=started_mono,
                auth_initial=auth_initial,
                auth_final=auth_final,
                recovery_attempts=recovery_attempts,
                user_interrupted=False,
                interruption_type=None,
                field_observations=[],
                snapshot=None,
                warnings=["authentication_required"],
                runtime_error=runtime_error,
                extraction_method_counts={},
                error=error,
                error_reason=error_reason,
                status=status,
            )

        # Ensure overview surface through runtime (no Page exposure).
        try:
            surface = self._ensure_provider_surface(self.provider, "overview")
        except Exception as exc:  # noqa: BLE001
            surface = {
                "ok": False,
                "error": "surface_unavailable",
                "reason": type(exc).__name__,
            }
        if not surface.get("ok"):
            return self._finalize(
                refresh_id=refresh_id,
                started=started,
                started_mono=started_mono,
                auth_initial=auth_initial,
                auth_final=auth_final,
                recovery_attempts=recovery_attempts,
                user_interrupted=False,
                interruption_type=None,
                field_observations=[],
                snapshot=None,
                warnings=["surface_unavailable"],
                runtime_error="surface_unavailable",
                extraction_method_counts={},
                error="surface_unavailable",
                error_reason=ConnectorErrorReason.SURFACE_UNAVAILABLE,
                status=RefreshStatus.UNAVAILABLE,
            )

        # Optional re-verify after surface navigation.
        if self._verify_fn is not None:
            try:
                verified = self._verify_fn(self.provider)
                auth_final = self._auth_state(verified)
            except Exception:
                pass

        if auth_final in {"SIGNED_OUT", "LOGIN_UNKNOWN"}:
            status, error_reason, error = classify_refresh_status(
                authentication_state=auth_final,
                snapshot=None,
                field_observations=[],
            )
            return self._finalize(
                refresh_id=refresh_id,
                started=started,
                started_mono=started_mono,
                auth_initial=auth_initial,
                auth_final=auth_final,
                recovery_attempts=recovery_attempts,
                user_interrupted=False,
                interruption_type=None,
                field_observations=[],
                snapshot=None,
                warnings=["authentication_required"],
                runtime_error=None,
                extraction_method_counts={},
                error=error,
                error_reason=error_reason,
                status=status,
            )

        try:
            extraction_payload = self._execute_readonly_extraction(
                self.provider, "overview_accounts"
            )
        except Exception as exc:  # noqa: BLE001
            extraction_payload = {
                "ok": False,
                "error": "extraction_failed",
                "reason": type(exc).__name__,
            }

        if not extraction_payload.get("ok"):
            error_key = str(extraction_payload.get("error") or "extraction_failed")
            return self._finalize(
                refresh_id=refresh_id,
                started=started,
                started_mono=started_mono,
                auth_initial=auth_initial,
                auth_final=auth_final,
                recovery_attempts=recovery_attempts,
                user_interrupted=False,
                interruption_type=None,
                field_observations=[],
                snapshot=None,
                warnings=["extraction_partial"],
                runtime_error="extraction_failed",
                extraction_method_counts={},
                error=error_key,
                error_reason=ConnectorErrorReason.EXTRACTION_FAILED,
                status=RefreshStatus.FAILED,
            )

        intermediate = extraction_payload.get("observation")
        if intermediate is None:
            return self._finalize(
                refresh_id=refresh_id,
                started=started,
                started_mono=started_mono,
                auth_initial=auth_initial,
                auth_final=auth_final,
                recovery_attempts=recovery_attempts,
                user_interrupted=False,
                interruption_type=None,
                field_observations=[],
                snapshot=None,
                warnings=[],
                runtime_error=None,
                extraction_method_counts={},
                error="no_useful_data",
                error_reason=ConnectorErrorReason.NO_USEFUL_DATA,
                status=RefreshStatus.FAILED,
            )

        try:
            snapshot, field_observations, warnings = normalize_amex_observation(
                intermediate,
                verified_at=auth_final and started,
            )
            # Prefer extraction observed_at / runtime verified stamp when present.
            verified_stamp = extraction_payload.get("verified_at") or started
            if snapshot is not None:
                snapshot = type(snapshot)(
                    provider=snapshot.provider,
                    provider_customer_id=snapshot.provider_customer_id,
                    accounts=snapshot.accounts,
                    rewards=snapshot.rewards,
                    observed_at=snapshot.observed_at,
                    verified_at=verified_stamp,
                    completeness=snapshot.completeness,
                    warnings=snapshot.warnings,
                    provider_metadata=snapshot.provider_metadata,
                )
            extraction_method_counts = dict(
                getattr(intermediate, "method_counts", None)
                or extraction_payload.get("method_counts")
                or {}
            )
        except Exception:  # noqa: BLE001
            return self._finalize(
                refresh_id=refresh_id,
                started=started,
                started_mono=started_mono,
                auth_initial=auth_initial,
                auth_final=auth_final,
                recovery_attempts=recovery_attempts,
                user_interrupted=False,
                interruption_type=None,
                field_observations=[],
                snapshot=None,
                warnings=[],
                runtime_error=None,
                extraction_method_counts={},
                error="normalization_failed",
                error_reason=ConnectorErrorReason.NORMALIZATION_FAILED,
                status=RefreshStatus.FAILED,
            )

        status, error_reason, error = classify_refresh_status(
            authentication_state=auth_final,
            snapshot=snapshot,
            field_observations=field_observations,
        )
        result = self._finalize(
            refresh_id=refresh_id,
            started=started,
            started_mono=started_mono,
            auth_initial=auth_initial,
            auth_final=auth_final,
            recovery_attempts=recovery_attempts,
            user_interrupted=False,
            interruption_type=None,
            field_observations=field_observations,
            snapshot=snapshot,
            warnings=list(warnings),
            runtime_error=None,
            extraction_method_counts=extraction_method_counts,
            error=error,
            error_reason=error_reason,
            status=status,
        )
        assert_no_provider_raw_objects(result.to_sanitized_dict())
        return result

    def _finalize(
        self,
        *,
        refresh_id: str,
        started: str,
        started_mono: float,
        auth_initial: str | None,
        auth_final: str | None,
        recovery_attempts: int,
        user_interrupted: bool,
        interruption_type: str | None,
        field_observations: list,
        snapshot: Any,
        warnings: list[str],
        runtime_error: str | None,
        extraction_method_counts: dict[str, int],
        error: str | None,
        error_reason: ConnectorErrorReason | None,
        status: RefreshStatus,
    ) -> ConnectorRefreshResult:
        completed = utc_now_iso()
        duration_ms = int((time.monotonic() - started_mono) * 1000)
        counts = summarize_field_observations(field_observations)
        telemetry = ConnectorTelemetry(
            provider=self.provider,
            refresh_id=refresh_id,
            started_at=started,
            completed_at=completed,
            duration_ms=duration_ms,
            authentication_initial_state=auth_initial,
            authentication_final_state=auth_final,
            extraction_method_counts=extraction_method_counts,
            fields_attempted=counts["fields_attempted"],
            fields_succeeded=counts["fields_succeeded"],
            fields_unavailable=counts["fields_unavailable"],
            fields_failed=counts["fields_failed"],
            runtime_recovery_attempts=recovery_attempts,
            user_interrupted=user_interrupted,
            interruption_type=interruption_type,
            snapshot_account_count=len(snapshot.accounts) if snapshot else 0,
            rewards_program_count=len(snapshot.rewards) if snapshot else 0,
        )
        result = ConnectorRefreshResult(
            provider=self.provider,
            status=status,
            snapshot=snapshot,
            field_observations=tuple(field_observations),
            telemetry=telemetry,
            user_interrupted=user_interrupted,
            interruption_type=interruption_type,
            warnings=tuple(warnings),
            error=error,
            error_reason=error_reason,
        )
        self._record_timeline(
            "connector_refresh_completed",
            payload={
                "refresh_id": refresh_id,
                "status": status.value,
                "authentication_initial_state": auth_initial,
                "authentication_final_state": auth_final,
                "fields_succeeded": telemetry.fields_succeeded,
                "fields_unavailable": telemetry.fields_unavailable,
                "fields_failed": telemetry.fields_failed,
                "user_interrupted": user_interrupted,
                "snapshot_account_count": telemetry.snapshot_account_count,
                "rewards_program_count": telemetry.rewards_program_count,
                "error_reason": error_reason.value if error_reason else None,
                "runtime_error": runtime_error,
            },
        )
        return result

    def _record_timeline(self, event_type: str, *, payload: dict[str, Any]) -> None:
        if self._timeline is None:
            return
        try:
            self._timeline(event_type, provider=self.provider, payload=payload)
        except Exception:
            pass

    @staticmethod
    def _auth_state(payload: Any) -> str | None:
        if payload is None:
            return None
        if hasattr(payload, "authentication_state"):
            return str(getattr(payload, "authentication_state"))
        if isinstance(payload, dict):
            return payload.get("authentication_state")
        return None

    def _verification_from_payload(self, raw: Any) -> ConnectorVerificationResult:
        if hasattr(raw, "authentication_state"):
            state = str(raw.authentication_state)
            reason = getattr(raw, "reason", None)
            observed_at = getattr(raw, "observed_at", None)
            ok = state == "SIGNED_IN"
            return ConnectorVerificationResult(
                provider=self.provider,
                authentication_state=state,
                ok=ok,
                reason=reason,
                observed_at=observed_at,
            )
        payload = raw if isinstance(raw, dict) else {}
        state = str(payload.get("authentication_state") or "LOGIN_UNKNOWN")
        return ConnectorVerificationResult(
            provider=self.provider,
            authentication_state=state,
            ok=bool(payload.get("ok")) and state == "SIGNED_IN",
            reason=payload.get("reason") or payload.get("error"),
            observed_at=payload.get("observed_at"),
            user_interrupted=bool(payload.get("user_interrupted")),
            interruption_type=payload.get("interruption_type"),
        )


def build_amex_connector_from_runtime(runtime: Any) -> AmexConnector:
    """Wire AmexConnector to a ProviderRuntime instance."""
    return AmexConnector(
        ensure_usable_session_fn=runtime.ensure_usable_session,
        ensure_provider_surface_fn=runtime.ensure_provider_surface,
        execute_readonly_extraction_fn=runtime.execute_readonly_extraction,
        verify_fn=lambda provider: runtime.verify(provider),
        timeline_fn=getattr(runtime, "_timeline", None),
    )
