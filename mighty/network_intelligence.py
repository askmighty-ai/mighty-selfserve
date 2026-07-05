"""Network Intelligence (Phase 2): structured network capture markers and privacy helpers."""

from __future__ import annotations

import json
import re
from typing import Any

# ── Evidence block markers ──────────────────────────────────────────────────────
# Tier 1 passive intercept (unchanged)
API_RESPONSE_MARKER = "API RESPONSE"
# Phase 2 sync-bundled network capture
NETWORK_JSON_MARKER = "NETWORK JSON"
GRAPHQL_MARKER = "GRAPHQL"

_API_BLOCK_RE = re.compile(r"=== API RESPONSE:", re.IGNORECASE)
_NETWORK_JSON_BLOCK_RE = re.compile(r"=== NETWORK JSON:", re.IGNORECASE)
_GRAPHQL_BLOCK_RE = re.compile(r"=== GRAPHQL:", re.IGNORECASE)
_NETWORK_BLOCK_RE = re.compile(
    r"=== (?:API RESPONSE|NETWORK JSON|GRAPHQL):[^\n]*===\n([\s\S]*?)(?=\n\n=== |\n\n--- |\Z)",
    re.IGNORECASE,
)

# Sensitive JSON keys — values are redacted before storage.
SENSITIVE_JSON_KEY_RE = re.compile(
    r'"(access_token|refresh_token|id_token|password|secret|authorization|cookie|csrf|session_token|session_id|sessionid|set-cookie)"',
    re.IGNORECASE,
)

# URL path segments to skip (static assets, analytics, telemetry, uploads).
SKIP_URL_PATH_RE = re.compile(
    r"/(?:"
    r"static|assets|asset|dist|bundle|bundles|chunks|chunk|"
    r"\.(?:js|css|png|jpg|jpeg|gif|svg|webp|ico|woff2?|ttf|map)(?:\?|$)|"
    r"analytics|telemetry|tracking|track|metrics|beacon|pixel|"
    r"ads|advert|doubleclick|googletagmanager|gtm|segment|"
    r"upload|uploads|multipart"
    r")(?:/|$|\?)",
    re.IGNORECASE,
)

# Account-relevance keywords for structured JSON filtering.
ACCOUNT_KEYWORDS: tuple[str, ...] = (
    "balance",
    "points",
    "miles",
    "status",
    "tier",
    "trip",
    "reservation",
    "account",
    "payment",
    "statement",
    "transactions",
    "rewards",
    "member",
    "reward",
    "loyalty",
    "certificate",
    "award",
    "credit",
    "wallet",
    "elite",
    "skymiles",
    "bonvoy",
    "mileageplus",
    "aadvantage",
    "rapidrewards",
    "worldofhyatt",
    "membership",
    "pointsbalance",
    "membernumber",
    "memberid",
    "amountdue",
    "statementbalance",
)

# GraphQL response shape hints.
_GRAPHQL_SHAPE_RE = re.compile(r'^\s*\{\s*"(data|errors)"\s*:', re.MULTILINE)


def should_skip_network_url(url: str) -> bool:
    """Return True when a URL is unlikely to carry account JSON."""
    if not url or url.startswith("embedded:"):
        return False
    lower = url.lower()
    if lower.startswith("data:") or lower.startswith("blob:"):
        return True
    if SKIP_URL_PATH_RE.search(lower):
        return True
    if re.search(r"[?&](?:format=(?:png|jpg|gif|webp|svg|css|js)|content-type=image)", lower):
        return True
    return False


def contains_sensitive_json_keys(text: str) -> bool:
    return bool(SENSITIVE_JSON_KEY_RE.search(text or ""))


def redact_sensitive_json(text: str) -> str:
    """Redact values for sensitive keys in JSON text; returns original on parse failure."""
    if not text or not contains_sensitive_json_keys(text):
        return text
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if SENSITIVE_JSON_KEY_RE.search(f'"{key}"'):
                    out[key] = "[REDACTED]"
                else:
                    out[key] = _walk(item)
            return out
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return json.dumps(_walk(payload), separators=(",", ":"))


def is_graphql_payload(text: str) -> bool:
    if not text:
        return False
    if _GRAPHQL_SHAPE_RE.search(text):
        return True
    lower = text.lower()
    return '"query"' in lower and ('"data"' in lower or '"errors"' in lower)


def looks_like_account_json(text: str) -> bool:
    """Heuristic filter: parseable JSON with account-relevant keywords."""
    if not text or len(text) < 80 or len(text) > 1_000_000:
        return False
    try:
        json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    if contains_sensitive_json_keys(text) and re.search(
        r'"(token_type|access_token|refresh_token|id_token)"', text, re.IGNORECASE
    ):
        return False
    lower = text.lower()
    hits = sum(1 for keyword in ACCOUNT_KEYWORDS if keyword in lower)
    if is_graphql_payload(text):
        return hits >= 1 or len(text) >= 300
    if len(text) >= 500:
        return hits >= 1
    return hits >= 2


def network_block_marker(url: str, *, graphql: bool = False, sync: bool = False) -> str:
    if graphql:
        label = GRAPHQL_MARKER
    elif sync:
        label = NETWORK_JSON_MARKER
    else:
        label = API_RESPONSE_MARKER
    return f"=== {label}: {url} ==="


def format_network_block(url: str, json_data: str, *, graphql: bool = False, sync: bool = False) -> str:
    safe = redact_sensitive_json(json_data)
    return f"\n\n{network_block_marker(url, graphql=graphql, sync=sync)}\n{safe}\n"


def extract_network_blocks(raw_text: str) -> list[str]:
    """Return all network evidence blocks from raw_text."""
    text = raw_text or ""
    return [match.group(0).strip() for match in _NETWORK_BLOCK_RE.finditer(text)]


def merge_network_blocks(existing_raw: str, new_raw: str) -> str:
    """Preserve structured network blocks from existing raw_text when sync replaces content."""
    blocks = extract_network_blocks(existing_raw)
    if not blocks:
        return new_raw
    new_text = new_raw or ""
    missing = [block for block in blocks if block not in new_text]
    if not missing:
        return new_text
    prefix = "\n\n".join(missing)
    combined = f"{prefix}\n\n{new_text}".strip()
    return combined[:40_000]


def network_marker_counts(raw_text: str) -> dict[str, int]:
    text = raw_text or ""
    return {
        "api_response_blocks": len(_API_BLOCK_RE.findall(text)),
        "network_json_blocks": len(_NETWORK_JSON_BLOCK_RE.findall(text)),
        "graphql_blocks": len(_GRAPHQL_BLOCK_RE.findall(text)),
    }
