# Living Calm V1

**Status:** Design exploration (prototype only)  
**Branch:** `feat/living-calm-v1`  
**Prototype:** [`prototypes/living-calm-v1/`](../prototypes/living-calm-v1/)  
**Does not replace:** Trust V1 (`prototypes/trust_v1/`), production design system, or customer UI

---

## Objective

Evolve Mighty from a **polished interface** into a **memorable product**.

Trust V1 proved that calm, honest hierarchy, and Quiet Field atmosphere can earn confidence. Living Calm V1 asks the next question:

> Once people trust Mighty — will they *remember* it?

Memorable does not mean louder. It means a product that leaves a clear emotional afterimage: a field that feels watched without feeling watched, an answer that feels alive without feeling busy, and a personality that cannot be swapped for another fintech template.

---

## What this exploration is

| In scope | Out of scope |
|----------|--------------|
| Design rationale documents | Production Flask/templates |
| Standalone static prototype | Production design tokens / `mds-*` components |
| Quiet Field as primary metaphor | Migrating customer pages |
| Brand personality & hierarchy system | Replacing or editing Trust V1 |
| Three alternative Home concepts | Shipping behavior changes |

---

## Companion documents

| Document | Question it answers |
|----------|---------------------|
| [QUIET_FIELD_V2.md](QUIET_FIELD_V2.md) | What is the Quiet Field when it becomes the product metaphor, not a hero decoration? |
| [BRAND_PERSONALITY.md](BRAND_PERSONALITY.md) | Who is Mighty emotionally — and who must it never become? |
| [VISUAL_HIERARCHY.md](VISUAL_HIERARCHY.md) | How do four hierarchy levels reduce visual competition? |
| [HOME_CONCEPTS.md](HOME_CONCEPTS.md) | What are three viable Home experiences under Living Calm? |

---

## The problem Living Calm solves

Trust V1 can still read as “a very good dashboard with nicer atmosphere.” The risk:

1. **Calm without character** — silence reads as empty SaaS, not presence.
2. **Metaphor as chrome** — Quiet Field appears on landing, then disappears into cards.
3. **Equal visual weight** — story, evidence, and ops compete even when copy is sparse.
4. **One Home orthodoxy** — we never tested whether extreme minimalism, full-field immersion, or operational calm is the right daily ritual.

Living Calm V1 treats these as design problems to explore, not production defects to patch.

---

## Design thesis

**Mighty’s product metaphor is a living Quiet Field.**

- Accounts rest as steady points.
- Mighty’s work is ambient motion beneath the field.
- Attention is a single rising signal — never a stack.
- When nothing needs you, the field’s stillness *is* the product.

Everything else — personality, hierarchy, Home concepts — exists to make that metaphor felt in daily use, not only on a marketing screen.

---

## Principles (Living Calm)

| Principle | Meaning |
|-----------|---------|
| **Metaphor first** | If the Quiet Field is removed and the UI still works the same, the metaphor failed. |
| **Memorable by restraint** | Character comes from what we refuse to show, not from decoration. |
| **One emotional job** | Each viewport creates one feeling; secondary jobs stay in lower hierarchy. |
| **Alive, not animated** | Motion proves state (watching / working / needs you). Decoration that loop for delight are forbidden. |
| **Hierarchy as kindness** | Four levels exist so the eye never has to negotiate. |
| **Exploration over migration** | Concepts compete in a prototype; production stays untouched. |

---

## Relationship to prior work

| Source | Role in Living Calm |
|--------|---------------------|
| [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) | North star: quiet co-pilot; Home answers “Does anything need me?” |
| [TRUST_BY_DESIGN.md](TRUST_BY_DESIGN.md) | Trust remains the floor; Living Calm builds memorability *on* trust |
| [MIGHTY_VISUAL_SYSTEM_V1.md](MIGHTY_VISUAL_SYSTEM_V1.md) | Inherited palette/type DNA; Living Calm may intensify field use without becoming production tokens |
| Trust V1 prototype | Frozen reference. Living Calm is a sibling exploration, not a patch. |
| Production design system | Untouched. No `static/design-system` or `mighty/design_system` edits. |

---

## Success criteria

Reviewers should be able to say:

1. “Quiet Field feels like the product — not a landing illustration.”
2. “I can name Mighty’s personality in one sentence.”
3. “I see four clear hierarchy levels; nothing fights the primary answer.”
4. “The three Homes feel like distinct rituals, not skin variants.”
5. “This is more memorable than Trust V1 without being louder.”

---

## Prototype map

See [`prototypes/living-calm-v1/README.md`](../prototypes/living-calm-v1/README.md).

| Surface | Intent |
|---------|--------|
| Quiet Field stage | Metaphor as primary experience |
| Personality stage | Emotional identity made visible |
| Hierarchy stage | Four levels demonstrated side by side |
| Home · Minimal Calm | Extreme reduction |
| Home · Living Quiet Field | Immersive field as Home |
| Home · Operational Calm | Calm with operational clarity |

---

## Major decisions (summary)

| Decision | Rationale |
|----------|-----------|
| Standalone prototype folder | Protects production DS, Trust V1, and customer UI from exploration churn |
| Docs before pixels | Rationale is the deliverable; UI illustrates it |
| Three Homes, not one | Memorability requires choice; premature convergence freezes the wrong ritual |
| Four hierarchy levels | Named levels are enforceable in review; “keep it calm” is not |
| No production components | Living Calm is a design argument, not a migration vehicle |
| Trust V1 frozen | Preserve the trust baseline as a comparison artifact |

Detailed rationale lives in the companion documents and in the prototype README / page notes.

---

## Document control

| Field | Value |
|-------|-------|
| Version | V1 |
| Nature | Exploration brief + index |
| Implementation | `prototypes/living-calm-v1/` only |
| Production | Forbidden until a separate adoption decision |
