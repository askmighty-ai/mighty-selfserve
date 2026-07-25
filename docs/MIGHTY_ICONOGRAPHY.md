# Mighty Iconography

**Status:** Canonical production iconography contracts (documentation only)  
**Audience:** Product, design, engineering  
**Governs:** Future customer-facing icons and marks  
**Depends on:** [MIGHTY_VISUAL_SYSTEM_V1.md](MIGHTY_VISUAL_SYSTEM_V1.md) · [TRUST_BY_DESIGN.md](TRUST_BY_DESIGN.md) · [MIGHTY_COMPONENT_LIBRARY.md](MIGHTY_COMPONENT_LIBRARY.md)

**Not this document:** Icon font files, SVG sprite implementation, CSS, or asset production.  
**Prototype note:** Trust V1 is frozen. This document defines the production icon language informed by that prototype’s Quiet Field identity.

---

## Mission

Icons in Mighty are quiet instruments of clarity. They orient, confirm, and differentiate state — they never perform, decorate, or invent trust.

If an icon can be removed without reducing understanding, remove it.

---

## Icon philosophy

### What icons should do

| Job | How |
|-----|-----|
| **Orient** | Mark a region’s purpose (mail, browser, evidence) without restating the headline |
| **Confirm** | Show done / current / authorized with a simple check or calm mark |
| **Differentiate** | Help scan event kinds and status alongside text labels |
| **Support empty teaching** | One small mark that signals “this area will matter,” not cuteness |

### What icons must not do

- Entertain, gamify, or mascot the product
- Imply surveillance, hacking, or AI mystique
- Replace status text
- Compete with the primary question on a screen
- Introduce a second visual language (mixed icon families)

### Character adjectives

Icons should feel: **calm, precise, warm-neutral, restrained, quietly capable.**

They should not feel: corporate clipart, playful stickers, neon-tech, skeuomorphic, or “fintech purple glow.”

---

## Construction system

### Grid

- Default optical canvas: **24×24**
- Live area: **20×20** (2px padding)
- Small: **16×16** (status inline only)
- Large / empty-state mark container: **48×48** (icon drawn at 24–28 optical inside)

### Line weights

| Context | Stroke |
|---------|--------|
| Default UI icons | **1.75px** |
| Dense / 16px icons | **1.5px** |
| Emphasis marks (rare) | **2px** |
| Hairline decorative rules | Not icons — use layout borders |

Stroke ends: **round**. Joins: **round**. Avoid sharp aggressive terminals.

### Corner radii

| Element | Radius |
|---------|--------|
| Icon stroke bends | Natural curve from round joins |
| Icon container (empty state, welcome mark) | **12–14px** |
| Account monogram tile | **11px** on 40px tile |
| Brand mark container | **7–8px** on ~26px mark |
| Status dot | Full circle |

Do not mix fully sharp geometric icons with bubbly rounded icons in the same UI.

### Color

Icons inherit semantic color from context:

| Context | Color |
|---------|-------|
| Default on surface | `--ink-soft` or `--pine` |
| On Quiet Field (dark) | Warm off-white at 85–92% |
| Success / done | `--success` |
| Waiting / live | `--waiting` or `--pine` |
| Attention | `--attention` |
| Muted / informational | `--muted` |
| On primary button | White |

Prefer **monochrome** icons. Duotone only if both tones are from the Mighty palette and contrast remains calm — default is single-tone.

Filled vs stroke:

- **Stroke** is default for UI icons.
- **Soft fill + stroke** allowed for status marks in timeline circles.
- Avoid heavy solid pictograms that feel like app-store category icons.

---

## Visual metaphors

Mighty’s parent metaphor is the **Quiet Field**: accounts as steady points; work as ambient pulse; attention as a single rising signal.

Icons should extend that language, not invent a new one.

### Approved metaphor directions

| Meaning | Prefer | Avoid |
|---------|--------|-------|
| Watching / monitoring | Horizon, steady point, calm pulse mark | Eye, camera, CCTV, binoculars |
| Protection / care | Simple check, calm enclosure (soft rounded square) | Shield walls, locks as default chrome, badges |
| Progress | Named step checkmarks, minimal circular arc | Indeterminate vanity spinners as the hero |
| Mail discovery | Envelope, simple inbox corner | Open letter with “AI stars” |
| Browser / Chrome setup | Simple window frame | Robot, puzzle piece clusters |
| Evidence / activity | List with check, small timeline notch | Terminal/log glyphs, gear storms |
| Success / all-clear | Check, settled point | Confetti, trophies, streaks |
| Attention needed | Single small mark / amber cue | Sirens, triangles everywhere, red floods |
| User control | Toggle/switch metaphor only in controls; “leave” as clear text | Chains, handcuffs, leash imagery |

### Account identity marks

- Use **monogram tiles** (2 letters) for providers in product lists.
- Do not rely on third-party logo assets as the design system’s icon language.
- Monograms are content marks, not UI icons — keep them geometrically consistent (size, radius, type weight).

### Brand mark

- Compact squared container + quiet chevron/arc suggesting forward motion without aggression.
- Pairs with the word **Mighty**.
- Never replace the wordmark on landing’s hero brand moment.

---

## Core icon set (production intent)

Documentation names only — no assets in this doc.

| Name | Intended use |
|------|----------------|
| `check` | Done, verified, success |
| `minus` / `dash` | Neutral lifecycle, unset |
| `info` | Informational lifecycle |
| `mail` | Gmail / mail discovery context |
| `window` | Mighty in Chrome / browser |
| `accounts` | Portfolio / list |
| `activity` | Evidence / timeline |
| `plus` | Add account |
| `close` | Dismiss modal/banner |
| `chevron-right` | Inline navigation affordance (sparingly) |
| `warning` | Rare attention/error (always with text) |
| `horizon-points` | Optional Quiet Field decorative motif (non-semantic, `aria-hidden`) |

Do not grow the set casually. New icons require a clear job not already served.

---

## Usage rules

### Placement

1. **Lead with words.** Icons support; they do not headline trust explanations.
2. **One mark per empty state.** Not an icon row.
3. **Timeline marks** distinguish event kind with shape + label, not color alone.
4. **Buttons** may include an icon only when it improves recognition (e.g. mail) — never for decoration.
5. **Navigation** prefers text labels; icons optional and always paired with text in production app nav.

### Density

- Home and onboarding: very few icons.
- Accounts/Activity: functional marks only.
- Marketing: Quiet Field visualization may use abstract points; no icon salad.

### Alignment

- Optical align icons to first line of adjacent text.
- Keep 8px gap between icon and label in buttons/lists.
- In 24px icons beside 0.95–1rem text, align to cap height optically.

### Motion

- Icons may pulse only for **live** progress (discovery step, working quietly).
- Motion communicates state; it does not sparkle.
- Honor `prefers-reduced-motion`: static equivalents required.

### Accessibility

- Decorative icons: `aria-hidden="true"`.
- Meaningful icons without visible text: accessible name required (prefer adding visible text instead).
- Status icons must accompany text labels (“Current”, “Needs Chrome”).
- Do not convey errors by icon color alone.

### Localization

- Prefer non-letter metaphors for UI icons.
- Account monograms may be Latin provider initials as content — product decision, not UI chrome.

---

## Anti-patterns

| Anti-pattern | Why it fails |
|--------------|--------------|
| Emoji as UI | Inconsistent, playful, platform-variable |
| Mixed icon packs | Breaks calm identity |
| Surveillance eye “watching” | Violates trust metaphor |
| Shield-lock stacks on every screen | Security theater |
| AI sparkles / stars | Overclaims intelligence; feels generic |
| Icon-only primary nav | Hurts clarity and accessibility |
| Animated decorative loops | Noise; undermines quiet competence |
| Provider logo collage in marketing | Clutter; partnership implications |

---

## Relationship to components

| Component | Icon role |
|-----------|-----------|
| Button | Optional leading stroke icon |
| Status Badge | Optional 6px dot (not a full icon) |
| Trust / Permission cards | Usually no icons; text hierarchy first |
| Timeline | Kind mark in 36px circle |
| Account Row | Monogram tile + optional row action icons |
| Empty State | One container mark |
| Progress Stepper | Check / live dot / empty circle |
| Toast / Banner | Optional leading status icon with text |
| Navigation | Text-first; icons secondary |

---

## Contribution checklist

Before adding an icon to the production set:

1. What user question does it clarify?
2. Is there already an icon that serves this job?
3. Does it match 24px grid, 1.75px stroke, round joins?
4. Does it avoid forbidden metaphors?
5. Is there a text label (or justified `aria-label`)?
6. Does reduced motion still communicate the state?

If any answer fails, do not add the icon.

---

## Document control

| Field | Value |
|-------|-------|
| Version | V1 |
| Nature | Production iconography contracts (docs only) |
| Assets | Not included in this document |
| Frozen reference | Trust V1 prototype (do not modify as part of this work) |
