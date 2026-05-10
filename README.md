# Mighty Self-Serve

Personal authorization layer for AI agents. Sign up, get an API key, paste a system prompt into your Claude project — your agents log every action and request your approval before doing anything consequential.

## Deploy to Railway

1. Fork or push this repo to GitHub
2. Create a new Railway service from the repo
3. Set these environment variables in Railway:
   - `SECRET_KEY` — any random string (Railway → Variables → Add)
   - `BASE_URL` — your Railway public URL (e.g. `https://mighty-selfserve.up.railway.app`)
   - `DATABASE_PATH` — `/app/mighty.db` (or add a Railway Volume for persistence)
4. Set start command: `python3 app.py`

## Run locally

```bash
pip install flask
python3 app.py
# → http://localhost:5004
```

## API

### Log a completed action (no approval needed)
```
POST /api/record
{"api_key": "mk_...", "action_type": "email", "label": "Sent weekly summary", "outcome": "completed", "fields": [["To", "user@example.com"]]}
```

### Request authorization before an action
```
POST /api/authorize
{"api_key": "mk_...", "action_type": "purchase", "label": "Buy Pro subscription", "fields": [["Amount", "$49/mo"], ["Vendor", "Acme Corp"]]}

→ {"status": "pending", "request_id": "...", "approval_url": "...", "poll_url": "..."}
```

### Poll for decision
```
GET /api/status/<request_id>?api_key=mk_...
→ {"status": "approved"}  or  "denied"  or  "pending"  or  "timeout"
```

## Note on persistence

Railway has an ephemeral filesystem — the SQLite database resets on each deploy. For production, add a Railway Volume and set `DATABASE_PATH` to a path inside it, or swap SQLite for Railway's Postgres add-on.
