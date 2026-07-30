# Deployment alignment (four states)

Implementation reports must distinguish:

1. **Local working tree** — may include uncommitted edits
2. **Committed HEAD** — `git rev-parse HEAD`
3. **Pushed remote** — upstream tip for the branch
4. **Deployed production** — `/health.readiness_content_sha` (and `deployment_sha` / `git_sha` when Railway injects `RAILWAY_GIT_COMMIT_SHA`)

Do **not** claim “shipped” or “working in production” until all four align.

```bash
FOUNDER_HOST=https://mighty-selfserve-production.up.railway.app \
  python scripts/report_deployment_alignment.py
```
