"""SQLite state: leads, outreach progress, settings, and the request ledger.

Everything the app needs to be portable lives here, so the whole directory can
be copied to another machine and still work.
"""

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "business-finder.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    place_id       TEXT PRIMARY KEY,
    name           TEXT, address TEXT, phone TEXT, website TEXT,
    rating         REAL, review_count INTEGER,
    category       TEXT, maps_uri TEXT,
    issue          TEXT,
    is_high_ticket INTEGER DEFAULT 0,
    platform       TEXT, mobile_ready INTEGER, https INTEGER,
    load_seconds   REAL, copyright_year INTEGER, has_booking INTEGER,
    site_title     TEXT, audit_note TEXT,
    query          TEXT, city TEXT,
    first_seen     TEXT, last_seen TEXT
);

-- Separate table so re-harvesting a city never clobbers call history.
CREATE TABLE IF NOT EXISTS outreach (
    place_id       TEXT PRIMARY KEY,
    status         TEXT DEFAULT 'new',
    notes          TEXT DEFAULT '',
    last_contacted TEXT,
    updated_at     TEXT
);

-- Which searches turned up which business. A business found by two sweeps
-- gets a row per sweep, so filtering by search stays exact once areas overlap.
CREATE TABLE IF NOT EXISTS lead_searches (
    place_id   TEXT NOT NULL,
    query      TEXT NOT NULL,
    city       TEXT NOT NULL,
    first_seen TEXT,
    last_seen  TEXT,
    PRIMARY KEY (place_id, query, city)
);

-- Only real API calls land here; cache hits cost nothing and aren't logged.
CREATE TABLE IF NOT EXISTS requests_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    at    TEXT NOT NULL,
    count INTEGER NOT NULL,
    query TEXT, city TEXT,
    sku   TEXT NOT NULL DEFAULT 'text'
);

CREATE INDEX IF NOT EXISTS idx_leads_issue  ON leads(issue);
CREATE INDEX IF NOT EXISTS idx_leads_city   ON leads(city);
CREATE INDEX IF NOT EXISTS idx_reqlog_at    ON requests_log(at);
CREATE INDEX IF NOT EXISTS idx_ls_search    ON lead_searches(query, city);
CREATE INDEX IF NOT EXISTS idx_ls_place     ON lead_searches(place_id);
"""

STATUSES = ["new", "called", "no answer", "interested", "dead"]

# Each SKU has its own monthly free allowance, so a keyword sweep and a blank
# sweep draw from different pools. One combined counter would mislead.
FREE_TIER = {"text": 1000, "nearby": 1000, "geocode": 10000}
SKU_LABEL = {"text": "keyword search", "nearby": "blank search",
             "geocode": "geocoding"}
FREE_TIER_MONTHLY = 1000  # kept for the CLI's simpler view


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migrate databases created before per-SKU tracking existed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(requests_log)")}
    if "sku" not in cols:
        conn.execute("ALTER TABLE requests_log ADD COLUMN sku TEXT NOT NULL DEFAULT 'text'")
        conn.commit()

    # Backfill the join table from leads recorded before it existed. Each such
    # lead knows only the first search that found it; that's the best we have.
    empty = conn.execute("SELECT COUNT(*) AS n FROM lead_searches").fetchone()["n"] == 0
    have_leads = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"] > 0
    if empty and have_leads:
        conn.execute(
            "INSERT OR IGNORE INTO lead_searches(place_id,query,city,first_seen,last_seen) "
            "SELECT place_id, COALESCE(query,''), COALESCE(city,''), first_seen, last_seen "
            "FROM leads"
        )
        conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- settings ----------

def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def api_key(conn) -> str | None:
    """.env wins if present, so an existing CLI setup keeps working untouched."""
    return os.getenv("GOOGLE_MAPS_API_KEY") or get_setting(conn, "google_api_key")


def api_key_source(conn) -> str:
    if os.getenv("GOOGLE_MAPS_API_KEY"):
        return "env"
    if get_setting(conn, "google_api_key"):
        return "stored"
    return "missing"


def mask(key: str | None) -> str:
    """Never hand the full key back to the browser."""
    if not key:
        return ""
    return f"{key[:4]}{'•' * 8}{key[-4:]}" if len(key) > 10 else "•" * len(key)


# ---------- request ledger ----------

def log_requests(conn, spend: dict, query: str = "", city: str = "") -> None:
    """spend is {sku: count}; only non-zero SKUs are recorded."""
    now = _now()
    for sku, count in spend.items():
        if count > 0:
            conn.execute(
                "INSERT INTO requests_log(at,count,query,city,sku) VALUES(?,?,?,?,?)",
                (now, count, query, city, sku),
            )
    conn.commit()


def month_spend(conn) -> dict:
    """Per-SKU spend this calendar month.

    The per-run cap doesn't stop ten runs adding up, so this is what tells you
    where you actually stand against each free allowance.
    """
    start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT sku, COALESCE(SUM(count),0) AS n FROM requests_log "
        "WHERE at >= ? GROUP BY sku", (start,)
    ).fetchall()
    counts = {r["sku"]: r["n"] for r in rows}

    skus = {}
    for sku, limit in FREE_TIER.items():
        used = counts.get(sku, 0)
        skus[sku] = {
            "label": SKU_LABEL[sku], "used": used, "limit": limit,
            "remaining": max(0, limit - used),
            "pct": round(100 * used / limit, 1),
        }
    return {
        "skus": skus,
        "month": datetime.now(timezone.utc).strftime("%B %Y"),
        # searches only; geocoding has its own far larger allowance
        "search_used": counts.get("text", 0) + counts.get("nearby", 0),
    }


# ---------- leads ----------

def upsert_leads(conn, rows: list[dict], query: str, city: str) -> int:
    now = _now()
    for r in rows:
        conn.execute(
            """
            INSERT INTO leads (place_id,name,address,phone,website,rating,
                review_count,category,maps_uri,issue,is_high_ticket,platform,
                mobile_ready,https,load_seconds,copyright_year,has_booking,
                site_title,audit_note,query,city,first_seen,last_seen)
            VALUES (:place_id,:name,:address,:phone,:website,:rating,
                :review_count,:category,:maps_uri,:issue,:is_high_ticket,:platform,
                :mobile_ready,:https,:load_seconds,:copyright_year,:has_booking,
                :site_title,:audit_note,:query,:city,:now,:now)
            ON CONFLICT(place_id) DO UPDATE SET
                name=excluded.name, phone=excluded.phone, website=excluded.website,
                rating=excluded.rating, review_count=excluded.review_count,
                issue=excluded.issue, platform=excluded.platform,
                mobile_ready=excluded.mobile_ready, https=excluded.https,
                load_seconds=excluded.load_seconds,
                copyright_year=excluded.copyright_year,
                has_booking=excluded.has_booking, site_title=excluded.site_title,
                audit_note=excluded.audit_note, last_seen=excluded.last_seen
            """,
            {**r, "query": query, "city": city, "now": now},
        )
        conn.execute(
            "INSERT INTO lead_searches(place_id,query,city,first_seen,last_seen) "
            "VALUES(?,?,?,?,?) ON CONFLICT(place_id,query,city) "
            "DO UPDATE SET last_seen=excluded.last_seen",
            (r["place_id"], query, city, now, now),
        )
    conn.commit()
    return len(rows)


def all_leads(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT l.*,
               COALESCE(o.status,'new') AS status,
               COALESCE(o.notes,'')     AS notes,
               o.last_contacted
        FROM leads l LEFT JOIN outreach o ON o.place_id = l.place_id
        """
    ).fetchall()
    out = [dict(r) for r in rows]
    pairs = conn.execute("SELECT place_id, query, city FROM lead_searches").fetchall()
    by_place: dict[str, list[str]] = {}
    for p in pairs:
        by_place.setdefault(p["place_id"], []).append(search_key(p["query"], p["city"]))
    for r in out:
        r["searches"] = by_place.get(r["place_id"], [])
    return out


def set_outreach(conn, place_id: str, status=None, notes=None) -> dict:
    cur = conn.execute(
        "SELECT * FROM outreach WHERE place_id=?", (place_id,)
    ).fetchone()
    status = status if status is not None else (cur["status"] if cur else "new")
    notes = notes if notes is not None else (cur["notes"] if cur else "")

    # Stamp the contact date the first time it leaves 'new'.
    last = cur["last_contacted"] if cur else None
    if status != "new" and not last:
        last = _now()

    conn.execute(
        "INSERT INTO outreach(place_id,status,notes,last_contacted,updated_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(place_id) DO UPDATE SET "
        "status=excluded.status, notes=excluded.notes, "
        "last_contacted=excluded.last_contacted, updated_at=excluded.updated_at",
        (place_id, status, notes, last, _now()),
    )
    conn.commit()
    return {"place_id": place_id, "status": status, "notes": notes,
            "last_contacted": last}


def search_key(query: str, city: str) -> str:
    return f"{query}\u241f{city}"


def search_label(query: str, city: str) -> str:
    return f"{query or '(all businesses)'} — {city}"


def searches(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT query, city, COUNT(*) AS n, MAX(last_seen) AS last_seen "
        "FROM lead_searches GROUP BY query, city ORDER BY last_seen DESC"
    ).fetchall()
    return [
        {"key": search_key(r["query"], r["city"]), "query": r["query"],
         "city": r["city"], "label": search_label(r["query"], r["city"]),
         "count": r["n"], "last_seen": r["last_seen"]}
        for r in rows
    ]


def delete_search(conn, query: str, city: str) -> dict:
    """Drop one search. Leads still claimed by another search survive, and
    outreach rows are always kept so call history isn't lost if a business
    turns up again in a later sweep."""
    removed = conn.execute(
        "DELETE FROM lead_searches WHERE query=? AND city=?", (query, city)
    ).rowcount
    orphaned = conn.execute(
        "DELETE FROM leads WHERE place_id NOT IN (SELECT place_id FROM lead_searches)"
    ).rowcount
    conn.commit()
    return {"unlinked": removed, "deleted": orphaned,
            "kept": removed - orphaned, "outreach_preserved": True}


def cities(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT city FROM leads WHERE city != '' ORDER BY city"
    ).fetchall()
    return [r["city"] for r in rows]
