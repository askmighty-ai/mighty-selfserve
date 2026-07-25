# Visual Hierarchy

**Status:** Design exploration (Living Calm V1)  
**Parent:** [LIVING_CALM_V1.md](LIVING_CALM_V1.md)  
**Related:** [QUIET_FIELD_V2.md](QUIET_FIELD_V2.md) · [HOME_CONCEPTS.md](HOME_CONCEPTS.md)

---

## Purpose

Polished UIs still fail when **everything is equally important**. Living Calm defines **four levels** so designers and reviewers can fail a screen for competition — not for vibes.

> Hierarchy is how Mighty reduces visual competition without deleting necessary truth.

---

## The four levels

| Level | Name | Job | User question |
|-------|------|-----|---------------|
| **L1** | **Signal** | The one answer | Does anything need me? |
| **L2** | **Story** | Make the signal believable | Why should I believe that? |
| **L3** | **Evidence** | Proof on demand / light proof | What supports this? |
| **L4** | **Chrome** | Orientation & footnotes | Where am I / what’s quietly true? |

Only **one L1** may exist in a viewport. L2 supports it. L3 and L4 must lose any fight with L1.

---

## Level specifications

### L1 — Signal

**What it is:** The dominant human sentence or field state.

Examples:

- “You’re good.”
- “Delta needs a sign-in.”
- “A Marriott cert expires in 12 days.”

**Visual rights:**

- Largest type (display)
- Highest contrast in the composition
- May own the Quiet Field’s emotional center
- May include **one** primary action when action is required

**Forbidden at L1:**

- Metric clusters
- Multiple CTAs
- Provider logo rows
- Secondary stories

**Decision:** Signal is typographic / field-state — not a badge and not a card title inside a grid.

---

### L2 — Story

**What it is:** One short supporting breath (1–2 sentences).

Examples:

- “Mighty verified your accounts and will watch quietly from here.”
- “Open Delta in Chrome when you can — Mighty will continue from there.”

**Visual rights:**

- Body / lede size
- Directly adjacent to Signal (usually beneath)
- Same emotional register as L1

**Forbidden at L2:**

- Lists of accounts
- Ops instructions beyond the single next step
- Marketing feature lists

**Decision:** If Story needs bullets, the Signal was wrong or Evidence was promoted too early.

---

### L3 — Evidence

**What it is:** Light proof that the Signal is earned — recent work, a focused account row, a single benefit fact.

**Visual rights:**

- Smaller type than Story
- Lower contrast
- May use quiet rows or field points — not hero cards
- Optional: omit entirely when silence is correct (Minimal Calm)

**Forbidden at L3:**

- Competing headlines
- Filled secondary CTAs
- “Also for you” opportunity walls when L1 is all-clear

**Decision:** Evidence proves; it does not pitch.

---

### L4 — Chrome

**What it is:** Brand mark, nav, status pill, timestamps, “Mighty in Chrome,” legal/footer whispers.

**Visual rights:**

- Meta size
- Muted ink
- Sticky but visually recessive
- Never introduces a second narrative

**Forbidden at L4:**

- Promo banners
- Notification badge theater
- Bright status that outshines L1

**Decision:** “Working quietly” is L4 — useful, never heroic.

---

## Competition rules

1. **One L1 per viewport.** If two elements could be L1, demote one.
2. **L3 cannot use display type.** Ever.
3. **L4 cannot use brand color fills** except for tiny status dots.
4. **When L1 is all-clear, do not invent L3 urgency.**
5. **Attention/Opportunity promote one item to L1** — siblings stay out of the viewport or drop to L3 at most.
6. **Whitespace separates levels**, not boxes. Borders are earned.

---

## Mapping to Quiet Field

| Field layer | Hierarchy level |
|-------------|-----------------|
| Rising signal / settled calm answer | L1 |
| Short copy in/just above field | L2 |
| Account points + quiet labels | L3 |
| App header, meta row, pulse indicator | L4 |

**Decision:** The field’s drama belongs to L1/L2. Points are L3 presence, not a second story.

---

## Mapping to Home concepts

| Concept | L1 | L2 | L3 | L4 |
|---------|----|----|----|-----|
| **Minimal Calm** | Dominant | One line | Absent or link-revealed | Minimal header/meta |
| **Living Quiet Field** | Answer in field | Whisper in field | Points as evidence | Thin top chrome |
| **Operational Calm** | Status line | Outcome copy | Recent work list | Ops strip + nav |

See [HOME_CONCEPTS.md](HOME_CONCEPTS.md).

---

## Review checklist

A screen fails Living Calm hierarchy if:

- [ ] Two filled buttons share the first viewport
- [ ] A meta timestamp is more salient than the Signal
- [ ] Evidence uses a larger type ramp than Story
- [ ] All-clear still shows attention-colored chrome
- [ ] Cards create a grid of equal stories
- [ ] Nav status pill visually competes with “You’re good.”

---

## Major decisions

| Decision | Rationale |
|----------|-----------|
| Exactly four named levels | Three collapses Story/Evidence; five invites bureaucracy |
| Signal must be singular | Matches manifesto: one question in five seconds |
| Evidence optional | Silence is a designed state (Minimal Calm) |
| Chrome never narrates | Prevents “dashboard header” personality |
| Enforceable checklist | Turns taste into reviewable rules |

---

## Document control

| Field | Value |
|-------|-------|
| Version | Living Calm V1 |
| Governs | Hierarchy demos + all Home concepts in the prototype |
| Production | Not applied to customer UI |
