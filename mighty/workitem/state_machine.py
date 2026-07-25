"""WorkItem state machine — shared skeleton with type-policy guards.

Pure transition validation and application. Does not persist, project Home,
or invent Work Items.

See docs/HOME_OS_DOMAIN_MODEL.md §7.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import FrozenSet

from mighty.workitem.model import WorkItem, WorkItemState, WorkItemType


class IllegalTransition(ValueError):
    """Raised when a requested lifecycle transition is not legal."""


@dataclass(frozen=True)
class TransitionContext:
    """Guards and side facts for evaluating a transition."""

    as_of: datetime
    deferrable_override: bool | None = None
    dismissible_override: bool | None = None
    proof_earned: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        as_of = self.as_of
        if as_of.tzinfo is None:
            object.__setattr__(self, "as_of", as_of.replace(tzinfo=timezone.utc))


# Legal edges from domain model §7.
_LEGAL: dict[WorkItemState, FrozenSet[WorkItemState]] = {
    WorkItemState.CREATED: frozenset(
        {WorkItemState.VISIBLE, WorkItemState.ARCHIVED}
    ),
    WorkItemState.VISIBLE: frozenset(
        {
            WorkItemState.EXPANDED,
            WorkItemState.DEFERRED,
            WorkItemState.COMPLETED,
            WorkItemState.ARCHIVED,
        }
    ),
    WorkItemState.EXPANDED: frozenset(
        {
            WorkItemState.VISIBLE,
            WorkItemState.DEFERRED,
            WorkItemState.COMPLETED,
            WorkItemState.ARCHIVED,
        }
    ),
    WorkItemState.DEFERRED: frozenset(
        {
            WorkItemState.VISIBLE,
            WorkItemState.COMPLETED,
            WorkItemState.ARCHIVED,
        }
    ),
    WorkItemState.COMPLETED: frozenset(
        {WorkItemState.PROOF, WorkItemState.ARCHIVED}
    ),
    WorkItemState.PROOF: frozenset({WorkItemState.ARCHIVED}),
    WorkItemState.ARCHIVED: frozenset(),
}


def can_transition(
    item: WorkItem,
    to_state: WorkItemState,
    *,
    context: TransitionContext | None = None,
) -> bool:
    """Return True when ``item.state → to_state`` is legal under policy."""
    try:
        _validate(item, to_state, context=context)
    except IllegalTransition:
        return False
    return True


def transition(
    item: WorkItem,
    to_state: WorkItemState,
    *,
    context: TransitionContext | None = None,
    proof_reference: str | None = None,
) -> WorkItem:
    """Apply a legal state transition; return a new WorkItem.

    Does not mutate ``item``. Updates ``updated_at`` from ``context.as_of``
    when provided, otherwise leaves timestamps unchanged.
    """
    _validate(item, to_state, context=context)
    updates: dict = {"state": to_state}
    if context is not None:
        updates["updated_at"] = context.as_of
    if to_state is WorkItemState.PROOF:
        ref = proof_reference or item.proof_reference
        if not ref:
            raise IllegalTransition(
                "transition to proof requires proof_reference"
            )
        updates["proof_reference"] = ref
    return item.with_updates(**updates)


def _validate(
    item: WorkItem,
    to_state: WorkItemState,
    *,
    context: TransitionContext | None,
) -> None:
    if not isinstance(to_state, WorkItemState):
        raise IllegalTransition(f"to_state must be a WorkItemState, got {to_state!r}")
    if item.state is to_state:
        raise IllegalTransition(
            f"WorkItem {item.id!r} is already in state {to_state.value!r}"
        )
    allowed = _LEGAL.get(item.state, frozenset())
    if to_state not in allowed:
        raise IllegalTransition(
            f"illegal transition {item.state.value!r} → {to_state.value!r} "
            f"for WorkItem {item.id!r}"
        )

    # Terminal reopen bans.
    if item.state is WorkItemState.PROOF and to_state in (
        WorkItemState.VISIBLE,
        WorkItemState.EXPANDED,
        WorkItemState.DEFERRED,
        WorkItemState.COMPLETED,
        WorkItemState.CREATED,
    ):
        raise IllegalTransition("proof cannot reopen work")
    if item.state is WorkItemState.ARCHIVED:
        raise IllegalTransition(
            "archived WorkItems cannot re-enter active states; create a new id"
        )
    if item.state is WorkItemState.COMPLETED and to_state is WorkItemState.DEFERRED:
        raise IllegalTransition("completed cannot transition to deferred")

    if to_state is WorkItemState.DEFERRED:
        deferrable = (
            item.deferrable
            if context is None or context.deferrable_override is None
            else context.deferrable_override
        )
        if not deferrable:
            raise IllegalTransition(
                f"WorkItem {item.id!r} is not deferrable"
            )

    if to_state is WorkItemState.ARCHIVED and _is_dismiss_archive(item, context):
        dismissible = (
            item.dismissible
            if context is None or context.dismissible_override is None
            else context.dismissible_override
        )
        if not dismissible:
            raise IllegalTransition(
                f"WorkItem {item.id!r} is not dismissible"
            )
        if item.type is WorkItemType.APPROVAL:
            raise IllegalTransition(
                "Approvals must be decided (approve/reject), not dismissed"
            )

    if to_state is WorkItemState.PROOF:
        if context is not None and not context.proof_earned and not item.proof_reference:
            # Allow when proof_reference will be supplied by transition().
            pass


def _is_dismiss_archive(
    item: WorkItem,
    context: TransitionContext | None,
) -> bool:
    """True when archival is a user dismiss (not expiry / withdraw / supersede)."""
    if context is None:
        return False
    reason = (context.reason or "").strip().lower()
    return reason in {"dismiss", "dismissed", "user_dismiss"}
