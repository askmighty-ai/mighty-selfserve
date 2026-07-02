"""Field discovery preprocessing pipeline.

HTML/text → normalize → remove noise → extract sections → compress → LLM input.
Provider-agnostic — no LLM calls here.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass

from mighty.field_discovery import field_discovery_max_chars


def estimate_tokens(char_count: int) -> int:
    return max(1, char_count // 4) if char_count > 0 else 0

SNIPPET_TRIGGERS = [
    "expires", "expiration", "expiry", "valid through", "valid until",
    "valid thru", "use by", "book by", "fly by", "book and fly by",
    "certificate", "voucher", "e-credit", "ecredit", "travel fund",
    "award", "benefit", "companion", "upgrade", "free night",
    "points", "miles", "balance", "rewards", "cash back",
    "due", "due date", "payment due", "autopay", "auto pay",
    "available", "remaining", "redeemable",
    "status", "tier", "medallion", "elite",
    "offer", "promotion", "bonus", "anniversary",
    "plan", "renewal", "billing", "subscription",
    "amount due", "total due", "minimum payment",
    "credit limit", "available credit",
]

HIGH_VALUE_TRIGGERS: frozenset[str] = frozenset([
    "companion", "certificate", "valid through", "valid until", "valid thru",
    "ecredit", "e-credit", "travel fund", "travel credit",
    "minimum payment", "amount due", "total due", "payment due",
    "upgrade", "global upgrade", "regional upgrade", "suite night",
    "free night", "lounge", "priority pass",
    "medallion", "elite", "autopay", "auto pay",
    "cash back", "annual fee", "statement credit",
    "book by", "fly by", "expires", "expiry", "expiration",
])

_GENERIC_ONLY_TRIGGERS: frozenset[str] = frozenset([
    "miles", "points", "balance", "rewards", "offer", "promotion", "bonus",
    "plan", "billing", "subscription", "status", "tier", "available",
])

_SNIPPET_VALUE_RE = re.compile(
    r'[$€£]\s*\d[\d,\.]*'
    r'|\b\d[\d,\.]*\b'
    r'|\b\d{1,2}[A-Za-z]{3}\d{4}\b'
    r'|\b\d{4}-\d{2}-\d{2}\b'
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}'
    , re.IGNORECASE
)

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|header|footer|nav|aside|iframe|svg|meta|link)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_BLOCK_TAG_RE = re.compile(r"</?(?:div|p|section|article|main|h[1-6]|li|tr|td|th|br)[^>]*>", re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.I)
_NOISE_LINE_RE = re.compile(
    r"^\s*(navigation:|footer:|skip to|we use cookies|accept (all )?cookies|sign up today|\[sign up|\[log in)",
    re.I,
)
_URL_MARKER_RE = re.compile(r"^===\s*(?:URL|EMBEDDED STATE|API RESPONSE):", re.I)


@dataclass(frozen=True)
class PipelineStats:
    raw_chars: int
    normalized_chars: int
    after_removal_chars: int
    sections_chars: int
    prepared_chars: int
    raw_tokens: int
    prepared_tokens: int

    @property
    def token_reduction_pct(self) -> float:
        if self.raw_tokens <= 0:
            return 0.0
        return max(0.0, (1.0 - self.prepared_tokens / self.raw_tokens) * 100.0)


@dataclass(frozen=True)
class PipelineResult:
    text: str
    stats: PipelineStats


def normalize_input(raw_text: str) -> str:
    if not raw_text:
        return ""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    if "<" in text and ">" in text:
        text = _SCRIPT_STYLE_RE.sub(" ", text)
        text = _BLOCK_TAG_RE.sub("\n", text)
        text = _HTML_TAG_RE.sub(" ", text)
        text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def remove_irrelevant_regions(text: str) -> str:
    if not text:
        return ""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if _NOISE_LINE_RE.match(stripped):
            continue
        if lower.startswith("footer:") or "privacy policy" in lower or "©" in stripped:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _section_is_relevant(section: str, hint_lower: list[str]) -> bool:
    section_lower = section.lower()
    if any(h in section_lower for h in hint_lower):
        return True
    if any(t in section_lower for t in HIGH_VALUE_TRIGGERS):
        return True
    if _SNIPPET_VALUE_RE.search(section_lower):
        return True
    if any(t in section_lower for t in _GENERIC_ONLY_TRIGGERS):
        return False
    return False


def extract_visible_sections(text: str, hint_phrases: list[str] | None = None) -> str:
    if not text:
        return ""
    hint_lower = [p.lower() for p in (hint_phrases or [])]
    sections: list[str] = []
    current: list[str] = []

    def _flush() -> None:
        if not current:
            return
        block = "\n".join(current).strip()
        current.clear()
        if block and _section_is_relevant(block, hint_lower):
            sections.append(block)

    for line in text.splitlines():
        stripped = line.strip()
        if _URL_MARKER_RE.match(stripped):
            _flush()
            sections.append(stripped)
            continue
        if not stripped:
            _flush()
            continue
        current.append(line)
    _flush()
    return "\n\n".join(sections) if sections else text


def extract_candidate_snippets(
    raw_text: str,
    context_lines: int = 8,
    max_blocks: int = 25,
    hint_phrases: list[str] | None = None,
    max_chars: int | None = None,
) -> str:
    if not raw_text:
        return ""
    cap = field_discovery_max_chars() if max_chars is None else max_chars
    raw_text = raw_text[:cap]
    fallback_len = min(8_000, cap)
    lines = raw_text.splitlines()
    lower_lines = [ln.lower() for ln in lines]
    n = len(lines)
    hit_set: set[int] = set()
    for trigger in SNIPPET_TRIGGERS:
        for i, ll in enumerate(lower_lines):
            if trigger in ll:
                hit_set.add(i)
    hint_lower = [p.lower() for p in (hint_phrases or [])]
    if hint_lower:
        for i, ll in enumerate(lower_lines):
            if any(h in ll for h in hint_lower):
                hit_set.add(i)
    if not hit_set:
        return raw_text[:fallback_len]
    ranges = [(max(0, i - context_lines), min(n, i + context_lines + 1)) for i in sorted(hit_set)]
    merged: list[tuple[int, int]] = []
    cs, ce = ranges[0]
    for s, e in ranges[1:]:
        if s <= ce + 3:
            ce = max(ce, e)
        else:
            merged.append((cs, ce))
            cs, ce = s, e
    merged.append((cs, ce))

    def _score(s: int, e: int) -> float:
        block_lines = lines[s:e]
        block_lower = "\n".join(block_lines).lower()
        high_count = sum(1 for t in HIGH_VALUE_TRIGGERS if t in block_lower)
        generic_count = sum(1 for t in SNIPPET_TRIGGERS if t in block_lower)
        val_count = len(_SNIPPET_VALUE_RE.findall(block_lower))
        avg_len = sum(len(ln) for ln in block_lines) / max(len(block_lines), 1)
        prose_penalty = max(0.0, (avg_len - 100) / 150)
        hint_bonus = 5.0 * sum(1 for h in hint_lower if h in block_lower)
        return high_count * 4.0 + generic_count * 2.0 + val_count * 1.5 - prose_penalty + hint_bonus

    scored = sorted(merged, key=lambda r: _score(*r), reverse=True)
    top = sorted(scored[:max_blocks])
    return "\n\n···\n\n".join("\n".join(lines[s:e]) for s, e in top)[:cap]


def prepare_discovery_input(
    raw_text: str,
    hint_phrases: list[str] | None = None,
    max_chars: int | None = None,
) -> PipelineResult:
    cap = field_discovery_max_chars() if max_chars is None else max_chars
    raw = raw_text or ""
    normalized = normalize_input(raw[:cap])
    cleaned = remove_irrelevant_regions(normalized)
    sections = extract_visible_sections(cleaned, hint_phrases=hint_phrases)
    prepared = extract_candidate_snippets(sections, hint_phrases=hint_phrases, max_chars=cap)
    stats = PipelineStats(
        raw_chars=len(raw),
        normalized_chars=len(normalized),
        after_removal_chars=len(cleaned),
        sections_chars=len(sections),
        prepared_chars=len(prepared),
        raw_tokens=estimate_tokens(min(len(raw), cap)),
        prepared_tokens=estimate_tokens(len(prepared)),
    )
    return PipelineResult(text=prepared, stats=stats)


def pipeline_cache_fingerprint(raw_text: str, *, max_chars: int | None = None) -> str:
    cap = field_discovery_max_chars() if max_chars is None else max_chars
    normalized = normalize_input((raw_text or "")[:cap])
    cleaned = remove_irrelevant_regions(normalized)
    return hashlib.sha256(cleaned.encode()).hexdigest()
