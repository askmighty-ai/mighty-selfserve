# PR #98 — Truth Validation & Evidence Engine

Observability / evidence only. No readiness, verification, extraction, snapshot, or adapter changes.

## Before / After

- **Before (PR #97):** Technical Details = 5-stage pipeline only.
- **After:** `truth_validation` object with confidence, explanation, 8-stage pipeline, evidence, Truth Timeline, transitions, developer ids.

## Fixtures (open details for capture)

- `01-extraction-success.html`: `extraction_success` · confidence=High · transition=None→extraction_success
- `02-extraction-failed.html`: `login_visible_extraction_failed` · confidence=High · transition=None→login_visible_extraction_failed
- `03-login-unknown.html`: `login_unknown` · confidence=Low · transition=None→login_unknown
- `04-signed-out.html`: `signed_out` · confidence=High · transition=extraction_success→signed_out
- `05-timeline-pipeline.html`: `extraction_success` · confidence=High · transition=login_unknown→extraction_success
