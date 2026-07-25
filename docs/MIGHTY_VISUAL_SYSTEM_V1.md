# Mighty Visual System V1

**Status:** Canonical visual system for the Trust V1 product prototype  
**Audience:** Product, design, and anyone refining customer-facing Trust surfaces  
**Governs:** `prototypes/trust_v1/` (design prototype only — not production CSS)  
**Philosophy:** [TRUST_BY_DESIGN.md](TRUST_BY_DESIGN.md) · [FIRST_10_MINUTES.md](FIRST_10_MINUTES.md)

**Not this document:** Implementation plans for production Flask/templates, marketing brand guidelines beyond customer product UI, or illustration libraries.

---

## Mission

This system turns Trust by Design into a durable visual identity: recognizable, calm, and specific enough to represent Mighty for years — without looking like a generic SaaS dashboard or a clone of Stripe, Notion, Apple, or Linear.

Visual design serves trust. Decoration that compete with the primary question undermine it.

---

## Brand character

### Mighty should feel

| Trait | What it looks like |
|-------|--------------------|
| **Trustworthy** | Honest hierarchy, precise language near permissions, no hype chrome |
| **Calm** | Low visual arousal; soft atmosphere; no urgency theater when clear |
| **Intelligent** | Clear structure; evidence when claims matter; no decorative clutter |
| **Premium** | Careful type, restrained color, intentional depth — seriousness about money and accounts |
| **Warm** | Soft stone atmospheres and human-scaled type — not cold glass tech |
| **Restrained** | One primary action; few surfaces; silence as a designed state |
| **Quietly capable** | Living status cues without noise; polish that implies competence |

### Mighty must not feel

- Corporate or clinical (sterile grids, navy-and-gray enterprise chrome)
- Like a generic SaaS dashboard (stat strips, widget walls, equal card grids)
- Playful or childish (mascots, confetti, emoji as UI)
- Overly futuristic (neon, glow stacks, glassmorphism theater)
- Visually busy (nested rectangles, competing CTAs, badge clusters)
- Like a Stripe / Notion / Apple / Linear clone (their exact type ramps, purple gradients, pure-white card stacks, or hairline broadsheet layouts)

---

## Visual identity

### Metaphor: The Quiet Field

Mighty’s visual idea is **the Quiet Field** — a calm atmospheric plane where the accounts you already have rest as steady points of light. Mighty’s work is ambient motion *beneath* that field: a soft pulse, not a dashboard of widgets. When nothing needs you, the field stays still. When something does, a single warm signal rises.

This expresses:

| Meaning | Visual expression |
|---------|-------------------|
| Accounts watched quietly | Soft account “points” / marks in a low field — never surveillance eyes |
| Background work | Slow ambient pulse / horizon breath — not spinners as the hero |
| Clarity replacing complexity | One message, one action; surfaces only when grouping earns them |
| Controlled automation | Progress shown as named steps the user can understand |
| Reassurance | Warm stone atmosphere + “You’re good.” as a living calm state |

### Signature motif

- **Horizon band** — a soft horizontal atmospheric transition (field above / earth of content below)
- **Field pulse** — extremely subtle radial breathing behind healthy / waiting states
- **Account marks** — short monogram tiles (not brand logos as decoration); calm, squared-round
- **Signal rise** — when attention is required, one amber cue — never a stack of alerts

### Explicitly forbidden imagery

- Literal robots, chatbot mascots, or anthropomorphic assistants
- Surveillance eyes, camera shutters, or “watching you” metaphors
- Shield-heavy security badge walls
- Generic AI sparkles, neural nets, or particle magic
- Fake certifications, partner logo strips, or invented social proof

### Brand mark (prototype)

A compact squared mark with a quiet chevron — suggesting forward motion without aggression. Paired with the word **Mighty** in UI sans at medium-bold weight. On landing, **Mighty** is the hero-level brand signal; supporting copy never overpowers it.

---

## Typography

### Font families (prototype)

Free, already available via Google Fonts CDN (no paid licenses):

| Role | Family | Fallback |
|------|--------|----------|
| **Display / brand moments** | Fraunces (soft optical size) | Georgia, "Times New Roman", serif |
| **UI / body** | Plus Jakarta Sans | "Segoe UI", system-ui, sans-serif |

Fraunces carries warmth and permanence for “You’re good.” and major titles. Plus Jakarta Sans carries clarity for controls, forms, and dense reading without Inter/Roboto anonymity.

### Type ramp

| Token | Use | Size (desktop) | Weight | Line-height |
|-------|-----|----------------|--------|-------------|
| `display-xl` | Landing brand / hero calm state | clamp(2.75rem, 5vw, 3.9rem) | 600 | 1.08 |
| `display-lg` | Home primary answer | clamp(2.1rem, 3.4vw, 2.85rem) | 600 | 1.12 |
| `display-md` | Onboarding page titles | clamp(1.7rem, 2.5vw, 2.2rem) | 600 | 1.15 |
| `title` | App page titles (Accounts, Activity) | 1.75rem | 600 (display) | 1.2 |
| `heading` | Section headings | 1.05–1.15rem | 650 (UI) | 1.3 |
| `body` | Primary supporting copy | 1.05–1.125rem | 400–500 | 1.55 |
| `body-sm` | Secondary explanations | 0.95rem | 400–500 | 1.5 |
| `label` | Form labels, meta labels | 0.86–0.9rem | 600 | 1.35 |
| `meta` | Timestamps, ops, footnotes | 0.8–0.86rem | 500 | 1.4 |
| `button` | Button text | 0.95–1.02rem | 600 | 1 |

### Principles

- **Letter-spacing:** Display −0.02em; uppercase eyebrows +0.06em; body 0.
- **Maximum readable line width:** 34–38rem for ledes; ~42rem absolute max for long trust copy.
- **One display voice per viewport** — do not stack multiple competing display sizes.
- **Buttons never use display serif** — UI sans only, for operational clarity.
- **Mobile:** Scale display down via clamp; keep body ≥16px equivalent to avoid iOS zoom.

---

## Color system

Warm stone atmosphere + deep pine accent. Recognizable as Mighty; not purple-tech, not terracotta-cream cliché, not cold clinical blue-gray.

### Core palette

| Token | Hex | Role |
|-------|-----|------|
| `--bg` | `#F3EEE6` | Page background (warm stone) |
| `--bg-deep` | `#E7E0D4` | Atmospheric depth / lower wash |
| `--surface` | `#FFFCF7` | Elevated surface |
| `--surface-soft` | `#F7F1E8` | Recessed grouping |
| `--ink` | `#1C1915` | Primary text |
| `--ink-soft` | `#3F3A33` | Secondary text |
| `--muted` | `#6F675C` | Muted text / meta |
| `--line` | `#E2D9CC` | Default borders |
| `--line-strong` | `#CFC4B4` | Strong borders / inputs |
| `--pine` | `#1F5C4F` | Primary action / brand accent |
| `--pine-hover` | `#184A40` | Primary hover |
| `--pine-soft` | `#E3F0EC` | Trust / reassurance soft fill |
| `--pine-ink` | `#163E36` | Text on soft pine |
| `--success` | `#2F6B45` | Success / current |
| `--success-soft` | `#E5F2E9` | Success fill |
| `--waiting` | `#9A6A1F` | Waiting / in progress |
| `--waiting-soft` | `#F7EDD8` | Waiting fill |
| `--attention` | `#9B4A2E` | Attention needed (warm, not alarm) |
| `--attention-soft` | `#F8E8E1` | Attention fill |
| `--danger` | `#8F2F2F` | Danger / irreversible |
| `--danger-soft` | `#F7E6E6` | Danger fill |
| `--focus` | `rgba(31, 92, 79, 0.35)` | Focus ring |
| `--field` | `#243B36` | Quiet Field deep (hero / ambient) |
| `--field-mid` | `#2F5A4E` | Field mid-tone |
| `--field-glow` | `rgba(232, 214, 176, 0.22)` | Soft horizon glow |

### Semantic mapping

| Meaning | Tokens |
|---------|--------|
| Page background | `--bg` → `--bg-deep` atmospheric wash |
| Elevated surfaces | `--surface` + light warm shadow |
| Primary / secondary / muted text | `--ink` / `--ink-soft` / `--muted` |
| Primary action | `--pine` fill, white label |
| Trust / reassurance | `--pine-soft` + `--pine-ink` |
| Success / current | `--success` + `--success-soft` |
| Waiting / in progress | `--waiting` + `--waiting-soft` |
| Attention needed | `--attention` + `--attention-soft` |
| Danger | `--danger` + `--danger-soft` |
| Borders | `--line` / `--line-strong` |
| Focus | 3px `--focus` ring on interactive controls |

### Contrast

- Body text on `--bg` / `--surface` meets WCAG AA for normal text.
- Primary buttons: white on `--pine`.
- Status badges never rely on color alone — include text labels (Current, Needs Chrome, Review).
- Links: `--pine` with underline on hover/focus for non-button links in dense copy.

---

## Spacing and layout

### Tokens

| Token | Value | Use |
|-------|-------|-----|
| `--space-1` | 4px | Micro |
| `--space-2` | 8px | Tight clusters |
| `--space-3` | 12px | Inline gaps |
| `--space-4` | 16px | Default component padding unit |
| `--space-5` | 24px | Card padding / section gaps |
| `--space-6` | 32px | Major section gaps |
| `--space-7` | 48px | Page-level breathing |
| `--space-8` | 64px | Hero / landing vertical |

### Layout widths

| Context | Max width |
|---------|-----------|
| Marketing / landing content | 1080px |
| Onboarding focused column | 520–720px (permission screens up to 720px) |
| Authenticated app | 1120px |
| Readable lede | ≤38rem |

### Structure

**Onboarding:** Centered stage, single column, progress rail, one primary CTA. Atmosphere from page background — not a floating “modal card” aesthetic on blank void.

**Authenticated app:** Sticky quiet header (brand · text nav · status). Main column with a dominant home story, then optional secondary grouping — not a widget dashboard.

**Mobile (~390px):** Single column; nav collapses to full-width segmented control; reduce horizontal padding to 1.25rem; keep primary CTA full-width where it is the page action.

### Whitespace rules

1. Whitespace separates **jobs**, not leftover gaps.
2. If a region feels empty, teach or reassure — do not add decorative cards.
3. Prefer one deep breath between sections over many small gutters.
4. Never leave a blank primary pane with only “No items.”

### Density

- Onboarding: medium-low — trust copy needs air.
- Home: low — the answer dominates.
- Accounts / Activity: medium — scannable rows, not spreadsheet compression.

---

## Components

### Navigation

- Marketing: brand left; quiet text links; one filled primary.
- App: brand · Home / Accounts / Activity as understated text segments (active = pine underline or soft fill — not heavy black pills that scream “dashboard”).
- Status chip right: quiet meta (Working quietly / Setup in progress).

### Buttons

| Kind | Treatment |
|------|-----------|
| Primary | Pine fill, white text, 999px radius or 12px — pick **12px** for quieter premium (less pill-SaaS) |
| Secondary | Surface + strong line, ink text |
| Ghost | Transparent, muted text, soft hover fill |
| Block | Full width on narrow onboarding |

One filled primary per page. Hover: darken pine 8–10%. Active: 1px press. Focus: ring.

### Cards / surfaces

Surfaces exist only for meaningful grouping (permission cluster, watched set, timeline). Prefer open layout + dividers over card-in-card nesting. No hero cards-on-cards.

### Status badges

Pill with text + optional dot. Variants: quiet/success, waiting, attention, neutral. Never color-only.

### Account rows

Monogram mark · name + evidence line · status · control. Selected state via soft pine wash + border — not heavy shadow.

### Progress indicators

Named steps with done / live / upcoming. Live step may use a soft pulse. Prefer this over a lone spinner.

### Trust callouts

Soft pine or waiting wash; short bold lead + one sentence. Used near permission and creation reassurance.

### Permission explanations

Labeled rows: Why / What you get / Limits / Next / Disconnect. Scannable; limits visually distinct (waiting-soft), never buried.

### Empty states

Icon mark (CSS/SVG line) · teach · reassure · future value · one action. Same discipline as onboarding.

### Timelines

Vertical marks · title · plain-language body · time. Group by day only when it aids scanning. Evidence tone, not log dump.

### Forms

Labels above fields; 12px radius inputs; strong focus ring; helper text in muted. Errors calm and specific (when present).

### Links

Pine; underline on hover/focus in paragraphs. Secondary actions as ghost buttons or quiet text links — never competing filled buttons.

---

## Motion principles

Motion communicates **state**, never decoration.

| Moment | Motion |
|--------|--------|
| Page enter | Soft rise 280–420ms, staggered ≤2 elements |
| Discovery | Step states advance; account marks can fade/slide in on review |
| Verification / waiting | Ambient field pulse (very low amplitude); live step pulse |
| Status change | Badge/text crossfade ≤200ms |
| Success / healthy | Field settles; optional single soft ease — no confetti |
| Background work | Continuous subtle pulse only when “working quietly” or scanning |

### Rules

- Duration 180–480ms; easing `cubic-bezier(0.22, 1, 0.36, 1)`.
- Respect `prefers-reduced-motion: reduce` — replace pulses with static indicators; keep opacity fades minimal or off.
- No parallax, bounce, or attention-grabbing loops on marketing claims.
- Prototype JS only — no frameworks.

---

## Iconography

**Style:** Simple 1.75–2px stroke, 24px optical grid, rounded joins. Monochrome pine/ink. One family only (inline SVG).

**Allowed:** Abstract geometric marks (check, mail, browser, horizon, list).  
**Avoid:** Emoji, mixed icon packs, filled skeuomorphic brand marks as chrome, shield stacks.

Account monograms are letter tiles, not third-party logo assets.

---

## Accessibility

1. **Keyboard focus** — visible ring on all interactive elements; logical tab order.
2. **Contrast** — AA for text and essential UI; status never color-only.
3. **Type size** — body ≥1rem; touch targets ≥44px where primary.
4. **Reduced motion** — honor `prefers-reduced-motion`.
5. **Semantic HTML** — landmarks, headings, lists, labels, buttons vs links correctly.
6. **Forms** — every input has a label; helpers tied by proximity/`aria-describedby` when needed.
7. **Status** — text labels; use `aria-live` for discovery step changes in the prototype.

---

## Page application map

| Screen | Visual emphasis |
|--------|-----------------|
| Landing | Quiet Field hero; brand-first; living calm preview of “You’re good.” |
| Create account | Professional form; visible reassurance callout; light commitment |
| Welcome | Short satisfaction; soft confirmation; single continue |
| How Mighty works | Scannable three-way split: does / you do / never does |
| Connect Gmail | Highest-trust layout; limits unmistakable; one primary CTA |
| Discovering | Named progress as magic-with-method |
| Review | Rewarding discovery; confidence tiers; clear confirm consequence |
| Home waiting | Active field; one required human step |
| Home healthy | Living calm; recent work; no action required |
| Accounts | Premium control surface — not a spreadsheet |
| Activity | Evidence hierarchy — not a system log |

---

## Anti-patterns (visual)

| Anti-pattern | Why it fails |
|--------------|--------------|
| Widget dashboard Home | Competes with “Am I good?” |
| Nested card stacks | Busy; feels like generic SaaS |
| Multiple filled CTAs | Ambiguity feels unsafe |
| Fake metrics / logos | Invented trust is worse than none |
| Indefinite spinner as hero | Silence reads as broken |
| Urgency red for routine waiting | Trains anxiety |
| Developer jargon in chrome | Breaks Trust by Design |

---

## Success criteria

This system succeeds when a reviewer can say:

1. “This looks like Mighty — not like another fintech template.”
2. “I understand what the product does from the landing field alone.”
3. “Permission screens feel careful, not extractive.”
4. “Home calm feels alive, not empty.”
5. “Desktop and mobile feel like the same product.”

---

## Document control

| Field | Value |
|-------|-------|
| Version | V1 |
| Nature | Visual design system for Trust prototype |
| Implementation target | `prototypes/trust_v1/` only |
| Production | Do not apply to Flask templates/CSS until a separate production design contract |
