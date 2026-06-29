"""
mighty.advisors.hotel
─────────────────────
Hotel-related contextual opportunity advisor.

Skeleton only — no hotel logic, Amex logic, database, AI, or network calls yet.
"""

from __future__ import annotations

from typing import Any

from mighty.advisors.base import Opportunity
from mighty.decision_engine import DecisionContext


def evaluate(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Opportunity]:
    return []
