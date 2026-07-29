"""Journey narrative events — UBE truthful Home narrator.

Persists user-action and system-observation events separately. Home composes
story from the event stream so Visit/Sign-in is never forgotten and repeated
asks explain why the previous attempt did not produce the expected outcome (R1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Sequence

from mighty.admin_local_time import to_utc_iso_z
from mighty import user_copy

KIND_USER_ACTION = "user_action"
KIND_SYSTEM_OBSERVATION = "system_observation"

ACTION_PROVIDER_VISIT = "provider_visit"
ACTION_PROVIDER_SIGN_IN = "provider_sign_in"

OBS_AWAITING_CONFIRMATION = "awaiting_confirmation"
OBS_NO_PROGRESS = "no_progress"
OBS_STILL_NEEDS_LOGIN = "still_needs_login"
OBS_VERIFICATION_PROGRESS = "verification_progress"
OBS_TERMINAL_OK = "terminal_ok"

NarrativeBeat = Literal[
    "act_acknowledged",
    "waiting",
    "non_progress",
    "repeat_ask",
    "progress",
    "terminal",
    "unaffected",
]

# How long a Visit/Sign-in stays "active" for narration without a clearing terminal.
ACTIVE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class NarrativeEvent:
    id: str
    kind: str
    event_type: str
    provider: str
    created_at: str
    detail: dict[str, Any]

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.event_type}#{self.id}"


@dataclass(frozen=True)
class NarrativeComposeResult:
    title: str | None = None
    body: str | None = None
    cta_label: str | None = None
    beat: NarrativeBeat = "unaffected"
    event_ids: tuple[str, ...] = ()
    event_refs: tuple[str, ...] = ()
    eyebrow: str | None = None


def ensure_journey_narrative_table(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS journey_narrative_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            event_type TEXT NOT NULL,
            provider TEXT NOT NULL,
            detail_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_journey_narrative_user_created "
        "ON journey_narrative_events(user_id, created_at)"
    )
    try:
        db.commit()
    except Exception:
        pass


def _utc_now() -> str:
    return to_utc_iso_z(datetime.now(timezone.utc))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def record_narrative_event(
    db: Any,
    user_id: str,
    *,
    kind: str,
    event_type: str,
    provider: str,
    detail: dict[str, Any] | None = None,
    source: str = "app",
) -> NarrativeEvent:
    ensure_journey_narrative_table(db)
    kind = kind if kind in (KIND_USER_ACTION, KIND_SYSTEM_OBSERVATION) else KIND_SYSTEM_OBSERVATION
    provider_key = (provider or "unknown").strip().lower()[:64] or "unknown"
    event_type = (event_type or "note").strip()[:80] or "note"
    payload = dict(detail or {})
    payload.setdefault("source", source)
    created = _utc_now()
    cur = db.execute(
        "INSERT INTO journey_narrative_events"
        "(user_id, kind, event_type, provider, detail_json, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            user_id,
            kind,
            event_type,
            provider_key,
            json.dumps(payload)[:2000],
            created,
        ),
    )
    db.commit()
    row_id = str(cur.lastrowid)
    return NarrativeEvent(
        id=row_id,
        kind=kind,
        event_type=event_type,
        provider=provider_key,
        created_at=created,
        detail=payload,
    )


def record_user_action(
    db: Any,
    user_id: str,
    *,
    event_type: str,
    provider: str,
    detail: dict[str, Any] | None = None,
    source: str = "client",
) -> NarrativeEvent:
    return record_narrative_event(
        db,
        user_id,
        kind=KIND_USER_ACTION,
        event_type=event_type,
        provider=provider,
        detail=detail,
        source=source,
    )


def record_system_observation(
    db: Any,
    user_id: str,
    *,
    event_type: str,
    provider: str,
    detail: dict[str, Any] | None = None,
    source: str = "home",
) -> NarrativeEvent:
    return record_narrative_event(
        db,
        user_id,
        kind=KIND_SYSTEM_OBSERVATION,
        event_type=event_type,
        provider=provider,
        detail=detail,
        source=source,
    )


def recent_narrative_events(
    db: Any,
    user_id: str,
    *,
    limit: int = 40,
    provider: str | None = None,
) -> list[NarrativeEvent]:
    ensure_journey_narrative_table(db)
    lim = max(1, min(int(limit), 100))
    if provider:
        rows = db.execute(
            "SELECT id, kind, event_type, provider, detail_json, created_at "
            "FROM journey_narrative_events WHERE user_id=? AND provider=? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, provider.strip().lower(), lim),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, kind, event_type, provider, detail_json, created_at "
            "FROM journey_narrative_events WHERE user_id=? "
            "ORDER BY id DESC LIMIT ?",
            (user_id, lim),
        ).fetchall()
    out: list[NarrativeEvent] = []
    for r in rows:
        detail: dict[str, Any] = {}
        raw = r["detail_json"] if hasattr(r, "keys") else r[4]
        if raw:
            try:
                detail = json.loads(raw)
            except Exception:
                detail = {"raw": str(raw)[:200]}
        out.append(
            NarrativeEvent(
                id=str(r["id"] if hasattr(r, "keys") else r[0]),
                kind=str(r["kind"] if hasattr(r, "keys") else r[1]),
                event_type=str(r["event_type"] if hasattr(r, "keys") else r[2]),
                provider=str(r["provider"] if hasattr(r, "keys") else r[3]),
                created_at=str(r["created_at"] if hasattr(r, "keys") else r[5]),
                detail=detail,
            )
        )
    return out


def _latest_user_action(
    events: Sequence[NarrativeEvent],
    *,
    provider: str | None = None,
) -> NarrativeEvent | None:
    for ev in events:
        if ev.kind != KIND_USER_ACTION:
            continue
        if provider and ev.provider != provider.strip().lower():
            continue
        if ev.event_type in (ACTION_PROVIDER_VISIT, ACTION_PROVIDER_SIGN_IN):
            return ev
    return None


def _observations_after(
    events: Sequence[NarrativeEvent],
    action: NarrativeEvent,
) -> list[NarrativeEvent]:
    action_id = int(action.id)
    out: list[NarrativeEvent] = []
    for ev in events:
        if ev.kind != KIND_SYSTEM_OBSERVATION:
            continue
        if ev.provider != action.provider:
            continue
        try:
            if int(ev.id) <= action_id:
                continue
        except ValueError:
            continue
        out.append(ev)
    return out


def _action_still_active(action: NarrativeEvent, *, now: datetime | None = None) -> bool:
    created = _parse_ts(action.created_at)
    if created is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - created) <= ACTIVE_WINDOW


def ensure_observation_after_action(
    db: Any,
    user_id: str,
    action: NarrativeEvent,
    *,
    event_type: str,
    detail: dict[str, Any] | None = None,
    events: Sequence[NarrativeEvent] | None = None,
) -> NarrativeEvent:
    """Idempotent: reuse latest matching observation after action if present."""
    stream = list(events) if events is not None else recent_narrative_events(
        db, user_id, provider=action.provider, limit=40
    )
    for obs in _observations_after(stream, action):
        if obs.event_type == event_type:
            return obs
    return record_system_observation(
        db,
        user_id,
        event_type=event_type,
        provider=action.provider,
        detail=detail or {"after_user_action_id": action.id},
        source="home_sync",
    )


def sync_journey_observations(
    db: Any,
    user_id: str,
    *,
    provider: str,
    still_needs_user: bool,
    verification_active: bool = False,
    terminal_ok: bool = False,
) -> list[NarrativeEvent]:
    """Record honest observations after a recent user action (no invented success)."""
    events = recent_narrative_events(db, user_id, provider=provider, limit=40)
    action = _latest_user_action(events, provider=provider)
    if action is None or not _action_still_active(action):
        return events

    if terminal_ok:
        ensure_observation_after_action(
            db,
            user_id,
            action,
            event_type=OBS_TERMINAL_OK,
            detail={"after_user_action_id": action.id},
            events=events,
        )
    elif verification_active:
        ensure_observation_after_action(
            db,
            user_id,
            action,
            event_type=OBS_VERIFICATION_PROGRESS,
            detail={"after_user_action_id": action.id},
            events=events,
        )
    elif still_needs_user:
        # Prefer awaiting immediately; escalate to still_needs_login / no_progress
        # when any observation already exists or action is not brand-new.
        created = _parse_ts(action.created_at) or datetime.now(timezone.utc)
        age = datetime.now(timezone.utc) - created
        obs_after = _observations_after(events, action)
        if not obs_after and age < timedelta(seconds=45):
            ensure_observation_after_action(
                db,
                user_id,
                action,
                event_type=OBS_AWAITING_CONFIRMATION,
                detail={"after_user_action_id": action.id},
                events=events,
            )
        else:
            ensure_observation_after_action(
                db,
                user_id,
                action,
                event_type=OBS_STILL_NEEDS_LOGIN
                if still_needs_user
                else OBS_NO_PROGRESS,
                detail={
                    "after_user_action_id": action.id,
                    "expected": "confirmed_session",
                    "outcome": "not_confirmed",
                },
                events=events,
            )
    return recent_narrative_events(db, user_id, provider=provider, limit=40)


def compose_narrative_for_provider_ask(
    *,
    provider_key: str,
    provider_display: str,
    events: Sequence[NarrativeEvent],
    repeating_user_action: bool,
) -> NarrativeComposeResult | None:
    """Compose continuity / R1 copy when evidence still asks the user to Visit/Sign-in."""
    action = _latest_user_action(events, provider=provider_key)
    if action is None or not _action_still_active(action):
        return None

    obs_after = _observations_after(events, action)
    refs = [action.ref]
    ids = [action.id]
    latest_obs = obs_after[0] if obs_after else None
    if latest_obs is not None:
        refs.append(latest_obs.ref)
        ids.append(latest_obs.id)

    name = (provider_display or provider_key).strip() or "this account"
    action_label = (
        "opened"
        if action.event_type == ACTION_PROVIDER_VISIT
        else "started sign-in for"
    )

    if latest_obs is None or latest_obs.event_type == OBS_AWAITING_CONFIRMATION:
        return NarrativeComposeResult(
            title=user_copy.home_journey_waiting_headline(name),
            body=user_copy.home_journey_waiting_body(name, action_label=action_label),
            cta_label=None,  # keep caller CTA
            beat="waiting",
            event_ids=tuple(ids),
            event_refs=tuple(refs),
            eyebrow="In progress",
        )

    if latest_obs.event_type == OBS_VERIFICATION_PROGRESS:
        return NarrativeComposeResult(
            title=user_copy.home_journey_progress_headline(name),
            body=user_copy.home_journey_progress_body(name, action_label=action_label),
            beat="progress",
            event_ids=tuple(ids),
            event_refs=tuple(refs),
            eyebrow="Checking",
        )

    if latest_obs.event_type == OBS_TERMINAL_OK:
        return NarrativeComposeResult(
            beat="terminal",
            event_ids=tuple(ids),
            event_refs=tuple(refs),
        )

    # no_progress / still_needs_login / other — R1 when repeating the ask
    if repeating_user_action:
        return NarrativeComposeResult(
            title=user_copy.home_journey_repeat_ask_headline(name),
            body=user_copy.home_journey_repeat_ask_body(
                name, action_label=action_label
            ),
            beat="repeat_ask",
            event_ids=tuple(ids),
            event_refs=tuple(refs),
            eyebrow="Still needed",
        )

    return NarrativeComposeResult(
        title=user_copy.home_journey_non_progress_headline(name),
        body=user_copy.home_journey_non_progress_body(name, action_label=action_label),
        beat="non_progress",
        event_ids=tuple(ids),
        event_refs=tuple(refs),
        eyebrow="Waiting on confirmation",
    )


def apply_narrative_to_home_card(
    card: Any,
    compose: NarrativeComposeResult,
) -> Any:
    """Return a replaced HomeCard with narrative copy + event binding."""
    if compose.beat == "terminal" and not compose.title:
        # Leave success/other stories alone but stamp event ids if card supports it.
        try:
            return replace(
                card,
                narrative_event_ids=compose.event_ids,
                narrative_event_refs=compose.event_refs,
                narrative_beat=compose.beat,
            )
        except TypeError:
            return card

    kwargs: dict[str, Any] = {
        "narrative_event_ids": compose.event_ids,
        "narrative_event_refs": compose.event_refs,
        "narrative_beat": compose.beat,
    }
    if compose.title is not None:
        kwargs["title"] = compose.title
    if compose.body is not None:
        kwargs["body"] = compose.body
    if compose.eyebrow is not None:
        kwargs["eyebrow"] = compose.eyebrow
    if compose.cta_label is not None:
        kwargs["cta_label"] = compose.cta_label
    try:
        return replace(card, **kwargs)
    except TypeError:
        return card


def apply_journey_narrative_to_projection(
    projection: Any,
    db: Any,
    user_id: str,
    *,
    still_needs_user: bool,
    provider_key: str | None,
    provider_display: str | None = None,
    verification_active: bool = False,
    terminal_ok: bool = False,
) -> Any:
    """Sync observations and overlay Home featured story when a journey is active."""
    if projection is None or not user_id:
        return projection
    card = getattr(projection, "featured", None)
    key = (provider_key or getattr(card, "provider", None) or "").strip().lower()
    if not key:
        # Infer amex as default when CTA looks like provider visit without key
        return projection

    display = (provider_display or key.replace("_", " ").title()).strip()
    events = sync_journey_observations(
        db,
        user_id,
        provider=key,
        still_needs_user=still_needs_user,
        verification_active=verification_active,
        terminal_ok=terminal_ok,
    )
    if card is None:
        return projection

    repeating = bool(
        still_needs_user
        and card.cta_label
        and card.cta_url
        and str(card.cta_url).startswith("http")
    )
    compose = compose_narrative_for_provider_ask(
        provider_key=key,
        provider_display=display,
        events=events,
        repeating_user_action=repeating,
    )
    if compose is None:
        return projection

    new_card = apply_narrative_to_home_card(card, compose)
    try:
        return replace(
            projection,
            featured=new_card,
            narrative_event_ids=compose.event_ids,
            narrative_event_refs=compose.event_refs,
            narrative_beat=compose.beat,
            answer=compose.title or projection.answer,
        )
    except TypeError:
        return projection
