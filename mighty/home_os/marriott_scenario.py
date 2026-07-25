"""Canonical Marriott authentication Interrupt scenario (Home OS slice).

Builds WorkItem / Coverage / Proof inputs for the signed-out Marriott case.
Does not rank, project, or render — that belongs to mighty.workitem.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mighty.workitem.coverage import (
    AuthPosture,
    CoverageHealth,
    CoverageItem,
    CoverageStatus,
    VerificationState,
)
from mighty.workitem.model import (
    UrgencyBand,
    WorkItem,
    WorkItemAction,
    WorkItemEvidence,
    WorkItemPriority,
    WorkItemState,
    WorkItemType,
)
from mighty.workitem.proof import ProofItem
from mighty.workitem.projection_inputs import CanonicalModels

PROVIDER = "marriott"
PROVIDER_DISPLAY = "Marriott"
WORK_ITEM_ID = "wi_interrupt_marriott_signin"
OWNER_DOMAIN = "home_os.auth_repair"

# Simulation marker — never treated as a live provider session.
SIMULATION_MODE = "demo_simulated_auth_repair"


def build_marriott_interrupt(
    *,
    as_of: datetime,
    state: WorkItemState = WorkItemState.EXPANDED,
    expires_at: datetime | None = None,
) -> WorkItem:
    """Canonical Interrupt: Marriott included but signed out."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    expiry = expires_at
    if expiry is None:
        expiry = as_of + timedelta(days=7)
    return WorkItem(
        id=WORK_ITEM_ID,
        type=WorkItemType.INTERRUPT,
        priority=WorkItemPriority.INTERRUPT,
        title="Marriott needs a sign-in",
        summary=(
            "You asked Mighty to include Marriott. Mighty still has it in your "
            "coverage, but cannot see it right now — most likely because you are "
            "signed out. Sign in once so Mighty can watch Marriott again."
        ),
        evidence=WorkItemEvidence.from_mapping(
            {
                "provider": PROVIDER,
                "display_name": PROVIDER_DISPLAY,
                "reason": "signed_out",
                "included_by_user": "true",
                "simulation": SIMULATION_MODE,
            }
        ),
        primary_action=WorkItemAction(
            key="start_marriott_signin",
            intent="Sign in to Marriott so Mighty can see it again",
        ),
        secondary_action=WorkItemAction(
            key="defer_marriott_signin",
            intent="Not now",
        ),
        dismissible=False,
        deferrable=True,
        created_at=as_of - timedelta(hours=1),
        updated_at=as_of,
        expires_at=expiry,
        proof_reference=None,
        provider=PROVIDER,
        capability="session",
        state=state,
        owner_domain=OWNER_DOMAIN,
        urgency_band=UrgencyBand.HARD,
        effort_weight=20,
        confidence=0.95,
    )


def build_marriott_coverage(*, signed_out: bool) -> CoverageItem:
    """Honest Coverage row for Marriott — no Accounts deep link."""
    if signed_out:
        return CoverageItem(
            provider=PROVIDER,
            status=CoverageStatus.ENROLLED,
            health=CoverageHealth.BLOCKED,
            capabilities=("loyalty", "capture"),
            verification=VerificationState.FAILED,
            discovery="manual",
            authentication=AuthPosture.MISSING,
            monitoring="paused_until_sign_in",
            display_name=PROVIDER_DISPLAY,
        )
    return CoverageItem(
        provider=PROVIDER,
        status=CoverageStatus.ENROLLED,
        health=CoverageHealth.HEALTHY,
        capabilities=("loyalty", "capture"),
        verification=VerificationState.VERIFIED,
        discovery="manual",
        authentication=AuthPosture.VALID,
        monitoring="active",
        display_name=PROVIDER_DISPLAY,
    )


def build_seed_proof(*, as_of: datetime) -> ProofItem:
    """Existing recent proof (unrelated) so Proof region is non-empty initially."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    return ProofItem(
        id="proof_amex_credit_refresh",
        outcome_at=as_of - timedelta(days=2),
        summary="Amex · Annual credit refreshed",
        provider="amex",
        outcome_class="benefit",
        impact="normal",
    )


def build_access_restored_proof(
    *,
    as_of: datetime,
    work_item_id: str = WORK_ITEM_ID,
) -> ProofItem:
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    return ProofItem(
        id=f"proof_marriott_access_restored:{int(as_of.timestamp())}",
        outcome_at=as_of,
        summary="Marriott access restored — Mighty can watch it again",
        provider=PROVIDER,
        outcome_class="access_restored",
        work_item_id=work_item_id,
        impact="high",
    )


def initial_canonical_models(*, as_of: datetime) -> CanonicalModels:
    """Starting snapshot: interrupt + signed-out coverage + prior proof."""
    return CanonicalModels(
        work_items=(build_marriott_interrupt(as_of=as_of),),
        coverage=(build_marriott_coverage(signed_out=True),),
        proof=(build_seed_proof(as_of=as_of),),
    )
