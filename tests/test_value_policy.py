"""Pure Value Intelligence policy tests (Milestone 10)."""

from __future__ import annotations

from datetime import date

from mighty.value_capability_registry import (
    KIND_DUPLICATED_BENEFIT,
    KIND_ELITE_QUALIFICATION_RISK,
    KIND_EXPIRING_CERTIFICATE,
    KIND_EXPIRING_CREDIT,
    KIND_UPGRADE_OPPORTUNITY,
    provider_supports_kind,
)
from mighty.value_policy import compute_opportunity_candidates

TODAY = date(2026, 7, 22)


def test_expiring_credit():
    result = compute_opportunity_candidates(
        [
            {
                "key": "dining",
                "label": "Dining Credit",
                "value": "$40 Expires Aug 1, 2026",
                "_type": "cash_credit",
            }
        ],
        provider="amex",
        today=TODAY,
    )
    active = result.active_candidates
    assert any(c.kind == KIND_EXPIRING_CREDIT for c in active)
    assert result.generated >= 1


def test_expiring_certificate_and_upgrade():
    result = compute_opportunity_candidates(
        [
            {
                "key": "upgrade",
                "label": "Systemwide Upgrade Certificate",
                "value": "Expires 08/01/2026",
                "_type": "certificate",
            }
        ],
        provider="delta",
        today=TODAY,
    )
    kinds = {c.kind for c in result.active_candidates}
    assert KIND_EXPIRING_CERTIFICATE in kinds
    assert KIND_UPGRADE_OPPORTUNITY in kinds


def test_elite_qualification_risk():
    result = compute_opportunity_candidates(
        [
            {
                "key": "mqm",
                "label": "Medallion Qualifying Miles",
                "value": "45,000 of 50,000",
                "_type": "progress_toward",
            }
        ],
        provider="delta",
        today=TODAY,
    )
    assert any(
        c.kind == KIND_ELITE_QUALIFICATION_RISK and not c.suppressed
        for c in result.candidates
    )


def test_duplicate_benefit():
    result = compute_opportunity_candidates(
        [
            {
                "key": "c1",
                "label": "Hotel Credit",
                "value": "$200",
                "_type": "cash_credit",
            },
            {
                "key": "c2",
                "label": "Hotel Credit",
                "value": "$200",
                "_type": "cash_credit",
            },
        ],
        provider="amex",
        today=TODAY,
    )
    assert any(c.kind == KIND_DUPLICATED_BENEFIT for c in result.active_candidates)


def test_replay_deterministic():
    fields = [
        {
            "key": "pts",
            "label": "Points",
            "value": "1000 Expires Aug 5, 2026",
            "_type": "points_balance",
        }
    ]
    a = compute_opportunity_candidates(fields, provider="amex", today=TODAY)
    b = compute_opportunity_candidates(fields, provider="amex", today=TODAY)
    assert [c.fingerprint for c in a.candidates] == [
        c.fingerprint for c in b.candidates
    ]
    assert a.generated == b.generated


def test_provider_capability_config():
    assert provider_supports_kind("amex", KIND_EXPIRING_CREDIT)
    assert provider_supports_kind("unknown_provider", KIND_EXPIRING_CREDIT)


def test_suppress_low_score_unused_without_expiry():
    result = compute_opportunity_candidates(
        [
            {
                "key": "tiny",
                "label": "Tiny perk",
                "value": "1",
                "_type": "other",
            }
        ],
        provider="amex",
        today=TODAY,
    )
    assert result.generated == 0
