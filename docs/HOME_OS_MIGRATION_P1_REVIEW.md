# Home OS Migration P1 — Review

**Status:** Staging migration  
**Branch:** `feat/home-os-migration-p1`  
**Depends on:** WorkItem engine + Marriott auth-repair slice

---

## Staging URL

With `DEMO_MODE=true`, `HOME_OS_ENABLED=true`, and a non-production staging label:

| URL | Role |
|-----|------|
| `/home` | **Default** Home OS shell |
| `/research/home` | Redirects to `/research/home-os` → `/home` |
| `/research/home-os` | Ephemeral Marriott preview session |
| `/dashboard` | Redirects to `/home` |
| `/dashboard/legacy` | Explicit legacy dashboard (developer/debug) |

Production: Home OS routes remain gated/404; `/dashboard` is unchanged as the customer home.

---

## Migration summary

1. **Default landing** — Login, signup, `/`, and `/dashboard` land on Home OS when `HOME_OS_ENABLED` is set.
2. **Single concept surface** — Status / Work Queue / Coverage / Proof only; no Living Calm hero, no Attention card stack, no sidebar on `/home`.
3. **Real sources** — Authenticated users project Attention → WorkItems, AccountState → Coverage, `account_changes` → Proof via adapters into `mighty.workitem.project_home`.
4. **Ephemeral preview** — `/research/home-os` keeps the Marriott simulated scenario for moderated demos without customer rows.
5. **Legacy preserved** — Full legacy dashboard at `/dashboard/legacy` only.

---

## Screenshots

[`docs/pr-screenshots/home-os-migration-p1/`](pr-screenshots/home-os-migration-p1/)

| File | State |
|------|--------|
| `attention.png` | Default Home OS interrupt |
| `opportunity.png` | In-place repair modal |
| `all-clear.png` | Calm after repair |

---

## Remaining legacy dependencies

- `/dashboard/legacy` still hosts Living Calm + Attention + demo brief for debug.
- Settings / logout still use existing routes (utility, not daily work).
- Attention engine remains the *producer* of candidates; Home OS does not replace Attention persistence.
- Account credentials and AuthTruth stores are read for Coverage/Work; Home OS commands do not write them yet.

---

## Simulation gaps

See [HOME_OS_SIMULATION_GAPS.md](HOME_OS_SIMULATION_GAPS.md).

Primary remaining simulation: **staged auth-repair completion** (no live provider sign-in from Home).

---

## Readiness assessment for replacing production `/dashboard`

| Criterion | P1 status |
|-----------|-----------|
| Single Status/Work/Coverage/Proof IA | Ready on staging |
| Deterministic WorkItem ranking | Ready |
| Real Attention → WorkItems | Ready (read path) |
| Real Coverage from AccountState | Ready (read path) |
| Real Proof from account_changes | Ready (read path) |
| Live in-place provider auth | **Not ready** |
| Approval / Opportunity / Setup interactions | **Not ready** (mapped into queue; repair UI is Interrupt-focused) |
| Durable lifecycle persistence | **Not ready** (session overlays for staged repair) |
| Production gate removed | **Not ready** — must stay staging-only |

**Verdict:** Ready as the **staging default** experience. **Not ready** to replace production `/dashboard` until live auth repair and durable WorkItem overlays exist.

---

## Tests

```bash
.venv/bin/python -m pytest \
  tests/test_home_os_migration_p1.py \
  tests/test_home_os_auth_repair.py \
  tests/test_workitem_engine.py \
  tests/test_research_home.py -q
```
