# Milestone 9 — Freshness and Change Intelligence

**Status:** Complete  
**Design note:** [FRESHNESS_CHANGE.md](../FRESHNESS_CHANGE.md)  
**Related:** [ACCOUNT_SNAPSHOTS.md](../ACCOUNT_SNAPSHOTS.md) · [ACCOUNT_STATE.md](../ACCOUNT_STATE.md)

## User capability

Mighty tells the user what meaningfully changed since they last looked, while remaining silent when nothing important changed.

## Objective

Build a unified freshness and change-intelligence capability that transforms refreshed account data into durable, explainable user-facing changes — without recording every field mutation or creating a parallel history system.

## PRs merged

| PR | Theme |
|----|--------|
| [#152](https://github.com/askmighty-ai/mighty-selfserve/pull/152) | Design note, freshness/change modules, snapshot wire-up, API bridge, tests, living report |

## Architecture changes

- Added pure `freshness_policy` (data currency: fresh / stale / unavailable)
- Added pure `change_intelligence` (snapshot diff → meaningful deltas + summaries)
- Added `change_store` (`account_changes` + fingerprint dedupe)
- Added `freshness_change` coordinator after successful snapshot persist
- Added `freshness_metrics` snapshot counters
- Snapshot create path observes previous→new without a parallel history system
- `/api/field-history` and change alerts prefer `account_changes`

## Architecture Decisions

### AD-M9-1: Snapshots are the change foundation; no parallel history SSoT

- **Decision:** Diff append-only Account Snapshots; persist derived `account_changes` events.  
- **Why:** Snapshots already normalize successful extractions; inventing a second field log would diverge.  
- **Alternatives considered:** Extend only `field_history`; implement `account_state_events` as primary.  
- **Long-term impact:** History and Briefs read the same event model.

### AD-M9-2: Separate freshness (currency) from change (delta)

- **Decision:** Two pure models; combine only at presentation via six product states.  
- **Why:** Stale ≠ changed; quiet refresh ≠ unavailable.  
- **Alternatives considered:** Single enum mixing clocks and diffs.  
- **Long-term impact:** Clear ownership vs Natural Session scheduling clocks.

### AD-M9-3: Reuse data-refresh TTL; do not add a fifth clock

- **Decision:** Product freshness uses `DATA_REFRESH_TTL_HOURS` (financial 48h / default 7d).  
- **Why:** M8 already documented intentional session vs data clock split.  
- **Alternatives considered:** Unify with AuthTruth 24h or ready 15m.  
- **Long-term impact:** Scheduling stays Natural Session; currency stays presentation.

### AD-M9-4: Meaningful types are provider-independent

- **Decision:** Significance uses snapshot field `_type` buckets (points, credits, certs, warnings, …).  
- **Why:** Shared policy must not branch on provider id.  
- **Alternatives considered:** Provider-specific thresholds in policy.  
- **Long-term impact:** New providers inherit change semantics via classification.

### AD-M9-5: Change Intelligence does not rank Attention

- **Decision:** No `AttentionClass` for routine changes; complete data clears `data_gap` via AccountState.  
- **Why:** Freshness computes facts; Attention owns interruption.  
- **Alternatives considered:** Informational “account_updated” Attention items.  
- **Long-term impact:** Daily Briefs/notifications can consume summaries later without a second ranker.

### AD-M9-6: Fingerprint dedupe suppresses repetitive reporting

- **Decision:** Current field fingerprints block re-reporting the same old→new pair.  
- **Why:** Required outcome — prevent duplicate/repetitive change noise.  
- **Alternatives considered:** Time-window silence only.  
- **Long-term impact:** Stable quiet behavior under re-extract churn.

## Final production data flow

```text
Successful extraction
  → read previous latest Account Snapshot
  → persist new Account Snapshot (append-only)
  → Change Intelligence diff (pure)
  → account_changes (+ fingerprint dedupe)
  → AccountState recompute (existing)
  → data_gap Attention clears when complete
  → Account Detail History / change alerts / future Briefs render summaries
```

Natural Session / PAM / Recovery remain upstream. Change Intelligence never enqueues verification or mutates AuthTruth.

## Freshness model

| Class | Meaning |
|-------|---------|
| `fresh` | Usable data within data-refresh TTL |
| `stale` | Usable data older than TTL |
| `unavailable` | No usable data / not readable |

## Change model

| Outcome | Meaning |
|---------|---------|
| `newly_discovered` | First successful snapshot |
| `refreshed_no_meaningful_change` | Snapshot refresh; no material deltas (or dupes suppressed) |
| `materially_changed` | Novel meaningful field deltas |
| `unchanged` | Read-time quiet state (no new event) |

Combined product states: unchanged · refreshed_no_meaningful_change · materially_changed · newly_discovered · stale · unavailable.

## Validation performed

- Inventory of AccountState, snapshots, field_history, Attention, Natural Session clocks  
- Pure freshness + change golden tests  
- Duplicate suppression store tests  
- Deterministic replay of diffs  
- Provider capability TTL (financial vs loyalty)  
- E2E snapshot refresh → account_changes  
- Attention: complete data clears data_gap; no change Attention class  
- Failure isolation when store writes fail  
- Snapshot regression suite remains green  

## Tests executed

```text
.venv/bin/pytest tests/test_freshness_policy.py \
  tests/test_change_intelligence.py \
  tests/test_freshness_change.py \
  tests/test_account_snapshots.py
→ green
```

## Metrics

| Signal | Where |
|--------|--------|
| freshness_rate / stale_rate | `freshness_metric_snapshot` |
| meaningful_change_rate | material(+newly) / refreshes |
| duplicates_suppressed | change events + metrics |
| quiet_refreshes / newly_discovered | counters |
| avg_refresh_latency_seconds | when metadata start timestamp present |
| avg_first_data_latency_seconds | first-data samples |

## Technical debt

- Legacy `field_history` still written on discovery path (bridged, not deleted)  
- `_get_change_alerts` heuristics remain as fallback only  
- Home Daily Brief surface not built (parking lot) — summaries are ready for it  
- AccountState does not yet project `last_meaningful_change_at` column (readable via `account_changes`)  
- first_data_latency needs enrollment timestamps for richer samples  

## Lessons learned

- Diffing normalized snapshots beat growing string-diff heuristics in `app.py`.  
- Separating currency from delta avoided conflating “stale” with “something changed.”  
- Keeping Attention free of routine updates preserved the quiet-co-pilot posture.

## Recommendation for the next milestone

**Milestone 10 — Value Intelligence**

Use durable change summaries + Benefit Attention / action_items to surface value at risk and opportunities worth acting on — without turning Home into a deals feed. Freshness/change facts remain inputs; Attention ranking stays singular.
