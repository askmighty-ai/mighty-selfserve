# Home OS — Future Preview

**Status:** Review-only staging/research scenario  
**Audience:** Product, design, engineering evaluating Home OS at full operation  
**Entry:** `/research/home-os/future` (never available in production)

Future Preview answers: *What does Home feel like when Mighty is already watching a real household?*

It does **not** change production `/dashboard` behavior, authenticated Home OS compose, or the Marriott auth-repair slice.

---

## How to open it

Requires the same gate as other Home OS research surfaces:

- `DEMO_MODE=true`
- Non-production environment
- Staging/research (`RAILWAY_ENVIRONMENT_NAME=staging` / `research`) **or** `HOME_OS_ENABLED` / `RESEARCH_HOME_ENABLED`

| URL | Home status | Work queue |
|-----|-------------|------------|
| `/research/home-os/future` | Needs user | Approval + 3 Opportunities |
| `/research/home-os/future?state=attention` | Needs user | Same (Approval ranked first) |
| `/research/home-os/future?state=opportunity` | Value waiting | Opportunities only |
| `/research/home-os/future?state=all-clear` | Calm | Empty queue |
| `/research/home-os/future?interrupt=1` | Needs user | Optional Hilton soft Interrupt + Approval + Opportunities |

Each entry:

1. Creates an ephemeral cookie session (no `users` row)
2. Seeds deterministic `CanonicalModels`
3. Redirects to `/home`
4. Projects through normal `project_home` → existing Home OS renderer

CTAs are review stubs — Work commands do not mutate the seed.

---

## Persona: Jordan

One frequent traveler based in San Francisco — not a random sample pack.

- Cards: Amex Platinum, Chase Sapphire, Capital One Venture  
- Travel: United, Delta, Alaska, Marriott, Hilton, Hyatt, Airbnb, Expedia  
- Everyday: Amazon, Target, Costco, Uber, Lyft (candidate), Netflix, Spotify, Apple, Adobe, YouTube  
- Money: Bank of America, Fidelity, Venmo  
- Membership: AAA  

Near-term story thread: a Chicago trip in September. United award inventory, Amex hotel credit, Marriott free night, and a Chase→United transfer bonus all point at the same trip — so Opportunities and the Approval feel like one life, not demos.

---

## Canonical contents

Generated in `mighty/home_os/future_preview.py` and consumed as ordinary projection inputs.

| Domain | Count / shape |
|--------|----------------|
| Coverage | **25** monitored providers (23 healthy enrolled, Adobe pending re-verify, Lyft candidate) |
| Proof | **12** historical outcomes across ~14 days |
| Approval | **1** — United award booking (SFO→ORD) |
| Opportunities | **3** — Amex hotel credit, Marriott free night, Chase transfer bonus |
| Interrupt | **0** by default; optional Hilton session via `?interrupt=1` |

### Fixed clock

Projection always uses:

```text
2026-07-25T16:00:00+00:00
```

Same inputs + same `as_of` ⇒ identical `HomeState` (stable screenshots).

Simulation tag: `ephemeral_future_preview`.

---

## What is intentionally *not* simulated

- Ranking order — still owned by `mighty.workitem.rank_work_items`
- Renderer layout — no Future Preview-specific UI chrome
- Authenticated Attention / AccountState / `account_changes` paths
- Live provider auth or booking

See also [HOME_OS_SIMULATION_GAPS.md](HOME_OS_SIMULATION_GAPS.md).

---

## Local verification

```bash
DEMO_MODE=true RAILWAY_ENVIRONMENT_NAME=staging HOME_OS_ENABLED=true \
  pytest tests/test_home_os_future_preview.py -q
```

Screenshots: `docs/pr-screenshots/home-os-future-preview/`  
Capture script: `scripts/capture_home_os_future_preview_screenshots.py`
