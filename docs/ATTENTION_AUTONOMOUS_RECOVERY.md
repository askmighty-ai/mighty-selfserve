# Autonomous Recovery — Milestone 6 Design Note

**Status:** Complete  
**Milestone report:** [milestones/MILESTONE_6.md](milestones/MILESTONE_6.md)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) · [ACCESS_FLOW.md](ACCESS_FLOW.md) · [AUTH_TRUTH.md](AUTH_TRUTH.md)  
**Depends on:** Milestone 5 ([ATTENTION_AUTONOMOUS.md](ATTENTION_AUTONOMOUS.md))

## User capability

When something breaks, Mighty attempts every safe and appropriate recovery path before asking the user for help.

## Objective

Establish one production recovery flow that converts provider and access failures into autonomous recovery attempts, and produces user attention only when recovery is exhausted or the required step is genuinely human-only.

Consolidate existing Access Manager / AuthTruth / Attention work. Do **not** create a parallel recovery or interruption system.

## Inventory summary (kickoff)

| Existing path | Role vs M6 |
|---------------|------------|
| `provider_access_manager` | **Executor** for browser_session repair (`internal_recovery`) — keep |
| `session_verification` | Job FSM / timeouts — infrastructure, not strategy |
| `provider_access_probe` | Failure classification facts — input to planner |
| `auth_truth` | Access read model — unchanged as projection |
| Attention auth / trust / access_degraded | Human interrupt — **gated** until recovery escalates |
| AttentionSupervisor `in_flight` | CTA overlay timeout — not session recovery |
| AttentionDelivery retry | Notify retry — not session recovery |
| `login_truth` / legacy needs_login | Diagnostic / legacy — do not extend; prefer Attention |
| Recovery Planner module | **Missing** — invent here |

## Ownership boundaries

| Responsibility | Owner |
|----------------|-------|
| Failure facts (PSS, Runtime publication, probe) | Access writers (unchanged) |
| Recovery policy (which capability next) | **Recovery Planner** (pure, deterministic) |
| Recovery lifecycle state + attempt history | **Recovery Store** (one owner) |
| Execute browser_session repair | Provider Access Manager |
| Execute managed_runtime repair | Thin Runtime hooks only when available; else bound-wait → escalate |
| Rank / notify humans | Attention (after escalation only) |
| Provider-specific DOM tactics | Extension / probe — not cross-platform policy |

**Invariants:** Recovery does not rank attention. Attention does not choose recovery strategy. Shared policy does not branch on provider identity unless a documented capability difference requires it.

## Canonical recovery lifecycle

```text
failure facts observed
  → open RecoveryCase (unique per user/provider/root_cause)
  → plan_next_capability (pure)
  → execute capability (PAM / wait / skip-unavailable)
  → record attempt
  → succeeded → close case (no human attention)
  → human-only or exhausted → escalate
  → AttentionCompiler emits one AttentionItem (existing producers)
```

### Case states

| State | Meaning |
|-------|---------|
| `open` | Claimed; planning |
| `running` | Capability execution in flight |
| `waiting` | Bounded backoff before next attempt |
| `succeeded` | Terminal — failure cleared |
| `escalated` | Terminal for autonomy — Attention may interrupt |
| `cancelled` | Underlying failure cleared without escalation |

Exactly one **active** case (`open` / `running` / `waiting`) per `(user_id, provider, root_cause)`.

### Capabilities (rank order)

Deterministic order. Human-only is always last. Unavailable capabilities are recorded as `skipped` and do not block the chain.

1. `session_verify` — Access Manager `trigger_source=internal_recovery`  
2. `silent_reauth` — only if credentials/capability present; else skip  
3. `account_resync` — stale ensure / resync path via Access Manager  
4. `navigation_gap_fill` — only if probe/capability indicates gap; else skip  
5. `deep_probe` — stronger verification cycle via Access Manager  
6. `bounded_wait` — backoff then continue (cap attempts)  
7. `ask_human` — escalate (login / MFA / CAPTCHA / consent / exhausted)

**Immediate `ask_human`:** interruption class ∈ {mfa, captcha, consent} (genuinely human-only).

## Interfaces

```python
# Pure policy — no I/O
plan_recovery(facts: RecoveryFacts, history: RecoveryHistory) -> RecoveryDecision
# decision: next_capability | escalate(reason) | succeed

# Store
ensure_recovery_tables(db)
claim_or_get_case(db, user_id, provider, root_cause, *, now) -> RecoveryCase
append_attempt(...)
transition_case(...)

# Supervisor (heartbeat; never on GET hot path)
run_recovery_supervisor(db, *, now) -> RecoverySupervisorResult
```

### Attention gate

Auth / trust / access_degraded compilers emit **only** when the Recovery Store reports `escalated` for that provider (or an explicit no-recovery path is not applicable — see implementation). Active recovery suppresses obsolete human attention. Successful recovery clears/prevents escalation.

## Implementation order

1. Design note + living report + roadmap current (this PR)  
2. Pure planner + store + unit tests  
3. Executor + supervisor heartbeat + PAM wiring  
4. Attention compiler/loader gate + success clears  
5. Metrics (autonomous recovery coverage, unexpected interruption) + e2e/replay/idempotency/concurrency/isolation tests  
6. Docs close + obsolete parallel policy removal where safe  

## Risks

| Risk | Mitigation |
|------|------------|
| False silence if supervisor off | Default `ENABLE_RECOVERY_SUPERVISOR=true`; escalate path tested |
| Double owners with ensure-due | Case claim + `internal_recovery` trigger; supervisor is strategy owner |
| Trust `awaiting_user` interrupts before recovery | Gate trust compiler on escalation |
| Hot-path side effects | Supervisor/heartbeat only; GETs remain read-only |
| Managed_runtime actuators missing | Skip unavailable capabilities; bound-wait then escalate |

## Testing strategy

- Pure planner golden tests (human-only first; ordered skips; exhaustion)  
- Store idempotency + single active case concurrency  
- Supervisor: timeout, wait, success clears, escalate → AttentionItem  
- Failure isolation: supervisor exceptions never raise into Home/Worker  
- Replay: signed_out → verify → success → no auth_blocker; MFA → immediate escalate → auth_blocker  

## Non-goals

- Full Runtime Control Center restore  
- Redesign of Home / Worker / Attention ranking  
- Multi-item push  
- Credential vault as default path  
- Standalone admin dashboard milestone  

## Success criteria

- Supported failures route through one Recovery Planner  
- Deterministic capability ranking + execution  
- Human-only last; one owner per account/root cause  
- Exhausted/human-only → one AttentionItem via existing compiler  
- Success clears/prevents obsolete attention  
- Attempt history + metrics + isolation tests  
- Invariants preserved; living report complete with Architecture Decisions  
