# Attention overlays — filter + compose (PR 2D)

**Status:** Implemented (PR 2D)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.4 / §5.2 / §7  
**Module:** `mighty/attention_overlay.py`  
**Depends on:** [ATTENTION_ITEM.md](ATTENTION_ITEM.md) (PR 2A), [ATTENTION_STATE.md](ATTENTION_STATE.md) (PR 2C)

## Why this exists

Overlays are the only mutable attention interaction state. They never create candidates. This PR implements the pure read-path stage between compiler output and ranking:

```text
candidates + overlays + now  →  visible candidates
visible + snooze facts       →  AttentionState  (incl. suppressed)
```

Given identical items, overlays, and clock, identical `AttentionState` values must be produced.

This PR does **not** persist overlays, expose HTTP commands, deliver notifications, or run the supervisor job.

---

## Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **Attention overlays / compose (this module)** | Overlay contract, visibility filter, `suppressed` composition | Persistence, APIs, copy, delivery |
| **AttentionItem (PR 2A)** | Immutable candidates | Interaction state |
| **select_attention (PR 2C)** | Effectiveness + total order + non-overlay silence | Overlays |
| **AttentionStore (later)** | Persist snooze / dismiss / in_flight / receipts | Ranking policy |
| **AttentionSupervisor (later)** | Persist-clear timed-out in_flight / GC | Browser I/O |

---

## Model

```text
AttentionOverlay
  attention_id   # joins to AttentionItem.attention_id
  status         # clear | snoozed | in_flight | durable_dismissed
  until          # snooze end (ISO-8601), required when snoozed
  started_at     # in_flight start (ISO-8601), required when in_flight
  updated_at     # last overlay write (ISO-8601)
```

Missing overlay for an `attention_id` ≡ `clear`.

---

## Visibility (apply)

| Overlay | While active | After expiry |
|---------|--------------|--------------|
| `clear` / absent | visible | — |
| `snoozed` and `now < until` | **hidden** | visible (treated as clear at read time) |
| `in_flight` | **visible** (in-progress copy is View-layer) | still visible here; Supervisor persists clear after 30m |
| `durable_dismissed` on `opportunity` | **hidden** | stays hidden while fingerprint still emitted |
| `durable_dismissed` on non-opportunity | ignored (treated as clear) | Store must reject these writes later |

`IN_FLIGHT_TIMEOUT_SECONDS` (30m) is exported for Store/Supervisor writers. Compose does not hide timed-out in_flight rows — ranking still sees the candidate until the overlay is cleared.

---

## Composition (`compose_attention`)

1. `apply_overlays` → visible items + whether any **rank 1–4** candidate is actively snoozed.
2. `select_attention(visible, now=now)` → base state.
3. **Suppressed promotion** (only place `SilenceVerdict.SUPPRESSED` is produced):

   When at least one rank 1–4 candidate is actively snoozed **and** the base state has no visible effective ranks 1–5 (`silence is not None`):

   - `silence = suppressed`
   - `primary = None` (opportunities / informational must not fill the hero — RFC §7 / Part XIV #10)
   - `remaining` = the base ordered visible queue (former primary, if any, prepended)

If a visible effective rank 1–5 item exists (e.g. `value_at_risk`), silence stays `None` and that item may be primary even while lower blockers are snoozed.

---

## Entry points

```python
apply_overlays(items, overlays, *, now) -> OverlayFilterResult
compose_attention(items, overlays, *, now) -> AttentionState
```

Input order never affects output. Items and overlays are never mutated.

---

## Non-goals (this PR)

- No SQLite / AttentionStore persistence
- No HTTP snooze/dismiss/cta routes
- No delivery receipts
- No AttentionSupervisor job / GC writes
- No Home / Worker / Push / copy resolution
- No command validation (max snooze 1h, dismiss-opportunity-only) — Store write path later
- No compiler input expansion
