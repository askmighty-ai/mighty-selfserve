# Attention Engine — read path (Milestone 2)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.6  
**Modules:** `mighty/attention_loaders.py`, `mighty/attention_engine.py`

## Why this exists

The Attention Engine is a thin composer over existing pure stages. It owns **no** ranking, silence, overlay, or domain producer rules.

```text
DB facts
  → load AuthTruth[] + AuthorizeRow[] + WorkerSignal + BenefitSignal[]
       + AccountState[] + overlays
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
| `load_account_states_for_attention` | `account_state` | `AccountState[]` (data_gap input) |
| `load_authorize_rows` | `actions` | `AuthorizeRow[]` (default: `status=pending`) |
| `load_auth_truths` | enrolled providers × `project_auth_truth` | `AuthTruth[]` |
| `load_worker_signal` | `users.extension_*` | `WorkerSignal` or `None` |
| `load_benefit_signals` | open `action_items` | `BenefitSignal[]` |
| `load_trust_signals` | managed_runtime accounts × `runtime_access_state` | `TrustSignal[]` |

Provider list comes from enrollment (`account_state`), not from inventing accounts in Attention. One AccountState load is reused for AuthTruth projection, worker enrollment count, and data_gap gather.

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

Shadow writes `attention_shadow` (latest per user×surface). When a legacy probe is supplied, also writes `attention_compare` agreement metrics (latest per user×surface). Failures are swallowed. **Home/Worker consume AttentionView when cutover is `on`** (default). Legacy compare probes are opt-in via `ATTENTION_SHADOW_COMPARE` — see [ATTENTION_CUTOVER.md](ATTENTION_CUTOVER.md).

---

## Milestone 4 extensions

- `data_gap` producer wired (AccountState → gather). See [ATTENTION_COMPILER_DATA_GAP.md](ATTENTION_COMPILER_DATA_GAP.md).
- `system` worker producer wired (WorkerSignal → gather). See [ATTENTION_COMPILER_WORKER.md](ATTENTION_COMPILER_WORKER.md).
- Benefit producers wired (`value_at_risk` / `opportunity`). See [ATTENTION_COMPILER_BENEFIT.md](ATTENTION_COMPILER_BENEFIT.md).
- AttentionSupervisor: [ATTENTION_SUPERVISOR.md](ATTENTION_SUPERVISOR.md)
- Delivery + HTTP commands: [ATTENTION_DELIVERY.md](ATTENTION_DELIVERY.md)

## Non-goals (historical Milestone 2)

- No production Home/Worker cutover (done in M3)
- No push / email delivery
- No Benefit / Worker producers
- No AttentionSupervisor job
- No public HTTP attention API (internal engine + shadow only)
