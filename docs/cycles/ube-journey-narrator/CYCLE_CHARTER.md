# Cycle Charter — Journey Narrator (UBE State Model)

**Charter status:** **Accepted & frozen** (Founder: proceed 2026-07-29; event-based model; governing no-repeat-without-why rule; Independent Audit incl. interruptions + R1)  
**Freeze rule:** Scope / success / non-goals / pause triggers / event model / audit criteria below freeze except by Founder re-acceptance.

**Area slug:** `ube-journey-narrator`  
**Parent milestone:** [Unified Beta Experience](../../milestones/MILESTONE_UNIFIED_BETA_EXPERIENCE.md)  
**Driven by:** [UBE Gap Assessment — reopened](../ube-gap-assessment/UBE_GAP_ASSESSMENT.md) · Founder testing (Visit → cold “Sign in to American Express”)  
**Prior related:** `visit-amex-home-base` (interaction) · `amex-value-pipeline` (lifecycle honesty) · `ube-one-daily-product` (chrome; visual migration suspended)  
**Audit brief:** [AUDIT_BRIEF.md](AUDIT_BRIEF.md)

---

## Intake

| Field | Content |
|-------|---------|
| **Area** | Authenticated Home / dashboard as truthful narrator of the user’s journey |
| **Outcome** | Every meaningful action advances the story; never resets without explanation; always explains why the next action is requested |
| **Non-goals** | Visual surface migration; landing / login / popup redesign; new providers; inventing account data; unrelated cleanup; declaring UBE milestone complete |
| **Hard constraints** | Feature-neutral UBE; **event-based** narrative model (below); **R1** governing rule (below); preserve Visit/orientation contracts; Truth Over Completeness; no silent state invention; Independent Audit on continuity + interruptions + R1 before deploy |
| **Known philosophy** | Mighty is system of engagement; provider tabs are temporary; competence trust requires memory of what the user just did **and** what the system observed |
| **Review** | Implement → Independent Audit (perception + interruptions + R1) → Founder → deploy only on explicit ask |

---

## Success criterion (binding)

> After a meaningful Home action (especially Visit / Sign-in to a provider), a Founder returning to Mighty can see **what just happened**, **what Mighty is waiting for**, and **why any next ask exists** — and cannot conclude the product forgot their action.

Supporting checks (evidence, not substitutes):

1. Visit (or Sign-in CTA) persists as a durable **user-action event** and advances the story across reload/poll.  
2. System observations (session, verification, non-progress, terminal) persist as separate **system-observation events** — never merged into or mistaken for user actions.  
3. Every dashboard narrative state **identifies which event(s)** it reflects (traceable in projection / audit evidence).  
4. Cold identical “Sign in to American Express” without acknowledgment/reason after a just-completed Visit is a **fail**.  
5. Non-progress after Visit is explained via observation event(s) — not silent reset.  
6. User lag vs evidence lag is distinguishable because user-action and system-observation events remain separate.  
7. **R1:** A repeated user-action ask never appears without explaining why the previous attempt did not produce the expected outcome.  
8. Independent Auditor falsifies journey continuity **including interruption scenarios and R1** — not “fields added.”

---

## Governing rule R1 (binding)

> **The dashboard may never request a repeated user action without first explaining why the previous attempt did not produce the expected outcome.**

This is mandatory Independent Audit criterion **R1** alongside interruption scenarios I1–I5.

Hard fail examples:

- Visit Amex, return, see identical “Sign in to American Express” with first-ask body and no mention that the prior Visit did not yield a confirmed session.  
- Re-offer Visit/Sign-in after an abandoned Visit with no “why previous didn’t complete” explanation.

---

## Narrative state model — event-based (product contract)

The narrator is **event-based**, not a single mutable status string.

### Event kinds (persist separately)

| Kind | What it records | Examples |
|------|-----------------|----------|
| **User-action event** | What the user deliberately did in Mighty | Visit Amex CTA accepted; Sign-in CTA accepted |
| **System-observation event** | What Mighty observed or concluded | Awaiting confirmation; no progress; still needs login; verification progress; terminal |

Rules:

1. **Persist both.** User actions and system observations are first-class narrative events with distinct identity, timestamps, and provenance.  
2. **Never collapse.** Do not overwrite a user-action event with an observation, or invent a user action from an observation.  
3. **Compose, don’t amnesia.** Dashboard story is a composition over the recent event stream + current evidence — not a cold re-projection that erases prior events.  
4. **State → events.** Every user-visible dashboard narrative state **must identify which event(s)** it reflects.  
5. **Advance / explain / why-next.** Meaningful events advance the story; unexplained resets fail; every next ask cites the events that justify it.  
6. **R1.** Repeated user-action asks require an explicit explanation of why the previous attempt failed to produce the expected outcome.

### User-visible beats (composed from events)

| Beat | Typically reflects | User should understand |
|------|--------------------|------------------------|
| Act acknowledged | Latest relevant user-action event | “You opened {Provider}” |
| Waiting with reason | User-action + pending observation (or absence) | What Mighty is waiting for and why |
| Progress | System-observation progress events | Verifying / reading — bounded and honest |
| Terminal with continuity | Terminal observation **plus** prior user-action context | Outcome + why any further ask exists |
| Explicit non-progress | Observation of non-progress after user-action | Nothing confirmed yet — **not** amnesia |
| Repeat ask with why (R1) | Prior user-action + observation that expected outcome missing | Why asking again |

---

## Independent Audit acceptance criteria (binding)

Auditor role: [INDEPENDENT_AUDIT_CHARTER.md](../../INDEPENDENT_AUDIT_CHARTER.md). Detail: [AUDIT_BRIEF.md](AUDIT_BRIEF.md).

**Accept for Founder review** only if falsification of the following **fails** (product survives). **Return** if any hard fail sticks.

### Continuity (baseline)

- A. After Visit, reload/poll does not cold-reissue Sign-in/Visit without acknowledging the Visit user-action event.  
- B. Every shown narrative state can be mapped to named event(s) (user-action and/or system-observation).  
- C. User-action and system-observation remain distinguishable in the story (user lag vs evidence lag).  
- **R1.** Repeated user-action ask always explains why the previous attempt did not produce the expected outcome.

### Interruption scenarios (required)

| ID | Scenario | Hard fail if |
|----|----------|--------------|
| **I1** | **Reload mid-journey** — Visit, then hard reload before any system observation | Story loses the Visit; cold opening CTA with no event acknowledgment |
| **I2** | **Tab return / focus** — Visit, leave Amex open, return to Mighty via focus/visibility poll with no extension progress | Amnesia or silent reset; no non-progress / waiting-with-reason bound to Visit event |
| **I3** | **Observation arrives late** — Visit, then system observation (session / verifying / terminal) after delay | Story jumps to observation-only state that erases or contradicts the Visit without explanation |
| **I4** | **Interrupted Visit** — Visit starts, user abandons provider tab / never completes sign-in; returns to Mighty | Product pretends Visit never happened **or** pretends verification succeeded; must narrate from Visit + honest observation (or explicit non-progress) |
| **I5** | **Contradictory observation** — Visit, then observation still “needs login” / signed-out | Cold identical Sign-in with no continuity (**R1**); or invents success |

Judgment target: **Founder perception of continuity under interruption**, not event-table completeness theater.

---

## Pause triggers

- Resuming visual migration in this cycle  
- Painting copy without durable **event** persistence  
- Collapsing user-action and system-observation into one undifferentiated flag  
- Dashboard states that cannot identify which event(s) they reflect  
- Repeated user-action ask without R1 explanation  
- Inventing provider session/data to “look busy”  
- Shipping without Independent Audit covering I1–I5 and **R1**  
- Deploy before Independent Audit Accept  

---

## Governing citations

- UBE Gate 4 (State model) — journey narration; event-based; R1  
- System of engagement · Visit Amex home-base (interaction preserved)  
- Decision [2026-07-29-unified-beta-experience.md](../../product/decisions/2026-07-29-unified-beta-experience.md)  
- Decision [2026-07-29-ube-state-model-narrator.md](../../product/decisions/2026-07-29-ube-state-model-narrator.md)
