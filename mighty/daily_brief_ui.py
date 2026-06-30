"""
mighty.daily_brief_ui
─────────────────────
Executive two-column Daily Brief hero — data composition and HTML rendering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from mighty.action import Action, ActionCategory, ActionPriority
from mighty.action_builders import attention_actions, savings_actions
from mighty.daily_brief import DailyBrief


@dataclass
class PriorityActionItem:
    headline: str
    why: str
    urgency: str = "info"  # urgent | soon | info
    value: str = ""
    cta_label: str = ""
    cta_url: str = ""


@dataclass
class BriefMetrics:
    accounts_monitored: int = 0
    benefits_tracked: int = 0
    total_estimated_value: str = ""
    items_needing_attention: int = 0


@dataclass
class ExecutiveBriefing:
    priority_summary: str
    priority_actions: list[PriorityActionItem] = field(default_factory=list)
    metrics: BriefMetrics = field(default_factory=BriefMetrics)
    is_demo: bool = False
    show_onboard_cta: bool = False


_DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _is_meaningful_value(value: str) -> bool:
    return value.strip().lower() not in {"available", "active", "yes", "enabled", ""}


def _urgency_from_action(action: Action) -> str:
    if action.priority == ActionPriority.URGENT or action.category == ActionCategory.LOGIN_ISSUE:
        return "urgent"
    if action.priority == ActionPriority.SOON or action.is_expiring_soon(30):
        return "soon"
    return "info"


def _why_for_action(action: Action) -> str:
    reasoning = _clean(action.reasoning)
    if reasoning and reasoning.lower() != "demo recommendation.":
        return reasoning
    summary = _clean(action.summary)
    if summary and summary.lower() not in {action.title.lower()}:
        return summary
    phrase = action.expiry_phrase()
    if phrase:
        source = action.display_name or action.source_display()
        if source:
            return f"{source} · {phrase.replace('expires', 'Expires')}"
        return phrase.replace("expires", "Expires").capitalize()
    source = action.display_name or action.source_display()
    if source:
        return f"Surfaced from {source} during your latest sync."
    return "Worth a few minutes before it slips off your radar."


def _value_for_action(action: Action) -> str:
    value = _clean(action.estimated_value)
    if value and _is_meaningful_value(value):
        if "$" in value or "credit" in value.lower() or "night" in value.lower():
            return value
    phrase = action.expiry_phrase()
    if phrase:
        return phrase.replace("expires", "Expires").capitalize()
    return ""


def _cta_for_action(action: Action) -> tuple[str, str]:
    label = _clean(action.recommended_next_step or action.action_label)
    url = _clean(action.action_url)
    if label and url:
        return label, url
    if action.category == ActionCategory.LOGIN_ISSUE:
        return "Log in to re-sync", "/credentials"
    if action.category == ActionCategory.EXPIRING_BENEFIT:
        return "Use before expiry", url or "#accounts"
    if action.category == ActionCategory.SAVINGS_OPPORTUNITY:
        return label or "View opportunity", url or "#recommendations"
    if action.category == ActionCategory.DISCOVERY:
        return "Review discovery", url or "#accounts"
    if "email" in action.title.lower():
        return "Review accounts", "/email-scan"
    return label or "Take action", url or "#accounts"


def _action_to_priority(action: Action) -> PriorityActionItem:
    cta_label, cta_url = _cta_for_action(action)
    return PriorityActionItem(
        headline=action.title,
        why=_why_for_action(action),
        urgency=_urgency_from_action(action),
        value=_value_for_action(action),
        cta_label=cta_label,
        cta_url=cta_url,
    )


def _priority_actions_from_brief(brief: DailyBrief) -> list[PriorityActionItem]:
    actions = list(getattr(brief, "actions", None) or [])
    if actions:
        ordered: list[Action] = []
        seen: set[int] = set()
        for bucket in (
            attention_actions(actions),
            [a for a in actions if a.score is not None and a.is_expiring_soon(45)],
            savings_actions(actions),
            [a for a in actions if a.score is not None],
        ):
            for action in bucket:
                aid = id(action)
                if aid in seen:
                    continue
                seen.add(aid)
                ordered.append(action)
        return [_action_to_priority(action) for action in ordered[:3]]

    items: list[PriorityActionItem] = []
    for item in getattr(brief, "attention", None) or []:
        items.append(
            PriorityActionItem(
                headline=item.title,
                why=item.detail or "Needs your attention soon.",
                urgency="urgent",
                value=item.detail if "$" in (item.detail or "") else "",
                cta_label="Take action",
                cta_url="#accounts",
            )
        )
    for item in getattr(brief, "recommendations", None) or []:
        if len(items) >= 3:
            break
        items.append(
            PriorityActionItem(
                headline=item.title,
                why=item.detail or "A strong fit for your upcoming plans.",
                urgency="info",
                value=item.detail if "$" in (item.detail or "") else "",
                cta_label="View opportunity",
                cta_url="#recommendations",
            )
        )
    for ins in getattr(brief, "insights", None) or []:
        if len(items) >= 3:
            break
        urgency = "urgent" if ins.severity == "warning" else "info"
        items.append(
            PriorityActionItem(
                headline=ins.title,
                why=ins.detail or "Surfaced in today's brief.",
                urgency=urgency,
                value=ins.detail if "$" in (ins.detail or "") else "",
                cta_label="Review",
                cta_url="#accounts",
            )
        )
    return items[:3]


def _sum_dollar_values(texts: list[str]) -> str:
    total = 0.0
    found = False
    for text in texts:
        for match in _DOLLAR_RE.finditer(text or ""):
            found = True
            try:
                total += float(match.group(1).replace(",", ""))
            except ValueError:
                pass
    if not found:
        return ""
    if total >= 1000:
        return f"${total:,.0f}"
    if total == int(total):
        return f"${int(total)}"
    return f"${total:,.2f}"


def _demo_priority_actions() -> list[PriorityActionItem]:
    return [
        PriorityActionItem(
            headline="Use your Marriott free night before Tokyo",
            why="Certificate expires 14 days before your departure — book now or lose a full night.",
            urgency="urgent",
            value="1 free night · ~$400 value",
            cta_label="Book with Marriott",
            cta_url="https://www.marriott.com/",
        ),
        PriorityActionItem(
            headline="Activate your Amex $40 dining credit",
            why="Offer expires Friday; unused credits don't roll over to next quarter.",
            urgency="soon",
            value="$40 dining credit",
            cta_label="View Amex offers",
            cta_url="#accounts",
        ),
        PriorityActionItem(
            headline="Apply Delta upgrade cert on SFO→NRT",
            why="Regional upgrade expires before your long-haul segment — best used on the Tokyo flight.",
            urgency="soon",
            value="1 upgrade certificate",
            cta_label="View Delta trips",
            cta_url="https://www.delta.com/myprofile/",
        ),
    ]


def _demo_metrics() -> BriefMetrics:
    return BriefMetrics(
        accounts_monitored=5,
        benefits_tracked=12,
        total_estimated_value="$1,240",
        items_needing_attention=4,
    )


def build_executive_briefing(
    brief: DailyBrief | None,
    *,
    account_count: int = 0,
    benefit_count: int = 0,
    expiring_count: int = 0,
    use_demo_when_empty: bool = True,
) -> ExecutiveBriefing:
    """Compose executive briefing data from a DailyBrief and dashboard counts."""
    priority_actions = _priority_actions_from_brief(brief) if brief else []
    attention_count = len(getattr(brief, "attention", None) or []) if brief else 0
    items_needing_attention = attention_count or expiring_count

    value_sources: list[str] = []
    if brief:
        for action in getattr(brief, "actions", None) or []:
            value_sources.append(action.estimated_value or action.summary or action.reasoning)
        for ins in getattr(brief, "insights", None) or []:
            value_sources.append(ins.detail or ins.title)
    total_value = _sum_dollar_values(value_sources)

    metrics = BriefMetrics(
        accounts_monitored=account_count,
        benefits_tracked=benefit_count,
        total_estimated_value=total_value,
        items_needing_attention=items_needing_attention,
    )

    has_real_content = bool(priority_actions) and (account_count > 0 or attention_count > 0)
    show_onboard = account_count == 0 and not priority_actions

    if not priority_actions and use_demo_when_empty:
        priority_actions = _demo_priority_actions()
        if show_onboard:
            metrics = _demo_metrics()
        elif not total_value:
            metrics.total_estimated_value = _demo_metrics().total_estimated_value

    if priority_actions and not metrics.total_estimated_value and use_demo_when_empty:
        metrics.total_estimated_value = _sum_dollar_values(
            [a.value for a in priority_actions] + [a.why for a in priority_actions]
        ) or _demo_metrics().total_estimated_value

    count = max(items_needing_attention, len(priority_actions)) if has_real_content else len(priority_actions)
    if count == 0:
        count = len(priority_actions)
    if count == 1:
        priority_summary = "You have 1 thing worth your attention today."
    elif count > 1:
        priority_summary = f"You have {count} things worth your attention today."
    else:
        priority_summary = "Your accounts look current — nothing urgent today."

    return ExecutiveBriefing(
        priority_summary=priority_summary,
        priority_actions=priority_actions[:3],
        metrics=metrics,
        is_demo=show_onboard or (use_demo_when_empty and not has_real_content),
        show_onboard_cta=show_onboard,
    )


def render_executive_briefing_hero(
    briefing: ExecutiveBriefing,
    *,
    first_name: str,
    today_label: str,
    escape: Callable[[Any], str],
) -> str:
    """Render the two-column executive Daily Brief hero."""

    def _render_priority(action: PriorityActionItem) -> str:
        value_html = (
            f'<div class="dash-brief-priority-value">{escape(action.value)}</div>'
            if action.value else ""
        )
        cta_html = ""
        if action.cta_label:
            href = escape(action.cta_url or "#")
            cta_html = (
                f'<a href="{href}" class="dash-brief-priority-cta">'
                f'{escape(action.cta_label)}</a>'
            )
        return (
            f'<article class="dash-brief-priority-item dash-brief-priority-item--{escape(action.urgency)}">'
            f'<span class="dash-brief-priority-dot" aria-hidden="true"></span>'
            f'<div class="dash-brief-priority-body">'
            f'<h3 class="dash-brief-priority-headline">{escape(action.headline)}</h3>'
            f'<p class="dash-brief-priority-why">{escape(action.why)}</p>'
            f'{value_html}'
            f'{cta_html}'
            f'</div>'
            f'</article>'
        )

    priorities_html = "".join(_render_priority(item) for item in briefing.priority_actions[:3])

    def _metric_card(label: str, value: str) -> str:
        return (
            f'<div class="dash-brief-metric">'
            f'<div class="dash-brief-metric-val">{escape(value)}</div>'
            f'<div class="dash-brief-metric-lbl">{escape(label)}</div>'
            f'</div>'
        )

    metrics = briefing.metrics
    metrics_html = (
        _metric_card("Accounts monitored", str(metrics.accounts_monitored))
        + _metric_card("Benefits tracked", str(metrics.benefits_tracked))
        + _metric_card("Total estimated value found", metrics.total_estimated_value or "—")
        + _metric_card("Items needing attention", str(metrics.items_needing_attention))
    )

    demo_tag = ""
    if briefing.is_demo:
        demo_tag = (
            '<span class="dash-brief-demo-tag">Demo data</span>'
        )

    onboard_html = ""
    if briefing.show_onboard_cta:
        onboard_html = (
            f'<div class="dash-brief-onboard-cta">'
            f'<p class="dash-brief-onboard-note">Connect Gmail to replace sample data with your accounts.</p>'
            f'<div class="dash-brief-onboard-actions">'
            f'<a href="/email-scan" class="dash-brief-onboard-primary">Connect Gmail</a>'
            f'<a href="/credentials" class="dash-brief-onboard-secondary">Connect manually</a>'
            f'</div>'
            f'</div>'
        )

    safe_name = escape(first_name)

    return (
        f'<div class="dash-hero">'
        f'<div class="dash-brief-card dash-brief-card--exec">'
        f'<div class="dash-brief-exec">'
        f'<div class="dash-brief-exec-left">'
        f'<div class="dash-brief-greeting" id="hero-greeting">Hello, {safe_name}</div>'
        f'<div class="dash-brief-today">'
        f'<time class="dash-brief-today-date">{escape(today_label)}</time>'
        f'{demo_tag}'
        f'</div>'
        f'<p class="dash-brief-priority-summary">{escape(briefing.priority_summary)}</p>'
        f'<div class="dash-brief-priorities">{priorities_html}</div>'
        f'{onboard_html}'
        f'</div>'
        f'<div class="dash-brief-exec-right">'
        f'<div class="dash-brief-metrics-head">Summary</div>'
        f'<div class="dash-brief-metrics">{metrics_html}</div>'
        f'</div>'
        f'</div>'
        f'</div>'
        f'<script>'
        f'(function(){{'
        f'  var h=new Date().getHours();'
        f'  var g=h<12?"Good morning":h<17?"Good afternoon":"Good evening";'
        f'  var el=document.getElementById("hero-greeting");'
        f'  if(el) el.textContent=g+", {safe_name}";'
        f'}})();'
        f'</script>'
        f'</div>'
    )
