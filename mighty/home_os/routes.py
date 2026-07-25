"""Flask handlers for the Home OS Marriott repair slice.

Thin HTTP adapter — business rules live in commands.py / workitem.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from mighty.home_os.commands import (
    CommandError,
    apply_expiration_if_needed,
    cancel_repair,
    complete_repair,
    fail_repair,
    project_slice,
    start_repair,
)
from mighty.home_os.gate import home_os_allowed, is_active_home_os_session
from mighty.home_os.render import render_home_os_page
from mighty.home_os.session_state import (
    begin_home_os_session,
    load_slice_state,
    new_slice_state,
    save_slice_state,
)


def unavailable_html() -> str:
    return (
        "<!DOCTYPE html><title>Not found</title>"
        "<body style='font-family:sans-serif;padding:40px'>"
        "<h1>Not found</h1>"
        "<p>Home OS preview is unavailable in this environment.</p>"
        "</body>"
    )


def handle_research_entry(session: Any) -> tuple[str, int] | None:
    """Begin Home OS session. Returns error body tuple, or None on success."""
    if not home_os_allowed():
        return unavailable_html(), 404
    begin_home_os_session(session)
    return None


def handle_home_get(
    session: Any,
    *,
    csrf_token_factory: Callable[[], str],
    as_of: datetime | None = None,
) -> tuple[str, int]:
    if not home_os_allowed():
        return unavailable_html(), 404
    now = as_of or datetime.now(timezone.utc)
    if not is_active_home_os_session(session):
        # Allow direct /home in staging demo by seeding a session.
        begin_home_os_session(session, as_of=now)
    # CSRF must be minted after session seeding (begin clears the cookie session).
    csrf_token = csrf_token_factory()
    slice_state = load_slice_state(session)
    if slice_state is None:
        slice_state = new_slice_state(as_of=now)
        save_slice_state(session, slice_state)
    apply_expiration_if_needed(slice_state, as_of=now)
    save_slice_state(session, slice_state)
    home = project_slice(slice_state, as_of=now)
    today = now.strftime("%a %b %d").replace(" 0", " ")
    html = render_home_os_page(
        home,
        slice_state,
        csrf_token=csrf_token,
        today_label=today,
    )
    return html, 200


def handle_work_command(
    session: Any,
    *,
    work_item_id: str,
    action: str,
    csrf_checker: Callable[[], None],
    as_of: datetime | None = None,
) -> tuple[str, int]:
    """POST command → redirect Location or error page."""
    csrf_checker()
    if not home_os_allowed() or not is_active_home_os_session(session):
        return unavailable_html(), 404
    now = as_of or datetime.now(timezone.utc)
    slice_state = load_slice_state(session)
    if slice_state is None:
        return unavailable_html(), 404
    apply_expiration_if_needed(slice_state, as_of=now)

    try:
        if action == "start":
            result = start_repair(
                slice_state, work_item_id=work_item_id, as_of=now
            )
        elif action == "complete":
            result = complete_repair(
                slice_state, work_item_id=work_item_id, as_of=now
            )
        elif action == "fail":
            result = fail_repair(
                slice_state, work_item_id=work_item_id, as_of=now
            )
        elif action == "cancel":
            result = cancel_repair(
                slice_state, work_item_id=work_item_id, as_of=now
            )
        else:
            return ("Unknown action", 400)
    except CommandError as exc:
        slice_state.repair_message = str(exc)
        save_slice_state(session, slice_state)
        return ("redirect:/home", 302)

    save_slice_state(session, result.state)
    return ("redirect:/home", 302)
