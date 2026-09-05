#!/usr/bin/env python3
"""Local web UI for business-finder.

  ./venv/bin/python app.py            ->  http://127.0.0.1:5111
  ./venv/bin/python app.py --port 8080

Binds to loopback only, on purpose. The API key is stored server-side and has
billing attached; anything that makes this reachable from another machine lets
a stranger spend your quota. Don't change the host.
"""

import argparse
import asyncio
import csv
import io
import os
import threading
import traceback

from types import SimpleNamespace

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

import audit as audit_mod
import cache
import db
import scoring
from grid import Rect
from sources import BudgetExhausted, GooglePlaces

load_dotenv()
app = Flask(__name__)
conn = db.connect()

# Single-user local app, so one in-flight job is enough.
JOB = {
    "running": False, "done": False, "phase": "idle", "log": [],
    "spent": 0, "budget": 0, "found": 0, "kept": 0, "error": None,
}
JOB_LOCK = threading.Lock()


def reset_job(budget: int) -> None:
    with JOB_LOCK:
        JOB.update({"running": True, "done": False, "phase": "starting",
                    "log": [], "spent": 0, "budget": budget, "found": 0,
                    "kept": 0, "error": None})


def say(phase: str, line: str = "") -> None:
    with JOB_LOCK:
        JOB["phase"] = phase
        if line:
            JOB["log"].append(line)
            del JOB["log"][:-400]


def run_search(params: dict) -> None:
    """Sweep each requested vertical, sharing one budget and one geocode.

    Every vertical is stored as its own search, so comparing them falls out of
    the lead_searches table rather than needing separate runs.
    """
    try:
        key = db.api_key(conn)
        if not key:
            raise RuntimeError("No API key set. Add one in Settings.")

        client = GooglePlaces(key, budget=params["max_requests"], use_cache=True)
        queries, city = params["queries"], params["city"]

        say("resolving", f"Resolving {city}...")
        info = client.geocode_details(city, params.get("radius_km"))
        area = info["rect"]
        say("resolving", f"Resolved to: {info['formatted']}")
        say("resolving", f"Area: {info['width_km']}x{info['height_km']}km"
                         + (f" (fixed {params['radius_km']}km radius)"
                            if params.get("radius_km") else " (Google viewport)"))
        if len(queries) > 1:
            say("resolving", f"{len(queries)} verticals sharing "
                             f"{params['max_requests']} requests")

        results = []
        for qi, query in enumerate(queries):
            label = query or "(all businesses)"
            # Divide what's left among the verticals still to run, so a sparse
            # one hands its unused budget to the next rather than wasting it.
            left = params["max_requests"] - client.spent
            allowance = max(1, left // (len(queries) - qi))
            client.budget = client.spent + allowance
            say("sweeping", f"\n=== {qi + 1}/{len(queries)}  {label} "
                            f"(up to {allowance} requests) ===")
            results.append(run_one(client, query, city, area, params))

        if len(queries) > 1:
            say("done", "\n=== comparison ===")
            say("done", f"  {'vertical':<26}{'leads':>7}{'A':>5}{'reqs':>6}")
            for r in results:
                say("done", f"  {r['label'][:26]:<26}{r['leads']:>7}"
                            f"{r['tier_a']:>5}{r['requests']:>6}")
        say("done", f"\nFinished. {client.spent} requests spent.")
        with JOB_LOCK:
            JOB.update({"running": False, "done": True, "phase": "done"})

    except Exception as e:
        traceback.print_exc()
        with JOB_LOCK:
            JOB.update({"running": False, "done": True, "phase": "error",
                        "error": f"{type(e).__name__}: {e}"})


def run_one(client, query, city, area, params) -> dict:
    """One vertical: sweep, filter, audit, score, store."""
    label = query or "(all businesses)"

    def on_cell(info):
        with JOB_LOCK:
            JOB.update({"spent": info["spent"], "found": info["total"]})
        flag = "FULL" if info["saturated"] else "    "
        say("sweeping",
            f"  [{info['spent']:>3}/{params['max_requests']}] {flag} "
            f"{info['cell']} -> {info['found']:>2} ({info['new']} new) "
            f"| total {info['total']}")

    if not query:
        say("sweeping", "  blank search: every business here "
                        "(Nearby Search, 20 per cell, no paging)")

    before = dict(client.spend)
    spent_before = client.spent
    try:
        places = client.harvest(query, area, min_cell_km=params["min_cell_km"],
                                verbose=False, on_cell=on_cell)
    except BudgetExhausted as e:
        say("sweeping", f"  budget stop: {e}")
        places = []

    delta = {k: v - before.get(k, 0) for k, v in client.spend.items()}
    db.log_requests(conn, delta, query, city)
    used = client.spent - spent_before
    say("filtering", f"  {len(places)} unique businesses")

    kept = [p for p in places
            if scoring.passes_filter(p, params["min_rating"], params["min_reviews"])]
    with JOB_LOCK:
        JOB["kept"] = len(kept)
    say("filtering", f"  {len(kept)} pass filters")

    if kept:
        needs = sum(1 for p in kept if audit_mod.classify_url(p.website) is None)
        say("auditing", f"  auditing {len(kept)} sites ({needs} need a fetch)...")
        audits = asyncio.run(audit_mod.audit_all(
            [p.website for p in kept], concurrency=20, use_cache=True))
    else:
        audits = []

    rows, tier_a = [], 0
    for place, a in zip(kept, audits):
        s = scoring.score(place, a)
        if s["tier"] == "A":
            tier_a += 1
        rows.append({
            "place_id": place.place_id, "name": place.name,
            "address": place.address, "phone": place.phone or "",
            "website": place.website or "", "rating": place.rating,
            "review_count": place.review_count, "category": place.category,
            "maps_uri": place.maps_uri, "issue": a.issue,
            "is_high_ticket": int(scoring.is_high_ticket(place)),
            "platform": a.platform, "mobile_ready": int(a.mobile_ready),
            "https": int(a.https), "load_seconds": round(a.load_seconds, 2),
            "copyright_year": a.copyright_year, "has_booking": int(a.has_booking),
            "site_title": a.title, "audit_note": a.note,
        })

    db.upsert_leads(conn, rows, query, city)
    say("filtering", f"  stored {len(rows)} leads, {tier_a} tier A")
    return {"label": label, "leads": len(rows), "tier_a": tier_a, "requests": used}


# ---------- routes ----------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def config():
    """Scoring constants live server-side so the browser re-ranks with the
    exact same numbers the CLI uses."""
    return jsonify({
        "issue_scores": scoring.ISSUE_SCORE,
        "weights": {
            "reviews": scoring.W_REVIEWS, "rating": scoring.W_RATING,
            "phone": scoring.W_PHONE, "high_ticket": scoring.W_HIGH_TICKET,
            "review_saturation": scoring.REVIEW_SATURATION,
        },
        "tiers": scoring.TIERS,
        "statuses": db.STATUSES,
        "key": {"source": db.api_key_source(conn),
                "masked": db.mask(db.api_key(conn))},
        "spend": db.month_spend(conn),
        "cities": db.cities(conn),
        "searches": db.searches(conn),
        "cache": cache.stats(),
    })


@app.post("/api/key")
def save_key():
    key = (request.json or {}).get("key", "").strip()
    if not key:
        return jsonify({"error": "Key is empty"}), 400
    db.set_setting(conn, "google_api_key", key)
    return jsonify({"ok": True, "masked": db.mask(key),
                    "source": db.api_key_source(conn)})


@app.post("/api/key/test")
def test_key():
    """One geocode call - the cheapest way to prove the key works before a
    sweep fails halfway through."""
    key = db.api_key(conn)
    if not key:
        return jsonify({"ok": False, "error": "No key set"}), 400
    try:
        client = GooglePlaces(key, budget=1, use_cache=False)
        rect = client.geocode("Austin, TX")
        db.log_requests(conn, client.spend, "key test", "")
        return jsonify({"ok": True, "message": f"Key works. Resolved {rect}",
                        "spend": db.month_spend(conn)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400


@app.post("/api/geocode")
def geocode_check():
    """Resolve a location and report what was found, before any sweep spends
    Text/Nearby quota. Geocoding is a separate SKU with a 10,000/month
    allowance and results are cached, so checking is effectively free."""
    key = db.api_key(conn)
    if not key:
        return jsonify({"error": "No API key set"}), 400
    d = request.json or {}
    where = (d.get("city") or "").strip()
    if not where:
        return jsonify({"error": "Enter a location first"}), 400
    try:
        client = GooglePlaces(key, budget=1, use_cache=True)
        info = client.geocode_details(where, d.get("radius_km") or None)
        db.log_requests(conn, client.spend, "geocode check", where)
        info.pop("rect")
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 400


@app.post("/api/search")
def start_search():
    with JOB_LOCK:
        if JOB["running"]:
            return jsonify({"error": "A search is already running"}), 409
    d = request.json or {}
    # query may be blank: that means "every business here" via Nearby Search
    if not d.get("city"):
        return jsonify({"error": "A location is required"}), 400

    # One field, many verticals: split on newlines or commas. An empty result
    # means one blank sweep (every business), which is a valid request.
    raw = d.get("query") or ""
    queries = [q.strip() for q in raw.replace("\n", ",").split(",") if q.strip()]
    if not queries:
        queries = [""]

    params = {
        "queries": queries, "city": d["city"].strip(),
        "min_rating": float(d.get("min_rating", 4.0)),
        "min_reviews": int(d.get("min_reviews", 10)),
        "max_requests": int(d.get("max_requests", 40)),
        "min_cell_km": float(d.get("min_cell_km", 2.0)),
        "radius_km": float(d["radius_km"]) if d.get("radius_km") else None,
    }
    reset_job(params["max_requests"])
    threading.Thread(target=run_search, args=(params,), daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/search/status")
def search_status():
    with JOB_LOCK:
        state = dict(JOB)
    state["spend"] = db.month_spend(conn)
    return jsonify(state)


@app.post("/api/searches/delete")
def delete_search():
    d = request.json or {}
    if "city" not in d:
        return jsonify({"error": "city is required"}), 400
    return jsonify(db.delete_search(conn, d.get("query", ""), d["city"]))


@app.get("/api/compare")
def compare():
    """Per-search stats, so verticals can be judged side by side rather than
    by flicking through the filter dropdown."""
    leads = db.all_leads(conn)
    by_key = {}
    for l in leads:
        for key in l.get("searches", []):
            by_key.setdefault(key, []).append(l)

    out = []
    for s in db.searches(conn):
        group = by_key.get(s["key"], [])
        tiers = {"A": 0, "B": 0, "C": 0, "D": 0}
        issues = {}
        best = None
        for l in group:
            place = SimpleNamespace(
                review_count=l["review_count"], rating=l["rating"],
                phone=l["phone"], category=l["category"], name=l["name"])
            a = SimpleNamespace(issue=l["issue"])
            sc = scoring.score(place, a)
            tiers[sc["tier"]] += 1
            issues[l["issue"]] = issues.get(l["issue"], 0) + 1
            if best is None or sc["score"] > best["score"]:
                best = {"name": l["name"], "score": sc["score"], "issue": l["issue"]}
        spend = conn.execute(
            "SELECT COALESCE(SUM(count),0) AS n FROM requests_log WHERE query=? AND city=?",
            (s["query"], s["city"]),
        ).fetchone()["n"]
        out.append({
            **s, "tiers": tiers, "best": best, "requests": spend,
            "top_issues": sorted(issues.items(), key=lambda kv: -kv[1])[:3],
            # the number that actually decides which vertical to work
            "a_per_100_reqs": round(100 * tiers["A"] / spend, 1) if spend else None,
        })
    out.sort(key=lambda r: -r["tiers"]["A"])
    return jsonify(out)


@app.get("/api/leads")
def leads():
    return jsonify(db.all_leads(conn))


@app.post("/api/lead/<place_id>/outreach")
def outreach(place_id):
    d = request.json or {}
    status = d.get("status")
    if status is not None and status not in db.STATUSES:
        return jsonify({"error": f"bad status {status!r}"}), 400
    return jsonify(db.set_outreach(conn, place_id, status, d.get("notes")))


@app.get("/api/export.csv")
def export():
    rows = db.all_leads(conn)
    if not rows:
        return Response("", mimetype="text/csv")
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return Response(
        buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


if __name__ == "__main__":
    # Not 5000: macOS ControlCenter (AirPlay Receiver) squats on that port and
    # the resulting "Address already in use" is easy to misread as your own
    # stale process.
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", 5111)))
    cli_args = ap.parse_args()

    spend = db.month_spend(conn)
    print(f"\n  business-finder  ->  http://127.0.0.1:{cli_args.port}")
    print(f"  API key: {db.api_key_source(conn)}")
    for sku in ("text", "nearby"):
        d = spend["skus"][sku]
        print(f"  {spend['month']} {d['label']}: {d['used']}/{d['limit']}")
    print()
    # host is loopback deliberately - see module docstring.
    app.run(host="127.0.0.1", port=cli_args.port, debug=False)
