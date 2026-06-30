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
    badge = _badge_from_texts(value, action.title, action.summary, action.reasoning)
    if badge:
        return badge
    phrase = action.expiry_phrase()
    if phrase:
        return _compact_days_badge(phrase) or phrase.replace("expires", "Expires").capitalize()
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
                value=_badge_from_texts(item.detail or "", item.title),
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
                value=_badge_from_texts(item.detail or "", item.title),
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
                value=_badge_from_texts(ins.detail or "", ins.title),
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
            value="$400 value",
            cta_label="Book with Marriott",
            cta_url="https://www.marriott.com/",
        ),
        PriorityActionItem(
            headline="Activate your Amex $40 dining credit",
            why="Offer expires Friday; unused credits don't roll over to next quarter.",
            urgency="soon",
            value="$40 credit",
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


def _compact_days_badge(text: str) -> str:
    match = re.search(r"(\d+)\s*days?\s*left", text or "", re.I)
    if match:
        return f"{match.group(1)}d"
    return ""


def _badge_from_texts(*texts: str) -> str:
    for text in texts:
        badge = _format_value_badge(text)
        if badge:
            return badge
        badge = _compact_days_badge(text)
        if badge:
            return badge
    return ""


def _format_value_badge(value: str) -> str:
    """Normalize estimated value for a compact badge (e.g. '$400 value')."""
    cleaned = _clean(value)
    if not cleaned:
        return ""
    match = _DOLLAR_RE.search(cleaned)
    if match:
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            amount = None
        if amount is not None:
            if amount >= 1000:
                dollars = f"${amount:,.0f}"
            elif amount == int(amount):
                dollars = f"${int(amount)}"
            else:
                dollars = f"${amount:,.2f}"
            if "credit" in cleaned.lower():
                return f"{dollars} credit"
            if "value" in cleaned.lower() or "~" in cleaned:
                return f"{dollars} value"
            return dollars
    if len(cleaned) > 28:
        return cleaned[:25].rstrip() + "…"
    return cleaned


def _urgency_icon(urgency: str) -> str:
    if urgency == "urgent":
        return (
            '<svg class="dash-brief-urgency-svg" viewBox="0 0 16 16" fill="none" '
            'aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke="currentColor" '
            'stroke-width="1.25"/><path d="M8 4.75v4" stroke="currentColor" '
            'stroke-width="1.5" stroke-linecap="round"/><circle cx="8" cy="11.25" '
            'r=".75" fill="currentColor"/></svg>'
        )
    if urgency == "soon":
        return (
            '<svg class="dash-brief-urgency-svg" viewBox="0 0 16 16" fill="none" '
            'aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke="currentColor" '
            'stroke-width="1.25"/><path d="M8 4.5v3.75l2.25 1.35" stroke="currentColor" '
            'stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        )
    return (
        '<svg class="dash-brief-urgency-svg" viewBox="0 0 16 16" fill="none" '
        'aria-hidden="true"><circle cx="8" cy="8" r="6.5" stroke="currentColor" '
        'stroke-width="1.25"/><circle cx="8" cy="8" r="1.75" fill="currentColor"/></svg>'
    )


def _value_badge_html(value: str, escape: Callable[[Any], str]) -> str:
    badge = _format_value_badge(value)
    if not badge:
        return ""
    return f'<span class="dash-brief-value-badge">{escape(badge)}</span>'


def render_executive_briefing_hero(
    briefing: ExecutiveBriefing,
    *,
    first_name: str,
    today_label: str,
    escape: Callable[[Any], str],
) -> str:
    """Render the executive Daily Brief hero."""

    actions = briefing.priority_actions[:3]
    featured = actions[0] if actions else None
    secondary = actions[1:3]

    def _featured_action(action: PriorityActionItem) -> str:
        href = escape(action.cta_url or "#")
        cta = escape(action.cta_label or "Take action")
        urgency = action.urgency if action.urgency in {"urgent", "soon", "info"} else "info"
        return (
            f'<article class="dash-brief-featured dash-brief-urgency--{urgency}">'
            f'<div class="dash-brief-featured-meta">'
            f'<span class="dash-brief-urgency-icon" aria-hidden="true">'
            f'{_urgency_icon(urgency)}</span>'
            f'{_value_badge_html(action.value, escape)}'
            f'</div>'
            f'<h2 class="dash-brief-featured-headline">{escape(action.headline)}</h2>'
            f'<a href="{href}" class="dash-brief-featured-cta">{cta}</a>'
            f'</article>'
        )

    def _secondary_row(action: PriorityActionItem) -> str:
        href = escape(action.cta_url or "#")
        urgency = action.urgency if action.urgency in {"urgent", "soon", "info"} else "info"
        badge = _value_badge_html(action.value, escape)
        return (
            f'<a href="{href}" class="dash-brief-row dash-brief-urgency--{urgency}">'
            f'<span class="dash-brief-row-icon" aria-hidden="true">'
            f'{_urgency_icon(urgency)}</span>'
            f'<span class="dash-brief-row-body">'
            f'<span class="dash-brief-row-headline">{escape(action.headline)}</span>'
            f'{badge}'
            f'</span>'
            f'<span class="dash-brief-row-arrow" aria-hidden="true">'
            f'<svg viewBox="0 0 16 16" fill="none"><path d="M6 3.5l4.5 4.5L6 12.5" '
            f'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
            f'stroke-linejoin="round"/></svg></span>'
            f'</a>'
        )

    featured_html = _featured_action(featured) if featured else ""
    secondary_html = "".join(_secondary_row(item) for item in secondary)

    metrics = briefing.metrics
    else_items: list[str] = []
    if metrics.accounts_monitored:
        n = metrics.accounts_monitored
        else_items.append(f"{n} account{'s' if n != 1 else ''}")
    if metrics.benefits_tracked:
        n = metrics.benefits_tracked
        else_items.append(f"{n} benefit{'s' if n != 1 else ''}")
    if metrics.total_estimated_value:
        else_items.append(f"{escape(metrics.total_estimated_value)} tracked")
    if metrics.items_needing_attention:
        n = metrics.items_needing_attention
        else_items.append(f"{n} need attention")

    else_html = ""
    if else_items:
        chips = "".join(
            f'<span class="dash-brief-else-chip">{item}</span>' for item in else_items
        )
        else_html = (
            f'<section class="dash-brief-else" aria-label="Overview">'
            f'<p class="dash-brief-else-label">Also</p>'
            f'<div class="dash-brief-else-chips">{chips}</div>'
            f'</section>'
        )

    demo_tag = ""
    if briefing.is_demo:
        demo_tag = '<span class="dash-brief-demo-tag">Demo</span>'

    onboard_html = ""
    if briefing.show_onboard_cta:
        onboard_html = (
            f'<div class="dash-brief-onboard-cta">'
            f'<a href="/email-scan" class="dash-brief-onboard-primary">Connect Gmail</a>'
            f'<a href="/credentials" class="dash-brief-onboard-secondary">Connect manually</a>'
            f'</div>'
        )

    safe_name = escape(first_name)
    secondary_section = ""
    if secondary_html:
        secondary_section = (
            f'<section class="dash-brief-secondary" aria-label="Next priorities">'
            f'{secondary_html}'
            f'</section>'
        )

    return (
        f'<div class="dash-hero">'
        f'<div class="dash-brief-card dash-brief-card--exec">'
        f'<div class="dash-brief-exec">'
        f'<header class="dash-brief-header">'
        f'<h1 class="dash-brief-greeting" id="hero-greeting">Hello, {safe_name}</h1>'
        f'<div class="dash-brief-meta">'
        f'<time class="dash-brief-today-date">{escape(today_label)}</time>'
        f'{demo_tag}'
        f'</div>'
        f'</header>'
        f'<section class="dash-brief-primary" aria-label="Top priority">'
        f'{featured_html}'
        f'</section>'
        f'{secondary_section}'
        f'{onboard_html}'
        f'{else_html}'
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
