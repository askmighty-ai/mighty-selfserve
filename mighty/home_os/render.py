"""Home OS shell renderer — Quiet Field + four regions + in-place repair modal.

Presentation only. Consumes projected HomeState + slice repair phase.
No ranking, lifecycle, or projection logic.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Callable

from mighty.design_system.components import (
    render_brand,
    render_button,
    render_modal,
    render_status_badge,
)
from mighty.home_os.marriott_scenario import PROVIDER_DISPLAY, SIMULATION_MODE
from mighty.home_os.session_state import HomeOsSliceState, RepairPhase
from mighty.workitem.coverage import AuthPosture, CoverageItem
from mighty.workitem.home_state import HomeState, HomeStatusMode
from mighty.workitem.model import WorkItem
from mighty.workitem.proof import ProofDisclosure


Escape = Callable[[Any], str]


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _status_copy(home: HomeState, *, first_name: str) -> tuple[str, str, str]:
    """Return (eyebrow, title, lede) for the Status region."""
    greeting = f"Good morning, {first_name}" if first_name else "Welcome back"
    if home.status is HomeStatusMode.CALM:
        return (
            greeting,
            "You're good.",
            "Nothing needs you right now. Mighty is watching quietly.",
        )
    if home.status is HomeStatusMode.NEEDS_USER:
        n = len(home.work_queue)
        title = "1 thing needs you" if n == 1 else f"{n} things need you"
        return (
            greeting,
            title,
            "Resolve it here — you do not need to leave Home.",
        )
    if home.status is HomeStatusMode.VALUE_WAITING:
        return (greeting, "Value is waiting", "Something worthwhile is ready when you are.")
    if home.status is HomeStatusMode.SETUP_INCOMPLETE:
        return (greeting, "A setup step is waiting", "Mighty needs one unlock to keep going.")
    return (greeting, "You're good.", "")


def _auth_label(item: CoverageItem) -> str:
    if item.authentication is AuthPosture.VALID:
        return "Signed in"
    if item.authentication is AuthPosture.MISSING:
        return "Signed out"
    if item.authentication is AuthPosture.EXPIRED:
        return "Sign-in expired"
    return "Access unknown"


def _coverage_summary(coverage: tuple[CoverageItem, ...]) -> str:
    if not coverage:
        return "No accounts in coverage yet"
    n = len(coverage)
    blocked = sum(1 for c in coverage if c.authentication is not AuthPosture.VALID)
    if blocked:
        return f"{n} watched · {blocked} need attention"
    return f"{n} watched · All settled"


def render_home_os_page(
    home: HomeState,
    slice_state: HomeOsSliceState,
    *,
    csrf_token: str,
    today_label: str = "",
    escape: Escape | None = None,
) -> str:
    """Full-page Home OS HTML — no legacy sidebar or destination rail."""
    esc = escape or _esc
    first_name = slice_state.display_name
    eyebrow, title, lede = _status_copy(home, first_name=first_name)
    modal_open = slice_state.repair_phase in (
        RepairPhase.IN_PROGRESS,
        RepairPhase.FAILED,
    )
    data_state = "calm" if home.silence else "attention"
    if slice_state.repair_phase is RepairPhase.SUCCEEDED:
        data_state = "calm"
    if slice_state.repair_phase is RepairPhase.FAILED:
        data_state = "attention"

    body = (
        f'{_render_chrome(esc, first_name=first_name)}'
        f'<main class="home-os__main" id="home-os-main">'
        f'{_render_field(home, eyebrow=eyebrow, title=title, lede=lede, today_label=today_label, esc=esc)}'
        f'{_render_work_queue(home, slice_state, csrf_token=csrf_token, esc=esc)}'
        f'{_render_proof(home, esc=esc)}'
        f'{_render_coverage(home, esc=esc)}'
        f"</main>"
        f'{_render_repair_modal(home, slice_state, csrf_token=csrf_token, open=modal_open, esc=esc)}'
        f"{_page_script()}"
    )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8"/>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
        "<title>Home — Mighty</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com"/>\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&'
        'family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"/>\n'
        '<link rel="stylesheet" href="/static/design-system/mighty-ds.css"/>\n'
        f"<style>{_page_css()}</style>\n"
        "</head>\n"
        f'<body class="mds home-os" data-home-os="1" data-state="{esc(data_state)}" '
        f'data-repair-phase="{esc(slice_state.repair_phase.value)}" '
        f'data-simulation="{esc(SIMULATION_MODE)}">\n'
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _render_chrome(esc: Escape, *, first_name: str) -> str:
    initial = (first_name[:1] or "M").upper()
    brand = render_brand(href="/home", wordmark="Mighty")
    return (
        '<header class="home-os__chrome">'
        f"{brand}"
        '<div class="home-os__utilities">'
        f'<details class="home-os__avatar-menu">'
        f'<summary class="home-os__avatar" aria-label="Account menu for {esc(first_name)}">'
        f'<span aria-hidden="true">{esc(initial)}</span>'
        "</summary>"
        '<div class="home-os__menu" role="menu">'
        '<a role="menuitem" href="/settings">Settings</a>'
        '<a role="menuitem" href="/logout">Sign out</a>'
        "</div>"
        "</details>"
        "</div>"
        "</header>"
    )


def _render_field(
    home: HomeState,
    *,
    eyebrow: str,
    title: str,
    lede: str,
    today_label: str,
    esc: Escape,
) -> str:
    signal = not home.silence
    points = "".join(
        f'<span class="mds-field-point{" is-signal" if signal and i == 2 else ""}"></span>'
        for i in range(5)
    )
    meta_bits = []
    if today_label:
        meta_bits.append(esc(today_label))
    if home.silence:
        meta_bits.append("Working quietly")
    else:
        meta_bits.append("Needs you")
    meta = " · ".join(meta_bits)
    return (
        f'<section class="home-os__status" aria-labelledby="home-os-status-title" '
        f'data-region="status" data-silence="{str(home.silence).lower()}">'
        f'<div class="mds-quiet-field home-os__field{" mds-field-breathe" if not home.silence else ""}" '
        f'aria-hidden="true">'
        f'<div class="mds-quiet-field__horizon"></div>'
        f'<div class="mds-quiet-field__points">{points}</div>'
        f"</div>"
        f'<div class="home-os__status-copy">'
        f'<p class="mds-eyebrow">{esc(eyebrow)}</p>'
        f'<h1 class="mds-display mds-display-lg" id="home-os-status-title">{esc(title)}</h1>'
        f'<p class="home-os__lede">{esc(lede)}</p>'
        f'<p class="mds-meta">{meta}</p>'
        f"</div>"
        f"</section>"
    )


def _render_work_queue(
    home: HomeState,
    slice_state: HomeOsSliceState,
    *,
    csrf_token: str,
    esc: Escape,
) -> str:
    if not home.work_queue:
        return (
            '<section class="home-os__queue" data-region="work-queue" hidden '
            'aria-hidden="true"></section>'
        )
    item = home.work_queue[0]
    fail_note = ""
    if slice_state.repair_phase is RepairPhase.FAILED and slice_state.repair_message:
        fail_note = (
            f'<p class="home-os__inline-alert" role="status">{esc(slice_state.repair_message)}</p>'
        )
    elif slice_state.repair_phase is RepairPhase.EXPIRED and slice_state.repair_message:
        fail_note = (
            f'<p class="home-os__inline-alert" role="status">{esc(slice_state.repair_message)}</p>'
        )

    primary = render_button(
        item.primary_action.intent,
        variant="primary",
        type="submit",
        class_name="home-os__primary",
        extra_attrs={"form": f"home-os-start-{item.id}"},
    )
    secondary = ""
    if item.secondary_action is not None and item.deferrable:
        secondary = render_button(
            item.secondary_action.intent,
            variant="ghost",
            type="submit",
            class_name="home-os__secondary",
            extra_attrs={"form": f"home-os-cancel-{item.id}", "formaction": f"/home/work/{item.id}/cancel"},
        )

    forms = (
        f'<form id="home-os-start-{esc(item.id)}" method="post" '
        f'action="/home/work/{esc(item.id)}/start" class="home-os__sr-form">'
        f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}"/>'
        f"</form>"
        f'<form id="home-os-cancel-{esc(item.id)}" method="post" '
        f'action="/home/work/{esc(item.id)}/cancel" class="home-os__sr-form">'
        f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}"/>'
        f"</form>"
    )

    badge = render_status_badge("Needs sign-in", variant="attention")
    return (
        f'<section class="home-os__queue" data-region="work-queue" '
        f'aria-labelledby="home-os-work-title">'
        f'<header class="home-os__region-head">'
        f'<h2 class="mds-meta" id="home-os-work-title">Needs you</h2>'
        f"</header>"
        f'<article class="home-os__work" data-work-item-id="{esc(item.id)}" '
        f'data-work-type="{esc(item.type.value)}" data-expanded="true">'
        f'<div class="home-os__work-top">{badge}'
        f'<p class="mds-meta">{esc(PROVIDER_DISPLAY)}</p></div>'
        f'<h3 class="mds-heading">{esc(item.title)}</h3>'
        f'<p class="home-os__work-summary">{esc(item.summary)}</p>'
        f"{fail_note}"
        f'<div class="home-os__work-actions">{primary}{secondary}</div>'
        f'<p class="mds-meta home-os__work-footnote">'
        f"Resolved on Home — no Accounts or Credentials hop."
        f"</p>"
        f"</article>"
        f"{forms}"
        f"</section>"
    )


def _render_proof(home: HomeState, *, esc: Escape) -> str:
    if not home.proof:
        return ""
    rows = "".join(_proof_row(row, esc=esc) for row in home.proof[:5])
    return (
        f'<section class="home-os__proof" data-region="proof" '
        f'aria-labelledby="home-os-proof-title">'
        f'<header class="home-os__region-head">'
        f'<h2 class="mds-meta" id="home-os-proof-title">Recent proof</h2>'
        f"</header>"
        f'<ul class="home-os__proof-list">{rows}</ul>'
        f"</section>"
    )


def _proof_row(row: ProofDisclosure, *, esc: Escape) -> str:
    when = row.outcome_at.strftime("%b %d").replace(" 0", " ")
    provider = row.provider or ""
    label = row.summary
    return (
        f'<li class="home-os__proof-item" data-proof-id="{esc(row.id)}">'
        f'<span class="home-os__proof-summary">{esc(label)}</span>'
        f'<span class="mds-meta">{esc(when)}'
        f'{(" · " + esc(provider)) if provider else ""}</span>'
        f"</li>"
    )


def _render_coverage(home: HomeState, *, esc: Escape) -> str:
    summary = _coverage_summary(home.coverage)
    rows = "".join(_coverage_row(item, esc=esc) for item in home.coverage)
    return (
        f'<section class="home-os__coverage" data-region="coverage">'
        f'<details class="home-os__coverage-details">'
        f'<summary class="home-os__coverage-summary">'
        f'<span class="mds-meta">Coverage</span>'
        f'<span class="home-os__coverage-line">{esc(summary)}</span>'
        f'<span class="mds-meta">Expand</span>'
        f"</summary>"
        f'<ul class="home-os__coverage-list" aria-label="What Mighty is watching">'
        f"{rows}"
        f"</ul>"
        f'<p class="mds-meta home-os__coverage-footnote">'
        f"Coverage is disclosure on Home — not a separate Accounts page."
        f"</p>"
        f"</details>"
        f"</section>"
    )


def _coverage_row(item: CoverageItem, *, esc: Escape) -> str:
    name = item.display_name or item.provider
    auth = _auth_label(item)
    variant = "quiet" if item.authentication is AuthPosture.VALID else "attention"
    badge = render_status_badge(auth, variant=variant)
    return (
        f'<li class="home-os__coverage-item" data-provider="{esc(item.provider)}" '
        f'data-auth="{esc(item.authentication.value)}">'
        f'<div><p class="mds-label">{esc(name)}</p>'
        f'<p class="mds-meta">Included in your coverage</p></div>'
        f"{badge}"
        f"</li>"
    )


def _render_repair_modal(
    home: HomeState,
    slice_state: HomeOsSliceState,
    *,
    csrf_token: str,
    open: bool,
    esc: Escape,
) -> str:
    item = _expanded_item(home)
    if item is None:
        # Still allow failed modal if phase says so with last known id
        work_id = "unknown"
    else:
        work_id = item.id

    if slice_state.repair_phase is RepairPhase.FAILED:
        title = "Sign-in did not finish"
        body = slice_state.repair_message or (
            "Marriott sign-in did not finish. Try again from Home."
        )
        actions = (
            f'<form method="post" action="/home/work/{esc(work_id)}/start">'
            f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}"/>'
            f'{render_button("Try again", variant="primary", type="submit")}'
            f"</form>"
            f'<form method="post" action="/home/work/{esc(work_id)}/cancel">'
            f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}"/>'
            f'{render_button("Close", variant="ghost", type="submit")}'
            f"</form>"
        )
        return render_modal(
            title=title,
            body=body,
            actions_html=actions,
            open=open,
            modal_id="home-os-repair",
            class_name="home-os__modal",
        )

    title = "Restore Marriott access"
    body = (
        "You included Marriott so Mighty can watch it. Confirm the staged sign-in "
        "to restore access. This preview uses a safe simulated completion — "
        "no live Marriott password is collected here."
    )
    actions = (
        f'<form method="post" action="/home/work/{esc(work_id)}/complete" '
        f'data-simulation="{esc(SIMULATION_MODE)}">'
        f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}"/>'
        f'{render_button("Confirm sign-in", variant="primary", type="submit", id="home-os-confirm")}'
        f"</form>"
        f'<form method="post" action="/home/work/{esc(work_id)}/fail">'
        f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}"/>'
        f'{render_button("Sign-in didn\'t work", variant="secondary", type="submit")}'
        f"</form>"
        f'<form method="post" action="/home/work/{esc(work_id)}/cancel">'
        f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}"/>'
        f'{render_button("Cancel", variant="ghost", type="submit")}'
        f"</form>"
    )
    return render_modal(
        title=title,
        body=body,
        actions_html=actions,
        open=open,
        modal_id="home-os-repair",
        class_name="home-os__modal",
    )


def _expanded_item(home: HomeState) -> WorkItem | None:
    if not home.expanded_work_item_id:
        return None
    for item in home.work_queue:
        if item.id == home.expanded_work_item_id:
            return item
    return home.work_queue[0] if home.work_queue else None


def _page_css() -> str:
    return """
.home-os{min-height:100vh;background:var(--mds-bg);color:var(--mds-ink);
  font-family:var(--mds-font-ui);}
.home-os__chrome{display:flex;align-items:center;justify-content:space-between;
  max-width:720px;margin:0 auto;padding:var(--mds-space-4) var(--mds-space-5);}
.home-os__chrome .mds-brand{font-size:1.05rem;}
.home-os__avatar{list-style:none;width:40px;height:40px;border-radius:var(--mds-radius-monogram);
  background:var(--mds-pine-soft);color:var(--mds-pine-ink);display:grid;place-items:center;
  font-weight:650;cursor:pointer;border:1px solid var(--mds-line);}
.home-os__avatar::-webkit-details-marker{display:none;}
.home-os__avatar:focus-visible,.home-os__menu a:focus-visible,.mds-btn:focus-visible,
.home-os__coverage-summary:focus-visible{outline:3px solid var(--mds-focus);outline-offset:2px;}
.home-os__avatar-menu{position:relative;}
.home-os__menu{position:absolute;right:0;top:calc(100% + 8px);min-width:180px;
  background:var(--mds-surface);border:1px solid var(--mds-line);border-radius:var(--mds-radius);
  box-shadow:var(--mds-shadow-sm);padding:var(--mds-space-2);display:grid;gap:2px;z-index:20;}
.home-os__menu a{display:block;padding:10px 12px;border-radius:10px;color:var(--mds-ink);
  text-decoration:none;font-size:var(--mds-text-body-sm);font-weight:600;}
.home-os__menu a:hover{background:var(--mds-surface-soft);}
.home-os__main{max-width:720px;margin:0 auto;padding:0 var(--mds-space-5) var(--mds-space-8);
  display:grid;gap:var(--mds-space-5);}
.home-os__status{position:relative;border-radius:var(--mds-radius-lg);overflow:hidden;
  min-height:220px;isolation:isolate;}
.home-os__field{position:absolute;inset:0;border-radius:inherit;}
.home-os__status-copy{position:relative;z-index:1;padding:var(--mds-space-6) var(--mds-space-5);
  color:#fff;}
.home-os__status-copy .mds-eyebrow,.home-os__status-copy .mds-meta{color:rgba(255,255,255,.72);}
.home-os__status-copy .mds-display{color:#fff;}
.home-os__lede{margin:0.55rem 0 0;max-width:36ch;font-size:var(--mds-text-body);
  line-height:1.5;color:rgba(255,255,255,.88);}
.home-os__region-head{margin-bottom:var(--mds-space-3);}
.home-os__work{background:var(--mds-surface);border:1px solid var(--mds-line);
  border-radius:var(--mds-radius-lg);padding:var(--mds-space-5);box-shadow:var(--mds-shadow-sm);
  display:grid;gap:var(--mds-space-3);}
.home-os__work-top{display:flex;align-items:center;justify-content:space-between;gap:12px;}
.home-os__work-summary{margin:0;color:var(--mds-ink-soft);font-size:var(--mds-text-body);
  line-height:1.55;max-width:48ch;}
.home-os__work-actions{display:flex;flex-wrap:wrap;gap:var(--mds-space-3);margin-top:var(--mds-space-2);}
.home-os__work-footnote,.home-os__coverage-footnote{margin:0;}
.home-os__inline-alert{margin:0;padding:12px 14px;border-radius:var(--mds-radius);
  background:var(--mds-attention-soft);color:var(--mds-attention);font-size:var(--mds-text-body-sm);
  line-height:1.45;}
.home-os__proof-list,.home-os__coverage-list{list-style:none;margin:0;padding:0;display:grid;gap:10px;}
.home-os__proof-item,.home-os__coverage-item{display:flex;justify-content:space-between;
  gap:16px;align-items:baseline;padding:12px 0;border-bottom:1px solid var(--mds-line);}
.home-os__proof-item:last-child,.home-os__coverage-item:last-child{border-bottom:0;}
.home-os__proof-summary{font-size:var(--mds-text-body-sm);color:var(--mds-ink-soft);}
.home-os__coverage-details{background:var(--mds-surface-soft);border:1px solid var(--mds-line);
  border-radius:var(--mds-radius-lg);padding:var(--mds-space-4) var(--mds-space-5);}
.home-os__coverage-summary{display:flex;align-items:center;justify-content:space-between;
  gap:12px;cursor:pointer;list-style:none;}
.home-os__coverage-summary::-webkit-details-marker{display:none;}
.home-os__coverage-line{flex:1;color:var(--mds-ink-soft);font-size:var(--mds-text-body-sm);}
.home-os__coverage-list{margin-top:var(--mds-space-4);}
.home-os__sr-form{display:none;}
.home-os .mds-modal-root:not([hidden]){position:fixed;inset:0;z-index:40;display:grid;
  place-items:center;padding:24px;}
.home-os .mds-modal__scrim{position:absolute;inset:0;background:rgba(28,25,21,.42);}
.home-os .mds-modal{position:relative;z-index:1;width:min(100%,560px);background:var(--mds-surface);
  border-radius:var(--mds-radius-lg);border:1px solid var(--mds-line);box-shadow:var(--mds-shadow-md);
  padding:var(--mds-space-6);display:grid;gap:var(--mds-space-4);}
.home-os .mds-modal__actions{display:flex;flex-wrap:wrap;gap:var(--mds-space-3);}
.home-os .mds-modal__actions form{margin:0;}
@media (max-width:640px){
  .home-os__chrome,.home-os__main{padding-left:var(--mds-space-4);padding-right:var(--mds-space-4);}
  .home-os__status{min-height:200px;}
  .home-os__work-actions{flex-direction:column;align-items:stretch;}
  .home-os__work-actions .mds-btn{width:100%;}
}
@media (prefers-reduced-motion:reduce){
  .home-os .mds-field-breathe,.home-os .mds-quiet-field{animation:none !important;}
}
"""


def _page_script() -> str:
    """Accessibility helpers only — no business logic."""
    return """
<script>
(function () {
  var root = document.getElementById('home-os-repair-root');
  if (!root || root.hasAttribute('hidden')) return;
  var dialog = root.querySelector('.mds-modal');
  var focusable = dialog ? dialog.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') : [];
  if (focusable.length) focusable[0].focus();
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      var cancel = root.querySelector('form[action$="/cancel"] button');
      if (cancel) cancel.click();
    }
  });
})();
</script>
"""
