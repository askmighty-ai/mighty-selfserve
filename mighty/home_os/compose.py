"""Compose Home OS projection inputs from real sources or explicit simulation.

Business ranking/lifecycle stay in mighty.workitem. This module only gathers
canonical inputs and records which pieces remain simulated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.attention_engine import read_attention
from mighty.attention_loaders import load_account_states_for_attention
from mighty.attention_view import build_attention_view
from mighty.change_store import change_alerts_from_store
from mighty.home_os.adapters import (
    account_states_to_coverage,
    attention_items_to_work_items,
    change_alerts_to_proof,
)
from mighty.home_os.future_preview import (
    PERSONA_DISPLAY_NAME as FUTURE_PREVIEW_DISPLAY_NAME,
    SIMULATION_TAG as SIM_FUTURE_PREVIEW,
    initial_canonical_models as future_preview_canonical_models,
    normalize_preview_state,
    preview_as_of,
)
from mighty.home_os.marriott_scenario import initial_canonical_models
from mighty.home_os.session_state import (
    HOME_OS_DISPLAY_NAME,
    HomeOsSliceState,
    SessionOverlays,
)
from mighty.workitem.coverage import AuthPosture, CoverageItem
from mighty.workitem.model import WorkItemState
from mighty.workitem.projection_inputs import CanonicalModels
from mighty.workitem.proof import ProofItem


# Stable tags for docs/HOME_OS_SIMULATION_GAPS.md and tests.
SIM_EPHEMERAL_SCENARIO = "ephemeral_marriott_scenario"
SIM_FUTURE_PREVIEW_SCENARIO = SIM_FUTURE_PREVIEW
SIM_AUTH_REPAIR_COMPLETION = "auth_repair_completion_simulated"
SIM_SESSION_PROOF_OVERLAY = "session_local_proof_overlay"
SIM_SESSION_COVERAGE_OVERRIDE = "session_local_coverage_override"
SIM_NO_REAL_USER = "no_authenticated_user"


@dataclass
class ComposeResult:
    """Canonical inputs + honesty about remaining simulation."""

    models: CanonicalModels
    display_name: str
    simulation_tags: tuple[str, ...] = ()
    source: str = "unknown"  # "authenticated" | "ephemeral"
    authenticated: bool = False

    def uses_real_attention(self) -> bool:
        return self.authenticated and SIM_EPHEMERAL_SCENARIO not in self.simulation_tags


def compose_for_ephemeral(*, as_of: datetime) -> ComposeResult:
    """Research/demo preview — fully simulated Marriott scenario."""
    models = initial_canonical_models(as_of=as_of)
    return ComposeResult(
        models=models,
        display_name=HOME_OS_DISPLAY_NAME,
        simulation_tags=(SIM_EPHEMERAL_SCENARIO, SIM_AUTH_REPAIR_COMPLETION),
        source="ephemeral",
        authenticated=False,
    )


def compose_for_future_preview(
    *,
    as_of: datetime | None = None,
    state: str | None = "full",
    include_interrupt: bool = False,
) -> ComposeResult:
    """Review-only Future Preview — deterministic operational household."""
    clock = as_of or preview_as_of()
    preview_state = normalize_preview_state(state)
    models = future_preview_canonical_models(
        as_of=clock,
        state=preview_state,
        include_interrupt=include_interrupt,
    )
    return ComposeResult(
        models=models,
        display_name=FUTURE_PREVIEW_DISPLAY_NAME,
        simulation_tags=(SIM_FUTURE_PREVIEW_SCENARIO,),
        source="future_preview",
        authenticated=False,
    )


def compose_for_authenticated_user(
    db: Any,
    user_id: str,
    *,
    as_of: datetime,
    display_name: str,
    overlays: SessionOverlays | None = None,
) -> ComposeResult:
    """Project real Attention / AccountState / changes into canonical models."""
    as_of = _aware(as_of)
    overlays = overlays or SessionOverlays()
    tags: list[str] = []

    attention_state = read_attention(db, user_id, now=as_of)
    visible = []
    if attention_state.primary is not None:
        visible.append(attention_state.primary)
    visible.extend(attention_state.remaining)

    work_items = list(
        attention_items_to_work_items(visible, as_of=as_of)
    )

    # Apply session-local completions (simulated repair outcomes for P1).
    if overlays.completed_work_item_ids:
        tags.append(SIM_AUTH_REPAIR_COMPLETION)
        completed = set(overlays.completed_work_item_ids)
        work_items = [
            item.with_updates(state=WorkItemState.ARCHIVED)
            if item.id in completed
            else item
            for item in work_items
        ]
        # Drop archived from effective inputs — keep only active.
        work_items = [
            item
            for item in work_items
            if item.id not in completed
        ]

    accounts = load_account_states_for_attention(db, user_id)
    coverage = list(account_states_to_coverage(accounts))
    if overlays.coverage_auth_overrides:
        tags.append(SIM_SESSION_COVERAGE_OVERRIDE)
        coverage = [
            _apply_coverage_override(c, overlays.coverage_auth_overrides)
            for c in coverage
        ]

    alerts = change_alerts_from_store(db, user_id, limit=20)
    proof = list(change_alerts_to_proof(alerts, as_of=as_of))
    if overlays.extra_proof:
        tags.append(SIM_SESSION_PROOF_OVERLAY)
        # Newest first preference — extras prepended.
        proof = list(overlays.extra_proof) + proof

    # Ensure AttentionView copy path is exercised (no ranking) for consistency.
    _ = build_attention_view(attention_state, surface="home")

    models = CanonicalModels(
        work_items=tuple(work_items),
        coverage=tuple(coverage),
        proof=tuple(proof),
    )
    return ComposeResult(
        models=models,
        display_name=display_name or HOME_OS_DISPLAY_NAME,
        simulation_tags=tuple(dict.fromkeys(tags)),
        source="authenticated",
        authenticated=True,
    )


def slice_from_compose(
    result: ComposeResult,
    *,
    overlays: SessionOverlays | None = None,
) -> HomeOsSliceState:
    """Build a HomeOsSliceState suitable for render + command handlers."""
    overlays = overlays or SessionOverlays()
    return HomeOsSliceState(
        work_items=list(result.models.work_items),
        coverage=list(result.models.coverage),
        proof=list(result.models.proof),
        overlays=[],
        repair_phase=overlays.repair_phase,
        repair_message=overlays.repair_message,
        display_name=result.display_name,
        simulation=not result.authenticated
        or SIM_AUTH_REPAIR_COMPLETION in result.simulation_tags
        or SIM_EPHEMERAL_SCENARIO in result.simulation_tags
        or SIM_FUTURE_PREVIEW_SCENARIO in result.simulation_tags,
        seeded_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )


def _apply_coverage_override(
    item: CoverageItem,
    overrides: dict[str, str],
) -> CoverageItem:
    desired = overrides.get(item.provider)
    if not desired:
        return item
    try:
        auth = AuthPosture(desired)
    except ValueError:
        return item
    if auth is AuthPosture.VALID:
        from mighty.workitem.coverage import CoverageHealth, VerificationState

        return CoverageItem(
            provider=item.provider,
            status=item.status,
            health=CoverageHealth.HEALTHY,
            capabilities=item.capabilities,
            verification=VerificationState.VERIFIED,
            discovery=item.discovery,
            authentication=auth,
            monitoring="active",
            display_name=item.display_name,
        )
    return item


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
