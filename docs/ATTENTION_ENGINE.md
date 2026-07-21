# Attention Engine — read path (Milestone 2)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.6  
**Modules:** `mighty/attention_loaders.py`, `mighty/attention_engine.py`

## Why this exists

The Attention Engine is a thin composer over existing pure stages. It owns **no** ranking, silence, overlay, or domain producer rules.

```text
DB facts
  → load AuthTruth[] + AuthorizeRow[] + overlays
  → compile_attention_candidates
  → compose_attention (overlays + select_attention)
  → AttentionState
```

---

## Responsibility boundary

| Layer | Owns |
|-------|------|
| **attention_loaders** | Map DB rows → compiler inputs (no policy) |
| **producers** (`attention_compiler`) | Domain → AttentionItem |
| **gather** | Concatenate producer outputs |
| **AttentionStore** | Overlay persistence |
| **compose_attention** | Overlay filter + ranking + suppressed |
| **attention_engine** | Call the above in order; return immutable `AttentionState` |
| **AttentionView** | Surface window + copy/CTA resolution ([ATTENTION_VIEW.md](ATTENTION_VIEW.md)) |
| **Home / Worker** | Consume AttentionView after cutover; shadow/compare during rollout |

---

## Loaders

| Loader | Source | Output |
|--------|--------|--------|
| `load_authorize_rows` | `actions` | `AuthorizeRow[]` (default: `status=pending`) |
| `load_auth_truths` | `account_state` providers × `project_auth_truth` | `AuthTruth[]` |

Provider list comes from enrollment (`account_state`), not from inventing accounts in Attention.

---

## Entry point

```python
read_attention(db, user_id, *, now: datetime) -> AttentionState
```

Optional diagnostic:

```python
read_attention_snapshot(db, user_id, *, now) -> AttentionReadSnapshot
# state + candidates (pre-overlay) + overlays
```

`now` is required (no internal wall-clock in the engine body beyond passing through to projectors/loaders that already accept it).

---

## Shadow integration (Milestone 2)

| Surface | Trigger | Behavior |
|---------|---------|----------|
| Home | Dashboard hero render | `record_attention_shadow(..., "home")` after existing `resolve_home_state` |
| Worker | `GET /api/account-status` | `record_attention_shadow(..., "worker")` before JSON response |

Shadow writes `attention_shadow` (latest per user×surface). Failures are swallowed. **Home/Worker UI and CTAs are unchanged** — they do not read AttentionState yet.

---

## Non-goals (Milestone 2)

- No production Home/Worker cutover
- No push / email delivery
- No Benefit / Worker / data_gap producers
- No AttentionSupervisor job
- No public HTTP attention API (internal engine + shadow only)
