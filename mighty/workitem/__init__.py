"""Canonical WorkItem engine for Home OS.

Pure domain package. No Flask routes, HTML, CSS, JavaScript, UI, or
external I/O. Any surface may consume these models and projections.

See docs/WORKITEM_ENGINE.md, docs/HOME_OS_DOMAIN_MODEL.md,
and docs/HOME_OS_BEHAVIOR.md.
"""

from __future__ import annotations

from mighty.workitem.coverage import CoverageItem
from mighty.workitem.home_state import HomeStatusMode, HomeState
from mighty.workitem.lifecycle import (
    LifecycleError,
    WorkItemLifecycle,
    create_proof_for_completion,
)
from mighty.workitem.model import (
    WORK_ITEM_SCHEMA_VERSION,
    UrgencyBand,
    WorkItem,
    WorkItemAction,
    WorkItemEvidence,
    WorkItemPriority,
    WorkItemState,
    WorkItemType,
    WorkItemValidationError,
)
from mighty.workitem.projection import project_home
from mighty.workitem.projection_inputs import (
    CanonicalModels,
    WorkItemOverlay,
    effective_work_items,
)
from mighty.workitem.proof import ProofItem, collapse_proof_items
from mighty.workitem.ranking import RankingError, rank_work_items
from mighty.workitem.state_machine import (
    IllegalTransition,
    TransitionContext,
    can_transition,
    transition,
)

__all__ = [
    "WORK_ITEM_SCHEMA_VERSION",
    "CanonicalModels",
    "CoverageItem",
    "HomeState",
    "HomeStatusMode",
    "IllegalTransition",
    "LifecycleError",
    "ProofItem",
    "RankingError",
    "TransitionContext",
    "UrgencyBand",
    "WorkItem",
    "WorkItemAction",
    "WorkItemEvidence",
    "WorkItemLifecycle",
    "WorkItemOverlay",
    "WorkItemPriority",
    "WorkItemState",
    "WorkItemType",
    "WorkItemValidationError",
    "can_transition",
    "collapse_proof_items",
    "create_proof_for_completion",
    "effective_work_items",
    "project_home",
    "rank_work_items",
    "transition",
]
