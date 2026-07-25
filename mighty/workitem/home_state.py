"""Canonical HomeState — disposable projection snapshot (Home OS §1).

Not a system of record. Distinct from ``mighty.home_state.HomeState``
(enrollment enum for the legacy Living Calm surface).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from mighty.workitem.coverage import CoverageItem
from mighty.workitem.model import WorkItem
from mighty.workitem.proof import ProofDisclosure


class HomeStatusMode(str, Enum):
    """Semantic answer to “What needs me?” — not presentation."""

    CALM = "calm"
    NEEDS_USER = "needs_user"
    VALUE_WAITING = "value_waiting"
    SETUP_INCOMPLETE = "setup_incomplete"


@dataclass(frozen=True)
class HomeState:
    """Complete, self-contained Home snapshot for one user at ``as_of``."""

    as_of: datetime
    status: HomeStatusMode
    work_queue: tuple[WorkItem, ...]
    expanded_work_item_id: str | None
    coverage: tuple[CoverageItem, ...]
    proof: tuple[ProofDisclosure, ...]
    silence: bool
    provenance: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.as_of, datetime):
            raise TypeError("as_of must be a datetime")
        if self.as_of.tzinfo is None:
            object.__setattr__(
                self, "as_of", self.as_of.replace(tzinfo=timezone.utc)
            )
        if not isinstance(self.status, HomeStatusMode):
            raise TypeError("status must be a HomeStatusMode")
        if not isinstance(self.work_queue, tuple):
            object.__setattr__(self, "work_queue", tuple(self.work_queue))
        if not isinstance(self.coverage, tuple):
            object.__setattr__(self, "coverage", tuple(self.coverage))
        if not isinstance(self.proof, tuple):
            object.__setattr__(self, "proof", tuple(self.proof))
        if not isinstance(self.provenance, tuple):
            object.__setattr__(self, "provenance", tuple(self.provenance))

        if self.expanded_work_item_id is not None:
            eid = str(self.expanded_work_item_id).strip()
            object.__setattr__(self, "expanded_work_item_id", eid or None)

        if self.silence and self.expanded_work_item_id is not None:
            raise ValueError("silence=True forbids an expanded Work Item")
        if not self.silence and self.expanded_work_item_id is None:
            raise ValueError(
                "non-silent HomeState requires expanded_work_item_id"
            )
        if self.expanded_work_item_id is not None:
            ids = {item.id for item in self.work_queue}
            if self.expanded_work_item_id not in ids:
                raise ValueError(
                    "expanded_work_item_id must be present in work_queue"
                )
            if self.work_queue and self.work_queue[0].id != self.expanded_work_item_id:
                raise ValueError(
                    "expanded_work_item_id must be the first ranked Work Item"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "status": self.status.value,
            "work_queue": [item.to_dict() for item in self.work_queue],
            "expanded_work_item_id": self.expanded_work_item_id,
            "coverage": [c.to_dict() for c in self.coverage],
            "proof": [p.to_dict() for p in self.proof],
            "silence": self.silence,
            "provenance": [[k, v] for k, v in self.provenance],
        }
