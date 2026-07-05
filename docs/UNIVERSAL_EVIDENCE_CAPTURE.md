# Universal Evidence Capture — Phase 1

Phase 1 expands what Mighty preserves during every extension sync **without changing extraction, login, or provider-specific logic**.

## Newly captured evidence types

During tab-based sync and silent fetch, each visited account page now appends these blocks to `account_data.raw_text`:

| Block marker | Evidence type | Contents |
|--------------|---------------|----------|
| `--- {url} ---` | Visible text | Same as before — stripped body text (existing) |
| `=== PAGE META: {url} ===` | Page metadata | `title`, `canonical`, Open Graph / meta tags, `storage_keys` (names only) |
| `=== JSON-LD: {url} ===` | JSON-LD | Each `application/ld+json` script with account-relevant structured data |
| `=== EMBEDDED STATE: embedded:{key}@{url} ===` | Embedded JSON | `__NEXT_DATA__`, `__NUXT__` script payloads during sync (not only passive intercept) |

Existing passive intercept blocks are unchanged:

| Block marker | Evidence type |
|--------------|---------------|
| `=== API RESPONSE: {url} ===` | XHR / fetch JSON (Tier 1 intercept) |
| `=== EMBEDDED STATE: embedded:{key}@{url} ===` | Window globals during browse (Tier 2 intercept) |

## Privacy filters

Captured JSON is skipped when it:

- Fails JSON parse
- Exceeds 12 KB per block
- Contains sensitive keys (`access_token`, `password`, `cookie`, `csrf`, etc.)

Page metadata skips meta tag names matching password/token/cookie/auth patterns.

`localStorage` captures **key names only**, never values.

## Systems updated

- **Extension sync** (`crawlAccount`, `_silentFetchPages`): universal page evidence collector
- **Capture Capability** (`mighty/capture_capability.py`): detects `page_metadata_blocks`, `json_ld_blocks`, and `page_metadata` capability (distinct from `dom_html`)
- **Pipeline Inspector**: inferred capture stage stores parsed `evidence_markers` dict
- **Field discovery preprocess**: preserves `PAGE META` and `JSON-LD` blocks through normalization

## Not in Phase 1

- Full HTML snapshots
- Replay engine
- Provider-specific capture paths
- Extraction or recommendation changes
