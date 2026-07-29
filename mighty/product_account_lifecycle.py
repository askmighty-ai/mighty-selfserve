"""Bounded product account lifecycle (state-derived, Session 2).

Maps capability / access signals to Founder-facing lifecycle buckets:

  success | waiting | needs-action | unsupported-data | failure

Presentation only — does not invent account balances.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mighty.capability_state import CapabilityState
from mighty.session_verification import ACTIVE_VERIFICATION_LIFECYCLES

SUCCESS = "success"
WAITING = "waiting"
NEEDS_ACTION = "needs-action"
UNSUPPORTED_DATA = "unsupported-data"
FAILURE = "failure"

LIFECYCLE_LABELS = {
    SUCCESS: "Up to date",
    WAITING: "Waiting",
    NEEDS_ACTION: "Needs action",
    UNSUPPORTED_DATA: "No account data",
    FAILURE: "Update failed",
}


@dataclass(frozen=True)
class ProductAccountLifecycle:
    state: str
    label: str
    timestamp: str | None
    next_action: str | None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "label": self.label,
            "timestamp": self.timestamp,
            "next_action": self.next_action,
            "detail": self.detail,
        }


def resolve_product_account_lifecycle(
    *,
    capability_state: CapabilityState | str | None,
    verification_lifecycle: str | None = None,
    last_confirmed_at: str | None = None,
    extraction_terminal_at: str | None = None,
) -> ProductAccountLifecycle:
    """Derive bounded lifecycle + next action from durable signals."""
    state_val = (
        capability_state.value
        if isinstance(capability_state, CapabilityState)
        else (capability_state or "")
    )
    lifecycle = (verification_lifecycle or "").strip()
    ts = extraction_terminal_at or last_confirmed_at

    if state_val == CapabilityState.EXTRACTION_SUCCESS.value:
        return ProductAccountLifecycle(
            state=SUCCESS,
            label=LIFECYCLE_LABELS[SUCCESS],
            timestamp=ts,
            next_action=None,
            detail="Meaningful account data is available.",
        )
    if state_val == CapabilityState.SIGNED_OUT.value:
        return ProductAccountLifecycle(
            state=NEEDS_ACTION,
            label=LIFECYCLE_LABELS[NEEDS_ACTION],
            timestamp=ts,
            next_action="Sign in to American Express in Chrome",
            detail="Mighty needs you signed in to verify access.",
        )
    if state_val == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED.value:
        return ProductAccountLifecycle(
            state=FAILURE,
            label=LIFECYCLE_LABELS[FAILURE],
            timestamp=ts,
            next_action="Open American Express while Mighty is installed, then retry",
            detail="Login was visible but extraction failed.",
        )
    if state_val == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA.value:
        return ProductAccountLifecycle(
            state=UNSUPPORTED_DATA,
            label=LIFECYCLE_LABELS[UNSUPPORTED_DATA],
            timestamp=ts,
            next_action=(
                "Stay signed in and open your Amex account pages — "
                "Mighty will try again when private data is visible"
            ),
            detail="Signed in, but no publishable account fields were observed.",
        )
    if lifecycle in ACTIVE_VERIFICATION_LIFECYCLES:
        return ProductAccountLifecycle(
            state=WAITING,
            label=LIFECYCLE_LABELS[WAITING],
            timestamp=ts,
            next_action="Keep the Amex tab open while Mighty finishes this check",
            detail="Access check in progress.",
        )
    if lifecycle == "timed_out":
        return ProductAccountLifecycle(
            state=FAILURE,
            label=LIFECYCLE_LABELS[FAILURE],
            timestamp=ts,
            next_action="Open American Express and try again",
            detail="The last check timed out.",
        )
    if lifecycle == "failed":
        return ProductAccountLifecycle(
            state=FAILURE,
            label=LIFECYCLE_LABELS[FAILURE],
            timestamp=ts,
            next_action="Open American Express and try again",
            detail="The last check failed.",
        )
    return ProductAccountLifecycle(
        state=WAITING,
        label=LIFECYCLE_LABELS[WAITING],
        timestamp=ts,
        next_action="Open American Express in Chrome so Mighty can verify access",
        detail="Login state not yet confirmed.",
    )
