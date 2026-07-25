# Home OS — Marriott auth repair vertical slice

**Status:** Staging preview  
**Branch:** `feat/home-os-auth-repair`  
**Engine:** `mighty.workitem` ([WORKITEM_ENGINE.md](WORKITEM_ENGINE.md))

---

## Staging URL

Exact entry (staging / research / demo only):

```text
/research/home-os
```

Resolves to the Home OS shell:

```text
/home
```

**Gates (all required):**

- `DEMO_MODE=true`
- Non-production environment (`RAILWAY_ENVIRONMENT_NAME` / `MIGHTY_ENV` ≠ `production`)
- Staging/research label **or** `HOME_OS_ENABLED=true` / `RESEARCH_HOME_ENABLED=true`

Unavailable in production (404). Creates **no** `users` rows.

---

## Exact test scenario

1. Open `/research/home-os` on a gated staging deploy (or local with the env gates above).
2. Land on `/home` with the Marriott Interrupt expanded.
3. Read Status (“1 thing needs you”) and the Work Item copy.
4. Optionally expand Coverage — Marriott shows signed out.
5. Confirm Proof already shows an unrelated recent win (Amex).
6. Select **Sign in to Marriott so Mighty can see it again**.
7. In the modal, choose one path:
   - **Confirm sign-in** → success
   - **Sign-in didn’t work** → failure
   - **Cancel** → back to the same Interrupt

---

## Expected behavior at every step

| Step | Expected |
|------|----------|
| Initial | Status needs-you; single expanded Marriott Interrupt; Coverage collapsed with “1 need attention”; Proof shows prior Amex win; **no sidebar**; **no** `/credentials` or `/activity` links for repair |
| Start repair | Modal “Restore Marriott access” opens on Home; focus moves into dialog; Escape cancels |
| Success | Interrupt leaves the queue; Status → “You're good.”; Coverage → “All settled” / Marriott signed in; Proof gains “Marriott access restored…”; repair phase `succeeded` |
| Failure | Modal explains failure; Interrupt remains; Coverage still signed out; Try again / Close stay on Home |
| Cancel | Modal closes; same Interrupt + Status as before start |
| Expired | Stale Interrupt archives via lifecycle; fresh Interrupt supersedes with new id; user stays on Home with an actionable ask |

Simulation is explicit (`demo_simulated_auth_repair`). No live Marriott credentials are collected.

---

## Screenshots

Open from the repo (do not rely on chat embeds):

[`docs/pr-screenshots/home-os-auth-repair/`](pr-screenshots/home-os-auth-repair/)

| State | File |
|-------|------|
| Initial (signed out) | `attention.png` / `initial.png` |
| Interaction (modal) | `opportunity.png` / `interaction.png` |
| Success (calm + proof) | `all-clear.png` / `success.png` |
| Failure | `failure.png` |

---

## Known limitations

1. **Simulated auth only** — staged confirm/fail; not a live Marriott session capture.
2. **Single vertical slice** — Interrupt only; no Approval / Opportunity / Setup interactions.
3. **Session-backed state** — ephemeral cookie session; not a durable WorkItem store.
4. **`/dashboard` unchanged** — production Home composition remains Living Calm V2 / Attention.
5. **Coverage is Marriott-focused** — minimal inventory for the slice, not a full portfolio.
6. **Defer (“Not now”)** posts cancel today — quiet-window defer overlay can be wired next without changing ranking.

---

## Production remains unchanged

| Surface | Change |
|---------|--------|
| `/dashboard` | **None** — still legacy / Home V2 composition |
| `/credentials`, `/activity` | **None** — not migrated |
| Customer DB | **None** — Home OS preview never inserts `users` |
| Attention engine | **None** — this slice consumes `mighty.workitem`, not Attention cutover |

New routes only:

- `GET /research/home-os`
- `GET /home`
- `POST /home/work/<id>/{start,complete,fail,cancel}`

---

## Implementation map

| Concern | Module |
|---------|--------|
| Scenario + canonical Interrupt | `mighty/home_os/marriott_scenario.py` |
| Session snapshot | `mighty/home_os/session_state.py` |
| Lifecycle commands | `mighty/home_os/commands.py` → `WorkItemLifecycle` / `project_home` |
| Shell UI | `mighty/home_os/render.py` |
| HTTP adapter | `mighty/home_os/routes.py` + thin `app.py` wiring |
| Tests | `tests/test_home_os_auth_repair.py` |
