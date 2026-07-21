# AttentionSupervisor — in_flight timeout + overlay GC (Milestone 4)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.4 / §5.2  
**Design note:** [ATTENTION_INTELLIGENT.md](ATTENTION_INTELLIGENT.md)  
**Module:** `mighty/attention_supervisor.py`

## Why this exists

`in_flight` overlays stay visible for in-progress copy, but must not stick forever. When the root-cause fingerprint leaves the candidate set, orphan overlays must be GC'd. AttentionSupervisor owns these persist-clears. It performs **no browser I/O**.

```text
overlays + current candidates + now
  → clear in_flight older than 30m
  → delete overlays absent from candidates
```

---

## Entry point

```python
run_attention_supervisor(db, *, now, user_ids=None) -> AttentionSupervisorResult
```

| Field | Meaning |
|-------|---------|
| `users_scanned` | Users with overlays processed |
| `in_flight_cleared` | Timed-out in_flight overlays deleted |
| `orphans_deleted` | Overlays whose attention_id not in candidates |
| `errors` | Per-user failures swallowed |

Default user set: `list_attention_overlay_user_ids(db)`.

Timeout constant: `IN_FLIGHT_TIMEOUT_SECONDS` (30 minutes) from `attention_overlay`.

---

## Scheduler

`app.py` starts `_start_attention_supervisor_scheduler` when
`ENABLE_ATTENTION_SUPERVISOR=true` (default). Interval:
`ATTENTION_SUPERVISOR_INTERVAL_SECONDS` (default 60). Failures are logged only.

---

## Non-goals

- No Access Manager / Runtime calls  
- No delivery / push  
- No ranking or producer policy  
- Compose still treats in_flight as visible until Store clear  

---

## Tests

`tests/test_attention_supervisor.py`
