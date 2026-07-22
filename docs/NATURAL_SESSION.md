# Natural-Session Coverage — Milestone 8 Design Note

**Status:** Complete  
**Milestone report:** [milestones/MILESTONE_8.md](milestones/MILESTONE_8.md)  
**Related:** [ACCESS_FLOW.md](ACCESS_FLOW.md) · [ATTENTION_AUTONOMOUS_RECOVERY.md](ATTENTION_AUTONOMOUS_RECOVERY.md)

## User capability

When a user naturally visits a supported provider, Mighty detects the session, validates it, refreshes account data when freshness policy requires, and quietly keeps the account current.

## Objective

Unify browser activity, Provider Access Manager, Recovery, AuthTruth, AccountState, and Attention into one **deterministic Natural Session pipeline** that maximizes passive freshness and minimizes interruption — without a parallel sync system.

## Inventory summary

| Existing | Role vs M8 |
|----------|------------|
| PAM + `session_verification` | Canonical verify/enqueue — **keep** |
| `provider_page_observed` trigger | Allowed but unused — **emit** |
| Extension keepalive ensure-due | Routine freshness — route via Natural Session |
| Recovery Supervisor | Failure strategy — Natural Session **defers** when active |
| Attention escalate gate | Interrupt after recovery — Natural Session does not rank |
| Hourly sync / legacy probes | Competing paths — do not extend; prefer PAM |

## Ownership

| Concern | Owner |
|---------|-------|
| Natural Session lifecycle / freshness decision | **Natural Session policy + coordinator** |
| Enqueue / PSS writes | Provider Access Manager only |
| Failure recovery strategy | Recovery Planner |
| Human interrupt | Attention (after escalation) |
| AuthTruth | Projection only — Natural Session does not mutate it |

## Canonical lifecycle

```text
browser observe | scheduled ensure-due
  → Natural Session decision (pure)
       unsupported | defer_recovery | skip_fresh | enqueue_verify
  → PAM ensure/request (trigger_source=provider_page_observed|scheduled_recheck|…)
  → extension runSessionVerification → probe → PSS
  → AuthTruth / AccountState recompute (existing)
  → Recovery may close succeeded cases; Attention stays quiet unless escalated
```

## Recovery coordination

If an **active** Recovery case exists for `(user, provider)`, Natural Session **defers** (no competing enqueue). Recovery remains the strategy owner for failures.

## Freshness

Reuse `session_state_needs_verification` / ready revalidation interval — one scheduling policy. Do not invent a second freshness clock.

## Implementation order

1. Design + living report + roadmap  
2. Pure policy + coordinator + metrics + Recovery deferral  
3. Wire ensure-due + observe API + extension emitter  
4. Tests + docs close  

## Non-goals

New recovery planner, Attention ranking, AuthTruth writes, multi-provider customer expansion as a product milestone, hourly sync redesign beyond preferring PAM.
