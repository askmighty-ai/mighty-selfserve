"""Flask handlers for Home OS staging — thin HTTP adapter.

Business rules live in commands.py / workitem / compose adapters.
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
from mighty.home_os.compose import (
    SIM_AUTH_REPAIR_COMPLETION,
    compose_for_authenticated_user,
    compose_for_ephemeral,
    compose_for_future_preview,
    slice_from_compose,
)
from mighty.home_os.future_preview import PERSONA_USER_ID, preview_as_of
from mighty.home_os.gate import (
    home_os_allowed,
    home_os_is_default_landing,
    home_os_session_mode,
    is_active_home_os_session,
    is_future_preview_session,
)
from mighty.home_os.render import render_home_os_page
from mighty.home_os.session_state import (
    HOME_OS_USER_ID,
    RepairPhase,
    SessionOverlays,
    begin_future_preview_session,
    begin_home_os_session,
    enable_home_os_for_authenticated_user,
    load_session_overlays,
    load_slice_state,
    save_session_overlays,
    save_simulation_tags,
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
    """Begin ephemeral Home OS session. Returns error body tuple, or None."""
    if not home_os_allowed():
        return unavailable_html(), 404
    begin_home_os_session(session)
    return None


def handle_future_preview_entry(
    session: Any,
    *,
    state: str | None = "full",
    include_interrupt: bool = False,
) -> tuple[str, int] | None:
    """Begin review-only Future Preview session. Returns error body, or None."""
    if not home_os_allowed():
        return unavailable_html(), 404
    begin_future_preview_session(
        session,
        as_of=preview_as_of(),
        state=state,
        include_interrupt=include_interrupt,
    )
    return None


def _display_name_from_user(user: Any) -> str:
    if user is None:
        return "there"
    preferred = ""
    try:
        preferred = str(user["preferred_name"] or "").strip()
    except Exception:
        preferred = str(getattr(user, "preferred_name", "") or "").strip()
    if preferred:
        return preferred.split()[0]
    try:
        email = str(user["email"] or "")
    except Exception:
        email = str(getattr(user, "email", "") or "")
    if email and "@" in email:
        return email.split("@", 1)[0]
    return "there"


def _is_real_user_id(user_id: str | None) -> bool:
    uid = str(user_id or "").strip()
    if not uid:
        return False
    if uid == HOME_OS_USER_ID:
        return False
    if uid == PERSONA_USER_ID:
        return False
    if uid == "research-preview-session":
        return False
    return True


def handle_home_get(
    session: Any,
    *,
    csrf_token_factory: Callable[[], str],
    db: Any | None = None,
    user_row: Any | None = None,
    as_of: datetime | None = None,
) -> tuple[str, int]:
    if not home_os_allowed():
        return unavailable_html(), 404

    future = is_future_preview_session(session)
    # Future Preview always projects against the fixed scenario clock.
    now = as_of or (preview_as_of() if future else datetime.now(timezone.utc))
    user_id = session.get("user_id") if session is not None else None

    if _is_real_user_id(user_id) and db is not None:
        enable_home_os_for_authenticated_user(session)
        overlays = load_session_overlays(session)
        compose = compose_for_authenticated_user(
            db,
            str(user_id),
            as_of=now,
            display_name=_display_name_from_user(user_row),
            overlays=overlays,
        )
        slice_state = slice_from_compose(compose, overlays=overlays)
        save_slice_state(session, slice_state)
        save_simulation_tags(session, compose.simulation_tags)
    else:
        if not is_active_home_os_session(session):
            begin_home_os_session(session, as_of=now)
            future = False
        overlays = load_session_overlays(session)
        # Ephemeral: prefer stored slice (commands mutate it), else seed.
        slice_state = load_slice_state(session)
        if slice_state is None:
            if is_future_preview_session(session):
                compose = compose_for_future_preview(as_of=preview_as_of())
            else:
                compose = compose_for_ephemeral(as_of=now)
            slice_state = slice_from_compose(compose, overlays=overlays)
            save_slice_state(session, slice_state)
            save_simulation_tags(session, compose.simulation_tags)
        if not is_future_preview_session(session):
            apply_expiration_if_needed(slice_state, as_of=now)
        save_slice_state(session, slice_state)
        if is_future_preview_session(session):
            now = as_of or preview_as_of()

    csrf_token = csrf_token_factory()
    home = project_slice(slice_state, as_of=now)
    today = now.strftime("%a %b %d").replace(" 0", " ")
    html = render_home_os_page(
        home,
        slice_state,
        csrf_token=csrf_token,
        today_label=today,
        simulation_tags=load_simulation_tags_safe(session),
    )
    return html, 200


def load_simulation_tags_safe(session: Any) -> tuple[str, ...]:
    from mighty.home_os.session_state import load_simulation_tags

    return load_simulation_tags(session)


def handle_work_command(
    session: Any,
    *,
    work_item_id: str,
    action: str,
    csrf_checker: Callable[[], None],
    db: Any | None = None,
    user_row: Any | None = None,
    as_of: datetime | None = None,
) -> tuple[str, int]:
    """POST command → redirect Location or error page."""
    csrf_checker()
    if not home_os_allowed():
        return unavailable_html(), 404

    # Future Preview is review-only — never mutate the deterministic seed.
    if is_future_preview_session(session):
        return ("redirect:/home", 302)

    now = as_of or datetime.now(timezone.utc)
    user_id = session.get("user_id") if session is not None else None
    overlays = load_session_overlays(session)

    if _is_real_user_id(user_id) and db is not None:
        enable_home_os_for_authenticated_user(session)
        compose = compose_for_authenticated_user(
            db,
            str(user_id),
            as_of=now,
            display_name=_display_name_from_user(user_row),
            overlays=overlays,
        )
        slice_state = slice_from_compose(compose, overlays=overlays)
    else:
        if not is_active_home_os_session(session):
            return unavailable_html(), 404
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
        overlays.repair_message = str(exc)
        save_session_overlays(session, overlays)
        save_slice_state(session, slice_state)
        return ("redirect:/home", 302)

    # Persist slice + session overlays for both modes.
    save_slice_state(session, result.state)
    overlays = _overlays_from_slice(result.state, overlays, action=action)
    if action == "complete":
        # Authenticated recompose path needs durable overlay markers.
        completed = set(overlays.completed_work_item_ids)
        completed.add(work_item_id)
        overlays.completed_work_item_ids = tuple(sorted(completed))
        if result.proof is not None:
            extras = [result.proof, *overlays.extra_proof]
            # de-dupe by id
            seen: set[str] = set()
            deduped = []
            for p in extras:
                if p.id in seen:
                    continue
                seen.add(p.id)
                deduped.append(p)
            overlays.extra_proof = tuple(deduped)
        item = next(
            (i for i in result.state.work_items if i.id == work_item_id),
            None,
        )
        provider = item.provider if item else None
        if provider:
            overlays.coverage_auth_overrides[provider] = "valid"
        tags = list(load_simulation_tags_safe(session))
        if SIM_AUTH_REPAIR_COMPLETION not in tags:
            tags.append(SIM_AUTH_REPAIR_COMPLETION)
        save_simulation_tags(session, tuple(tags))
    save_session_overlays(session, overlays)
    return ("redirect:/home", 302)


def _overlays_from_slice(
    slice_state: Any,
    prior: SessionOverlays,
    *,
    action: str,
) -> SessionOverlays:
    return SessionOverlays(
        repair_phase=slice_state.repair_phase,
        repair_message=slice_state.repair_message,
        repair_work_item_id=prior.repair_work_item_id,
        completed_work_item_ids=prior.completed_work_item_ids,
        extra_proof=prior.extra_proof,
        coverage_auth_overrides=dict(prior.coverage_auth_overrides),
    )


def should_redirect_dashboard_to_home() -> bool:
    return home_os_is_default_landing()
