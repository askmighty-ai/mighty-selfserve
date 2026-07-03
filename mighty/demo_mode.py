"""
mighty.demo_mode
────────────────
Self-contained demo data for Mighty dashboard presentations.

Enable via:
  - DEMO_MODE=true environment variable (always on)
  - ?demo=1 on /dashboard (session toggle)
  - ?demo=0 to turn off session demo mode

Demo data is clearly labeled and never mixed with production account data.
"""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from mighty.daily_brief import BriefInsight, BriefItem, DailyBrief
from mighty.daily_brief_ui import build_executive_briefing, render_executive_briefing_hero
from mighty.decision_engine import Recommendation


def _he(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def is_demo_mode_enabled(request=None, session: dict | None = None) -> bool:
    """Return True when demo mode should be active."""
    if os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes", "on"):
        return True
    if session is not None and session.get("demo_mode"):
        return True
    if request is not None:
        demo_param = request.args.get("demo")
        if demo_param == "1":
            return True
        if demo_param == "0":
            return False
    return False


def set_demo_mode(session: dict, enabled: bool) -> None:
    if enabled:
        session["demo_mode"] = True
    else:
        session.pop("demo_mode", None)


def handle_demo_query_param(request, session: dict) -> bool | None:
    """Apply ?demo=1 or ?demo=0 to the session. Returns new state or None if unchanged."""
    demo_param = request.args.get("demo")
    if demo_param == "1":
        set_demo_mode(session, True)
        return True
    if demo_param == "0":
        set_demo_mode(session, False)
        return False
    return None


# ── Demo persona: Alex Chen ───────────────────────────────────────────────────
# Alex is planning a 10-day Tokyo trip (~6 weeks out). Mighty surfaces five
# connected accounts, three benefits expiring before departure, two fresh
# discoveries from sync, and savings paths that combine those perks.

_DEMO_FIRST_NAME = "Alex"
_DEMO_TRIP_DESTINATION = "Tokyo"
_DEMO_TRIP_DAYS = 45
_DEMO_MARRIOTT_CERT_DAYS = 14
_DEMO_AMEX_OFFER_DAYS = 5
_DEMO_DELTA_UPGRADE_DAYS = 21
_DEMO_CHASE_DINING_DAYS = 12

_DEMO_ACCOUNT_URLS = {
    "marriott": "https://www.marriott.com/loyalty/myAccount/default.mi",
    "amex": "https://www.americanexpress.com/en-us/account/",
    "delta": "https://www.delta.com/myprofile/",
    "chase": "https://secure.chase.com/web/auth/dashboard",
    "southwest": "https://www.southwest.com/loyalty/myaccount/",
}


@dataclass(frozen=True)
class DemoStory:
    """Single source of truth for the demo user's trip and benefit timeline."""

    first_name: str
    destination: str
    trip_date: date
    positioning_date: date
    marriott_cert_expiry: date
    amex_offer_expiry: date
    delta_upgrade_expiry: date
    chase_dining_end: date

    @property
    def trip_label(self) -> str:
        return f"{self.destination} · {self.trip_date.strftime('%b %d')}"

    @property
    def marriott_cert_days(self) -> int:
        return _DEMO_MARRIOTT_CERT_DAYS

    @property
    def amex_offer_days(self) -> int:
        return _DEMO_AMEX_OFFER_DAYS

    @property
    def delta_upgrade_days(self) -> int:
        return _DEMO_DELTA_UPGRADE_DAYS

    @property
    def chase_dining_days(self) -> int:
        return _DEMO_CHASE_DINING_DAYS

    @property
    def expiring_before_trip_count(self) -> int:
        return 3


def _story() -> DemoStory:
    today = date.today()
    trip_date = today + timedelta(days=_DEMO_TRIP_DAYS)
    return DemoStory(
        first_name=_DEMO_FIRST_NAME,
        destination=_DEMO_TRIP_DESTINATION,
        trip_date=trip_date,
        positioning_date=trip_date - timedelta(days=1),
        marriott_cert_expiry=today + timedelta(days=_DEMO_MARRIOTT_CERT_DAYS),
        amex_offer_expiry=today + timedelta(days=_DEMO_AMEX_OFFER_DAYS),
        delta_upgrade_expiry=today + timedelta(days=_DEMO_DELTA_UPGRADE_DAYS),
        chase_dining_end=today + timedelta(days=_DEMO_CHASE_DINING_DAYS),
    )


def get_demo_first_name() -> str:
    return _story().first_name


@dataclass(frozen=True)
class DemoAccount:
    source: str
    display_name: str
    category: str
    synced_ago: str
    status_tier: str | None
    hero_label: str
    hero_value: str
    secondary: list[tuple[str, str]]
    alert_label: str | None = None
    alert_value: str | None = None
    alert_level: str = "amber"
    badge_certs: int = 0
    badge_credits: int = 0


def _demo_accounts() -> list[DemoAccount]:
    s = _story()

    return [
        DemoAccount(
            source="marriott",
            display_name="Marriott Bonvoy",
            category="Travel",
            synced_ago="2h ago",
            status_tier="Platinum Elite",
            hero_label="Points Balance",
            hero_value="48,230",
            secondary=[
                ("Elite Status", "Platinum Elite"),
                ("Free Night Certificate", "1 certificate"),
                ("Certificate Expires", s.marriott_cert_expiry.strftime("%b %d, %Y")),
            ],
            alert_label="Free Night Certificate",
            alert_value=s.marriott_cert_expiry.strftime("%b %d, %Y"),
            alert_level="amber",
            badge_certs=1,
        ),
        DemoAccount(
            source="amex",
            display_name="American Express",
            category="Banking & Finance",
            synced_ago="4h ago",
            status_tier="Platinum",
            hero_label="Membership Rewards",
            hero_value="124,350",
            secondary=[
                ("Card", "Platinum"),
                ("Airline Fee Credit", "$200 remaining"),
                ("Hotel Credit", "$300 remaining"),
                ("Annual Fee", "$695"),
            ],
            alert_label="Amex Offer",
            alert_value=f"$40 dining · expires {s.amex_offer_expiry.strftime('%b %d')}",
            alert_level="red",
            badge_credits=2,
        ),
        DemoAccount(
            source="delta",
            display_name="Delta SkyMiles",
            category="Travel",
            synced_ago="6h ago",
            status_tier="Gold Medallion",
            hero_label="SkyMiles Balance",
            hero_value="87,450",
            secondary=[
                ("Medallion Status", "Gold Medallion"),
                ("Booked Flight", f"SFO → NRT · {s.trip_date.strftime('%b %d')}"),
                ("Regional Upgrade Certificate", "1 available"),
                ("Valid Through", s.delta_upgrade_expiry.strftime("%b %d, %Y")),
            ],
            alert_label="Regional Upgrade Certificate",
            alert_value=s.delta_upgrade_expiry.strftime("%b %d, %Y"),
            alert_level="amber",
            badge_certs=1,
        ),
        DemoAccount(
            source="chase",
            display_name="Chase Sapphire Reserve",
            category="Banking & Finance",
            synced_ago="1h ago",
            status_tier=None,
            hero_label="Ultimate Rewards",
            hero_value="92,180",
            secondary=[
                ("Travel Credit", "$50 remaining"),
                ("5× Dining Multiplier", f"Ends {s.chase_dining_end.strftime('%b %d')}"),
                ("Annual Fee", "$550"),
            ],
            alert_label="5× Dining Multiplier",
            alert_value=s.chase_dining_end.strftime("%b %d, %Y"),
            alert_level="amber",
            badge_credits=1,
        ),
        DemoAccount(
            source="southwest",
            display_name="Southwest Rapid Rewards",
            category="Travel",
            synced_ago="3h ago",
            status_tier="Companion Pass",
            hero_label="Rapid Rewards Points",
            hero_value="45,200",
            secondary=[
                ("Companion Pass", "Active through Dec 2026"),
                (
                    "Positioning Flight",
                    f"AUS → SFO · {s.positioning_date.strftime('%b %d')} (companion fare)",
                ),
            ],
        ),
    ]


def get_demo_daily_brief() -> DailyBrief:
    s = _story()
    account_total = account_count()
    discovery_count = len(get_demo_recent_discoveries())

    return DailyBrief(
        headline=(
            f"{s.expiring_before_trip_count} benefits expire before your "
            f"{s.destination} trip — act this week."
        ),
        summary=(
            f"Checked {account_total} accounts · "
            f"{s.expiring_before_trip_count} expiring soon · "
            f"{discovery_count} new discoveries since yesterday."
        ),
        attention=[
            BriefItem(
                title="Amex $40 dining offer expires soon",
                detail=f"Platinum · {s.amex_offer_days} days left",
                tone="attention",
            ),
            BriefItem(
                title="Chase 5× dining multiplier ending soon",
                detail=f"Sapphire Reserve · {s.chase_dining_days} days left",
                tone="attention",
            ),
        ],
        discoveries=[
            BriefItem(
                title="Delta regional upgrade certificate",
                detail="SkyMiles · Found during last sync",
                tone="discovery",
            ),
            BriefItem(
                title="Marriott free night certificate",
                detail="Bonvoy · Synced from account page",
                tone="discovery",
            ),
        ],
        recommendations=[
            BriefItem(
                title="Book Park Hyatt Tokyo via Amex FHR",
                detail="Platinum · Breakfast + $300 hotel credit",
                tone="neutral",
            ),
            BriefItem(
                title="Transfer 60K Chase UR to Hyatt",
                detail="Sapphire Reserve · Covers 2 nights in Tokyo",
                tone="neutral",
            ),
            BriefItem(
                title="Apply Delta upgrade cert on SFO→NRT",
                detail=f"SkyMiles · Flight on {s.trip_date.strftime('%b %d')}",
                tone="neutral",
            ),
        ],
        completed=[
            BriefItem(
                title="Southwest Companion Pass confirmed active",
                detail=(
                    f"Rapid Rewards · Companion fare ready for "
                    f"{s.positioning_date.strftime('%b %d')} positioning flight"
                ),
                tone="completed",
            ),
        ],
        insights=[
            BriefInsight(
                title="Marriott free night expires before Tokyo",
                detail=f"Bonvoy · Expires in {s.marriott_cert_days} days",
                severity="warning",
            ),
            BriefInsight(
                title="Amex $40 dining offer expires soon",
                detail=f"Platinum · Expires in {s.amex_offer_days} days",
                severity="warning",
            ),
            BriefInsight(
                title="Delta upgrade cert expires before departure",
                detail=f"SkyMiles · Expires in {s.delta_upgrade_days} days",
                severity="warning",
            ),
            BriefInsight(
                title="Stack Hyatt points with Marriott free night",
                detail="Sapphire Reserve · 60K UR transfer + 1 free night = 3 nights",
                severity="opportunity",
            ),
            BriefInsight(
                title="$550 in unused Amex credits this year",
                detail="Platinum · $200 airline + $300 hotel credit remaining",
                severity="opportunity",
            ),
        ],
    )


def get_demo_recommendations() -> list[Recommendation]:
    s = _story()
    trip_str = s.trip_date.strftime("%b %d")

    return [
        Recommendation(
            title="Book Park Hyatt Tokyo via Amex Travel",
            summary=(
                "Your Platinum Fine Hotels + Resorts benefits include breakfast, "
                "upgrades, and late checkout — plus $300 hotel credit to apply."
            ),
            rationale=(
                f"Matches your {s.destination} trip ({trip_str}) and unused "
                "Amex hotel credit."
            ),
            evidence=[
                "Platinum card with Fine Hotels + Resorts eligibility",
                f"{s.destination} trip on {trip_str} in connected account data",
                "$300 Amex hotel credit remaining this year",
            ],
            why_now=(
                f"Your {s.destination} trip is {trip_str} — book now to apply "
                "FHR benefits and the hotel credit before rates rise."
            ),
            alternative_options=[
                "Transfer Chase UR to Hyatt and book with points",
                "Book direct with Hyatt and use elite status benefits",
            ],
            recommendation_type="hotel",
            confidence="high",
            bullets=[
                "Fine Hotels + Resorts eligible property",
                "Apply $300 hotel credit before booking",
                "Combine with Marriott free night for a longer stay",
            ],
            action_label="Open Amex Travel",
            action_url="https://www.americanexpress.com/travel/",
        ),
        Recommendation(
            title="Transfer 60K Chase UR to Hyatt",
            summary=(
                "World of Hyatt points deliver strong value at Category 5–6 "
                "properties in Tokyo."
            ),
            rationale=(
                f"You have 92K Ultimate Rewards and a {s.destination} trip on "
                f"{trip_str}."
            ),
            evidence=[
                "92K Ultimate Rewards balance in connected Chase account",
                f"{s.destination} trip on {trip_str}",
                "Park Hyatt Tokyo ~30K/night off-peak award rate",
            ],
            why_now=(
                f"Transfer before {trip_str} to lock in award availability at "
                "Park Hyatt Tokyo."
            ),
            alternative_options=[
                "Book via Amex Travel with FHR and pay cash",
                "Use Marriott free night certificate instead",
            ],
            recommendation_type="hotel",
            confidence="high",
            bullets=[
                "1:1 transfer from Chase Ultimate Rewards",
                "Park Hyatt Tokyo ~30K/night off-peak",
                "Stack with Marriott free night for 3 nights covered",
            ],
            action_label="Transfer to Hyatt",
            action_url="https://www.hyatt.com/",
        ),
        Recommendation(
            title="Apply Delta upgrade cert on SFO→NRT",
            summary=(
                "Your regional upgrade certificate expires before departure — "
                "use it on the long-haul segment."
            ),
            rationale=(
                f"Certificate expires in {s.delta_upgrade_days} days; "
                f"your {s.destination} flight is {trip_str}."
            ),
            evidence=[
                "Regional upgrade certificate available on Delta account",
                f"Certificate expires in {s.delta_upgrade_days} days",
                f"SFO→NRT flight on {trip_str}",
            ],
            why_now=(
                f"Certificate expires in {s.delta_upgrade_days} days — apply "
                "before your {trip_str} departure."
            ),
            alternative_options=[
                "Rely on Gold Medallion complimentary upgrade eligibility",
                "Save the certificate for a future long-haul trip",
            ],
            recommendation_type="travel",
            confidence="medium",
            bullets=[
                "Regional upgrade valid on SFO→NRT",
                "Apply at booking or via My Trips",
                "Gold Medallion complimentary upgrades may stack",
            ],
            action_label="View Delta Trips",
            action_url="https://www.delta.com/myprofile/",
        ),
    ]


def get_demo_hero_candidates() -> list[tuple]:
    """Return hero candidate tuples: (priority, exp_sort, display, label, value, exp_days, btype)."""
    s = _story()
    return [
        (95, s.marriott_cert_days, "Marriott Bonvoy", "Free Night Certificate", "1 certificate", s.marriott_cert_days, "certificate"),
        (88, s.amex_offer_days, "American Express", "Amex Offer", "$40 dining credit", s.amex_offer_days, "cash_credit"),
        (82, s.delta_upgrade_days, "Delta SkyMiles", "Regional Upgrade Certificate", "1 available", s.delta_upgrade_days, "certificate"),
        (70, s.chase_dining_days, "Chase Sapphire Reserve", "5× Dining Multiplier", "Active", s.chase_dining_days, "travel_credit"),
        (60, 9999, "Chase Sapphire Reserve", "Travel Credit", "$50 remaining", None, "travel_credit"),
    ]


def get_demo_value_items() -> list[tuple]:
    """Return value_items tuples: (display, label, value, relevance, method, btype)."""
    return [
        ("Marriott Bonvoy", "Free Night Certificate", "1 certificate", 0.9, "sync", "certificate"),
        ("Marriott Bonvoy", "Points Balance", "48,230", 0.5, "sync", "points_balance"),
        ("Marriott Bonvoy", "Elite Status", "Platinum Elite", 0.4, "sync", "elite_status"),
        ("American Express", "Airline Fee Credit", "$200 remaining", 0.85, "sync", "travel_credit"),
        ("American Express", "Hotel Credit", "$300 remaining", 0.85, "sync", "travel_credit"),
        ("American Express", "Membership Rewards", "124,350", 0.3, "sync", "points_balance"),
        ("Delta SkyMiles", "Regional Upgrade Certificate", "1 available", 0.88, "sync", "certificate"),
        ("Delta SkyMiles", "SkyMiles Balance", "87,450", 0.3, "sync", "points_balance"),
        ("Delta SkyMiles", "Medallion Status", "Gold Medallion", 0.4, "sync", "elite_status"),
        ("Chase Sapphire Reserve", "Ultimate Rewards", "92,180", 0.3, "sync", "points_balance"),
        ("Chase Sapphire Reserve", "Travel Credit", "$50 remaining", 0.7, "sync", "travel_credit"),
        ("Southwest Rapid Rewards", "Companion Pass", "Active through Dec 2026", 0.75, "sync", "partner_benefit"),
        ("Southwest Rapid Rewards", "Rapid Rewards Points", "45,200", 0.3, "sync", "points_balance"),
    ]


def get_demo_action_items() -> list[dict]:
    s = _story()
    cert_expiry = s.marriott_cert_expiry.strftime("%b %d, %Y")
    offer_expiry = s.amex_offer_expiry.strftime("%b %d, %Y")
    return [
        {
            "id": None,
            "source": "marriott",
            "label": "Free Night Certificate",
            "value": cert_expiry,
            "btype": "certificate",
            "urgency": "soon",
            "days_left": s.marriott_cert_days,
            "exp_date": cert_expiry,
        },
        {
            "id": None,
            "source": "amex",
            "label": "Amex Offer",
            "value": offer_expiry,
            "btype": "cash_credit",
            "urgency": "urgent",
            "days_left": s.amex_offer_days,
            "exp_date": offer_expiry,
        },
    ]


def get_demo_reminders() -> list[dict]:
    s = _story()

    return [
        {
            "source": "amex",
            "account_name": "American Express",
            "label": "Amex Offer",
            "value": f"Expires {s.amex_offer_expiry.strftime('%b %d, %Y')}",
            "message": f"Amex Offer: Expires {s.amex_offer_expiry.strftime('%b %d, %Y')}",
            "urgency": "urgent",
            "days_left": s.amex_offer_days,
            "expires_on": s.amex_offer_expiry.isoformat(),
        },
        {
            "source": "marriott",
            "account_name": "Marriott Bonvoy",
            "label": "Free Night Certificate",
            "value": s.marriott_cert_expiry.strftime("%b %d, %Y"),
            "message": f"Free Night Certificate: {s.marriott_cert_expiry.strftime('%b %d, %Y')}",
            "urgency": "soon",
            "days_left": s.marriott_cert_days,
            "expires_on": s.marriott_cert_expiry.isoformat(),
        },
        {
            "source": "delta",
            "account_name": "Delta SkyMiles",
            "label": "Regional Upgrade Certificate",
            "value": s.delta_upgrade_expiry.strftime("%b %d, %Y"),
            "message": f"Regional Upgrade Certificate: {s.delta_upgrade_expiry.strftime('%b %d, %Y')}",
            "urgency": "soon",
            "days_left": s.delta_upgrade_days,
            "expires_on": s.delta_upgrade_expiry.isoformat(),
        },
        {
            "source": "amex",
            "account_name": "American Express",
            "label": "Airline Fee Credit",
            "value": "$200 remaining",
            "message": "Airline Fee Credit: $200 remaining",
            "urgency": "info",
            "days_left": None,
            "expires_on": None,
        },
        {
            "source": "chase",
            "account_name": "Chase Sapphire Reserve",
            "label": "5× Dining Multiplier",
            "value": s.chase_dining_end.strftime("%b %d, %Y"),
            "message": f"5× Dining Multiplier: Ends {s.chase_dining_end.strftime('%b %d, %Y')}",
            "urgency": "soon",
            "days_left": s.chase_dining_days,
            "expires_on": s.chase_dining_end.isoformat(),
        },
    ]


def get_demo_change_alerts() -> list[dict]:
    return [
        {
            "type": "credit_added",
            "urgency": "info",
            "source": "delta",
            "account_name": "Delta SkyMiles",
            "label": "Regional Upgrade Certificate",
            "message": "Delta SkyMiles — Regional Upgrade Certificate found",
            "detail": "New certificate discovered during sync",
            "changed_at": (datetime.utcnow() - timedelta(hours=6)).isoformat(),
        },
        {
            "type": "credit_added",
            "urgency": "info",
            "source": "marriott",
            "account_name": "Marriott Bonvoy",
            "label": "Free Night Certificate",
            "message": "Marriott Bonvoy — Free Night Certificate found",
            "detail": "Certificate synced from account page",
            "changed_at": (datetime.utcnow() - timedelta(hours=2)).isoformat(),
        },
    ]


def get_demo_reminders_summary() -> dict:
    reminders = get_demo_reminders()
    change_alerts = get_demo_change_alerts()
    all_items = reminders + change_alerts

    themes = [
        {
            "theme": "expiring",
            "label": "Expiring benefits",
            "icon": "\U0001f4c5",
            "count": 3,
            "urgent_count": 1,
            "items": [r for r in reminders if r.get("expires_on")][:3],
        },
        {
            "theme": "unused",
            "label": "Unused credits",
            "icon": "\U0001f4a1",
            "count": 2,
            "urgent_count": 0,
            "items": [r for r in reminders if r.get("urgency") == "info"][:3],
        },
    ]

    return {
        "total": len(all_items),
        "urgent": sum(1 for i in all_items if i.get("urgency") == "urgent"),
        "themes": themes,
    }


def get_demo_recent_discoveries() -> list[dict]:
    return [
        {
            "source": "delta",
            "display_name": "Delta SkyMiles",
            "label": "Regional Upgrade Certificate",
            "verb": "found",
            "ago": "6h ago",
        },
        {
            "source": "marriott",
            "display_name": "Marriott Bonvoy",
            "label": "Free Night Certificate",
            "verb": "found",
            "ago": "2h ago",
        },
        {
            "source": "chase",
            "display_name": "Chase Sapphire Reserve",
            "label": "5× Dining Multiplier",
            "verb": "updated",
            "ago": "1h ago",
        },
    ]


def expiring_count() -> int:
    return _story().expiring_before_trip_count


def account_count() -> int:
    return len(_demo_accounts())


def render_demo_banner() -> str:
    return (
        '<div class="demo-mode-banner" role="status">'
        '<div class="demo-mode-banner-inner">'
        '<span class="demo-mode-badge">Demo Mode</span>'
        '<span class="demo-mode-copy">Sample data for product demos — not your real accounts.</span>'
        '<a href="/dashboard?demo=0" class="demo-mode-exit">Exit demo</a>'
        '</div>'
        '</div>'
    )


def render_demo_account_cards(
    reg_domain: Callable[[str], str] | None = None,
) -> str:
    """Render connected account cards using demo data."""
    reg_domain = reg_domain or (lambda _url: "")
    accounts = _demo_accounts()

    by_category: dict[str, list[DemoAccount]] = {}
    for acct in accounts:
        by_category.setdefault(acct.category, []).append(acct)

    cards_html = ""
    for category, accts in by_category.items():
        grid_cards = ""
        for acct in accts:
            fav_domain = reg_domain(_DEMO_ACCOUNT_URLS.get(acct.source, ""))
            fav_html = (
                f'<div class="acct-favicon-wrap">'
                f'<img src="https://www.google.com/s2/favicons?domain={fav_domain}&sz=64"'
                f' class="acct-favicon" alt="" onerror="this.parentElement.style.display=\'none\'">'
                f'</div>'
            ) if fav_domain else ""

            status_html = ""
            if acct.status_tier:
                tier_lc = acct.status_tier.lower()
                if any(k in tier_lc for k in ("platinum", "gold", "companion")):
                    st_color, st_bg = "#5b21b6", "#ede9fe"
                else:
                    st_color, st_bg = "#1e40af", "#dbeafe"
                status_html = (
                    f'<div style="display:inline-block;margin-top:3px;font-size:11px;font-weight:600;'
                    f'color:{st_color};background:{st_bg};border-radius:8px;'
                    f'padding:2px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%">'
                    f'◆ {_he(acct.status_tier)}</div>'
                )

            badges_html = ""
            if acct.badge_certs:
                badges_html += (
                    f'<span style="font-size:10px;font-weight:600;color:#1d4ed8;background:#dbeafe;'
                    f'border-radius:10px;padding:2px 7px;white-space:nowrap">🎫 {acct.badge_certs}</span>'
                )
            if acct.badge_credits:
                badges_html += (
                    f'<span style="font-size:10px;font-weight:600;color:#047857;background:#d1fae5;'
                    f'border-radius:10px;padding:2px 7px;white-space:nowrap">💳 {acct.badge_credits}</span>'
                )
            if badges_html:
                badges_html = (
                    f'<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:3px">{badges_html}</div>'
                )

            sec_rows = ""
            for lbl, val in acct.secondary:
                sec_rows += (
                    f'<div class="sec-row">'
                    f'<span class="sec-lbl">{_he(lbl)}</span>'
                    f'<span class="sec-val" title="{_he(val)}">{_he(val)}</span>'
                    f'</div>'
                )
            sec_html = f'<div class="acct-secondary">{sec_rows}</div>' if sec_rows else ""

            alert_html = ""
            if acct.alert_label and acct.alert_value:
                cls = "acct-alert-red" if acct.alert_level == "red" else "acct-alert-amber"
                alert_html = (
                    f'<div class="acct-alert {cls}">'
                    f'<div>'
                    f'<div class="alert-lbl">{_he(acct.alert_label)}</div>'
                    f'<div class="alert-sub">{_he(acct.alert_value)}</div>'
                    f'</div>'
                    f'</div>'
                )

            grid_cards += (
                f'<div class="acct-card is-expiring" data-name="{_he(acct.display_name)}" data-sync-status="ok">'
                f'<div class="acct-card-header">'
                f'{fav_html}'
                f'<div style="flex:1;min-width:0">'
                f'<div class="acct-name">{_he(acct.display_name)}</div>'
                f'{status_html}'
                f'{badges_html}'
                f'<div class="acct-sync-time">Synced {acct.synced_ago} · <span style="color:#a78bfa">Demo</span></div>'
                f'</div>'
                f'<div class="acct-controls">'
                f'<div class="sync-status-dot" style="width:7px;height:7px;border-radius:50%;background:#30d158;flex-shrink:0" title="Demo data"></div>'
                f'</div>'
                f'</div>'
                f'<div class="acct-divider"></div>'
                f'<div class="acct-hero">'
                f'<div class="hero-val" title="{_he(acct.hero_value)}">{_he(acct.hero_value)}</div>'
                f'<div class="hero-lbl">{_he(acct.hero_label)}</div>'
                f'</div>'
                f'{sec_html}'
                f'{alert_html}'
                f'<div class="acct-footer"><span style="font-size:11px;color:#c0bab4">Demo account</span></div>'
                f'</div>'
            )

        cards_html += (
            f'<div class="cat-group">'
            f'<div class="cat-header">'
            f'<span class="cat-label">{_he(category)}</span>'
            f'<div class="cat-rule"></div>'
            f'</div>'
            f'<div class="card-grid">{grid_cards}</div>'
            f'</div>'
        )

    return cards_html


def render_demo_benefits_row(hero_candidates: list[tuple] | None = None) -> str:
    """Render 'Benefits available now' insight cards."""
    candidates = hero_candidates or get_demo_hero_candidates()
    name_to_src = {
        "Marriott Bonvoy": "marriott",
        "American Express": "amex",
        "Delta SkyMiles": "delta",
        "Chase Sapphire Reserve": "chase",
        "Southwest Rapid Rewards": "southwest",
    }

    cards_html = ""
    for _ip, _, disp, lbl, val, exp_days, btype in candidates[:3]:
        src_key = name_to_src.get(disp, "")
        domain = _DEMO_ACCOUNT_URLS.get(src_key, "").replace("https://", "").split("/")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        fav_html = (
            f'<div style="width:30px;height:30px;border-radius:7px;border:0.5px solid #e8e4de;'
            f'background:#f5f2ed;display:flex;align-items:center;justify-content:center;'
            f'flex-shrink:0;overflow:hidden">'
            f'<img src="https://www.google.com/s2/favicons?domain={domain}&sz=64"'
            f' style="width:20px;height:20px;object-fit:contain"'
            f' onerror="this.parentElement.style.display=\'none\'" alt="">'
            f'</div>'
        ) if domain else ""

        exp_txt = ""
        if exp_days is not None and exp_days >= 0:
            exp_date = (date.today() + timedelta(days=exp_days)).strftime("%b %d, %Y")
            exp_color = "#dc2626" if exp_days <= 14 else "#d97706"
            exp_txt = f'<div style="font-size:11px;color:{exp_color};margin-top:3px">exp {exp_date}</div>'

        bd = json.dumps({
            "label": lbl,
            "account": disp,
            "value": val,
            "icon": "🎫",
            "expDays": exp_days,
            "field_key": f"{disp}::{lbl}",
            "btype": btype,
            "corrected": False,
        }).replace("'", "&#39;")

        cards_html += (
            f'<div onclick="openBenefitDrawer(this)" data-benefit=\'{bd}\' '
            f'style="flex:1;min-width:0;border:0.5px solid #e8e4de;border-radius:8px;'
            f'padding:10px 12px;cursor:pointer;display:flex;gap:9px;align-items:flex-start;'
            f'transition:background 0.1s" '
            f'onmouseover="this.style.background=\'#f5f2ed\'" '
            f'onmouseout="this.style.background=\'\'">'
            f'{fav_html}'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:12px;font-weight:600;color:#1c1917;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis">{_he(lbl)}</div>'
            f'<div style="font-size:11px;color:#6b7280;margin-top:1px">{_he(disp)}</div>'
            f'{exp_txt}'
            f'</div>'
            f'</div>'
        )

    return (
        f'<div style="margin-bottom:16px">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">'
        f'<span style="font-size:13px;font-weight:600;color:#1c1917">Benefits available now</span>'
        f'</div>'
        f'<div style="display:flex;gap:8px">{cards_html}</div>'
        f'</div>'
    )


def render_demo_recommendations(recs: list[Recommendation] | None = None) -> str:
    recs = recs or get_demo_recommendations()
    if not recs:
        return ""

    rec_type_labels = {
        "hotel": "Hotels",
        "travel": "Flights",
        "flight": "Flights",
        "credit_card": "Credit Cards",
    }

    groups: dict[str, list[Recommendation]] = {}
    group_order: list[str] = []
    for rec in recs:
        rtype = (rec.recommendation_type or "general").strip().lower() or "general"
        if rtype not in groups:
            groups[rtype] = []
            group_order.append(rtype)
        groups[rtype].append(rec)

    groups_html = ""
    for rtype in group_order:
        cards_html = ""
        for rec in groups[rtype]:
            badge_html = ""
            if rec.confidence == "high":
                badge_html = (
                    '<span style="font-size:10px;font-weight:700;color:#059669;'
                    'background:rgba(5,150,105,0.1);border:0.5px solid rgba(5,150,105,0.25);'
                    'border-radius:20px;padding:2px 7px;text-transform:uppercase;'
                    'letter-spacing:.04em;flex-shrink:0;line-height:1.4">High</span>'
                )

            bullets_html = ""
            if rec.bullets:
                bullet_items = "".join(
                    f'<div style="display:flex;align-items:flex-start;gap:6px;margin:0 0 2px">'
                    f'<span style="color:#059669;font-size:11px;line-height:1.4;flex-shrink:0">&#10003;</span>'
                    f'<span style="font-size:11px;color:#4b5563;line-height:1.4">{_he(str(b).strip())}</span></div>'
                    for b in rec.bullets if str(b or "").strip()
                )
                if bullet_items:
                    bullets_html = (
                        f'<div style="margin:6px 0 0;display:flex;flex-direction:column;'
                        f'gap:1px">{bullet_items}</div>'
                    )

            cta_html = ""
            if rec.action_label and rec.action_url:
                cta_html = (
                    f'<div style="display:flex;justify-content:flex-end;margin-top:8px">'
                    f'<a href="{_he(rec.action_url)}" target="_blank" rel="noopener noreferrer" '
                    f'style="display:inline-block;font-size:12px;font-weight:600;'
                    f'color:#fff;background:#1c1917;border-radius:8px;padding:7px 14px;'
                    f'text-decoration:none;line-height:1">{_he(rec.action_label)}</a>'
                    f'</div>'
                )

            rationale_html = ""
            if rec.rationale:
                rationale_html = (
                    f'<div style="font-size:11px;color:#9ca3af;margin-top:3px;line-height:1.4">'
                    f'{_he(rec.rationale)}</div>'
                )

            cards_html += (
                f'<div style="background:#ffffff;border:1px solid rgba(0,0,0,0.08);'
                f'border-radius:16px;padding:12px 14px;'
                f'box-shadow:0 1px 3px rgba(0,0,0,0.06),0 4px 12px rgba(0,0,0,0.04)">'
                f'<div style="display:flex;align-items:center;justify-content:space-between;gap:10px">'
                f'<div style="font-size:16px;font-weight:700;color:#1c1917;line-height:1.3">'
                f'{_he(rec.title)}</div>'
                f'{badge_html}'
                f'</div>'
                f'<div style="font-size:12px;color:#6b7280;margin-top:4px;line-height:1.4">'
                f'{_he(rec.summary)}</div>'
                f'{rationale_html}'
                f'{bullets_html}'
                f'{cta_html}'
                f'</div>'
            )

        category_label = rec_type_labels.get(rtype, rtype.replace("_", " ").title())
        groups_html += (
            f'<div>'
            f'<div style="font-size:13px;font-weight:700;color:#1c1917;margin:0 0 8px">'
            f'{_he(category_label)}</div>'
            f'<div style="display:flex;flex-direction:column;gap:12px">{cards_html}</div>'
            f'</div>'
        )

    inner = (
        f'<div style="flex:1;min-width:0">'
        f'<div style="font-size:11px;font-weight:700;color:#9ca3af;margin:0 0 8px;'
        f'text-transform:uppercase;letter-spacing:.06em">Recommendations</div>'
        f'<div style="display:flex;flex-direction:column;gap:18px">{groups_html}</div>'
        f'</div>'
    )
    return (
        f'<div class="dash-section dash-recommendations-section">'
        f'<div class="dash-section-inner">{inner}</div>'
        f'</div>'
    )


def render_demo_recent_discoveries() -> str:
    lines = []
    for item in get_demo_recent_discoveries():
        line_text = f'{_he(item["display_name"])} — {_he(item["label"])} {item["verb"]}'
        lines.append(
            f'<div style="padding:6px 0;border-bottom:1px solid #f3f4f6;display:flex;'
            f'align-items:baseline;justify-content:space-between;gap:8px">'
            f'<div style="font-size:13px;color:#374151;line-height:1.4">{line_text}</div>'
            f'<div style="font-size:11px;color:#9ca3af;flex-shrink:0">{_he(item["ago"])}</div>'
            f'</div>'
        )

    return (
        f'<div style="margin-bottom:20px">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">'
        f'<span style="font-size:13px;font-weight:600;color:#1c1917">Recent discoveries</span>'
        f'<span style="font-size:10px;font-weight:600;color:#a78bfa;background:#f5f3ff;'
        f'border-radius:20px;padding:2px 8px">Demo</span>'
        f'</div>'
        f'{"".join(lines)}'
        f'</div>'
    )


def render_demo_daily_brief_hero(brief: DailyBrief, first_name: str, today_label: str) -> str:
    """Render the Daily Brief hero card from a DailyBrief object."""
    exec_brief = build_executive_briefing(
        brief,
        account_count=account_count(),
        benefit_count=len(get_demo_hero_candidates()),
        expiring_count=expiring_count(),
        use_demo_when_empty=True,
    )
    exec_brief.is_demo = True
    return render_executive_briefing_hero(
        exec_brief,
        first_name=first_name,
        today_label=today_label,
        escape=_he,
    )
