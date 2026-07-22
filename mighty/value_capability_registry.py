"""Provider capability config for Value Intelligence (Milestone 10).

Shared policy never branches on provider id. This registry only *enables*
opportunity kinds per provider — empty/missing → all default kinds allowed
when evidenced by field types.
"""

from __future__ import annotations

KIND_EXPIRING_CREDIT = "expiring_credit"
KIND_UNUSED_BENEFIT = "unused_benefit"
KIND_EXPIRING_CERTIFICATE = "expiring_certificate"
KIND_ELITE_QUALIFICATION_RISK = "elite_qualification_risk"
KIND_UPGRADE_OPPORTUNITY = "upgrade_opportunity"
KIND_EXPIRING_POINTS = "expiring_points"
KIND_DUPLICATED_BENEFIT = "duplicated_benefit"
KIND_PAYMENT_DUE = "payment_due"
KIND_RENEWAL = "renewal"

ALL_OPPORTUNITY_KINDS: frozenset[str] = frozenset(
    {
        KIND_EXPIRING_CREDIT,
        KIND_UNUSED_BENEFIT,
        KIND_EXPIRING_CERTIFICATE,
        KIND_ELITE_QUALIFICATION_RISK,
        KIND_UPGRADE_OPPORTUNITY,
        KIND_EXPIRING_POINTS,
        KIND_DUPLICATED_BENEFIT,
        KIND_PAYMENT_DUE,
        KIND_RENEWAL,
    }
)

# Optional per-provider enablement. Omitted providers → full default set.
# Keep Amex (customer-visible) fully enabled; expand via config only.
PROVIDER_OPPORTUNITY_KINDS: dict[str, frozenset[str]] = {
    "amex": ALL_OPPORTUNITY_KINDS,
}


def enabled_kinds_for_provider(provider: str) -> frozenset[str]:
    key = str(provider or "").strip().lower()
    if not key:
        return ALL_OPPORTUNITY_KINDS
    return PROVIDER_OPPORTUNITY_KINDS.get(key, ALL_OPPORTUNITY_KINDS)


def provider_supports_kind(provider: str, kind: str) -> bool:
    return str(kind or "") in enabled_kinds_for_provider(provider)
