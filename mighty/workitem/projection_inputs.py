"""Projection inputs for pure HomeProjection.

Canonical models + Work Item overlays + as_of. No I/O, no UI, no mutation
of owning-domain ledgers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from mighty.workitem.coverage import CoverageItem
from mighty.workitem.model import WorkItem, WorkItemState
from mighty.workitem.proof import ProofItem


class OverlayValidationError(ValueError):
    """Raised when a WorkItemOverlay is invalid."""


@dataclass(frozen=True)
class WorkItemOverlay:
    """Lifecycle overlay owned outside HomeState (defer / dismiss / suppress)."""

    work_item_id: str
    deferred_until: datetime | None = None
    dismissed: bool = False
    inactive: bool = False

    def __post_init__(self) -> None:
        wid = str(self.work_item_id or "").strip()
        if not wid:
            raise OverlayValidationError("work_item_id must be a non-empty string")
        object.__setattr__(self, "work_item_id", wid)
        if self.deferred_until is not None:
            until = self.deferred_until
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            object.__setattr__(self, "deferred_until", until)
        if not isinstance(self.dismissed, bool):
            raise OverlayValidationError("dismissed must be a bool")
        if not isinstance(self.inactive, bool):
            raise OverlayValidationError("inactive must be a bool")


@dataclass(frozen=True)
class CanonicalModels:
    """Canonical inputs consumed by HomeProjection.

    Work Items (or owning records already mapped to Work Items), Coverage
    sources, and earned Proof sources. Projection never scrapes UI state.
    """

    work_items: tuple[WorkItem, ...] = ()
    coverage: tuple[CoverageItem, ...] = ()
    proof: tuple[ProofItem, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.work_items, tuple):
            object.__setattr__(self, "work_items", tuple(self.work_items))
        if not isinstance(self.coverage, tuple):
            object.__setattr__(self, "coverage", tuple(self.coverage))
        if not isinstance(self.proof, tuple):
            object.__setattr__(self, "proof", tuple(self.proof))


# States that never appear in the effective Work Queue.
_TERMINAL_OR_QUIET = frozenset(
    {
        WorkItemState.CREATED,
        WorkItemState.DEFERRED,
        WorkItemState.COMPLETED,
        WorkItemState.PROOF,
        WorkItemState.ARCHIVED,
    }
)


def effective_work_items(
    items: Sequence[WorkItem],
    overlays: Sequence[WorkItemOverlay],
    *,
    as_of: datetime,
) -> tuple[WorkItem, ...]:
    """Filter to Work Items eligible for the Work Queue.

    Effective when:
    - Not archived / completed / proof / created
    - Not deferred with an active quiet window (state or overlay)
    - Not expired (as_of < expires_at or expires_at is null)
    - Not suppressed by an owning-domain overlay (inactive / dismissed)
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    by_id = {o.work_item_id: o for o in overlays}
    effective: list[WorkItem] = []
    for item in items:
        overlay = by_id.get(item.id)
        if not _is_effective(item, overlay, as_of=as_of):
            continue
        effective.append(item)
    return tuple(effective)


def _is_effective(
    item: WorkItem,
    overlay: WorkItemOverlay | None,
    *,
    as_of: datetime,
) -> bool:
    if overlay is not None:
        if overlay.inactive or overlay.dismissed:
            return False
        if overlay.deferred_until is not None and as_of < overlay.deferred_until:
            return False

    if item.state is WorkItemState.ARCHIVED:
        return False
    if item.state is WorkItemState.COMPLETED:
        return False
    if item.state is WorkItemState.PROOF:
        return False
    if item.state is WorkItemState.CREATED:
        return False

    if item.state is WorkItemState.DEFERRED:
        # Deferred state alone removes from queue; overlay window also applies.
        # Reactivation is a lifecycle concern that moves state back to visible.
        return False

    if item.expires_at is not None and as_of >= item.expires_at:
        return False

    # visible | expanded remain eligible
    if item.state in (WorkItemState.VISIBLE, WorkItemState.EXPANDED):
        return True

    # Defensive: unknown future states are non-effective.
    if item.state in _TERMINAL_OR_QUIET:
        return False
    return False
