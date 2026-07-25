"""WorkItem lifecycle manager — pure command application.

Owns transition orchestration for complete / defer / dismiss / expire /
expand / proof binding. Does not persist, call APIs, or project Home UI.

Owning domains supply facts; this manager returns updated WorkItem (and
optional ProofItem) values for the caller to store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from mighty.workitem.model import (
    WorkItem,
    WorkItemState,
    WorkItemType,
)
from mighty.workitem.proof import ProofItem
from mighty.workitem.state_machine import (
    IllegalTransition,
    TransitionContext,
    transition,
)


class LifecycleError(ValueError):
    """Raised when a lifecycle command cannot be applied."""


DEFAULT_DEFER_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class LifecycleResult:
    """Outcome of one lifecycle command."""

    work_item: WorkItem
    proof: ProofItem | None = None


class WorkItemLifecycle:
    """Pure lifecycle operations over WorkItem instances."""

    def make_visible(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
    ) -> WorkItem:
        """created → visible."""
        ctx = TransitionContext(as_of=_aware(as_of), reason="make_visible")
        return transition(item, WorkItemState.VISIBLE, context=ctx)

    def expand(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
    ) -> WorkItem:
        """visible → expanded (ranking-selected top item)."""
        if item.state is WorkItemState.EXPANDED:
            return item
        ctx = TransitionContext(as_of=_aware(as_of), reason="expand")
        return transition(item, WorkItemState.EXPANDED, context=ctx)

    def demote_to_visible(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
    ) -> WorkItem:
        """expanded → visible when ranking selects a higher item."""
        ctx = TransitionContext(as_of=_aware(as_of), reason="demote")
        return transition(item, WorkItemState.VISIBLE, context=ctx)

    def defer(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
        until: datetime | None = None,
    ) -> tuple[WorkItem, datetime]:
        """visible|expanded → deferred.

        Returns ``(updated_item, deferred_until)``. The quiet window is an
        overlay concern; callers should persist ``deferred_until`` as overlay.
        """
        as_of = _aware(as_of)
        if not item.deferrable:
            raise LifecycleError(f"WorkItem {item.id!r} is not deferrable")
        if item.state not in (WorkItemState.VISIBLE, WorkItemState.EXPANDED):
            raise LifecycleError(
                f"cannot defer WorkItem {item.id!r} from state {item.state.value!r}"
            )
        deferred_until = _aware(until) if until is not None else as_of + DEFAULT_DEFER_WINDOW
        if deferred_until <= as_of:
            raise LifecycleError("defer until must be strictly after as_of")
        ctx = TransitionContext(as_of=as_of, reason="defer")
        updated = transition(item, WorkItemState.DEFERRED, context=ctx)
        return updated, deferred_until

    def reactivate(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
    ) -> WorkItem:
        """deferred → visible when quiet window ends or condition worsens."""
        ctx = TransitionContext(as_of=_aware(as_of), reason="reactivate")
        return transition(item, WorkItemState.VISIBLE, context=ctx)

    def complete(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
        earn_proof: bool = False,
        proof_summary: str | None = None,
        proof_id: str | None = None,
        outcome_class: str = "completion",
        impact: str = "normal",
    ) -> LifecycleResult:
        """visible|expanded|deferred → completed [→ proof].

        Dismiss/defer never call this path for fabricating Proof.
        Approvals always earn Proof for the decision when ``earn_proof`` is True
        (callers should pass True for approve/reject/expire decisions).
        """
        as_of = _aware(as_of)
        if item.state not in (
            WorkItemState.VISIBLE,
            WorkItemState.EXPANDED,
            WorkItemState.DEFERRED,
        ):
            raise LifecycleError(
                f"cannot complete WorkItem {item.id!r} from state {item.state.value!r}"
            )
        ctx = TransitionContext(as_of=as_of, reason="complete")
        completed = transition(item, WorkItemState.COMPLETED, context=ctx)

        # Approvals always earn decision Proof. Other types only when requested.
        should_earn = True if item.type is WorkItemType.APPROVAL else bool(earn_proof)

        if not should_earn:
            return LifecycleResult(work_item=completed, proof=None)

        proof = create_proof_for_completion(
            completed,
            as_of=as_of,
            summary=proof_summary,
            proof_id=proof_id,
            outcome_class=outcome_class,
            impact=impact,
        )
        ctx_proof = TransitionContext(
            as_of=as_of, proof_earned=True, reason="proof"
        )
        with_proof = transition(
            completed,
            WorkItemState.PROOF,
            context=ctx_proof,
            proof_reference=proof.id,
        )
        return LifecycleResult(work_item=with_proof, proof=proof)

    def dismiss(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
    ) -> WorkItem:
        """visible|expanded|deferred → archived via dismiss (no Proof)."""
        as_of = _aware(as_of)
        if not item.dismissible:
            raise LifecycleError(f"WorkItem {item.id!r} is not dismissible")
        if item.type is WorkItemType.APPROVAL:
            raise LifecycleError(
                "Approvals cannot be dismissed; record approve or reject"
            )
        if item.state not in (
            WorkItemState.VISIBLE,
            WorkItemState.EXPANDED,
            WorkItemState.DEFERRED,
        ):
            raise LifecycleError(
                f"cannot dismiss WorkItem {item.id!r} from state {item.state.value!r}"
            )
        ctx = TransitionContext(
            as_of=as_of, reason="dismiss", dismissible_override=True
        )
        return transition(item, WorkItemState.ARCHIVED, context=ctx)

    def expire(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
        record_approval_expiry: bool = True,
    ) -> LifecycleResult:
        """Expire an actionable item when ``as_of >= expires_at``.

        Approvals resolve as expired with Proof (decision recorded).
        Opportunities archive without scolding / without inventing Proof.
        """
        as_of = _aware(as_of)
        if item.expires_at is None:
            raise LifecycleError(
                f"WorkItem {item.id!r} has no expires_at; cannot expire"
            )
        if as_of < item.expires_at:
            raise LifecycleError(
                f"WorkItem {item.id!r} is not yet expired at {as_of.isoformat()}"
            )
        if item.state in (
            WorkItemState.COMPLETED,
            WorkItemState.PROOF,
            WorkItemState.ARCHIVED,
        ):
            return LifecycleResult(work_item=item, proof=None)

        if item.type is WorkItemType.APPROVAL and record_approval_expiry:
            # Decision path: completed + proof for expired consent.
            try:
                if item.state is WorkItemState.CREATED:
                    item = transition(
                        item,
                        WorkItemState.VISIBLE,
                        context=TransitionContext(as_of=as_of, reason="expire_enter"),
                    )
                return self.complete(
                    item,
                    as_of=as_of,
                    earn_proof=True,
                    proof_summary=f"Approval expired: {item.title}",
                    outcome_class="approval_expired",
                    impact="high",
                )
            except (IllegalTransition, LifecycleError):
                # Fall through to archive if somehow stuck.
                pass

        # Default: archive without Proof.
        from_state = item.state
        if from_state is WorkItemState.CREATED:
            ctx = TransitionContext(as_of=as_of, reason="expire")
            archived = transition(item, WorkItemState.ARCHIVED, context=ctx)
            return LifecycleResult(work_item=archived, proof=None)

        ctx = TransitionContext(as_of=as_of, reason="expire")
        archived = transition(item, WorkItemState.ARCHIVED, context=ctx)
        return LifecycleResult(work_item=archived, proof=None)

    def archive(
        self,
        item: WorkItem,
        *,
        as_of: datetime,
        reason: str = "archive",
    ) -> WorkItem:
        """Move to archived along a legal edge (withdraw / post-proof / etc.)."""
        as_of = _aware(as_of)
        if item.state is WorkItemState.ARCHIVED:
            return item
        ctx = TransitionContext(as_of=as_of, reason=reason)
        return transition(item, WorkItemState.ARCHIVED, context=ctx)

    def bind_proof(
        self,
        item: WorkItem,
        proof: ProofItem,
        *,
        as_of: datetime,
    ) -> WorkItem:
        """completed → proof with proof_reference set."""
        if item.state is not WorkItemState.COMPLETED:
            raise LifecycleError(
                f"bind_proof requires completed state, got {item.state.value!r}"
            )
        as_of = _aware(as_of)
        ctx = TransitionContext(as_of=as_of, proof_earned=True, reason="bind_proof")
        return transition(
            item,
            WorkItemState.PROOF,
            context=ctx,
            proof_reference=proof.id,
        )

    def apply_expirations(
        self,
        items: Sequence[WorkItem],
        *,
        as_of: datetime,
    ) -> tuple[LifecycleResult, ...]:
        """Expire all items whose ``expires_at`` has passed."""
        as_of = _aware(as_of)
        results: list[LifecycleResult] = []
        for item in items:
            if item.expires_at is None:
                continue
            if as_of < item.expires_at:
                continue
            if item.state in (
                WorkItemState.COMPLETED,
                WorkItemState.PROOF,
                WorkItemState.ARCHIVED,
            ):
                continue
            results.append(self.expire(item, as_of=as_of))
        return tuple(results)


def create_proof_for_completion(
    item: WorkItem,
    *,
    as_of: datetime,
    summary: str | None = None,
    proof_id: str | None = None,
    outcome_class: str = "completion",
    impact: str = "normal",
) -> ProofItem:
    """Create a ProofItem earned from a qualifying Work Item completion.

    Dismiss/defer must not call this. Returns a new ProofItem; does not
    mutate the WorkItem (lifecycle.complete binds the reference).
    """
    as_of = _aware(as_of)
    pid = (proof_id or f"proof:{item.id}:{int(as_of.timestamp())}").strip()
    text = (summary or f"Completed: {item.title}").strip()
    return ProofItem(
        id=pid,
        outcome_at=as_of,
        summary=text,
        provider=item.provider,
        outcome_class=outcome_class,
        work_item_id=item.id,
        impact=impact,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
