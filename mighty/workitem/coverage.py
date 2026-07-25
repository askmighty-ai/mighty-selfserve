"""CoverageItem — provider disclosure for Home (not a Work Item).

Coverage cannot directly modify Work Items.
See docs/HOME_OS_DOMAIN_MODEL.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class CoverageValidationError(ValueError):
    """Raised when a CoverageItem payload is invalid."""


class CoverageStatus(str, Enum):
    ENROLLED = "enrolled"
    CANDIDATE = "candidate"
    UNSUPPORTED = "unsupported"
    REMOVED = "removed"


class CoverageHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class VerificationState(str, Enum):
    VERIFIED = "verified"
    PENDING = "pending"
    FAILED = "failed"
    NEVER = "never"


class AuthPosture(str, Enum):
    VALID = "valid"
    MISSING = "missing"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CoverageItem:
    """One provider under observation (or candidate / unsupported slot)."""

    provider: str
    status: CoverageStatus
    health: CoverageHealth
    capabilities: tuple[str, ...]
    verification: VerificationState
    discovery: str
    authentication: AuthPosture
    monitoring: str
    display_name: str | None = None

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip()
        if not provider:
            raise CoverageValidationError("provider must be a non-empty string")
        object.__setattr__(self, "provider", provider)

        if not isinstance(self.status, CoverageStatus):
            raise CoverageValidationError("status must be a CoverageStatus")
        if not isinstance(self.health, CoverageHealth):
            raise CoverageValidationError("health must be a CoverageHealth")
        if not isinstance(self.verification, VerificationState):
            raise CoverageValidationError(
                "verification must be a VerificationState"
            )
        if not isinstance(self.authentication, AuthPosture):
            raise CoverageValidationError(
                "authentication must be an AuthPosture"
            )

        if not isinstance(self.capabilities, tuple):
            object.__setattr__(self, "capabilities", tuple(self.capabilities))
        caps = tuple(str(c).strip() for c in self.capabilities if str(c).strip())
        object.__setattr__(self, "capabilities", caps)

        discovery = str(self.discovery or "").strip()
        monitoring = str(self.monitoring or "").strip()
        if not discovery:
            raise CoverageValidationError("discovery must be a non-empty string")
        if not monitoring:
            raise CoverageValidationError("monitoring must be a non-empty string")
        object.__setattr__(self, "discovery", discovery)
        object.__setattr__(self, "monitoring", monitoring)

        display = (
            None
            if self.display_name is None
            else str(self.display_name).strip() or None
        )
        object.__setattr__(self, "display_name", display)

        if self.status is CoverageStatus.UNSUPPORTED:
            if self.health not in (
                CoverageHealth.UNSUPPORTED,
                CoverageHealth.UNKNOWN,
            ):
                # Allow explicit unsupported health; coerce is not silent fake enrollment.
                pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "health": self.health.value,
            "capabilities": list(self.capabilities),
            "verification": self.verification.value,
            "discovery": self.discovery,
            "authentication": self.authentication.value,
            "monitoring": self.monitoring,
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CoverageItem:
        if not isinstance(payload, Mapping):
            raise CoverageValidationError("CoverageItem payload must be a mapping")
        return cls(
            provider=str(payload.get("provider") or ""),
            status=CoverageStatus(str(payload.get("status") or "").strip().lower()),
            health=CoverageHealth(str(payload.get("health") or "").strip().lower()),
            capabilities=tuple(payload.get("capabilities") or ()),
            verification=VerificationState(
                str(payload.get("verification") or "").strip().lower()
            ),
            discovery=str(payload.get("discovery") or ""),
            authentication=AuthPosture(
                str(payload.get("authentication") or "").strip().lower()
            ),
            monitoring=str(payload.get("monitoring") or ""),
            display_name=payload.get("display_name"),
        )
