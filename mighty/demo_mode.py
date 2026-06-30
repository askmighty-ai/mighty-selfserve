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


# ── Coherent story: planning a spring trip to Tokyo ──────────────────────────
# Alex tracks loyalty accounts across airlines, hotels, and cards. Three perks
# expire before the trip; recommendations tie directly to those accounts.

_DEMO_ACCOUNT_URLS = {
    "marriott": "https://www.marriott.com/loyalty/myAccount/default.mi",
    "amex": "https://www.americanexpress.com/en-us/account/",
    "delta": "https://www.delta.com/myprofile/",
    "chase": "https://secure.chase.com/web/auth/dashboard",
    "southwest": "https://www.southwest.com/loyalty/myaccount/",
}


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
    trip_date = date.today() + timedelta(days=45)
    cert_expiry = date.today() + timedelta(days=14)
    offer_expiry = date.today() + timedelta(days=5)
    upgrade_expiry = date.today() + timedelta(days=21)
    dining_end = date.today() + timedelta(days=12)

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
                ("Certificate Expires", cert_expiry.strftime("%b %d, %Y")),
            ],
            alert_label="Free Night Certificate",
            alert_value=cert_expiry.strftime("%b %d, %Y"),
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
            alert_value=f"${offer_expiry.strftime('%b %d')}",
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
                ("Regional Upgrade Certificate", "1 available"),
                ("Valid Through", upgrade_expiry.strftime("%b %d, %Y")),
            ],
            alert_label="Regional Upgrade Certificate",
            alert_value=upgrade_expiry.strftime("%b %d, %Y"),
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
                ("5× Dining Multiplier", f"Ends {dining_end.strftime('%b %d')}"),
                ("Annual Fee", "$550"),
            ],
            alert_label="5× Dining Multiplier",
            alert_value=dining_end.strftime("%b %d, %Y"),
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
                ("Upcoming Trip", f"Tokyo · {trip_date.strftime('%b %d')}"),
            ],
        ),
    ]


def get_demo_daily_brief() -> DailyBrief:
    cert_days = 14
    offer_days = 5
    upgrade_days = 21

    return DailyBrief(
        headline="3 benefits expire before your Tokyo trip — act this week.",
        summary=(
            "Checked 5 demo accounts · 3 items need you · "
            "2 new discoveries since yesterday."
        ),
        attention=[
            BriefItem(
                title="Chase 5× dining multiplier ending soon",
                detail=f"Sapphire Reserve · Ends in {cert_days - 2} days",
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
                detail="Platinum · Breakfast + late checkout included",
                tone="neutral",
            ),
            BriefItem(
                title="Transfer 60K Chase UR to Hyatt",
                detail="Sapphire Reserve · Strong value for 2 nights in Tokyo",
                tone="neutral",
            ),
        ],
        completed=[
            BriefItem(
                title="Southwest Companion Pass confirmed active",
                detail="Rapid Rewards · Valid through Dec 2026",
                tone="completed",
            ),
        ],
        insights=[
            BriefInsight(
                title="Marriott free night expires soon",
                detail=f"Bonvoy · Expires in {cert_days} days",
                severity="warning",
            ),
            BriefInsight(
                title="Amex $40 dining offer expires Friday",
                detail=f"Platinum · Expires in {offer_days} days",
                severity="warning",
            ),
            BriefInsight(
                title="Delta upgrade cert valid through next month",
                detail=f"SkyMiles · Expires in {upgrade_days} days",
                severity="warning",
            ),
            BriefInsight(
                title="Strong Hyatt redemption via Chase transfer",
                detail="Sapphire Reserve · 1:1 transfer to World of Hyatt",
                severity="opportunity",
            ),
        ],
    )


def get_demo_recommendations() -> list[Recommendation]:
    return [
        Recommendation(
            title="Book Park Hyatt Tokyo via Amex Travel",
            summary="Your Platinum Fine Hotels + Resorts benefits include breakfast, upgrades, and late checkout.",
            rationale="Demo · Matches your Tokyo trip dates and unused hotel credit.",
            recommendation_type="hotel",
            confidence="high",
            bullets=[
                "Fine Hotels + Resorts eligible property",
                "Potential suite upgrade at check-in",
                "Apply $300 hotel credit before booking",
            ],
            action_label="Open Amex Travel",
            action_url="https://www.americanexpress.com/travel/",
        ),
        Recommendation(
            title="Transfer 60K Chase UR to Hyatt",
            summary="World of Hyatt points deliver strong value at Category 5–6 properties in Tokyo.",
            rationale="Demo · You have 92K Ultimate Rewards and a Tokyo trip planned.",
            recommendation_type="hotel",
            confidence="high",
            bullets=[
                "1:1 transfer from Chase Ultimate Rewards",
                "Park Hyatt Tokyo ~30K/night off-peak",
                "Combine with Marriott free night for 3 nights covered",
            ],
            action_label="Transfer to Hyatt",
            action_url="https://www.hyatt.com/",
        ),
        Recommendation(
            title="Apply Delta upgrade cert on SFO→NRT",
            summary="Your regional upgrade certificate expires before your trip — use it on the long-haul segment.",
            rationale="Demo · Certificate expires in 21 days; Tokyo flight is in 45 days.",
            recommendation_type="travel",
            confidence="medium",
            bullets=[
                "Regional upgrade valid on select international routes",
                "Apply at booking or via My Trips",
                "Gold Medallion complimentary upgrades may stack",
            ],
            action_label="View Delta Trips",
            action_url="https://www.delta.com/myprofile/",
        ),
    ]


def get_demo_hero_candidates() -> list[tuple]:
    """Return hero candidate tuples: (priority, exp_sort, display, label, value, exp_days, btype)."""
    return [
        (95, 14, "Marriott Bonvoy", "Free Night Certificate", "1 certificate", 14, "certificate"),
        (88, 5, "American Express", "Amex Offer", "$40 dining credit", 5, "cash_credit"),
        (82, 21, "Delta SkyMiles", "Regional Upgrade Certificate", "1 available", 21, "certificate"),
        (70, 12, "Chase Sapphire Reserve", "5× Dining Multiplier", "Active", 12, "travel_credit"),
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
    cert_expiry = (date.today() + timedelta(days=14)).strftime("%b %d, %Y")
    offer_expiry = (date.today() + timedelta(days=5)).strftime("%b %d, %Y")
    return [
        {
            "id": None,
            "source": "marriott",
            "label": "Free Night Certificate",
            "value": cert_expiry,
            "btype": "certificate",
            "urgency": "soon",
            "days_left": 14,
            "exp_date": cert_expiry,
        },
        {
            "id": None,
            "source": "amex",
            "label": "Amex Offer",
            "value": offer_expiry,
            "btype": "cash_credit",
            "urgency": "urgent",
            "days_left": 5,
            "exp_date": offer_expiry,
        },
    ]


def get_demo_reminders() -> list[dict]:
    cert_expiry = date.today() + timedelta(days=14)
    offer_expiry = date.today() + timedelta(days=5)
    upgrade_expiry = date.today() + timedelta(days=21)
    dining_end = date.today() + timedelta(days=12)

    return [
        {
            "source": "amex",
            "account_name": "American Express",
            "label": "Amex Offer",
            "value": f"Expires {offer_expiry.strftime('%b %d, %Y')}",
            "message": f"Amex Offer: Expires {offer_expiry.strftime('%b %d, %Y')}",
            "urgency": "urgent",
            "days_left": 5,
            "expires_on": offer_expiry.isoformat(),
        },
        {
            "source": "marriott",
            "account_name": "Marriott Bonvoy",
            "label": "Free Night Certificate",
            "value": cert_expiry.strftime("%b %d, %Y"),
            "message": f"Free Night Certificate: {cert_expiry.strftime('%b %d, %Y')}",
            "urgency": "soon",
            "days_left": 14,
            "expires_on": cert_expiry.isoformat(),
        },
        {
            "source": "delta",
            "account_name": "Delta SkyMiles",
            "label": "Regional Upgrade Certificate",
            "value": upgrade_expiry.strftime("%b %d, %Y"),
            "message": f"Regional Upgrade Certificate: {upgrade_expiry.strftime('%b %d, %Y')}",
            "urgency": "soon",
            "days_left": 21,
            "expires_on": upgrade_expiry.isoformat(),
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
            "value": dining_end.strftime("%b %d, %Y"),
            "message": f"5× Dining Multiplier: Ends {dining_end.strftime('%b %d, %Y')}",
            "urgency": "soon",
            "days_left": 12,
            "expires_on": dining_end.isoformat(),
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
    return 3


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

    def render_insight_row(title: str, detail: str, severity: str) -> str:
        detail_html = (
            f'<div class="dash-brief-insight-detail">{_he(detail)}</div>'
            if detail else ""
        )
        return (
            f'<li class="dash-brief-insight dash-brief-insight--{severity}">'
            f'<span class="dash-brief-severity" aria-hidden="true"></span>'
            f'<div class="dash-brief-insight-body">'
            f'<div class="dash-brief-insight-title">{_he(title)}</div>'
            f'{detail_html}'
            f'</div></li>'
        )

    def render_insight_list(items: list[tuple[str, str, str]]) -> str:
        if not items:
            return ""
        rows = "".join(render_insight_row(t, d, s) for t, d, s in items)
        return f'<ul class="dash-brief-insights">{rows}</ul>'

    def render_brief_section(section_key: str, title: str, items: list[tuple[str, str, str]]) -> str:
        if not items:
            return ""
        return (
            f'<section class="dash-brief-section dash-brief-section--{section_key}">'
            f'<div class="dash-brief-section-head">'
            f'<h3 class="dash-brief-section-title">{_he(title)}</h3>'
            f'<span class="dash-brief-section-count">{len(items)}</span>'
            f'</div>'
            f'{render_insight_list(items)}'
            f'</section>'
        )

    needs_attention: list[tuple[str, str, str]] = []
    savings: list[tuple[str, str, str]] = []
    expiring: list[tuple[str, str, str]] = []
    discoveries: list[tuple[str, str, str]] = []
    status: list[tuple[str, str, str]] = []

    for item in brief.attention:
        needs_attention.append((item.title, item.detail, "warning"))
    for item in brief.recommendations:
        savings.append((item.title, item.detail, "opportunity"))
    for item in brief.discoveries:
        detail = item.detail or ""
        if "expire" in detail.lower():
            expiring.append((item.title, detail, "warning"))
        else:
            discoveries.append((item.title, detail, "info"))
    for ins in brief.insights:
        sev = ins.severity if ins.severity in ("warning", "opportunity", "info", "success") else "info"
        detail = ins.detail or ""
        if sev == "success":
            status.append((ins.title, detail, "success"))
        elif sev == "opportunity":
            savings.append((ins.title, detail, "opportunity"))
        elif "expire" in detail.lower() or "expire" in ins.title.lower():
            expiring.append((ins.title, detail, "warning"))
        elif sev == "warning":
            needs_attention.append((ins.title, detail, "warning"))
        else:
            discoveries.append((ins.title, detail, "info"))
    for item in brief.completed:
        status.append((item.title, item.detail, "success"))

    sections_html = (
        render_brief_section("attention", "Needs attention", needs_attention)
        + render_brief_section("expiring", "Benefits expiring", expiring)
        + render_brief_section("savings", "Savings opportunities", savings)
        + render_brief_section("discoveries", "Recent discoveries", discoveries)
    )

    lede_html = (
        f'<div class="dash-brief-lede">'
        f'<h2 class="dash-brief-headline">{_he(brief.headline)}</h2>'
        f'<p class="dash-brief-summary-text">{_he(brief.summary)}</p>'
        f'</div>'
    )
    status_html = render_insight_list(status[:2])
    if status_html:
        status_html = f'<div class="dash-brief-status">{status_html}</div>'

    demo_tag = (
        '<span style="font-size:10px;font-weight:600;color:#7c3aed;background:#f5f3ff;'
        'border:0.5px solid rgba(124,58,237,0.2);border-radius:20px;padding:3px 9px;'
        'margin-left:8px;vertical-align:middle">Demo data</span>'
    )

    body_html = (
        f'{lede_html}'
        f'<div class="dash-brief-sections">{sections_html}</div>'
        f'{status_html}'
    )

    return (
        f'<div class="dash-hero">'
        f'<div class="dash-brief-card">'
        f'<div class="dash-brief-greeting" id="hero-greeting">Hello, {_he(first_name)}</div>'
        f'<div class="dash-brief-today">'
        f'<span class="dash-brief-today-label">Today</span>'
        f'<span class="dash-brief-today-date">{_he(today_label)}{demo_tag}</span>'
        f'</div>'
        f'{body_html}'
        f'</div>'
        f'<script>'
        f'(function(){{'
        f'  var h=new Date().getHours();'
        f'  var g=h<12?"Good morning":h<17?"Good afternoon":"Good evening";'
        f'  var el=document.getElementById("hero-greeting");'
        f'  if(el) el.textContent=g+", {_he(first_name)}";'
        f'}})();'
        f'</script>'
        f'</div>'
    )
