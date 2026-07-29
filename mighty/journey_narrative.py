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

OBS_AWAITING_CONFIRMATION = "awaiting_confirmation"  # legacy; do not mint from intent
OBS_NO_PROGRESS = "no_progress"
OBS_STILL_NEEDS_LOGIN = "still_needs_login"
OBS_VERIFICATION_PROGRESS = "verification_progress"
OBS_TERMINAL_OK = "terminal_ok"

# Evidence tiers (R2): intent ≠ observed progress ≠ verified outcome
TIER_INTENT = "intent"
TIER_OBSERVED_PROGRESS = "observed_progress"
TIER_VERIFIED_OUTCOME = "verified_outcome"
TIER_OBSERVED_NEGATIVE = "observed_negative"  # still needs login / no progress

NarrativeBeat = Literal[
    "intent",
    "act_acknowledged",  # alias for intent (compat)
    "waiting",
    "non_progress",
    "repeat_ask",
    "progress",
    "terminal",
    "unaffected",
]

EvidenceTier = Literal[
    "intent",
    "observed_progress",
    "observed_negative",
    "verified_outcome",
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
    evidence_tier: EvidenceTier = TIER_INTENT
    authorizing_evidence: str = ""  # human-readable: which events authorize this transition


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
    """Record system observations only from real evidence — never from Visit intent alone.

    R2: Do not mint verification_progress / awaiting_confirmation solely because the
    user clicked Visit. Progress observations require verification_active evidence;
    terminal requires terminal_ok; negative still_needs_login may record when AuthTruth
    still requires the user after a Visit.
    """
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
            detail={
                "after_user_action_id": action.id,
                "evidence": "home_terminal_ok",
            },
            events=events,
        )
    elif verification_active:
        ensure_observation_after_action(
            db,
            user_id,
            action,
            event_type=OBS_VERIFICATION_PROGRESS,
            detail={
                "after_user_action_id": action.id,
                "evidence": "verification_or_updating_active",
            },
            events=events,
        )
    elif still_needs_user:
        # Honest negative observation from current access truth — not "progress."
        ensure_observation_after_action(
            db,
            user_id,
            action,
            event_type=OBS_STILL_NEEDS_LOGIN,
            detail={
                "after_user_action_id": action.id,
                "expected": "confirmed_session",
                "outcome": "not_confirmed",
                "evidence": "auth_or_health_still_needs_user",
            },
            events=events,
        )
    return recent_narrative_events(db, user_id, provider=provider, limit=40)


def _authorize(
    *,
    beat: NarrativeBeat,
    tier: EvidenceTier,
    events: Sequence[NarrativeEvent],
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    ids = tuple(e.id for e in events)
    refs = tuple(e.ref for e in events)
    parts = [f"tier={tier}", f"beat={beat}"] + [e.ref for e in events]
    return ids, refs, "; ".join(parts)


def compose_narrative_for_provider_ask(
    *,
    provider_key: str,
    provider_display: str,
    events: Sequence[NarrativeEvent],
    repeating_user_action: bool,
) -> NarrativeComposeResult | None:
    """Compose continuity / R1 / R2 copy — claims gated by evidence tier."""
    action = _latest_user_action(events, provider=provider_key)
    if action is None or not _action_still_active(action):
        return None

    obs_after = _observations_after(events, action)
    # Prefer strongest authorizing observation (progress > terminal > negative)
    latest_obs = None
    for prefer in (
        OBS_TERMINAL_OK,
        OBS_VERIFICATION_PROGRESS,
        OBS_STILL_NEEDS_LOGIN,
        OBS_NO_PROGRESS,
    ):
        for obs in obs_after:
            if obs.event_type == prefer:
                latest_obs = obs
                break
        if latest_obs is not None:
            break
    if latest_obs is None and obs_after:
        latest_obs = obs_after[0]

    name = (provider_display or provider_key).strip() or "this account"
    action_label = (
        "opened"
        if action.event_type == ACTION_PROVIDER_VISIT
        else "started sign-in for"
    )

    # --- verified outcome ---
    if latest_obs is not None and latest_obs.event_type == OBS_TERMINAL_OK:
        ids, refs, auth = _authorize(
            beat="terminal",
            tier=TIER_VERIFIED_OUTCOME,
            events=(action, latest_obs),
        )
        return NarrativeComposeResult(
            beat="terminal",
            event_ids=ids,
            event_refs=refs,
            evidence_tier=TIER_VERIFIED_OUTCOME,
            authorizing_evidence=auth,
        )

    # --- observed progress (verification underway) ---
    if latest_obs is not None and latest_obs.event_type == OBS_VERIFICATION_PROGRESS:
        ids, refs, auth = _authorize(
            beat="progress",
            tier=TIER_OBSERVED_PROGRESS,
            events=(action, latest_obs),
        )
        return NarrativeComposeResult(
            title=user_copy.home_journey_progress_headline(name),
            body=user_copy.home_journey_progress_body(name, action_label=action_label),
            beat="progress",
            event_ids=ids,
            event_refs=refs,
            eyebrow="Checking",
            evidence_tier=TIER_OBSERVED_PROGRESS,
            authorizing_evidence=auth,
        )

    # --- observed negative (still needs login / no progress) ---
    if latest_obs is not None and latest_obs.event_type in (
        OBS_STILL_NEEDS_LOGIN,
        OBS_NO_PROGRESS,
    ):
        if repeating_user_action:
            ids, refs, auth = _authorize(
                beat="repeat_ask",
                tier=TIER_OBSERVED_NEGATIVE,
                events=(action, latest_obs),
            )
            return NarrativeComposeResult(
                title=user_copy.home_journey_repeat_ask_headline(name),
                body=user_copy.home_journey_repeat_ask_body(
                    name, action_label=action_label
                ),
                beat="repeat_ask",
                event_ids=ids,
                event_refs=refs,
                eyebrow="Still needed",
                evidence_tier=TIER_OBSERVED_NEGATIVE,
                authorizing_evidence=auth,
            )
        ids, refs, auth = _authorize(
            beat="non_progress",
            tier=TIER_OBSERVED_NEGATIVE,
            events=(action, latest_obs),
        )
        return NarrativeComposeResult(
            title=user_copy.home_journey_non_progress_headline(name),
            body=user_copy.home_journey_non_progress_body(
                name, action_label=action_label
            ),
            beat="non_progress",
            event_ids=ids,
            event_refs=refs,
            eyebrow="Not confirmed yet",
            evidence_tier=TIER_OBSERVED_NEGATIVE,
            authorizing_evidence=auth,
        )

    # --- intent only (user-action; no authorizing system observation) ---
    # R2: must NOT claim verifying or "you do not need to do anything else."
    ids, refs, auth = _authorize(
        beat="intent",
        tier=TIER_INTENT,
        events=(action,),
    )
    return NarrativeComposeResult(
        title=user_copy.home_journey_intent_headline(name),
        body=user_copy.home_journey_intent_body(name, action_label=action_label),
        cta_label=None,
        beat="intent",
        event_ids=ids,
        event_refs=refs,
        eyebrow="You acted",
        evidence_tier=TIER_INTENT,
        authorizing_evidence=auth,
    )


def apply_narrative_to_home_card(
    card: Any,
    compose: NarrativeComposeResult,
) -> Any:
    """Return a replaced HomeCard with narrative copy + event binding."""
    if compose.beat == "terminal" and not compose.title:
        try:
            return replace(
                card,
                narrative_event_ids=compose.event_ids,
                narrative_event_refs=compose.event_refs,
                narrative_beat=compose.beat,
                narrative_evidence_tier=compose.evidence_tier,
                narrative_authorizing_evidence=compose.authorizing_evidence,
            )
        except TypeError:
            return card

    kwargs: dict[str, Any] = {
        "narrative_event_ids": compose.event_ids,
        "narrative_event_refs": compose.event_refs,
        "narrative_beat": compose.beat,
        "narrative_evidence_tier": compose.evidence_tier,
        "narrative_authorizing_evidence": compose.authorizing_evidence,
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
        return projection

    # AT-13: Chrome-setup primary must not be overwritten by Amex Visit narrative.
    cta_url = str(getattr(card, "cta_url", None) or "") if card is not None else ""
    if "extension-setup" in cta_url:
        if key:
            sync_journey_observations(
                db,
                user_id,
                provider=key,
                still_needs_user=still_needs_user,
                verification_active=verification_active,
                terminal_ok=terminal_ok,
            )
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
            narrative_evidence_tier=compose.evidence_tier,
            narrative_authorizing_evidence=compose.authorizing_evidence,
            answer=compose.title or projection.answer,
        )
    except TypeError:
        return projection
