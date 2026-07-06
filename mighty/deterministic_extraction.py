"""Deterministic field extraction from captured sync evidence (no LLM).

Runs, in order:
  1. JSON connector paths (SITE_CONNECTORS)
  2. Provider-specific adapters
  3. Generic label:value parser
  4. Generic regex extractors
Then applies caller-supplied normalization (e.g. _post_filter_fields).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

_EVIDENCE_BLOCK_RE = re.compile(
    r"===\s*(?:API RESPONSE|EMBEDDED STATE)(?::[^\n=]*)?\s*===\s*\n(.*?)(?=\n=== |\Z)",
    re.DOTALL | re.IGNORECASE,
)

_LABEL_VALUE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 \-/]+):\s+(.+)$")

_SKIP_LABEL_WORDS = frozenset({"address", "phone", "email", "password"})

_EMPTY_VALUES = frozenset({"", "0", "-", "–", "—", "n/a", "none", "null", "undefined", "tbd"})

# Map noisy label-derived keys to canonical field keys used by observation coverage.
FIELD_KEY_ALIASES: dict[str, str] = {
    "current_balance": "statement_balance",
    "ultimate_rewards_points": "points_balance",
    "membership_rewards_points": "points_balance",
    "rewards_points": "points_balance",
    "points_balance": "points_balance",
    "skymiles": "points_balance",
    "medallion_status": "elite_status",
    "medallion_member": "elite_status",
}

# Account-page signals — label:value / regex extractors run only when evidence looks
# like a logged-in account surface, not generic marketing/help content.
_ACCOUNT_EVIDENCE_URL_RE = re.compile(
    r"/(?:account|dashboard|statement|rewards|my-?account|wallet|profile|benefits|summary|activity|overview|pay|certificates|e-?credits?)",
    re.IGNORECASE,
)
_ACCOUNT_EVIDENCE_TEXT_RE = re.compile(
    r"\b("
    r"points balance|membership rewards|current balance|payment due|credit limit|"
    r"skymiles|medallion|ultimate rewards|statement balance|minimum payment|"
    r"available credit|annual fee|statement date|amount due"
    r")\b",
    re.IGNORECASE,
)

_PROMO_VALUE_RE = re.compile(
    r"\b(bonus|promotional|offer|after you spend|when you spend|up to|apply today)\b",
    re.IGNORECASE,
)

_PROVIDER_REGEX: dict[str, list[tuple[str, str, str]]] = {
    "amex": [
        (r"Points Balance:\s*([\d,]+)", "points_balance", "Points Balance"),
        (r"Statement Balance:\s*\$?([\d,]+\.?\d*)", "statement_balance", "Statement Balance"),
        (r"Payment Due Date:\s*(.+)", "payment_due_date", "Payment Due Date"),
        (r"Credit Limit:\s*\$?([\d,]+)", "credit_limit", "Credit Limit"),
    ],
    "chase": [
        (r"Current Balance:\s*\$?([\d,]+\.?\d*)", "statement_balance", "Current Balance"),
        (r"Minimum Payment:\s*\$?([\d,]+\.?\d*)", "minimum_payment", "Minimum Payment"),
        (r"Payment Due Date:\s*(.+)", "payment_due_date", "Payment Due Date"),
        (r"Ultimate Rewards Points:\s*([\d,]+)", "points_balance", "Ultimate Rewards Points"),
        (r"Credit Limit:\s*\$?([\d,]+)", "credit_limit", "Credit Limit"),
        (r"Available Credit:\s*\$?([\d,]+\.?\d*)", "available_credit", "Available Credit"),
    ],
    "delta": [
        (r"SkyMiles[^0-9\n]{0,60}([\d,]+)", "points_balance", "SkyMiles Balance"),
        (r"(?:Medallion Status|Status):\s*((?:Diamond|Platinum|Gold|Silver)[^\n]*)", "elite_status", "Medallion Status"),
        (r"\b(Diamond|Platinum|Gold|Silver)\s+Medallion\b", "elite_status", "Medallion Status"),
    ],
}


@dataclass
class DeterministicExtractionResult:
    source: str
    connector_fields: list[dict[str, Any]] = field(default_factory=list)
    adapter_fields: list[dict[str, Any]] = field(default_factory=list)
    label_value_fields: list[dict[str, Any]] = field(default_factory=list)
    regex_fields: list[dict[str, Any]] = field(default_factory=list)
    normalized_fields: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    attempted: bool = False

    @property
    def raw_field_count(self) -> int:
        return (
            len(self.connector_fields)
            + len(self.adapter_fields)
            + len(self.label_value_fields)
            + len(self.regex_fields)
        )

    @property
    def field_provenance(self) -> dict[str, str]:
        """Map each surviving field key to the extractor that first produced it."""
        provenance: dict[str, str] = {}
        for group, name in (
            (self.connector_fields, "connector"),
            (self.adapter_fields, "adapter"),
            (self.label_value_fields, "label_value"),
            (self.regex_fields, "regex"),
        ):
            for field_dict in group:
                key = field_dict.get("key")
                if key and key not in provenance:
                    provenance[key] = name
        return provenance

    @property
    def stage_artifacts(self) -> dict[str, Any]:
        provenance = self.field_provenance
        normalized_keys = [f.get("key") for f in self.normalized_fields if f.get("key")]
        return {
            "source_label": "deterministic",
            "connector_count": len(self.connector_fields),
            "adapter_count": len(self.adapter_fields),
            "label_value_count": len(self.label_value_fields),
            "regex_count": len(self.regex_fields),
            "field_count": len(self.normalized_fields),
            "field_keys": normalized_keys[:20],
            "field_provenance": {k: provenance[k] for k in normalized_keys if k in provenance},
        }


def _meaningful_items(items: list[dict[str, Any]] | None) -> bool:
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip().lower()
        if key and value and value not in _EMPTY_VALUES:
            return True
    return False


def _canonical_key(key: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (key or "").lower()).strip("_")
    return FIELD_KEY_ALIASES.get(normalized, normalized)


def _field(
    key: str,
    label: str,
    value: str,
    *,
    confidence: float,
    source_tag: str,
    source_snippet: str = "",
    from_connector: bool = False,
) -> dict[str, Any]:
    canonical = _canonical_key(key)
    val = str(value or "").strip()
    out: dict[str, Any] = {
        "key": canonical,
        "label": label,
        "value": val,
        "confidence": confidence,
        "source_snippet": (source_snippet or f"[{source_tag}]")[:150],
        "from_deterministic": True,
        "extractor": source_tag.split(":")[0] if ":" in source_tag else source_tag,
    }
    if from_connector:
        out["from_connector"] = True
        out["from_api"] = True
    return out


def has_account_evidence(raw_text: str) -> bool:
    """Return True when captured text looks like account evidence, not generic marketing."""
    text = raw_text or ""
    if iter_evidence_json_blocks(text):
        return True
    if _ACCOUNT_EVIDENCE_URL_RE.search(text):
        return True
    if _ACCOUNT_EVIDENCE_TEXT_RE.search(text):
        return True
    return False


def iter_evidence_json_blocks(raw_text: str) -> list[str]:
    """Return JSON payload strings from API RESPONSE / EMBEDDED STATE blocks."""
    payloads: list[str] = []
    for match in _EVIDENCE_BLOCK_RE.finditer(raw_text or ""):
        block = (match.group(1) or "").strip()
        if not block:
            continue
        payloads.append(block)
    return payloads


def extract_connector_fields(
    source: str,
    raw_text: str,
    *,
    connector_fn: Callable[[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for payload in iter_evidence_json_blocks(raw_text):
        for hit in connector_fn(source, payload) or []:
            key = hit.get("key")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            tagged = dict(hit)
            tagged.setdefault("extractor", "connector")
            tagged.setdefault("from_deterministic", True)
            fields.append(tagged)
    return fields


def extract_adapter_fields(source: str, raw_text: str) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    if source == "amex":
        fields.extend(_extract_amex_adapter(raw_text))
    for payload in iter_evidence_json_blocks(raw_text):
        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if source == "delta":
            fields.extend(_extract_delta_api_adapter(obj))
        elif source == "amex":
            fields.extend(_extract_amex_json_adapter(obj))
        elif source == "chase":
            fields.extend(_extract_chase_json_adapter(obj))
    return fields


def _validate_field_value(key: str, value: str, *, context: str = "") -> bool:
    """Reject values that are obviously not account balances (promo copy, empty numerics)."""
    val = (value or "").strip()
    if not val or val.lower() in _EMPTY_VALUES:
        return False
    promo_scope = f"{context} {val}"
    if _PROMO_VALUE_RE.search(promo_scope):
        return False
    if key == "points_balance":
        digits = re.sub(r"[^\d]", "", val)
        return bool(digits) and int(digits) > 0
    if key in {"statement_balance", "minimum_payment", "credit_limit", "available_credit"}:
        return bool(re.search(r"\d", val))
    return True


def _extract_amex_adapter(raw_text: str) -> list[dict[str, Any]]:
    from mighty.adapters.amex_extraction import build_amex_mr_item

    fields: list[dict[str, Any]] = []
    # Only match explicit balance labels — broad "Membership Rewards … N" matches spend promos.
    match = re.search(r"Points Balance:\s*([\d,]+)", raw_text or "", re.IGNORECASE)
    if not match:
        return fields
    try:
        item = build_amex_mr_item(match.group(1))
    except ValueError:
        return fields
    fields.append(
        _field(
            item["key"],
            item["label"],
            item["value"],
            confidence=0.98,
            source_tag="adapter:amex",
            source_snippet=match.group(0)[:150],
        )
    )
    return fields


def _extract_delta_api_adapter(obj: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    bal = obj.get("smBalance")
    if bal is not None and str(bal).strip():
        digits = re.sub(r"[^\d]", "", str(bal))
        display = f"{int(digits):,}" if digits else str(bal).strip()
        fields.append(
            _field("points_balance", "SkyMiles Balance", display, confidence=0.98, source_tag="adapter:delta_api")
        )
    tier = obj.get("medallionMemberDesc") or obj.get("medallionStatus")
    if tier is not None and str(tier).strip():
        fields.append(
            _field("elite_status", "Medallion Status", str(tier).strip(), confidence=0.98, source_tag="adapter:delta_api")
        )
    return fields


def _extract_amex_json_adapter(obj: dict[str, Any]) -> list[dict[str, Any]]:
    from mighty.adapters.amex_extraction import build_amex_mr_item

    fields: list[dict[str, Any]] = []
    candidates = [
        obj.get("membershipRewards", {}).get("points") if isinstance(obj.get("membershipRewards"), dict) else None,
        obj.get("accountSummary", {}).get("rewardsBalance") if isinstance(obj.get("accountSummary"), dict) else None,
    ]
    for raw in candidates:
        if raw is None:
            continue
        try:
            item = build_amex_mr_item(str(raw))
        except ValueError:
            continue
        fields.append(
            _field(item["key"], item["label"], item["value"], confidence=0.98, source_tag="adapter:amex_json")
        )
        break
    return fields


def _extract_chase_json_adapter(obj: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    ur = obj.get("ultimateRewards", {})
    if isinstance(ur, dict):
        bal = ur.get("balance")
        if bal is not None and str(bal).strip():
            fields.append(
                _field("points_balance", "Ultimate Rewards Points", str(bal).strip(), confidence=0.98, source_tag="adapter:chase_json")
            )
    rewards = obj.get("rewards", {})
    if isinstance(rewards, dict):
        bal = rewards.get("pointsBalance")
        if bal is not None and str(bal).strip():
            fields.append(
                _field("points_balance", "Ultimate Rewards Points", str(bal).strip(), confidence=0.98, source_tag="adapter:chase_json")
            )
    summary = obj.get("accountSummary", {})
    if isinstance(summary, dict):
        stmt = summary.get("statementBalance")
        if stmt is not None and str(stmt).strip():
            fields.append(
                _field("statement_balance", "Statement Balance", str(stmt).strip(), confidence=0.98, source_tag="adapter:chase_json")
            )
    return fields


def extract_label_value_fields(raw_text: str) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for line in (raw_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("==="):
            continue
        match = _LABEL_VALUE_RE.match(line)
        if not match:
            continue
        label_raw = match.group(1).strip()
        value = match.group(2).strip()
        if any(word in label_raw.lower() for word in _SKIP_LABEL_WORDS):
            continue
        key = _canonical_key(re.sub(r"[^a-z0-9]+", "_", label_raw.lower()).strip("_"))
        if len(key) < 3 or key in seen_keys:
            continue
        if value.lower() in _EMPTY_VALUES:
            continue
        if not _validate_field_value(key, value):
            continue
        seen_keys.add(key)
        fields.append(
            _field(key, label_raw, value, confidence=0.90, source_tag="label_value", source_snippet=line[:150])
        )
    return fields


def extract_regex_fields(source: str, raw_text: str) -> list[dict[str, Any]]:
    patterns = _PROVIDER_REGEX.get(source, [])
    fields: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for pattern, key, label in patterns:
        match = re.search(pattern, raw_text or "", re.IGNORECASE | re.MULTILINE)
        if not match:
            continue
        canonical = _canonical_key(key)
        if canonical in seen_keys:
            continue
        value = match.group(1).strip()
        if value.lower() in _EMPTY_VALUES:
            continue
        if not _validate_field_value(canonical, value, context=match.group(0)):
            continue
        seen_keys.add(canonical)
        fields.append(
            _field(
                canonical,
                label,
                value,
                confidence=0.85,
                source_tag="regex",
                source_snippet=match.group(0)[:150],
            )
        )
    return fields


def _merge_field_lists(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge by canonical key; earlier groups win (higher-priority extractors first)."""
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for field_dict in group:
            key = field_dict.get("key")
            if not key or key in merged:
                continue
            merged[key] = field_dict
    return list(merged.values())


def fields_to_items(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for field_dict in fields:
        key = field_dict.get("key")
        value = str(field_dict.get("value") or "").strip()
        if not key or value.lower() in _EMPTY_VALUES:
            continue
        item: dict[str, Any] = {
            "key": key,
            "label": field_dict.get("label") or key,
            "value": value,
        }
        if field_dict.get("from_api"):
            item["from_api"] = True
        if field_dict.get("from_connector"):
            item["from_connector"] = True
        if field_dict.get("confidence") is not None:
            item["confidence"] = field_dict["confidence"]
        if field_dict.get("source_snippet"):
            item["source_snippet"] = field_dict["source_snippet"]
        items.append(item)
    return items


def extract_deterministic_fields(
    source: str,
    raw_text: str,
    *,
    existing_items: list[dict[str, Any]] | None = None,
    connector_fn: Callable[[str, str], list[dict[str, Any]]] | None = None,
    post_filter_fn: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]] | None = None,
) -> DeterministicExtractionResult:
    """Run all deterministic extractors against captured evidence."""
    result = DeterministicExtractionResult(source=source)
    if _meaningful_items(existing_items):
        result.items = list(existing_items or [])
        return result
    if not (raw_text or "").strip():
        return result

    result.attempted = True

    if connector_fn is not None:
        result.connector_fields = extract_connector_fields(source, raw_text, connector_fn=connector_fn)
    result.adapter_fields = extract_adapter_fields(source, raw_text)

    # Heuristic text extractors only on account-like evidence (not marketing/help pages).
    if has_account_evidence(raw_text):
        result.label_value_fields = extract_label_value_fields(raw_text)
        result.regex_fields = extract_regex_fields(source, raw_text)

    merged = _merge_field_lists(
        result.connector_fields,
        result.adapter_fields,
        result.label_value_fields,
        result.regex_fields,
    )

    if post_filter_fn is not None:
        result.normalized_fields = post_filter_fn(merged, source)
    else:
        result.normalized_fields = merged

    result.items = fields_to_items(result.normalized_fields)
    return result


def enrich_sync_items_from_evidence(
    source: str,
    raw_text: str,
    items: list[dict[str, Any]] | None,
    *,
    connector_fn: Callable[[str, str], list[dict[str, Any]]] | None = None,
    post_filter_fn: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]] | None = None,
) -> DeterministicExtractionResult:
    """Convenience wrapper used by sync ingest before persistence."""
    return extract_deterministic_fields(
        source,
        raw_text,
        existing_items=items,
        connector_fn=connector_fn,
        post_filter_fn=post_filter_fn,
    )
