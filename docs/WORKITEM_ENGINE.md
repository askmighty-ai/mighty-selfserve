# WorkItem Engine

**Status:** Implemented (domain engine)  
**Authority:** [HOME_OS_BEHAVIOR.md](HOME_OS_BEHAVIOR.md) · [HOME_OS_DOMAIN_MODEL.md](HOME_OS_DOMAIN_MODEL.md)  
**Package:** `mighty.workitem`

Pure, reusable Home OS domain engine. Any UI (or none) may consume it. It does not render, route, or persist.

---

## Architecture

```text
Owning domains
  → WorkItem[] + CoverageItem[] + ProofItem[] + WorkItemOverlay[]
       ↓
  WorkItemLifecycle   (complete / defer / dismiss / expire / proof)
       ↓
  effective_work_items(overlays, as_of)
       ↓
  rank_work_items(as_of)          ← deterministic total order
       ↓
  project_home(...)               ← pure HomeProjection
       ↓
  HomeState                       ← disposable snapshot
```

| Module | Role |
|--------|------|
| `mighty.workitem.model` | Canonical `WorkItem` + actions / evidence / enums |
| `mighty.workitem.state_machine` | Legal lifecycle transitions + type-policy guards |
| `mighty.workitem.ranking` | Deterministic ranking contract (§6) |
| `mighty.workitem.lifecycle` | Command orchestration (complete, defer, expire, proof) |
| `mighty.workitem.proof` | `ProofItem` + disclosure collapse/grouping |
| `mighty.workitem.coverage` | `CoverageItem` disclosure model |
| `mighty.workitem.projection_inputs` | `CanonicalModels`, `WorkItemOverlay`, effectiveness |
| `mighty.workitem.projection` | Pure `project_home` → `HomeState` |
| `mighty.workitem.home_state` | Canonical disposable `HomeState` |

**Naming note:** Canonical `HomeState` / `project_home` live under `mighty.workitem`. They are distinct from the legacy Living Calm helpers in `mighty.home_state` / `mighty.home_projection`, which remain presentation composition for the current Home surface.

---

## Responsibilities

### Owns

1. **Canonical WorkItem contract** — identity, type, priority class, evidence, actions, lifecycle state fields.
2. **State machine** — legal transitions (`created` → `visible` → `expanded` → `completed` → `proof` → `archived`, plus deferral branch).
3. **Ranking** — deterministic total order over effective items (class → urgency band → expiry → effort → dependency → confidence → provider → id).
4. **Lifecycle commands** — pure functions that return updated `WorkItem` / optional `ProofItem` values (caller persists).
5. **Effectiveness filtering** — apply overlays + expiry + terminal states before ranking.
6. **HomeProjection** — assemble disposable `HomeState` (status, queue, expanded id, coverage, proof, silence).
7. **Proof disclosure ordering/collapse** — newest first; low-impact same-day groups.

### Does not own

- Creating Work Items from platform facts (Attention, enrollment, benefits, etc.)
- Persistence / databases / migrations
- HTTP routes or commands
- UI, HTML, CSS, JavaScript, copy templates as authority
- Coverage enrollment or auth truth projection
- Notification delivery
- Ranking *policy definition* beyond implementing the published contract

---

## Ranking contract (implemented)

Effective items compare by keys, earlier dominating later:

1. **Class / priority** — Interrupt → Approval → blocking Setup → Opportunity → non-blocking Setup  
2. **Urgency band** — hard → high → normal → soft  
3. **Time sensitivity** — earlier `expires_at`; missing expiry sorts last within band  
4. **Effort** — lower `effort_weight` within band  
5. **Dependency** — declared `blocks` edges: blocker above blocked  
6. **Confidence** — higher first within band  
7. **Tie-break** — `provider` ascending (missing as `""`), then `id` ascending  

Explicit non-inputs: visit recency, randomness, marketing priority, input list order, UI flags.

Expanded selection: first item in the total order, or none when calm.

---

## Lifecycle summary

| Command | Typical transition | Proof? |
|---------|--------------------|--------|
| `make_visible` | created → visible | No |
| `expand` | visible → expanded | No |
| `defer` | visible\|expanded → deferred | No (returns `deferred_until` for overlay) |
| `reactivate` | deferred → visible | No |
| `complete` | → completed [→ proof] | Approvals always; others when `earn_proof=True` |
| `dismiss` | → archived | **Never** |
| `expire` | → archived or approval proof | Approvals record expired decision |
| `bind_proof` / `create_proof_for_completion` | completed → proof | Yes |

Dismiss/defer alone never fabricate Proof.

---

## Projection API

```python
from datetime import datetime, timezone
from mighty.workitem import (
    CanonicalModels,
    WorkItemOverlay,
    project_home,
)

state = project_home(
    CanonicalModels(
        work_items=items,
        coverage=coverage_items,
        proof=proof_items,
    ),
    overlays,
    as_of=datetime.now(timezone.utc),
)
# state.status, state.work_queue, state.expanded_work_item_id,
# state.coverage, state.proof, state.silence
```

Requirements:

- Pure: same inputs + overlays + `as_of` ⇒ identical `HomeState`
- Time is an argument (no hidden wall clock)
- Reads only; never writes business ledgers
- At most one expanded Work Item
- Empty effective queue ⇒ calm / silence

---

## Extension points

| Extension | How |
|-----------|-----|
| New owning-domain producers | Map domain facts → `WorkItem` with correct `type` / `priority` / `owner_domain`; do not fork a parallel model |
| Overlay store | Persist `WorkItemOverlay` (defer until, dismissed, inactive); feed into `project_home` |
| Proof earning rules | Call `WorkItemLifecycle.complete(..., earn_proof=True)` or `create_proof_for_completion` from owners when outcomes qualify |
| Dependency edges | Set `WorkItem.blocks` from owning domains (auth before opportunity on same provider, etc.) |
| Retention / loaders | Filter Proof/Coverage before constructing `CanonicalModels`; engine collapses only disclosure shape |
| UI / delivery consumers | Read `HomeState` only; never re-rank or invent Work Items in the surface |

---

## Prohibited responsibilities

The WorkItem engine **must not**:

1. Implement Home UI, templates, CSS, or JavaScript  
2. Add Flask routes or HTTP handlers  
3. Perform external API calls  
4. Own database schemas or migrations (unless a future persistence adapter is explicitly scoped)  
5. Treat `HomeState` as a system of record  
6. Re-define ranking silently (change docs first)  
7. Fabricate Proof from dismiss/defer  
8. Create Work Items from Coverage or Proof  
9. Deep-link routine resolution as domain truth  
10. Store durable ranking scores on `WorkItem`  
11. Mutate `WorkItem.type` for an existing id  
12. Expand more than one Work Item in a `HomeState`

---

## Tests

`tests/test_workitem_engine.py` covers:

- Ranking class order and within-class urgency  
- Tie-breaking (provider, id, input-order independence)  
- State transitions (happy path + illegal)  
- Expiration (opportunity archive, approval proof)  
- Defer + overlay quiet windows  
- Completion with and without Proof  
- Proof creation and collapse  
- HomeProjection determinism, calm, status modes  

```bash
.venv/bin/python -m pytest tests/test_workitem_engine.py -q
```

---

## Document control

| Field | Value |
|-------|--------|
| Name | WorkItem Engine |
| Implements | Home OS domain model + behavioral Work Item / queue / ranking contracts |
| Non-goals | UI, routes, persistence, owning-domain producers |
| Change bar | Behavioral/domain contract changes require updating HOME_OS_* docs first |
