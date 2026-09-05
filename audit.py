"""Grade the website a listing points at.

This stage is free forever (plain HTTP from your machine) and produces the
highest-value bucket: businesses that already believe in having a web presence
but have a broken or fake one. Those need no education, only a better option.
"""

import asyncio
import re
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

import httpx

import cache

# A social profile listed as "the website" is the strongest signal in the set:
# they wanted a web presence, settled for a rented one, and can't rank for
# anything. No education needed in the pitch.
SOCIAL_HOSTS = {
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "fb.me",
    "instagram.com", "www.instagram.com", "linktr.ee", "linktree.com",
    "twitter.com", "x.com", "tiktok.com", "www.tiktok.com", "yelp.com",
    "www.yelp.com", "nextdoor.com", "wa.me", "m.me", "beacons.ai", "bio.link",
    "t.me", "vsble.me", "allmylinks.com",
}

# Google shut down Business Profile-built websites in March 2024; these domains
# now dead-end. A listing still pointing here has a website field that goes
# nowhere, and the owner very likely doesn't know.
DEAD_GOOGLE_BUILDER = {"business.site", "negocio.site"}

# Free-tier builder subdomains: never paid a cent, so low commitment, but it
# also proves they tried. Middling signal.
FREE_BUILDER_SUFFIXES = (
    ".wixsite.com", ".weebly.com", ".godaddysites.com", ".square.site",
    ".webnode.com", ".jimdosite.com", ".blogspot.com", ".wordpress.com",
    ".sites.google.com", ".myportfolio.com", ".carrd.co", ".strikingly.com",
    ".tilda.ws", ".webflow.io", ".netlify.app", ".github.io",
)

PARKED_MARKERS = (
    "domain is for sale", "buy this domain", "domain for sale",
    "sedoparking", "afternic", "this domain is parked", "parked free",
    "hugedomains", "godaddy.com/domainsearch", "future home of something",
    "under construction", "coming soon", "website coming soon",
)

PLATFORM_MARKERS = {
    "WordPress": ("wp-content", "wp-includes", "wp-json"),
    "Wix": ("static.parastorage.com", "wix.com", "_wixCssStates"),
    "Squarespace": ("squarespace.com", "static1.squarespace"),
    "Shopify": ("cdn.shopify.com", "shopify.js"),
    "GoDaddy": ("godaddy.com", "img1.wsimg.com"),
    "Weebly": ("weebly.com", "editmysite.com"),
    "Webflow": ("webflow.com", "assets.website-files.com"),
    "Duda": ("dudamobile.com", "multiscreensite.com"),
}

BOOKING_MARKERS = (
    "calendly.com", "acuityscheduling", "booksy.com", "squareup.com/appointments",
    "opentable.com", "resy.com", "housecallpro", "getjobber", "servicetitan",
    "setmore", "simplybook", "vagaro", "mindbodyonline", "schedulicity",
    "square.site/book", "tidycal", "cal.com",
)

# Ordered worst-first; the first match wins.
ISSUE_ORDER = [
    "DEAD_GOOGLE_BUILDER", "SOCIAL_ONLY", "UNREACHABLE", "PARKED",
    "DEAD_DOMAIN", "CERT_ERROR", "SERVER_ERROR", "NO_HTTPS", "NOT_MOBILE",
    "FREE_BUILDER", "STALE", "SLOW", "THIN", "TIMEOUT", "BLOCKED", "OK",
    "NO_WEBSITE",
]

# A server refusing to talk to a non-browser client tells us nothing about
# whether the site works. Cloudflare and friends answer with these, and a 429
# is our own rate limiting. None of them is a lead.
BLOCKING_CODES = {401, 403, 406, 409, 429}

CURRENT_YEAR = 2026
STALE_AFTER_YEARS = 3
SLOW_SECONDS = 3.0
THIN_BYTES = 2000


@dataclass
class Audit:
    url: str | None = None
    final_url: str | None = None
    issue: str = "NO_WEBSITE"
    reachable: bool = False
    status_code: int | None = None
    https: bool = False
    mobile_ready: bool = False
    platform: str = ""
    load_seconds: float = 0.0
    page_bytes: int = 0
    copyright_year: int | None = None
    has_booking: bool = False
    has_form: bool = False
    has_phone_link: bool = False
    title: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def classify_url(url: str | None) -> str | None:
    """Issues decidable from the URL alone, before spending a fetch."""
    if not url or not url.strip():
        return "NO_WEBSITE"
    host = _host(url)
    if not host:
        return "NO_WEBSITE"
    if _registrable(host) in DEAD_GOOGLE_BUILDER:
        return "DEAD_GOOGLE_BUILDER"
    if host in SOCIAL_HOSTS or _registrable(host) in SOCIAL_HOSTS:
        return "SOCIAL_ONLY"
    return None


def _copyright_year(html: str) -> int | None:
    years = re.findall(
        r"(?:©|&copy;|&#169;|copyright)[^0-9]{0,20}(?:\d{4}\s*[-–—]\s*)?(20\d{2})",
        html,
        re.I,
    )
    valid = [int(y) for y in years if 2000 <= int(y) <= CURRENT_YEAR + 1]
    return max(valid) if valid else None


def _platform(html: str) -> str:
    lower = html.lower()
    for name, markers in PLATFORM_MARKERS.items():
        if any(m in lower for m in markers):
            return name
    return ""


def grade_html(a: Audit, html: str) -> Audit:
    lower = html.lower()

    a.page_bytes = len(html)
    a.platform = _platform(html)
    a.mobile_ready = bool(re.search(r'<meta[^>]+name=["\']?viewport', html, re.I))
    a.copyright_year = _copyright_year(html)
    a.has_booking = any(m in lower for m in BOOKING_MARKERS)
    a.has_form = "<form" in lower
    a.has_phone_link = "tel:" in lower

    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        a.title = re.sub(r"\s+", " ", m.group(1)).strip()[:120]

    host = _host(a.final_url or a.url or "")

    if any(marker in lower for marker in PARKED_MARKERS) and a.page_bytes < 30000:
        a.issue = "PARKED"
    elif a.status_code and a.status_code >= 500:
        a.issue = "SERVER_ERROR"
    elif a.status_code in BLOCKING_CODES:
        # We were refused, so we learned nothing. Not evidence of a bad site.
        a.issue = "BLOCKED"
        a.note = f"HTTP {a.status_code} - blocked us, site may be fine"
    elif a.status_code and a.status_code >= 400:
        a.issue = "UNREACHABLE"
        a.note = f"HTTP {a.status_code}"
    elif not a.https:
        a.issue = "NO_HTTPS"
    elif not a.mobile_ready:
        a.issue = "NOT_MOBILE"
    elif any(host.endswith(s) for s in FREE_BUILDER_SUFFIXES):
        a.issue = "FREE_BUILDER"
    elif a.copyright_year and CURRENT_YEAR - a.copyright_year >= STALE_AFTER_YEARS:
        a.issue = "STALE"
        a.note = f"© {a.copyright_year}"
    elif a.load_seconds > SLOW_SECONDS:
        a.issue = "SLOW"
        a.note = f"{a.load_seconds:.1f}s"
    elif a.page_bytes < THIN_BYTES:
        a.issue = "THIN"
        a.note = f"{a.page_bytes}B of HTML"
    else:
        a.issue = "OK"
    return a


async def _fetch(client: httpx.AsyncClient, url: str):
    """Fetch with one patient retry.

    A single timeout is weak evidence: slow shared hosting, a firewall that
    dislikes our user agent, or a blip. Only a second failure counts. DNS
    failure needs no retry - that domain is genuinely gone.
    """
    try:
        return await client.get(url, follow_redirects=True)
    except httpx.TimeoutException:
        return await client.get(url, follow_redirects=True, timeout=25.0)


async def audit_one(client: httpx.AsyncClient, url: str | None, use_cache=True) -> Audit:
    early = classify_url(url)
    if early == "NO_WEBSITE":
        return Audit(issue="NO_WEBSITE")
    if early:
        return Audit(url=url, issue=early, reachable=True)

    if use_cache:
        cached = cache.get("audit", url, cache.AUDIT_TTL)
        if cached is not None:
            return Audit(**cached)

    a = Audit(url=url)
    loop = asyncio.get_event_loop()
    started = loop.time()
    try:
        resp = await _fetch(client, url)
        a.load_seconds = loop.time() - started
        a.reachable = True
        a.status_code = resp.status_code
        a.final_url = str(resp.url)
        a.https = a.final_url.startswith("https://")
        a = grade_html(a, resp.text)
    except Exception as e:
        a.load_seconds = loop.time() - started
        a.reachable = False
        text = f"{type(e).__name__}: {e}"
        upper = text.upper()
        if any(k in upper for k in ("CERTIFICATE", "SSL", "TLSV")):
            a.issue = "CERT_ERROR"
        elif isinstance(e, httpx.TimeoutException):
            # Twice timed out but DNS resolved: probably alive, just slow or
            # blocking us. Scored low on purpose - verify before you call.
            a.issue = "TIMEOUT"
        elif ("nodename nor servname" in text
              or "Name or service not known" in text
              or "getaddrinfo failed" in text):
            # No DNS record at all. The strongest signal available: they are
            # paying for a listing that points at nothing.
            a.issue = "DEAD_DOMAIN"
            a.note = "domain does not resolve"
        else:
            a.issue = "UNREACHABLE"
        a.note = a.note or text[:160]

    if use_cache:
        cache.set("audit", url, a.as_dict())
    return a


async def audit_all(urls: list[str | None], concurrency: int = 20, use_cache=True):
    limits = httpx.Limits(max_connections=concurrency)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        )
    }
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        # verify=True on purpose: a bad cert should raise so we can record
        # CERT_ERROR. Disabling verification would silently hide a real signal.
        timeout=12.0, limits=limits, headers=headers, verify=True
    ) as client:

        async def bounded(u):
            async with sem:
                return await audit_one(client, u, use_cache)

        return await asyncio.gather(*(bounded(u) for u in urls))
