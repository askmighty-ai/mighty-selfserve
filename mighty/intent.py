"""
mighty.intent
─────────────
Framework for inferring user intent from page context.

Pure functions only — no database, AI, or network calls.
Intent-specific rules are added later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re


@dataclass
class IntentResult:
    intent: str
    confidence: str = "low"
    evidence: list[str] = field(default_factory=list)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().strip())


def infer_intent(
    url: str = "",
    page_title: str = "",
    page_text: str = "",
) -> IntentResult:
    return IntentResult(intent="unknown", confidence="low", evidence=[])
