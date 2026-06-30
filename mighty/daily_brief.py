"""
mighty.daily_brief
──────────────────
Composes Mighty dashboard Actions into a narrative Daily Brief.

This module intentionally does not introduce new business logic, schema changes,
AI calls, or database writes. It is an orchestration layer that helps the UI
answer: "What do I need to know?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mighty.action import Action, ActionCategory, ActionPriority
from mighty.action_builders import (
    attention_actions,
    build_dashboard_actions,
    discovery_actions,
    email_discovery_action,
    savings_actions,
)


@dataclass
class BriefItem:
    title: str
    detail: str = ""
    tone: str = "neutral"


@dataclass
class BriefInsight:
    title: str
    detail: str = ""
    severity: str = "info"  # warning | info | success | opportunity


@dataclass
class DailyBrief:
    headline: str
    summary: str
    attention: list[BriefItem] = field(default_factory=list)
    discoveries: list[BriefItem] = field(default_factory=list)
    recommendations: list[BriefItem] = field(default_factory=list)
    completed: list[BriefItem] = field(default_factory=list)
    insights: list[BriefInsight] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _action_to_brief_item(action: Action, *, tone: str = "neutral", title: str | None = None) -> BriefItem:
    return BriefItem(
        title=title or action.title,
        detail=action.detail_line(),
        tone=tone,
    )


def _discovery_title(action: Action) -> str:
    title = action.title.strip()
    if title.lower().startswith("i found "):
        return title
    return f"I found {title}"


def _discovery_detail(action: Action) -> str:
    parts: list[str] = []
    value = (action.estimated_value or action.summary or "").strip()
    if value and _is_meaningful_value(value):
        parts.append(value)
    if action.display_name:
        parts.append(action.display_name)
    phrase = action.expiry_phrase()
    if phrase:
        parts.append(phrase)
    return " · ".join(parts)


def _is_meaningful_value(value: str) -> bool:
    return value.strip().lower() not in {"available", "active", "yes", "enabled", ""}


def _build_insights_from_actions(
    *,
    actions: list[Action],
    account_count: int,
    global_sync_label: str,
) -> list[BriefInsight]:
    """Compose 3–5 executive-style insights with severity indicators."""
    insights: list[BriefInsight] = []
    seen_titles: set[str] = set()

    def _add(title: str, detail: str = "", severity: str = "info") -> None:
        title = _clean(title)
        if not title or title in seen_titles or len(insights) >= 5:
            return
        seen_titles.add(title)
        insights.append(BriefInsight(title=title, detail=_clean(detail), severity=severity))

    attention = attention_actions(actions)
    urgent = [
        action
        for action in attention
        if action.priority == ActionPriority.URGENT or action.category == ActionCategory.LOGIN_ISSUE
    ]
    soon = [
        action
        for action in attention
        if action not in urgent
        and (
            action.priority == ActionPriority.SOON
            or action.is_expiring_soon(45)
        )
    ]

    for action in (urgent + soon)[:2]:
        severity = "warning" if action in urgent else "info"
        _add(action.title, action.detail_line(), severity)

    hero_like = [
        action
        for action in actions
        if action.score is not None or action.category == ActionCategory.DISCOVERY
    ]
    for action in hero_like:
        if len(insights) >= 5:
            break
        parts: list[str] = []
        value = (action.estimated_value or action.summary or "").strip()
        if value and _is_meaningful_value(value):
            parts.append(value)
        if action.display_name:
            parts.append(action.display_name)
        phrase = action.expiry_phrase()
        if phrase:
            parts.append(phrase)
        severity = "warning" if action.is_expiring_soon(30) else "info"
        _add(action.title, " · ".join(parts), severity)

    for action in actions:
        if len(insights) >= 5:
            break
        if action.title.lower().startswith("i found ") and "email" in action.title.lower():
            _add(action.title, action.summary or "Ready to connect when you are.", "info")
            break

    for action in savings_actions(actions):
        if len(insights) >= 5:
            break
        _add(action.title, action.summary or action.reasoning, "info")

    if not insights and account_count:
        detail = global_sync_label or "Recently synced"
        _add(
            f"Watching {_plural(account_count, 'connected account')}",
            detail,
            "success",
        )
    elif not insights and account_count == 0:
        pass
    elif len(insights) < 5 and account_count and not urgent:
        _add("Accounts look current", global_sync_label or "No urgent items", "success")

    return insights[:5]


def build_daily_brief(
    *,
    actions: list[Action] | None = None,
    account_count: int = 0,
    benefit_count: int = 0,
    expiring_count: int = 0,
    global_sync_label: str = "",
    acct_rows: list[Any] | None = None,
    # Legacy shapes — converted to Actions when ``actions`` is omitted.
    action_items: list[dict] | None = None,
    hero_candidates: list[tuple] | None = None,
    email_suggestion_count: int = 0,
    recommendations: list[Any] | None = None,
) -> DailyBrief:
    """Build a Daily Brief from unified Actions (preferred) or legacy dashboard shapes."""
    if actions is None:
        actions = build_dashboard_actions(
            action_items=action_items,
            hero_candidates=hero_candidates,
            recommendations=recommendations,
            email_suggestion_count=email_suggestion_count,
        )

    acct_rows = acct_rows or []
    attention_source = attention_actions(actions)[:3]

    attention = [
        _action_to_brief_item(action, tone="attention")
        for action in attention_source
    ]

    discoveries: list[BriefItem] = []
    for action in discovery_actions(actions):
        discoveries.append(
            BriefItem(
                title=_discovery_title(action),
                detail=_discovery_detail(action),
                tone="discovery",
            )
        )

    email_action = email_discovery_action(actions)
    if email_action is not None and len(discoveries) < 3:
        discoveries.append(
            BriefItem(
                title=email_action.title,
                detail=email_action.summary or "Review them when you're ready.",
                tone="discovery",
            )
        )

    completed: list[BriefItem] = []
    checked_count = 0
    for row in acct_rows:
        try:
            if row["synced_at"]:
                checked_count += 1
        except Exception:
            pass

    if checked_count:
        completed.append(
            BriefItem(
                title=f"Checked {_plural(checked_count, 'connected service')}",
                detail=global_sync_label or "Recently",
                tone="completed",
            )
        )

    if account_count:
        completed.append(
            BriefItem(
                title=f"Watching {_plural(account_count, 'service')}",
                detail="I'll surface anything that needs you.",
                tone="completed",
            )
        )

    if not completed:
        completed.append(
            BriefItem(
                title="Ready to start watching",
                detail="Connect email or an account and I'll begin looking for things that matter.",
                tone="completed",
            )
        )

    if attention:
        headline = f"{_plural(len(attention), 'thing')} need your attention."
    elif discoveries:
        headline = f"I found {_plural(len(discoveries), 'thing')} worth a look."
    elif account_count:
        headline = "Everything looks good."
    else:
        headline = "I'm ready to start watching."

    if account_count:
        checked_phrase = f"I checked {_plural(account_count, 'connected service')}"
        if global_sync_label:
            checked_phrase += f" · {global_sync_label}"
        summary_bits = [checked_phrase]
    else:
        summary_bits = ["Connect email or an account to get your first brief"]

    if attention:
        summary_bits.append(f"{_plural(len(attention), 'item')} need you")
    if discoveries:
        summary_bits.append(f"{_plural(len(discoveries), 'discovery', 'discoveries')} found")
    if expiring_count:
        summary_bits.append(f"{_plural(expiring_count, 'item')} expiring soon")

    recommendation_items = [
        BriefItem(
            title=action.title,
            detail=action.summary or action.reasoning,
            tone="discovery",
        )
        for action in savings_actions(actions)
    ]

    insights = _build_insights_from_actions(
        actions=actions,
        account_count=account_count,
        global_sync_label=global_sync_label,
    )

    return DailyBrief(
        headline=headline,
        summary=" · ".join(summary_bits) + ".",
        attention=attention,
        discoveries=discoveries,
        recommendations=recommendation_items,
        completed=completed[:4],
        insights=insights,
        actions=actions,
    )
