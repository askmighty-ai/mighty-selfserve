"""
Field discovery input preprocessing pipeline.

HTML or scraped text → normalize → remove noise → extract sections → compress
before sending to the LLM. Provider-agnostic; preserves downstream field schema.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

from mighty.field_discovery import field_discovery_max_chars

# ── Trigger words for visible-section extraction ─────────────────────────────
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

_HIGH_VALUE_TRIGGERS: frozenset[str] = frozenset([
    "companion", "certificate", "valid through", "valid until", "valid thru",
    "ecredit", "e-credit", "travel fund", "travel credit",
    "minimum payment", "amount due", "total due", "payment due",
    "upgrade", "global upgrade", "regional upgrade", "suite night",
    "free night", "lounge", "priority pass",
    "medallion", "elite", "autopay", "auto pay",
    "cash back", "annual fee", "statement credit",
    "book by", "fly by", "expires", "expiry", "expiration",
])

_SNIPPET_VALUE_RE = re.compile(
    r'[$€£]\s*\d[\d,\.]*'
    r'|\b\d[\d,\.]*\b'
    r'|\b\d{1,2}[A-Za-z]{3}\d{4}\b'
    r'|\b\d{4}-\d{2}-\d{2}\b'
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}'
    , re.IGNORECASE
)

_STRUCTURED_BLOCK_RE = re.compile(
    r"(=== (?:URL|EMBEDDED STATE|API RESPONSE)[^\n]*===[\s\S]*?)(?=\n=== |\Z)",
    re.MULTILINE,
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(?:script|style)[\s\S]*?</(?:script|style)>", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")

_NOISE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*navigation\s*:", re.IGNORECASE),
    re.compile(r"^\s*footer\s*:", re.IGNORECASE),
    re.compile(r"^\s*©\s*\d{4}", re.IGNORECASE),
    re.compile(r"privacy policy|cookie policy|terms (?:and|&)\s*conditions", re.IGNORECASE),
    re.compile(r"^\s*(?:sign out|log out|help center|contact us)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:book flights?|car rental|flight status)\s*[|→]", re.IGNORECASE),
    re.compile(r"^\s*explore all partners", re.IGNORECASE),
    re.compile(r"^\s*earn more miles with our partners", re.IGNORECASE),
    re.compile(r"^\s*shop, dine, and stay to earn", re.IGNORECASE),
)

_MARKETING_HEADINGS = frozenset([
    "how it works", "our partners", "shop and earn", "dine and earn",
    "stay and earn", "sign up today", "earn miles on everything you do",
])

_SEPARATOR_ONLY_RE = re.compile(r"^[\s\-_|•→·.]+$")


def estimate_tokens(text: str) -> int:
    """Rough token estimate for benchmarking (≈4 chars per token)."""
    return max(1, len(text or "") // 4)


def _looks_like_html(text: str) -> bool:
    sample = (text or "")[:4000]
    if "<html" in sample.lower() or "<body" in sample.lower():
        return True
    tags = _HTML_TAG_RE.findall(sample)
    return len(tags) >= 3


def normalize_discovery_input(raw_text: str) -> str:
    """Normalize HTML or plain text into consistent line-oriented input."""
    if not raw_text:
        return ""

    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)

    if _looks_like_html(text):
        text = _SCRIPT_STYLE_RE.sub(" ", text)
        text = _HTML_COMMENT_RE.sub(" ", text)
        text = _HTML_TAG_RE.sub("\n", text)
        text = html.unescape(text)

    # Preserve structured blocks while normalizing surrounding whitespace.
    blocks: list[str] = []
    last_end = 0
    for match in _STRUCTURED_BLOCK_RE.finditer(text):
        prefix = text[last_end:match.start()]
        blocks.append(_collapse_line_whitespace(prefix))
        blocks.append(match.group(1).strip())
        last_end = match.end()
    blocks.append(_collapse_line_whitespace(text[last_end:]))

    normalized = "\n\n".join(part for part in blocks if part.strip())
    return normalized.strip()


def _collapse_line_whitespace(text: str) -> str:
    lines = []
    for line in text.splitlines():
        collapsed = re.sub(r"[ \t]+", " ", line.strip())
        if collapsed:
            lines.append(collapsed)
    return "\n".join(lines)


def _line_is_noise(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if lower in _MARKETING_HEADINGS:
        return True
    if _SEPARATOR_ONLY_RE.match(stripped):
        return True
    if len(stripped) <= 2 and stripped in {"|", "→", "·", "-"}:
        return True
    return any(pattern.search(stripped) for pattern in _NOISE_LINE_PATTERNS)


def remove_irrelevant_regions(text: str) -> str:
    """Drop navigation, footer, and generic marketing lines."""
    if not text:
        return ""

    kept: list[str] = []
    for line in text.splitlines():
        if _line_is_noise(line):
            continue
        kept.append(line.rstrip())

    # Drop trailing footer runs (copyright / legal links at document tail).
    while kept and _line_is_noise(kept[-1]):
        kept.pop()

    return "\n".join(kept).strip()


def extract_visible_sections(
    text: str,
    *,
    context_lines: int = 8,
    max_blocks: int = 25,
    hint_phrases: list[str] | None = None,
    max_chars: int | None = None,
) -> str:
    """Extract scored line blocks around account-data trigger words."""
    if not text:
        return ""

    cap = field_discovery_max_chars() if max_chars is None else max_chars
    text = text[:cap]
    fallback_len = min(8_000, cap)

    lines = text.splitlines()
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
        return text[:fallback_len]

    ranges: list[tuple[int, int]] = [
        (max(0, i - context_lines), min(n, i + context_lines + 1))
        for i in sorted(hit_set)
    ]

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
        high_count = sum(1 for t in _HIGH_VALUE_TRIGGERS if t in block_lower)
        generic_count = sum(1 for t in SNIPPET_TRIGGERS if t in block_lower)
        val_count = len(_SNIPPET_VALUE_RE.findall(block_lower))
        avg_len = sum(len(ln) for ln in block_lines) / max(len(block_lines), 1)
        prose_penalty = max(0.0, (avg_len - 100) / 150)
        hint_bonus = 5.0 * sum(1 for h in hint_lower if h in block_lower)
        return high_count * 4.0 + generic_count * 2.0 + val_count * 1.5 - prose_penalty + hint_bonus

    scored = sorted(merged, key=lambda r: _score(*r), reverse=True)
    top = sorted(scored[:max_blocks])

    return "\n\n···\n\n".join("\n".join(lines[s:e]) for s, e in top)


def compress_discovery_text(text: str, max_chars: int | None = None) -> str:
    """Deduplicate lines, collapse blank runs, and enforce a char cap."""
    if not text:
        return ""

    cap = field_discovery_max_chars() if max_chars is None else max_chars
    out_lines: list[str] = []
    seen: set[str] = set()
    blank_run = 0

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            blank_run += 1
            if blank_run <= 1:
                out_lines.append("")
            continue
        blank_run = 0

        key = stripped.lower()
        if key in seen:
            continue
        seen.add(key)
        out_lines.append(stripped)

    compressed = "\n".join(out_lines).strip()
    if cap > 0:
        compressed = compressed[:cap]
    return compressed


@dataclass(frozen=True)
class PreprocessStats:
    raw_chars: int
    normalized_chars: int
    filtered_chars: int
    sections_chars: int
    final_chars: int

    @property
    def raw_tokens(self) -> int:
        return estimate_tokens(" " * self.raw_chars)

    @property
    def final_tokens(self) -> int:
        return estimate_tokens(" " * self.final_chars)

    @property
    def reduction_ratio(self) -> float:
        if self.raw_chars <= 0:
            return 0.0
        return 1.0 - (self.final_chars / self.raw_chars)


@dataclass(frozen=True)
class PreprocessResult:
    text: str
    stats: PreprocessStats


def prepare_discovery_input(
    raw_text: str,
    *,
    hint_phrases: list[str] | None = None,
    max_chars: int | None = None,
) -> PreprocessResult:
    """Run the full discovery preprocessing pipeline."""
    raw_chars = len(raw_text or "")
    normalized = normalize_discovery_input(raw_text)
    filtered = remove_irrelevant_regions(normalized)
    sections = extract_visible_sections(
        filtered,
        hint_phrases=hint_phrases,
        max_chars=max_chars,
    )
    final = compress_discovery_text(sections, max_chars=max_chars)
    stats = PreprocessStats(
        raw_chars=raw_chars,
        normalized_chars=len(normalized),
        filtered_chars=len(filtered),
        sections_chars=len(sections),
        final_chars=len(final),
    )
    return PreprocessResult(text=final, stats=stats)
