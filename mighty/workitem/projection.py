"""Pure HomeProjection — canonical models + overlays + as_of → HomeState.

No side effects. No hidden clock. No business mutation.
See docs/HOME_OS_DOMAIN_MODEL.md §5.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from mighty.workitem.coverage import CoverageItem
from mighty.workitem.home_state import HomeState, HomeStatusMode
from mighty.workitem.model import WorkItem, WorkItemType
from mighty.workitem.projection_inputs import (
    CanonicalModels,
    WorkItemOverlay,
    effective_work_items,
)
from mighty.workitem.proof import ProofDisclosure, collapse_proof_items
from mighty.workitem.ranking import rank_work_items


def project_home(
    canonical_models: CanonicalModels,
    work_item_overlays: Sequence[WorkItemOverlay] = (),
    *,
    as_of: datetime,
) -> HomeState:
    """Pure projection: same inputs + overlays + as_of ⇒ identical HomeState."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    effective = effective_work_items(
        canonical_models.work_items,
        work_item_overlays,
        as_of=as_of,
    )
    ranked = rank_work_items(effective, as_of=as_of)

    expanded_id: str | None = ranked[0].id if ranked else None
    silence = expanded_id is None
    status = _derive_status(ranked)
    coverage = _project_coverage(canonical_models.coverage)
    proof = collapse_proof_items(canonical_models.proof, as_of=as_of)
    provenance = _provenance(ranked, coverage, proof)

    return HomeState(
        as_of=as_of,
        status=status,
        work_queue=ranked,
        expanded_work_item_id=expanded_id,
        coverage=coverage,
        proof=proof,
        silence=silence,
        provenance=provenance,
    )


def _derive_status(ranked: Sequence[WorkItem]) -> HomeStatusMode:
    if not ranked:
        return HomeStatusMode.CALM
    top = ranked[0]
    if top.type in (WorkItemType.INTERRUPT, WorkItemType.APPROVAL):
        return HomeStatusMode.NEEDS_USER
    if top.type is WorkItemType.OPPORTUNITY:
        return HomeStatusMode.VALUE_WAITING
    if top.type is WorkItemType.SETUP:
        return HomeStatusMode.SETUP_INCOMPLETE
    return HomeStatusMode.CALM


def _project_coverage(
    items: Sequence[CoverageItem],
) -> tuple[CoverageItem, ...]:
    """Stable coverage order: provider ascending. Omit nothing fabricated."""
    if not items:
        return ()
    return tuple(sorted(items, key=lambda c: c.provider))


def _provenance(
    ranked: Sequence[WorkItem],
    coverage: Sequence[CoverageItem],
    proof: Sequence[ProofDisclosure],
) -> tuple[tuple[str, str], ...]:
    refs: list[tuple[str, str]] = []
    for item in ranked:
        refs.append(("work_item", item.id))
        refs.append(("owner_domain", f"{item.id}:{item.owner_domain}"))
    for cov in coverage:
        refs.append(("coverage", cov.provider))
    for row in proof:
        refs.append(("proof", row.id))
    return tuple(refs)
