# AttentionDelivery — primary push + receipts (Milestone 4)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.4 / §6.3 / §7  
**Design note:** [ATTENTION_INTELLIGENT.md](ATTENTION_INTELLIGENT.md)  
**Module:** `mighty/attention_delivery.py`

## Why this exists

Push must key off **AttentionState.primary** only. Delivery receipts support SLA / false-silence metrics. Failures never raise into Home, Worker, or sync.

```text
AttentionState.primary
  → eligible urgency? (blocker | time_sensitive)
  → already delivered?
  → send_push(title, body, url from AttentionView push)
  → attention_delivery_receipt
```

---

## Entry points

```python
deliver_attention_primary(db, user_id, *, now, state=None, send_push=None)
run_attention_delivery_sweep(db, *, now, user_ids=None, send_push=None)
```

| Status | Meaning |
|--------|---------|
| `delivered` | Push sender returned true |
| `failed` | Sender raised / returned false |
| `skipped` | Not eligible, already delivered, or no sender |

Scheduler: same heartbeat as AttentionSupervisor (`ENABLE_ATTENTION_SUPERVISOR`). Push sender is injected from `app.py` (`send_web_push` + notify/subscription gates).

---

## HTTP (commands + view)

| Route | Behavior |
|-------|----------|
| `GET /api/attention/view?surface=` | AttentionView + state snapshot |
| `POST /api/attention/<id>/snooze` | Store snooze |
| `POST /api/attention/<id>/dismiss` | Store durable dismiss (opportunity only) |
| `POST /api/attention/<id>/cta` | Store in_flight; optional Access Manager `user_check_now` |

Helpers: `mighty/attention_commands.py`.

---

## Non-goals

- Multi-item push  
- Email as primary channel  
- Delivery on GET `/api/account-status` hot path  

---

## Tests

`tests/test_attention_delivery.py` · `tests/test_attention_commands.py`
