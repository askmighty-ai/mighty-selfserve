# Autonomous Attention — Milestone 5 Design Note

**Status:** In progress  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.2 / §6 / §7 / Part X / P5  
**Depends on:** Milestone 4 ([ATTENTION_INTELLIGENT.md](ATTENTION_INTELLIGENT.md))

## Objective

Strengthen the production Attention Platform — trust producer, supervisor/delivery robustness, product metrics, and objective cutover retirement — **without** changing the core architecture from Milestones 1–4.

This milestone makes the platform production-ready. It does not redesign it.

## Current state (origin/main at M5 start)

| Layer | Status |
|-------|--------|
| Producers | auth, authorize, access_degraded, system(worker), value_at_risk, opportunity, data_gap |
| Trust producer | Missing (enums/ranking/View stubs only) |
| `runtime_access_state` module | **Absent from tree** (AuthTruth soft-imports; local pycache only) |
| AttentionSupervisor | in_flight 30m clear + orphan GC |
| Delivery | Primary push + receipts; no SLA breach / retry |
| Product metrics | Shadow compare only; RFC Part X metrics not computed |
| Cutover flags | Default `on`; legacy probes still always recorded |

## Architectural impact

No new ranking table. No consumer policy. No provider branching in shared policy.

```text
runtime_access_state (+ AccountState primary method)
  → TrustSignal → compile_trust_attention
  → gather (before auth family or after? — see order)
  → existing compose / View / delivery / supervisor
```

| Concern | Owner |
|---------|-------|
| Trust domain logic | `compile_trust_attention` only |
| Runtime publication store (minimal read/write) | `runtime_access_state` (thin; not Control Center) |
| in_flight timeout / orphan GC / reopen log | AttentionSupervisor |
| Delivery retry + SLA breach | AttentionDelivery (+ metrics) |
| Product counters | `attention_metrics` |
| Cutover retirement criteria | docs + consumer gating |

## Interfaces

### TrustSignal (pure)

```text
TrustSignal
  user_id, provider
  access_method                 # must be managed_runtime to emit
  authentication_state
  access_health
  recovery_state
  runtime_state
  escalation_reason
  updated_at / observed_at
  needs_human                   # if True → producer returns None (auth owns)
  interruption_expected
  presentation_status           # never_reported|runtime_offline|awaiting_user|stale|…
```

### Emit rules (mutual exclusion)

| Condition | Output |
|-----------|--------|
| `access_method != managed_runtime` | `None` |
| `needs_human` | `None` (auth_blocker path) |
| `presentation_status in {awaiting_user, runtime_offline, never_reported}` | `trust` blocker |
| `presentation_status == stale` and not healthy signed-in | `trust` blocker |
| otherwise | `None` |

| Field | Value |
|-------|-------|
| class / urgency | `trust` / `blocker` |
| fingerprint | `trust:{provider}:runtime` (stable; reason may change via updated) |
| attention_id | `att_{user}_trust_{provider}` |
| source_kind / source_ref | `trust` / `runtime_access_state:{user}:{provider}` |
| cta_key | `focus_managed_runtime` |
| reason | `trust` or more specific machine code (`awaiting_user`, `runtime_offline`, …) |

**Runtime focus bridge:** still gated — `command_cta` records in_flight; does **not** invent a Runtime control API. View continues to resolve a provider/setup URL. Full Runtime bridge remains post-API-auth (RFC P5).

### Gather order

Insert trust after authorize, before worker (rank-1 class still wins via ranking, not gather order):

1. AuthTruth → auth_blocker | access_degraded  
2. AuthorizeRow  
3. **TrustSignal**  
4. WorkerSignal  
5. BenefitSignal  
6. AccountState → data_gap  

### Supervisor / retry

| Behavior | Owner |
|----------|-------|
| Clear in_flight ≥ 30m | Supervisor (existing) |
| Log `attention.reopened` on timeout clear | Supervisor (new) |
| Orphan GC | Supervisor (existing) |
| Retry failed push when receipt=`failed` and age < retry window | Delivery (new) |
| Cap retries (e.g. 3) / backoff | Delivery receipts detail |

### Metrics (`attention_metrics`)

Computed on supervisor/delivery heartbeat (never on GET hot path):

| Metric | Definition |
|--------|------------|
| `autonomous_coverage` | Among enrolled managed_runtime accounts: share with healthy Runtime publication and no trust/auth_blocker |
| `false_silence` | Primary ranks 1–4 visible ≥ blocker SLA (60s) with no successful push receipt (when push enabled) |
| `false_interruption` | Primary ranks 1–4 with `interruption_expected=true` (expected) vs unexpected human blockers rate — counter of unexpected visible auth/trust blockers |
| `delivery_sla` | Share of blocker primaries with successful receipt within 60s of first observation / first delivery attempt |

Persist latest snapshot per user (or global rollup row) in `attention_metric_snapshot`.

### Cutover retirement criteria (objective)

Remove `ATTENTION_CUTOVER_*` rollback surface when **all** hold for ≥ 7 consecutive days in production (or staging soak):

1. Cutover mode already `on` for Home and Worker  
2. `platform_failure` rate < 0.5% of attention consumer calls  
3. Shadow `old_active_new_silent` (false silence candidate) < 2% of compares  
4. Delivery `false_silence` (SLA) < 5% of push-eligible blocker primaries  
5. No open Sev-1/2 attributable to Attention mis-ranking  

Until then: keep flags; stop **requiring** legacy probes when mode=`on` (compare becomes opt-in via `ATTENTION_SHADOW_COMPARE=1`).

## Implementation order

| PR | Theme |
|----|-------|
| 1 | This Design Note + RFC pointer |
| 2 | Minimal `runtime_access_state` read/write + Trust producer + gather/engine |
| 3 | Supervisor reopen logging + delivery retry |
| 4 | Production metrics module + heartbeat hook |
| 5 | Cutover retirement criteria doc + opt-in legacy compare + safe cleanup |

## Risks

| Risk | Mitigation |
|------|------------|
| Trust duplicates auth_blocker | Mutual exclusion on `needs_human` |
| Missing Runtime module blocks AuthTruth/tests | Ship thin `runtime_access_state` without Control Center |
| Metrics on hot path | Only supervisor heartbeat |
| Premature cutover flag removal | Objective criteria; opt-in compare first |
| Inventing Runtime bridge | Explicit non-goal until API auth exists |

## Testing strategy

- Golden TrustSignal emit / no-emit table  
- Mutual exclusion vs auth_blocker  
- Engine replay with managed_runtime + awaiting_user → trust primary  
- Supervisor timeout → reopened log path  
- Delivery retry after failed receipt; SLA breach metric  
- Cutover: mode=`on` without legacy probe still serves AttentionView  

## Non-goals

- Full Provider Runtime Control Center restore  
- Runtime focus bridge API  
- Multi-item push / email primary  
- Redesigning ranking or AttentionItem schema  

## Success criteria

- Trust producer wired through existing compiler architecture  
- Supervisor + in_flight lifecycle hardened (reopen + retry)  
- Production metrics for coverage / false silence / false interruption / delivery SLA  
- Objective cutover retirement criteria documented; obsolete probe path optional  
- Invariants preserved; tests green; docs updated  
