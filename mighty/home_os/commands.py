"""Home OS Marriott repair commands — server-side lifecycle application.

All mutation of WorkItems / Proof / Coverage happens here using
``WorkItemLifecycle``. Templates and client scripts must not implement
business rules.

Simulation is explicit: this slice confirms a staged repair rather than
performing a live Marriott authentication. Production auth paths are not
invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from mighty.home_os.marriott_scenario import (
    SIMULATION_MODE,
    WORK_ITEM_ID,
    build_access_restored_proof,
    build_marriott_coverage,
)
from mighty.home_os.session_state import HomeOsSliceState, RepairPhase
from mighty.workitem.lifecycle import WorkItemLifecycle
from mighty.workitem.model import WorkItem, WorkItemState
from mighty.workitem.projection import project_home
from mighty.workitem.home_state import HomeState


class CommandError(ValueError):
    """Raised when a Home OS command cannot be applied."""


@dataclass(frozen=True)
class CommandResult:
    state: HomeOsSliceState
    home: HomeState


def project_slice(slice_state: HomeOsSliceState, *, as_of: datetime) -> HomeState:
    """Pure projection — thin wrapper over mighty.workitem.project_home."""
    return project_home(
        slice_state.canonical_models(),
        slice_state.overlays,
        as_of=as_of,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _find_work_item(slice_state: HomeOsSliceState, work_item_id: str) -> WorkItem:
    for item in slice_state.work_items:
        if item.id == work_item_id:
            return item
    raise CommandError(f"Unknown WorkItem {work_item_id!r}")


def _replace_item(slice_state: HomeOsSliceState, updated: WorkItem) -> None:
    slice_state.work_items = [
        updated if item.id == updated.id else item for item in slice_state.work_items
    ]


def _replace_coverage_marriott(slice_state: HomeOsSliceState, *, signed_out: bool) -> None:
    new_row = build_marriott_coverage(signed_out=signed_out)
    others = [c for c in slice_state.coverage if c.provider != new_row.provider]
    slice_state.coverage = others + [new_row]


def apply_expiration_if_needed(
    slice_state: HomeOsSliceState,
    *,
    as_of: datetime,
) -> HomeOsSliceState:
    """Expire stale repair WorkItems and supersede with a fresh Interrupt id."""
    from mighty.home_os.marriott_scenario import build_marriott_interrupt

    as_of = _aware(as_of)
    life = WorkItemLifecycle()
    renewed: list[WorkItem] = []
    kept: list[WorkItem] = []

    for item in list(slice_state.work_items):
        if item.provider != "marriott" or item.type.value != "interrupt":
            kept.append(item)
            continue
        if item.state in (
            WorkItemState.COMPLETED,
            WorkItemState.PROOF,
            WorkItemState.ARCHIVED,
        ):
            kept.append(item)
            continue
        if item.expires_at is None or as_of < item.expires_at:
            kept.append(item)
            continue

        result = life.expire(item, as_of=as_of)
        kept.append(result.work_item)
        payload = build_marriott_interrupt(
            as_of=as_of, state=WorkItemState.EXPANDED
        ).to_dict()
        payload["id"] = f"{WORK_ITEM_ID}:renewed:{int(as_of.timestamp())}"
        renewed.append(WorkItem.from_dict(payload))
        slice_state.repair_phase = RepairPhase.EXPIRED
        slice_state.repair_message = (
            "This sign-in request expired. Mighty still cannot see Marriott — "
            "start a fresh sign-in when you are ready."
        )

    slice_state.work_items = kept + renewed
    return slice_state


def start_repair(
    slice_state: HomeOsSliceState,
    *,
    work_item_id: str,
    as_of: datetime,
) -> CommandResult:
    """Open the in-place repair interaction (simulation)."""
    as_of = _aware(as_of)
    item = _find_work_item(slice_state, work_item_id)
    if item.state in (
        WorkItemState.COMPLETED,
        WorkItemState.PROOF,
        WorkItemState.ARCHIVED,
    ):
        raise CommandError("This work item is already resolved.")
    if item.expires_at is not None and as_of >= item.expires_at:
        apply_expiration_if_needed(slice_state, as_of=as_of)
        raise CommandError("This sign-in request has expired.")

    # Ensure expanded for the focused interaction.
    life = WorkItemLifecycle()
    if item.state is WorkItemState.VISIBLE:
        item = life.expand(item, as_of=as_of)
        _replace_item(slice_state, item)
    elif item.state is WorkItemState.CREATED:
        item = life.make_visible(item, as_of=as_of)
        item = life.expand(item, as_of=as_of)
        _replace_item(slice_state, item)

    slice_state.repair_phase = RepairPhase.IN_PROGRESS
    slice_state.repair_message = (
        "Confirm the staged Marriott sign-in to restore Mighty’s access. "
        f"({SIMULATION_MODE}: no live Marriott credentials are used.)"
    )
    return CommandResult(state=slice_state, home=project_slice(slice_state, as_of=as_of))


def complete_repair(
    slice_state: HomeOsSliceState,
    *,
    work_item_id: str,
    as_of: datetime,
) -> CommandResult:
    """Simulated successful repair → lifecycle complete + Proof + coverage."""
    as_of = _aware(as_of)
    if not slice_state.simulation:
        raise CommandError(
            "Live Marriott authentication is not available in this slice."
        )
    item = _find_work_item(slice_state, work_item_id)
    if item.state in (
        WorkItemState.COMPLETED,
        WorkItemState.PROOF,
        WorkItemState.ARCHIVED,
    ):
        raise CommandError("This work item is already resolved.")

    life = WorkItemLifecycle()
    if item.state is WorkItemState.VISIBLE:
        item = life.expand(item, as_of=as_of)
    result = life.complete(
        item,
        as_of=as_of,
        earn_proof=True,
        proof_summary="Marriott access restored — Mighty can watch it again",
        proof_id=None,
        outcome_class="access_restored",
        impact="high",
    )
    # Prefer scenario proof identity for stable copy; bind via lifecycle result.
    proof = result.proof or build_access_restored_proof(
        as_of=as_of, work_item_id=work_item_id
    )
    if result.work_item.proof_reference != proof.id and result.proof is not None:
        proof = result.proof

    _replace_item(slice_state, result.work_item)
    if proof is not None and all(p.id != proof.id for p in slice_state.proof):
        slice_state.proof = [proof, *slice_state.proof]
    _replace_coverage_marriott(slice_state, signed_out=False)
    slice_state.repair_phase = RepairPhase.SUCCEEDED
    slice_state.repair_message = (
        "Marriott is signed in again. Mighty can watch it from Home."
    )
    # Archive after proof so it leaves effective queue cleanly.
    if result.work_item.state is WorkItemState.PROOF:
        archived = life.archive(result.work_item, as_of=as_of, reason="proof_bound")
        _replace_item(slice_state, archived)

    return CommandResult(state=slice_state, home=project_slice(slice_state, as_of=as_of))


def fail_repair(
    slice_state: HomeOsSliceState,
    *,
    work_item_id: str,
    as_of: datetime,
) -> CommandResult:
    """Simulated failure — WorkItem remains actionable on Home."""
    as_of = _aware(as_of)
    item = _find_work_item(slice_state, work_item_id)
    if item.state in (
        WorkItemState.COMPLETED,
        WorkItemState.PROOF,
        WorkItemState.ARCHIVED,
    ):
        raise CommandError("This work item is already resolved.")

    life = WorkItemLifecycle()
    if item.state is WorkItemState.VISIBLE:
        item = life.expand(item, as_of=as_of)
        _replace_item(slice_state, item)

    _replace_coverage_marriott(slice_state, signed_out=True)
    slice_state.repair_phase = RepairPhase.FAILED
    slice_state.repair_message = (
        "Marriott sign-in did not finish. Mighty still cannot see Marriott. "
        "Try signing in again from Home when you are ready."
    )
    return CommandResult(state=slice_state, home=project_slice(slice_state, as_of=as_of))


def cancel_repair(
    slice_state: HomeOsSliceState,
    *,
    work_item_id: str,
    as_of: datetime,
) -> CommandResult:
    """User cancels — return to the same Home interrupt state."""
    as_of = _aware(as_of)
    item = _find_work_item(slice_state, work_item_id)
    # Keep expanded interrupt; close interaction phase only.
    if item.state not in (
        WorkItemState.COMPLETED,
        WorkItemState.PROOF,
        WorkItemState.ARCHIVED,
    ):
        life = WorkItemLifecycle()
        if item.state is WorkItemState.VISIBLE:
            item = life.expand(item, as_of=as_of)
            _replace_item(slice_state, item)
    slice_state.repair_phase = RepairPhase.IDLE
    slice_state.repair_message = ""
    return CommandResult(state=slice_state, home=project_slice(slice_state, as_of=as_of))
