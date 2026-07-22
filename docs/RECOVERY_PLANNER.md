# Recovery Planner

**Status:** Implemented (Milestone 6)  
**Design note:** [ATTENTION_AUTONOMOUS_RECOVERY.md](ATTENTION_AUTONOMOUS_RECOVERY.md)  
**Related:** [ACCESS_FLOW.md](ACCESS_FLOW.md) · [AUTH_TRUTH.md](AUTH_TRUTH.md)

## Role

Deterministic, pure policy that chooses the next autonomous recovery capability from failure facts and attempt history.

- Does **not** rank user attention  
- Does **not** write auth evidence  
- Does **not** branch on provider identity unless a capability flag requires it  

## Modules

| Module | Role |
|--------|------|
| `mighty/recovery_planner.py` | Pure `plan_recovery` |
| `mighty/recovery_store.py` | Lifecycle + attempt history (one owner) |
| `mighty/recovery_executor.py` | Capability → Access Manager |
| `mighty/recovery_supervisor.py` | Heartbeat observe → plan → execute |
| `mighty/recovery_metrics.py` | Coverage + unexpected interruption |

## Attention gate

Auth / trust / access_degraded candidates emit only for providers with Recovery status `escalated`. Active recovery suppresses interrupt. See `list_escalated_providers` / `compile_attention_candidates(recovery_attention_allowed=…)`.
