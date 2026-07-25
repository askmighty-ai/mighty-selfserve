# Home OS — Behavioral Contract

**Status:** Behavioral authority  
**Audience:** Product, design, engineering  
**Scope:** What Home *does* and *must never do*. Not visual design. Not implementation.

When Home behavior is ambiguous, this document wins.

Related (non-authoritative for Home OS behavior): [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) · [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md) · [HOME_V1.md](HOME_V1.md) · [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md)

---

## 1. Core Principle

**Home is the operating system.**

Every routine user task begins and ends on Home.

Home answers one question:

> **What needs me?**

If nothing needs the user, Home says so clearly and stays calm. If something needs the user, Home presents exactly one thing to do next, then returns the user to calm when that work is done.

Home is not a dashboard of destinations. It is the place where work is received, acted on, and closed.

---

## 2. Work Item Contract

A **Work Item** is anything that may require the user’s attention on Home. There are exactly four types.

| Type | User meaning |
|------|----------------|
| **Interrupt** | Mighty is blocked until you act |
| **Approval** | An agent needs your explicit consent |
| **Opportunity** | Real value is waiting; acting is optional but worthwhile |
| **Setup** | Coverage or capability is incomplete; Mighty cannot do its job yet |

Every Work Item obeys the fields below.

### Shared rules (all types)

- A Work Item is completed, dismissed, deferred, or expired **on Home**.
- Completing a Work Item returns the user to Home’s current state (usually calm).
- A Work Item never deep-links the user into another product page as the primary path for routine resolution.
- Secondary actions may open Coverage disclosure, Settings, or deep inspection only when the type’s contract allows it.

---

### 2.1 Interrupt

| Field | Contract |
|-------|----------|
| **Trigger** | Mighty cannot proceed without the user. Typical cases: provider sign-in required, session lost on a tracked account, recovery that needs a human, trust or access blockers that stop ongoing work. |
| **Priority** | Highest class of Work Item. Outranks Approval, Opportunity, and Setup when effective. |
| **Information shown** | What is blocked; which account/provider (when applicable); why the user is needed; what happens after they act. No passwords. No internal status codes. |
| **Primary action** | The single action that unblocks Mighty (e.g. sign in at the provider, complete required recovery). Performed from Home. |
| **Secondary action** | Optional: defer; or open Coverage for related accounts if more than one interrupt exists. Never a competing second primary. |
| **Can dismiss?** | No for hard blockers that leave Mighty stuck. Soft interrupts may allow dismiss only when product policy marks them non-blocking. |
| **Can defer?** | Yes, within policy windows (snooze). Deferred Interrupts leave the queue and return when the window ends or the condition worsens. |
| **Completion behavior** | When the blocker clears, the Interrupt leaves the queue immediately. Home re-evaluates and either expands the next Work Item or returns to calm. |
| **Resulting Proof entry** | Yes when the resolution produced a material outcome (e.g. session restored and data refreshed). Entry describes the outcome, not the interrupt itself. |

---

### 2.2 Approval

| Field | Contract |
|-------|----------|
| **Trigger** | An agent (or authorized automation) requests consent before a consequential action. |
| **Priority** | Below Interrupt; above Opportunity and Setup. |
| **Information shown** | Who/what is asking; the exact action; material fields (amounts, recipients, account); consequence of approve vs reject; expiry if any. Enough to decide without leaving Home. |
| **Primary action** | Approve (or equivalent affirmative consent). |
| **Secondary action** | Reject / deny. Optional: defer if policy allows temporary hold. |
| **Can dismiss?** | No. Approvals are decided (approve or reject), not casually cleared. Ignoring until expiry is not dismiss. |
| **Can defer?** | Yes only if policy allows a hold window; otherwise no. Deferred Approvals remain pending and age. |
| **Completion behavior** | Decision recorded; Work Item removed; user remains on Home. If the agent proceeds, outcome appears later as Proof when verified. |
| **Resulting Proof entry** | Always for the decision (approved / rejected / expired). Later, a separate Proof entry when the action completes or fails. |

---

### 2.3 Opportunity

| Field | Contract |
|-------|----------|
| **Trigger** | Concrete, real value tied to the user’s accounts crosses the worth-surfacing threshold (e.g. expiring credit, certificate, high-confidence benefit). Must be factual—never promotional filler. |
| **Priority** | Below Interrupt and Approval. May be primary only when no higher class is effective. |
| **Information shown** | The benefit in concrete terms; which account; why it matters now; time sensitivity if real; enough context to act. |
| **Primary action** | The action that captures the value (use credit, redeem, book, open the relevant next step)—executed as Home-centered work, not as a tour into another Mighty page. |
| **Secondary action** | Dismiss or defer (“not now”). |
| **Can dismiss?** | Yes. Dismiss means “don’t show this instance again” unless the underlying fact materially changes. |
| **Can defer?** | Yes. Defer quiets the item for a defined window; it may return if still valid afterward. |
| **Completion behavior** | On act, dismiss, defer, or natural expiry/invalidity, the item leaves the expanded slot. Home returns to calm or the next Work Item. |
| **Resulting Proof entry** | Yes when value was captured or when a meaningful change was recorded. Dismiss/defer alone does not invent Proof. |

---

### 2.4 Setup

| Field | Contract |
|-------|----------|
| **Trigger** | Mighty lacks required coverage or capability to do its job: no accounts yet, Gmail not connected when discovery is the unlock, worker/extension missing when capture depends on it, first-run orientation that blocks value. |
| **Priority** | Below Interrupt and Approval. Competing with Opportunity: Setup wins when Mighty cannot operate; Opportunity wins when Mighty can operate and value is waiting. |
| **Information shown** | What is missing; why it matters; the one step that unlocks progress. Honest waiting language—never fake data. |
| **Primary action** | The unlock step (connect Gmail, complete worker setup, add first account, confirm discovery). Done from Home. |
| **Secondary action** | Optional alternate path (e.g. add manually instead of Gmail) or defer when Setup is not a hard gate. |
| **Can dismiss?** | First-run orientation: yes (dismissible once). Hard capability gaps that leave Mighty non-functional: no until resolved or an allowed alternate path is taken. |
| **Can defer?** | Soft Setup may defer. Hard Setup that blocks all value should not pretend to be deferrable as “all clear.” |
| **Completion behavior** | When the gap closes, Setup leaves the queue. Home may briefly reflect progress, then calm or the next Work Item. |
| **Resulting Proof entry** | Yes for meaningful milestones (first accounts found, first successful capture). Not for every intermediate setup click. |

---

## 3. Queue Rules

Home maintains a single **Work Queue** of effective Work Items.

### Only one expanded item

- Exactly one Work Item may be **expanded** at a time.
- The expanded item is the sole primary action surface.
- All other queue members stay collapsed: visible as presence/count if needed, never as equal competing primaries.

### Ranking algorithm

Effective items are ordered by class, then by urgency within class:

1. **Interrupt** (hard blockers before soft)
2. **Approval** (earlier expiry / higher consequence first)
3. **Setup** that blocks operation
4. **Opportunity** (sooner real deadline / higher concrete value first)
5. **Setup** that does not block operation

Within the same class and urgency band, prefer the item whose inaction loses more user value sooner.

### Tie-breaking

When ranking still ties:

1. Earlier deadline / expiry wins  
2. Stable deterministic identity (e.g. provider, then item id) — never random  
3. Input arrival order must not affect the result given identical items and time  

### Empty queue behavior

- Empty effective queue ⇒ **calm** (all clear).
- Calm is a first-class success state, not an empty failure.
- Home still answers “What needs me?” with: nothing.
- Coverage and Proof may still be available as disclosure; they do not invent work.

### Multiple simultaneous Work Items

- Allowed in the queue.
- Only the top-ranked item is expanded.
- Completing, dismissing, or deferring the expanded item promotes the next by ranking.
- Sibling items must not each present a primary action.

### Aging

- Items gain urgency as deadlines approach or blockers persist.
- Aging never invents new item types; it only reorders within policy.
- Long-lived soft items may be compressed or deferred by policy so calm days stay calm.

### Escalation

- A deferred or quiet condition may escalate into a higher-urgency Interrupt when inaction becomes a hard block or value is imminently lost.
- Escalation changes class or urgency by policy; it does not spawn duplicate items for the same underlying condition.
- External notifications must land on Home with the same expanded item the notification promised.

### Expiration

- Items with deadlines expire when the underlying fact is no longer actionable.
- Expired Approvals resolve as expired (recorded), not as silent vanish without trace when consent was pending.
- Expired Opportunities leave the queue without scolding; optional Proof only if a material change was recorded elsewhere.
- Expiration is automatic; the user should not need to “clear” an expired item to restore calm.

---

## 4. Home Regions

Four behavioral regions. This section defines responsibility and forbidden content—not layout.

### 4.1 Status

**Responsible for**

- Answering “What needs me?” (and when nothing: that the user is clear).
- Reflecting the emotional/operational mode implied by the queue: calm, needs you, value waiting, setting up.
- Naming the condition in plain language.

**Must never contain**

- A second primary Work Item.
- Portfolio spreadsheets, balances grids, or maintenance lists.
- Settings, deep diagnostics, or recovery labs.
- Fake urgency or engagement prompts.

### 4.2 Work Queue

**Responsible for**

- Holding all effective Work Items.
- Expanding exactly one item with its primary (and allowed secondary) actions.
- Advancing the queue on complete / dismiss / defer / expire.
- Preserving the Work Item Contract for every type.

**Must never contain**

- Navigation destinations disguised as work.
- Multiple expanded items.
- Permanent marketing or “explore features” cards.
- Proof history or Coverage inventory as if they were Work Items.

### 4.3 Coverage

**Responsible for**

- Disclosing what Mighty is watching and what is missing.
- Supporting inventory, search, add provider, unsupported-provider honesty, and verification status (see §5).
- Answering “What are you covering?” without becoming the daily workplace.

**Must never contain**

- The daily primary Work Item (that belongs to the Work Queue).
- Approval decisioning as a separate product silo.
- Becoming a required destination for routine tasks.
- Fake “connected” collapse of discovery vs session vs extraction.

### 4.4 Proof

**Responsible for**

- Showing that Mighty did real work or that the user/agent completed something consequential.
- Building trust through retained, ordered evidence (see §6).
- Remaining optional to read—available, not demanding.

**Must never contain**

- Open Work Items or primary CTAs.
- Fabricated wins, demo balances, or placeholder “activity.”
- Sync logs, heartbeats, or technical success spam.
- Engagement bait (“you haven’t visited in 3 days”).

---

## 5. Coverage Contract

**Coverage is a disclosure, not a destination.**

Users may open Coverage from Home to understand or extend what Mighty watches. Routine work must still complete without treating Coverage as a separate app.

### Inventory

- Lists enrolled / discovered providers Mighty is responsible for.
- Honest per-account status along separate axes (known, session, data)—never a single misleading “connected.”
- Absence of accounts is a valid inventory state and may pair with Setup—not with fake rows.

### Search

- Helps the user find a provider in inventory or when adding.
- Search does not create Work Items by itself.
- Search is a Coverage affordance, not a global product mode that replaces Home.

### Add provider

- Allowed from Coverage (and as a Setup secondary when appropriate).
- Adding may create or resolve Setup Work Items on Home.
- After add, the user returns to Home; Coverage does not become the new home base.

### Unsupported provider

- Must be stated honestly when Mighty cannot watch a provider.
- Unsupported is not framed as user failure.
- No fake enrollment that implies coverage where none exists.

### Verification status

- Coverage shows whether Mighty has verified session/data for a provider, in plain language.
- Verification progress may appear as transient Status/Setup context; it must not become a permanent progress dashboard.
- Verification failure that blocks Mighty becomes an Interrupt (or Setup), not a silent red badge farm.

---

## 6. Proof Contract

### What qualifies as Proof

Proof is a retained record of **material, true outcomes**, such as:

- Meaningful account changes Mighty observed
- Completed Setup milestones that unlocked real capability
- Resolved Interrupts that produced a verified outcome
- Approval decisions and subsequent verified agent outcomes
- Captured Opportunities that resulted in real value change

Proof is never:

- Background refresh success on healthy accounts
- Dismiss/defer alone
- Marketing announcements
- Internal worker telemetry

### Retention

- Proof is kept long enough to support trust and audit of recent consequential outcomes.
- Stale immaterial noise is not retained as Proof.
- Approval and agent outcomes that affect money or access follow stricter retention expectations than soft benefit notices.

### Ordering

- Newest material Proof first.
- Stable ordering for identical timestamps (deterministic id).
- Ordering is chronological by outcome time, not by when the user last opened Home.

### Collapse rules

- When there is no qualifying Proof, the Proof region is omitted or empty—silence is correct.
- Multiple similar low-impact events collapse into a tighter summary; they must not flood Home.
- Proof never expands into a Work Item and never steals the primary action.

---

## 7. Navigation Rules

**Routine work must never require leaving Home.**

The user should be able to:

1. Open Home  
2. See what needs them (or that nothing does)  
3. Complete, dismiss, or defer that work  
4. Remain on Home in the resulting state  

### Navigation is allowed only for

| Destination | When allowed |
|-------------|----------------|
| **Settings** | Trust, privacy, notifications, worker/capability configuration, account-level controls that are not daily work. |
| **Deep inspection** | User-chosen depth: full account detail, history, or evidence beyond what Proof/Coverage disclose. Always optional. |
| **Exceptional recovery** | Rare failure modes that cannot be resolved safely inside a Work Item (e.g. broken install, account deletion, security recovery). |

### Navigation is not allowed for

- Completing an Interrupt, Approval, Opportunity, or Setup as the default path
- “View all” patterns that move the primary action off Home
- Making Accounts/Activity/Settings the place routine work ends

Leaving Home for allowed reasons must not strand the user: returning lands on Home with queue and Status re-evaluated.

---

## 8. Invariants

Hard rules. Future features cannot violate these.

1. **Home always answers “What needs me?”** — including with “nothing.”
2. **Calm is the default state.** Noise must be earned by real need or real value.
3. **Exactly one expanded Work Item** when the queue is non-empty.
4. **No Work Item deep-links to another page** as its primary routine resolution path.
5. **Completing work returns to Home** in the post-completion state.
6. **Routine work never requires leaving Home.**
7. **Coverage is disclosure, not a destination.**
8. **Proof is earned and true** — never fabricated, never used as a CTA engine.
9. **Interrupts are earned** — if Mighty can proceed without the user, it must not create an Interrupt.
10. **Approvals are decided, not dismissed** — consent is explicit.
11. **Opportunities are factual** — no promotional filler, no FOMO theater.
12. **Setup never pretends the product is healthy** while Mighty cannot operate.
13. **One primary action** for the expanded item — no competing primaries.
14. **Queue ranking is deterministic** — same items, same time ⇒ same order.
15. **Notifications fulfill their promise on Home** — the expanded item matches what interrupted the user.
16. **Dismiss and defer are respected** until the window ends or the underlying fact materially changes.
17. **Home is not a portfolio spreadsheet** — depth is on demand, outside the Work Queue.
18. **Truth over optimism** — honest waiting beats fake completeness.
19. **Agents act only with recorded Approval** — consequential automation is never silent on consent.
20. **This document is the behavioral authority for Home OS** — visual systems and implementations may change; these behaviors may not without an explicit contract change.

---

## Document control

| Field | Value |
|-------|--------|
| Name | Home OS Behavioral Contract |
| Authority | Behavioral (product) |
| Non-goals | Visual design, layout, modules, APIs, ranking code ownership |
| Change bar | Any feature that would violate §8 requires updating this document first |
