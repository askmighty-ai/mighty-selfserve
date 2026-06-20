# Mighty Session Handoff

> Read this at the start of every new session. Update it at the end.
> Last updated: 2026-06-20

---

## Live App
- URL: `https://mighty-selfserve-production.up.railway.app`
- Deploy: `cd ~/Desktop/mighty-selfserve && git add -A && git commit -m "..." && git push origin main`
- Railway auto-deploys on push (takes ~2 min)

## Your API Key
- On Settings page → "Reveal" to see full key (format: `mk_...`)
- Required for `X-Mighty-Key` header on all `/api/*` endpoints

---

## Dev & Testing Workflow

### Local unit tests (no Railway deploy needed)
```bash
cd ~/Desktop/mighty-selfserve
python3 dev_test.py              # all tests
python3 dev_test.py connector    # connector path logic only
python3 dev_test.py intercept    # full intercept → save pipeline
python3 dev_test.py health       # site extraction health report
python3 dev_test.py candidate    # connector candidate logging
```

### Syntax check before every push
```bash
python3 -c "import ast; ast.parse(open('app.py').read()); print('OK')"
```

### Live debug endpoints (replace YOUR_KEY)
```bash
# Field discovery — synchronous, shows all Gemini steps
curl -s -H "Cookie: session=..." \
  https://mighty-selfserve-production.up.railway.app/api/debug/discover-now/hilton | jq .

# Site health — extraction rates across all sources
curl -s -H "Cookie: session=..." \
  https://mighty-selfserve-production.up.railway.app/api/admin/site-health | jq .sites[].source,.sites[].extraction_rate_pct

# Re-run discovery on all connected accounts
curl -s "https://mighty-selfserve-production.up.railway.app/api/data/rediscover-all"
```
Note: debug endpoints require browser session cookie (login first, grab cookie from DevTools).

---

## Current Architecture

### Extraction pipeline (in order)
1. **Connector paths** (`SITE_CONNECTORS` in app.py ~line 1800) — deterministic JSON paths, no Gemini
2. **Gemini** (`claude_discover_fields`) — fallback for anything connectors miss
3. **Merge** — connector fields take priority, Gemini fills gaps
4. **Auto-logging** — `[ConnectorCandidate]` in Railway logs = new path to promote to registry

### Connector registry
- 8 sites covered: hilton, delta, united, marriott, hyatt, southwest, amex, chase
- Paths are educated guesses — promote `[ConnectorCandidate]` log entries to verify
- Add new paths under `SITE_CONNECTORS` in app.py

### Acquisition tiers
- Tier 1: fetch/XHR API responses (`api_interceptor.js` → `api_extension_intercept`)
- Tier 2: embedded page state (`__NEXT_DATA__`, Apollo, Redux) — prefixed `embedded:KEY@URL`
- Tier 3/4: DOM text supplement + AI extraction (`api_extension_supplement`)

---

## Known Issues / In-Flight

### Hilton Gold not showing on dashboard
- **Status**: `discover-now` confirms 6 fields extracted (Gold, 143996 pts, etc.)
- **Fix**: Navigate to `/en/hilton-honors/guest/my-account/` with extension active,
  then hit `/api/data/rediscover-all` to save to dashboard
- **Root cause**: Hilton data comes from DOM supplement (not JSON intercept), so
  connector paths won't help — supplement pipeline is the path

### United "No data synced yet" (orange dot)
- **Status**: Likely bot detection — Railway scraper blocked
- **Next step**: Extension supplement from united.com account page

### Marriott Bonvoy card
- **Status**: Shows Gold in YOUR STATUS but card `ai_items` empty
- **Next step**: Check `discover-now/marriott` to see what raw_text contains

---

## Railway Log Signals to Watch For

```
[ConnectorCandidate] delta.elite_status → data.member.medallionStatus = 'Platinum'
→ Verified path — add to SITE_CONNECTORS["delta"]

[Intercept] hilton (tier 2): 2 fields saved (2 connector + 0 Gemini)
→ Connector working, no Gemini needed

[Intercept] hilton (tier 1): 0 fields saved (0 connector + 0 Gemini)
→ JSON payload didn't match any connector paths OR Gemini extracted nothing
→ Check what URL was intercepted (might be wrong page)

[Supplement] hilton: 6 fields extracted from /en/hilton-honors/guest/my-account/
→ DOM supplement working — this is Hilton's actual extraction path
```

---

## Files Modified This Session (2026-06-20)

| File | What changed |
|------|-------------|
| `app.py` | Added `SITE_CONNECTORS`, `try_connector_paths()`, `_resolve_json_path()`, `_find_json_path_for_value()`, `_record_connector_candidates()`, `/api/admin/site-health` endpoint; wired connectors into `api_extension_intercept` |
| `extension/manifest.json` | Bumped to 1.2.9 |
| `dev_test.py` | New — local test harness using Flask test client |
| `MIGHTY_SESSION.md` | New — this file |

**Not yet deployed** — needs `git push` after current session.

---

## Context Recovery

If a session hits the context limit, the summary prompt at the top of the next session will cover recent changes. Additionally:
- Run `python3 dev_test.py` to verify current state of logic
- Check `git log --oneline -10` to see what's been deployed
- Hit `/api/admin/site-health` on the live app to see extraction state

---

## Next Priorities

1. **Deploy current changes** (connector registry + site-health endpoint)
2. **Verify Hilton fields on dashboard** — navigate to account page, hit rediscover-all
3. **Check Railway logs for `[ConnectorCandidate]`** entries after browsing Delta/United
4. **United extraction** — debug why no data (bot detection vs wrong page)
5. **Marriott Bonvoy card** — run discover-now/marriott to diagnose empty ai_items
