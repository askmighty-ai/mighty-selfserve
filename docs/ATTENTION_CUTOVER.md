# Attention cutover flags (Milestone 3 / 5)

**Module:** `mighty/attention_cutover.py` · consumer: `mighty/attention_consumer.py`  
**Retirement criteria:** [ATTENTION_CUTOVER_RETIREMENT.md](ATTENTION_CUTOVER_RETIREMENT.md)

## Modes

| Mode | Engine / compare | Consumer behavior |
|------|------------------|-------------------|
| `off` | Optional (caller may still shadow) | Legacy only; no `attention` API field |
| `shadow` | Record shadow + compare | Legacy UI/behavior; `attention` payload exposed for observability |
| `on` | Record shadow; compare **opt-in** | Home/Worker use `AttentionView`; legacy only on platform failure |

Default: **`on`**.

## Env

```text
ATTENTION_CUTOVER=on|shadow|off
ATTENTION_CUTOVER_HOME=...
ATTENTION_CUTOVER_WORKER=...
ATTENTION_SHADOW_COMPARE=0|1   # M5: legacy probe compare (default off when cutover=on)
```

Per-surface overrides win over `ATTENTION_CUTOVER`.

## Failure behavior

If the Attention Engine fails while mode is `on`, Home/Worker keep functioning without AttentionView (no crash). Worker popup falls back to account-status `access_loop`. Home omits the attention panel and still renders the Truth capability instrument.
