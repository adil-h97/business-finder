#!/usr/bin/env python3
"""business-finder - find local businesses with good reviews and bad websites.

  python finder.py --query "roofing contractor" --city "Austin, TX"

Requests are the scarce resource (1,000/month free on the Places Enterprise
SKU), so: results are cached to disk, the grid subdivides only where it needs
to, and --max-requests hard-stops a run. Set a quota cap in GCP too.
"""

import argparse
import asyncio
import csv
import os
import sys
from collections import Counter

from dotenv import load_dotenv

import audit as audit_mod
import cache
import scoring
from grid import Rect
from sources import Apify, GooglePlaces

CSV_COLUMNS = [
    "score", "tier", "issue", "name", "phone", "website", "rating",
    "review_count", "category", "address", "maps_url", "website_score",
    "business_score", "reviews_pts", "rating_pts", "phone_pts",
    "high_ticket_pts", "platform", "mobile_ready", "https", "load_seconds",
    "copyright_year", "has_booking", "has_form", "title", "note", "place_id",
]


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Find local businesses with strong reviews and weak websites.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--query", default="",
                   help='e.g. "roofing contractor". Omit for every business '
                        'in the area (uses Nearby Search: 20 per cell, no paging)')
    p.add_argument("--city", help='e.g. "Austin, TX"')
    p.add_argument("--bbox", help="south,west,north,east - use instead of --city")
    p.add_argument("--source", choices=["google", "apify"], default="google")
    p.add_argument("--min-rating", type=float, default=4.0)
    p.add_argument("--min-reviews", type=int, default=10)
    p.add_argument("--max-requests", type=int, default=60,
                   help="hard stop for this run (free tier is 1000/month)")
    p.add_argument("--min-cell-km", type=float, default=2.0,
                   help="stop subdividing below this cell width")
    p.add_argument("--no-audit", action="store_true",
                   help="skip website grading (loses the best signals)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--out", default="leads.csv")
    p.add_argument("--top", type=int, default=15, help="rows to print")
    p.add_argument("--dry-run", action="store_true",
                   help="resolve the area and show the budget, spend nothing else")
    return p.parse_args(argv)


def resolve_area(client, args) -> Rect:
    if args.bbox:
        s, w, n, e = (float(x) for x in args.bbox.split(","))
        return Rect(s, w, n, e)
    if not args.city:
        sys.exit("error: pass --city or --bbox")
    return client.geocode(args.city)


def print_summary(rows, args, requests_spent):
    if not rows:
        print("\nNo businesses matched the filters.")
        return

    by_issue = Counter(r["issue"] for r in rows)
    by_tier = Counter(r["tier"] for r in rows)

    print(f"\n{'='*78}\n  {len(rows)} leads  |  {requests_spent} API requests spent\n{'='*78}")

    print("\n  BY WEBSITE PROBLEM")
    for issue, n in sorted(by_issue.items(), key=lambda kv: -kv[1]):
        pts = scoring.ISSUE_SCORE.get(issue, 10)
        bar = "#" * min(34, n)
        print(f"    {issue:<20} {n:>4}  {bar}  ({pts} pts)")

    print("\n  BY TIER")
    for tier in ("A", "B", "C", "D"):
        if by_tier.get(tier):
            print(f"    {tier}  {by_tier[tier]:>4}")

    n = min(args.top, len(rows))
    print(f"\n  TOP {n}")
    print(f"    {'#':>3} {'score':>5} {'t':1} {'name':<32} {'issue':<20} "
          f"{'rev':>5} {'★':>4}  phone")
    print(f"    {'-'*3} {'-'*5} {'-'*1} {'-'*32} {'-'*20} {'-'*5} {'-'*4}  {'-'*14}")
    for i, r in enumerate(rows[:n], 1):
        print(f"    {i:>3} {r['score']:>5} {r['tier']:1} {r['name'][:32]:<32} "
              f"{r['issue']:<20} {r['review_count']:>5} {r['rating'] or '-':>4}  "
              f"{r['phone'] or ''}")

    a_and_b = by_tier.get("A", 0) + by_tier.get("B", 0)
    print(f"\n  {a_and_b} A/B-tier leads worth calling. Full list -> {args.out}")


def main(argv=None):
    args = parse_args(argv)
    load_dotenv()

    if args.source == "google":
        key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not key:
            sys.exit("error: GOOGLE_MAPS_API_KEY not set (copy .env.example to .env)")
        client = GooglePlaces(key, budget=args.max_requests,
                              use_cache=not args.no_cache)
    else:
        token = os.getenv("APIFY_TOKEN")
        if not token:
            sys.exit("error: APIFY_TOKEN not set")
        client = Apify(token)

    print(f"\n  query   {args.query!r}" if args.query
          else "\n  query   (blank - every business in the area)")

    if args.source == "apify":
        if not args.city:
            sys.exit("error: --source apify needs --city")
        print(f"  area    {args.city}")
        places = client.harvest_by_location(args.query, args.city)
    else:
        area = resolve_area(client, args)
        print(f"  area    {area}")
        print(f"  budget  {args.max_requests} requests "
              f"({client.spent} used resolving the area)")
        if args.dry_run:
            print("\n  --dry-run: stopping before the sweep.")
            print(f"  Worst case this run costs {args.max_requests} of your "
                  f"1,000 monthly Enterprise requests.")
            print(f"  Cache currently holds: {cache.stats() or 'nothing'}")
            return 0
        print("\n  sweeping:")
        places = client.harvest(args.query, area, min_cell_km=args.min_cell_km)

    kept = [p for p in places
            if scoring.passes_filter(p, args.min_rating, args.min_reviews)]
    print(f"  {len(kept)} of {len(places)} pass "
          f"(>={args.min_rating}★, >={args.min_reviews} reviews, operating)")

    if args.no_audit:
        audits = [audit_mod.Audit(url=p.website,
                                  issue=audit_mod.classify_url(p.website) or "OK")
                  for p in kept]
    else:
        needs_fetch = sum(1 for p in kept if audit_mod.classify_url(p.website) is None)
        print(f"\n  auditing {len(kept)} listings ({needs_fetch} need a fetch)...")
        audits = asyncio.run(audit_mod.audit_all(
            [p.website for p in kept],
            concurrency=args.concurrency,
            use_cache=not args.no_cache,
        ))

    rows = []
    for place, a in zip(kept, audits):
        s = scoring.score(place, a)
        rows.append({
            "score": s["score"], "tier": s["tier"], "issue": a.issue,
            "name": place.name, "phone": place.phone or "",
            "website": place.website or "", "rating": place.rating,
            "review_count": place.review_count, "category": place.category,
            "address": place.address, "maps_url": place.maps_uri,
            "website_score": s["website_score"],
            "business_score": s["business_score"],
            "reviews_pts": s["reviews_pts"], "rating_pts": s["rating_pts"],
            "phone_pts": s["phone_pts"], "high_ticket_pts": s["high_ticket_pts"],
            "platform": a.platform, "mobile_ready": a.mobile_ready,
            "https": a.https, "load_seconds": round(a.load_seconds, 2),
            "copyright_year": a.copyright_year or "", "has_booking": a.has_booking,
            "has_form": a.has_form, "title": a.title, "note": a.note,
            "place_id": place.place_id,
        })

    rows.sort(key=lambda r: -r["score"])

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    print_summary(rows, args, client.spent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
