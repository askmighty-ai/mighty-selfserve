# Home OS — Domain Model

**Status:** Canonical domain authority  
**Audience:** Product, design, engineering  
**Scope:** Entities, ownership, lifecycle, projection, ranking, and invariants for Home OS.

**Non-goals:** UI, rendering, CSS, components, routes, visual layout, APIs, modules, code, storage schemas.

When Home OS data meaning is ambiguous, this document wins.

Behavioral authority (what Home must do): [HOME_OS_BEHAVIOR.md](HOME_OS_BEHAVIOR.md)

---

## Document relationship

| Document | Authority |
|----------|-----------|
| [HOME_OS_BEHAVIOR.md](HOME_OS_BEHAVIOR.md) | Behavioral contract — product rules of Home |
| **This document** | Domain model — canonical entities and their semantics |
| Implementation docs | How systems realize the model — never redefine it |

Domain terms used here are normative. Synonyms in older docs map into these entities; they do not create parallel models.

---

## 1. HomeState

`HomeState` is the complete, self-contained snapshot of everything required to present Home for one user at one instant.

It is **disposable and reproducible**: any valid `HomeState` can be discarded and regenerated from canonical inputs plus the projection clock. It is not a system of record.

### Ownership

| Concern | Owner |
|---------|--------|
| Producing `HomeState` | `HomeProjection` (pure) |
| Mutable Work Item lifecycle overlays (defer, dismiss decision, expansion choice constraints) | Owning domain systems behind Work Items — not `HomeState` |
| Canonical facts (accounts, opportunities, authorizations, changes, capability) | Their respective domain owners |
| Holding `HomeState` as durable truth | **Forbidden** — no owner may treat `HomeState` as source of truth |

`HomeState` has no independent lifecycle store. Its “lifecycle” is: projected → consumed → discarded.

### Lifecycle

```text
(canonical inputs + overlays + now)
        ↓
   HomeProjection
        ↓
    HomeState        ← ephemeral
        ↓
   consumed / discarded
```

- Created only by projection.
- Valid only for the `as_of` instant used to project it.
- Never patched in place as business truth; regenerate instead.
- May be cached as a derived artifact; cache invalidation must not invent new semantics.

### Projection rules

1. Projection is **pure**: same inputs, same overlays, same `as_of` ⇒ identical `HomeState`.
2. Projection **reads** canonical models and overlays; it does not write them.
3. Projection **ranks** Work Items according to the Ranking contract (§6); it does not invent Work Items.
4. Projection selects at most one **expanded** Work Item.
5. Projection includes Coverage and Proof only as disclosed domain content, not as competing work.
6. Projection omits empty optional collections rather than fabricating placeholders.
7. Projection never embeds presentation instructions (theme, layout, component choice).

### What it may contain

| Member | Meaning |
|--------|---------|
| `as_of` | Instant the snapshot describes |
| `status` | Derived answer to “What needs me?” (calm / needs user / value waiting / setup incomplete — semantic modes, not presentation) |
| `work_queue` | Ordered list of effective `WorkItem`s |
| `expanded_work_item_id` | Id of the single expanded item, or none when calm |
| `coverage` | Ordered/set of `CoverageItem`s for disclosure |
| `proof` | Ordered list of `ProofItem`s after collapse/grouping |
| `silence` | Whether the queue is effectively calm (no expanded work) |
| Provenance references | Ids/refs back to canonical sources (opaque to presentation) |

### What it must never contain

- Mutable business ledgers or write-intent commands
- UI trees, layout hints, styles, copy templates as authority
- Routes, deep links, or navigation graphs as domain truth
- Multiple expanded Work Items
- Fabricated Proof or Coverage
- Ranking scores as durable fields on Work Items (scores are ranking outputs, not Work Item identity)
- Secrets (passwords, raw credentials)
- Parallel “shadow” queues that bypass ranking

---

## 2. WorkItem

`WorkItem` is the canonical unit of user work in Home OS.

There are exactly four types: `interrupt` | `approval` | `opportunity` | `setup`  
(Semantics of each type: [HOME_OS_BEHAVIOR.md](HOME_OS_BEHAVIOR.md) §2.)

### Fields and semantics

| Field | Semantics |
|-------|-----------|
| `id` | Stable unique identity for this Work Item instance. Used for expansion selection, overlays (defer/dismiss), lifecycle transitions, and ranking tie-break. Deterministic from owning-domain identity where possible. |
| `type` | One of `interrupt`, `approval`, `opportunity`, `setup`. Fixed for the lifetime of the item; type changes require a new item (or explicit supersession), never silent mutation. |
| `priority` | Intrinsic class priority used as the first ranking key. Must be consistent with `type` (Interrupt highest, then Approval, then blocking Setup vs Opportunity per behavioral ranking). Not a free-form score. |
| `title` | Short structured claim of what needs the user. Domain content, not layout. Must be truthful and specific enough to distinguish items. |
| `summary` | Supporting explanation: why this needs the user, what happens after action. No passwords; no internal enum dumps as primary meaning. |
| `evidence` | Structured facts that justify the item (account refs, amounts, deadlines, agent action payload summary, capability gap). Evidence supports trust; it is not a second Work Item. |
| `primary_action` | The single action that advances or resolves the item under normal completion. Exactly one. Machine-meaningful action descriptor plus human-meaningful intent. Routine resolution must be Home-centered (behavioral invariant). |
| `secondary_action` | Optional alternate: reject (approvals), dismiss, defer, or limited disclosure. Never a second primary. May be absent. |
| `dismissible` | Whether the user may dismiss this instance. Constrained by type policy (e.g. Approvals are not casually dismissible; hard Interrupts are not dismissible). |
| `deferrable` | Whether the user may defer this instance into a quiet window. Constrained by type policy. |
| `created_at` | When this Work Item instance entered Created (first materialized from its owning fact). |
| `updated_at` | When material fields or lifecycle state last changed. |
| `expires_at` | Intrinsic deadline after which the item is no longer actionable, if any. `null` means no intrinsic expiry. Expiration is a domain event, not a user clear ritual. |
| `proof_reference` | Optional link to a `ProofItem` (or proof identity) produced by this item’s resolution path. Set when Proof exists; absent when resolution produced no Proof. |
| `provider` | Optional subject provider identity when the work is about one provider relationship. |
| `capability` | Optional capability or access axis involved (e.g. discovery, session, capture, agent authorization). Clarifies *which* system gap or power the work concerns. |
| `state` | Lifecycle state per §7: `created` \| `visible` \| `expanded` \| `deferred` \| `completed` \| `proof` \| `archived` (and legal intermediates defined there). |

### Ownership of a WorkItem

Every `WorkItem` has **exactly one owning domain** that is authoritative for its existence and factual payload (e.g. attention/auth for Interrupts, authorization for Approvals, value facts for Opportunities, enrollment/capability for Setup).

Home OS consumes Work Items; it does not become a second owner of their facts.

### Effectiveness

A Work Item is **effective** when it is eligible for the Work Queue under overlays and time:

- Not archived  
- Not completed (unless in the brief Proof-associated transition window defined by lifecycle)  
- Not deferred with an active quiet window  
- Not expired (`as_of` < `expires_at` or `expires_at` is null)  
- Not suppressed by an owning-domain overlay that marks it inactive  

Only effective items appear in `HomeState.work_queue`.

---

## 3. ProofItem

`ProofItem` is evidence that Mighty (or the user/agent through Mighty) successfully performed **material, true work**.

Proof is not a Work Item. Proof cannot create Work Items.

### Lifecycle

```text
Earned (qualifying outcome occurs)
    ↓
Recorded (ProofItem exists)
    ↓
Visible in HomeState.proof (if retained and not collapsed away)
    ↓
Collapsed / grouped (presentation of the list may compress; identity remains)
    ↓
Aged out of Home disclosure (retention policy)
    ↓
Archived or retained only in owning audit stores (outside HomeState)
```

- **Earned** only from qualifying outcomes (see behavioral Proof contract).
- Dismiss/defer of a Work Item does not earn Proof by itself.
- Background success on healthy accounts does not earn Proof.

### Retention

- Retained long enough to support trust and consequential audit.
- Approval decisions and verified agent outcomes retain at least as strictly as soft benefit notices.
- Immaterial noise is never retained as Proof.
- Home disclosure may show a recent window; longer retention may live only in owning audit domains.

### Ordering

- Primary order: outcome time descending (newest material Proof first).
- Tie-break: stable deterministic id ascending.
- Ordering uses outcome time, not the time the user last opened Home.

### Collapse

- Multiple low-impact similar events may collapse into one disclosed summary row while preserving that underlying Proof identities exist.
- Collapse never fabricates outcomes.
- Collapse never promotes Proof into a Work Item or primary action.

### Grouping

- Grouping keys are semantic (same provider + same outcome class + same day band, or equivalent policy), not visual.
- Groups have a representative summary and a count or member refs.
- Grouping is a projection concern over Proof records; it does not merge distinct audit identities in owning systems.

---

## 4. CoverageItem

`CoverageItem` represents **one provider under observation** (or one candidate provider slot in inventory).

Coverage is disclosure. A `CoverageItem` does not directly modify Work Items.

### Fields / facets

| Facet | Semantics |
|-------|-----------|
| `provider` | Canonical provider identity (and display-stable name key). One CoverageItem per provider relationship under observation for the user. |
| `status` | Coarse observational posture: e.g. enrolled, candidate, unsupported, removed. Not a substitute for health axes. |
| `health` | Honesty about whether Mighty can currently do its job for this provider—derived from verification/auth/monitoring, never a fake “green connected” collapse. |
| `capabilities` | What Mighty can do for this provider (discover, capture balances, track benefits, support agent actions, etc.). Unsupported capabilities are explicit. |
| `verification` | Whether session/data has been verified, pending, failed, or never attempted—plain semantic states. |
| `discovery` | How the provider entered inventory (gmail, manual add, agent, etc.) and whether discovery is complete. |
| `authentication` | Session/auth posture: valid, missing, expired, unknown—separate from discovery and from monitoring freshness. |
| `monitoring` | Whether Mighty is actively watching; last successful observation meaning; waiting-for-first-visit honesty. |

### Rules

- Discovery, authentication, and monitoring are **separate axes**. They must not be collapsed into a single boolean.
- Unsupported providers are valid CoverageItems with explicit unsupported capability/status.
- CoverageItems may *inform* Setup or Interrupt Work Items created by owning domains; Coverage itself does not enqueue work.

---

## 5. HomeProjection

`HomeProjection` is the pure function that produces `HomeState`.

### Contract

```text
HomeProjection(
  canonical_models,
  work_item_overlays,
  as_of
) → HomeState
```

### Requirements

1. **Pure** — no side effects; no writes to business state; no hidden clock (time is an input: `as_of`).
2. **Owns no mutable business state** — overlays and facts are inputs owned elsewhere.
3. **Consumes canonical models only** — Work Items (or their owning canonical records mapped to Work Items), Coverage sources, Proof sources, capability/enrollment facts as defined by domain owners. No scraping UI state. No reading presentation caches as truth.
4. **Applies Ranking (§6)** to order the effective Work Queue and choose the expanded item.
5. **Applies Proof collapse/grouping** for the disclosure list.
6. **Derives `status` and `silence`** from the ranked effective queue — does not invent a second policy.
7. **Contains no product mutation logic** — completing, deferring, dismissing, approving are commands on owning domains; projection only reflects results after those domains change.

### Non-ownership

HomeProjection does **not** own:

- Work Item creation  
- Ranking policy definition (it *applies* the Ranking contract)  
- Proof earning rules (it *selects* earned Proof for disclosure)  
- Coverage enrollment  
- User preferences storage  

Those belong to their domain owners; projection assembles a disposable HomeState.

---

## 6. Ranking

Ranking is a **deterministic total order** over effective Work Items.

Ranking is defined separately from any algorithm implementation. Implementations must realize this contract; they must not redefine it silently.

### Determinism

Given the same set of effective Work Items and the same `as_of`:

- The ordered queue is identical  
- The expanded item is identical  
- Tie-breaks never use randomness, wall-clock “now” inside the ranker, or input list order  

### Ranking keys (in order)

Items are compared by the following keys, earlier keys dominating later keys.

#### 1. Urgency (class + severity)

- Work Item `type` / `priority` class order per behavioral ranking: Interrupt → Approval → blocking Setup → Opportunity → non-blocking Setup.
- Within class, intrinsic severity/urgency band (hard block before soft; higher consequence before lower).

#### 2. Time sensitivity

- Earlier `expires_at` wins among items where expiry is meaningful.
- Items with no expiry sort after items with expiry in the same band (unless policy marks them permanently blocking—blocking Interrupts still outrank expiring Opportunities by class).

#### 3. User effort

- Prefer the item that unblocks the most dependent work with the least additional user steps **only as a within-band tie assist**, never to promote an Opportunity over an Interrupt.
- Effort is an attribute of the action (sign-in vs multi-step recovery), not a preference for “easy busywork.”

#### 4. Dependency

- If Work Item A blocks the resolution or validity of Work Item B, A ranks above B.
- Dependency edges come from owning domains (e.g. auth block before opportunity on same provider). Projection/ranking must not invent dependencies.

#### 5. Confidence

- Higher confidence that the item is real and actionable ranks above speculative items **within the same class and time band**.
- Low-confidence items must not outrank high-confidence items of a higher class.
- Confidence never fabricates work; it only orders existing effective items.

### Final tie-break

If all keys tie:

1. `provider` ascending (missing provider sorts as empty)  
2. `id` ascending  

### Expanded selection

- The first item in the total order is the expanded Work Item when the effective queue is non-empty.
- Empty effective queue ⇒ no expanded item ⇒ calm.

### Explicit non-inputs

Ranking must not use:

- Recency of user visits for engagement  
- Random boosts  
- Marketing priority  
- UI A/B presentation flags as domain rank  

---

## 7. State Machine

Applies to every `WorkItem`. Type policy constrains which transitions are legal; the graph below is the shared skeleton.

### States

| State | Meaning |
|-------|---------|
| `created` | Materialized from owning facts; not yet eligible for Home disclosure. |
| `visible` | Effective and in the Work Queue; not expanded. |
| `expanded` | Sole expanded item in `HomeState`; primary action is available. |
| `deferred` | Quieted by user/policy until a window ends or condition escalates. Not in effective queue while window holds. |
| `completed` | Terminal success/decision path reached (resolved, approved/rejected, acted, setup unlocked, or equivalent type completion). |
| `proof` | Completion has an associated earned `ProofItem`; item is bound to that proof reference before archival. |
| `archived` | No longer part of Home work. Retained only as history in owning domains if needed. |

### Canonical happy path

```text
Created
  → Visible
  → Expanded
  → Completed
  → Proof        (when Proof is earned)
  → Archived
```

Deferral branch:

```text
Visible | Expanded
  → Deferred
  → Visible      (window ended or condition still valid)
  → Expanded     (if ranking selects it)
```

### Legal transitions

| From | To | When |
|------|-----|------|
| `created` | `visible` | Item becomes effective and enters the queue. |
| `created` | `archived` | Superseded/invalid before ever becoming visible (deduped, fact withdrawn). |
| `visible` | `expanded` | Ranking selects it as top effective item. |
| `visible` | `deferred` | User/policy defers; `deferrable` must be true. |
| `visible` | `completed` | Resolved without expansion (e.g. auto-clear when underlying block lifts). |
| `visible` | `archived` | Expired or withdrawn without completion/Proof. |
| `expanded` | `visible` | Demoted because ranking selected a higher item (rare same-cycle) or queue reprojected. Prefer: previous expanded completes/defers; new projection expands the next. |
| `expanded` | `deferred` | User/policy defers; `deferrable` must be true. |
| `expanded` | `completed` | Primary action succeeds, approval decision recorded, opportunity consumed, setup unlocked, or owning fact clears as resolved. |
| `expanded` | `archived` | Dismissed when `dismissible`, or expired while expanded without completion. |
| `deferred` | `visible` | Quiet window ends and item still effective; or material worsening reactivates. |
| `deferred` | `completed` | Underlying condition resolves while deferred. |
| `deferred` | `archived` | Expired, dismissed (if allowed), or withdrawn while deferred. |
| `completed` | `proof` | Qualifying Proof is earned and `proof_reference` set. |
| `completed` | `archived` | Completion earns no Proof (allowed for some types/outcomes). |
| `proof` | `archived` | Proof binding recorded; item leaves Home work. |

### Illegal transitions (examples)

- `proof` → `visible` / `expanded` (Proof cannot reopen work)  
- `archived` → any active state except via **new** Work Item identity  
- `completed` → `deferred`  
- Any state → `expanded` for two items simultaneously (enforced at HomeState, not by dual expanded states)  
- Creating Proof from `deferred` or dismiss without `completed` + qualifying outcome  

### Type-specific constraints

| Type | Constraints |
|------|-------------|
| **Interrupt** | Hard interrupts: `dismissible=false`. Soft interrupts may archive via dismiss only if policy allows. Completion when blocker clears—even if user never acted. |
| **Approval** | No casual dismiss. Secondary reject is completion (decision), not dismiss. Defer only if policy allows. Proof always for the decision; possible second Proof later for agent outcome (separate ProofItem; may reference related work, not reopen this item). |
| **Opportunity** | Dismiss and defer allowed. Dismiss/defer → archive or deferred without Proof. Proof only if value captured / meaningful change recorded. |
| **Setup** | Hard setup cannot archive into calm via dismiss while capability remains blocking. Soft orientation may archive via dismiss. |

---

## 8. Invariants

Hard guarantees. Future features cannot violate these.

1. **Every WorkItem has exactly one owner** (one authoritative domain for its facts).
2. **HomeState is disposable and reproducible** from canonical inputs, overlays, and `as_of`.
3. **HomeProjection is pure** and owns no mutable business state.
4. **Projection owns no business mutation logic** — it assembles; owners decide.
5. **HomeProjection consumes canonical models only** — never UI state as truth.
6. **At most one WorkItem is expanded** in any HomeState.
7. **Ranking is deterministic** for identical effective inputs and `as_of`.
8. **Proof cannot create WorkItems.**
9. **Coverage cannot directly modify WorkItems.**
10. **WorkItems do not own Proof ledgers** — they may only reference Proof they earned.
11. **Dismiss/defer never fabricate Proof.**
12. **WorkItem.type is immutable** for an id; new meaning ⇒ new id (or explicit supersession record).
13. **Discovery, authentication, and monitoring remain separate** on CoverageItem.
14. **Unsupported coverage is representable** without fake enrollment.
15. **Expired items leave the effective queue** without requiring a user clear ritual.
16. **Secondary action never equals primary action.**
17. **HomeState never becomes a system of record.**
18. **No parallel Home-local WorkItem model** may diverge from this canonical WorkItem.
19. **Calm HomeState is valid** — empty effective queue is success, not an error state.
20. **This document is the canonical domain authority for Home OS** — implementations may change; these entities and invariants may not without an explicit domain revision.

---

## Term map (non-normative)

Older or adjacent vocabulary maps into this model; it does not redefine it.

| Adjacent term | Home OS domain |
|---------------|----------------|
| Attention candidate / primary | May source or map to `WorkItem` (typically Interrupt / Opportunity) |
| Recent win / meaningful change | May source `ProofItem` |
| Account health / inventory row | `CoverageItem` |
| Daily brief / featured story | Derived views of `HomeState.status` + expanded `WorkItem` — not separate domain entities |
| Home projection DTO | Realization of `HomeState` — must obey this model |

---

## Document control

| Field | Value |
|-------|--------|
| Name | Home OS Domain Model |
| Authority | Canonical domain |
| Depends on | [HOME_OS_BEHAVIOR.md](HOME_OS_BEHAVIOR.md) for type behavior |
| Forbids | UI, layout, APIs, code, storage engine choice |
| Change bar | Any feature that would violate §8 requires updating this document first |
