"""Benchmarks: legacy truncate+snippets vs full preprocessing pipeline."""

import os

import pytest

from mighty.discovery_pipeline import extract_candidate_snippets, estimate_tokens, prepare_discovery_input
from mighty.field_discovery import field_discovery_max_chars, truncate_discovery_input

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
_NAMES = [
    "delta_companion_cert.txt", "marriott_free_night.txt", "amex_credits.txt",
    "chase_payment.txt", "xfinity_bill.txt", "noisy_marketing.txt", "delta_wallet.html",
]


def _load(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


def _legacy_tokens(raw: str) -> int:
    cap = field_discovery_max_chars()
    bounded = truncate_discovery_input(raw, cap)
    snippets = extract_candidate_snippets(bounded, max_chars=cap)
    return estimate_tokens(len(snippets))


def _pipeline_tokens(raw: str) -> int:
    return prepare_discovery_input(raw).stats.prepared_tokens


@pytest.mark.parametrize("name", _NAMES)
def test_pipeline_not_worse_than_legacy(name: str):
    raw = _load(name)
    legacy = _legacy_tokens(raw)
    pipeline = _pipeline_tokens(raw)
    print(f"\n[{name}] legacy≈{legacy} pipeline≈{pipeline} saved≈{legacy - pipeline}")
    assert pipeline <= legacy


def test_aggregate_reduction():
    total_legacy = sum(_legacy_tokens(_load(n)) for n in _NAMES)
    total_pipeline = sum(_pipeline_tokens(_load(n)) for n in _NAMES)
    reduction = (1 - total_pipeline / max(total_legacy, 1)) * 100
    print(f"\nAggregate token reduction vs legacy: {reduction:.1f}%")
    assert total_pipeline <= total_legacy
