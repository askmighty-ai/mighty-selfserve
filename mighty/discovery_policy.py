"""Discovery matching and confidence — pure policy (Milestone 7).

Uses registry configuration from ``email_scan.SITE_SENDER_DOMAINS``.
Does not branch shared policy on provider identity.

See docs/ACCOUNT_DISCOVERY.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from mighty.email_scan import SITE_SENDER_DOMAINS

# Deterministic thresholds.
CONFIDENCE_EXACT = 0.90
CONFIDENCE_SUFFIX = 0.85
CONFIDENCE_COUNT_BONUS = 0.05
CONFIDENCE_COUNT_THRESHOLD = 3
AUTO_ENROLL_MIN_CONFIDENCE = 0.85

DISPOSITION_DISCOVERED = "discovered"
DISPOSITION_ELIGIBLE = "eligible"
DISPOSITION_ENROLLED = "enrolled"
DISPOSITION_AMBIGUOUS = "ambiguous"
DISPOSITION_DISMISSED = "dismissed"
DISPOSITION_ALREADY_ENROLLED = "already_enrolled"
DISPOSITION_IGNORED = "ignored"

MATCH_EXACT = "exact"
MATCH_SUFFIX = "suffix"


@dataclass(frozen=True)
class ProviderMatch:
    provider: str
    display_name: str
    category: str
    matched_domain: str
    match_method: str


@dataclass(frozen=True)
class DiscoveryDecision:
    provider: str
    display_name: str
    category: str
    matched_domain: str
    match_method: str
    confidence: float
    disposition: str
    email_count: int
    evidence_summary: str


def match_sender_domain(domain: str) -> ProviderMatch | None:
    """Match a sender domain to a registry provider (exact, then suffix)."""
    normalized = str(domain or "").strip().lower()
    if not normalized:
        return None
    if normalized in SITE_SENDER_DOMAINS:
        site_key, display_name, category = SITE_SENDER_DOMAINS[normalized]
        return ProviderMatch(
            provider=str(site_key),
            display_name=str(display_name),
            category=str(category),
            matched_domain=normalized,
            match_method=MATCH_EXACT,
        )
    parts = normalized.split(".")
    for i in range(1, max(1, len(parts) - 1)):
        candidate = ".".join(parts[i:])
        if candidate in SITE_SENDER_DOMAINS:
            site_key, display_name, category = SITE_SENDER_DOMAINS[candidate]
            return ProviderMatch(
                provider=str(site_key),
                display_name=str(display_name),
                category=str(category),
                matched_domain=candidate,
                match_method=MATCH_SUFFIX,
            )
    return None


def score_confidence(match: ProviderMatch, email_count: int) -> float:
    """Deterministic confidence in [0, 1]."""
    base = (
        CONFIDENCE_EXACT
        if match.match_method == MATCH_EXACT
        else CONFIDENCE_SUFFIX
    )
    count = max(0, int(email_count or 0))
    bonus = CONFIDENCE_COUNT_BONUS if count >= CONFIDENCE_COUNT_THRESHOLD else 0.0
    return min(1.0, round(base + bonus, 2))


def decide_disposition(
    confidence: float,
    *,
    is_enrolled: bool,
    is_dismissed: bool,
    auto_enroll_eligible: bool,
) -> str:
    """Map facts to a discovery disposition."""
    if is_dismissed:
        return DISPOSITION_DISMISSED
    if is_enrolled:
        return DISPOSITION_ALREADY_ENROLLED
    if (
        auto_enroll_eligible
        and confidence >= AUTO_ENROLL_MIN_CONFIDENCE
    ):
        return DISPOSITION_ELIGIBLE
    if confidence >= AUTO_ENROLL_MIN_CONFIDENCE:
        # Known registry match but not in auto-enroll set.
        return DISPOSITION_AMBIGUOUS
    return DISPOSITION_DISCOVERED


def decide_discovery(
    *,
    domain: str,
    email_count: int,
    is_enrolled: bool,
    is_dismissed: bool,
    auto_enroll_providers: frozenset[str],
) -> DiscoveryDecision | None:
    """Full pure decision for one sender-domain observation."""
    match = match_sender_domain(domain)
    if match is None:
        return None
    confidence = score_confidence(match, email_count)
    auto_eligible = match.provider in auto_enroll_providers
    disposition = decide_disposition(
        confidence,
        is_enrolled=is_enrolled,
        is_dismissed=is_dismissed,
        auto_enroll_eligible=auto_eligible,
    )
    count = max(0, int(email_count or 0))
    summary = (
        f"sender_domain={match.matched_domain}; "
        f"method={match.match_method}; messages≈{count}"
    )
    return DiscoveryDecision(
        provider=match.provider,
        display_name=match.display_name,
        category=match.category,
        matched_domain=match.matched_domain,
        match_method=match.match_method,
        confidence=confidence,
        disposition=disposition,
        email_count=count,
        evidence_summary=summary,
    )


def suggestion_to_decision(
    suggestion: dict,
    *,
    is_enrolled: bool,
    is_dismissed: bool,
    auto_enroll_providers: frozenset[str],
) -> DiscoveryDecision | None:
    """Map a legacy scan suggestion dict into a DiscoveryDecision."""
    domain = str(suggestion.get("sender") or "").strip().lower()
    if not domain:
        # Fall back to provider key lookup via any registry domain.
        site_key = str(suggestion.get("site_key") or "").strip().lower()
        for reg_domain, (key, display, category) in SITE_SENDER_DOMAINS.items():
            if key == site_key:
                domain = reg_domain
                break
    if not domain:
        return None
    return decide_discovery(
        domain=domain,
        email_count=int(suggestion.get("email_count") or 0),
        is_enrolled=is_enrolled,
        is_dismissed=is_dismissed,
        auto_enroll_providers=auto_enroll_providers,
    )
