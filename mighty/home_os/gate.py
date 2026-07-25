"""Environment gates for the Home OS staging surface.

Never available in production. Default landing requires HOME_OS_ENABLED.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

_TRUTHY = frozenset({"1", "true", "yes", "on"})

SESSION_FLAG = "home_os_auth_repair"
SESSION_MODE_KEY = "home_os_mode"  # "ephemeral" | "authenticated"

LEGACY_DASHBOARD_PATH = "/dashboard/legacy"


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
    """Gate for /home surfaces — demo + non-production + staging/research."""
    if not _env_truthy("DEMO_MODE"):
        return False
    if is_production_environment():
        return False
    return is_staging_or_research_environment()


def home_os_is_default_landing() -> bool:
    """True when staging users should arrive at Home OS by default.

    Requires explicit HOME_OS_ENABLED (not merely RESEARCH_HOME_ENABLED).
    """
    if not _env_truthy("HOME_OS_ENABLED"):
        return False
    return home_os_allowed()


def default_app_path() -> str:
    """Post-login / landing destination for the current environment."""
    if home_os_is_default_landing():
        return "/home"
    return "/dashboard"


def is_home_os_session(session: Mapping[str, Any] | None) -> bool:
    if not session:
        return False
    return bool(session.get(SESSION_FLAG))


def is_active_home_os_session(session: Mapping[str, Any] | None) -> bool:
    return is_home_os_session(session) and home_os_allowed()


def home_os_session_mode(session: Mapping[str, Any] | None) -> str:
    if not session:
        return "ephemeral"
    mode = str(session.get(SESSION_MODE_KEY) or "").strip().lower()
    if mode in ("ephemeral", "authenticated"):
        return mode
    # Authenticated users with a real user_id default to authenticated mode.
    uid = str(session.get("user_id") or "")
    if uid and not uid.startswith("home-os-") and uid != "research-preview-session":
        return "authenticated"
    return "ephemeral"
