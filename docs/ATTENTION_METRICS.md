# Attention production metrics (Milestone 5)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) Part X  
**Design note:** [ATTENTION_AUTONOMOUS.md](ATTENTION_AUTONOMOUS.md)  
**Module:** `mighty/attention_metrics.py`

## Metrics

| Metric | Definition |
|--------|------------|
| `autonomous_coverage` | managed_runtime accounts with healthy Runtime publication and no trust/auth_blocker primary / eligible |
| `false_silence_rate` | Blocker primaries aged ≥ 60s without successful push receipt / push-eligible blockers |
| `false_interruption_rate` | Visible ranks 1–4 with `interruption_expected=false` / visible blocker primaries |
| `delivery_sla_rate` | Delivered receipts whose success landed within 60s of first attempt / receipts with attempts |

Persisted as latest `attention_metric_snapshot` scope=`global`. Computed on AttentionSupervisor heartbeat only.

## Entry points

```python
compute_attention_metrics(db, *, now, user_ids=None) -> AttentionMetricSnapshot
run_attention_metrics_sweep(db, *, now) -> AttentionMetricSnapshot | None
load_attention_metric_snapshot(db) -> AttentionMetricSnapshot | None
```

## Tests

`tests/test_attention_metrics.py`
