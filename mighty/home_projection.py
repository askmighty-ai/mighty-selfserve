"""
mighty.home_projection
──────────────────────
Compose Home V2 (Living Calm) from existing platform projections.

Presentation composition only. Does not rank Attention, score opportunities,
plan recovery, authorize agents, or invent enrollment / change policy.

See docs/HOME_V1.md, docs/LIVING_CALM_V1.md, docs/VISUAL_HIERARCHY.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from mighty.attention import AttentionClass
from mighty.attention_view import AttentionPresentation, AttentionView
from mighty.home_state import (
    HomeFeatured,
    HomeState,
    HomeStateResult,
)
from mighty import user_copy

HomeCardTone = Literal["interrupt", "opportunity", "progress", "calm", "neutral"]
HomeCardKind = Literal[
    "featured",
    "secondary",
    "silence",
    "enrollment",
    "story",
]
HomeStoryKind = Literal[
    "attention",
    "opportunity",
    "all_clear",
    "empty",
    "handoff",
    "first_success",
]
HomeVisualState = Literal[
    "healthy",
    "attention",
    "opportunity",
    "empty",
    "handoff",
]


@dataclass(frozen=True)
class HomeCard:
    """Featured story DTO — primary message content for Home."""

    kind: HomeCardKind
    title: str
    body: str = ""
    tone: HomeCardTone = "neutral"
    eyebrow: str | None = None
    cta_label: str | None = None
    cta_url: str | None = None
    secondary_label: str | None = None
    secondary_url: str | None = None
    # "snooze" → Not now via Attention snooze API (no navigation URL).
    secondary_action: str | None = None
    disabled_cta_label: str | None = None
    attention_id: str | None = None
    attention_class: str | None = None
    provider: str | None = None
    # Journey narrator (UBE): which narrative event(s) this card reflects.
    narrative_event_ids: tuple[str, ...] = ()
    narrative_event_refs: tuple[str, ...] = ()
    narrative_beat: str | None = None


@dataclass(frozen=True)
class HomeWin:
    """One activity preview line — projected from meaningful account_changes."""

    message: str
    provider: str | None = None
    href: str | None = None


@dataclass(frozen=True)
class HomeOpsNote:
    """Quiet operational footnote — never the featured story."""

    text: str
    href: str | None = None


@dataclass(frozen=True)
class HomeEvidenceItem:
    """Concise proof that the primary signal is earned (L3)."""

    label: str
    ok: bool | None = None


@dataclass(frozen=True)
class HomeProjection:
    """Pure projection for Home V2 Living Calm."""

    enrollment_state: HomeState
    first_name: str
    today_label: str
    answer: str
    story_kind: HomeStoryKind
    visual_state: HomeVisualState
    featured: HomeCard | None
    evidence: tuple[HomeEvidenceItem, ...] = ()
    recent_wins: tuple[HomeWin, ...] = ()
    ops_notes: tuple[HomeOpsNote, ...] = ()
    last_checked: str = ""
    watched_count: int = 0
    gmail_connected: bool | None = None
    chrome_active: bool | None = None
    attention_silence: str | None = None
    attention_interrupt: bool = False
    show_truth_debug: bool = False
    narrative_event_ids: tuple[str, ...] = ()
    narrative_event_refs: tuple[str, ...] = ()
    narrative_beat: str | None = None

    @property
    def activity_preview(self) -> tuple[HomeWin, ...]:
        return self.recent_wins

    # Compatibility aliases for older call sites / tests
    @property
    def priority_summary(self) -> str:
        return self.answer

    @property
    def secondary(self) -> tuple[HomeCard, ...]:
        return ()

    @property
    def show_health(self) -> bool:
        return False

    @property
    def health_chips(self) -> tuple:
        return ()

    @property
    def waiting_rows(self) -> tuple:
        return ()

    @property
    def show_metrics(self) -> bool:
        return False

    @property
    def activity_pending_count(self) -> int:
        return 0


def project_home(
    result: HomeStateResult,
    *,
    first_name: str,
    today_label: str,
    last_checked: str = "",
    attention: AttentionView | None = None,
    use_attention: bool = False,
    recent_wins: Sequence[Mapping[str, Any] | HomeWin] | None = None,
    gmail_connected: bool | None = None,
    chrome_active: bool | None = None,
    first_success_provider: str | None = None,
    first_success_partial: bool = False,
) -> HomeProjection:
    """Compose HomeProjection from enrollment + Attention + connection evidence."""
    attn = attention if use_attention else None
    featured, story_kind = _compose_story(
        result,
        attn,
        first_success_provider=first_success_provider,
        first_success_partial=first_success_partial,
    )
    answer = _compose_answer(result, attn, story_kind, featured)
    visual_state = _visual_state(story_kind)
    silence = (
        attn.silence.value
        if attn is not None and attn.silence is not None
        else None
    )
    interrupt = bool(attn is not None and attn.render_hints.interrupt)
    checked = last_checked or result.freshness_label
    watched = _watched_count(result)

    return HomeProjection(
        enrollment_state=result.state,
        first_name=first_name,
        today_label=today_label,
        answer=answer,
        story_kind=story_kind,
        visual_state=visual_state,
        featured=featured,
        evidence=_compose_evidence(
            result,
            story_kind=story_kind,
            last_checked=checked,
            watched_count=watched,
            gmail_connected=gmail_connected,
            chrome_active=chrome_active,
        ),
        recent_wins=_project_wins(recent_wins),
        ops_notes=_compose_ops(result, attn, story_kind),
        last_checked=checked,
        watched_count=watched,
        gmail_connected=gmail_connected,
        chrome_active=chrome_active,
        attention_silence=silence,
        attention_interrupt=interrupt,
        show_truth_debug=bool(result.show_access_debug),
    )


def _visual_state(story_kind: HomeStoryKind) -> HomeVisualState:
    if story_kind in ("all_clear", "first_success"):
        return "healthy"
    if story_kind in ("attention", "opportunity", "empty", "handoff"):
        return story_kind
    return "healthy"


def _watched_count(result: HomeStateResult) -> int:
    connected = len(result.health.connected_names or [])
    if connected:
        return connected
    # Fall back to portfolio size from health buckets when names are empty.
    return (
        result.health.up_to_date
        + result.health.waiting
        + result.health.needs_login
        + result.health.needs_attention
    )


def _portfolio_needs_user(result: HomeStateResult) -> bool:
    """True when watched accounts prove the user is needed (Attention may be silent)."""
    return bool(result.health.needs_login or result.health.needs_attention)


def _compose_story(
    result: HomeStateResult,
    attention: AttentionView | None,
    *,
    first_success_provider: str | None = None,
    first_success_partial: bool = False,
) -> tuple[HomeCard | None, HomeStoryKind]:
    if result.state == HomeState.EMPTY:
        return (
            _from_featured(result.featured, tone="calm", kind="enrollment"),
            "empty",
        )

    if attention is not None and attention.primary is not None:
        card = _from_attention(attention.primary, kind="story")
        kind: HomeStoryKind = (
            "opportunity" if card.tone == "opportunity" else "attention"
        )
        return card, kind

    # First-data handoff: WAITING owns the hero when Attention is silent.
    # Do not say "You're good" while enrollment setup is incomplete (D1).
    if result.state == HomeState.WAITING:
        provider = None
        if result.capability is not None:
            provider = getattr(result.capability, "provider", None)
        return (
            _from_featured(
                result.featured,
                tone="progress",
                kind="enrollment",
                provider=provider,
            ),
            "handoff",
        )

    # CP-006: Attention silence ≠ all-clear when portfolio health proves need.
    # Prefer Attention when available; this is the honesty fallback only.
    if _portfolio_needs_user(result):
        return _honesty_needs_user_story(result), "attention"

    # Honest waiting: refresh in flight is not Permission to Leave.
    if result.state == HomeState.UPDATE:
        return (
            _from_featured(result.featured, tone="progress", kind="enrollment"),
            "handoff",
        )

    # One-shot first-success beat (CP-005) before ambient all-clear.
    if first_success_provider:
        return (
            _first_success_story(
                first_success_provider, partial=first_success_partial
            ),
            "first_success",
        )

    return _all_clear_story(result), "all_clear"


def _honesty_needs_user_story(result: HomeStateResult) -> HomeCard:
    """One earned ask when Attention is silent but portfolio health needs the user."""
    count = result.health.needs_login or result.health.needs_attention
    return HomeCard(
        kind="story",
        title=user_copy.HOME_PRIORITY_LOGIN,
        body=user_copy.home_steady_needs_sign_in_body(count),
        tone="interrupt",
        cta_label=user_copy.HOME_VIEW_NEEDS_LOGIN_LABEL,
        cta_url="/credentials?filter=needs_login",
        secondary_label=None,
        secondary_url=None,
    )


def _first_success_story(provider_name: str, *, partial: bool) -> HomeCard:
    body = (
        user_copy.home_first_success_partial_body(provider_name)
        if partial
        else user_copy.home_first_success_body(provider_name)
    )
    return HomeCard(
        kind="story",
        title="",
        body=body,
        tone="calm",
        cta_label=None,
        cta_url=None,
        secondary_label=user_copy.HOME_VIEW_ACCOUNTS_LABEL,
        secondary_url="/credentials",
    )


def _all_clear_story(result: HomeStateResult) -> HomeCard:
    del result
    return HomeCard(
        kind="story",
        title="",
        body=user_copy.home_v2_healthy_body(),
        tone="calm",
        cta_label=None,
        cta_url=None,
        secondary_label=user_copy.HOME_VIEW_ACCOUNTS_LABEL,
        secondary_url="/credentials",
    )


def _compose_answer(
    result: HomeStateResult,
    attention: AttentionView | None,
    story_kind: HomeStoryKind,
    featured: HomeCard | None,
) -> str:
    del result, attention
    if story_kind == "empty":
        return (featured.title if featured and featured.title else "")
    if story_kind == "attention":
        # Living Calm L1: the concrete ask, not a meta status line.
        if featured and featured.title:
            return featured.title
        return user_copy.HOME_BRIEFING_ANSWER_ATTENTION
    if story_kind == "opportunity":
        if featured and featured.title:
            return featured.title
        return user_copy.HOME_BRIEFING_ANSWER_OPPORTUNITY
    if story_kind == "handoff":
        if featured and featured.title:
            return featured.title
        return user_copy.HOME_BRIEFING_ANSWER_HANDOFF
    # first_success and all_clear share the You're good answer line.
    return user_copy.HOME_BRIEFING_ANSWER_GOOD


def _compose_evidence(
    result: HomeStateResult,
    *,
    story_kind: HomeStoryKind,
    last_checked: str,
    watched_count: int,
    gmail_connected: bool | None,
    chrome_active: bool | None,
) -> tuple[HomeEvidenceItem, ...]:
    """Light L3 proof — calm on healthy; never a dashboard metric strip."""
    items: list[HomeEvidenceItem] = []

    if story_kind == "empty":
        if gmail_connected is False:
            items.append(HomeEvidenceItem(label=user_copy.HOME_EVIDENCE_GMAIL_NEEDED, ok=False))
        elif gmail_connected is True:
            items.append(HomeEvidenceItem(label=user_copy.HOME_EVIDENCE_GMAIL_CONNECTED, ok=True))
        if chrome_active is True:
            items.append(HomeEvidenceItem(label=user_copy.HOME_EVIDENCE_CHROME_ACTIVE, ok=True))
        return tuple(items)

    healthy_story = story_kind in ("all_clear", "first_success")
    if watched_count > 0:
        items.append(
            HomeEvidenceItem(
                label=user_copy.home_evidence_watching(watched_count),
                ok=True if healthy_story else None,
            )
        )

    if last_checked:
        items.append(
            HomeEvidenceItem(
                label=user_copy.home_freshness_label(last_checked),
                ok=True if healthy_story else None,
            )
        )

    if gmail_connected is True:
        items.append(HomeEvidenceItem(label=user_copy.HOME_EVIDENCE_GMAIL_CONNECTED, ok=True))
    elif gmail_connected is False and story_kind in ("handoff", "all_clear", "first_success"):
        items.append(HomeEvidenceItem(label=user_copy.HOME_EVIDENCE_GMAIL_NEEDED, ok=False))

    if chrome_active is True:
        items.append(HomeEvidenceItem(label=user_copy.HOME_EVIDENCE_CHROME_ACTIVE, ok=True))
    elif chrome_active is False:
        items.append(HomeEvidenceItem(label=user_copy.HOME_EVIDENCE_CHROME_NEEDED, ok=False))

    if result.state == HomeState.UPDATE and result.updating_display_name:
        items.append(
            HomeEvidenceItem(
                label=user_copy.home_ops_refreshing(result.updating_display_name),
                ok=None,
            )
        )

    return tuple(items[:4])


def _compose_ops(
    result: HomeStateResult,
    attention: AttentionView | None,
    story_kind: HomeStoryKind,
) -> tuple[HomeOpsNote, ...]:
    notes: list[HomeOpsNote] = []

    if result.state == HomeState.UPDATE and result.updating_display_name:
        notes.append(
            HomeOpsNote(
                text=user_copy.home_ops_refreshing(result.updating_display_name),
            )
        )
    elif result.state == HomeState.WAITING and story_kind != "handoff":
        if result.waiting_rows:
            name = result.waiting_rows[0].display_name
            notes.append(
                HomeOpsNote(
                    text=user_copy.home_ops_setting_up_provider(name),
                    href="/credentials?filter=waiting",
                )
            )
        elif result.health.waiting:
            notes.append(
                HomeOpsNote(
                    text=user_copy.home_ops_setting_up(result.health.waiting),
                    href="/credentials?filter=waiting",
                )
            )

    # Needs-login is the hero when story_kind == attention (honesty fallback);
    # do not duplicate it as an ops footnote under the same ask.
    if (
        story_kind not in ("attention", "opportunity")
        and result.health.needs_login
        and (attention is None or attention.primary is None)
    ):
        notes.append(
            HomeOpsNote(
                text=user_copy.home_ops_needs_login(result.health.needs_login),
                href="/credentials?filter=needs_login",
            )
        )

    if result.activity_pending_count:
        notes.append(
            HomeOpsNote(
                text=user_copy.HOME_ACTIVITY_LINK.format(
                    count=result.activity_pending_count,
                ),
                href="#pending-badge",
            )
        )

    return tuple(notes[:3])


def _project_wins(
    recent_wins: Sequence[Mapping[str, Any] | HomeWin] | None,
) -> tuple[HomeWin, ...]:
    if not recent_wins:
        return ()
    wins: list[HomeWin] = []
    for item in recent_wins[:3]:
        if isinstance(item, HomeWin):
            if item.message.strip():
                wins.append(item)
            continue
        message = str(item.get("message") or item.get("summary") or "").strip()
        if not message:
            continue
        provider = item.get("source") or item.get("provider") or item.get("label")
        href = item.get("href")
        wins.append(
            HomeWin(
                message=message,
                provider=str(provider) if provider else None,
                href=str(href) if href else None,
            )
        )
    return tuple(wins)


def attention_to_card(item: AttentionPresentation, *, kind: HomeCardKind = "featured") -> HomeCard:
    """Project one AttentionPresentation into a HomeCard (no ranking)."""
    return _from_attention(item, kind=kind)


def _from_attention(item: AttentionPresentation, *, kind: HomeCardKind) -> HomeCard:
    tone = _tone_for_class(item.attention_class)
    secondary_label = None
    secondary_action = None
    # First-success / auth ask: defer is first-class (CP-005). AUTH_BLOCKER only —
    # do not offer Not now on approvals that require an explicit decision.
    if item.attention_class == AttentionClass.AUTH_BLOCKER:
        secondary_label = user_copy.HOME_NOT_NOW_LABEL
        secondary_action = "snooze"
    return HomeCard(
        kind=kind,
        title=item.title,
        body=item.body or "",
        tone=tone,
        eyebrow=None,
        cta_label=item.cta_label,
        cta_url=item.cta_url,
        secondary_label=secondary_label,
        secondary_action=secondary_action,
        attention_id=item.attention_id,
        attention_class=item.attention_class.value,
        provider=item.provider,
    )


def _from_featured(
    featured: HomeFeatured,
    *,
    tone: HomeCardTone,
    kind: HomeCardKind,
    provider: str | None = None,
) -> HomeCard:
    return HomeCard(
        kind=kind,
        title=featured.headline,
        body=featured.body or "",
        tone=tone,
        cta_label=featured.cta_label,
        cta_url=featured.cta_url,
        secondary_label=featured.secondary_label,
        secondary_url=featured.secondary_url,
        disabled_cta_label=featured.disabled_cta_label,
        provider=provider,
    )


def _tone_for_class(cls: AttentionClass) -> HomeCardTone:
    if cls in (
        AttentionClass.AUTH_BLOCKER,
        AttentionClass.AGENT_AUTHORIZATION,
        AttentionClass.TRUST,
        AttentionClass.SYSTEM,
        AttentionClass.ACCESS_DEGRADED,
        AttentionClass.DATA_GAP,
    ):
        return "interrupt"
    if cls in (AttentionClass.VALUE_AT_RISK, AttentionClass.OPPORTUNITY):
        return "opportunity"
    return "neutral"
