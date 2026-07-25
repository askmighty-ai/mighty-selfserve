"""AttentionView — surface window over AttentionState (Milestone 3).

Windows an already-ranked AttentionState for a product surface and resolves
customer English + CTA URLs. Contains no ranking, silence, overlay, or
producer policy.

See docs/ATTENTION_VIEW.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from mighty.attention import (
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionUrgency,
)
from mighty.attention_state import AttentionState, SilenceVerdict
from mighty import user_copy

AttentionSurface = Literal["home", "accounts", "activity", "worker", "push", "email"]

ATTENTION_VIEW_SCHEMA_VERSION = 1

_DEFAULT_PROVIDER_NAMES: dict[str, str] = {
    "amex": "American Express",
    "delta": "Delta",
    "united": "United",
    "marriott": "Marriott",
    "hilton": "Hilton",
}


@dataclass(frozen=True)
class AttentionCounts:
    """Compact counts for chips / badges (derived from ranked state)."""

    blockers: int
    time_sensitive: int
    opportunities: int
    informational: int
    total_visible: int

    def to_dict(self) -> dict[str, int]:
        return {
            "blockers": self.blockers,
            "time_sensitive": self.time_sensitive,
            "opportunities": self.opportunities,
            "informational": self.informational,
            "total_visible": self.total_visible,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AttentionCounts:
        return cls(
            blockers=int(payload.get("blockers") or 0),
            time_sensitive=int(payload.get("time_sensitive") or 0),
            opportunities=int(payload.get("opportunities") or 0),
            informational=int(payload.get("informational") or 0),
            total_visible=int(payload.get("total_visible") or 0),
        )


@dataclass(frozen=True)
class AttentionRenderHints:
    """Surface-local presentation flags — not product policy."""

    show_primary: bool
    show_silence: bool
    interrupt: bool
    secondary_limit: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "show_primary": self.show_primary,
            "show_silence": self.show_silence,
            "interrupt": self.interrupt,
            "secondary_limit": self.secondary_limit,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AttentionRenderHints:
        return cls(
            show_primary=bool(payload.get("show_primary")),
            show_silence=bool(payload.get("show_silence")),
            interrupt=bool(payload.get("interrupt")),
            secondary_limit=int(payload.get("secondary_limit") or 0),
        )


@dataclass(frozen=True)
class AttentionPresentation:
    """Resolved customer-facing projection of one AttentionItem."""

    attention_id: str
    attention_class: AttentionClass
    urgency: AttentionUrgency
    provider: str | None
    reason_code: str
    cta_key: AttentionCtaKey
    title: str
    body: str
    cta_label: str | None
    cta_url: str | None
    interruption_expected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "attention_id": self.attention_id,
            "attention_class": self.attention_class.value,
            "urgency": self.urgency.value,
            "provider": self.provider,
            "reason_code": self.reason_code,
            "cta_key": self.cta_key.value,
            "title": self.title,
            "body": self.body,
            "cta_label": self.cta_label,
            "cta_url": self.cta_url,
            "interruption_expected": self.interruption_expected,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AttentionPresentation:
        return cls(
            attention_id=str(payload.get("attention_id") or ""),
            attention_class=AttentionClass(str(payload.get("attention_class"))),
            urgency=AttentionUrgency(str(payload.get("urgency"))),
            provider=payload.get("provider"),
            reason_code=str(payload.get("reason_code") or ""),
            cta_key=AttentionCtaKey(str(payload.get("cta_key"))),
            title=str(payload.get("title") or ""),
            body=str(payload.get("body") or ""),
            cta_label=payload.get("cta_label"),
            cta_url=payload.get("cta_url"),
            interruption_expected=bool(payload.get("interruption_expected", False)),
        )


@dataclass(frozen=True)
class AttentionView:
    """Surface window over AttentionState (RFC §4.1 / §8)."""

    schema_version: int
    surface: AttentionSurface
    primary: AttentionPresentation | None
    secondary: tuple[AttentionPresentation, ...]
    health_counts: AttentionCounts
    silence: SilenceVerdict | None
    render_hints: AttentionRenderHints

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "surface": self.surface,
            "primary": self.primary.to_dict() if self.primary is not None else None,
            "secondary": [item.to_dict() for item in self.secondary],
            "health_counts": self.health_counts.to_dict(),
            "silence": self.silence.value if self.silence is not None else None,
            "render_hints": self.render_hints.to_dict(),
        }


def build_attention_view(
    state: AttentionState,
    *,
    surface: AttentionSurface,
    provider_display_names: Mapping[str, str] | None = None,
    provider_open_urls: Mapping[str, str] | None = None,
    secondary_limit: int | None = None,
) -> AttentionView:
    """Window AttentionState for a surface. Does not re-rank."""
    names = dict(_DEFAULT_PROVIDER_NAMES)
    if provider_display_names:
        names.update({str(k).lower(): str(v) for k, v in provider_display_names.items()})
    urls = {
        str(k).lower(): str(v)
        for k, v in (provider_open_urls or {}).items()
        if v
    }
    limit = _secondary_limit_for(surface) if secondary_limit is None else max(0, int(secondary_limit))

    visible = _visible_items(state)
    # Activity filters to authorize-sourced items without changing global order
    # (RFC §4.6). Home primary attention_id remains product primary even if
    # Activity's visible top differs.
    if surface == "activity":
        window_items = tuple(
            item for item in visible if item.attention_class == AttentionClass.AGENT_AUTHORIZATION
        )
        primary_item = window_items[0] if window_items else None
        secondary_items = window_items[1 : 1 + limit]
    else:
        primary_item = state.primary
        remaining = list(state.remaining)
        secondary_items = tuple(remaining[:limit])

    primary = (
        _present(primary_item, names=names, urls=urls)
        if primary_item is not None
        else None
    )
    secondary = tuple(
        _present(item, names=names, urls=urls) for item in secondary_items
    )
    counts = _counts(visible)
    # silence=None means effective ranks 1–5 are visible (RFC §7).
    interrupt = state.silence is None and primary_item is not None
    hints = AttentionRenderHints(
        show_primary=primary is not None,
        show_silence=state.silence is not None,
        interrupt=bool(interrupt),
        secondary_limit=limit,
    )
    return AttentionView(
        schema_version=ATTENTION_VIEW_SCHEMA_VERSION,
        surface=surface,
        primary=primary,
        secondary=secondary,
        health_counts=counts,
        silence=state.silence,
        render_hints=hints,
    )


def resolve_attention_copy(
    item: AttentionItem,
    *,
    provider_display_names: Mapping[str, str] | None = None,
) -> tuple[str, str, str | None]:
    """Resolve title, body, cta_label for an AttentionItem (no URL)."""
    names = dict(_DEFAULT_PROVIDER_NAMES)
    if provider_display_names:
        names.update({str(k).lower(): str(v) for k, v in provider_display_names.items()})
    provider_name = _provider_name(item.provider, names)
    return _copy_for(item, provider_name)


def resolve_attention_cta_url(
    item: AttentionItem,
    *,
    provider_open_urls: Mapping[str, str] | None = None,
) -> str | None:
    """Map cta_key → URL. Surfaces must not invent alternate destinations."""
    urls = {
        str(k).lower(): str(v)
        for k, v in (provider_open_urls or {}).items()
        if v
    }
    return _cta_url(item, urls)


def _visible_items(state: AttentionState) -> tuple[AttentionItem, ...]:
    if state.primary is None:
        return tuple(state.remaining)
    return (state.primary,) + tuple(state.remaining)


def _counts(items: Sequence[AttentionItem]) -> AttentionCounts:
    blockers = time_sensitive = opportunities = informational = 0
    for item in items:
        if item.urgency == AttentionUrgency.BLOCKER:
            blockers += 1
        elif item.urgency == AttentionUrgency.TIME_SENSITIVE:
            time_sensitive += 1
        elif item.urgency == AttentionUrgency.OPPORTUNITY:
            opportunities += 1
        else:
            informational += 1
    return AttentionCounts(
        blockers=blockers,
        time_sensitive=time_sensitive,
        opportunities=opportunities,
        informational=informational,
        total_visible=len(items),
    )


def _secondary_limit_for(surface: AttentionSurface) -> int:
    if surface == "home":
        return 2
    if surface == "activity":
        return 20
    if surface == "worker":
        return 0
    if surface in ("push", "email"):
        return 0
    return 3


def _present(
    item: AttentionItem,
    *,
    names: Mapping[str, str],
    urls: Mapping[str, str],
) -> AttentionPresentation:
    provider_name = _provider_name(item.provider, names)
    title, body, cta_label = _copy_for(item, provider_name)
    return AttentionPresentation(
        attention_id=item.attention_id,
        attention_class=item.attention_class,
        urgency=item.urgency,
        provider=item.provider,
        reason_code=item.reason.code,
        cta_key=item.cta_key,
        title=title,
        body=body,
        cta_label=cta_label,
        cta_url=_cta_url(item, urls),
        interruption_expected=bool(item.interruption_expected),
    )


def _provider_name(provider: str | None, names: Mapping[str, str]) -> str:
    if not provider:
        return "your account"
    key = str(provider).strip().lower()
    return names.get(key) or provider.replace("_", " ").title()


def _benefit_topic(item: AttentionItem) -> str | None:
    """Humanize benefit fingerprint field_key for concrete opportunity copy."""
    fingerprint = (item.fingerprint or "").strip()
    if not fingerprint.startswith("benefit:"):
        return None
    parts = fingerprint.split(":", 2)
    if len(parts) < 3:
        return None
    topic = parts[2].replace("_", " ").strip()
    return topic or None


def _copy_for(item: AttentionItem, provider_name: str) -> tuple[str, str, str | None]:
    cls = item.attention_class
    reason = item.reason.code

    if cls == AttentionClass.AUTH_BLOCKER:
        title = user_copy.home_login_headline(provider_name)
        if reason == "mfa":
            body = (
                f"This is the only step we can't complete for you. "
                f"Finish the {provider_name} sign-in challenge, and we'll take care of the rest."
            )
            cta = f"Continue {provider_name} login"
        elif reason == "captcha":
            body = (
                f"This is the only step we can't complete for you. "
                f"Complete the {provider_name} security check, and we'll take care of the rest."
            )
            cta = f"Open {provider_name}"
        elif reason == "consent":
            body = (
                f"This is the only step we can't complete for you. "
                f"Approve access for {provider_name}, and we'll take care of the rest."
            )
            cta = f"Open {provider_name}"
        else:
            body = user_copy.home_login_body(provider_name)
            cta = user_copy.home_login_cta(provider_name)
        return title, body, cta

    if cls == AttentionClass.AGENT_AUTHORIZATION:
        return (
            "Approval needed",
            "Mighty is waiting for your approval before continuing.",
            "Review approval",
        )

    if cls == AttentionClass.ACCESS_DEGRADED:
        if reason == "stale":
            title = f"{provider_name} access may be stale"
            body = (
                f"Mighty has not confirmed a fresh session for {provider_name}. "
                "Open the account in Chrome while logged in to refresh."
            )
        else:
            title = f"Checking {provider_name} access"
            body = (
                f"Mighty is not yet sure about {provider_name} access. "
                "Open the account in Chrome if you want to verify now."
            )
        return title, body, user_copy.home_open_provider_cta(provider_name)

    if cls == AttentionClass.SYSTEM:
        if reason == "worker_unreachable":
            return (
                "Mighty in Chrome needs a refresh",
                "Open Chrome with the Chrome extension so account watching can continue.",
                "Open Mighty in Chrome setup",
            )
        return (
            "Mighty needs a quick setup step",
            "Set up Mighty in Chrome so Mighty can keep watching your accounts.",
            "Set up Mighty in Chrome",
        )

    if cls == AttentionClass.TRUST:
        if reason == "awaiting_user":
            return (
                f"{provider_name} needs a quick confirmation",
                f"Mighty's Runtime is waiting on you for {provider_name}.",
                f"Open {provider_name}",
            )
        if reason in {"runtime_offline", "never_reported"}:
            return (
                f"{provider_name} Runtime is unavailable",
                f"Mighty cannot confirm autonomous access for {provider_name}.",
                f"Open {provider_name}",
            )
        if reason == "stale":
            return (
                f"{provider_name} Runtime status is stale",
                f"Mighty has not received a fresh Runtime update for {provider_name}.",
                f"Open {provider_name}",
            )
        return (
            "Trust check required",
            "Mighty needs you to confirm something before continuing.",
            "Review",
        )

    if cls == AttentionClass.VALUE_AT_RISK:
        topic = _benefit_topic(item)
        return (
            user_copy.home_value_at_risk_headline(provider_name, topic),
            user_copy.home_value_at_risk_body(provider_name, topic),
            "Review benefit",
        )

    if cls == AttentionClass.OPPORTUNITY:
        topic = _benefit_topic(item)
        return (
            user_copy.home_opportunity_headline(provider_name, topic),
            user_copy.home_opportunity_body(provider_name, topic),
            "See the benefit",
        )

    if cls == AttentionClass.DATA_GAP:
        return (
            f"Waiting on {provider_name} data",
            f"Visit {provider_name} in Chrome while logged in so Mighty can capture data.",
            user_copy.home_open_provider_cta(provider_name),
        )

    return ("Needs attention", "Something needs your attention.", "Open Mighty")


def _cta_url(item: AttentionItem, urls: Mapping[str, str]) -> str | None:
    key = item.cta_key
    provider = (item.provider or "").strip().lower()
    provider_url = urls.get(provider) if provider else None

    if key == AttentionCtaKey.START_PROVIDER_LOGIN:
        return provider_url or "/credentials"
    if key == AttentionCtaKey.OPEN_PROVIDER_SURFACE:
        return provider_url or "/credentials"
    if key == AttentionCtaKey.OPEN_ACCOUNT_DETAIL:
        if provider:
            return f"/credentials?provider={provider}"
        return "/credentials"
    if key == AttentionCtaKey.OPEN_ACTIVITY_APPROVAL:
        return "/activity"
    if key == AttentionCtaKey.INSTALL_WORKER:
        return "/extension-setup"
    if key == AttentionCtaKey.CONNECT_GMAIL:
        return "/email-scan"
    if key == AttentionCtaKey.FOCUS_MANAGED_RUNTIME:
        return provider_url
    if key == AttentionCtaKey.NOOP:
        return None
    return None
