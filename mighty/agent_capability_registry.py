"""Executable action-type capabilities (Milestone 11).

Shared authorization policy never branches on provider id. This registry
only enables which action_types may be executed after authorization.
"""

from __future__ import annotations

# Default executable types — expand via config, not provider if-branches.
DEFAULT_EXECUTABLE_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "book",
        "cancel",
        "modify",
        "redeem",
        "transfer",
        "pay",
        "message",
        "other",
        "record",
        "log",
    }
)

# Optional per-provider tightening of executable types (config only).
PROVIDER_EXECUTABLE_ACTION_TYPES: dict[str, frozenset[str]] = {}


def executable_types_for_provider(provider: str | None = None) -> frozenset[str]:
    key = str(provider or "").strip().lower()
    if key and key in PROVIDER_EXECUTABLE_ACTION_TYPES:
        return PROVIDER_EXECUTABLE_ACTION_TYPES[key]
    return DEFAULT_EXECUTABLE_ACTION_TYPES


def action_type_is_executable(action_type: str, *, provider: str | None = None) -> bool:
    return str(action_type or "").strip().lower() in executable_types_for_provider(
        provider
    )
