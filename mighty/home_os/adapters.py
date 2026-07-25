"""Map existing platform facts → canonical Home OS models.

Pure adapters. No ranking, lifecycle, or HomeState assembly.
Consumes Attention / AccountState / account_changes; produces WorkItem,
CoverageItem, and ProofItem inputs for mighty.workitem.project_home.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from mighty.account_state import (
    CONN_CONNECTED,
    CONN_NEEDS_LOGIN,
    SESSION_EXPIRED,
    SESSION_HEALTHY,
    AccountState,
)
from mighty.attention import (
    AttentionClass,
    AttentionItem,
    AttentionUrgency,
)
from mighty.attention_view import (
    AttentionPresentation,
    _DEFAULT_PROVIDER_NAMES,
    _copy_for,
    _provider_name,
)
from mighty.workitem.coverage import (
    AuthPosture,
    CoverageHealth,
    CoverageItem,
    CoverageStatus,
    VerificationState,
)
from mighty.workitem.model import (
    UrgencyBand,
    WorkItem,
    WorkItemAction,
    WorkItemEvidence,
    WorkItemPriority,
    WorkItemState,
    WorkItemType,
)
from mighty.workitem.proof import ProofItem

OWNER_ATTENTION = "attention"
OWNER_ACCOUNT_STATE = "account_state"
OWNER_CHANGE_STORE = "change_store"


def attention_items_to_work_items(
    items: Sequence[AttentionItem],
    *,
    as_of: datetime,
    provider_display_names: Mapping[str, str] | None = None,
) -> tuple[WorkItem, ...]:
    """Map Attention candidates to canonical WorkItems (not yet ranked)."""
    names = {**_DEFAULT_PROVIDER_NAMES, **(provider_display_names or {})}
    out: list[WorkItem] = []
    for item in items:
        mapped = _attention_to_work_item(item, as_of=as_of, names=names)
        if mapped is not None:
            out.append(mapped)
    return tuple(out)


def presentations_to_work_items(
    presentations: Sequence[AttentionPresentation],
    *,
    as_of: datetime,
    source_items: Sequence[AttentionItem] | None = None,
) -> tuple[WorkItem, ...]:
    """Prefer resolved customer copy from AttentionPresentation when available."""
    by_id = {i.attention_id: i for i in (source_items or ())}
    out: list[WorkItem] = []
    for pres in presentations:
        source = by_id.get(pres.attention_id)
        if source is None:
            # Reconstruct minimal AttentionItem-shaped mapping via presentation fields.
            wi = _presentation_to_work_item(pres, as_of=as_of)
        else:
            wi = _attention_to_work_item(
                source,
                as_of=as_of,
                names=_DEFAULT_PROVIDER_NAMES,
                title=pres.title,
                summary=pres.body,
                cta_label=pres.cta_label,
            )
        if wi is not None:
            out.append(wi)
    return tuple(out)


def account_states_to_coverage(
    accounts: Sequence[AccountState],
) -> tuple[CoverageItem, ...]:
    """Map AccountState rows to CoverageItems (honest auth axes)."""
    rows: list[CoverageItem] = []
    for acct in accounts:
        provider = str(acct.provider or "").strip().lower()
        if not provider or provider.startswith("_"):
            continue
        rows.append(_account_to_coverage(acct))
    rows.sort(key=lambda c: c.provider)
    return tuple(rows)


def change_alerts_to_proof(
    alerts: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> tuple[ProofItem, ...]:
    """Map meaningful account_changes alerts to ProofItems."""
    del as_of
    proofs: list[ProofItem] = []
    for alert in alerts:
        summary = str(alert.get("message") or "").strip()
        if not summary:
            continue
        change_id = str(alert.get("change_id") or "").strip()
        provider = alert.get("source") or alert.get("label")
        provider_key = str(provider).strip().lower() if provider else None
        raw_at = alert.get("changed_at")
        outcome_at = _parse_dt(raw_at) or datetime.now(timezone.utc)
        proof_id = change_id or f"proof:change:{provider_key or 'unknown'}:{int(outcome_at.timestamp())}"
        proofs.append(
            ProofItem(
                id=proof_id,
                outcome_at=outcome_at,
                summary=summary,
                provider=provider_key,
                outcome_class=str(alert.get("type") or "account_change"),
                impact="normal",
            )
        )
    return tuple(proofs)


def _attention_to_work_item(
    item: AttentionItem,
    *,
    as_of: datetime,
    names: Mapping[str, str],
    title: str | None = None,
    summary: str | None = None,
    cta_label: str | None = None,
) -> WorkItem | None:
    work_type, priority = _class_to_type_priority(item.attention_class)
    if work_type is None or priority is None:
        return None

    provider_name = _provider_name(item.provider, names)
    if title is None or summary is None:
        t, b, cta = _copy_for(item, provider_name)
        title = title or t
        summary = summary or b
        cta_label = cta_label or cta

    urgency = _urgency_band(item)
    dismissible, deferrable = _policy_flags(work_type, urgency, priority)
    primary_key = f"resolve_{item.attention_class.value}"
    primary_intent = (cta_label or f"Resolve {provider_name}").strip()
    secondary = None
    if deferrable:
        secondary = WorkItemAction(key="defer", intent="Not now")

    observed = _parse_dt(item.observed_at) or as_of
    expires = _parse_dt(item.becomes_stale_at)

    return WorkItem(
        id=f"wi_attn:{item.attention_id}",
        type=work_type,
        priority=priority,
        title=title.strip(),
        summary=summary.strip(),
        evidence=WorkItemEvidence.from_mapping(
            {
                "attention_id": item.attention_id,
                "reason": item.reason.code,
                "source_kind": item.source_kind.value,
                "source_ref": item.source_ref,
            }
        ),
        primary_action=WorkItemAction(key=primary_key, intent=primary_intent),
        secondary_action=secondary,
        dismissible=dismissible,
        deferrable=deferrable,
        created_at=observed,
        updated_at=as_of,
        expires_at=expires,
        proof_reference=None,
        provider=item.provider,
        capability=_capability_for(item),
        state=WorkItemState.VISIBLE,
        owner_domain=OWNER_ATTENTION,
        urgency_band=urgency,
        effort_weight=_effort_for(item),
        confidence=0.9,
    )


def _presentation_to_work_item(
    pres: AttentionPresentation,
    *,
    as_of: datetime,
) -> WorkItem | None:
    work_type, priority = _class_to_type_priority(pres.attention_class)
    if work_type is None or priority is None:
        return None
    urgency = (
        UrgencyBand.HARD
        if pres.urgency is AttentionUrgency.BLOCKER
        else UrgencyBand.HIGH
        if pres.urgency is AttentionUrgency.TIME_SENSITIVE
        else UrgencyBand.NORMAL
        if pres.urgency is AttentionUrgency.OPPORTUNITY
        else UrgencyBand.SOFT
    )
    dismissible, deferrable = _policy_flags(work_type, urgency, priority)
    secondary = (
        WorkItemAction(key="defer", intent="Not now") if deferrable else None
    )
    return WorkItem(
        id=f"wi_attn:{pres.attention_id}",
        type=work_type,
        priority=priority,
        title=pres.title.strip(),
        summary=pres.body.strip(),
        evidence=WorkItemEvidence.from_mapping(
            {
                "attention_id": pres.attention_id,
                "reason": pres.reason_code,
            }
        ),
        primary_action=WorkItemAction(
            key=f"resolve_{pres.attention_class.value}",
            intent=(pres.cta_label or "Continue").strip(),
        ),
        secondary_action=secondary,
        dismissible=dismissible,
        deferrable=deferrable,
        created_at=as_of,
        updated_at=as_of,
        expires_at=None,
        proof_reference=None,
        provider=pres.provider,
        capability="session",
        state=WorkItemState.VISIBLE,
        owner_domain=OWNER_ATTENTION,
        urgency_band=urgency,
        confidence=0.9,
    )


def _class_to_type_priority(
    cls: AttentionClass,
) -> tuple[WorkItemType | None, WorkItemPriority | None]:
    if cls in (
        AttentionClass.AUTH_BLOCKER,
        AttentionClass.TRUST,
        AttentionClass.ACCESS_DEGRADED,
    ):
        return WorkItemType.INTERRUPT, WorkItemPriority.INTERRUPT
    if cls is AttentionClass.AGENT_AUTHORIZATION:
        return WorkItemType.APPROVAL, WorkItemPriority.APPROVAL
    if cls in (AttentionClass.VALUE_AT_RISK, AttentionClass.OPPORTUNITY):
        return WorkItemType.OPPORTUNITY, WorkItemPriority.OPPORTUNITY
    if cls in (AttentionClass.SYSTEM, AttentionClass.DATA_GAP):
        return WorkItemType.SETUP, WorkItemPriority.SETUP_BLOCKING
    return None, None


def _urgency_band(item: AttentionItem) -> UrgencyBand:
    if item.urgency is AttentionUrgency.BLOCKER:
        return UrgencyBand.HARD
    if item.urgency is AttentionUrgency.TIME_SENSITIVE:
        return UrgencyBand.HIGH
    if item.urgency is AttentionUrgency.OPPORTUNITY:
        return UrgencyBand.NORMAL
    return UrgencyBand.SOFT


def _policy_flags(
    work_type: WorkItemType,
    urgency: UrgencyBand,
    priority: WorkItemPriority,
) -> tuple[bool, bool]:
    if work_type is WorkItemType.APPROVAL:
        return False, False
    if work_type is WorkItemType.INTERRUPT and urgency is UrgencyBand.HARD:
        return False, True
    if (
        work_type is WorkItemType.SETUP
        and priority is WorkItemPriority.SETUP_BLOCKING
    ):
        return False, True
    return True, True


def _capability_for(item: AttentionItem) -> str | None:
    if item.attention_class in (
        AttentionClass.AUTH_BLOCKER,
        AttentionClass.ACCESS_DEGRADED,
        AttentionClass.TRUST,
    ):
        return "session"
    if item.attention_class is AttentionClass.SYSTEM:
        return "capture"
    if item.attention_class is AttentionClass.DATA_GAP:
        return "discovery"
    if item.attention_class is AttentionClass.AGENT_AUTHORIZATION:
        return "agent_authorization"
    return None


def _effort_for(item: AttentionItem) -> int:
    if item.attention_class is AttentionClass.AUTH_BLOCKER:
        return 20
    if item.attention_class is AttentionClass.AGENT_AUTHORIZATION:
        return 30
    if item.attention_class is AttentionClass.SYSTEM:
        return 40
    return 50


def _account_to_coverage(acct: AccountState) -> CoverageItem:
    provider = str(acct.provider).strip().lower()
    display = (
        str(acct.display_name or "").strip()
        or _DEFAULT_PROVIDER_NAMES.get(provider)
        or provider.replace("_", " ").title()
    )
    conn = str(acct.connection_state or "").lower()
    session = str(acct.session_health or "").lower()

    if conn == CONN_NEEDS_LOGIN or session == SESSION_EXPIRED:
        auth = AuthPosture.MISSING if conn == CONN_NEEDS_LOGIN else AuthPosture.EXPIRED
        health = CoverageHealth.BLOCKED
        verification = VerificationState.FAILED
        monitoring = "paused_until_sign_in"
    elif conn == CONN_CONNECTED and session == SESSION_HEALTHY:
        auth = AuthPosture.VALID
        health = CoverageHealth.HEALTHY
        verification = VerificationState.VERIFIED
        monitoring = "active"
    elif conn == CONN_CONNECTED:
        auth = AuthPosture.VALID
        health = CoverageHealth.DEGRADED
        verification = VerificationState.PENDING
        monitoring = "verifying"
    else:
        auth = AuthPosture.UNKNOWN
        health = CoverageHealth.UNKNOWN
        verification = VerificationState.NEVER
        monitoring = "waiting"

    caps: list[str] = ["monitor"]
    if int(getattr(acct, "field_count", 0) or 0) > 0:
        caps.append("capture")

    return CoverageItem(
        provider=provider,
        status=CoverageStatus.ENROLLED,
        health=health,
        capabilities=tuple(caps),
        verification=verification,
        discovery="enrolled",
        authentication=auth,
        monitoring=monitoring,
        display_name=display,
    )


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None
