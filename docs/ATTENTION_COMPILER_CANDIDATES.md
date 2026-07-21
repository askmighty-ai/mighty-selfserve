# AttentionCompiler — candidate gather (PR 2H)

**Status:** Implemented (PR 2H)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.3 / §4.6  
**Module:** `mighty/attention_compiler.py`  
**Depends on:** PR 2B / 2F / 2G producers

## Why this exists

The read path loads compiler inputs then needs one candidate set before overlays and ranking:

```text
AuthTruth[] + AuthorizeRow[]  →  tuple[AttentionItem, ...]
```

`compile_attention_candidates` is a pure gather over existing producers. It does **not** rank, apply overlays, or load from the database.

---

## Behavior

1. For each `AuthTruth` (input order): emit `compile_auth_attention` if not `None`, else `compile_access_degraded_attention` (mutually exclusive via `needs_human`).
2. For each `AuthorizeRow` (input order): emit `compile_authorize_attention` if not `None`.
3. Return a tuple. Input order is preserved within each input family; authorize candidates follow auth-derived candidates.
4. Does not dedupe across families (different `attention_id` spaces). Does not sort by RFC §7 — that is `select_attention` / `compose_attention`.

---

## Non-goals

- No Benefit / Worker / AccountState inputs yet
- No DB loaders
- No overlays / ranking / Home
- No silence / primary selection

---

## Tests

`tests/test_attention_compiler_candidates.py`
