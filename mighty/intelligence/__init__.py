"""
mighty.intelligence
───────────────────
Provider-agnostic intelligence layer.

Input:  provider account data (normalized fields across synced accounts)
Output: inferred travel profile, hotel preferences, spending strategy,
        loyalty strategy, and risk profile.

Pure functions only — no database, AI, or network calls.
"""

from mighty.intelligence.aggregate import (
    aggregate_input,
    build_intelligence_input,
    snapshot_from_accounts,
)
from mighty.intelligence.infer import infer_from_snapshot, infer_intelligence
from mighty.intelligence.models import (
    HotelPreferences,
    InferredAttribute,
    IntelligenceInput,
    IntelligenceProfile,
    LoyaltyStrategy,
    ProviderAccountSnapshot,
    RiskProfile,
    SpendingStrategy,
    TravelProfile,
)

__all__ = [
    "HotelPreferences",
    "InferredAttribute",
    "IntelligenceInput",
    "IntelligenceProfile",
    "LoyaltyStrategy",
    "ProviderAccountSnapshot",
    "RiskProfile",
    "SpendingStrategy",
    "TravelProfile",
    "aggregate_input",
    "build_intelligence_input",
    "infer_from_snapshot",
    "infer_intelligence",
    "snapshot_from_accounts",
]
