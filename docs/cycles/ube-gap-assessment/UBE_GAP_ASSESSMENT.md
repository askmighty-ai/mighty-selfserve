# UBE Gap Assessment — Reopened (State Model)

**Date:** 2026-07-29 (reopen)  
**Prior assessment:** 2026-07-29 visual/nav primacy → cycle `ube-one-daily-product` (chrome Accept; deploy optional)  
**Milestone:** [Unified Beta Experience](../../milestones/MILESTONE_UNIFIED_BETA_EXPERIENCE.md)  
**Evidence:** Founder testing after chrome unification — **primary remaining coherence failure is State Model**, not visual language  
**Nature:** Perception assessment + cycle proposal — **no implementation in this reopen**

---

## Founder override (binding)

> The primary remaining coherence failure is no longer visual — it is the **State Model**. The dashboard fails to preserve and communicate the user's recent actions, causing the product to appear **stateless and untrustworthy** (for example, immediately returning to “Sign in to American Express” after the Founder has just visited Amex).  
> **Suspend further visual migration** until the dashboard becomes a **truthful narrator of the user's journey**.

Success for the next repair:

> Every meaningful action **advances the story**, never **resets it without explanation**, and always **explains why the next action is being requested**.

---

## Method

Same six UBE gates. Re-score after Founder evidence. Choose **one** repair cycle. No backlog.

---

## Gate 1 — Visual language

| Field | Assessment |
|-------|------------|
| **Current state** | Authenticated Application chrome unified on production customer path (`ube-one-daily-product` Independent Audit Accept). Landing / login / popup still multi-generation. |
| **Largest remaining gap** | Marketing + Authentication doors still dual — **deferred**. |
| **Why it matters (now)** | Founder judges remaining coherence failure is **not** visual. Continuing visual migration would optimize the wrong wound. |
| **Class** | **Suspended** for further migration until narrative State Model repair. Residual door LD remains named, not active. |

---

## Gate 2 — Vocabulary

| Field | Assessment |
|-------|------------|
| **Current state** | Session 1 worst strings cleared; monitor/watch/manage/connect still mixed. |
| **Largest remaining gap** | Glossary freeze — softer than journey amnesia. |
| **Class** | LD residual — **deferred**. |

---

## Gate 3 — Interaction model

| Field | Assessment |
|-------|------------|
| **Current state** | Visit Amex home-base shipped (new tab, orientation, stay note, focus poll). Audit Accept. |
| **Largest remaining gap** | Interaction chrome works; **state does not advance with the interaction**. Visit is navigation + copy; Home re-derives Sign-in/Visit from AuthTruth/PSS without remembering the user’s act. |
| **Why it matters** | Interaction without narrative memory teaches “Mighty forgot me.” |
| **Class** | Primary path **OR closed** for tab behavior; **coupled failure** to State Model for trust. |

---

## Gate 4 — State model (**weakest**)

| Field | Assessment |
|-------|------------|
| **Current state** | Lifecycle honesty improved for terminal Extracting / no-account-data. Attention + Home compose from evidence each poll. Visit click writes **no durable user-journey fact**. |
| **Largest remaining gap** | **No user-visible narrative state.** Meaningful acts (Visit Amex, return to Mighty, wait for Chrome) do not advance a story the dashboard can tell. Pre-visit “Sign in…” reappears as if the Visit never happened whenever extension evidence has not yet moved. Nested N1 (“Unable to verify”) remains a secondary contradiction. |
| **Mechanism (engineering)** | Visit = navigation affordance; Home CTA = evidence projection (`AuthTruth` → `AUTH_BLOCKER` / WAITING handoff). Poll is read-only; no `visit_started` ledger; mid-login-page evidence can re-stamp signed-out while the user is mid-flow. |
| **Why it matters** | A product that forgets the user’s last act is **untrustworthy** even when chrome is one family. Fails Gate 4 perception **and** exit-test “When is Mighty working?” |
| **Smallest material improvement** | Introduce a **user-visible narrative state model** on Home: recent meaningful actions advance the story; resets require explanation; next ask always carries why. |
| **Class** | **Learning Distorter** (Founder-promoted; open) |

### Narrative state model (definition for repair)

**Event-based** (amended before engineering):

1. Persist **user-action events** and **system-observation events** separately.  
2. Every dashboard narrative state **identifies which event(s)** it reflects.  
3. Compose the story from the event stream — do not cold-reproject evidence alone.  
4. Meaningful events advance the story; unexplained resets fail; next asks cite justifying events.  
5. Independent Audit must cover **interruption scenarios** (reload, focus/no progress, late observation, abandoned Visit, contradictory needs-login) — see [../ube-journey-narrator/CYCLE_CHARTER.md](../ube-journey-narrator/CYCLE_CHARTER.md) and [AUDIT_BRIEF.md](../ube-journey-narrator/AUDIT_BRIEF.md).

User-visible beats remain: act acknowledged → waiting with reason → progress → terminal with continuity → explicit non-progress — each bound to named events.

---

## Gate 5 — Navigation model

| Field | Assessment |
|-------|------------|
| **Current state** | Production Home ↔ Accounts share MDS shell (chrome seam closed on customer path). |
| **Largest remaining gap** | Discover still onboarding chrome — deferred with visual suspend. |
| **Class** | Material LD largely closed for authenticated daily path; further nav chrome work **suspended**. |

---

## Gate 6 — Mental model

| Field | Assessment |
|-------|------------|
| **Current state** | Claims “watches”; lived journey still setup-heavy. |
| **Largest remaining gap** | Stateless Sign-in re-ask after Visit teaches “Mighty is a wizard that forgets,” blocking “works quietly / all the time.” |
| **Class** | LD — **unblocked primarily by State Model repair**, not more chrome. |

---

## Overall assessment (revised)

| Question | Answer |
|----------|--------|
| **Weakest gate?** | **State model** — journey amnesia / cold CTA reset after meaningful user action. |
| **Gate that most limits “one premium product”?** | **State model** (trust). Visual dual-system LD on daily authenticated chrome is addressed; further visual migration suspended. |
| **Single repair cycle with largest coherence gain?** | **Truthful journey narrator on the dashboard** — user-visible narrative state; Visit and sibling meaningful acts advance the story. |

### Why not continue visual migration?

Founder testing: primary failure is no longer visual. Chrome unification was the prior bet; trust now fails on **memory and explanation**. Visual door work waits.

### Why not nested N1 alone?

N1 is a label contradiction. Founder’s exemplar is **journey reset** (Sign in again after Visit). Narrative continuity is the parent repair; N1 aligns under the same “one story” rule.

---

## Recommended repair cycle (only one)

**Slug:** `ube-journey-narrator`  
**Outcome:** After a meaningful Home action (especially Visit / Sign-in to Amex), returning to Mighty never feels like amnesia: the dashboard narrates what just happened, what Mighty is waiting for, and why any next ask exists.

**Charter / plan:** [../ube-journey-narrator/](../ube-journey-narrator/) — **Proposed; awaiting Founder Accept before implementation.**

**Explicitly not this cycle:** Visual surface migration; landing/login/popup redesign; new providers; inventing Amex balances; unrelated cleanup.

**Prior cycle:** `ube-one-daily-product` — chrome Accept; **further visual migration suspended** per this reopen.
