# Executive Review — UBE Reopen (State Model)

**Package:** `docs/cycles/ube-gap-assessment/` (reopened) + proposed cycle `docs/cycles/ube-journey-narrator/`  
**Target Founder time:** ≤20 minutes  
**Implementation:** **None yet** — this review is the gate before engineering  
**Milestone:** [Unified Beta Experience](../../milestones/MILESTONE_UNIFIED_BETA_EXPERIENCE.md)  
**Supersedes:** Prior gap Executive Review that recommended `ube-one-daily-product` as next (chrome). That cycle’s chrome claim stands; **next** priority is State Model.

---

## 1. Headline

Founder testing shows the primary remaining coherence failure is **no longer visual**. It is the **State Model**: the dashboard does not preserve or communicate the user’s recent actions, so Mighty feels **stateless and untrustworthy** — e.g. immediately returning to **“Sign in to American Express”** after the Founder has just visited Amex.

**Further visual migration is suspended** until Home becomes a **truthful narrator of the user’s journey**.

---

## 2. What changed since the last UBE assessment

| Prior bet | Status |
|-----------|--------|
| Production Inter home vs MDS Accounts (Visual + Navigation LD) | Addressed on customer chrome path (`ube-one-daily-product` Independent Audit Accept) |
| Nested “Unable to verify” (State residual N1) | Still open — secondary |
| Visit Amex home-base (Interaction) | Shipped for tab/orientation; **does not advance narrative state** |

| New Founder signal | Implication |
|--------------------|-------------|
| Cold Sign-in CTA after Visit | Visit is navigation; Home re-projects AUTH_BLOCKER/WAITING from evidence with **no memory of the act** |
| Product feels untrustworthy | Gate 4 failure dominates Gate 1 for daily trust |

Full write-up: [UBE_GAP_ASSESSMENT.md](UBE_GAP_ASSESSMENT.md).

---

## 3. Six-gate snapshot (revised)

| Gate | Weakest remaining gap | Class |
|------|----------------------|-------|
| Visual language | Landing / login / popup dual systems | **Suspended** (further migration) |
| Vocabulary | monitor / watch / manage / connect | LD deferred |
| Interaction model | Tab behavior OK; no journey memory | Coupled to State |
| **State model** | **Journey amnesia; cold CTA reset; weak “why next”** | **LD (primary)** |
| Navigation model | Authenticated chrome largely unified | Further chrome deferred |
| Mental model | “Forgot my Visit” blocks quiet-watching belief | LD via State |

---

## 4. Narrative state model (definition — binding for next cycle)

Home must be an **event-based** narrator, not only an evidence projector:

1. Persist **user-action events** and **system-observation events** as **separate** narrative events.  
2. Every dashboard narrative state **identifies which event(s)** it reflects.  
3. Every meaningful action advances the story; never resets without explanation; always explains why the next ask is requested.  
4. Distinguish **you acted** from **Mighty observed** (user lag vs evidence lag) by keeping event kinds separate.  
5. If nothing progressed after Visit, say **non-progress honestly** via observation event(s) — do not re-issue the opening ask as if nothing happened.

Exemplar beat sequence (Amex):

`You opened Amex` *(user-action)* → `Waiting for Chrome` / `No progress yet` *(observation or absence)* → verifying/extracting *(observation)* → terminal **with continuity** *(observation + prior user-action)*.

**Independent Audit** must include **interruption scenarios** (reload mid-journey, tab return with no progress, late observation, abandoned Visit, contradictory still-needs-login) — see charter + [AUDIT_BRIEF.md](../ube-journey-narrator/AUDIT_BRIEF.md).

Charter (amended): [CYCLE_CHARTER.md](../ube-journey-narrator/CYCLE_CHARTER.md).

---

## 5. Recommended next cycle (only one)

**Name:** Journey Narrator (`ube-journey-narrator`)  
**Charter:** [CYCLE_CHARTER.md](../ube-journey-narrator/CYCLE_CHARTER.md) — status **Proposed**  
**Outcome:** After Visit / Sign-in (and sibling meaningful Home acts), returning to Mighty never feels like amnesia; next actions always carry reason.

**Why this one:** Maximizes trust and Gate 4 / exit-test “When is Mighty working?” Visual doors and vocabulary wait.

**Out of scope:** Visual migration; landing/login/popup; new providers; invented balances; unrelated cleanup.

---

## 6. Ask of Founder

Choose one:

1. **Accept** the `ube-journey-narrator` charter → engineering begins next (Independent Audit before deploy).  
2. **Redirect** — name a different single gate to maximize instead.  
3. **Override** the narrative rules in one sentence.

No implementation proceeds until you choose.
