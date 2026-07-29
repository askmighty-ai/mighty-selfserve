# Milestone — Unified Beta Experience

**Status:** Current (open)  
**Type:** Experience coherence gate (Founder-facing) — **not** a capability milestone  
**Audience:** Founder, Independent Auditor, delivery agents optimizing the existing product  
**Opened:** 2026-07-29  
**Related:** [ROADMAP.md](../ROADMAP.md) · [BETA_MLP.md](../BETA_MLP.md) · [MIGHTY_ONE_SENTENCE.md](../MIGHTY_ONE_SENTENCE.md) · [Founder Vision](../founder-vision/FOUNDER_VISION.md) · Decision [2026-07-29-unified-beta-experience.md](../product/decisions/2026-07-29-unified-beta-experience.md)

---

## Why this milestone exists

Capability milestones M6–M12 shipped durable product power. Founder beta sessions then showed that **power without unity reads as multiple products**.

This milestone exists to make the *existing* end-to-end experience feel inevitable **before** Mighty adds significant new capabilities or providers.

It is intentionally **feature-neutral**.

| In scope | Out of scope |
|----------|--------------|
| Perception of one product | New providers |
| Coherence of what already ships | New AI capabilities |
| Release gates below | New dashboards / analytics |
| Independent first-time user experience | New product capabilities of any kind |

Success is **not** page-level consistency. Success is the user's belief that they are inside one coherent, premium product from landing through daily use.

---

## Success criterion

> **A first-time user believes they are using one coherent, premium product from the first landing page through daily use.**

Notice what is missing: CSS, design-system names, routes, components. Those are implementation details. This milestone judges **perception**.

---

## Acceptance criteria (release gates)

All six must pass. Closing a subset of surfaces does **not** close the milestone.

### 1. One visual language

A user should never think:

> "This looks like a different application."

Must hold across:

- landing
- authentication
- onboarding
- home / daily use
- accounts
- settings
- extension UI

**Fail if:** any step in the first-run or return journey feels like a different product generation.

### 2. One vocabulary

No competing terminology for the same idea.

Do not alternate among words such as:

- monitor
- watch
- manage
- extract
- discover
- connect
- configure

unless they intentionally mean different things.

Every important word has exactly one meaning in the product.

**Fail if:** a careful first-time user cannot tell whether two labels describe the same action or two different ones.

### 3. One interaction model

The user should understand:

> Mighty is where I live.

Provider sites are temporary. Everything returns to Mighty.

**Fail if:** the journey trains the user to live inside a provider, or to treat Mighty as a setup wizard that ends when the provider opens.

### 4. One state model

No contradictory labels for overlapping situations.

Do not present overlapping states such as:

```text
Checking
Extracting
Unable to verify
Ready
Monitoring
```

as if they were unrelated truths for the same underlying condition.

Every screen derives from the same canonical lifecycle.

**Journey narration (Founder refinement, 2026-07-29):** The dashboard must also be a **truthful narrator of the user’s journey**. Meaningful actions advance a user-visible story; the product must not reset that story without explanation; every next ask must explain why. A cold identical CTA after a just-completed Visit (as if the Visit never happened) fails this gate even when lifecycle labels match across surfaces.

**Fail if:** two surfaces disagree about whether Mighty is working, blocked, or waiting — for the same account at the same moment — **or** if Home appears to forget a meaningful action the user just took.

### 5. One navigation model

Every page should feel like moving around the **same** application.

Never a sequence of:

```text
onboarding app → admin app → dashboard app → settings app
```

**Fail if:** chrome, orientation, or wayfinding resets the sense of product between major steps.

### 6. One mental model

The user should be able to explain Mighty in one sentence, in the spirit of:

> "Mighty quietly watches the accounts I already have and tells me when something important changes."

If they need three paragraphs, the product still is not coherent.

**Fail if:** after a complete first journey, an independent new user cannot state Mighty’s job without listing features or screens.

---

## Exit test (Founder release gate)

A new user — not the delivery agent — should be able to answer:

### Question 1

> What does Mighty do?

### Question 2

> When is Mighty working?

### Question 3

> If you closed your laptop now, what would Mighty continue doing?

Approximate passing answers:

| Question | Passing essence |
|----------|-----------------|
| What does Mighty do? | Watches my accounts. |
| When is Mighty working? | All the time. |
| If you closed your laptop…? | Keeps monitoring in the background. |

If answers drift into setup chores, provider-site work, or “I’m not sure,” the milestone is **not** done.

---

## How to use this milestone

1. **Governing gate.** Before introducing significant new capabilities or providers, optimize the existing end-to-end experience until these criteria pass.  
2. **Founder judgment.** Criteria are perceptual. Engineering evidence (screenshots, audits, cycle reports) supports review; it does not replace the exit-test conversation with a first-time user.  
3. **Independent Audit.** Delivery claiming this milestone complete must survive Independent Audit against these gates — not against a list of migrated stylesheets.  
4. **Feature freeze intent.** Work that adds providers, AI, dashboards, analytics, or other new capabilities is **out of scope** for closing this milestone. Such work waits until the exit test passes (or the Founder explicitly overrides).

---

## Explicit non-goals

- Shipping a new surface “family” and declaring victory while another family still breaks the one-product feeling  
- Measuring success by route count migrated or components renamed  
- Expanding provider coverage to compensate for a confusing first journey  
- Adding explanatory copy that papers over contradictory states

---

## Relationship to prior work

- Capability roadmap **M6–M12** remains complete and authoritative for what Mighty *can* do.  
- Visual surface-family migration and related cycles are **supporting work** toward these gates — not substitutes for them.  
- [BETA_MLP.md](../BETA_MLP.md) still defines the smallest lovable beta slice; this milestone defines when that slice feels like **one product**.

---

## Completion record

| Field | Value |
|-------|-------|
| **Status** | Open |
| **Gap assessment** | [docs/cycles/ube-gap-assessment/UBE_GAP_ASSESSMENT.md](../cycles/ube-gap-assessment/UBE_GAP_ASSESSMENT.md) (2026-07-29) |
| **Proposed next cycle** | [ube-one-daily-product](../cycles/ube-one-daily-product/CYCLE_CHARTER.md) — awaiting Founder Accept |
| **Closed when** | All six acceptance criteria pass **and** exit-test answers match the passing essence for an independent first-time user |
| **Closed by** | Founder Accept after Independent Audit (or explicit Founder override) |
| **Living report updates** | Append evidence of exit-test sessions and gate scores here when closing; do not convert this file into an implementation backlog |
