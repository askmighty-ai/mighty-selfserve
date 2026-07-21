# AttentionStore — overlay persistence + commands (PR 2E)

**Status:** Implemented (PR 2E)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.4 / §4.5 / §5.2  
**Module:** `mighty/attention_store.py`  
**Depends on:** [ATTENTION_OVERLAY.md](ATTENTION_OVERLAY.md) (PR 2D), [ATTENTION_ITEM.md](ATTENTION_ITEM.md) (PR 2A)

## Why this exists

AttentionStore is the **only** writer of interaction overlays. It does not create `AttentionItem`s, project AuthTruth, or rank. Commands accept an existing candidate (or its class) and return/persist an `AttentionOverlay`.

```text
command(item, now, …)  →  AttentionOverlay
store.upsert(user_id, overlay)
compose_attention(items, store.list(user_id), now)  →  AttentionState
```

---

## Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **AttentionStore (this module)** | Overlay CRUD; snooze / dismiss / in_flight / clear command validation | Ranking, AuthTruth, delivery, HTTP |
| **compose / apply (PR 2D)** | Read-path visibility + suppressed | Persistence |
| **AttentionSupervisor** | Timeout clear + GC of orphan overlays ([ATTENTION_SUPERVISOR.md](ATTENTION_SUPERVISOR.md)) | Browser I/O |
| **HTTP / surfaces (later)** | Route → command → optional Access Manager side effect | Inventing overlays |

---

## Persistence

Table `attention_overlay`:

| Column | Notes |
|--------|-------|
| `user_id` | Scope key (must match item.user_id on command) |
| `attention_id` | Join to candidate |
| `status` | clear \| snoozed \| in_flight \| durable_dismissed |
| `until` | snooze end |
| `started_at` | in_flight start |
| `updated_at` | last write |
| `overlay_json` | full serialized overlay |

Primary key: `(user_id, attention_id)`.

`clear` may be stored or deleted — `delete` is preferred; missing row ≡ clear on read path.

---

## Commands (pure builders + store helpers)

All builders take an explicit `now` (no internal wall-clock).

| Command | Resulting status | Rules |
|---------|------------------|-------|
| `snooze` | `snoozed` | `duration` required; **max 1 hour**; sets `until` |
| `dismiss` | `durable_dismissed` | **opportunity only**; rejected for all other classes |
| `start_cta` | `in_flight` | sets `started_at=now`; does not enqueue verification here |
| `clear` | delete / absent | used after root-cause gone or supervisor timeout |

Rejected commands raise `AttentionStoreCommandError` and do not write.

Side effects from RFC §4.5 (`request_provider_verification`, Runtime focus) are **out of scope** — callers invoke adapters after a successful store write.

---

## Entry points

```python
build_snooze_overlay(item, *, now, duration) -> AttentionOverlay
build_dismiss_overlay(item, *, now) -> AttentionOverlay
build_in_flight_overlay(item, *, now) -> AttentionOverlay

ensure_attention_overlay_tables(db)
upsert_attention_overlay(db, user_id, overlay)
get_attention_overlay(db, user_id, attention_id) -> AttentionOverlay | None
list_attention_overlays(db, user_id) -> list[AttentionOverlay]
delete_attention_overlay(db, user_id, attention_id) -> None

snooze_attention(...) / dismiss_attention(...) / start_attention_cta(...) / clear_attention_overlay(...)
```

Store helpers validate `user_id == item.user_id` before write.

---

## Non-goals (this PR)

- No HTTP routes
- No Access Manager / Runtime side commands
- No delivery receipts table
- No supervisor job
- No Home / compose wiring into app.py
- No events / metrics emission
