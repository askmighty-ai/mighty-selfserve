"""
mighty.intelligence.models
──────────────────────────
Provider-agnostic output schema for inferred user intelligence.

Pure dataclasses only — no database, AI, or network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InferredAttribute:
    value: str
    confidence: str = "low"
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }


@dataclass
class TravelProfile:
    primary_domain: InferredAttribute
    travel_frequency: InferredAttribute
    trip_style: InferredAttribute

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_domain": self.primary_domain.to_dict(),
            "travel_frequency": self.travel_frequency.to_dict(),
            "trip_style": self.trip_style.to_dict(),
        }


@dataclass
class HotelPreferences:
    preferred_brands: InferredAttribute
    elite_tiers: InferredAttribute
    booking_approach: InferredAttribute

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferred_brands": self.preferred_brands.to_dict(),
            "elite_tiers": self.elite_tiers.to_dict(),
            "booking_approach": self.booking_approach.to_dict(),
        }


@dataclass
class SpendingStrategy:
    primary_mode: InferredAttribute
    card_focus: InferredAttribute
    credit_utilization: InferredAttribute

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_mode": self.primary_mode.to_dict(),
            "card_focus": self.card_focus.to_dict(),
            "credit_utilization": self.credit_utilization.to_dict(),
        }


@dataclass
class LoyaltyStrategy:
    accumulation_style: InferredAttribute
    program_diversity: InferredAttribute
    redemption_pressure: InferredAttribute

    def to_dict(self) -> dict[str, Any]:
        return {
            "accumulation_style": self.accumulation_style.to_dict(),
            "program_diversity": self.program_diversity.to_dict(),
            "redemption_pressure": self.redemption_pressure.to_dict(),
        }


@dataclass
class RiskProfile:
    financial_risk: InferredAttribute
    payment_health: InferredAttribute
    attention_areas: InferredAttribute

    def to_dict(self) -> dict[str, Any]:
        return {
            "financial_risk": self.financial_risk.to_dict(),
            "payment_health": self.payment_health.to_dict(),
            "attention_areas": self.attention_areas.to_dict(),
        }


@dataclass
class IntelligenceProfile:
    travel_profile: TravelProfile
    hotel_preferences: HotelPreferences
    spending_strategy: SpendingStrategy
    loyalty_strategy: LoyaltyStrategy
    risk_profile: RiskProfile

    def to_dict(self) -> dict[str, Any]:
        return {
            "travel_profile": self.travel_profile.to_dict(),
            "hotel_preferences": self.hotel_preferences.to_dict(),
            "spending_strategy": self.spending_strategy.to_dict(),
            "loyalty_strategy": self.loyalty_strategy.to_dict(),
            "risk_profile": self.risk_profile.to_dict(),
        }


@dataclass
class ProviderAccountSnapshot:
    source: str
    category: str
    fields: list[dict]
    is_synced: bool = True


@dataclass
class IntelligenceInput:
    accounts: list[ProviderAccountSnapshot]
    intent_summary: dict[str, int] = field(default_factory=dict)
    type_affinity: dict[str, float] = field(default_factory=dict)
    email_subjects: list[str] = field(default_factory=list)

    @property
    def connected_sources(self) -> list[str]:
        return sorted({acct.source for acct in self.accounts})
