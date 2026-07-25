"""Canonical WorkItem model (Home OS domain authority).

Frozen identity + factual payload for one unit of user work. Contains no
ranking scores, UI instructions, routes, or mutable overlay state.

See docs/HOME_OS_DOMAIN_MODEL.md §2 and docs/HOME_OS_BEHAVIOR.md §2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


WORK_ITEM_SCHEMA_VERSION = 1


class WorkItemType(str, Enum):
    """Exactly four Work Item types (behavioral contract §2)."""

    INTERRUPT = "interrupt"
    APPROVAL = "approval"
    OPPORTUNITY = "opportunity"
    SETUP = "setup"


class WorkItemPriority(str, Enum):
    """Intrinsic class priority — first ranking key, not a free-form score.

    Must stay consistent with ``WorkItem.type`` and blocking Setup semantics.
    Order (highest first): interrupt → approval → setup_blocking →
    opportunity → setup_nonblocking.
    """

    INTERRUPT = "interrupt"
    APPROVAL = "approval"
    SETUP_BLOCKING = "setup_blocking"
    OPPORTUNITY = "opportunity"
    SETUP_NONBLOCKING = "setup_nonblocking"


class WorkItemState(str, Enum):
    """Lifecycle states (domain model §7)."""

    CREATED = "created"
    VISIBLE = "visible"
    EXPANDED = "expanded"
    DEFERRED = "deferred"
    COMPLETED = "completed"
    PROOF = "proof"
    ARCHIVED = "archived"


class UrgencyBand(str, Enum):
    """Within-class severity / urgency band (ranking key 1)."""

    HARD = "hard"
    HIGH = "high"
    NORMAL = "normal"
    SOFT = "soft"


# Lower number wins within the urgency ranking key.
_PRIORITY_RANK: dict[WorkItemPriority, int] = {
    WorkItemPriority.INTERRUPT: 1,
    WorkItemPriority.APPROVAL: 2,
    WorkItemPriority.SETUP_BLOCKING: 3,
    WorkItemPriority.OPPORTUNITY: 4,
    WorkItemPriority.SETUP_NONBLOCKING: 5,
}

_URGENCY_RANK: dict[UrgencyBand, int] = {
    UrgencyBand.HARD: 1,
    UrgencyBand.HIGH: 2,
    UrgencyBand.NORMAL: 3,
    UrgencyBand.SOFT: 4,
}

_ALLOWED_TYPE_PRIORITY: dict[WorkItemType, frozenset[WorkItemPriority]] = {
    WorkItemType.INTERRUPT: frozenset({WorkItemPriority.INTERRUPT}),
    WorkItemType.APPROVAL: frozenset({WorkItemPriority.APPROVAL}),
    WorkItemType.OPPORTUNITY: frozenset({WorkItemPriority.OPPORTUNITY}),
    WorkItemType.SETUP: frozenset(
        {WorkItemPriority.SETUP_BLOCKING, WorkItemPriority.SETUP_NONBLOCKING}
    ),
}


class WorkItemValidationError(ValueError):
    """Raised when a WorkItem payload violates the frozen contract."""


@dataclass(frozen=True)
class WorkItemAction:
    """Single machine-meaningful action with human-meaningful intent.

    Domain content only — not a route, layout hint, or CTA label authority.
    """

    key: str
    intent: str

    def __post_init__(self) -> None:
        key = str(self.key or "").strip()
        intent = str(self.intent or "").strip()
        if not key:
            raise WorkItemValidationError("action.key must be a non-empty string")
        if any(ch.isspace() for ch in key):
            raise WorkItemValidationError("action.key must not contain whitespace")
        if not intent:
            raise WorkItemValidationError("action.intent must be a non-empty string")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "intent", intent)

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "intent": self.intent}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WorkItemAction:
        if not isinstance(payload, Mapping):
            raise WorkItemValidationError("action must be a mapping")
        return cls(
            key=str(payload.get("key") or ""),
            intent=str(payload.get("intent") or ""),
        )


@dataclass(frozen=True)
class WorkItemEvidence:
    """Structured facts that justify the Work Item (domain model §2)."""

    facts: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.facts, tuple):
            object.__setattr__(self, "facts", tuple(self.facts))
        normalized: list[tuple[str, str]] = []
        for pair in self.facts:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise WorkItemValidationError(
                    "evidence.facts entries must be (key, value) pairs"
                )
            key = str(pair[0] or "").strip()
            value = str(pair[1] or "").strip()
            if not key:
                raise WorkItemValidationError("evidence fact key must be non-empty")
            normalized.append((key, value))
        object.__setattr__(self, "facts", tuple(normalized))

    def to_dict(self) -> dict[str, Any]:
        return {"facts": [[k, v] for k, v in self.facts]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> WorkItemEvidence:
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise WorkItemValidationError("evidence must be a mapping")
        raw = payload.get("facts") or ()
        if not isinstance(raw, (list, tuple)):
            raise WorkItemValidationError("evidence.facts must be a list or tuple")
        return cls(facts=tuple((str(a), str(b)) for a, b in raw))

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any] | None) -> WorkItemEvidence:
        if not mapping:
            return cls()
        return cls(
            facts=tuple(
                (str(k), str(v)) for k, v in mapping.items() if v is not None
            )
        )


@dataclass(frozen=True)
class WorkItem:
    """Immutable canonical Work Item instance.

    Lifecycle overlays (defer windows, dismiss) live outside this record and
    are applied at projection time. Ranking scores are never stored here.
    """

    id: str
    type: WorkItemType
    priority: WorkItemPriority
    title: str
    summary: str
    evidence: WorkItemEvidence
    primary_action: WorkItemAction
    secondary_action: WorkItemAction | None
    dismissible: bool
    deferrable: bool
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    proof_reference: str | None
    provider: str | None
    capability: str | None
    state: WorkItemState
    owner_domain: str
    urgency_band: UrgencyBand = UrgencyBand.NORMAL
    # Within-band assist: lower effort_weight ranks first (unblocks more / fewer steps).
    effort_weight: int = 100
    # Dependency: ids of Work Items that this item blocks (A blocks B ⇒ A above B).
    blocks: tuple[str, ...] = ()
    # Confidence that the item is real and actionable (0.0–1.0); within-band only.
    confidence: float = 1.0
    schema_version: int = WORK_ITEM_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != WORK_ITEM_SCHEMA_VERSION:
            raise WorkItemValidationError(
                f"schema_version must be {WORK_ITEM_SCHEMA_VERSION}, "
                f"got {self.schema_version!r}"
            )
        item_id = str(self.id or "").strip()
        if not item_id:
            raise WorkItemValidationError("id must be a non-empty string")
        object.__setattr__(self, "id", item_id)

        if not isinstance(self.type, WorkItemType):
            raise WorkItemValidationError("type must be a WorkItemType")
        if not isinstance(self.priority, WorkItemPriority):
            raise WorkItemValidationError("priority must be a WorkItemPriority")
        allowed = _ALLOWED_TYPE_PRIORITY[self.type]
        if self.priority not in allowed:
            raise WorkItemValidationError(
                f"priority {self.priority.value!r} is not valid for type "
                f"{self.type.value!r}"
            )

        title = str(self.title or "").strip()
        summary = str(self.summary or "").strip()
        if not title:
            raise WorkItemValidationError("title must be a non-empty string")
        if not summary:
            raise WorkItemValidationError("summary must be a non-empty string")
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "summary", summary)

        if not isinstance(self.evidence, WorkItemEvidence):
            raise WorkItemValidationError("evidence must be a WorkItemEvidence")
        if not isinstance(self.primary_action, WorkItemAction):
            raise WorkItemValidationError("primary_action must be a WorkItemAction")
        if self.secondary_action is not None and not isinstance(
            self.secondary_action, WorkItemAction
        ):
            raise WorkItemValidationError(
                "secondary_action must be a WorkItemAction or None"
            )
        if (
            self.secondary_action is not None
            and self.secondary_action.key == self.primary_action.key
        ):
            raise WorkItemValidationError(
                "secondary_action must not equal primary_action"
            )

        if not isinstance(self.dismissible, bool):
            raise WorkItemValidationError("dismissible must be a bool")
        if not isinstance(self.deferrable, bool):
            raise WorkItemValidationError("deferrable must be a bool")

        _enforce_type_policy(self)

        object.__setattr__(self, "created_at", _ensure_aware(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _ensure_aware(self.updated_at, "updated_at"))
        if self.expires_at is not None:
            object.__setattr__(
                self, "expires_at", _ensure_aware(self.expires_at, "expires_at")
            )

        if self.proof_reference is not None:
            ref = str(self.proof_reference).strip()
            if not ref:
                raise WorkItemValidationError(
                    "proof_reference must be non-empty when set"
                )
            object.__setattr__(self, "proof_reference", ref)

        provider = None if self.provider is None else str(self.provider).strip()
        capability = None if self.capability is None else str(self.capability).strip()
        object.__setattr__(self, "provider", provider or None)
        object.__setattr__(self, "capability", capability or None)

        if not isinstance(self.state, WorkItemState):
            raise WorkItemValidationError("state must be a WorkItemState")

        owner = str(self.owner_domain or "").strip()
        if not owner:
            raise WorkItemValidationError("owner_domain must be a non-empty string")
        object.__setattr__(self, "owner_domain", owner)

        if not isinstance(self.urgency_band, UrgencyBand):
            raise WorkItemValidationError("urgency_band must be an UrgencyBand")
        if not isinstance(self.effort_weight, int) or isinstance(self.effort_weight, bool):
            raise WorkItemValidationError("effort_weight must be an int")
        if self.effort_weight < 0:
            raise WorkItemValidationError("effort_weight must be >= 0")

        if not isinstance(self.blocks, tuple):
            object.__setattr__(self, "blocks", tuple(self.blocks))
        blocks = tuple(str(b).strip() for b in self.blocks if str(b).strip())
        object.__setattr__(self, "blocks", blocks)

        if not isinstance(self.confidence, (int, float)) or isinstance(
            self.confidence, bool
        ):
            raise WorkItemValidationError("confidence must be a number")
        conf = float(self.confidence)
        if conf < 0.0 or conf > 1.0:
            raise WorkItemValidationError("confidence must be between 0.0 and 1.0")
        object.__setattr__(self, "confidence", conf)

    @property
    def priority_rank(self) -> int:
        return _PRIORITY_RANK[self.priority]

    @property
    def urgency_rank(self) -> int:
        return _URGENCY_RANK[self.urgency_band]

    def with_updates(self, **changes: Any) -> WorkItem:
        """Return a copy with the given field replacements (type is immutable)."""
        if "type" in changes and changes["type"] != self.type:
            raise WorkItemValidationError(
                "WorkItem.type is immutable for an id; create a new WorkItem"
            )
        if "id" in changes and str(changes["id"]) != self.id:
            raise WorkItemValidationError(
                "WorkItem.id is immutable; create a new WorkItem"
            )
        payload = {
            "id": self.id,
            "type": self.type,
            "priority": self.priority,
            "title": self.title,
            "summary": self.summary,
            "evidence": self.evidence,
            "primary_action": self.primary_action,
            "secondary_action": self.secondary_action,
            "dismissible": self.dismissible,
            "deferrable": self.deferrable,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "proof_reference": self.proof_reference,
            "provider": self.provider,
            "capability": self.capability,
            "state": self.state,
            "owner_domain": self.owner_domain,
            "urgency_band": self.urgency_band,
            "effort_weight": self.effort_weight,
            "blocks": self.blocks,
            "confidence": self.confidence,
            "schema_version": self.schema_version,
        }
        payload.update(changes)
        return WorkItem(**payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "type": self.type.value,
            "priority": self.priority.value,
            "title": self.title,
            "summary": self.summary,
            "evidence": self.evidence.to_dict(),
            "primary_action": self.primary_action.to_dict(),
            "secondary_action": (
                None
                if self.secondary_action is None
                else self.secondary_action.to_dict()
            ),
            "dismissible": self.dismissible,
            "deferrable": self.deferrable,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "expires_at": None if self.expires_at is None else _iso(self.expires_at),
            "proof_reference": self.proof_reference,
            "provider": self.provider,
            "capability": self.capability,
            "state": self.state.value,
            "owner_domain": self.owner_domain,
            "urgency_band": self.urgency_band.value,
            "effort_weight": self.effort_weight,
            "blocks": list(self.blocks),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> WorkItem:
        if not isinstance(payload, Mapping):
            raise WorkItemValidationError("WorkItem payload must be a mapping")
        secondary_raw = payload.get("secondary_action")
        return cls(
            schema_version=int(
                payload.get("schema_version", WORK_ITEM_SCHEMA_VERSION)
            ),
            id=str(payload.get("id") or ""),
            type=_parse_enum(WorkItemType, payload.get("type"), "type"),
            priority=_parse_enum(
                WorkItemPriority, payload.get("priority"), "priority"
            ),
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or ""),
            evidence=WorkItemEvidence.from_dict(payload.get("evidence")),
            primary_action=WorkItemAction.from_dict(
                payload.get("primary_action") or {}
            ),
            secondary_action=(
                None
                if secondary_raw is None
                else WorkItemAction.from_dict(secondary_raw)
            ),
            dismissible=bool(payload.get("dismissible")),
            deferrable=bool(payload.get("deferrable")),
            created_at=_parse_dt(payload.get("created_at"), "created_at"),
            updated_at=_parse_dt(payload.get("updated_at"), "updated_at"),
            expires_at=_parse_optional_dt(payload.get("expires_at"), "expires_at"),
            proof_reference=payload.get("proof_reference"),
            provider=payload.get("provider"),
            capability=payload.get("capability"),
            state=_parse_enum(WorkItemState, payload.get("state"), "state"),
            owner_domain=str(payload.get("owner_domain") or ""),
            urgency_band=_parse_enum(
                UrgencyBand,
                payload.get("urgency_band", UrgencyBand.NORMAL.value),
                "urgency_band",
            ),
            effort_weight=int(payload.get("effort_weight", 100)),
            blocks=tuple(payload.get("blocks") or ()),
            confidence=float(payload.get("confidence", 1.0)),
        )


def priority_rank(priority: WorkItemPriority) -> int:
    """Numeric rank for priority (lower wins)."""
    return _PRIORITY_RANK[priority]


def urgency_rank(band: UrgencyBand) -> int:
    """Numeric rank for urgency band (lower wins)."""
    return _URGENCY_RANK[band]


def _enforce_type_policy(item: WorkItem) -> None:
    """Type-specific dismiss/defer constraints (domain model §7 / behavior §2)."""
    if item.type is WorkItemType.APPROVAL and item.dismissible:
        raise WorkItemValidationError(
            "Approvals are not casually dismissible (dismissible must be False)"
        )
    if (
        item.type is WorkItemType.INTERRUPT
        and item.urgency_band is UrgencyBand.HARD
        and item.dismissible
    ):
        raise WorkItemValidationError(
            "Hard Interrupts are not dismissible (dismissible must be False)"
        )
    if (
        item.type is WorkItemType.SETUP
        and item.priority is WorkItemPriority.SETUP_BLOCKING
        and item.dismissible
    ):
        raise WorkItemValidationError(
            "Blocking Setup cannot be dismissed while capability remains blocked"
        )


def _ensure_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise WorkItemValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_dt(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _ensure_aware(value, field_name)
    if not value:
        raise WorkItemValidationError(f"{field_name} is required")
    try:
        text = str(value).replace("Z", "+00:00")
        return _ensure_aware(datetime.fromisoformat(text), field_name)
    except (TypeError, ValueError) as exc:
        raise WorkItemValidationError(
            f"{field_name} must be an ISO-8601 datetime, got {value!r}"
        ) from exc


def _parse_optional_dt(value: Any, field_name: str) -> datetime | None:
    if value is None or value == "":
        return None
    return _parse_dt(value, field_name)


def _parse_enum(enum_cls: type[Enum], value: Any, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    text = str(value or "").strip().lower()
    try:
        return enum_cls(text)
    except ValueError as exc:
        allowed = sorted(item.value for item in enum_cls)
        raise WorkItemValidationError(
            f"{field_name} must be one of {allowed}, got {value!r}"
        ) from exc
