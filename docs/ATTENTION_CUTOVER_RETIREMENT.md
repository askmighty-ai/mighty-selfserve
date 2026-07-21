# Attention cutover retirement criteria (Milestone 5)

**Status:** Criteria defined; flags retained for rollback  
**Design note:** [ATTENTION_AUTONOMOUS.md](ATTENTION_AUTONOMOUS.md)  
**Flags:** [ATTENTION_CUTOVER.md](ATTENTION_CUTOVER.md)

## Current production posture

| Control | Default |
|---------|---------|
| `ATTENTION_CUTOVER_*` | `on` |
| `ATTENTION_SHADOW_COMPARE` | **off** (legacy probes not required) |

When cutover is `on` and shadow-compare is off, Home/Worker consume AttentionView without building legacy attention probes. Shadow snapshots of AttentionState may still record; `attention_compare` only writes when a legacy probe is supplied.

Re-enable compare soak: `ATTENTION_SHADOW_COMPARE=1`.

## Objective criteria to delete cutover flags

All must hold for **≥ 7 consecutive days** in production (or an equivalent staging soak):

1. Cutover mode is already `on` for Home and Worker  
2. `platform_failure` rate < **0.5%** of attention consumer calls  
3. When compare is sampled: `old_active_new_silent` < **2%** of compares  
4. Metrics `false_silence_rate` < **5%** of push-eligible blocker primaries  
5. No open Sev-1/2 attributable to Attention mis-ranking  

Until then: keep `ATTENTION_CUTOVER_*` env rollback; do not delete modules.

## Safe cleanups already done (M5)

- Legacy probes are **opt-in**, not always-on  
- Home-local attention ranking already removed (M3)  
- Consumers never re-rank AttentionState  

## Remaining before hard deletion

- Confirm metrics soak against live traffic  
- Remove `legacy_signal_from_*` call sites and compare table writers  
- Delete `ATTENTION_CUTOVER_*` env handling once rollback is unused  
