# Surface inventory — customer-facing visual systems

**Cycle:** `visual-surface-migration`  
**Date:** 2026-07-28  
**Purpose:** Ground the migration plan in a complete inventory. Update when routes change.

## Visual system legend

| ID | Name | Fingerprints |
|----|------|--------------|
| **A** | Marketing / Inter shell | Inter (Google), `BASE_CSS`, purple `#7c3aed` **or** navy/indigo marketing accents, purple-gradient or PNG wordmark, card/auth patterns, dark indigo `.app-shell` sidebar |
| **B** | MDS Quiet Field / pine | `body.mds`, `static/design-system/*`, Fraunces + Plus Jakarta, pine CTAs, `.mds-brand` |
| **C** | Legacy green accents | Green CTAs/chips (`#16a34a`, `#059669`, `#10b981`) without a full green shell |
| **D** | Hybrid / mixed | Two systems on one route (e.g. Inter sidebar + MDS hero) |
| **E** | Other | Admin, research, system-font utility pages |

**Target product language for customer families:** **B** (Quiet Field / MDS), with shared shell where Authenticated Application + Settings require chrome. Marketing/Public may stay a deliberate “door” only if logo/tokens/type are continuous enough not to reset “two products.”

---

## Cross-cutting defects (Founder Session 1–2)

| Defect | Status |
|--------|--------|
| Landing logo / mark treatment vs app | Open — navy marketing mark ≠ purple gradient wordmark ≠ MDS brand ≠ sidebar PNG |
| Duplicated Sign In on `/` | Open — nav `.nav-signin` **and** hero `.hero-link` both → `/login` |
| Typography | Open — Inter vs Fraunces/Jakarta vs system UI within journeys |
| Color system | Open — purple / navy-indigo / pine / green accents coexist |
| Authenticated Home vs nav | **Closed (P0 + UBE daily)** — `/home` (when enabled) and production `/dashboard`, `/credentials`, `/activity`, `/settings` share MDS `data-app-shell="mds"` nav |

---

## Inventory by route

| Route | Family | System | Notes |
|-------|--------|--------|-------|
| `/` | Marketing/Public | **A\*** | Inter + navy (`#0a2540` / `#635bff`), not purple wordmark; **dual Sign In** |
| `/privacy`, `/tos` | Marketing/Public | **A** | Inter + purple links |
| 404 / 403 / 500 | Marketing/Public | **A** | Purple gradient wordmark |
| `/login` | Authentication | **A** | Purple Inter card |
| `/forgot-password`, `/reset-password/<token>` | Authentication | **A** | Same stack as login |
| `/signup` | Authentication | **B** | MDS — **mixed Authentication family** with login |
| `/beta/restart` | Authentication | **B** | MDS factory reset |
| `/email-scan` (+ confirm) | Onboarding | **B** | Discover shell |
| `/enable-monitoring` | Onboarding | **B** | MDS |
| `/extension-setup` | Onboarding / Extension | **B** | MDS (was green shell); Settings still green-links here (**C** accent) |
| `/onboarding` | Onboarding | redirect | Dead `ONBOARDING_HTML` still **A** in tree |
| `/home` | Authenticated Application | **B** | Home OS Quiet Field on shared MDS app shell (`authenticated_app_shell`) |
| `/dashboard` | Authenticated Application | **B** (customer) | Production daily home re-framed on shared MDS shell (`ube-one-daily-product`); `?keep=1` Inter debug escape |
| `/dashboard/legacy` | Authenticated Application | **B** / redirect | Same body as `/dashboard` when Home OS off; redirect→`/home` when Home OS default |
| `/credentials` | Authenticated Application | **B** | Same MDS shell; list chrome token-bridged (pine) |
| `/activity` | Authenticated Application | **B** | Same MDS shell; MDS empty state |
| `/account-center` | Authenticated Application | redirect→`/credentials` | — |
| `/settings` | Settings | **B** (shell) | Same MDS app shell as Authenticated Application; privacy children still **E** |
| `/privacy/audit-log`, `/privacy/domains` | Settings | **E** | System font `pg-nav` — **third Settings generation** |
| `/approve/<token>` | Authenticated Application (token) | **D** | Purple brand + green Approve |
| Extension `popup.html` | Extension/Chrome | **D** | Purple chrome + green status; not MDS |
| `/candidates/<source>` | Authenticated Application | **E** | Minimal system UI |
| `/admin/*`, research stubs | Admin/Internal | **E** / **B** showcase | Out of customer Done definition unless linked from customer path |

---

## Family coherence scorecard

| Family | Surfaces | Generations present | Coherent? | Blocking mixes |
|--------|----------|---------------------|-----------|----------------|
| **Marketing/Public** | `/`, privacy, tos, errors | A (navy landing) + A (purple errors/privacy) | **No** | Dual Sign In; logo ≠ app; navy vs purple |
| **Authentication** | signup, login, forgot, reset, beta restart | **B** + **A** | **No** | MDS signup vs purple Inter login door |
| **Onboarding** | email-scan, enable-monitoring, extension-setup | **B** (mostly) | **Near** | Still not on shared app shell nav (P0 left Discover as onboarding family) |
| **Authenticated Application** | `/home` (when enabled), production `/dashboard`, credentials, activity, settings | **B** | **Yes (P0 + ube-one-daily-product)** | Shared MDS shell on customer path including production `/dashboard`. Residual: Discover not on app shell; privacy children E; body composition may still differ Home OS vs legacy sections |
| **Settings** | `/settings`, privacy children | **B** shell + **E** children | **Near** | `/settings` on app shell; privacy children still system-font **E** (P2 residual) |
| **Extension/Chrome** | popup + extension-setup web | **D** + **B** | **No** | Popup purple/green vs MDS setup page |

**Done rule:** A family is complete only when its scorecard row is **Coherent = Yes** (single generation + one logo treatment + one type stack + one color system), not when a subset of routes look updated.

---

## Chrome topology (authenticated hotspot)

```text
MDS app shell (B):   /dashboard (prod) · /home (Home OS on) · /credentials · /activity · /settings
                     └─ shared mds-nav (Home / Accounts / Activity* / Find / Settings)
                        * Activity conditional on projected items

When Home OS is default:  /dashboard → /home ; /dashboard/legacy → /home
When Home OS is gated:    /dashboard serves home body on shared MDS shell (ube-one-daily-product);
                          ?keep=1 keeps Inter debug document
Discover (B onboarding):  /email-scan (not yet on shared app shell nav chrome)
```

Home OS environments and production (Home OS gated): customers stay inside one MDS shell across the Authenticated Application chrome families. Body composition (Quiet Field vs legacy home sections) may still differ by gate — chrome claim only.

---

## Source anchors

| Area | Primary modules |
|------|-----------------|
| A shell | `app.py` `BASE_CSS`, `*_HTML`, `_sidebar_parts` |
| B onboarding / Home | `mighty/{signup,discover_accounts,enable_monitoring,extension_setup,beta_restart}_ui.py`, `mighty/home_os/render.py`, `mighty/home_ui.py` |
| Tokens | `static/design-system/{tokens,base,mighty-ds}.css` |
| Extension | `extension/popup.html` |
