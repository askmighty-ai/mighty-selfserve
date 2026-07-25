"""Deterministic Future Preview scenario for Home OS (review-only).

Builds CanonicalModels for a fully-operational Mighty household — not a
random sample. Does not rank, project, or render; HomeProjection consumes
these inputs normally.

Available only via gated research entry. Production compose paths never call
this module.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from mighty.workitem.coverage import (
    AuthPosture,
    CoverageHealth,
    CoverageItem,
    CoverageStatus,
    VerificationState,
)
from mighty.workitem.model import (
    UrgencyBand,
    WorkItem,
    WorkItemAction,
    WorkItemEvidence,
    WorkItemPriority,
    WorkItemState,
    WorkItemType,
)
from mighty.workitem.proof import ProofItem
from mighty.workitem.projection_inputs import CanonicalModels

# Fixed projection clock — screenshots and reviews stay byte-stable.
FIXED_AS_OF = datetime(2026, 7, 25, 16, 0, 0, tzinfo=timezone.utc)

PERSONA_DISPLAY_NAME = "Jordan"
PERSONA_USER_ID = "home-os-future-preview-session"
OWNER_DOMAIN = "home_os.future_preview"
SIMULATION_MODE = "future_preview"
SIMULATION_TAG = "ephemeral_future_preview"

PreviewState = Literal["full", "attention", "opportunity", "all-clear"]


def normalize_preview_state(raw: str | None) -> PreviewState:
    """Map URL/query values onto a supported Future Preview state."""
    value = (raw or "full").strip().lower().replace("_", "-")
    if value in ("calm", "allclear", "all-clear", "healthy"):
        return "all-clear"
    if value in ("attention", "approval", "needs-user"):
        return "attention"
    if value in ("opportunity", "value", "value-waiting"):
        return "opportunity"
    if value == "full":
        return "full"
    return "full"


def preview_as_of() -> datetime:
    return FIXED_AS_OF


# ---------------------------------------------------------------------------
# Coverage — one person's monitored life (25 enrolled providers)
# ---------------------------------------------------------------------------

# (provider_id, display_name, capabilities, category hint for docs)
_PROVIDER_CATALOG: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # Travel
    ("marriott", "Marriott Bonvoy", ("loyalty", "capture")),
    ("hilton", "Hilton Honors", ("loyalty", "capture")),
    ("hyatt", "World of Hyatt", ("loyalty", "capture")),
    ("united", "United MileagePlus", ("loyalty", "capture")),
    ("delta", "Delta SkyMiles", ("loyalty", "capture")),
    ("alaska", "Alaska Mileage Plan", ("loyalty", "capture")),
    ("airbnb", "Airbnb", ("booking", "capture")),
    ("expedia", "Expedia", ("booking", "capture")),
    # Banking / cards
    ("amex", "American Express", ("card", "benefits", "capture")),
    ("chase", "Chase Sapphire", ("card", "benefits", "capture")),
    ("capital_one", "Capital One Venture", ("card", "capture")),
    ("bank_of_america", "Bank of America", ("banking", "capture")),
    ("fidelity", "Fidelity", ("brokerage", "capture")),
    ("venmo", "Venmo", ("payments", "capture")),
    # Subscriptions
    ("netflix", "Netflix", ("subscription", "capture")),
    ("spotify", "Spotify", ("subscription", "capture")),
    ("apple", "Apple", ("subscription", "capture")),
    ("adobe", "Adobe", ("subscription", "capture")),
    ("youtube", "YouTube Premium", ("subscription", "capture")),
    # Commerce / mobility
    ("amazon", "Amazon", ("commerce", "capture")),
    ("target", "Target", ("commerce", "capture")),
    ("costco", "Costco", ("commerce", "capture")),
    ("uber", "Uber", ("mobility", "capture")),
    ("lyft", "Lyft", ("mobility", "capture")),
    # Utilities / membership
    ("aaa", "AAA", ("membership", "capture")),
)


def build_coverage() -> tuple[CoverageItem, ...]:
    """25 enrolled providers — almost all healthy; a couple soft edges."""
    items: list[CoverageItem] = []
    for provider, display, caps in _PROVIDER_CATALOG:
        # Soft edges that still feel "operational" (not fires):
        # - adobe: pending re-verify after password rotation last week
        # - lyft: candidate Mighty noticed from card spend (not yet enrolled)
        if provider == "adobe":
            items.append(
                CoverageItem(
                    provider=provider,
                    status=CoverageStatus.ENROLLED,
                    health=CoverageHealth.DEGRADED,
                    capabilities=caps,
                    verification=VerificationState.PENDING,
                    discovery="manual",
                    authentication=AuthPosture.VALID,
                    monitoring="reverify_scheduled",
                    display_name=display,
                )
            )
            continue
        if provider == "lyft":
            items.append(
                CoverageItem(
                    provider=provider,
                    status=CoverageStatus.CANDIDATE,
                    health=CoverageHealth.UNKNOWN,
                    capabilities=caps,
                    verification=VerificationState.NEVER,
                    discovery="card_spend",
                    authentication=AuthPosture.UNKNOWN,
                    monitoring="awaiting_enrollment",
                    display_name=display,
                )
            )
            continue
        items.append(
            CoverageItem(
                provider=provider,
                status=CoverageStatus.ENROLLED,
                health=CoverageHealth.HEALTHY,
                capabilities=caps,
                verification=VerificationState.VERIFIED,
                discovery="manual",
                authentication=AuthPosture.VALID,
                monitoring="active",
                display_name=display,
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# Proof — historical outcomes across the last ~two weeks
# ---------------------------------------------------------------------------


def build_proof(*, as_of: datetime | None = None) -> tuple[ProofItem, ...]:
    clock = as_of or FIXED_AS_OF
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)

    specs: tuple[tuple[str, int, str, str, str, str], ...] = (
        # id_suffix, hours_ago, summary, provider, outcome_class, impact
        (
            "united_upgrade",
            18,
            "United · Complimentary upgrade confirmed (SFO→ORD)",
            "united",
            "benefit",
            "high",
        ),
        (
            "amex_dining",
            36,
            "Amex · $35 dining credit applied at State Bird",
            "amex",
            "benefit",
            "normal",
        ),
        (
            "marriott_points",
            52,
            "Marriott · 12,400 points posted from Chicago stay",
            "marriott",
            "loyalty",
            "normal",
        ),
        (
            "chase_portal",
            70,
            "Chase · Portal fare beat matched (−$84)",
            "chase",
            "savings",
            "high",
        ),
        (
            "amazon_price",
            96,
            "Amazon · Price drop claimed on noise-cancelling headphones",
            "amazon",
            "savings",
            "normal",
        ),
        (
            "delta_companion",
            120,
            "Delta · Companion Certificate hold placed for Thanksgiving",
            "delta",
            "benefit",
            "high",
        ),
        (
            "spotify_plan",
            148,
            "Spotify · Family plan seat reconciled",
            "spotify",
            "subscription",
            "low",
        ),
        (
            "hilton_folio",
            172,
            "Hilton · Folio charge corrected (−$47 resort fee)",
            "hilton",
            "savings",
            "normal",
        ),
        (
            "uber_receipt",
            200,
            "Uber · Duplicate airport ride refunded",
            "uber",
            "savings",
            "low",
        ),
        (
            "fidelity_dividend",
            240,
            "Fidelity · Dividend reinvestment confirmed",
            "fidelity",
            "banking",
            "low",
        ),
        (
            "apple_renewal",
            280,
            "Apple · iCloud+ renewal moved to annual (−$21/yr)",
            "apple",
            "subscription",
            "normal",
        ),
        (
            "aaa_roadside",
            320,
            "AAA · Roadside membership auto-renew verified",
            "aaa",
            "membership",
            "low",
        ),
    )

    items: list[ProofItem] = []
    for suffix, hours_ago, summary, provider, outcome_class, impact in specs:
        items.append(
            ProofItem(
                id=f"proof_future_{suffix}",
                outcome_at=clock - timedelta(hours=hours_ago),
                summary=summary,
                provider=provider,
                outcome_class=outcome_class,
                impact=impact,
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# Work Items — one Approval, three Opportunities, zero Interrupts (default)
# ---------------------------------------------------------------------------


def build_approval(*, as_of: datetime) -> WorkItem:
    return WorkItem(
        id="wi_approval_united_award",
        type=WorkItemType.APPROVAL,
        priority=WorkItemPriority.APPROVAL,
        title="Approve United award booking",
        summary=(
            "Mighty found saver-space award seats for your Chicago trip "
            "(SFO→ORD, Sep 12). 32,500 miles + $5.60 — well under the cash "
            "fare. Approve so Mighty can ticket before the inventory moves."
        ),
        evidence=WorkItemEvidence.from_mapping(
            {
                "provider": "united",
                "display_name": "United MileagePlus",
                "route": "SFO-ORD",
                "depart": "2026-09-12",
                "miles": "32500",
                "cash_taxes": "5.60",
                "cash_fare_usd": "418",
                "simulation": SIMULATION_MODE,
            }
        ),
        primary_action=WorkItemAction(
            key="approve_united_award",
            intent="Approve award booking",
        ),
        secondary_action=WorkItemAction(
            key="defer_united_award",
            intent="Not now",
        ),
        dismissible=False,
        deferrable=True,
        created_at=as_of - timedelta(hours=5),
        updated_at=as_of - timedelta(minutes=40),
        expires_at=as_of + timedelta(hours=36),
        proof_reference=None,
        provider="united",
        capability="booking",
        state=WorkItemState.EXPANDED,
        owner_domain=OWNER_DOMAIN,
        urgency_band=UrgencyBand.HIGH,
        effort_weight=15,
        confidence=0.93,
    )


def build_opportunities(*, as_of: datetime) -> tuple[WorkItem, ...]:
    amex = WorkItem(
        id="wi_opportunity_amex_hotel_credit",
        type=WorkItemType.OPPORTUNITY,
        priority=WorkItemPriority.OPPORTUNITY,
        title="Amex hotel credit still unused",
        summary=(
            "Your Platinum Fine Hotels + Resorts credit has $200 left this "
            "period. A qualifying Chicago property is already on your trip — "
            "book through Amex and Mighty will track the statement credit."
        ),
        evidence=WorkItemEvidence.from_mapping(
            {
                "provider": "amex",
                "display_name": "American Express",
                "credit_remaining_usd": "200",
                "period_ends": "2026-09-30",
                "simulation": SIMULATION_MODE,
            }
        ),
        primary_action=WorkItemAction(
            key="use_amex_hotel_credit",
            intent="Use hotel credit",
        ),
        secondary_action=WorkItemAction(
            key="defer_amex_hotel_credit",
            intent="Remind me later",
        ),
        dismissible=True,
        deferrable=True,
        created_at=as_of - timedelta(days=2, hours=3),
        updated_at=as_of - timedelta(hours=6),
        expires_at=as_of + timedelta(days=40),
        proof_reference=None,
        provider="amex",
        capability="benefits",
        state=WorkItemState.VISIBLE,
        owner_domain=OWNER_DOMAIN,
        urgency_band=UrgencyBand.NORMAL,
        effort_weight=25,
        confidence=0.9,
    )

    marriott = WorkItem(
        id="wi_opportunity_marriott_free_night",
        type=WorkItemType.OPPORTUNITY,
        priority=WorkItemPriority.OPPORTUNITY,
        title="Marriott free night expires Oct 15",
        summary=(
            "Your Bonvoy Free Night Award (up to 50,000 points) expires "
            "October 15. Chicago dates on your calendar still have award "
            "availability — claim it before it slips."
        ),
        evidence=WorkItemEvidence.from_mapping(
            {
                "provider": "marriott",
                "display_name": "Marriott Bonvoy",
                "award": "free_night_50k",
                "expires": "2026-10-15",
                "simulation": SIMULATION_MODE,
            }
        ),
        primary_action=WorkItemAction(
            key="claim_marriott_free_night",
            intent="Claim free night",
        ),
        secondary_action=WorkItemAction(
            key="defer_marriott_free_night",
            intent="Not this trip",
        ),
        dismissible=True,
        deferrable=True,
        created_at=as_of - timedelta(days=4),
        updated_at=as_of - timedelta(days=1),
        expires_at=as_of + timedelta(days=82),
        proof_reference=None,
        provider="marriott",
        capability="loyalty",
        state=WorkItemState.VISIBLE,
        owner_domain=OWNER_DOMAIN,
        urgency_band=UrgencyBand.NORMAL,
        effort_weight=30,
        confidence=0.88,
    )

    chase = WorkItem(
        id="wi_opportunity_chase_transfer_bonus",
        type=WorkItemType.OPPORTUNITY,
        priority=WorkItemPriority.OPPORTUNITY,
        title="Chase → United transfer bonus live",
        summary=(
            "Ultimate Rewards is offering 30% transfer bonus to United through "
            "August 8. Moving 40,000 points now yields 52,000 miles — enough "
            "for the award seats Mighty already found."
        ),
        evidence=WorkItemEvidence.from_mapping(
            {
                "provider": "chase",
                "display_name": "Chase Sapphire",
                "bonus_pct": "30",
                "partner": "united",
                "window_ends": "2026-08-08",
                "simulation": SIMULATION_MODE,
            }
        ),
        primary_action=WorkItemAction(
            key="transfer_chase_united",
            intent="Transfer with bonus",
        ),
        secondary_action=WorkItemAction(
            key="defer_chase_transfer",
            intent="Skip bonus",
        ),
        dismissible=True,
        deferrable=True,
        created_at=as_of - timedelta(days=1, hours=8),
        updated_at=as_of - timedelta(hours=2),
        expires_at=as_of + timedelta(days=14),
        proof_reference=None,
        provider="chase",
        capability="benefits",
        state=WorkItemState.VISIBLE,
        owner_domain=OWNER_DOMAIN,
        urgency_band=UrgencyBand.HIGH,
        effort_weight=20,
        confidence=0.91,
    )
    return (amex, marriott, chase)


def build_optional_interrupt(*, as_of: datetime) -> WorkItem:
    """Soft Interrupt kept available for attention reviews (not in default full)."""
    return WorkItem(
        id="wi_interrupt_hilton_session",
        type=WorkItemType.INTERRUPT,
        priority=WorkItemPriority.INTERRUPT,
        title="Hilton needs a quick sign-in",
        summary=(
            "Mighty lost its Hilton session overnight — likely a routine "
            "logout. Sign in once so folio watching and elite-night tracking "
            "can continue for your Chicago stay."
        ),
        evidence=WorkItemEvidence.from_mapping(
            {
                "provider": "hilton",
                "display_name": "Hilton Honors",
                "reason": "session_expired",
                "simulation": SIMULATION_MODE,
            }
        ),
        primary_action=WorkItemAction(
            key="start_hilton_signin",
            intent="Sign in to Hilton",
        ),
        secondary_action=WorkItemAction(
            key="defer_hilton_signin",
            intent="Not now",
        ),
        dismissible=False,
        deferrable=True,
        created_at=as_of - timedelta(hours=9),
        updated_at=as_of - timedelta(hours=1),
        expires_at=as_of + timedelta(days=5),
        proof_reference=None,
        provider="hilton",
        capability="session",
        state=WorkItemState.EXPANDED,
        owner_domain=OWNER_DOMAIN,
        urgency_band=UrgencyBand.HARD,
        effort_weight=18,
        confidence=0.94,
    )


def build_work_items(
    *,
    as_of: datetime | None = None,
    state: PreviewState = "full",
    include_interrupt: bool = False,
) -> tuple[WorkItem, ...]:
    clock = as_of or FIXED_AS_OF
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)

    approval = build_approval(as_of=clock)
    opportunities = build_opportunities(as_of=clock)
    interrupt = build_optional_interrupt(as_of=clock)

    if state == "all-clear":
        return ()
    if state == "opportunity":
        # Lead with the highest-urgency opportunity (Chase transfer).
        chase = next(o for o in opportunities if o.id.endswith("chase_transfer_bonus"))
        rest = tuple(o for o in opportunities if o.id != chase.id)
        return (chase.with_updates(state=WorkItemState.EXPANDED),) + rest
    if state == "attention":
        if include_interrupt:
            return (interrupt, approval) + opportunities
        return (approval,) + opportunities
    # full — operational house: approval + opportunities, zero interrupts
    if include_interrupt:
        return (interrupt, approval) + opportunities
    return (approval,) + opportunities


def initial_canonical_models(
    *,
    as_of: datetime | None = None,
    state: str | None = "full",
    include_interrupt: bool = False,
) -> CanonicalModels:
    """Canonical snapshot for Future Preview research sessions."""
    clock = as_of or FIXED_AS_OF
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    preview_state = normalize_preview_state(state)
    return CanonicalModels(
        work_items=build_work_items(
            as_of=clock,
            state=preview_state,
            include_interrupt=include_interrupt,
        ),
        coverage=build_coverage(),
        proof=build_proof(as_of=clock),
    )


def provider_count() -> int:
    return len(_PROVIDER_CATALOG)
