"""
mighty.home_projection
──────────────────────
Compose Home V1A daily briefing from existing platform projections.

Presentation composition only. Does not rank Attention, score opportunities,
plan recovery, authorize agents, or invent enrollment / change policy.

See docs/HOME_V1.md.
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
HomeStoryKind = Literal["attention", "opportunity", "all_clear", "empty"]


@dataclass(frozen=True)
class HomeCard:
    """Reusable Home card DTO — visual language for the featured story."""

    kind: HomeCardKind
    title: str
    body: str = ""
    tone: HomeCardTone = "neutral"
    eyebrow: str | None = None
    cta_label: str | None = None
    cta_url: str | None = None
    secondary_label: str | None = None
    secondary_url: str | None = None
    disabled_cta_label: str | None = None
    attention_id: str | None = None
    attention_class: str | None = None


@dataclass(frozen=True)
class HomeWin:
    """One Recent Win line — projected from meaningful account_changes."""

    message: str
    provider: str | None = None
    href: str | None = None


@dataclass(frozen=True)
class HomeOpsNote:
    """Quiet operational footnote — never the featured story."""

    text: str
    href: str | None = None


@dataclass(frozen=True)
class HomeProjection:
    """Pure projection for Home V1A briefing."""

    enrollment_state: HomeState
    first_name: str
    today_label: str
    answer: str
    story_kind: HomeStoryKind
    featured: HomeCard | None
    recent_wins: tuple[HomeWin, ...] = ()
    ops_notes: tuple[HomeOpsNote, ...] = ()
    last_checked: str = ""
    attention_silence: str | None = None
    attention_interrupt: bool = False
    show_truth_debug: bool = False

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
) -> HomeProjection:
    """Compose HomeProjection briefing from enrollment + Attention + wins."""
    attn = attention if use_attention else None
    featured, story_kind = _compose_story(result, attn)
    answer = _compose_answer(result, attn, story_kind)
    silence = (
        attn.silence.value
        if attn is not None and attn.silence is not None
        else None
    )
    interrupt = bool(attn is not None and attn.render_hints.interrupt)
    checked = last_checked or result.freshness_label

    return HomeProjection(
        enrollment_state=result.state,
        first_name=first_name,
        today_label=today_label,
        answer=answer,
        story_kind=story_kind,
        featured=featured,
        recent_wins=_project_wins(recent_wins),
        ops_notes=_compose_ops(result, attn, story_kind),
        last_checked=checked,
        attention_silence=silence,
        attention_interrupt=interrupt,
        show_truth_debug=bool(result.show_access_debug),
    )


def _compose_story(
    result: HomeStateResult,
    attention: AttentionView | None,
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

    # Waiting / Update never own the hero — calm briefing instead.
    return _all_clear_story(result), "all_clear"


def _all_clear_story(result: HomeStateResult) -> HomeCard:
    featured = result.featured
    # Title stays empty — the briefing answer ("You're good.") is the headline.
    health = result.health
    watched = (
        result.metrics_accounts
        or health.up_to_date
        + health.waiting
        + health.needs_login
        + health.needs_attention
    )
    body = user_copy.home_briefing_all_clear_body(watched)
    cta_label = featured.cta_label if featured else user_copy.HOME_VIEW_ACCOUNTS_LABEL
    cta_url = featured.cta_url if featured else "/credentials"
    if result.state in (HomeState.WAITING, HomeState.UPDATE):
        # Soft depth path only — ops strip carries the operational detail.
        cta_label = user_copy.HOME_VIEW_ACCOUNTS_LABEL
        cta_url = "/credentials"
    return HomeCard(
        kind="story",
        title="",
        body=body,
        tone="calm",
        cta_label=cta_label,
        cta_url=cta_url,
    )


def _compose_answer(
    result: HomeStateResult,
    attention: AttentionView | None,
    story_kind: HomeStoryKind,
) -> str:
    if story_kind == "empty":
        return ""
    if story_kind == "attention":
        return user_copy.HOME_BRIEFING_ANSWER_ATTENTION
    if story_kind == "opportunity":
        return user_copy.HOME_BRIEFING_ANSWER_OPPORTUNITY
    return user_copy.HOME_BRIEFING_ANSWER_GOOD


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
    elif result.state == HomeState.WAITING:
        waiting = result.health.waiting or len(result.waiting_rows)
        if waiting:
            notes.append(
                HomeOpsNote(
                    text=user_copy.home_ops_setting_up(waiting),
                    href="/credentials?filter=waiting",
                )
            )

    # Login repair already featured as Attention story — don't duplicate.
    if (
        story_kind != "attention"
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
    return HomeCard(
        kind=kind,
        title=item.title,
        body=item.body or "",
        tone=tone,
        eyebrow=None,
        cta_label=item.cta_label,
        cta_url=item.cta_url,
        attention_id=item.attention_id,
        attention_class=item.attention_class.value,
    )


def _from_featured(
    featured: HomeFeatured,
    *,
    tone: HomeCardTone,
    kind: HomeCardKind,
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
