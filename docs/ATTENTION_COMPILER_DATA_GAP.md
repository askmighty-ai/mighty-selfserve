# AttentionCompiler — AccountState → data_gap (Milestone 4)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.2 / Part XI  
**Design note:** [ATTENTION_INTELLIGENT.md](ATTENTION_INTELLIGENT.md)  
**Module:** `mighty/attention_compiler.py`

## Why this exists

When an enrolled account is connected but Mighty still lacks usable data, the product owes an informational `data_gap` candidate. AccountState owns per-account `data_status`; Attention compiles the cross-account queue item. Home/Worker must not invent a second “needs data” policy.

```text
AccountState  →  Optional[AttentionItem]   # data_gap
```

---

## Mapping

| Condition | Output |
|-----------|--------|
| `connection_state != connected` | `None` |
| `data_status` not in `{none, partial}` | `None` |
| connected + none/partial | one `data_gap` |

| Field | Value |
|-------|-------|
| class / urgency | `data_gap` / `informational` |
| fingerprint | `account_data:{provider}:data_gap` |
| attention_id | `att_{user_id}_data_gap_{provider}` |
| source_kind / source_ref | `account_data` / `account_state:{user_id}:{provider}` |
| reason | `data_gap` |
| cta_key | `open_provider_surface` |
| observed_at | `last_data_refresh` or `updated_at` |
| becomes_stale_at | `None` |

Auth blockers and access_degraded remain separate producers. Ranking prefers ranks 1–7 over `data_gap` (rank 8).

---

## Non-goals

- No ranking / overlays / delivery in this producer
- No provider-specific branching
- No change to AccountState projection rules
- No Benefit / Worker producers (separate PRs)

---

## Tests

`tests/test_attention_compiler_data_gap.py`
