# Decision: Suspend visual migration; State Model is next UBE repair

**Date:** 2026-07-29  
**Status:** Accepted (Founder directive — UBE assessment reopen)  
**Milestone:** [MILESTONE_UNIFIED_BETA_EXPERIENCE.md](../../milestones/MILESTONE_UNIFIED_BETA_EXPERIENCE.md)

## Context

`ube-one-daily-product` closed the authenticated chrome seam on the customer path. Founder testing then showed the primary remaining coherence failure is **State Model**: Home does not preserve or communicate recent user actions (e.g. cold “Sign in to American Express” after Visit Amex), so the product feels stateless and untrustworthy.

## Decision

1. **Further visual surface migration is suspended** until Home is a truthful narrator of the user’s journey.  
2. The next UBE repair cycle prioritizes a **user-visible, event-based narrative state model**: persist user-action and system-observation events separately; every dashboard state identifies which event(s) it reflects; every meaningful action advances the story; never resets without explanation; always explains why the next action is requested. Independent Audit must include interruption scenarios before Accept.  
3. Proposed cycle: `ube-journey-narrator` — implementation only after Founder Accept of the (amended) charter / reopened Executive Review.

## Consequences

- Do not open new visual/family migration cycles while narrative State Model is open.  
- Visit Amex interaction contracts remain; narrative continuity is additive.  
- Independent Audit for the next cycle judges journey continuity perception, not chrome file counts.
