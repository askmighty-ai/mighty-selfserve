# Home Concepts

**Status:** Design exploration (Living Calm V1)  
**Parent:** [LIVING_CALM_V1.md](LIVING_CALM_V1.md)  
**Related:** [QUIET_FIELD_V2.md](QUIET_FIELD_V2.md) · [VISUAL_HIERARCHY.md](VISUAL_HIERARCHY.md) · [HOME_V1.md](HOME_V1.md)

---

## Purpose

Production Home (V1B) and Trust V1 Home converge on a briefing pattern: greeting, featured story, recent work, ops strip.

Living Calm deliberately **forks three alternative rituals** so we can feel which memorability path is right — without migrating production.

> These are competing concepts, not progressive enhancements of one Home.

---

## Shared constraints (all three)

1. Answer **“Does anything need me?”** in five seconds.
2. Obey **four hierarchy levels** ([VISUAL_HIERARCHY.md](VISUAL_HIERARCHY.md)).
3. Keep **Quiet Field** present at least as a remnant ([QUIET_FIELD_V2.md](QUIET_FIELD_V2.md)).
4. Express **composed / warm / precise** personality ([BRAND_PERSONALITY.md](BRAND_PERSONALITY.md)).
5. Support three emotional states for review: **all clear**, **attention**, **opportunity**.
6. No production components; no Trust V1 file edits.

---

## Concept A — Minimal Calm

**Prototype:** `prototypes/living-calm-v1/home-minimal.html` (+ state query)

### Ritual

Open → read one sentence → leave.

### Composition

| Level | Content |
|-------|---------|
| L1 | “You’re good.” / one ask / one opportunity line |
| L2 | Single supporting sentence |
| L3 | None by default — “View accounts” as quiet text |
| L4 | Brand · minimal nav · last checked |

Field: thin horizon breath under the answer — present, not immersive.

### When it wins

- Users treat Home as a weather glance
- Any evidence on clear days feels like guilt / clutter
- Memorability comes from the *courage of emptiness*

### When it fails

- Users don’t believe Mighty did work (trust regression)
- Waiting/setup states feel abandoned
- Opportunity lacks enough fact to act

### Major decisions

| Decision | Rationale |
|----------|-----------|
| Hide evidence on all-clear | Tests whether silence can be the brand |
| Keep a field remnant | Prevents “blank Notion page” reading |
| No Recent Wins | Wins move to Accounts/Activity — Home stays a glance |
| One secondary text link only | Protects L1 |

---

## Concept B — Living Quiet Field

**Prototype:** `prototypes/living-calm-v1/home-living-field.html` (+ state query)

### Ritual

Open → enter the field → feel watched-without-watching → leave or act on one rising signal.

### Composition

| Level | Content |
|-------|---------|
| L1 | Answer living *in* a full-bleed field |
| L2 | Whisper copy in the field |
| L3 | Account points; optional soft label on focus |
| L4 | Thin translucent top chrome |

Field: primary canvas (edge-to-edge). Content cards are rejected in the first viewport.

### When it wins

- Metaphor becomes unforgettable
- All-clear feels *alive*, not empty
- Attention’s single rising signal is visceral

### When it fails

- Feels like marketing parked in the app
- Ops clarity suffers (harder to scan facts)
- Points misread as a data visualization

### Major decisions

| Decision | Rationale |
|----------|-----------|
| Full-bleed field as Home | Maximizes metaphor-first thesis |
| No inset hero card | Avoids “illustration in a dashboard” |
| Points = L3 evidence | Portfolio presence without rows |
| Motion only for state | Alive ≠ entertaining |
| Attention = one point rises | Embodies single-ask rule |

---

## Concept C — Operational Calm

**Prototype:** `prototypes/living-calm-v1/home-operational.html` (+ state query)

### Ritual

Open → status → confirm ops health → optionally scan recent work → leave or act.

### Composition

| Level | Content |
|-------|---------|
| L1 | Status-first signal |
| L2 | Outcome copy (no account-count hero) |
| L3 | Compact recent work (2–3 rows) |
| L4 | Nav + **ops strip** (Chrome / last checked / pending) as footnotes |

Field: atmospheric header band behind L1/L2 — then earth of content below (closer to Trust V1 / Home V1B, but stricter hierarchy).

### When it wins

- Power users want proof without a second page
- Dogfood / ops-minded reviewers need scanability
- Bridge from today’s Home to Living Calm without culture shock

### When it fails

- Slides back into dashboard density
- Memorability ≈ Trust V1 (insufficient evolution)
- Ops strip creeps into L2

### Major decisions

| Decision | Rationale |
|----------|-----------|
| Keep recent work visible | Belief requires light evidence for some users |
| Ops strictly L4 | Prevents Control Tower relapse |
| Field as band, not full page | Operational readability over immersion |
| Same one-CTA rule | Shared Living Calm constraint |

---

## State matrix

Each concept must express:

| State | L1 essence | Field behavior |
|-------|------------|----------------|
| **All clear** | You’re good. | Settled; optional soft pulse |
| **Attention** | One account needs you (e.g. sign-in) | Single amber rise |
| **Opportunity** | One concrete benefit waiting | Soft gold lift |

**Decision:** Same underlying attention model as product (one primary). Concepts only change presentation ritual.

---

## Comparison (reviewer’s table)

| Lens | Minimal Calm | Living Quiet Field | Operational Calm |
|------|--------------|--------------------|------------------|
| Memorability | High (absence) | Highest (metaphor) | Medium |
| Trust proof | Lowest on clear | Medium (points) | Highest |
| Ops clarity | Low | Medium | High |
| Risk | Emptiness | Marketing feel | Dashboard relapse |
| Closest ancestor | Extreme Home V1B | Quiet Field V2 pure | Trust V1 Home |

---

## Recommendation posture

Living Calm V1 **does not pick a winner**. The prototype’s job is to make the tradeoffs embodied and reviewable.

A later adoption decision should pick **one** daily ritual (or a hybrid with explicit rules), then — and only then — consider production implications in a separate project.

**Lean (non-binding):** Living Quiet Field for brand memory + Operational Calm’s L3 evidence rules as a hybrid candidate — but Minimal Calm must be felt before dismissing it.

---

## Out of scope

- Production Home (`mighty/home_*.py`) changes
- Attention ranking changes
- Accounts / Activity redesign
- Merging concepts into Trust V1 files

---

## Document control

| Field | Value |
|-------|-------|
| Version | Living Calm V1 |
| Prototype | `prototypes/living-calm-v1/home-*.html` |
| Production | Forbidden |
