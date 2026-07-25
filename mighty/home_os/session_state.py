"""Session-backed state for the Marriott auth-repair Home OS slice.

Holds canonical models + repair UI phase. Not a system of record —
HomeState is always re-projected via mighty.workitem.project_home.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from mighty.home_os.gate import SESSION_FLAG
from mighty.home_os.marriott_scenario import initial_canonical_models
from mighty.workitem.coverage import CoverageItem
from mighty.workitem.model import WorkItem
from mighty.workitem.projection_inputs import CanonicalModels, WorkItemOverlay
from mighty.workitem.proof import ProofItem

# Synthetic staging identity — never written to users table.
HOME_OS_USER_ID = "home-os-auth-repair-session"
HOME_OS_DISPLAY_NAME = "Jordan"
HOME_OS_LABEL = "Home OS Preview"

SESSION_STATE_KEY = "home_os_slice"
SESSION_STARTED_AT = "home_os_started_at"


class RepairPhase(str, Enum):
    """UI interaction phase for the in-place Marriott repair (not WorkItem.state)."""

    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    SUCCEEDED = "succeeded"
    EXPIRED = "expired"


@dataclass
class HomeOsSliceState:
    """Mutable session snapshot for the staging vertical slice."""

    work_items: list[WorkItem]
    coverage: list[CoverageItem]
    proof: list[ProofItem]
    overlays: list[WorkItemOverlay]
    repair_phase: RepairPhase
    repair_message: str
    display_name: str
    simulation: bool
    seeded_at: str

    def canonical_models(self) -> CanonicalModels:
        return CanonicalModels(
            work_items=tuple(self.work_items),
            coverage=tuple(self.coverage),
            proof=tuple(self.proof),
        )

    def to_session_dict(self) -> dict[str, Any]:
        return {
            "work_items": [item.to_dict() for item in self.work_items],
            "coverage": [item.to_dict() for item in self.coverage],
            "proof": [item.to_dict() for item in self.proof],
            "overlays": [_overlay_to_dict(o) for o in self.overlays],
            "repair_phase": self.repair_phase.value,
            "repair_message": self.repair_message,
            "display_name": self.display_name,
            "simulation": self.simulation,
            "seeded_at": self.seeded_at,
        }

    @classmethod
    def from_session_dict(cls, payload: Mapping[str, Any]) -> HomeOsSliceState:
        overlays_raw = payload.get("overlays") or []
        return cls(
            work_items=[WorkItem.from_dict(i) for i in payload.get("work_items") or []],
            coverage=[
                CoverageItem.from_dict(i) for i in payload.get("coverage") or []
            ],
            proof=[ProofItem.from_dict(i) for i in payload.get("proof") or []],
            overlays=[_overlay_from_dict(o) for o in overlays_raw],
            repair_phase=RepairPhase(
                str(payload.get("repair_phase") or RepairPhase.IDLE.value)
            ),
            repair_message=str(payload.get("repair_message") or ""),
            display_name=str(payload.get("display_name") or HOME_OS_DISPLAY_NAME),
            simulation=bool(payload.get("simulation", True)),
            seeded_at=str(payload.get("seeded_at") or ""),
        )


def _overlay_to_dict(overlay: WorkItemOverlay) -> dict[str, Any]:
    return {
        "work_item_id": overlay.work_item_id,
        "deferred_until": (
            None
            if overlay.deferred_until is None
            else overlay.deferred_until.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        ),
        "dismissed": overlay.dismissed,
        "inactive": overlay.inactive,
    }


def _overlay_from_dict(payload: Mapping[str, Any]) -> WorkItemOverlay:
    raw_until = payload.get("deferred_until")
    until = None
    if raw_until:
        text = str(raw_until).replace("Z", "+00:00")
        until = datetime.fromisoformat(text)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    return WorkItemOverlay(
        work_item_id=str(payload.get("work_item_id") or ""),
        deferred_until=until,
        dismissed=bool(payload.get("dismissed")),
        inactive=bool(payload.get("inactive")),
    )


def new_slice_state(*, as_of: datetime | None = None) -> HomeOsSliceState:
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    models = initial_canonical_models(as_of=now)
    return HomeOsSliceState(
        work_items=list(models.work_items),
        coverage=list(models.coverage),
        proof=list(models.proof),
        overlays=[],
        repair_phase=RepairPhase.IDLE,
        repair_message="",
        display_name=HOME_OS_DISPLAY_NAME,
        simulation=True,
        seeded_at=now.replace(microsecond=0).isoformat(),
    )


def begin_home_os_session(session: Any, *, as_of: datetime | None = None) -> HomeOsSliceState:
    """Install an ephemeral Home OS session. Does not touch the users table."""
    state = new_slice_state(as_of=as_of)
    session.clear()
    session[SESSION_FLAG] = True
    session[SESSION_STARTED_AT] = state.seeded_at
    session[SESSION_STATE_KEY] = state.to_session_dict()
    session["user_id"] = HOME_OS_USER_ID
    session["demo_mode"] = True
    session.permanent = False
    return state


def load_slice_state(session: Mapping[str, Any] | None) -> HomeOsSliceState | None:
    if not session or not session.get(SESSION_FLAG):
        return None
    raw = session.get(SESSION_STATE_KEY)
    if not isinstance(raw, Mapping):
        return None
    return HomeOsSliceState.from_session_dict(raw)


def save_slice_state(session: Any, state: HomeOsSliceState) -> None:
    session[SESSION_STATE_KEY] = state.to_session_dict()


def synthetic_user_row() -> Mapping[str, Any]:
    return {
        "id": HOME_OS_USER_ID,
        "email": HOME_OS_LABEL,
        "preferred_name": HOME_OS_DISPLAY_NAME,
        "api_key": "mk_home_os_preview_not_a_real_key",
        "password_hash": "",
        "created_at": "home-os-preview",
        "extension_version": "home-os-preview",
        "extension_last_seen_at": datetime.now(timezone.utc).isoformat(),
        "intent_summary": None,
        "type_affinity": None,
    }
