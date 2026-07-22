# Freshness and Change Intelligence

**Status:** Complete (Milestone 9)  
**Related:** [ACCOUNT_SNAPSHOTS.md](ACCOUNT_SNAPSHOTS.md) · [ACCOUNT_STATE.md](ACCOUNT_STATE.md) · [NATURAL_SESSION.md](NATURAL_SESSION.md) · [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md)

---

## User capability

Mighty tells the user what meaningfully changed since they last looked, and stays silent when nothing important changed.

---

## Objective

Transform refreshed account data into durable, explainable, user-facing change facts — without recording every field mutation or building a parallel history system.

---

## Non-goals

- Status-dashboard noise (“synced 3 minutes ago” as a product surface)
- A second Attention ranking path for routine updates
- Provider-specific change policy in shared modules
- Replacing Natural Session / PAM scheduling clocks
- Owning Recovery or Discovery

---

## Ownership

| Concern | Owner |
|---------|--------|
| Session / access scheduling freshness | Natural Session + PAM (`session_state_needs_verification`) |
| Data currency (product freshness) | **Freshness policy** over AccountState `last_data_refresh` + `data_status` |
| Meaningful field deltas | **Change Intelligence** over Account Snapshots |
| Durable change reports | `account_changes` (derived from snapshot diffs) |
| Interruption | Attention (unchanged gather/rank; does not own change significance) |
| Failure repair | Recovery |

Freshness computes **facts**. Attention decides **interruption**. Consumers render summaries; they do not reinterpret significance.

---

## Canonical freshness model

Provider-independent classification of **data currency** (not session scheduling):

| Class | Meaning |
|-------|---------|
| `fresh` | Usable data within data-refresh TTL |
| `stale` | Usable data older than TTL |
| `unavailable` | No usable data, or account not in a readable connected state |

TTL reuses `account_presentation.DATA_REFRESH_TTL_HOURS` (financial 48h / default 7d). Do not invent a fifth clock.

---

## Canonical change model

Computed when a successful snapshot is persisted, by diffing against the previous successful snapshot:

| Outcome | Meaning |
|---------|---------|
| `newly_discovered` | First successful snapshot for the account |
| `refreshed_no_meaningful_change` | New snapshot; no material field deltas |
| `materially_changed` | One or more meaningful field deltas |
| `unchanged` | No new snapshot event (read-time; not persisted as a change row) |

**Meaningful** (provider-independent type set): rewards balances, credits, certificates, elite/membership, payment/renewal/expiry warnings. Formatting-only and empty noise are ignored.

---

## Combined product states

Surfaces may present one of:

1. `unchanged` — quiet; nothing new to say  
2. `refreshed_no_meaningful_change` — quietly current  
3. `materially_changed` — concise summary  
4. `newly_discovered` — first data available  
5. `stale` — data currency fact (not a change report)  
6. `unavailable` — no usable data / not readable  

---

## Lifecycle

```text
Successful extraction
  → previous latest snapshot (read)
  → persist new Account Snapshot (append-only)
  → Change Intelligence diff (pure)
  → account_changes persist (dedupe by field fingerprint)
  → AccountState recompute (existing; clears data_gap when complete)
  → consumers: Account Detail History / change summaries / future Briefs
```

Natural Session and Recovery remain upstream of extraction. Change Intelligence never enqueues verification and never mutates AuthTruth.

---

## Attention interaction

- Change Intelligence does **not** emit AttentionItems and does **not** rank.
- Successful complete data continues to suppress `data_gap` via AccountState.
- Actionable expiry/value-at-risk remains Benefit Attention (`action_items`).
- Routine material balance updates stay available as **summaries**, not Home interrupts (Daily Brief is a later surface).

---

## Observability

| Signal | Meaning |
|--------|---------|
| freshness_rate / stale_rate | Share of accounts with usable data inside/outside TTL |
| meaningful_change_rate | Material outcomes / snapshot refreshes |
| duplicate_suppression | Field fingerprints already current |
| refresh_latency | Optional when start→snapshot timestamps exist |
| first_data_latency | Time markers for `newly_discovered` when available |

---

## Modules

| Module | Role |
|--------|------|
| `freshness_policy` | Pure data-currency classification |
| `change_intelligence` | Pure snapshot diff + summary |
| `change_store` | Durable `account_changes` + dedupe |
| `freshness_change` | Coordinator after snapshot persist |
| `freshness_metrics` | Snapshot counters |
