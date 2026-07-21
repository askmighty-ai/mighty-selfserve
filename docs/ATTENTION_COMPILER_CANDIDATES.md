# AttentionCompiler — candidate gather

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.3 / §4.6  
**Module:** `mighty/attention_compiler.py`  
**Depends on:** auth / authorize / access_degraded / worker / benefit / data_gap producers

## Why this exists

The read path loads compiler inputs then needs one candidate set before overlays and ranking:

```text
AuthTruth[] + AuthorizeRow[] + WorkerSignal? + BenefitSignal[] + AccountState[]
  →  tuple[AttentionItem, ...]
```

`compile_attention_candidates` is a pure gather over existing producers. It does **not** rank, apply overlays, or load from the database.

---

## Behavior

1. For each `AuthTruth` (input order): emit `compile_auth_attention` if not `None`, else `compile_access_degraded_attention` (mutually exclusive via `needs_human`).
2. For each `AuthorizeRow` (input order): emit `compile_authorize_attention` if not `None`.
3. If `WorkerSignal` provided: emit `compile_worker_attention` if not `None`.
4. For each `BenefitSignal` (input order): emit `compile_benefit_attention` if not `None`.
5. For each `AccountState` (input order): emit `compile_data_gap_attention` if not `None`.
6. Return a tuple. Input order is preserved within each input family; later families append after earlier ones.
7. Does not dedupe across families (different `attention_id` spaces). Does not sort by RFC §7 — that is `select_attention` / `compose_attention`.

---

## Non-goals

- No DB loaders (see `attention_loaders` / engine)
- No overlays / ranking / Home
- No silence / primary selection

---

## Tests

`tests/test_attention_compiler_candidates.py` · `tests/test_attention_compiler_data_gap.py` · `tests/test_attention_compiler_worker.py` · `tests/test_attention_compiler_benefit.py`
