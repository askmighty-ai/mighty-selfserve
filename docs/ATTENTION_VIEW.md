# AttentionView — surface window (Milestone 3)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.1 / §4.6 / §8  
**Module:** `mighty/attention_view.py`  
**Depends on:** [ATTENTION_STATE.md](ATTENTION_STATE.md), [ATTENTION_ENGINE.md](ATTENTION_ENGINE.md)

## Why this exists

`AttentionView` is the only presentation window over an already-ranked `AttentionState`. It resolves customer English and CTA URLs for a surface. It does **not** rank, silence, compile, or invent attention.

```text
AttentionState
  → build_attention_view(surface, display names, open URLs)
  → AttentionView
```

## Responsibility boundary

| Layer | Owns |
|-------|------|
| **AttentionState** | Primary, remaining, silence |
| **AttentionView (this module)** | Surface windowing, copy resolution, CTA URL mapping, counts/hints |
| **Home / Worker** | Render the view; never re-rank |

## Model

```text
AttentionView
  schema_version
  surface            # home | accounts | activity | worker | push | email
  primary            # AttentionPresentation | None
  secondary          # tuple[AttentionPresentation, ...]
  health_counts      # blockers / time_sensitive / opportunities / informational / total
  silence            # copied from AttentionState
  render_hints       # show_primary, show_silence, interrupt, secondary_limit
```

`AttentionPresentation` carries resolved `title` / `body` / `cta_label` / `cta_url` plus machine fields (`attention_id`, class, urgency, provider, reason, cta_key).

## Windowing rules

| Surface | Primary | Secondary limit |
|---------|---------|-----------------|
| home | `AttentionState.primary` | 2 |
| worker | `AttentionState.primary` | 0 |
| activity | First `agent_authorization` in ranked order | 20 |
| push / email | `AttentionState.primary` | 0 |
| accounts | `AttentionState.primary` | 3 |

Activity may show a different visible top than Home; the global Home primary `attention_id` is unchanged (RFC §4.6).

## Non-goals

- No ranking / silence policy
- No overlay commands
- No HTTP API in this PR
- No Home/Worker cutover (follow-on adoption PRs)
- No phone capability gating beyond CTA URL availability
