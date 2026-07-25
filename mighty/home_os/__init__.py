"""Home OS vertical slices — staging surfaces that consume mighty.workitem.

Production ``/dashboard`` remains unchanged. These modules power gated
``/home`` experiences for Home OS migration.
"""

from __future__ import annotations

from mighty.home_os.gate import (
    home_os_allowed,
    home_os_is_default_landing,
    is_home_os_session,
)

__all__ = [
    "home_os_allowed",
    "home_os_is_default_landing",
    "is_home_os_session",
]
