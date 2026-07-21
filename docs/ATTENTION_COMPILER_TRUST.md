# AttentionCompiler — TrustSignal → trust (Milestone 5)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.2 rank 1  
**Design note:** [ATTENTION_AUTONOMOUS.md](ATTENTION_AUTONOMOUS.md)  
**Modules:** `mighty/attention_compiler.py`, `mighty/runtime_access_state.py`, `mighty/attention_loaders.py`

## Why this exists

Rank-1 `trust` was stubbed in enums/ranking but never produced. Managed Runtime publications in `runtime_access_state` carry the facts Attention needs for customer-facing Runtime trust breaks — without re-implementing the recovery planner.

```text
TrustSignal  →  Optional[AttentionItem]   # trust
```

---

## Mapping

| Condition | Output |
|-----------|--------|
| `access_method != managed_runtime` | `None` |
| `needs_human` | `None` (auth_blocker owns) |
| `presentation_status` in `{awaiting_user, runtime_offline, never_reported}` | `trust` |
| `presentation_status == stale` and not healthy signed-in | `trust` |
| otherwise | `None` |

| Field | Value |
|-------|-------|
| class / urgency | `trust` / `blocker` |
| fingerprint | `trust:{provider}:runtime` |
| attention_id | `att_{user_id}_trust_{provider}` |
| source_kind / source_ref | `trust` / `runtime_access_state:{user}:{provider}` |
| cta_key | `focus_managed_runtime` |
| reason | `awaiting_user` \| `runtime_offline` \| `never_reported` \| `stale` |

### Store

Thin `mighty/runtime_access_state.py` provides table ensure + get/upsert + `compute_presentation_status`. It does **not** restore Control Center.

Runtime focus bridge side effects remain gated (View URL only until Runtime API auth exists).

---

## Tests

`tests/test_attention_compiler_trust.py`
