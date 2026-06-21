"""
mighty.email_scan
─────────────────
Scan a user's email (Gmail OAuth, Outlook OAuth, or IMAP) to surface
account suggestions for the Mighty dashboard.

Only sender addresses and subjects are examined — no message bodies are
fetched or stored.

Usage
-----
    from mighty.email_scan import scan_gmail, scan_imap, scan_outlook
    from mighty.email_scan import SITE_SENDER_DOMAINS

    suggestions = scan_gmail(access_token, already_connected={"delta","united"})
    # → [{"site_key": "marriott", "display_name": "Marriott Bonvoy",
    #     "category": "hotel", "email_count": 12, "sender": "marriott.com"}, ...]
"""
from __future__ import annotations
import re
from typing import Optional

# ── Sender domain → site_key map ─────────────────────────────────────────────
# Values: (site_key, display_name, category)
# Multiple sender domains can map to the same site_key.
# Domains are matched as suffix: "email.delta.com" matches entry "delta.com"

SITE_SENDER_DOMAINS: dict[str, tuple[str, str, str]] = {
    # ── Airlines ─────────────────────────────────────────────────────────────
    "delta.com":                ("delta",         "Delta SkyMiles",                "airline"),
    "news.delta.com":           ("delta",         "Delta SkyMiles",                "airline"),
    "emails.delta.com":         ("delta",         "Delta SkyMiles",                "airline"),
    "email.delta.com":          ("delta",         "Delta SkyMiles",                "airline"),
    "united.com":               ("united",        "United MileagePlus",            "airline"),
    "news.united.com":          ("united",        "United MileagePlus",            "airline"),
    "email.united.com":         ("united",        "United MileagePlus",            "airline"),
    "aa.com":                   ("american_air",  "American Airlines AAdvantage",  "airline"),
    "aadvantage.aa.com":        ("american_air",  "American Airlines AAdvantage",  "airline"),
    "email.aa.com":             ("american_air",  "American Airlines AAdvantage",  "airline"),
    "southwest.com":            ("southwest",     "Southwest Rapid Rewards",       "airline"),
    "ifly.southwest.com":       ("southwest",     "Southwest Rapid Rewards",       "airline"),
    "email.southwest.com":      ("southwest",     "Southwest Rapid Rewards",       "airline"),
    "alaskaair.com":            ("alaska_air",    "Alaska Airlines Mileage Plan",  "airline"),
    "email.alaskaair.com":      ("alaska_air",    "Alaska Airlines Mileage Plan",  "airline"),
    "jetblue.com":              ("jetblue",       "JetBlue TrueBlue",              "airline"),
    "email.jetblue.com":        ("jetblue",       "JetBlue TrueBlue",              "airline"),
    "flyfrontier.com":          ("frontier",      "Frontier Airlines FRONTIER Miles","airline"),
    "spirit.com":               ("spirit",        "Spirit Airlines Free Spirit",   "airline"),
    "aircanada.com":            ("air_canada",    "Air Canada Aeroplan",           "airline"),
    "aeroplan.com":             ("air_canada",    "Air Canada Aeroplan",           "airline"),
    "hawaiianairlines.com":     ("hawaiian_air",  "Hawaiian Miles",                "airline"),
    "email.hawaiianairlines.com": ("hawaiian_air","Hawaiian Miles",                "airline"),
    "britishairways.com":       ("british_air",   "British Airways Avios",         "airline"),
    "email.britishairways.com": ("british_air",   "British Airways Avios",         "airline"),
    "lufthansa.com":            ("lufthansa",     "Lufthansa Miles & More",        "airline"),

    # ── Hotels ───────────────────────────────────────────────────────────────
    "marriott.com":             ("marriott",      "Marriott Bonvoy",               "hotel"),
    "e.marriott.com":           ("marriott",      "Marriott Bonvoy",               "hotel"),
    "email.marriott.com":       ("marriott",      "Marriott Bonvoy",               "hotel"),
    "news.marriott.com":        ("marriott",      "Marriott Bonvoy",               "hotel"),
    "bonvoy.marriott.com":      ("marriott",      "Marriott Bonvoy",               "hotel"),
    "hilton.com":               ("hilton",        "Hilton Honors",                 "hotel"),
    "email.hilton.com":         ("hilton",        "Hilton Honors",                 "hotel"),
    "hiltonhonors.com":         ("hilton",        "Hilton Honors",                 "hotel"),
    "hyatt.com":                ("hyatt",         "World of Hyatt",                "hotel"),
    "email.hyatt.com":          ("hyatt",         "World of Hyatt",                "hotel"),
    "worldofhyatt.com":         ("hyatt",         "World of Hyatt",                "hotel"),
    "ihg.com":                  ("ihg",           "IHG One Rewards",               "hotel"),
    "email.ihg.com":            ("ihg",           "IHG One Rewards",               "hotel"),
    "wyndhamhotels.com":        ("wyndham",       "Wyndham Rewards",               "hotel"),
    "email.wyndhamhotels.com":  ("wyndham",       "Wyndham Rewards",               "hotel"),
    "choicehotels.com":         ("choice_hotels", "Choice Privileges",             "hotel"),
    "email.choicehotels.com":   ("choice_hotels", "Choice Privileges",             "hotel"),
    "bestwestern.com":          ("best_western",  "Best Western Rewards",          "hotel"),
    "radissonhotels.com":       ("radisson",      "Radisson Rewards",              "hotel"),
    "mgmresorts.com":           ("mgm_rewards",   "MGM Rewards",                   "hotel"),

    # ── Credit Cards & Banks ──────────────────────────────────────────────────
    "americanexpress.com":      ("amex",          "American Express",              "credit_card"),
    "email.americanexpress.com":("amex",          "American Express",              "credit_card"),
    "aexp.com":                 ("amex",          "American Express",              "credit_card"),
    "chase.com":                ("chase",         "Chase",                         "credit_card"),
    "chaseonline.chase.com":    ("chase",         "Chase",                         "credit_card"),
    "email.chase.com":          ("chase",         "Chase",                         "credit_card"),
    "citi.com":                 ("citi",          "Citi",                          "credit_card"),
    "email.citi.com":           ("citi",          "Citi",                          "credit_card"),
    "citibank.com":             ("citi",          "Citi",                          "credit_card"),
    "capitalone.com":           ("capital_one",   "Capital One",                   "credit_card"),
    "email.capitalone.com":     ("capital_one",   "Capital One",                   "credit_card"),
    "discover.com":             ("discover",      "Discover",                      "credit_card"),
    "email.discover.com":       ("discover",      "Discover",                      "credit_card"),
    "wellsfargo.com":           ("wells_fargo",   "Wells Fargo",                   "bank"),
    "email.wellsfargo.com":     ("wells_fargo",   "Wells Fargo",                   "bank"),
    "bankofamerica.com":        ("bofa",          "Bank of America",               "bank"),
    "email.bankofamerica.com":  ("bofa",          "Bank of America",               "bank"),
    "usbank.com":               ("us_bank",       "U.S. Bank",                     "bank"),
    "email.usbank.com":         ("us_bank",       "U.S. Bank",                     "bank"),
    "barclaysus.com":           ("barclays",      "Barclays",                      "credit_card"),
    "paypal.com":               ("paypal",        "PayPal",                        "payment"),
    "email.paypal.com":         ("paypal",        "PayPal",                        "payment"),
    "venmo.com":                ("venmo",         "Venmo",                         "payment"),
    "fidelity.com":             ("fidelity",      "Fidelity",                      "investment"),
    "email.fidelity.com":       ("fidelity",      "Fidelity",                      "investment"),
    "schwab.com":               ("schwab",        "Charles Schwab",                "investment"),
    "vanguard.com":             ("vanguard",      "Vanguard",                      "investment"),
    "sofi.com":                 ("sofi",          "SoFi",                          "bank"),

    # ── Telecom & Cable ───────────────────────────────────────────────────────
    "xfinity.com":              ("xfinity",       "Xfinity",                       "telecom"),
    "email.xfinity.com":        ("xfinity",       "Xfinity",                       "telecom"),
    "comcast.com":              ("xfinity",       "Xfinity",                       "telecom"),
    "att.com":                  ("att",           "AT&T",                          "telecom"),
    "email.att.com":            ("att",           "AT&T",                          "telecom"),
    "verizon.com":              ("verizon",       "Verizon",                       "telecom"),
    "email.verizon.com":        ("verizon",       "Verizon",                       "telecom"),
    "vzw.com":                  ("verizon",       "Verizon",                       "telecom"),
    "t-mobile.com":             ("tmobile",       "T-Mobile",                      "telecom"),
    "email.t-mobile.com":       ("tmobile",       "T-Mobile",                      "telecom"),
    "spectrum.com":             ("spectrum",      "Spectrum",                      "telecom"),
    "cox.com":                  ("cox",           "Cox",                           "telecom"),

    # ── Streaming & Entertainment ─────────────────────────────────────────────
    "netflix.com":              ("netflix",       "Netflix",                       "streaming"),
    "info.netflix.com":         ("netflix",       "Netflix",                       "streaming"),
    "disneyplus.com":           ("disney_plus",   "Disney+",                       "streaming"),
    "hulu.com":                 ("hulu",          "Hulu",                          "streaming"),
    "spotify.com":              ("spotify",       "Spotify",                       "streaming"),
    "email.spotify.com":        ("spotify",       "Spotify",                       "streaming"),
    "max.com":                  ("max",           "Max (HBO)",                     "streaming"),
    "peacocktv.com":            ("peacock",       "Peacock",                       "streaming"),
    "paramountplus.com":        ("paramount",     "Paramount+",                    "streaming"),
    "appletv.apple.com":        ("apple_tv",      "Apple TV+",                     "streaming"),
    "amazon.com":               ("amazon_prime",  "Amazon Prime",                  "streaming"),
    "primevideo.com":           ("amazon_prime",  "Amazon Prime",                  "streaming"),
    "email.amazon.com":         ("amazon_prime",  "Amazon Prime",                  "streaming"),

    # ── Car Rental ───────────────────────────────────────────────────────────
    "hertz.com":                ("hertz",         "Hertz Gold Plus Rewards",       "car_rental"),
    "email.hertz.com":          ("hertz",         "Hertz Gold Plus Rewards",       "car_rental"),
    "avis.com":                 ("avis",          "Avis Preferred",                "car_rental"),
    "budget.com":               ("budget",        "Budget Fastbreak",              "car_rental"),
    "nationalcar.com":          ("national_car",  "National Emerald Club",         "car_rental"),
    "enterprise.com":           ("enterprise",    "Enterprise Plus",               "car_rental"),
    "email.enterprise.com":     ("enterprise",    "Enterprise Plus",               "car_rental"),
    "alamo.com":                ("alamo",         "Alamo Insiders",                "car_rental"),

    # ── Retail & Shopping ────────────────────────────────────────────────────
    "target.com":               ("target",        "Target Circle",                 "retail"),
    "email.target.com":         ("target",        "Target Circle",                 "retail"),
    "walmart.com":              ("walmart",       "Walmart+",                      "retail"),
    "costco.com":               ("costco",        "Costco",                        "retail"),
    "kohls.com":                ("kohls",         "Kohl's Rewards",                "retail"),
    "bestbuy.com":              ("best_buy",      "Best Buy My Best Buy",          "retail"),
    "emails.bestbuy.com":       ("best_buy",      "Best Buy My Best Buy",          "retail"),
    "nordstrom.com":            ("nordstrom",     "Nordstrom Nordy Club",          "retail"),
    "email.nordstrom.com":      ("nordstrom",     "Nordstrom Nordy Club",          "retail"),
    "macys.com":                ("macys",         "Macy's Star Rewards",           "retail"),
    "staples.com":              ("staples",       "Staples Rewards",               "retail"),
    "homedepot.com":            ("home_depot",    "The Home Depot",                "retail"),
    "lowes.com":                ("lowes",         "Lowe's MyLowe's Rewards",       "retail"),

    # ── Dining & Food ────────────────────────────────────────────────────────
    "starbucks.com":            ("starbucks",     "Starbucks Rewards",             "dining"),
    "email.starbucks.com":      ("starbucks",     "Starbucks Rewards",             "dining"),
    "chipotle.com":             ("chipotle",      "Chipotle Rewards",              "dining"),
    "dominos.com":              ("dominos",       "Domino's Piece of the Pie",     "dining"),
    "subway.com":               ("subway",        "Subway MVP Rewards",            "dining"),
    "panera.com":               ("panera",        "Panera Sip Club / Unlimited",   "dining"),
    "doordash.com":             ("doordash",      "DashPass",                      "dining"),
    "uber.com":                 ("uber",          "Uber One",                      "dining"),
    "grubhub.com":              ("grubhub",       "Grubhub+",                      "dining"),

    # ── Gas & Auto ───────────────────────────────────────────────────────────
    "shell.com":                ("shell",         "Shell Fuel Rewards",            "gas"),
    "exxon.com":                ("exxon",         "ExxonMobil Rewards+",           "gas"),
    "speedway.com":             ("speedway",      "Speedy Rewards",                "gas"),
    "circle-k.com":             ("circle_k",      "Inner Circle Rewards",          "gas"),

    # ── Health & Fitness ─────────────────────────────────────────────────────
    "cvs.com":                  ("cvs",           "CVS ExtraCare",                 "health"),
    "email.cvs.com":            ("cvs",           "CVS ExtraCare",                 "health"),
    "walgreens.com":            ("walgreens",     "Walgreens myWalgreens",         "health"),
    "rite-aid.com":             ("rite_aid",      "Rite Aid wellness+",            "health"),

    # ── Insurance ────────────────────────────────────────────────────────────
    "allstate.com":             ("allstate",      "Allstate",                      "insurance"),
    "statefarm.com":            ("state_farm",    "State Farm",                    "insurance"),
    "geico.com":                ("geico",         "GEICO",                         "insurance"),
    "progressive.com":          ("progressive",   "Progressive",                   "insurance"),

    # ── Tech & Subscriptions ─────────────────────────────────────────────────
    "apple.com":                ("apple",         "Apple",                         "tech"),
    "email.apple.com":          ("apple",         "Apple",                         "tech"),
    "microsoft.com":            ("microsoft",     "Microsoft",                     "tech"),
    "google.com":               ("google",        "Google",                        "tech"),
    "adobe.com":                ("adobe",         "Adobe",                         "tech"),
    "dropbox.com":              ("dropbox",       "Dropbox",                       "tech"),
    "email.dropbox.com":        ("dropbox",       "Dropbox",                       "tech"),
}

# Category display labels
CATEGORY_LABELS = {
    "airline":    "✈️  Airlines",
    "hotel":      "🏨  Hotels",
    "credit_card":"💳  Credit Cards",
    "bank":       "🏦  Banks & Investing",
    "investment": "📈  Banks & Investing",
    "payment":    "💰  Payments",
    "telecom":    "📱  Telecom & Cable",
    "streaming":  "🎬  Streaming",
    "car_rental": "🚗  Car Rental",
    "retail":     "🛍️  Retail",
    "dining":     "🍽️  Dining",
    "gas":        "⛽  Gas & Auto",
    "health":     "💊  Health",
    "insurance":  "🛡️  Insurance",
    "tech":       "💻  Tech & Apps",
}


def _sender_domain(from_address: str) -> str:
    """Extract the domain from a From: header value like 'Name <email@domain.com>'."""
    m = re.search(r"@([\w.\-]+)", from_address.lower())
    return m.group(1) if m else ""


def _domain_to_site_key(domain: str) -> Optional[tuple[str, str, str]]:
    """
    Attempt exact match first, then suffix match.
    Returns (site_key, display_name, category) or None.
    """
    if domain in SITE_SENDER_DOMAINS:
        return SITE_SENDER_DOMAINS[domain]
    # Try matching as a subdomain suffix: "news.delta.com" should match "delta.com"
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in SITE_SENDER_DOMAINS:
            return SITE_SENDER_DOMAINS[candidate]
    return None


def _build_suggestions(
    sender_counts: dict[str, int],
    already_connected: set[str],
) -> list[dict]:
    """
    Convert a {domain: count} dict into a ranked list of site suggestions,
    excluding already-connected sites.
    """
    seen_site_keys: set[str] = set()
    suggestions: list[dict] = []

    for domain, count in sorted(sender_counts.items(), key=lambda x: -x[1]):
        result = _domain_to_site_key(domain)
        if not result:
            continue
        site_key, display_name, category = result
        if site_key in already_connected or site_key in seen_site_keys:
            continue
        seen_site_keys.add(site_key)
        suggestions.append({
            "site_key":     site_key,
            "display_name": display_name,
            "category":     category,
            "email_count":  count,
            "sender":       domain,
        })

    # Sort: higher email count → more likely the account is active
    suggestions.sort(key=lambda x: -x["email_count"])
    return suggestions


# ── Gmail ─────────────────────────────────────────────────────────────────────

def scan_gmail(
    access_token: str,
    already_connected: set[str] | None = None,
    max_results: int = 500,
) -> list[dict]:
    """
    Scan Gmail for known loyalty/account senders.
    Uses the Gmail API with format=metadata (headers only — no body).

    Requires scope: https://www.googleapis.com/auth/gmail.readonly

    Returns a list of suggestion dicts (see module docstring).
    """
    import requests as _req

    already_connected = already_connected or set()
    sender_counts: dict[str, int] = {}

    headers = {"Authorization": f"Bearer {access_token}"}

    # Build a set of all domains we care about (and their TLDs for query)
    # Query Gmail for messages from each root domain we track
    root_domains = set()
    for domain in SITE_SENDER_DOMAINS:
        parts = domain.split(".")
        root_domains.add(".".join(parts[-2:]))  # e.g. "delta.com"

    for root in root_domains:
        try:
            resp = _req.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params={
                    "q": f"from:{root}",
                    "maxResults": 1,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            count = data.get("resultSizeEstimate", 0)
            if count > 0:
                sender_counts[root] = count
        except Exception:
            continue

    return _build_suggestions(sender_counts, already_connected)


# ── Outlook (Microsoft Graph) ─────────────────────────────────────────────────

def scan_outlook(
    access_token: str,
    already_connected: set[str] | None = None,
) -> list[dict]:
    """
    Scan Outlook / Microsoft 365 for known senders.
    Uses Microsoft Graph API — messages endpoint with $filter on sender.

    Requires scope: Mail.Read

    Returns a list of suggestion dicts.
    """
    import requests as _req

    already_connected = already_connected or set()
    sender_counts: dict[str, int] = {}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    root_domains = set()
    for domain in SITE_SENDER_DOMAINS:
        parts = domain.split(".")
        root_domains.add(".".join(parts[-2:]))

    for root in root_domains:
        try:
            resp = _req.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers=headers,
                params={
                    "$filter": f"contains(from/emailAddress/address, '{root}')",
                    "$select": "from",
                    "$top": 1,
                    "$count": "true",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            count = data.get("@odata.count", 0) or len(data.get("value", []))
            if count > 0:
                sender_counts[root] = count
        except Exception:
            continue

    return _build_suggestions(sender_counts, already_connected)


# ── IMAP ──────────────────────────────────────────────────────────────────────

def scan_imap(
    host: str,
    port: int,
    username: str,
    password: str,
    already_connected: set[str] | None = None,
    use_ssl: bool = True,
) -> list[dict]:
    """
    Scan any IMAP mailbox for known senders.
    Connects with SSL (port 993 by default), searches INBOX.

    Returns a list of suggestion dicts, or raises on auth failure.
    """
    import imaplib

    already_connected = already_connected or set()
    sender_counts: dict[str, int] = {}

    if use_ssl:
        imap = imaplib.IMAP4_SSL(host, port)
    else:
        imap = imaplib.IMAP4(host, port)

    imap.login(username, password)
    imap.select("INBOX", readonly=True)

    root_domains = set()
    for domain in SITE_SENDER_DOMAINS:
        parts = domain.split(".")
        root_domains.add(".".join(parts[-2:]))

    for root in root_domains:
        try:
            status, data = imap.search(None, f'(FROM "{root}")')
            if status == "OK" and data and data[0]:
                count = len(data[0].split())
                if count > 0:
                    sender_counts[root] = count
        except Exception:
            continue

    imap.logout()
    return _build_suggestions(sender_counts, already_connected)


# ── IMAP host presets ─────────────────────────────────────────────────────────

IMAP_PRESETS = {
    "gmail":   {"host": "imap.gmail.com",   "port": 993, "ssl": True},
    "outlook": {"host": "outlook.office365.com", "port": 993, "ssl": True},
    "yahoo":   {"host": "imap.mail.yahoo.com",   "port": 993, "ssl": True},
    "icloud":  {"host": "imap.mail.me.com",      "port": 993, "ssl": True},
    "custom":  {"host": "",                       "port": 993, "ssl": True},
}


def get_imap_preset(provider: str) -> dict:
    return IMAP_PRESETS.get(provider, IMAP_PRESETS["custom"])
