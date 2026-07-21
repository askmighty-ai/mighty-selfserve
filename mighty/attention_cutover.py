"""Attention Platform cutover flags (Milestone 3).

Values:
  off     — do not expose AttentionView to consumers; shadow may still record
  shadow  — record engine + compare; consumers keep legacy behavior
  on      — consumers read AttentionView; legacy used only as failure fallback

Per-surface env wins over global:

  ATTENTION_CUTOVER_HOME
  ATTENTION_CUTOVER_WORKER
  ATTENTION_CUTOVER          # default for both when specific unset

Default is ``on`` after M3 validation via unit/replay tests; set ``shadow`` or
``off`` to roll back without redeploying code.
"""

from __future__ import annotations

import os
from typing import Literal

CutoverMode = Literal["off", "shadow", "on"]
_VALID: frozenset[str] = frozenset({"off", "shadow", "on"})

# Production default after M3 cutover. Override with env for rollback.
_DEFAULT_MODE: CutoverMode = "on"


def attention_cutover_mode(surface: str) -> CutoverMode:
    """Return cutover mode for ``home`` or ``worker``."""
    surface_key = str(surface or "").strip().lower()
    specific = os.environ.get(f"ATTENTION_CUTOVER_{surface_key.upper()}")
    global_mode = os.environ.get("ATTENTION_CUTOVER")
    raw = (specific if specific not in (None, "") else global_mode)
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_MODE
    text = str(raw).strip().lower()
    if text in _VALID:
        return text  # type: ignore[return-value]
    return _DEFAULT_MODE


def attention_cutover_enabled(surface: str) -> bool:
    """True when the surface should render/consume AttentionView."""
    return attention_cutover_mode(surface) == "on"


def attention_cutover_exposes_payload(surface: str) -> bool:
    """True when API payloads may include AttentionView (on or shadow)."""
    return attention_cutover_mode(surface) in ("on", "shadow")
