"""
Mighty component renderers — production HTML contracts.

Each function emits accessible markup using mds-* classes.
Future pages compose these; existing production UI is unchanged until opted in.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from mighty.design_system._html import attrs, classes, esc
from mighty.design_system.icons import render_icon

BUTTON_VARIANTS = frozenset({"primary", "secondary", "ghost", "destructive", "link"})
BUTTON_SIZES = frozenset({"sm", "md", "lg"})
BADGE_VARIANTS = frozenset({"quiet", "waiting", "attention", "review", "neutral"})
CARD_VARIANTS = frozenset({"surface", "soft", "interactive", "field"})
TRUST_VARIANTS = frozenset({"reassure", "limit", "consequence"})
EMPTY_VARIANTS = frozenset({"first-use", "all-clear", "no-results", "error"})
BANNER_VARIANTS = frozenset({"info", "waiting", "attention", "success"})
TOAST_VARIANTS = frozenset({"success", "info", "attention", "error"})
HERO_VARIANTS = frozenset({"marketing", "home", "onboarding"})
SECTION_VARIANTS = frozenset({"page", "panel", "split", "strip"})
TIMELINE_KINDS = frozenset({"authorized", "completed", "lifecycle", "needs-you"})
PROGRESS_STATES = frozenset({"upcoming", "live", "done", "error"})
NAV_VARIANTS = frozenset({"marketing", "app", "mobile"})


def render_brand(*, href: str | None = "/", wordmark: str = "Mighty") -> str:
    tag = "a" if href else "span"
    href_attr = attrs(href=href) if href else ""
    return (
        f'<{tag} class="mds-brand"{href_attr}>'
        f'<span class="mds-brand__mark" aria-hidden="true"></span>'
        f"<span>{esc(wordmark)}</span>"
        f"</{tag}>"
    )


def render_button(
    label: str,
    *,
    variant: str = "primary",
    size: str = "md",
    href: str | None = None,
    type: str = "button",
    block: bool = False,
    disabled: bool = False,
    loading: bool = False,
    icon: str | None = None,
    class_name: str = "",
    id: str | None = None,
    name: str | None = None,
    extra_attrs: dict[str, Any] | None = None,
) -> str:
    if variant not in BUTTON_VARIANTS:
        raise ValueError(f"Invalid button variant: {variant}")
    if size not in BUTTON_SIZES:
        raise ValueError(f"Invalid button size: {size}")

    cls = classes(
        "mds-btn",
        f"mds-btn--{variant}",
        f"mds-btn--{size}" if size != "md" else None,
        "mds-btn--block" if block else None,
        class_name,
    )
    a: dict[str, Any] = {"class": cls, "id": id, "name": name}
    if extra_attrs:
        a.update(extra_attrs)

    if loading:
        a["aria-busy"] = "true"
    if disabled or loading:
        if href:
            a["aria-disabled"] = "true"
            a["tabindex"] = "-1"
        else:
            a["disabled"] = True

    icon_html = render_icon(icon, decorative=True) if icon else ""
    label_html = esc(label)
    if loading:
        content = (
            f'<span class="mds-btn__spinner" aria-hidden="true"></span>'
            f"<span>{label_html}</span>"
        )
    else:
        content = f"{icon_html}<span>{label_html}</span>" if icon_html else label_html

    if href is not None:
        a["href"] = href
        return f"<a{attrs(a)}>{content}</a>"

    a["type"] = type
    return f"<button{attrs(a)}>{content}</button>"


def render_card(
    content: str,
    *,
    variant: str = "surface",
    padding: str = "md",
    selected: bool = False,
    class_name: str = "",
    role: str | None = None,
    labelled_by: str | None = None,
    tabindex: int | None = None,
) -> str:
    if variant not in CARD_VARIANTS:
        raise ValueError(f"Invalid card variant: {variant}")
    cls = classes(
        "mds-card",
        f"mds-card--{variant}" if variant != "surface" else None,
        "mds-card--pad-lg" if padding == "lg" else None,
        "mds-card--pad-sm" if padding == "sm" else None,
        "mds-card--selected" if selected else None,
        class_name,
    )
    a: dict[str, Any] = {"class": cls, "role": role, "aria-labelledby": labelled_by}
    if tabindex is not None:
        a["tabindex"] = str(tabindex)
    return f"<div{attrs(a)}>{content}</div>"


def render_section(
    *,
    title: str,
    body: str = "",
    eyebrow: str = "",
    content: str = "",
    variant: str = "page",
    heading_level: int = 2,
    title_id: str | None = None,
    class_name: str = "",
) -> str:
    if variant not in SECTION_VARIANTS:
        raise ValueError(f"Invalid section variant: {variant}")
    if heading_level < 1 or heading_level > 6:
        raise ValueError("heading_level must be 1–6")

    level = heading_level
    title_attr = f' id="{esc(title_id)}"' if title_id else ""
    eyebrow_html = f'<p class="mds-eyebrow">{esc(eyebrow)}</p>' if eyebrow else ""
    lede_html = f'<p class="mds-section__lede">{esc(body)}</p>' if body else ""
    header = (
        f'<header class="mds-section__header">'
        f"{eyebrow_html}"
        f'<h{level} class="mds-heading"{title_attr}>{esc(title)}</h{level}>'
        f"{lede_html}"
        f"</header>"
    )
    cls = classes("mds-section", f"mds-section--{variant}" if variant != "page" else None, class_name)
    return f'<section class="{esc(cls)}">{header}{content}</section>'


def render_quiet_field(
    *,
    status: str = "You're good.",
    meta: str = "Working quietly",
    signal: bool = False,
    ambient: bool = True,
) -> str:
    breathe = " mds-field-breathe" if ambient else ""
    points = []
    for i in range(5):
        is_signal = signal and i == 3
        points.append(
            f'<span class="mds-field-point{" is-signal" if is_signal else ""}"></span>'
        )
    return (
        f'<div class="mds-quiet-field{breathe}" aria-hidden="true">'
        f'<div class="mds-quiet-field__horizon"></div>'
        f'<div class="mds-quiet-field__points">{"".join(points)}</div>'
        f'<p class="mds-meta" style="position:relative;z-index:1;margin:0">{esc(meta)}</p>'
        f'<p class="mds-display mds-display-md" style="position:relative;z-index:1;color:#fff;margin:0.4rem 0 0">'
        f"{esc(status)}</p>"
        f"</div>"
    )


def render_hero(
    *,
    title: str,
    lede: str = "",
    variant: str = "home",
    eyebrow: str = "",
    actions_html: str = "",
    meta_html: str = "",
    aside_html: str = "",
    state: str = "default",
    heading_level: int = 1,
    class_name: str = "",
) -> str:
    if variant not in HERO_VARIANTS:
        raise ValueError(f"Invalid hero variant: {variant}")
    if heading_level < 1 or heading_level > 6:
        raise ValueError("heading_level must be 1–6")

    display = {
        "marketing": "mds-display mds-display-xl",
        "home": "mds-display mds-display-lg",
        "onboarding": "mds-display mds-display-md",
    }[variant]
    attention = " mds-hero--attention" if state == "attention" else ""
    eyebrow_html = f'<p class="mds-eyebrow">{esc(eyebrow)}</p>' if eyebrow else ""
    lede_html = f'<p class="mds-hero__lede">{esc(lede)}</p>' if lede else ""
    actions = f'<div class="mds-hero__actions">{actions_html}</div>' if actions_html else ""
    meta = f'<div class="mds-hero__meta">{meta_html}</div>' if meta_html else ""
    copy = (
        f'<div class="mds-hero__copy">'
        f"{eyebrow_html}"
        f'<h{heading_level} class="{display} mds-hero__title">{esc(title)}</h{heading_level}>'
        f"{lede_html}{actions}{meta}"
        f"</div>"
    )
    aside = f'<div class="mds-hero__aside">{aside_html}</div>' if aside_html else ""
    cls = classes("mds-hero", f"mds-hero--{variant}", class_name) + attention
    return f'<header class="{esc(cls.strip())}">{copy}{aside}</header>'


def render_status_badge(
    label: str,
    *,
    variant: str = "quiet",
    show_dot: bool = True,
    class_name: str = "",
) -> str:
    if variant not in BADGE_VARIANTS:
        raise ValueError(f"Invalid badge variant: {variant}")
    cls = classes("mds-badge", f"mds-badge--{variant}", class_name)
    dot = '<span class="mds-badge__dot" aria-hidden="true"></span>' if show_dot else ""
    return f'<span class="{esc(cls)}">{dot}<span>{esc(label)}</span></span>'


def render_trust_card(
    lead: str,
    body: str = "",
    *,
    variant: str = "reassure",
    dismissible: bool = False,
    dismiss_label: str = "Dismiss",
    class_name: str = "",
) -> str:
    if variant not in TRUST_VARIANTS:
        raise ValueError(f"Invalid trust card variant: {variant}")
    mod = {
        "reassure": "",
        "limit": " mds-trust--limit",
        "consequence": " mds-trust--consequence",
    }[variant]
    cls = classes("mds-trust", class_name) + mod
    body_html = f'<p class="mds-trust__body">{esc(body)}</p>' if body else ""
    lead_html = f'<strong class="mds-trust__lead">{esc(lead)}</strong>'
    if dismissible:
        content = (
            f'<div class="mds-trust__header">'
            f"<div>{lead_html}{body_html}</div>"
            f'<button type="button" class="mds-trust__dismiss" aria-label="{esc(dismiss_label)}">'
            f'{render_icon("close", size="sm")}</button>'
            f"</div>"
        )
    else:
        content = f"{lead_html}{body_html}"
    return f'<aside class="{esc(cls.strip())}" aria-label="Reassurance">{content}</aside>'


def render_permission_card(
    rows: Sequence[dict[str, Any]],
    *,
    eyebrow: str = "Informed consent",
    title: str = "",
    lede: str = "",
    primary_action_html: str = "",
    secondary_action_html: str = "",
    class_name: str = "",
) -> str:
    """rows: [{title, body, limits?: bool}]"""
    items = []
    for row in rows:
        limits = bool(row.get("limits"))
        row_cls = classes("mds-permission__row", "mds-permission__row--limits" if limits else None)
        items.append(
            f'<li class="{esc(row_cls)}">'
            f'<span class="mds-permission__dot" aria-hidden="true"></span>'
            f"<div>"
            f'<strong class="mds-permission__title">{esc(row["title"])}</strong>'
            f'<p class="mds-permission__body">{esc(row.get("body", ""))}</p>'
            f"</div></li>"
        )
    header = ""
    if title:
        eyebrow_html = f'<p class="mds-eyebrow">{esc(eyebrow)}</p>' if eyebrow else ""
        lede_html = f'<p class="mds-body">{esc(lede)}</p>' if lede else ""
        header = (
            f'<div class="mds-stack-sm">'
            f"{eyebrow_html}"
            f'<h1 class="mds-display mds-display-md">{esc(title)}</h1>'
            f"{lede_html}"
            f"</div>"
        )
    actions = ""
    if primary_action_html or secondary_action_html:
        actions = (
            f'<div class="mds-permission__actions">'
            f"{primary_action_html}{secondary_action_html}"
            f"</div>"
        )
    cls = classes("mds-permission", class_name)
    return (
        f'<section class="{esc(cls)}" aria-label="Permission explanation">'
        f"{header}"
        f'<ul class="mds-permission__list">{"".join(items)}</ul>'
        f"{actions}"
        f"</section>"
    )


def render_timeline(
    events: Sequence[dict[str, Any]],
    *,
    variant: str = "activity",
    class_name: str = "",
) -> str:
    """events: [{kind, title, body, time, datetime}]"""
    items = []
    for event in events:
        kind = event.get("kind", "lifecycle")
        if kind not in TIMELINE_KINDS:
            raise ValueError(f"Invalid timeline kind: {kind}")
        mark_mod = {
            "authorized": "mds-timeline__mark--authorized",
            "completed": "mds-timeline__mark--completed",
            "lifecycle": "",
            "needs-you": "mds-timeline__mark--needs-you",
        }[kind]
        icon_name = {
            "authorized": "check",
            "completed": "check",
            "lifecycle": "info",
            "needs-you": "warning",
        }[kind]
        kind_label = {
            "authorized": "You authorized",
            "completed": "Completed work",
            "lifecycle": "Lifecycle",
            "needs-you": "Needs you",
        }[kind]
        time_html = ""
        if event.get("time"):
            dt = event.get("datetime") or ""
            time_html = (
                f'<time class="mds-timeline__time"{attrs(datetime=dt) if dt else ""}>'
                f'{esc(event["time"])}</time>'
            )
        items.append(
            f"<li class=\"mds-timeline__item\">"
            f'<div class="mds-timeline__mark {mark_mod}" aria-hidden="true">'
            f'{render_icon(icon_name, size="sm")}</div>'
            f"<div>"
            f'<span class="mds-timeline__kind">{esc(kind_label)}</span>'
            f'<h3 class="mds-timeline__title">{esc(event["title"])}</h3>'
            f'<p class="mds-timeline__body">{esc(event.get("body", ""))}</p>'
            f"{time_html}"
            f"</div></li>"
        )
    compact = " mds-timeline--compact" if variant == "compact" else ""
    cls = classes("mds-timeline", class_name) + compact
    return f'<ol class="{esc(cls.strip())}">{"".join(items)}</ol>'


def render_account_row(
    *,
    name: str,
    monogram: str,
    meta: str = "",
    balance: str = "",
    status_html: str = "",
    action_html: str = "",
    variant: str = "row",
    selected: bool = False,
    selectable: bool = False,
    checkbox_name: str = "",
    checkbox_value: str = "",
    disabled: bool = False,
    class_name: str = "",
) -> str:
    variants = {"row", "compact", "selectable", "suggestion"}
    if variant not in variants:
        raise ValueError(f"Invalid account row variant: {variant}")

    cls = classes(
        "mds-account",
        f"mds-account--{variant}" if variant != "row" else None,
        "mds-account--selected" if selected else None,
        class_name,
    )
    letters = esc((monogram or name[:2]).upper()[:2])
    meta_html = f'<p class="mds-account__meta">{esc(meta)}</p>' if meta else ""
    identity = (
        f'<div class="mds-account__identity">'
        f'<span class="mds-account__monogram" aria-hidden="true">{letters}</span>'
        f"<div>"
        f'<p class="mds-account__name">{esc(name)}</p>'
        f"{meta_html}"
        f"</div></div>"
    )
    balance_html = f'<div class="mds-account__balance">{esc(balance)}</div>' if balance else ""
    status_block = status_html
    actions = f'<div class="mds-account__actions">{action_html}</div>' if action_html else ""

    toggle = ""
    if selectable or variant == "selectable":
        label = f"Watch {name}"
        toggle = (
            f'<label class="mds-check">'
            f'<input type="checkbox" name="{esc(checkbox_name)}" value="{esc(checkbox_value or name)}"'
            f'{" checked" if selected else ""}{" disabled" if disabled else ""}'
            f' aria-label="{esc(label)}"/>'
            f"</label>"
        )

    return (
        f'<div class="{esc(cls)}" role="group" aria-label="{esc(name)}">'
        f"{toggle}{identity}{balance_html}{status_block}{actions}"
        f"</div>"
    )


def render_empty_state(
    *,
    title: str,
    body: str,
    future: str = "",
    action_html: str = "",
    icon: str = "horizon-points",
    variant: str = "first-use",
    class_name: str = "",
) -> str:
    if variant not in EMPTY_VARIANTS:
        raise ValueError(f"Invalid empty state variant: {variant}")
    role = "alert" if variant == "error" else "region"
    cls = classes(
        "mds-empty",
        "mds-empty--error" if variant == "error" else None,
        class_name,
    )
    future_html = f'<p class="mds-empty__future">{esc(future)}</p>' if future else ""
    action = f'<div class="mds-empty__action">{action_html}</div>' if action_html else ""
    return (
        f'<div class="{esc(cls)}" role="{role}" aria-label="{esc(title)}">'
        f'<div class="mds-empty__mark" aria-hidden="true">{render_icon(icon)}</div>'
        f'<h2 class="mds-empty__title">{esc(title)}</h2>'
        f'<p class="mds-empty__body">{esc(body)}</p>'
        f"{future_html}{action}"
        f"</div>"
    )


def render_modal(
    *,
    title: str,
    body: str,
    actions_html: str,
    open: bool = False,
    modal_id: str = "mds-modal",
    title_id: str | None = None,
    busy: bool = False,
    class_name: str = "",
) -> str:
    tid = title_id or f"{modal_id}-title"
    root_hidden = "" if open else " hidden"
    busy_attr = ' aria-busy="true"' if busy else ""
    cls = classes("mds-modal", class_name)
    return (
        f'<div class="mds-modal-root" id="{esc(modal_id)}-root"{root_hidden}>'
        f'<div class="mds-modal__scrim" data-mds-modal-dismiss></div>'
        f'<div class="{esc(cls)}" role="dialog" aria-modal="true" aria-labelledby="{esc(tid)}"{busy_attr}>'
        f'<h2 class="mds-modal__title" id="{esc(tid)}">{esc(title)}</h2>'
        f'<p class="mds-modal__body">{esc(body)}</p>'
        f'<div class="mds-modal__actions">{actions_html}</div>'
        f"</div></div>"
    )


def render_progress_stepper(
    steps: Sequence[dict[str, Any]],
    *,
    variant: str = "horizontal",
    labelled_by: str | None = None,
    live: bool = False,
    class_name: str = "",
) -> str:
    """steps: [{label, state, meta?}] state in upcoming|live|done|error"""
    items = []
    for index, step in enumerate(steps, start=1):
        state = step.get("state", "upcoming")
        if state not in PROGRESS_STATES:
            raise ValueError(f"Invalid progress state: {state}")
        state_cls = {
            "upcoming": "is-upcoming",
            "live": "is-live",
            "done": "is-done",
            "error": "is-error",
        }[state]
        current = ' aria-current="step"' if state == "live" else ""
        mark_content = "✓" if state == "done" else str(index)
        meta = (
            f'<span class="mds-progress__meta">{esc(step["meta"])}</span>'
            if step.get("meta")
            else ""
        )
        items.append(
            f'<li class="mds-progress__step {state_cls}"{current}>'
            f'<span class="mds-progress__mark" aria-hidden="true">{mark_content}</span>'
            f"<span><span>{esc(step['label'])}</span>{meta}</span>"
            f"</li>"
        )
    orient = "vertical" if variant in {"discovery", "vertical"} else "horizontal"
    cls = classes(
        "mds-progress",
        "mds-progress--vertical" if orient == "vertical" else None,
        class_name,
    )
    live_attr = ' aria-live="polite"' if live else ""
    label_attr = f' aria-labelledby="{esc(labelled_by)}"' if labelled_by else ' aria-label="Progress"'
    return f'<ol class="{esc(cls)}"{label_attr}{live_attr}>{"".join(items)}</ol>'


def render_navigation(
    items: Sequence[dict[str, Any]],
    *,
    variant: str = "app",
    brand_html: str = "",
    status_html: str = "",
    sticky: bool = False,
    label: str = "Primary",
    class_name: str = "",
) -> str:
    """items: [{label, href, current?}]"""
    if variant not in NAV_VARIANTS:
        raise ValueError(f"Invalid navigation variant: {variant}")
    links = []
    for item in items:
        current = bool(item.get("current"))
        a = {
            "class": "mds-nav__link",
            "href": item["href"],
            "aria-current": "page" if current else None,
        }
        links.append(f"<a{attrs(a)}>{esc(item['label'])}</a>")
    nav_cls = classes("mds-nav", f"mds-nav--{variant}")
    nav = f'<nav class="{esc(nav_cls)}" aria-label="{esc(label)}">{"".join(links)}</nav>'
    header_cls = classes(
        "mds-nav-header",
        "mds-nav-header--sticky" if sticky else None,
        class_name,
    )
    brand = brand_html or render_brand()
    status = f'<div class="mds-nav__status">{status_html}</div>' if status_html else ""
    return (
        f'<header class="{esc(header_cls)}">'
        f"{brand}{nav}{status}"
        f"</header>"
    )


def render_text_field(
    *,
    label: str,
    name: str,
    input_type: str = "text",
    value: str = "",
    placeholder: str = "",
    helper: str = "",
    error: str = "",
    required: bool = False,
    disabled: bool = False,
    autocomplete: str | None = None,
    field_id: str | None = None,
    class_name: str = "",
) -> str:
    fid = field_id or f"mds-field-{name}"
    helper_id = f"{fid}-helper" if helper else None
    error_id = f"{fid}-error" if error else None
    described_by = " ".join(x for x in (helper_id, error_id) if x)
    control_tag = "textarea" if input_type == "textarea" else "input"
    control_attrs: dict[str, Any] = {
        "class": "mds-field__control",
        "id": fid,
        "name": name,
        "placeholder": placeholder or None,
        "required": required,
        "disabled": disabled,
        "autocomplete": autocomplete,
        "aria-invalid": "true" if error else None,
        "aria-describedby": described_by or None,
    }
    if control_tag == "input":
        control_attrs["type"] = input_type
        control_attrs["value"] = value
        control = f"<input{attrs(control_attrs)}/>"
    else:
        control = f"<textarea{attrs(control_attrs)}>{esc(value)}</textarea>"

    helper_html = (
        f'<p class="mds-field__helper" id="{esc(helper_id)}">{esc(helper)}</p>'
        if helper
        else ""
    )
    error_html = (
        f'<p class="mds-field__error" id="{esc(error_id)}" role="alert">{esc(error)}</p>'
        if error
        else ""
    )
    cls = classes("mds-field", class_name)
    return (
        f'<div class="{esc(cls)}">'
        f'<label class="mds-field__label" for="{esc(fid)}">{esc(label)}</label>'
        f"{control}{helper_html}{error_html}"
        f"</div>"
    )


def render_checkbox(
    *,
    label: str,
    name: str,
    value: str = "on",
    checked: bool = False,
    disabled: bool = False,
    field_id: str | None = None,
) -> str:
    fid = field_id or f"mds-check-{name}"
    return (
        f'<label class="mds-check" for="{esc(fid)}">'
        f'<input id="{esc(fid)}" type="checkbox" name="{esc(name)}" value="{esc(value)}"'
        f'{" checked" if checked else ""}{" disabled" if disabled else ""}/>'
        f"<span>{esc(label)}</span>"
        f"</label>"
    )


def render_switch(
    *,
    label: str,
    name: str,
    checked: bool = False,
    disabled: bool = False,
    field_id: str | None = None,
) -> str:
    fid = field_id or f"mds-switch-{name}"
    return (
        f'<label class="mds-switch" for="{esc(fid)}">'
        f"<span>{esc(label)}</span>"
        f'<input id="{esc(fid)}" type="checkbox" role="switch" name="{esc(name)}"'
        f'{" checked" if checked else ""}{" disabled" if disabled else ""}'
        f' aria-checked="{"true" if checked else "false"}"/>'
        f'<span class="mds-switch__track" aria-hidden="true"></span>'
        f"</label>"
    )


def render_toast(
    message: str,
    *,
    variant: str = "success",
    action_label: str = "",
    toast_id: str | None = None,
    class_name: str = "",
) -> str:
    if variant not in TOAST_VARIANTS:
        raise ValueError(f"Invalid toast variant: {variant}")
    role = "alert" if variant == "error" else "status"
    cls = classes("mds-toast", f"mds-toast--{variant}", class_name)
    action = (
        f'<button type="button" class="mds-toast__action">{esc(action_label)}</button>'
        if action_label
        else ""
    )
    id_attr = f' id="{esc(toast_id)}"' if toast_id else ""
    return (
        f'<div class="{esc(cls)}" role="{role}"{id_attr}>'
        f"<span>{esc(message)}</span>{action}"
        f"</div>"
    )


def render_banner(
    message: str,
    *,
    variant: str = "info",
    action_html: str = "",
    dismissible: bool = False,
    dismiss_label: str = "Dismiss notification",
    class_name: str = "",
) -> str:
    if variant not in BANNER_VARIANTS:
        raise ValueError(f"Invalid banner variant: {variant}")
    cls = classes("mds-banner", f"mds-banner--{variant}", class_name)
    dismiss = (
        f'<button type="button" class="mds-banner__dismiss" aria-label="{esc(dismiss_label)}">'
        f'{render_icon("close", size="sm")}</button>'
        if dismissible
        else ""
    )
    actions = ""
    if action_html or dismiss:
        actions = f'<div class="mds-banner__actions">{action_html}{dismiss}</div>'
    return (
        f'<div class="{esc(cls)}" role="region" aria-label="Notification">'
        f'<p class="mds-banner__body">{esc(message)}</p>'
        f"{actions}"
        f"</div>"
    )


def join_blocks(blocks: Iterable[str], gap_class: str = "mds-stack-lg") -> str:
    return f'<div class="{esc(gap_class)}">{"".join(blocks)}</div>'
