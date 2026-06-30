"""
mighty.daily_brief
──────────────────
Composes existing Mighty dashboard data into a narrative Daily Brief.

This module intentionally does not introduce new business logic, schema changes,
AI calls, or database writes. It is an orchestration layer that helps the UI
answer: "What do I need to know?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BriefItem:
    title: str
    detail: str = ""
    tone: str = "neutral"


@dataclass
class BriefInsight:
    title: str
    detail: str = ""
    severity: str = "info"  # warning | info | success


@dataclass
class DailyBrief:
    headline: str
    summary: str
    attention: list[BriefItem] = field(default_factory=list)
    discoveries: list[BriefItem] = field(default_factory=list)
    recommendations: list[BriefItem] = field(default_factory=list)
    completed: list[BriefItem] = field(default_factory=list)
    insights: list[BriefInsight] = field(default_factory=list)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _is_demo_recommendation(recommendation: Any) -> bool:
    return _clean(getattr(recommendation, "rationale", "")).lower() == "demo recommendation."


def _build_insights(
    *,
    action_items: list[dict],
    hero_candidates: list[tuple],
    recommendations: list[Any] | None,
    account_count: int,
    global_sync_label: str,
    email_suggestion_count: int,
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

    urgent_items = [
        i for i in action_items
        if _clean(i.get("urgency")).lower() == "urgent"
        or _clean(i.get("btype")).lower() == "login_required"
    ]
    soon_items = [
        i for i in action_items
        if i not in urgent_items
        and (
            _clean(i.get("urgency")).lower() == "soon"
            or isinstance(i.get("days_left"), int)
            and i.get("days_left") <= 45
        )
    ]

    for item in (urgent_items + soon_items)[:2]:
        label = _clean(item.get("label")) or "Needs your attention"
        value = _clean(item.get("value"))
        source = _clean(item.get("source")).replace("_", " ").title()
        if source and source.lower() not in value.lower():
            detail = f"{source} · {value}" if value else source
        else:
            detail = value
        severity = "warning" if item in urgent_items else "info"
        _add(label, detail, severity)

    for cand in hero_candidates:
        if len(insights) >= 5:
            break
        try:
            _, _, display_name, label, value, exp_days, _btype = cand
        except Exception:
            continue

        detail_parts = []
        if value and str(value).strip().lower() not in {"available", "active", "yes", "enabled"}:
            detail_parts.append(str(value).strip())
        if display_name:
            detail_parts.append(str(display_name).strip())
        if isinstance(exp_days, int) and exp_days >= 0:
            if exp_days == 0:
                detail_parts.append("expires today")
            elif exp_days == 1:
                detail_parts.append("expires tomorrow")
            elif exp_days <= 30:
                detail_parts.append(f"expires in {exp_days} days")
            else:
                detail_parts.append(f"expires in {exp_days} days")

        severity = "warning" if isinstance(exp_days, int) and 0 <= exp_days <= 30 else "info"
        _add(label, " · ".join(detail_parts), severity)

    if email_suggestion_count > 0 and len(insights) < 5:
        _add(
            f"{email_suggestion_count} account{'s' if email_suggestion_count != 1 else ''} found in your email",
            "Ready to connect when you are.",
            "info",
        )

    for recommendation in recommendations or []:
        if len(insights) >= 5 or _is_demo_recommendation(recommendation):
            continue
        summary = _clean(getattr(recommendation, "summary", ""))
        rationale = _clean(getattr(recommendation, "rationale", ""))
        _add(
            _clean(getattr(recommendation, "title", "")),
            summary or rationale,
            "info",
        )

    if not insights and account_count:
        detail = global_sync_label or "Recently synced"
        _add(
            f"Watching {_plural(account_count, 'connected account')}",
            detail,
            "success",
        )
    elif not insights and account_count == 0:
        pass
    elif len(insights) < 5 and account_count and not urgent_items:
        _add("Accounts look current", global_sync_label or "No urgent items", "success")

    return insights[:5]


def build_daily_brief(
    *,
    account_count: int = 0,
    benefit_count: int = 0,
    expiring_count: int = 0,
    global_sync_label: str = "",
    action_items: list[dict] | None = None,
    hero_candidates: list[tuple] | None = None,
    acct_rows: list[Any] | None = None,
    email_suggestion_count: int = 0,
    recommendations: list[Any] | None = None,
) -> DailyBrief:
    """Build a Daily Brief from data already assembled by app.py.

    hero_candidates shape today:
      (priority, exp_sort, display_name, label, value, exp_days, btype)

    action_items shape today:
      dicts with label/value/source/urgency/days_left
    """

    action_items = action_items or []
    hero_candidates = hero_candidates or []
    acct_rows = acct_rows or []

    urgent_items = [
        i for i in action_items
        if _clean(i.get("urgency")).lower() == "urgent"
        or _clean(i.get("btype")).lower() == "login_required"
    ]

    soon_items = [
        i for i in action_items
        if i not in urgent_items
        and (
            _clean(i.get("urgency")).lower() == "soon"
            or isinstance(i.get("days_left"), int)
            and i.get("days_left") <= 45
        )
    ]

    attention_source = (urgent_items + soon_items + action_items)[:3]

    attention: list[BriefItem] = []
    for item in attention_source:
        label = _clean(item.get("label")) or "Needs your attention"
        value = _clean(item.get("value"))
        source = _clean(item.get("source")).replace("_", " ").title()
        if source and source.lower() not in value.lower():
            detail = f"{source} · {value}" if value else source
        else:
            detail = value
        attention.append(BriefItem(title=label, detail=detail, tone="attention"))

    discoveries: list[BriefItem] = []
    seen = set()
    for cand in hero_candidates[:3]:
        try:
            _, _, display_name, label, value, exp_days, _btype = cand
        except Exception:
            continue

        key = (display_name, label)
        if key in seen:
            continue
        seen.add(key)

        title = f"I found {label}"
        detail_parts = []
        if value and str(value).strip().lower() not in {"available", "active", "yes", "enabled"}:
            detail_parts.append(str(value).strip())
        if display_name:
            detail_parts.append(str(display_name).strip())
        if isinstance(exp_days, int) and exp_days >= 0:
            if exp_days == 0:
                detail_parts.append("expires today")
            elif exp_days == 1:
                detail_parts.append("expires tomorrow")
            else:
                detail_parts.append(f"expires in {exp_days} days")

        discoveries.append(
            BriefItem(title=title, detail=" · ".join(detail_parts), tone="discovery")
        )

    if email_suggestion_count > 0 and len(discoveries) < 3:
        discoveries.append(
            BriefItem(
                title=f"I found {_plural(email_suggestion_count, 'account')} in your email",
                detail="Review them when you're ready.",
                tone="discovery",
            )
        )

    completed: list[BriefItem] = []
    checked_count = 0
    for row in acct_rows or []:
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

    recommendation_items: list[BriefItem] = []
    for recommendation in recommendations or []:
        if _is_demo_recommendation(recommendation):
            continue
        summary = _clean(getattr(recommendation, "summary", ""))
        rationale = _clean(getattr(recommendation, "rationale", ""))
        recommendation_items.append(
            BriefItem(
                title=_clean(getattr(recommendation, "title", "")),
                detail=summary or rationale,
                tone="discovery",
            )
        )

    insights = _build_insights(
        action_items=action_items,
        hero_candidates=hero_candidates,
        recommendations=recommendations,
        account_count=account_count,
        global_sync_label=global_sync_label,
        email_suggestion_count=email_suggestion_count,
    )

    return DailyBrief(
        headline=headline,
        summary=" · ".join(summary_bits) + ".",
        attention=attention,
        discoveries=discoveries,
        recommendations=recommendation_items,
        completed=completed[:4],
        insights=insights,
    )
