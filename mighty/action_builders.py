"""
mighty.action_builders
──────────────────────
Convert legacy dashboard shapes into unified Action objects.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from mighty.action import (
    Action,
    ActionCategory,
    ActionPriority,
    parse_due_date,
    priority_from_urgency,
)


def _is_meaningful_value(value: str) -> bool:
    return value.strip().lower() not in {"available", "active", "yes", "enabled", ""}


def action_from_action_item(item: dict) -> Action:
    btype = str(item.get("btype") or "").strip().lower()
    if btype == "login_required":
        category = ActionCategory.LOGIN_ISSUE
    elif item.get("days_left") is not None or item.get("exp_date"):
        category = ActionCategory.EXPIRING_BENEFIT
    else:
        category = ActionCategory.ALERT

    source = str(item.get("source") or "").strip()
    label = str(item.get("label") or "").strip() or "Needs your attention"
    value = str(item.get("value") or "").strip()
    days_left = item.get("days_left")
    if days_left is not None and not isinstance(days_left, int):
        try:
            days_left = int(days_left)
        except (TypeError, ValueError):
            days_left = None

    return Action(
        id=item.get("id"),
        title=label,
        summary=value,
        priority=priority_from_urgency(str(item.get("urgency") or "")),
        category=category,
        estimated_value=value,
        due_date=parse_due_date(item.get("exp_date")),
        days_until_due=days_left,
        source_accounts=[source] if source else [],
        recommended_next_step="Log in to re-sync" if btype == "login_required" else "",
        benefit_type=btype,
        display_name=source.replace("_", " ").title() if source else "",
    )


def action_from_hero_candidate(candidate: tuple) -> Action | None:
    try:
        score, _exp_sort, display_name, label, value, exp_days, btype = candidate
    except Exception:
        return None

    value_str = str(value or "").strip()
    display = str(display_name or "").strip()
    label_str = str(label or "").strip()
    if not label_str:
        return None

    days_left = exp_days if isinstance(exp_days, int) else None
    category = ActionCategory.DISCOVERY
    priority = (
        ActionPriority.URGENT
        if days_left is not None and days_left <= 7
        else ActionPriority.SOON
        if days_left is not None and days_left <= 30
        else ActionPriority.INFO
    )

    due_date = None
    if days_left is not None and days_left >= 0:
        due_date = date.today().__class__.fromordinal(date.today().toordinal() + days_left)

    return Action(
        title=label_str,
        summary=value_str,
        priority=priority,
        category=category,
        estimated_value=value_str,
        due_date=due_date,
        days_until_due=days_left,
        source_accounts=[display] if display else [],
        display_name=display,
        benefit_type=str(btype or ""),
        score=int(score) if score is not None else None,
    )


def action_from_recommendation(recommendation: Any) -> Action:
    reasoning = str(getattr(recommendation, "rationale", "") or "").strip()
    subcategory = str(getattr(recommendation, "recommendation_type", "") or "general").strip().lower()
    return Action(
        title=str(getattr(recommendation, "title", "") or "").strip(),
        summary=str(getattr(recommendation, "summary", "") or "").strip(),
        priority=ActionPriority.INFO,
        category=ActionCategory.SAVINGS_OPPORTUNITY,
        confidence=str(getattr(recommendation, "confidence", "") or "low").strip().lower() or "low",
        reasoning=reasoning,
        recommended_next_step=str(getattr(recommendation, "action_label", "") or "").strip(),
        action_url=str(getattr(recommendation, "action_url", "") or "").strip(),
        bullets=list(getattr(recommendation, "bullets", None) or []),
        subcategory=subcategory or "general",
    )


def action_from_email_suggestions(count: int) -> Action | None:
    if count <= 0:
        return None
    noun = "account" if count == 1 else "accounts"
    return Action(
        title=f"I found {count} {noun} in your email",
        summary="Review them when you're ready.",
        priority=ActionPriority.INFO,
        category=ActionCategory.DISCOVERY,
        reasoning="Email scan found connectable accounts.",
    )


def build_dashboard_actions(
    *,
    action_items: list[dict] | None = None,
    hero_candidates: list[tuple] | None = None,
    recommendations: list[Any] | None = None,
    email_suggestion_count: int = 0,
) -> list[Action]:
    """Assemble dashboard Actions from shapes produced by app.py today."""
    actions: list[Action] = []

    for item in action_items or []:
        actions.append(action_from_action_item(item))

    seen_hero: set[tuple[str, str]] = set()
    for candidate in hero_candidates or []:
        action = action_from_hero_candidate(candidate)
        if action is None:
            continue
        key = (action.display_name, action.title)
        if key in seen_hero:
            continue
        seen_hero.add(key)
        actions.append(action)

    email_action = action_from_email_suggestions(email_suggestion_count)
    if email_action is not None:
        actions.append(email_action)

    for recommendation in recommendations or []:
        actions.append(action_from_recommendation(recommendation))

    return actions


def recommendation_actions(actions: list[Action]) -> list[Action]:
    """Actions suitable for the recommendations card section (non-demo only)."""
    return [
        action
        for action in actions
        if action.category == ActionCategory.SAVINGS_OPPORTUNITY and not action.is_demo
    ]


def attention_actions(actions: list[Action]) -> list[Action]:
    urgent = [
        action
        for action in actions
        if action.category in {ActionCategory.LOGIN_ISSUE, ActionCategory.EXPIRING_BENEFIT, ActionCategory.ALERT}
        and (
            action.priority == ActionPriority.URGENT
            or action.benefit_type == "login_required"
        )
    ]
    soon = [
        action
        for action in actions
        if action not in urgent
        and action.category in {ActionCategory.LOGIN_ISSUE, ActionCategory.EXPIRING_BENEFIT, ActionCategory.ALERT}
        and (
            action.priority == ActionPriority.SOON
            or action.is_expiring_soon(45)
        )
    ]
    fallback = [
        action
        for action in actions
        if action.category in {ActionCategory.LOGIN_ISSUE, ActionCategory.EXPIRING_BENEFIT, ActionCategory.ALERT}
    ]
    ordered: list[Action] = []
    seen: set[int] = set()
    for bucket in (urgent, soon, fallback):
        for action in bucket:
            action_id = id(action)
            if action_id in seen:
                continue
            seen.add(action_id)
            ordered.append(action)
    return ordered


def discovery_actions(actions: list[Action]) -> list[Action]:
    """Hero-sourced discoveries (mirrors hero_candidates[:3] in the legacy brief)."""
    combined: list[Action] = []
    seen: set[tuple[str, str]] = set()
    for action in actions:
        if action.score is None:
            continue
        key = (action.display_name or action.primary_source(), action.title)
        if key in seen:
            continue
        seen.add(key)
        combined.append(action)
        if len(combined) >= 3:
            break
    return combined


def email_discovery_action(actions: list[Action]) -> Action | None:
    for action in actions:
        title = action.title.lower()
        if title.startswith("i found ") and "email" in title:
            return action
    return None


def savings_actions(actions: list[Action]) -> list[Action]:
    return [
        action
        for action in actions
        if action.category == ActionCategory.SAVINGS_OPPORTUNITY and not action.is_demo
    ]
