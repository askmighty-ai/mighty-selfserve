"""
Amex-specific read-only extraction.

Priority:
1. captured structured network data already available in session storage
2. authenticated request through Provider Runtime transport
3. DOM fallback via Playwright locator text (never page.evaluate)

Returns a provider-specific intermediate observation model. The normalizer
converts this into canonical connector models.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from mighty.provider_connector import FieldConfidence, FieldSource, FieldStatus


AMEX_OVERVIEW_URL = "https://global.americanexpress.com/overview"
AMEX_READ_USER_SESSION_URL = "https://functions.americanexpress.com/ReadUserSession.v1"

# Known Amex authenticated JSON endpoints that may carry overview-adjacent data.
# Bodies are parsed only for structured account/rewards fields; never persisted raw.
AMEX_STRUCTURED_ENDPOINTS = (
    "https://functions.americanexpress.com/ReadAccountSummary.v1",
    "https://functions.americanexpress.com/ReadAccountDashboard.v1",
    "https://global.americanexpress.com/api/servicing/v1/financials/account_summary",
)

MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_url(raw_url: str | None) -> str | None:
    """Strip query strings and fragments from recorded URLs."""
    if not raw_url:
        return None
    text = str(raw_url).strip()
    if not text:
        return None
    for sep in ("#", "?"):
        if sep in text:
            text = text.split(sep, 1)[0]
    return text or None


def mask_account_number(raw: str | None) -> str | None:
    """Return last-four only; never expose a full account number."""
    if raw is None:
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    if len(digits) < 4:
        stars = re.sub(r"[^\d*]", "", str(raw))
        if len(stars) >= 4:
            return stars[-4:].replace("*", "") or None
        return None
    return digits[-4:]


def stable_account_id(
    *,
    last_four: str | None,
    product_name: str | None,
    provider_issued_id: str | None = None,
) -> str:
    """Opaque deterministic account id — never display order alone."""
    if provider_issued_id:
        digest = hashlib.sha256(f"amex:issued:{provider_issued_id}".encode("utf-8"))
        return f"amex_{digest.hexdigest()[:16]}"
    basis = f"amex:{last_four or 'xxxx'}:{product_name or 'card'}"
    digest = hashlib.sha256(basis.encode("utf-8"))
    return f"amex_{digest.hexdigest()[:16]}"


@dataclass
class AmexFieldObservation:
    field_name: str
    status: str
    source: str
    observed_at: str
    confidence: str
    value: Any = None
    reason: str | None = None
    account_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "field_name": self.field_name,
            "status": self.status,
            "source": self.source,
            "observed_at": self.observed_at,
            "confidence": self.confidence,
            "value": self.value,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.account_ref:
            payload["account_ref"] = self.account_ref
        return payload


@dataclass
class AmexCardObservation:
    last_four: str | None = None
    product_name: str | None = None
    display_name: str | None = None
    provider_issued_id: str | None = None
    current_balance: str | None = None
    available_credit: str | None = None
    payment_due_amount: str | None = None
    payment_due_date: str | None = None
    currency: str = "USD"

    def opaque_id(self) -> str:
        return stable_account_id(
            last_four=self.last_four,
            product_name=self.product_name or self.display_name,
            provider_issued_id=self.provider_issued_id,
        )


@dataclass
class AmexRewardsObservation:
    program_name: str = "Membership Rewards"
    balance: str | None = None
    unit: str = "points"


@dataclass
class AmexExtractionObservation:
    """Provider-specific intermediate model (not part of public connector API)."""

    provider: str = "amex"
    observed_at: str = field(default_factory=utc_now_iso)
    surface_url: str | None = None
    extraction_method: str = "none"
    cards: list[AmexCardObservation] = field(default_factory=list)
    rewards: AmexRewardsObservation | None = None
    field_observations: list[AmexFieldObservation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    method_counts: dict[str, int] = field(default_factory=dict)
    useful: bool = False

    def to_sanitized_dict(self) -> dict[str, Any]:
        """Sanitized diagnostics — values only, never raw HTML/bodies/cookies."""
        return {
            "provider": self.provider,
            "observed_at": self.observed_at,
            "surface_url": sanitize_url(self.surface_url),
            "extraction_method": self.extraction_method,
            "card_count": len(self.cards),
            "rewards_present": bool(self.rewards and self.rewards.balance),
            "field_observations": [
                {
                    "field_name": obs.field_name,
                    "status": obs.status,
                    "source": obs.source,
                    "observed_at": obs.observed_at,
                    "confidence": obs.confidence,
                    "reason": obs.reason,
                    "account_ref": obs.account_ref,
                    # Include scalar values for evidence; never full account numbers.
                    "value": obs.value,
                }
                for obs in self.field_observations
            ],
            "warnings": list(self.warnings),
            "method_counts": dict(self.method_counts),
            "useful": self.useful,
            "cards": [
                {
                    "provider_account_id": card.opaque_id(),
                    "last_four": mask_account_number(card.last_four),
                    "product_name": card.product_name,
                    "display_name": card.display_name,
                    "current_balance": card.current_balance,
                    "available_credit": card.available_credit,
                    "payment_due_amount": card.payment_due_amount,
                    "payment_due_date": card.payment_due_date,
                    "currency": card.currency,
                }
                for card in self.cards
            ],
            "rewards": (
                {
                    "program_name": self.rewards.program_name,
                    "balance": self.rewards.balance,
                    "unit": self.rewards.unit,
                }
                if self.rewards
                else None
            ),
        }


def _obs(
    field_name: str,
    *,
    status: str,
    source: str,
    value: Any = None,
    confidence: str = FieldConfidence.MEDIUM.value,
    reason: str | None = None,
    account_ref: str | None = None,
    observed_at: str | None = None,
) -> AmexFieldObservation:
    return AmexFieldObservation(
        field_name=field_name,
        status=status,
        source=source,
        observed_at=observed_at or utc_now_iso(),
        confidence=confidence,
        value=value,
        reason=reason,
        account_ref=account_ref,
    )


def _parse_points(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    if value <= 0:
        return None
    return str(value)


def _parse_money_string(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    if not cleaned:
        return None
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if amount < 0:
        return None
    return format(amount, "f")


def parse_due_date(raw: str | None) -> str | None:
    """Normalize common Amex due-date strings to ISO YYYY-MM-DD when possible."""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if iso:
        return text
    mdy = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", text)
    if mdy:
        month, day, year = int(mdy.group(1)), int(mdy.group(2)), int(mdy.group(3))
        if year < 100:
            year += 2000
        return f"{year:04d}-{month:02d}-{day:02d}"
    named = re.match(
        r"^(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:,)?\s+(\d{4})$",
        text,
        re.I,
    )
    if named:
        key = named.group(1).lower()
        month = MONTH_MAP.get(key) or MONTH_MAP.get(key[:3])
        if not month and key.startswith("sept"):
            month = 9
        if not month:
            return text
        day = int(named.group(2))
        year = int(named.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    return text


def extract_membership_rewards_from_text(body_text: str) -> str | None:
    patterns = (
        r"Membership\s+Rewards[^0-9\n]{0,120}([\d][\d,]*)",
        r"Points\s+Balance[^0-9\n]{0,40}([\d][\d,]*)",
        r"(?:points|rewards)\s*(?:balance|:)?\s*([\d][\d,]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, body_text, re.I)
        if match:
            parsed = _parse_points(match.group(1))
            if parsed:
                return parsed
    return None


def extract_cards_from_text(body_text: str) -> list[AmexCardObservation]:
    """Parse card blocks from overview body text."""
    cards: list[AmexCardObservation] = []
    if not body_text:
        return cards

    # Split on card-ending markers. Product names may sit just before the marker;
    # monetary fields are taken only from the marker through the next card.
    segments: list[tuple[str | None, str, str]] = []
    ending_iter = list(
        re.finditer(r"card\s+ending\s+(?:in\s+)?([\d*]{4,})", body_text, re.I)
    )
    if not ending_iter:
        segments.append((None, body_text, body_text))
    else:
        for idx, match in enumerate(ending_iter):
            start = match.start()
            end = (
                ending_iter[idx + 1].start()
                if idx + 1 < len(ending_iter)
                else len(body_text)
            )
            name_start = max(0, start - 120)
            if idx > 0:
                # Keep product-name lookback inside this card's preface only.
                name_start = max(ending_iter[idx - 1].start(), start - 120)
                # Prefer text after the previous card's money block when possible:
                # walk back only within the gap after the previous marker line.
                gap = body_text[ending_iter[idx - 1].end() : start]
                # Use the trailing portion of the gap (product name), not prior money.
                name_region = gap[-120:] if len(gap) > 120 else gap
            else:
                name_region = body_text[name_start:start]
            money_region = body_text[start:end]
            if idx == 0:
                name_region = body_text[name_start:start]
            segments.append(
                (mask_account_number(match.group(1)), name_region, money_region)
            )

    seen_endings: set[str] = set()
    for last_four, name_region, money_region in segments:
        if last_four and last_four in seen_endings:
            continue
        if last_four:
            seen_endings.add(last_four)

        product_name = _extract_product_name(name_region)
        current_balance = _first_money(
            money_region,
            (
                r"(?:current|statement)\s+balance[^$\d]{0,40}\$?([\d][\d,]*(?:\.\d{2})?)",
                r"balance[^$\d]{0,20}\$?([\d][\d,]*(?:\.\d{2})?)",
            ),
        )
        available_credit = _first_money(
            money_region,
            (r"available\s+credit[^$\d]{0,40}\$?([\d][\d,]*(?:\.\d{2})?)",),
        )
        payment_due_amount = _first_money(
            money_region,
            (
                r"(?:minimum\s+)?payment\s+due(?!\s+date)[^$\d]{0,40}\$?([\d][\d,]*(?:\.\d{2})?)",
                r"amount\s+due[^$\d]{0,40}\$?([\d][\d,]*(?:\.\d{2})?)",
            ),
        )
        payment_due_date_raw = _first_match(
            money_region,
            (
                r"(?:payment\s+)?due\s+(?:date\s+)?(?:on\s+)?"
                r"((?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
                r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
                r"nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,)?\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}"
                r"|\d{4}-\d{2}-\d{2})",
            ),
        )
        payment_due_date = parse_due_date(payment_due_date_raw)

        if not any(
            [
                last_four,
                current_balance,
                available_credit,
                payment_due_amount,
                payment_due_date,
                product_name,
            ]
        ):
            continue

        display = product_name or (
            f"Card ending {last_four}" if last_four else "Amex Card"
        )
        cards.append(
            AmexCardObservation(
                last_four=last_four,
                product_name=product_name,
                display_name=display,
                current_balance=current_balance,
                available_credit=available_credit,
                payment_due_amount=payment_due_amount,
                payment_due_date=payment_due_date,
            )
        )
        if len(cards) >= 8:
            break
    return cards


def _extract_product_name(segment: str) -> str | None:
    patterns = (
        r"((?:Blue|Gold|Green|Platinum|Business|Delta|Hilton|Marriott|EveryDay|"
        r"Cash Magnet|Optima|Centurion)[^.\n]{0,60}(?:Card|Card®)?)",
        r"([A-Z][A-Za-z0-9®™ &\-]{3,40} Card)",
    )
    for pattern in patterns:
        match = re.search(pattern, segment)
        if match:
            name = re.sub(r"\s+", " ", match.group(1)).strip()
            if name and "ending" not in name.lower():
                return name[:80]
    return None


def _first_money(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            parsed = _parse_money_string(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _first_match(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return None


def extract_from_structured_payload(
    payload: Any,
    *,
    source: str = FieldSource.NETWORK.value,
    observed_at: str | None = None,
) -> AmexExtractionObservation | None:
    """Parse a captured/authenticated JSON payload into an intermediate observation."""
    if not isinstance(payload, (dict, list)):
        return None
    observed = observed_at or utc_now_iso()
    cards: list[AmexCardObservation] = []
    rewards_balance: str | None = None

    def walk(node: Any) -> None:
        nonlocal rewards_balance
        if isinstance(node, dict):
            keys = {str(k).lower(): k for k in node.keys()}
            # Rewards
            for candidate in (
                "membershiprewardspoints",
                "membership_rewards_points",
                "rewardspoints",
                "rewards_balance",
                "pointsbalance",
                "points_balance",
            ):
                if candidate in keys and rewards_balance is None:
                    rewards_balance = _parse_points(str(node[keys[candidate]]))
            # Card-like object
            last_four = None
            for candidate in ("lastfour", "last_four", "cardending", "card_ending", "accountnumber"):
                if candidate in keys:
                    last_four = mask_account_number(str(node[keys[candidate]]))
                    break
            product = None
            for candidate in ("productname", "product_name", "cardproduct", "accountname", "name"):
                if candidate in keys and isinstance(node[keys[candidate]], str):
                    product = str(node[keys[candidate]]).strip()[:80] or None
                    break
            issued = None
            for candidate in ("accounttoken", "account_token", "accountid", "account_id", "key"):
                if candidate in keys and node[keys[candidate]] not in (None, ""):
                    issued = str(node[keys[candidate]])
                    break

            def money_key(*names: str) -> str | None:
                for name in names:
                    if name in keys:
                        return _parse_money_string(str(node[keys[name]]))
                return None

            current = money_key(
                "currentbalance",
                "current_balance",
                "statementbalance",
                "statement_balance",
                "balance",
            )
            available = money_key("availablecredit", "available_credit", "creditavailable")
            due_amt = money_key(
                "paymentdue",
                "payment_due",
                "minimumpaymentdue",
                "minimum_payment_due",
                "amountdue",
                "amount_due",
            )
            due_date = None
            for candidate in (
                "paymentduedate",
                "payment_due_date",
                "duedate",
                "due_date",
            ):
                if candidate in keys:
                    due_date = parse_due_date(str(node[keys[candidate]]))
                    break

            if last_four or current or available or due_amt or due_date:
                cards.append(
                    AmexCardObservation(
                        last_four=last_four,
                        product_name=product,
                        display_name=product or (f"Card ending {last_four}" if last_four else "Amex Card"),
                        provider_issued_id=issued,
                        current_balance=current,
                        available_credit=available,
                        payment_due_amount=due_amt,
                        payment_due_date=due_date,
                    )
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if not cards and not rewards_balance:
        return None

    observation = AmexExtractionObservation(
        observed_at=observed,
        extraction_method=source,
        cards=cards[:8],
        rewards=(
            AmexRewardsObservation(balance=rewards_balance)
            if rewards_balance
            else None
        ),
        method_counts={source: 1},
    )
    _finalize_field_observations(observation, source=source)
    return observation


def extract_from_dom_text(
    body_text: str,
    *,
    surface_url: str | None = None,
    observed_at: str | None = None,
) -> AmexExtractionObservation:
    """DOM-fallback extraction from overview body text."""
    observed = observed_at or utc_now_iso()
    source = FieldSource.DOM_FALLBACK.value
    rewards_value = extract_membership_rewards_from_text(body_text or "")
    cards = extract_cards_from_text(body_text or "")
    observation = AmexExtractionObservation(
        observed_at=observed,
        surface_url=sanitize_url(surface_url),
        extraction_method=source,
        cards=cards,
        rewards=(
            AmexRewardsObservation(balance=rewards_value) if rewards_value else None
        ),
        method_counts={source: 1},
    )
    if body_text and len(body_text.strip()) < 40:
        observation.warnings.append("overview_partially_loaded")
    _finalize_field_observations(observation, source=source)
    return observation


def _finalize_field_observations(
    observation: AmexExtractionObservation,
    *,
    source: str,
) -> None:
    observed = observation.observed_at
    fields: list[AmexFieldObservation] = []

    # Rewards
    if observation.rewards and observation.rewards.balance:
        fields.append(
            _obs(
                "rewards_balance",
                status=FieldStatus.SUCCESS.value,
                source=source,
                value=observation.rewards.balance,
                confidence=FieldConfidence.HIGH.value
                if source != FieldSource.DOM_FALLBACK.value
                else FieldConfidence.MEDIUM.value,
                observed_at=observed,
            )
        )
    else:
        fields.append(
            _obs(
                "rewards_balance",
                status=FieldStatus.UNAVAILABLE.value,
                source=source,
                reason="rewards_balance_unavailable",
                confidence=FieldConfidence.LOW.value,
                observed_at=observed,
            )
        )
        observation.warnings.append("rewards_balance_unavailable")

    if not observation.cards:
        for name in (
            "current_balance",
            "available_credit",
            "payment_due_amount",
            "payment_due_date",
            "last_four",
        ):
            fields.append(
                _obs(
                    name,
                    status=FieldStatus.UNAVAILABLE.value,
                    source=source,
                    reason="no_card_accounts_observed",
                    confidence=FieldConfidence.LOW.value,
                    observed_at=observed,
                )
            )
        observation.warnings.append("no_card_accounts_observed")
    else:
        for card in observation.cards:
            ref = card.opaque_id()
            fields.append(
                _obs(
                    "last_four",
                    status=FieldStatus.SUCCESS.value
                    if card.last_four
                    else FieldStatus.UNAVAILABLE.value,
                    source=source,
                    value=card.last_four,
                    reason=None if card.last_four else "last_four_unavailable",
                    account_ref=ref,
                    confidence=FieldConfidence.HIGH.value,
                    observed_at=observed,
                )
            )
            for field_name, value, unavailable_reason in (
                ("current_balance", card.current_balance, "current_balance_unavailable"),
                ("available_credit", card.available_credit, "available_credit_unavailable"),
                ("payment_due_amount", card.payment_due_amount, "payment_due_amount_unavailable"),
                ("payment_due_date", card.payment_due_date, "payment_due_date_unavailable"),
            ):
                if value is not None:
                    fields.append(
                        _obs(
                            field_name,
                            status=FieldStatus.SUCCESS.value,
                            source=source,
                            value=value,
                            account_ref=ref,
                            confidence=FieldConfidence.MEDIUM.value,
                            observed_at=observed,
                        )
                    )
                else:
                    fields.append(
                        _obs(
                            field_name,
                            status=FieldStatus.UNAVAILABLE.value,
                            source=source,
                            reason=unavailable_reason,
                            account_ref=ref,
                            confidence=FieldConfidence.LOW.value,
                            observed_at=observed,
                        )
                    )
                    if unavailable_reason not in observation.warnings:
                        observation.warnings.append(unavailable_reason)

    fields.append(
        _obs(
            "last_verified_timestamp",
            status=FieldStatus.SUCCESS.value,
            source=FieldSource.RUNTIME_API.value,
            value=observed,
            confidence=FieldConfidence.HIGH.value,
            observed_at=observed,
        )
    )

    observation.field_observations = fields
    observation.useful = bool(
        (observation.rewards and observation.rewards.balance) or observation.cards
    )


def merge_observations(
    primary: AmexExtractionObservation | None,
    fallback: AmexExtractionObservation | None,
) -> AmexExtractionObservation:
    """Prefer primary structured data; fill gaps from fallback."""
    if primary and primary.useful and not fallback:
        return primary
    if fallback and fallback.useful and not primary:
        return fallback
    if not primary and not fallback:
        empty = AmexExtractionObservation()
        _finalize_field_observations(empty, source=FieldSource.DOM_FALLBACK.value)
        return empty
    if primary is None:
        return fallback  # type: ignore[return-value]
    if fallback is None:
        return primary

    method_counts: dict[str, int] = {}
    for obs in (primary, fallback):
        for key, value in obs.method_counts.items():
            method_counts[key] = method_counts.get(key, 0) + int(value)

    cards = list(primary.cards) if primary.cards else list(fallback.cards)
    if primary.cards and fallback.cards:
        # Prefer primary; append fallback cards with new endings only.
        seen = {c.last_four for c in primary.cards if c.last_four}
        for card in fallback.cards:
            if card.last_four and card.last_four not in seen:
                cards.append(card)
                seen.add(card.last_four)

    rewards = primary.rewards if (primary.rewards and primary.rewards.balance) else fallback.rewards
    merged = AmexExtractionObservation(
        observed_at=primary.observed_at or fallback.observed_at,
        surface_url=primary.surface_url or fallback.surface_url,
        extraction_method=primary.extraction_method
        if primary.useful
        else fallback.extraction_method,
        cards=cards,
        rewards=rewards,
        warnings=[],
        method_counts=method_counts,
    )
    source = (
        FieldSource.NETWORK.value
        if method_counts.get(FieldSource.NETWORK.value)
        or method_counts.get(FieldSource.RUNTIME_API.value)
        else FieldSource.DOM_FALLBACK.value
    )
    if method_counts.get(FieldSource.NETWORK.value):
        source = FieldSource.NETWORK.value
    elif method_counts.get(FieldSource.RUNTIME_API.value):
        source = FieldSource.RUNTIME_API.value
    else:
        source = FieldSource.DOM_FALLBACK.value
    _finalize_field_observations(merged, source=source)
    # Preserve partial-load warnings from either side.
    for warning in list(primary.warnings) + list(fallback.warnings):
        if warning not in merged.warnings:
            merged.warnings.append(warning)
    return merged


def read_body_text_via_locator(page: Any) -> str:
    """Read body text without page.evaluate (Amex disables eval)."""
    try:
        return page.locator("body").inner_text(timeout=5_000) or ""
    except Exception:
        return ""


def try_authenticated_json(
    page: Any,
    url: str,
    *,
    request_fn: Callable[..., Any] | None = None,
) -> Any | None:
    """Fetch JSON via Playwright request context (cookie jar); never uses evaluate."""
    getter = request_fn
    if getter is None:
        try:
            getter = page.context.request.get
        except Exception:
            return None
    try:
        response = getter(
            url,
            headers={"Accept": "application/json"},
            max_redirects=0,
            timeout=15_000,
        )
    except Exception:
        return None
    status = getattr(response, "status", None)
    if status != 200:
        return None
    try:
        if hasattr(response, "json"):
            return response.json()
    except Exception:
        return None
    return None


def amex_observation_from_sanitized(
    payload: dict[str, Any] | None,
) -> AmexExtractionObservation | None:
    """Rebuild an intermediate observation from a sanitized extract payload."""
    if not isinstance(payload, dict):
        return None
    cards: list[AmexCardObservation] = []
    for raw in payload.get("cards") or []:
        if not isinstance(raw, dict):
            continue
        cards.append(
            AmexCardObservation(
                last_four=mask_account_number(raw.get("last_four")),
                product_name=raw.get("product_name"),
                display_name=raw.get("display_name"),
                provider_issued_id=None,
                current_balance=raw.get("current_balance"),
                available_credit=raw.get("available_credit"),
                payment_due_amount=raw.get("payment_due_amount"),
                payment_due_date=raw.get("payment_due_date"),
                currency=str(raw.get("currency") or "USD"),
            )
        )
    rewards = None
    raw_rewards = payload.get("rewards")
    if isinstance(raw_rewards, dict) and raw_rewards.get("balance"):
        rewards = AmexRewardsObservation(
            program_name=str(raw_rewards.get("program_name") or "Membership Rewards"),
            balance=str(raw_rewards.get("balance")),
            unit=str(raw_rewards.get("unit") or "points"),
        )
    observation = AmexExtractionObservation(
        provider=str(payload.get("provider") or "amex"),
        observed_at=str(payload.get("observed_at") or utc_now_iso()),
        surface_url=sanitize_url(payload.get("surface_url")),
        extraction_method=str(payload.get("extraction_method") or "none"),
        cards=cards,
        rewards=rewards,
        warnings=[str(w) for w in (payload.get("warnings") or []) if w],
        method_counts={
            str(k): int(v)
            for k, v in dict(payload.get("method_counts") or {}).items()
        },
    )
    source = observation.extraction_method or FieldSource.DOM_FALLBACK.value
    if source not in {
        FieldSource.NETWORK.value,
        FieldSource.RUNTIME_API.value,
        FieldSource.DOM_FALLBACK.value,
    }:
        source = FieldSource.DOM_FALLBACK.value
    _finalize_field_observations(observation, source=source)
    return observation


def extract_amex_overview(
    page: Any,
    *,
    captured_network: list[Any] | None = None,
    request_fn: Callable[..., Any] | None = None,
    prefer_dom: bool = False,
) -> AmexExtractionObservation:
    """
    Run the Amex extraction priority chain against an authenticated page.

    Does not navigate. Caller/runtime must ensure overview surface first.
    Never calls page.evaluate().
    """
    surface_url = sanitize_url(getattr(page, "url", None))
    network_obs: AmexExtractionObservation | None = None
    runtime_api_obs: AmexExtractionObservation | None = None
    dom_obs: AmexExtractionObservation | None = None

    # 1) Already-captured structured responses.
    for payload in captured_network or []:
        body = payload
        if isinstance(payload, dict) and "body" in payload:
            body = payload.get("body")
        candidate = extract_from_structured_payload(
            body,
            source=FieldSource.NETWORK.value,
        )
        if candidate and candidate.useful:
            network_obs = candidate
            network_obs.surface_url = surface_url
            break

    # 2) Authenticated request transport (unless DOM forced for tests).
    if not prefer_dom and network_obs is None:
        for url in AMEX_STRUCTURED_ENDPOINTS:
            payload = try_authenticated_json(page, url, request_fn=request_fn)
            if payload is None:
                continue
            candidate = extract_from_structured_payload(
                payload,
                source=FieldSource.RUNTIME_API.value,
            )
            if candidate and candidate.useful:
                runtime_api_obs = candidate
                runtime_api_obs.surface_url = surface_url
                break

    # 3) DOM fallback.
    body_text = read_body_text_via_locator(page)
    dom_obs = extract_from_dom_text(body_text, surface_url=surface_url)

    if network_obs and network_obs.useful:
        return merge_observations(network_obs, dom_obs)
    if runtime_api_obs and runtime_api_obs.useful:
        return merge_observations(runtime_api_obs, dom_obs)
    return dom_obs
