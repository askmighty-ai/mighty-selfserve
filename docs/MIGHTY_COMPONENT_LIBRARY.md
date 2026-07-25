# Mighty Component Library

**Status:** Canonical production component contracts (documentation only)  
**Audience:** Product, design, engineering  
**Governs:** Future customer-facing production UI  
**Depends on:** [MIGHTY_VISUAL_SYSTEM_V1.md](MIGHTY_VISUAL_SYSTEM_V1.md) · [TRUST_BY_DESIGN.md](TRUST_BY_DESIGN.md) · [FIRST_10_MINUTES.md](FIRST_10_MINUTES.md) · [MIGHTY_ICONOGRAPHY.md](MIGHTY_ICONOGRAPHY.md)

**Not this document:** CSS, HTML, Flask templates, JavaScript, or implementation plans.  
**Prototype note:** Trust V1 (`prototypes/trust_v1/`) is frozen. This library translates its proven patterns into production contracts without altering the prototype.

---

## Mission

Components exist to make trust repeatable.

Every component must:

1. Support **one primary job** on the screen where it appears.
2. Prefer **clarity over decoration**.
3. Keep the user in control near sensitive actions.
4. Communicate status with **text + structure**, never color alone.
5. Feel like Mighty’s Quiet Field identity — calm, warm, restrained, quietly capable.

Surfaces and cards are earned. If removing a border, shadow, or radius does not hurt understanding or interaction, it should not be a card.

---

## Global rules

### Hierarchy

- One filled primary action per view (or none when all-clear is intentional).
- Display serif is for brand moments and primary answers (“You’re good.”), not for buttons or dense UI.
- UI sans is for controls, forms, labels, and operational text.

### Spacing scale (reference)

| Token | Value |
|-------|-------|
| `space-1` | 4px |
| `space-2` | 8px |
| `space-3` | 12px |
| `space-4` | 16px |
| `space-5` | 24px |
| `space-6` | 32px |
| `space-7` | 48px |
| `space-8` | 64px |

### Shared interaction

| State | Behavior |
|-------|----------|
| Hover | Subtle darken / soft fill; no bounce |
| Active | 1px press translation max |
| Focus-visible | 3px pine focus ring, 2px offset |
| Disabled | 40–50% opacity; not-focusable; cursor not-allowed |
| Loading | Replace label with calm progress text or inline spinner; keep width stable |
| Reduced motion | Disable pulses and decorative motion; keep essential state changes |

### Shared accessibility

- Minimum touch target 44×44px for primary interactive controls on touch.
- WCAG AA contrast for text and essential controls.
- Visible focus always.
- Status never color-only.
- Semantic elements: `button` vs `a` correctly; labels for all inputs; live regions for dynamic status when needed.

---

## 1. Button

### Purpose

Commit to a single clear action. Buttons are operational; they never use display serif.

### Variants

| Variant | Use |
|---------|-----|
| **Primary** | The one filled action on the page |
| **Secondary** | Alternative path of equal structural weight but lower emphasis |
| **Ghost** | Low-emphasis deferral (“Not now”, “Add manually”) |
| **Destructive** | Irreversible or high-cost actions (disconnect, delete) — rare |
| **Link-button** | Inline textual action inside paragraphs (underline on hover/focus) |

Sizes: `md` (default), `lg` (onboarding / hero), `sm` (dense rows only).  
Width: `hug` or `block` (full width in narrow onboarding).

### States

Default · Hover · Active · Focus-visible · Disabled · Loading

### Spacing

- Height: `md` 44–46px; `lg` 50–52px; `sm` 36–40px
- Padding inline: `md` 18–20px; `lg` 22–24px
- Gap icon-to-label: 8px
- Stack gap between primary and ghost: 8–12px

### Typography

- Family: UI sans
- Weight: 600
- Size: `md` 0.95–0.97rem; `lg` 1.02rem; `sm` 0.88rem
- Line-height: 1

### Interaction behavior

- Only one Primary filled button per view.
- Loading keeps the control’s footprint; do not jump layout.
- Destructive requires confirmation (Modal) except where Trust docs specify an immediate reversible path.
- Keyboard: Enter/Space activate.

### Accessibility

- Accessible name from visible label.
- Loading: `aria-busy="true"`; announce result when complete.
- Destructive: clear verb (“Disconnect Gmail”), not vague “Confirm”.

### Examples

- Primary: “Get started”, “Continue to Google”, “Start watching these accounts”
- Secondary: “View accounts”
- Ghost: “Not now”, “Add an account manually instead”
- Destructive: “Disconnect Gmail”

---

## 2. Card

### Purpose

Group related content when grouping improves scanning or interaction. Default is **no card**.

### Variants

| Variant | Use |
|---------|-----|
| **Surface** | Default elevated group on warm page background |
| **Soft** | Recessed grouping inside a page (lower contrast) |
| **Interactive** | Whole-card hit target (rare; prefer explicit controls) |
| **Field** | Quiet Field atmospheric surface (deep pine) for hero/ambient moments |

### States

Default · Hover (interactive only) · Focus-within · Selected (when card is a selectable unit)

### Spacing

- Padding: 24px default; 32px for large onboarding surfaces; 20–22px in denser app panels
- Internal stack: 12–16px between title and body; 20–24px before actions
- Radius: 14px (surface), 12px (nested/soft)
- Avoid card-in-card nesting deeper than one level

### Typography

- Title: UI heading 1.05–1.15rem / 650, or display `title` when the card *is* the page answer
- Body: 0.95–1.05rem, ink-soft, max ~38rem

### Interaction behavior

- Prefer buttons/links inside cards over making the entire card clickable.
- If interactive, hover raises border emphasis lightly — not heavy shadow theater.

### Accessibility

- Interactive cards must be keyboard operable and have an accessible name.
- Do not trap focus inside ordinary content cards.

### Examples

- Home story surface containing “You’re good.”
- Accounts list container
- Activity evidence container

---

## 3. Section

### Purpose

Page-level structure: one job, one headline, usually one short supporting sentence. Sections create intentional whitespace between jobs.

### Variants

| Variant | Use |
|---------|-----|
| **Page** | Top-level app/marketing block |
| **Panel** | Secondary grouping under a primary story |
| **Split** | Two-column desktop / stacked mobile (story + aside) |
| **Strip** | Low-density multi-column explanation (landing promises) |

### States

Static content container (no interactive states of its own)

### Spacing

- Section gap: 32–48px
- Headline to lede: 10–14px
- Lede to content: 20–28px
- Strip columns gap: 24px desktop; stack on mobile

### Typography

- Section heading: UI 1.0–1.15rem / 650, or display when it is the primary answer
- Eyebrow (optional): 0.76rem, uppercase, +0.06–0.07em, pine-ink
- Supporting: body / body-sm, muted or ink-soft

### Interaction behavior

- Sections do not compete with the page’s primary CTA.
- Aside notes teach; they do not introduce a second filled primary.

### Accessibility

- Use real heading levels in order (`h1` → `h2`…).
- Landmark regions (`main`, `header`, `nav`) wrap sections appropriately.

### Examples

- “What’s happening” under Home waiting
- “Most recent work” under Home healthy
- Landing promise strip (what / different / trust)

---

## 4. Hero

### Purpose

Establish brand, primary answer, and one action in the first viewport. On marketing, brand is hero-level. On Home, the product answer (“You’re good.” / one ask) is hero-level.

### Variants

| Variant | Use |
|---------|-----|
| **Marketing** | Brand-first landing composition with Quiet Field visualization |
| **Home answer** | Dominant status sentence + supporting lede + optional low CTA |
| **Onboarding title** | Focused page title + lede above a single form or permission stack |

### States

Default · Ambient live (optional Quiet Field pulse when “working/watching”) · Attention (single signal when action required)

### Spacing

- Marketing vertical: generous (`space-7`–`space-8`)
- Home story padding: ~32px
- CTA cluster gap: 12–16px
- Meta row separated by 1px line + 16px top padding

### Typography

- Marketing brand: `display-xl`
- Home answer: `display-lg`
- Onboarding title: `display-md`
- Lede: 1.05–1.125rem, max 34–38rem
- Buttons: UI sans only

### Interaction behavior

- One primary CTA in marketing/onboarding heroes.
- Healthy Home may have **no** required CTA; secondary “View accounts” is allowed.
- Ambient motion must be subtle and respect reduced motion.

### Accessibility

- Single `h1` per page.
- Decorative field visuals are `aria-hidden` when purely visual.
- Live ambient status also exposed as text (“Working quietly”).

### Examples

- Landing: Mighty + promise + Get started + Quiet Field preview
- Home healthy: “You’re good.”
- Home waiting: “One step left for your first update”

---

## 5. Status Badge

### Purpose

Communicate account or system state in a scannable, honest label.

### Variants

| Variant | Meaning |
|---------|---------|
| **Current / Quiet** | Healthy, verified, no action |
| **Waiting** | In progress / dependency / setup |
| **Attention** | User action needed |
| **Review** | Optional decision pending |
| **Neutral** | Informational (e.g. “Reviewable history”) |

Optional leading status dot (same hue as text). Text is required.

### States

Default only (state is the variant). Do not animate badge color as alarm.

### Spacing

- Padding: 5–6px × 10–12px
- Radius: full pill
- Font size: 0.76–0.8rem
- Gap dot-to-label: 6px

### Typography

- UI sans, weight 650
- Sentence case labels: “Current”, “Needs Chrome”, “Review”, “Working quietly”

### Interaction behavior

- Badges are not buttons by default.
- If tappable, pair with explicit affordance and destination.

### Accessibility

- Do not rely on color alone; include text.
- Prefer visible words over icon-only status.

### Examples

- Header chip: “Working quietly”
- Account row: “Current” / “Needs Chrome” / “Review”

---

## 6. Trust Card

### Purpose

Reassure near commitment points without overclaiming. Short, precise, reversible framing.

### Variants

| Variant | Use |
|---------|-----|
| **Reassure** | Soft pine wash — “Nothing is connected yet.” |
| **Limit** | Waiting-soft wash — emphasize what Mighty will not do |
| **Consequence** | Neutral soft surface — what happens if the user continues |

### States

Default · Dismissible (optional, rare on first-run trust beats)

### Spacing

- Padding: 14–16px
- Radius: 12px
- Border: 1px matching wash family
- Stack below forms or before primary CTAs with 16–20px margin

### Typography

- Lead: 0.9–0.95rem / 650
- Body: 0.9rem / 400–500, pine-ink or ink-soft
- No display serif inside trust cards

### Interaction behavior

- Never compete with the primary CTA.
- Never invent certifications, audits, or unverifiable guarantees.
- Prefer precise limits over absolute slogans.

### Accessibility

- Not an alert unless content is time-critical; default is complementary region.
- If dismissible, keyboard-dismissible with accessible close name.

### Examples

- Signup: “Nothing is connected yet…”
- Review: “If you continue: Mighty will start watching…”

---

## 7. Permission Card

### Purpose

Earn informed consent before a sensitive system dialog (Gmail OAuth, provider sign-in preface). Answers why / what / limits / next / leave.

### Variants

| Variant | Use |
|---------|-----|
| **Preface stack** | Full pre-OAuth explanation (highest trust) |
| **Inline ask** | Compact Home/Accounts ask with role split |
| **Reconnect** | Repair path when access broke — calm, no blame |

### States

Default · Submitting / redirecting · Cancelled (return to calm empty/home)

### Spacing

- List rows: 12px gap
- Row padding: 14–16px
- Limits row visually distinct (waiting-soft)
- Primary CTA block below stack: 20–24px margin-top

### Typography

- Page title: `display-md`
- Row title: 0.94rem / 650
- Row body: 0.9rem muted
- Eyebrow: “Informed consent · Before Google”

### Interaction behavior

- Explain **before** system permission.
- Sole filled CTA is the continue-into-system action (“Continue to Google”).
- “Not now” / manual path remain first-class ghost actions.
- Do not surprise with OAuth on page load.

### Accessibility

- List semantics for explanation rows.
- Limits must be readable without color (heading text “What is not accessed”).
- Announce redirect state if there is a waiting beat.

### Examples

- Connect Gmail preface with why / accessed / not accessed / stored / next / disconnect
- Provider sign-in: “You’ll sign in on [Provider]. Mighty does not sign in as you.”

---

## 8. Timeline

### Purpose

Reviewable evidence of meaningful events — not a developer system log.

### Variants

| Variant | Use |
|---------|-----|
| **Activity feed** | Default evidence list |
| **Grouped by day** | When multi-day history needs scanning help |
| **Compact** | Embedded “most recent work” on Home |

### Event kinds

| Kind | Meaning |
|------|---------|
| **You authorized** | User consent / confirmation |
| **Completed work** | Autonomous verification/update that finished |
| **Lifecycle** | Discovery found, enrolled, disconnected, etc. |
| **Needs you** | Attention item history (if shown) |

### States

Default · Empty (use Empty State) · Loading skeleton (calm, no fake events)

### Spacing

- Item padding: 16–18px vertical
- Divider between items
- Mark size: 36px
- Mark-to-copy gap: 16px

### Typography

- Kind label: 0.72rem uppercase / 700 / muted
- Title: 0.98rem / 650
- Body: 0.9rem muted
- Time: 0.8rem meta

### Interaction behavior

- No urgency theater on historical items.
- Optional deep link to Accounts detail — quiet text link.
- Do not invent events to fill space.

### Accessibility

- Ordered list or article sequence with headings.
- Time as `<time datetime>`.
- Kind text required (color marks alone insufficient).

### Examples

- “Gmail connected” (You authorized)
- “American Express verified” (Completed work)
- “Found 4 account candidates” (Lifecycle)

---

## 9. Account Card / Account Row

### Purpose

Represent one watched or suggested account with honest axes: found ≠ watching ≠ logged in ≠ current.

### Variants

| Variant | Use |
|---------|-----|
| **Row** | Default Accounts list unit |
| **Compact** | Home watched list |
| **Selectable** | Discovery review with toggle |
| **Suggestion** | Not watching yet / needs decide |

### States

Current · Waiting · Attention · Review · Selected · Disabled (rare)

### Spacing

- Row padding: 16–18px vertical; 20–22px horizontal in list container
- Grid (desktop): identity · balance · status · action
- Mobile: stack identity → balance/status → action
- Monogram: 40px, radius 11px
- Identity gap: 12–14px

### Typography

- Name: 1.0rem / 650
- Evidence/meta: 0.84rem muted
- Balance: tabular nums, 1.05rem / 700
- Status via Status Badge

### Interaction behavior

- Row actions are explicit (“Details”, “Decide”, “Sign in”).
- Selectable rows use checkbox/switch with clear selected wash.
- Never show fake balances while unverified.

### Accessibility

- Toggle labels include account name.
- Status text + badge.
- Interactive controls tabbable independently of row text.

### Examples

- Amex current with points balance
- Chase suggestion with “Review”
- Uncertain United match, unchecked by default

---

## 10. Empty State

### Purpose

Teach, reassure, explain future value, and offer one action. Empty is onboarding by other means — never “No items.”

### Variants

| Variant | Use |
|---------|-----|
| **First use** | Area not yet unlocked |
| **All clear adjacent** | Nothing needs you (may be success, not emptiness) |
| **No results** | Honest discovery miss |
| **Error / unavailable** | Calm failure with recovery path |

### Anatomy

1. Optional mark (icon)
2. Title
3. Teaching / reassurance body
4. Future value sentence
5. One primary action (or none if intentional all-clear)

### States

Default · Loading (prefer skeleton over empty flash)

### Spacing

- Mark: 48px box, 14px radius, pine-soft
- Title to body: 8–10px
- Body max width: ~28–32rem
- Action margin-top: 16–20px

### Typography

- Title: 1.1–1.15rem (UI or soft display)
- Body: 0.95rem muted

### Interaction behavior

- Single path forward.
- No guilt copy, streaks, or fake urgency.

### Accessibility

- Not announced as an error unless it is an error variant.
- Action is a real button/link.

### Examples

- “Home stays quiet on purpose…”
- Discovery none found → manual add + optional rescan

---

## 11. Modal

### Purpose

Focus a single decision without inventing a second product. Use sparingly — prefer full pages for first-run trust prefaces.

### Variants

| Variant | Use |
|---------|-----|
| **Confirm** | Destructive or high-impact confirmation |
| **Inform** | Short necessary explanation (avoid for Gmail first ask — use page) |
| **Dismissible help** | Optional detail (“Learn how discovery works”) |

### States

Closed · Open · Closing · Busy (confirm in progress)

### Spacing

- Max width: 480–560px
- Padding: 24–32px
- Action row gap: 8–12px; actions right-aligned on desktop, stacked on mobile
- Scrim: warm dim, not pure black theater

### Typography

- Title: display-md or UI 1.25rem / 650
- Body: 1.0rem ink-soft
- Buttons: UI sans

### Interaction behavior

- Trap focus while open; restore focus on close.
- Esc closes dismissible modals; confirm destructive explicitly.
- Do not open permission system dialogs from a modal without preface content first.
- One primary action inside.

### Accessibility

- `role="dialog"` + `aria-modal="true"` + labelled by title.
- Initial focus on primary action or first meaningful control.
- Announce title on open.

### Examples

- Confirm disconnect Gmail
- Confirm stop watching an account

---

## 12. Progress Stepper

### Purpose

Show named, understandable progress for multi-step journeys and discovery. Prefer this over an indefinite hero spinner.

### Variants

| Variant | Use |
|---------|-----|
| **Onboarding rail** | Compact 1–2–3 above titles |
| **Discovery track** | Vertical named steps with evidence lines |
| **Horizontal** | Short flows (2–4 steps) on wide layouts |

### States (per step)

Upcoming · Live / current · Done · Error (rare, calm)

### Spacing

- Rail chips: 22px circle; gap 8px
- Discovery steps: 14px vertical padding; mark 22px
- Live step may soft-pulse mark (reduced-motion: static)

### Typography

- Step title: 0.95rem / 650 when live
- Meta: 0.84rem muted
- No fake percentages

### Interaction behavior

- Advance only on real progress (or honest prototype simulation).
- Allow leaving during long work with reassurance that progress continues when true.
- Error step explains recovery, not blame.

### Accessibility

- Expose current step to AT (`aria-current="step"`).
- Use `aria-live` polite for step changes during discovery.
- Color not the only done/live cue (checkmarks / text).

### Examples

- Create account → Welcome → How it works
- Gmail connected → Checking senders → Matching → Preparing results

---

## 13. Navigation

### Purpose

Orient without screaming “dashboard.” Navigation is quiet infrastructure; Home answers the product question.

### Variants

| Variant | Use |
|---------|-----|
| **Marketing header** | Brand · text links · one primary |
| **App header** | Brand · Home/Accounts/Activity · status chip |
| **Mobile app nav** | Full-width segment row under brand |

### States

Default · Active route · Hover/focus · Sticky (app header)

### Spacing

- App header min-height: 64px
- Nav item padding: 8px 14px
- Active: soft pine fill + thin underline cue
- Avoid heavy black pill clusters

### Typography

- Brand wordmark: UI 1.12rem / 700
- Nav items: 0.92rem / 600
- Status chip: Status Badge component

### Interaction behavior

- Active route always visible.
- Do not place competing filled CTAs in app nav.
- Marketing primary CTA routes to signup/start only.

### Accessibility

- `nav` landmark with name.
- Current page: `aria-current="page"`.
- Skip link to main content in production implementation.

### Examples

- App: Home | Accounts | Activity + “Working quietly”
- Marketing: How it works · Sign in · Get started

---

## 14. Form Controls

### Purpose

Collect only what is needed for the current job. Signup is identity, not surveillance.

### Variants

| Control | Use |
|---------|-----|
| **Text input** | Email, password, short text |
| **Text area** | Rare long notes |
| **Checkbox** | Multi-select agreements (avoid dark patterns) |
| **Switch / toggle** | Watch / don’t watch in review |
| **Select** | Constrained choices |
| **Helper text** | Quiet clarification under fields |
| **Error text** | Specific, calm, adjacent |

### States

Default · Hover · Focus · Filled · Error · Disabled · Read-only

### Spacing

- Label above field: 6–8px
- Field min-height: 44–46px
- Field radius: 10px
- Stack gap between fields: 16px
- Helper/error margin-top: 6px

### Typography

- Label: 0.86–0.9rem / 600 ink-soft
- Value: 1.0rem ink
- Helper/error: 0.84rem muted / danger

### Interaction behavior

- Focus ring pine.
- Errors explain how to fix; no shame language.
- Password fields never imply provider password storage for discovery.
- Do not request Gmail/Chrome inside signup forms.

### Accessibility

- Every control has a visible `<label>` (or `aria-label` only when visually adjacent UI already names it — prefer visible).
- Errors linked via `aria-describedby`.
- Autocomplete attributes set appropriately.

### Examples

- Create account email/password + helper “Used only to sign into Mighty…”
- Review switches labeled “Watch American Express”

---

## 15. Toast

### Purpose

Brief, non-blocking confirmation of a completed action. Not for permission asks or primary teaching.

### Variants

| Variant | Use |
|---------|-----|
| **Success** | Quiet confirmation |
| **Info** | Neutral notice |
| **Attention** | Needs awareness without trapping |
| **Error** | Recoverable failure summary |

### States

Entering · Visible · Exiting · Action available (optional single undo)

### Spacing

- Padding: 12px 14px
- Radius: 12px
- Offset from viewport edge: 16–24px
- Max width: ~360px

### Typography

- 0.9–0.95rem / 500–600
- Optional action: 0.9rem / 650

### Interaction behavior

- Auto-dismiss ~4–6s for success/info; persist errors until dismissed.
- Never steal focus unless an action is required.
- Do not stack many toasts; queue calmly.
- Prefer inline Trust/Banner for first-run explanations.

### Accessibility

- `role="status"` for success/info; `role="alert"` only for urgent errors.
- Do not auto-dismiss faster than readable.

### Examples

- “Gmail disconnected”
- “Stopped watching United MileagePlus”

---

## 16. Banner

### Purpose

Persistent or semi-persistent page-level notice that frames status without becoming the whole product.

### Variants

| Variant | Use |
|---------|-----|
| **Info** | Neutral ongoing context |
| **Waiting** | Setup/dependency still open |
| **Attention** | Action needed, if not already the Home hero |
| **Success** | Rare persistent success (prefer Home all-clear instead) |

### States

Default · Dismissible · With action

### Spacing

- Padding: 12px 16px
- Radius: 12px when inset; full-bleed optional under header
- Action gap: 12px

### Typography

- Body: 0.92–0.95rem
- Action: Button ghost/secondary sm

### Interaction behavior

- Do not duplicate the Home hero ask as a second banner CTA.
- Dismiss remember only when safe (don’t hide unpaid attention forever without a destination).

### Accessibility

- Region label (“Notification”).
- Color + icon/text.
- Dismiss button named.

### Examples

- “Mighty in Chrome isn’t set up yet” with link to setup (only if Home isn’t already owning that ask)
- Re-auth needed for a single provider on Accounts

---

## Composition rules

1. **Home owns interrupts.** Account rows and banners may point, but should not invent a second primary filled CTA for the same ask.
2. **Permission before system UI.** Permission Card / preface page precedes OAuth or OS prompts.
3. **Empty teaches.** Never ship Empty State with only “No data.”
4. **Evidence over magic.** Timeline and Account meta explain why.
5. **Surfaces are scarce.** Prefer Section + dividers; Card when grouping or interaction needs a container.
6. **No unsupported claims.** Components must not present fake audits, partner logos, or unverifiable security slogans.

---

## Component inventory checklist

| Component | Trust job |
|-----------|-----------|
| Button | Clear commitment |
| Card | Earned grouping |
| Section | One job per region |
| Hero | Brand / primary answer |
| Status Badge | Honest state |
| Trust Card | Reassure + limits |
| Permission Card | Informed consent |
| Timeline | Reviewable evidence |
| Account Card | Portfolio control |
| Empty State | Teach + one path |
| Modal | Focused confirmation |
| Progress Stepper | Visible process |
| Navigation | Quiet orientation |
| Form Controls | Least privilege input |
| Toast | Brief confirmation |
| Banner | Persistent framing |

---

## Document control

| Field | Value |
|-------|-------|
| Version | V1 |
| Nature | Production component contracts (docs only) |
| Implementation | Not started in this document |
| Frozen reference | Trust V1 prototype (do not modify as part of this work) |
