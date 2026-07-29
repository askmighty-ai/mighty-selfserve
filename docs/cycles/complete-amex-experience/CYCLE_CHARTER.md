# Cycle Charter — Complete American Express Experience

**Charter status:** **Accepted & frozen** (Founder: Accept 2026-07-29; amended same day — AT-00 Fresh Install binding)  
**Freeze rule:** Scope / success / non-goals / pause triggers / Acceptance Tests freeze except by Founder re-acceptance.

**Area slug:** `complete-amex-experience`  
**Parent milestone:** [Unified Beta Experience](../../milestones/MILESTONE_UNIFIED_BETA_EXPERIENCE.md)  
**Mission:** [Product Realization](../../PRODUCT_REALIZATION.md)  
**Driven by:** Founder-approved Product Realization Assessment (Complete American Express Experience) · prior Amex slices (`confirm-enrolls-watching`, `amex-value-pipeline`, `visit-amex-home-base`, `ube-journey-narrator`)  
**Prior related:** `ube-journey-narrator` (Amex Visit R1/R2 — Audit Accept, deploy stopped) · `amex-value-pipeline` · `visit-amex-home-base` · `first-success` · `steady-state-home`

---

## Intake

| Field | Content |
|-------|---------|
| **Area** | Complete American Express customer journey — Home, Accounts, and Amex provider state as one coherent experience |
| **Outcome** | End-to-end Founder Amex walkthrough succeeds without fundamental inconsistency; one canonical lifecycle; truthful narrative at every step; user always knows what Mighty knows / does not know / why any ask; production quality |
| **Non-goals** | Other providers; multi-provider abstractions beyond Amex needs; visual surface-family migration (except Amex-path defects that fail Acceptance Tests); vocabulary glossary freeze; new capabilities / AI / dashboards; inventing Amex balances; declaring full UBE complete; Home ritual / Living Calm adoption; unrelated cleanup |
| **Hard constraints** | **Amex only** — Amex is the reference implementation for future providers; Truth Over Completeness; Separate Axes; R1 + R2 on Amex path; system of engagement (Mighty home base, Amex temporary workspace); no silent state invention; **engineering complete only when every Acceptance Test passes**; Independent Audit + Founder walkthrough before deploy; deploy only on explicit Founder ask |
| **Known philosophy** | Found ≠ watching ≠ signed in ≠ has data; Permission to Leave only when earned; operator signs in at Amex |
| **Review** | Implement → Independent Audit against Acceptance Tests → Founder walkthrough → deploy only on explicit ask |

---

## Success criterion (binding)

> A Founder can walk the full American Express customer journey and experience one coherent, production-quality product: Home, Accounts, and Amex provider state agree; the narrative is truthful at every step; the user always knows what Mighty knows, what it does not know, and why any requested action is necessary.

**Engineering is complete only when every Acceptance Test in this charter passes.**

Supporting product rules (not substitutes for Acceptance Tests):

1. One canonical Amex lifecycle composition drives Home, Accounts, and nested Amex provider-state presentation.  
2. Event-based narrator on Amex path: user-action ≠ system-observation; **R1** + **R2**.  
3. Never invent Amex balances or claim verifying / do-nothing from Visit intent alone.  
4. Visit Amex keeps Mighty as home base (new tab, orientation, return).  
5. Amex becomes the reference pattern for future providers — without building multi-provider framework in this cycle.

---

## Governing rules (Amex path)

### R1 — Repeat ask

> The dashboard may never request a repeated Amex user action without first explaining why the previous attempt did not produce the expected outcome.

### R2 — Evidence-gated claims

> Narrative claims must not advance on user intent alone. Distinguish **intent**, **observed progress**, and **verified outcome**. “Mighty is verifying…” and “You do not need to do anything else” require prerequisite system observations.

### One lifecycle

> For the same Amex account at the same moment, Home and Accounts (including nested access/status fields) must not present contradictory overlapping truths.

---

## Governing citations

- Founder Vision §§3.2, 3.3, 3.6, 2.4, 6.3, 4.2  
- [02_mental_model.md](../../product/02_mental_model.md) · [05_experience_map.md](../../product/05_experience_map.md) · [07_product_architecture.md](../../product/07_product_architecture.md)  
- Experiences 02–05 (Amex as concrete beta provider)  
- ADRs: [confirm-enrolls-watching](../../product/decisions/2026-07-27-confirm-enrolls-watching.md) · [amex-bounded-extraction-lifecycle](../../product/decisions/2026-07-28-amex-bounded-extraction-lifecycle.md) · [mighty-system-of-engagement](../../product/decisions/2026-07-28-mighty-system-of-engagement.md) · [visit-amex-home-base](../../product/decisions/2026-07-28-visit-amex-home-base.md) · [ube-state-model-narrator](../../product/decisions/2026-07-29-ube-state-model-narrator.md) · [unified-beta-experience](../../product/decisions/2026-07-29-unified-beta-experience.md)

---

## Pause triggers

- Generalizing to non-Amex providers or building reusable multi-provider platform beyond Amex needs  
- Inventing Amex balances or fake progress  
- Shipping with any Acceptance Test failing or waived without Founder override  
- Resuming broad visual migration unrelated to Amex Acceptance Test failures  
- Deploy before Independent Audit Accept **and** Founder walkthrough against Acceptance Tests  
- Declaring UBE milestone complete from this cycle alone  

---

## Explicit out of scope

- Any provider other than American Express  
- Multi-provider abstractions / registries / generic provider framework beyond Amex  
- Landing / login / popup visual unification (unless an Amex Acceptance Test cannot pass without a minimal Amex-path fix)  
- Vocabulary freeze across the product  
- New providers, AI, dashboards, analytics  
- Living Calm / Home ritual adoption  
- Unrelated cleanup  

---

## Acceptance Tests (binding)

**Nature:** Founder walkthrough tests. These define “Complete American Express Experience.”  
**Rule:** Engineering is **not complete** until **every** test passes. Independent Audit and Founder review falsify against this section.  
**Scope:** American Express only. Surfaces under test: **Home**, **Accounts** (Amex row / detail as applicable), and **Amex provider state** as projected by Mighty (not Amex’s own UI chrome).

### Shared definitions

| Term | Meaning |
|------|---------|
| **Canonical lifecycle** | One Amex product lifecycle bucket for the account at that moment (e.g. needs-action / waiting / verifying / success-with-data / unsupported-data / failure) with consistent labels and next action across Home and Accounts |
| **Truthful narrative** | Story matches evidence tier: intent vs observed progress vs verified outcome; never stronger than authorizing evidence |
| **Knows / does not know** | UI states what Mighty has verified and what remains unknown — no comforting invention |
| **Fundamental inconsistency** | Home vs Accounts disagree on whether Amex needs sign-in, is verifying, has data, or is clear — or narrative claims progress/success without evidence |

### AT-00 — Fresh Install

| Field | Specification |
|-------|----------------|
| **Starting state** | Brand-new invite-beta user: **no** Mighty account, **no** extension installed, **no** provider connected |
| **User actions** | First launch → create Mighty account → Welcome → Discover / confirm Amex watching → Enable monitoring / install & verify Chrome extension as needed → Home Amex Visit/Sign-in → complete Amex path through terminal → reach steady Home |
| **Expected Home state** | At every step, one obvious next action; Home always tells the truth (never invents Amex data or progress); after terminal Amex outcome, steady state where quiet watching is understandable |
| **Expected Accounts state** | Once Amex is watched, Accounts never contradicts Home on Amex lifecycle / next action (including nested fields) at each checkpoint |
| **Expected narrative** | User is never confused about what Mighty is doing; knows / does not know / why-next answerable throughout; role split clear at Amex sign-in |
| **Pass** | Full first-launch → steady-state Amex walkthrough succeeds; every next action is obvious; no confusion about what Mighty is doing; Home always truthful; Home ↔ Accounts never contradict; user understands Mighty is now quietly watching Amex; **no fundamental inconsistency** discovered |
| **Fail** | Any dead end, opaque step, false progress/data, Home/Accounts contradiction, or failure to reach understandable steady watching of Amex |

### AT-01 — First-time Amex connection

| Field | Specification |
|-------|----------------|
| **Starting state** | Watched set confirmed with Amex enrolled; Chrome/monitoring available as required for capture; Amex never successfully verified for this user; no durable Amex session evidence |
| **User actions** | Open Home → follow primary Amex Visit/Sign-in ask → complete Amex sign-in in the provider tab → return to Mighty (tab focus / Home) → wait until terminal evidence arrives or honest waiting is shown |
| **Expected Home state** | One primary Amex ask at start; after return, continuity (not cold first-ask); then honest waiting or progress only with observations; then honest first-success or terminal outcome — never fabricated balances |
| **Expected Accounts state** | Amex row/detail matches Home on needs-sign-in → verifying/waiting → terminal bucket; nested status fields do not contradict |
| **Expected narrative** | Role split clear (user signs in at Amex); Visit/intent acknowledged; progress only with evidence; terminal explains what Mighty now knows |
| **Pass** | End-to-end reaches a coherent terminal (access verified with data, or honest partial/unsupported) with Home ↔ Accounts agreement and no invented data |
| **Fail** | Cold amnesia after Visit; verifying/do-nothing from intent alone; contradictory Home/Accounts; fabricated celebration or balances |

### AT-02 — Already signed into Amex

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; user already has an active Amex browser session; Mighty has not yet recorded verified capture for this visit cycle (or needs a fresh check) |
| **User actions** | From Home, Visit Amex (or equivalent primary) → Amex opens already signed in → browse/stay briefly → return to Mighty → allow observation/poll |
| **Expected Home state** | Acknowledges the Visit; does not demand a redundant “sign in” as if logged out once session evidence is positive; advances to verifying only with observations, then terminal |
| **Expected Accounts state** | Same lifecycle as Home; does not show “Needs sign-in” while Home shows verified/verifying from the same evidence |
| **Expected narrative** | Intent → observed progress (when checking) → verified outcome; user understands Mighty is confirming what it can see |
| **Pass** | No false logged-out ask after positive session evidence; Home/Accounts agree; narrative evidence-gated |
| **Fail** | Persistent “Sign in to American Express” after Mighty has confirmed session; or success claimed without observation |

### AT-03 — Logged out of Amex

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; no usable Amex session (signed out / expired); Home correctly needs user action |
| **User actions** | Open Home → read ask → Visit Amex → remain signed out or land on Amex login without completing → return to Mighty |
| **Expected Home state** | Clear needs-sign-in (or equivalent) with why; after Visit without session, **R1** continuity — not cold identical first-ask; never claims verifying success |
| **Expected Accounts state** | Amex needs sign-in / not connected consistently with Home |
| **Expected narrative** | What Mighty does not know (no confirmed session); why Visit/Sign-in is necessary; after abandoned Visit, why previous attempt did not complete |
| **Pass** | Honest logged-out story before and after incomplete Visit; R1 on repeat; Home ↔ Accounts agree |
| **Fail** | Pretends verification succeeded; or amnesia cold CTA with no why-previous |

### AT-04 — Session expires mid-flow

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; user begins Visit/sign-in while session is usable or login is in progress |
| **User actions** | Visit Amex → session expires or Amex forces re-auth mid-flow → return to Mighty (and/or retry) |
| **Expected Home state** | Does not keep “verifying” or “you’re done” after evidence shows signed-out / needs login; moves to honest needs-action with R1 if re-asking |
| **Expected Accounts state** | Reflects needs-sign-in / session lost consistently — not “Connected” or “Extracting” forever |
| **Expected narrative** | Explains that prior progress did not yield a lasting confirmed session; next ask justified |
| **Pass** | Lifecycle and narrative follow the new negative evidence; no sticky success or sticky Extracting |
| **Fail** | Sticky verifying/Extracting/success after session loss; Home/Accounts disagree |

### AT-05 — Unsupported account state (no publishable Amex data)

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; access cycle can complete with terminal `NO_ACCOUNT_DATA` / unsupported-data (or equivalent bounded outcome) |
| **User actions** | Complete Visit/sign-in path as needed → return → wait until terminal unsupported/empty outcome |
| **Expected Home state** | Leaves Extracting; shows honest unsupported/empty terminal with clear next action (or calm defer guidance) — **never** invents balances |
| **Expected Accounts state** | Same terminal bucket and next action as Home; nested labels do **not** say contradictory “Unable to verify” while top-level is honest unsupported/logged-in-no-data |
| **Expected narrative** | States what Mighty confirmed (e.g. access) and what it does not have (publishable account facts); why any further action exists |
| **Pass** | No sticky Extracting; Home ↔ Accounts (+ nested) agree on unsupported-data; no invented value |
| **Fail** | Indefinite Extracting; nested “Unable to verify” vs honest top-level; fake balances |

### AT-06 — Reload during verification

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; user has clicked Visit; verification may be in progress or only intent recorded |
| **User actions** | Visit Amex → hard reload Home before terminal outcome |
| **Expected Home state** | Preserves journey continuity (Visit acknowledged and/or current honest observation); does not cold-reset to first-ask amnesia; does not claim verified calm from reload alone |
| **Expected Accounts state** | Matches Home lifecycle for Amex after reload |
| **Expected narrative** | Same evidence tier as before reload (intent or observed_progress or non-progress) — reload is not authorizing evidence |
| **Pass** | Continuity + R2 preserved across reload; Home ↔ Accounts agree |
| **Fail** | Amnesia cold CTA; or upgrade to verifying/do-nothing solely because of reload |

### AT-07 — Return after 30 minutes

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; prior Visit or verification attempt earlier; ~30 minutes elapsed; evidence may be unchanged or updated |
| **User actions** | Leave Mighty unused ~30 minutes → reopen Home (and check Accounts) |
| **Expected Home state** | Reflects **current** evidence honestly; if still blocked, one clear ask with reason (R1 if repeating prior ask); if clear, Permission to Leave — not a setup restart |
| **Expected Accounts state** | Same Amex lifecycle as Home |
| **Expected narrative** | Time passage does not invent progress; story explains current knowns/unknowns |
| **Pass** | Coherent current-state story; no false all-clear; no wizard restart; Home ↔ Accounts agree |
| **Fail** | False calm while Amex still needs user; or amnesia as if Amex was never enrolled/visited when evidence says otherwise |

### AT-08 — Home vs Accounts consistency

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; any non-terminal or terminal Amex state reachable on the beta path |
| **User actions** | Open Home → note Amex status/next action/narrative → open Accounts (Amex row/detail) → compare → return Home → compare again after one poll/focus if needed |
| **Expected Home state** | Single coherent Amex story |
| **Expected Accounts state** | Same canonical lifecycle bucket, sign-in need, verifying vs terminal, and next-action meaning as Home (including nested fields) |
| **Expected narrative** | No surface teaches a different “truth” about Amex |
| **Pass** | No fundamental inconsistency across Home and Accounts for Amex |
| **Fail** | Any contradictory pair (e.g. Home “verifying” vs Accounts “Needs sign-in”; top-level unsupported vs nested “Unable to verify”) |

### AT-09 — Permission to Leave

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; no blocking Setup/Interrupt for Amex; access health not falsely asserted; Attention silent on Amex blockers |
| **User actions** | Open Home after Amex is genuinely clear (post first-success or steady clear) |
| **Expected Home state** | Brief all-clear / You’re good — Permission to Leave; no forced tour, upsell, or new Amex setup checklist as primary |
| **Expected Accounts state** | Amex shows healthy/clear consistent with Home — not “Needs sign-in” or Extracting |
| **Expected narrative** | Silence/calm means Mighty is watching; what Mighty knows is sufficient for leave |
| **Pass** | Founder can leave in ~10 seconds trusting calm; Home ↔ Accounts agree |
| **Fail** | All-clear while Amex still needs sign-in/verification; engagement theater; contradiction on Accounts |

### AT-10 — “Is Mighty working?” without opening Amex

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; at least one prior honest cycle so Mighty has a stance (waiting, clear, or needs-action with reason) |
| **User actions** | Open Home only — **do not** open Amex — answer: Is Mighty working? What does it know? What does it need from me? |
| **Expected Home state** | Enough honest signal to answer without visiting Amex: watching/waiting/needs-you/clear |
| **Expected Accounts state** | Consistent with Home if opened later; not required for the Home-only pass |
| **Expected narrative** | States knowns and unknowns; if action needed, why — without requiring the user to check Amex to interpret Mighty |
| **Pass** | Founder can answer “Is Mighty working?” from Home alone without opening Amex |
| **Fail** | Home is opaque/contradictory so Founder must open Amex to know whether Mighty is working |

### AT-11 — Visit then immediate return (intent only)

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; needs Visit/Sign-in |
| **User actions** | Click Visit Amex → immediately return / hard reload **before** Chrome/extension confirms anything |
| **Expected Home state** | Acknowledges Visit (**intent**); does **not** say “Mighty is verifying access” or “you do not need to do anything else” |
| **Expected Accounts state** | Does not claim verified success; aligns with needs-action or waiting-with-intent continuity |
| **Expected narrative** | Intent tier only; Mighty has not confirmed yet; why next wait or ask exists |
| **Pass** | R2 holds; continuity holds; Home ↔ Accounts agree |
| **Fail** | Verifying or do-nothing from Visit click alone; or cold amnesia |

### AT-12 — Repeat ask after failed Visit (R1)

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched; Visit recorded; observation still needs login / no confirmed session |
| **User actions** | Return to Home when Sign-in/Visit is offered again |
| **Expected Home state** | Repeat ask **includes why previous attempt failed** (no confirmed session / still needs sign-in) |
| **Expected Accounts state** | Needs sign-in consistent with Home |
| **Expected narrative** | R1 satisfied — previous attempt named; next action justified |
| **Pass** | Why-previous visible; not identical cold first-ask body |
| **Fail** | Cold identical “Sign in to American Express” with no acknowledgment of prior Visit |

### AT-13 — Chrome setup vs Amex job (no contradictory primary)

| Field | Specification |
|-------|----------------|
| **Starting state** | Amex watched and is the outstanding Amex job **or** Chrome setup genuinely blocks capture — whichever the product truth is |
| **User actions** | Open Home; identify the primary CTA and Amex narrative |
| **Expected Home state** | Primary action matches the true blocker: if Chrome setup is required for Amex capture, that is clear; if Amex Visit is the job, Amex narrative is not contradicted by a misleading primary that implies Amex is fine |
| **Expected Accounts state** | Amex lifecycle matches the same blocker story |
| **Expected narrative** | User knows whether the next human step is Chrome setup or Amex Visit/sign-in, and why |
| **Pass** | One coherent next step; no fundamental CTA/narrative contradiction |
| **Fail** | Amex continuity text says one job while primary CTA teaches another incompatible truth without explanation |

### AT-14 — Steady return: nothing needed

| Field | Specification |
|-------|----------------|
| **Starting state** | Day-2+ after successful Amex path; Amex still clear; user has been away |
| **User actions** | Open Home → optionally glance Accounts → leave |
| **Expected Home state** | Permission to Leave; ambient watching; no Amex re-onboarding |
| **Expected Accounts state** | Amex clear/consistent; not Needs sign-in |
| **Expected narrative** | Mighty watched while away; nothing needs the user |
| **Pass** | Calm return without opening Amex; Home ↔ Accounts agree |
| **Fail** | Spurious Amex interrupt; false needs-sign-in; setup checklist as primary |

### AT-15 — Production-quality Founder walkthrough (integration)

| Field | Specification |
|-------|----------------|
| **Starting state** | Clean invite-beta capable path: confirm Amex watching → monitoring/Chrome as needed → first Amex Visit through terminal → steady Home |
| **User actions** | Founder performs the full Amex journey in one sitting (and spot-checks AT-06 or AT-11 once) |
| **Expected Home state** | Production quality throughout: calm, precise, one job per moment, no fundamental inconsistency |
| **Expected Accounts state** | Always agrees with Home on Amex lifecycle at each checkpoint |
| **Expected narrative** | Truthful at every step; knows / does not know / why-next always answerable |
| **Pass** | Full walkthrough succeeds; Founder would ship this Amex experience to an invitee; **all AT-00–AT-14 also pass** |
| **Fail** | Any fundamental inconsistency; any failed AT-00–AT-14; demo-quality gaps (opaque errors, sticky false states, invented data) |

---

## Completion gate

| Gate | Requirement |
|------|-------------|
| **Acceptance Tests** | AT-00 through AT-15 all **Pass** |
| **Independent Audit** | Falsify against this Acceptance Tests section (Amex only); Accept for Founder review |
| **Founder** | Walkthrough validation against Acceptance Tests |
| **Deploy** | Only on explicit Founder ask |
