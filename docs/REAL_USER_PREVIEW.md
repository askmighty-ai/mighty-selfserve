# Real-user preview — Home V2 research

Moderated Home V2 testing on Railway **staging** only. Production is never used.

## Staging URL

https://mighty-selfserve-staging.up.railway.app

## Participant URLs (no login)

Share one link per task. Participants do **not** need a username, password, invitation, or setup.

| State | Participant URL |
| --- | --- |
| Healthy (default) | https://mighty-selfserve-staging.up.railway.app/research/home |
| Healthy (explicit) | https://mighty-selfserve-staging.up.railway.app/research/home?state=healthy |
| Attention Required | https://mighty-selfserve-staging.up.railway.app/research/home?state=attention |
| Opportunity Available | https://mighty-selfserve-staging.up.railway.app/research/home?state=opportunity |

Each URL:

1. Creates an ephemeral demo-only session (cookie only)
2. Loads deterministic fictional Home data (persona **Jordan**)
3. Redirects immediately to `/dashboard`
4. Shows a small **Research preview** indicator

No customer row is written to the database.

## Moderator checklist

1. Confirm Railway **staging** (not production):
   - `DEMO_MODE=true`
   - `RAILWAY_ENVIRONMENT_NAME=staging` (or `RESEARCH_HOME_ENABLED=true`)
   - `BASE_URL=https://mighty-selfserve-staging.up.railway.app`
2. Open each participant URL in a private window and confirm:
   - Healthy → “You're good.”
   - Attention → sign-in / only-step framing with primary CTA
   - Opportunity → “Value waiting” framing with primary CTA
   - Indicator **Research preview** is visible
3. Confirm production is unchanged and `/research/home` returns **404** there.
4. Remind participants that CTAs (Gmail, provider sign-in, accounts, email) are disabled stubs.

## Safety guarantees

The research route is available only when **all** are true:

- `DEMO_MODE=true`
- Environment is **not** production
- Environment is explicitly **staging** / **research**, or `RESEARCH_HOME_ENABLED=true`

While a research session is active:

- No persistent customer records
- No real credentials, tokens, or customer emails
- Gmail OAuth, provider sign-in, email send, enrollment/removal, account mutations, and mutating APIs return stubs / 403
- Background-facing API polls return empty disabled payloads

## Disabling the research route after testing

Do **one or more** of the following on the Railway **staging** service (Variables):

1. Set `DEMO_MODE=false` **or** remove it  
2. Set `RESEARCH_HOME_ENABLED=false` (if it was set)  
3. Optionally redeploy without the research entry (or remove `/research/home` in a follow-up PR)

After disable, https://mighty-selfserve-staging.up.railway.app/research/home must return **404**.

**Do not** enable these flags on production.

## Local verification

```bash
DEMO_MODE=true RAILWAY_ENVIRONMENT_NAME=staging RESEARCH_HOME_ENABLED=true \
  pytest tests/test_research_home.py -q
```
