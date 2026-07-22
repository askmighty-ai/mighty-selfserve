"""Value Intelligence coordinator (Milestone 10).

Runs after a successful Account Snapshot persist. Computes durable opportunity
facts. Never ranks Attention and never raises into sync/Home/Worker callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping

from mighty.opportunity_store import ReconcileResult, reconcile_opportunities
from mighty.value_policy import ValuePolicyResult, compute_opportunity_candidates

logger = logging.getLogger(__name__)


@dataclass
class ValueReconcileObservation:
    provider: str
    policy: ValuePolicyResult | None = None
    reconcile: ReconcileResult | None = None
    error: str | None = None


@dataclass
class ValueSweepCounters:
    providers: int = 0
    generated: int = 0
    suppressed: int = 0
    expired: int = 0
    duplicates_suppressed: int = 0
    active: int = 0
    value_at_risk_total: float = 0.0
    errors: int = 0
    observations: list[ValueReconcileObservation] = field(default_factory=list)


def reconcile_opportunities_from_snapshot(
    db: Any,
    *,
    snapshot: Any,
    today: date | datetime | None = None,
    user_intent: Mapping[str, Any] | None = None,
    user_type_affinity: Mapping[str, Any] | None = None,
    commit: bool = True,
) -> ValueReconcileObservation:
    """Compute + reconcile opportunities for one successful snapshot."""
    provider = str(getattr(snapshot, "provider", "") or "").strip().lower()
    obs = ValueReconcileObservation(provider=provider)
    try:
        if isinstance(today, datetime):
            today_d = today.date()
        else:
            today_d = today or date.today()

        fields = getattr(snapshot, "normalized_fields", None) or ()
        value_result = compute_opportunity_candidates(
            fields,
            provider=provider,
            today=today_d,
            user_intent=user_intent,
            user_type_affinity=user_type_affinity,
        )
        # Milestone 12 — honor User Policy opportunity suppression / monitoring.
        uid = str(getattr(snapshot, "user_id", "") or "")
        candidates = value_result.candidates
        try:
            from mighty.policy_store import load_user_policy
            from mighty.user_policy import (
                opportunity_kind_suppressed,
                provider_monitoring_enabled,
            )

            user_pol = load_user_policy(db, uid)
            if not provider_monitoring_enabled(user_pol, provider):
                from mighty.value_policy import OpportunityCandidate

                candidates = tuple(
                    OpportunityCandidate(
                        kind=c.kind,
                        field_key=c.field_key,
                        label=c.label,
                        value=c.value,
                        field_type=c.field_type,
                        score=c.score,
                        urgency=c.urgency,
                        days_left=c.days_left,
                        exp_date=c.exp_date,
                        value_estimate=c.value_estimate,
                        fingerprint=c.fingerprint,
                        summary=c.summary,
                        suppressed=True,
                        suppress_reason="policy_monitor_disabled",
                        metadata=dict(c.metadata),
                    )
                    for c in candidates
                )
            else:
                from mighty.value_policy import OpportunityCandidate

                filtered = []
                for c in candidates:
                    if opportunity_kind_suppressed(user_pol, c.kind):
                        filtered.append(
                            OpportunityCandidate(
                                kind=c.kind,
                                field_key=c.field_key,
                                label=c.label,
                                value=c.value,
                                field_type=c.field_type,
                                score=c.score,
                                urgency=c.urgency,
                                days_left=c.days_left,
                                exp_date=c.exp_date,
                                value_estimate=c.value_estimate,
                                fingerprint=c.fingerprint,
                                summary=c.summary,
                                suppressed=True,
                                suppress_reason="policy_opportunity_suppressed",
                                metadata=dict(c.metadata),
                            )
                        )
                    else:
                        filtered.append(c)
                candidates = tuple(filtered)
        except Exception:
            candidates = value_result.candidates

        obs.policy = value_result
        result = reconcile_opportunities(
            db,
            user_id=uid,
            provider=provider,
            candidates=candidates,
            snapshot_id=str(getattr(snapshot, "snapshot_id", "") or "") or None,
            today=today_d,
            commit=commit,
        )
        obs.reconcile = result
        logger.info(
            "value_intelligence.reconcile provider=%s generated=%s suppressed=%s "
            "expired=%s active=%s var_total=%.2f",
            provider,
            result.generated,
            value_result.suppressed,
            result.expired,
            result.active,
            result.value_at_risk_total,
        )
        return obs
    except Exception as exc:
        logger.exception(
            "value_intelligence.reconcile_failed provider=%s err=%s", provider, exc
        )
        obs.error = str(exc)
        return obs


def safe_reconcile_opportunities_from_snapshot(
    db: Any,
    *,
    snapshot: Any,
    **kwargs: Any,
) -> ValueReconcileObservation | None:
    if snapshot is None:
        return None
    try:
        return reconcile_opportunities_from_snapshot(
            db, snapshot=snapshot, **kwargs
        )
    except Exception as exc:
        logger.exception("value_intelligence.safe_reconcile_failed err=%s", exc)
        return ValueReconcileObservation(
            provider=str(getattr(snapshot, "provider", "") or ""),
            error=str(exc),
        )


def apply_observation(
    counters: ValueSweepCounters, obs: ValueReconcileObservation
) -> None:
    counters.providers += 1
    if obs.error:
        counters.errors += 1
        counters.observations.append(obs)
        return
    if obs.policy:
        counters.generated += obs.policy.generated
        counters.suppressed += obs.policy.suppressed
    if obs.reconcile:
        counters.expired += obs.reconcile.expired
        counters.duplicates_suppressed += obs.reconcile.duplicates_suppressed
        counters.active += obs.reconcile.active
        counters.value_at_risk_total += float(obs.reconcile.value_at_risk_total or 0)
    counters.observations.append(obs)
