"""Environment gates for the Home OS staging surface.

Never available in production. Requires DEMO_MODE plus an explicit
staging/research label (same posture as research_home).
"""

from __future__ import annotations

import os
from typing import Any, Mapping

_TRUTHY = frozenset({"1", "true", "yes", "on"})

SESSION_FLAG = "home_os_auth_repair"


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def _env_name() -> str:
    return (
        (os.environ.get("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
        or (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip().lower()
        or (os.environ.get("MIGHTY_ENV") or "").strip().lower()
    )


def is_production_environment() -> bool:
    if _env_name() == "production":
        return True
    if (os.environ.get("MIGHTY_ENV") or "").strip().lower() == "production":
        return True
    return False


def is_staging_or_research_environment() -> bool:
    if _env_truthy("HOME_OS_ENABLED"):
        return True
    if _env_truthy("RESEARCH_HOME_ENABLED"):
        return True
    return _env_name() in ("staging", "research")


def home_os_allowed() -> bool:
    """Gate for /home and /research/home-os — demo + non-production + staging."""
    if not _env_truthy("DEMO_MODE"):
        return False
    if is_production_environment():
        return False
    return is_staging_or_research_environment()


def is_home_os_session(session: Mapping[str, Any] | None) -> bool:
    if not session:
        return False
    return bool(session.get(SESSION_FLAG))


def is_active_home_os_session(session: Mapping[str, Any] | None) -> bool:
    return is_home_os_session(session) and home_os_allowed()
