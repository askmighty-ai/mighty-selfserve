# AttentionState — deterministic ranking over AttentionItems

**Status:** Implemented (PR 2C)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §7  
**Module:** `mighty/attention_state.py`  
**Depends on:** [ATTENTION_ITEM.md](ATTENTION_ITEM.md) (PR 2A)

## Why this exists

`select_attention` is the pure product-policy stage:

```text
Sequence[AttentionItem]  →  AttentionState
```

It decides which currently open candidate deserves primary attention and whether the product is silent. Given identical items and the same clock, identical `AttentionState` values must be produced.

This PR does **not** implement overlays, Store, persistence, delivery, Home, or provider-specific policy.

---

## Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **AttentionState / select_attention (this module)** | Effectiveness filter, §7 total order, primary, remaining, silence | Creating items, overlays, UI |
| **AttentionItem (PR 2A)** | Immutable candidate contract | Ranking |
| **AttentionCompiler (PR 2B+)** | Emit candidates from facts | Order / silence |
| **AttentionStore (later)** | Snooze / dismiss / in_flight / receipts | Ranking policy |
| **Composition (PR 2D)** | Overlays → filtered candidates → ranker; produces `suppressed` | Persistence / HTTP |

---

## Model

```text
AttentionState
  schema_version   # contract version (1)
  primary          # top effective item, or None
  remaining        # other effective items in the same total order
  silence          # SilenceVerdict | None
```

`SilenceVerdict`: `all_clear` | `suppressed` | `awaiting_data`.

`silence=None` means at least one effective rank 1–5 item is visible — the product is **not silent**. There is no `active` verdict.

`suppressed` is part of the contract (overlays) but **cannot** be produced by `select_attention` in this PR.

Intentionally omitted: `user_id`, `generated_at`, `counts`, AuthTruth, AccountState, copy, dismiss/snooze/in_flight/delivery, provider categories.

---

## Entry point

```python
select_attention(items: Sequence[AttentionItem], *, now: datetime) -> AttentionState
```

- `now` is required for effectiveness; no internal current-time calls.
- Input order never affects output.
- Input `AttentionItem`s are never mutated.

---

## Policy (RFC §7)

1. **Effectiveness** — exclude when `becomes_stale_at is not None` and `now >= becomes_stale_at`.
2. **Rank** — trust(1) > agent_authorization(2) > auth_blocker(3) > system(4) > value_at_risk(5) > opportunity(6) > access_degraded(7) > data_gap(8).
3. **Rank 5** — earlier `becomes_stale_at` wins; `None` sorts last.
4. **Ties** — `provider` ASC (`None` → `""`), then `attention_id` ASC.
5. **Primary** — first of the ordered effective set; `remaining` is the rest.
6. **Silence**
   - `None` if any effective rank 1–5
   - `awaiting_data` if no ranks 1–5 and any effective rank 7–8
   - `all_clear` otherwise (including empty queue, or opportunity-only)
7. Opportunity-only (or ranks 6–8 without 1–5) may still set **primary** while `silence=all_clear`. `all_clear` means no effective ranks 1–5, not an empty queue.

---

## Serialization

- `AttentionState.to_dict()` / `from_dict()` round-trip.
- Nested items use `AttentionItem` serialization.
- `silence` serializes as its string value or `null`.

---

## Non-goals (this PR)

- No AttentionStore / overlays / persistence
- No dismiss, snooze, in_flight, delivery
- No Home / Worker / Push / APIs / jobs / metrics
- No English copy or provider-specific policy
- No compiler changes
