# Provider Connectors

Connectors are the thin production layer that reads provider account data through
Provider Runtime. They never own browser lifecycle, MFA, or session recovery.

```text
Mighty orchestration
        ↓
ProviderConnector
        ↓
Provider Runtime
        ↓
Provider extractor
        ↓
Normalizer
        ↓
Canonical AccountSnapshot
```

## Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **Provider Runtime** | Managed browser, CDP attach, canonical auth verify, MFA/user interruption, session recovery, keepalive, campaign diagnostics, timeline | Account-data product persistence, advice |
| **Connector** | Request usable session, request surface, invoke read-only extraction, normalize, return structured refresh result | Chrome launch/stop, login loops, `page.evaluate`, payments/mutations |
| **Extractor** | Provider-specific endpoint/DOM parsing → intermediate observation | Canonical models, auth |
| **Normalizer** | Intermediate → canonical `AccountSnapshot` / `FinancialAccount` / `RewardsBalance` | Browser I/O |

## Canonical connector contract

```python
class ProviderConnector(ABC):
    provider: str

    def verify(self) -> ConnectorVerificationResult: ...
    def refresh(self) -> ConnectorRefreshResult: ...
    def capabilities(self) -> ConnectorCapabilities: ...
```

Future Chase, Delta, Marriott, Hilton, and Fidelity connectors implement the same
contract. Public return types must never include provider-specific raw objects,
Playwright pages, cookies, tokens, or response bodies.

### Canonical models

- `ConnectorCapabilities`
- `ConnectorVerificationResult`
- `ConnectorRefreshResult`
- `AccountSnapshot`
- `FinancialAccount`
- `RewardsBalance`
- `FieldObservation`
- `ConnectorTelemetry`

### Runtime APIs used by connectors

| Method | Purpose |
|--------|---------|
| `ensure_usable_session(provider)` | Canonical verify; optional recovery callback |
| `ensure_provider_surface(provider, surface)` | Navigate to a named surface (e.g. `overview`) |
| `execute_readonly_extraction(provider, extraction)` | Run a named read-only extract; returns intermediate observation |

Connectors must not call campaign-only helpers when these service methods exist.

## Amex initial field coverage

Implemented by `AmexConnector` (read-only overview slice):

| Field | Notes |
|-------|-------|
| Membership Rewards balance | Prefer structured network / runtime API; DOM fallback |
| Card accounts on overview | Opaque stable `provider_account_id`, masked `last_four` |
| Current / statement balance | When available |
| Available credit | When available |
| Payment due amount | When available |
| Payment due date | When available |
| Last verified timestamp | From refresh / runtime verify |

**Not** in this slice: transactions, offers, statements, payments, transfers,
account mutations.

## Read-only guarantee

`capabilities().read_only` is always `true` for Amex v1.

Connectors must not:

- submit payments
- redeem rewards
- activate offers
- change settings
- send messages
- update contact information
- initiate transfers
- trigger irreversible account actions

## Authentication behavior

1. Connector requests `ensure_usable_session`.
2. If `SIGNED_IN`, continue.
3. If `SIGNED_OUT` or `LOGIN_UNKNOWN`, invoke the existing runtime recovery /
   user-interruption path (operator MFA prompt). Do not invent a login loop.
4. Unresolved auth → `status=authentication_required`.
5. User Ctrl+C / MFA abandon → `user_interrupted=true` with evidence preserved.

## Partial success semantics

- Missing optional fields → `FieldStatus.unavailable`, refresh continues.
- Useful snapshot with some gaps → `partial_success` (not `failed`).
- No accounts and no rewards and no successful fields → `no_useful_data` failure.
- Warnings describe **data quality / access state only** (e.g.
  `payment_due_date_unavailable`, `overview_partially_loaded`,
  `authentication_required`). Never advice (“you should pay”, “redeem now”).

## Extraction priority (Amex)

1. Captured structured network payloads already available to the runtime
2. Authenticated JSON request via Playwright request context (cookie jar)
3. DOM fallback via `page.locator("body").inner_text` — **never** `page.evaluate`

## Developer refresh command

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py connector-refresh amex
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py connector-refresh amex --json
```

Behavior:

1. Ensure Provider Runtime (`serve` if needed)
2. Ensure a usable managed Amex session (existing production preflight)
3. Run `AmexConnector.refresh()`
4. Print a concise masked summary (or `--json` canonical result)
5. Write sanitized JSON evidence under
   `~/.mighty/provider_runtime/diagnostics/amex-connector-refresh-<UTC>.json`
6. Clean up only resources owned by the command; preexisting runtime/browser stay up
7. Ctrl+C preserves evidence

## Privacy rules

Never record or return:

- credentials, cookies, tokens
- request/response bodies
- full account numbers
- sensitive headers
- stack traces in public result objects

Stable identifiers are opaque hashes (provider-issued id when available; otherwise
hash of non-secret attributes). Display order alone is never used.

## Implementing a future provider

1. Add `<provider>_extractor.py` with intermediate observation + priority chain.
2. Add `<provider>_normalizer.py` → canonical models.
3. Add `<provider>_connector.py` implementing `ProviderConnector`.
4. Extend Provider Runtime with the smallest generic methods needed
   (`ensure_usable_session`, `ensure_provider_surface`,
   `execute_readonly_extraction`) — do not duplicate auth/browser ownership.
5. Add `connector-refresh <provider>` CLI wiring mirroring Amex.
6. Cover contract, auth, extraction, privacy, lifecycle, and advice-boundary tests.
