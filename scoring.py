"""Lead scoring. Every constant here is meant to be argued with.

Two halves, 50 points each:
  website opportunity - how broken their current presence is
  business quality    - whether they can actually pay

Components land in the CSV as their own columns, so you can re-rank in a
spreadsheet without re-running anything.
"""

import math

# Ranked by how easy the conversation is, not by how broken the site is.
# SOCIAL_ONLY and DEAD_GOOGLE_BUILDER top the list because the owner already
# decided they want a web presence - you're replacing something, not selling
# an idea. NO_WEBSITE sits below every bad-website case: absence of a site is
# often a deliberate choice by someone who's booked solid without one.
ISSUE_SCORE = {
    "SOCIAL_ONLY": 50,
    "DEAD_GOOGLE_BUILDER": 50,
    "PARKED": 46,
    # Listing points at a domain with no DNS record at all - definitively
    # broken, and they almost certainly don't know.
    "DEAD_DOMAIN": 47,
    "UNREACHABLE": 44,
    "CERT_ERROR": 44,
    # Timed out twice but the domain resolves - usually alive and slow, or
    # blocking our user agent. Low confidence, so scored below NO_WEBSITE.
    "TIMEOUT": 26,
    # We were refused (403/429/etc), so we never saw the site. Scored near the
    # bottom: assume it's fine until a human checks.
    "BLOCKED": 12,
    "SERVER_ERROR": 43,
    "NO_HTTPS": 41,
    "NOT_MOBILE": 39,
    "FREE_BUILDER": 35,
    "NO_WEBSITE": 30,
    "THIN": 28,
    "STALE": 24,
    "SLOW": 20,
    "OK": 5,
}

# One job pays for the website many times over. Same sales effort as a haircut.
HIGH_TICKET = (
    "roof", "hvac", "heating", "air condition", "plumb", "electric", "dentist",
    "dental", "orthodont", "lawyer", "attorney", "law ", "contractor", "landscap",
    "remodel", "renovation", "pest", "garage door", "foundation", "solar",
    "med spa", "medspa", "veterinar", "chiropract", "insurance", "accountant",
    "cpa", "moving", "paving", "fenc", "pool", "tree service", "restoration",
    "locksmith", "auto repair", "windows", "siding", "flooring", "cabinet",
    "septic", "well drilling", "excavat", "surveyor", "architect", "cosmetic",
)

REVIEW_SATURATION = 500  # reviews beyond this add nothing
W_REVIEWS = 26
W_RATING = 6
W_PHONE = 4
W_HIGH_TICKET = 14

TIERS = [(70, "A"), (55, "B"), (40, "C")]


def is_high_ticket(place) -> bool:
    haystack = f"{place.category} {place.name}".lower()
    return any(k in haystack for k in HIGH_TICKET)


def business_score(place) -> dict:
    """Can they pay? Review *count* carries this, not rating.

    4.9 stars from 12 reviews is a hobbyist. 4.3 from 400 is a real business
    with real revenue. Rating gets a token weight so it breaks ties.
    """
    n = place.review_count or 0
    reviews = W_REVIEWS * math.log10(1 + n) / math.log10(1 + REVIEW_SATURATION)
    reviews = min(W_REVIEWS, reviews)

    rating = place.rating or 0.0
    rating_pts = max(0.0, min(W_RATING, (rating - 4.0) * W_RATING))

    phone_pts = W_PHONE if place.phone else 0

    ticket_pts = W_HIGH_TICKET if is_high_ticket(place) else 0

    return {
        "reviews_pts": round(reviews, 1),
        "rating_pts": round(rating_pts, 1),
        "phone_pts": phone_pts,
        "high_ticket_pts": ticket_pts,
        "business_score": round(reviews + rating_pts + phone_pts + ticket_pts, 1),
    }


def score(place, audit) -> dict:
    web = ISSUE_SCORE.get(audit.issue, 10)
    biz = business_score(place)
    total = round(web + biz["business_score"], 1)

    tier = "D"
    for cutoff, label in TIERS:
        if total >= cutoff:
            tier = label
            break

    return {"score": total, "tier": tier, "website_score": web, **biz}


def passes_filter(place, min_rating: float, min_reviews: int) -> bool:
    if place.status != "OPERATIONAL":
        return False
    if (place.rating or 0) < min_rating:
        return False
    if (place.review_count or 0) < min_reviews:
        return False
    return True
