# Intelligent Attention — Milestone 4 Design Note

**Status:** In progress  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.3 / §4.4 / §6 / §7  
**Depends on:** Milestone 3 ([ATTENTION_PLATFORM_ADOPTION.md](ATTENTION_PLATFORM_ADOPTION.md))

## Objective

Expand the Attention Platform with new producers and delivery capabilities while preserving the architecture established in Milestones 1–3.

This milestone **extends** the platform. It does **not** redesign it.

## Current state (origin/main at M4 start)

| Layer | Status |
|-------|--------|
| AttentionItem / ranking / overlays / Store | Done |
| Producers | Auth blocker, access_degraded, authorize only |
| Engine `read_attention` | Loads AuthTruth + AuthorizeRow → gather → compose |
| AttentionView + Home/Worker cutover | Done (default on) |
| Benefit / Worker / data_gap producers | Missing |
| AttentionSupervisor | Constant only (`IN_FLIGHT_TIMEOUT_SECONDS`) |
| Delivery + receipts + `/api/attention` | Missing |

## Architectural impact

No new ranking table. No consumer-side policy. No provider branching in shared policy.

```text
DB facts
  → loaders (AuthTruth, AuthorizeRow, AccountState→gap, WorkerSignal, BenefitSignal)
  → compile_attention_candidates (gather only)
  → compose_attention (overlays + select_attention)
  → AttentionState
  → AttentionView(surface) → Home / Worker / Push
```

| Concern | Owner (unchanged) |
|---------|-------------------|
| Domain logic | Per-producer compilers |
| Gather only | `compile_attention_candidates` |
| Compose / no business policy | `AttentionEngine` |
| Overlay CRUD | `AttentionStore` |
| Timeout clear + orphan GC | `AttentionSupervisor` (new) |
| Channel fan-out of primary | `AttentionDelivery` (new) |
| Presentation | `AttentionView` only |
| Consumers | Render view; never re-rank |

## Interfaces

### New compiler inputs (pure dataclasses)

```text
BenefitSignal
  user_id, provider, field_key, btype, label, value,
  days_left, exp_date, urgency ("urgent"|"soon"|"info"),
  kind ("expiring"|"opportunity"), observed_at

WorkerSignal
  user_id, installed, reachable, last_seen_at,
  version, update_required

AccountState[] (existing)
  → data_gap when connected + data_status in {none, partial}
```

### Gather signature extension

```python
compile_attention_candidates(
    *,
    auth_truths=(),
    authorize_rows=(),
    account_states=(),   # data_gap
    worker_signal=None,  # system
    benefit_signals=(),  # value_at_risk + opportunity
) -> tuple[AttentionItem, ...]
```

Order within gather (stable families, still not ranking):

1. AuthTruth → auth_blocker | access_degraded  
2. AuthorizeRow → agent_authorization  
3. WorkerSignal → system (at most one)  
4. BenefitSignal → value_at_risk | opportunity (mutually exclusive per field_key)  
5. AccountState → data_gap  

### Supervisor

```python
run_attention_supervisor(db, *, now) -> AttentionSupervisorResult
# - clear in_flight overlays with started_at older than 30m
# - delete overlays whose attention_id is absent from current candidates
# No browser I/O.
```

### Delivery

```python
deliver_attention_primary(db, user_id, *, now, state) -> DeliveryAttempt | None
# Targets AttentionState.primary only.
# Records attention_delivery_receipt; emits attention.delivered / delivery_failed.
# Failures never raise to Home/Worker/sync callers.
```

### HTTP (thin adapters over Store)

```text
GET  /api/attention/view?surface=home|worker|…
POST /api/attention/<id>/snooze|dismiss|cta
```

CTA side effects (Access Manager verification enqueue) remain outside Store — route handlers call adapters after a successful overlay write.

## Policy decisions (engineering, within RFC)

| Topic | Decision |
|-------|----------|
| data_gap emit | `connection_state == connected` and `data_status in {none, partial}` |
| Worker system emit | Enrolled accounts exist AND (`not installed` OR `not reachable`); reachability SLA = 72h since `extension_last_seen_at` |
| value_at_risk | Open `action_items` with actionable/attention types and urgency `urgent`/`soon`, or `days_left <= 14` for actionable types |
| opportunity | Open actionable `action_items` that do not qualify as value_at_risk; durable dismiss already Store-enforced |
| Benefit fingerprint | `benefit:{provider}:{field_key}` |
| Delivery channels (v1) | Blocker primary → web push (if enabled) + worker already has view; time_sensitive → optional push; opportunity/informational → no push |
| Supervisor schedule | Same pattern as verification maintenance (~1m loop); failures swallowed |

## Proposed implementation order

| PR | Theme | Why this order | Status |
|----|-------|----------------|--------|
| 1 | This Design Note | Align reviewers before code | done |
| 2 | data_gap producer + loader | AccountState already listed by engine; lowest new surface | done |
| 3 | WorkerSignal → system | Heartbeat columns exist; unblocks setup hero | done |
| 4 | BenefitSignal → value_at_risk + opportunity | Reuses `action_items`; enables rank 5–6 | done |
| 5 | AttentionSupervisor | Unblocks stuck in_flight; GC orphans | in progress |
| 6 | HTTP attention commands + read view | Surfaces can drive overlays without inventing policy | pending |
| 7 | AttentionDelivery + receipts | Push uses primary only; observability for SLA | pending |
| 8 | Replay/e2e + metrics + RFC status | Harden + close milestone | pending |

Each PR stays focused, keeps tests green, and updates its module doc.

## Risks

| Risk | Mitigation |
|------|------------|
| Double policy vs legacy action_items UI | Producers read facts only; Home continues to render AttentionView; do not reintroduce Home ranking |
| Benefit spam / opportunity storms | Durable dismiss; no default push for opportunity; gather caps none — ranking + View window |
| Worker false SYSTEM (laptop closed) | 72h SLA; document; prefer silent when recently seen |
| data_gap noise while first sync | Emit only when `connected`; ranking below auth; View copy is informational |
| Delivery blocking sync | Delivery never on GET account-status hot path; supervisor/delivery failures logged only |
| Orphan overlay GC racing CTA | GC keyed off candidate absence; in_flight timeout independent |

## Testing strategy

- **Unit / golden:** each new producer (emit / no-emit tables), gather order, mutual exclusion (auth vs degraded; VAR vs opportunity).
- **Engine replay:** fixture DB with account_state + action_items + extension columns → expected AttentionState primary class.
- **Overlay lifecycle:** start_cta → supervisor timeout → clear; dismiss opportunity → hidden; snooze blocker → suppressed.
- **Delivery:** receipt written on success; failure recorded without raising; primary-only target.
- **Consumer safety:** forced engine exception still returns Home/Worker payload.
- **No provider branching** assertions in shared ranking/policy tests.

## Non-goals (Milestone 4)

- Trust producer / Runtime focus CTA after Runtime API auth (RFC P5)
- Multi-item push, email channel as primary delivery
- Household attention
- Replacing AccountState per-account repair copy
- Redesigning ranking, SilenceVerdict, or AttentionItem schema

## Success criteria

- New producers plug into existing compiler/engine architecture  
- No consumer implements its own attention policy  
- AttentionState remains SSoT; AttentionView presentation-only  
- Existing invariants intact; relevant tests pass; docs updated  
