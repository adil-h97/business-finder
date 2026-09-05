# business-finder

Finds local businesses that have **strong Google reviews but a weak or missing
website**, scores them by how easy the sales conversation will be, and gives you
somewhere to work the list.

Built for anyone selling websites, local SEO, or booking systems to small
businesses. It runs entirely on your own machine against your own Google API
key, and stays inside Google's free tier if you set the quota cap.

```
┌─ Discover ──────────────────────────────────────────────┐
│  sweep an area, one or many verticals at a time         │
└─────────────────────────┬───────────────────────────────┘
                          ▼
┌─ Leads ─────────────────────────────────────────────────┐
│  ranked, filtered, with the problem named in plain       │
│  English and an opening line written for you             │
└─────────────────────────┬───────────────────────────────┘
                          ▼
┌─ Pipeline ──────────────────────────────────────────────┐
│  called · no answer · interested · dead, with notes      │
└──────────────────────────────────────────────────────────┘
```

## Why the scoring is shaped this way

Two things make this different from a generic scraper:

**A business with *no* website is not the best lead.** Plenty of them are booked
solid from Google Maps and know it — the answer to your call is "I have more work
than I can handle." A business whose listing points at a *dead domain* or a
*Facebook page* has already decided it wants a web presence and currently has a
broken one. You're replacing something, not selling an idea. So `NO_WEBSITE`
scores **below** every broken-website case.

**Review count predicts revenue; star rating doesn't.** 4.9 from 12 reviews is a
hobbyist. 4.3 from 400 is a real business. Rating gets a token weight to break
ties, nothing more.

## Setup

```bash
git clone <your-fork-url> business-finder
cd business-finder
./setup.sh
./venv/bin/python app.py        # → http://127.0.0.1:5111
```

Then open Settings and paste a Google API key.

### Getting a key, and keeping it free

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com)
   and attach a billing account. A card is required even for the free tier.
2. Enable **[Places API (New)](https://console.cloud.google.com/apis/library/places.googleapis.com)**
   and **[Geocoding API](https://console.cloud.google.com/apis/library/geocoding-backend.googleapis.com)**.
   ⚠️ There are two products called "Places API" — you need the **(New)** one.
3. Create an API key under Credentials. Restrict it to those two APIs.
4. **Set a quota cap:** APIs & Services → Places API (New) → Quotas → Text Search
   Enterprise → about **33/day**. This is what makes £0 a guarantee rather than
   a hope.
5. Add a **£1 budget alert** under Billing → Budgets. It fires the moment
   anything is billed at all.

The key can go in the Settings page (stored in a local SQLite file) or in a
`.env` — see `.env.example`. `.env` wins if both are set.

### The budget

Free tier is **1,000 requests/month per SKU**. `rating`, `userRatingCount` and
`websiteUri` are all Enterprise-tier fields, so every keyword search bills at
Text Search Enterprise. Blank searches bill to Nearby Search Enterprise, a
*separate* allowance. Realistically that's **5,000–10,000 unique businesses a
month** — cells come back partial and overlapping cells return duplicates, so
you never get the theoretical 20,000.

Verify current limits in your own console; Google has changed this pricing before.

## How it works

```
RESOLVE   location → bounding box (Geocoding API)
HARVEST   adaptive grid sweep → dedupe by place_id      ← costs requests
AUDIT     fetch every website found, grade it           ← free
SCORE     rank, store
```

**Adaptive grid.** Text Search returns 20 results per request, 60 with paging, so
a metro needs many searches. A uniform grid wastes requests on empty cells. This
starts coarse and subdivides only cells that came back *full* — a full page means
the cell is saturated and hiding results.

**Caching.** Everything lands in `.cache/`. Re-running a query or retuning the
scoring costs zero requests. Search results expire after 25 days to stay inside
Google's 30-day content-caching limit.

**The audit stage is free forever** — it's plain HTTP from your machine, and it
produces the highest-value bucket.

## What it detects

| problem | pts | |
|---|---|---|
| Social profile as website | 50 | Facebook/Instagram/Linktree listed as the site |
| Dead Google-built site | 50 | `business.site` — Google killed these in 2024 |
| Domain doesn't exist | 47 | no DNS record at all |
| Domain parked | 46 | registrar placeholder |
| Returns an error | 44 | 404/410 |
| Broken SSL | 44 | full-page browser warning |
| Server erroring | 43 | 5xx |
| No HTTPS | 41 | Chrome shows "Not secure" |
| Not mobile-ready | 39 | no viewport tag |
| Free builder subdomain | 35 | `*.wixsite.com` etc |
| **No website at all** | **30** | see reasoning above |
| Almost no content | 28 | |
| Timed out | 26 | low confidence — verify manually |
| Looks abandoned | 24 | copyright 3+ years old |
| Slow | 20 | >3s |
| Couldn't check | 12 | blocked us (403/429) — tells us nothing |
| No obvious problem | 5 | |

Each one carries an explanation of why it costs the owner money, plus a
generated opening line using that business's real numbers.

## CLI

The web UI is the main interface, but the pipeline is scriptable:

```bash
./venv/bin/python finder.py --query "roofing contractor" --city "Austin, TX" \
    --min-reviews 25 --max-requests 40 --out leads.csv
```

`--dry-run` resolves the area and shows the budget without spending anything.
Omit `--query` to sweep every business in the area.

## Known limits

- **No review recency.** Whether a business is still active would be a strong
  filter, but review timestamps sit in the pricier Enterprise+Atmosphere SKU.
- **`NOT_MOBILE` has false positives.** A JS-rendered site can be responsive
  without a viewport tag in the initial HTML. This fetches raw HTML, no browser.
- **No chain detection.** A Holiday Inn scores like an independent hotel.
- **Blank search skews prominent.** Nearby Search returns the best-known places
  in each cell, which are exactly the chains you can't sell to. Good for
  reconnaissance, poor for lead-finding.
- **Category matching is keyword-based** — add terms to `HIGH_TICKET` in
  `scoring.py` for your market.

## Data

Everything lives in `business-finder.db` (SQLite) and `.cache/`. Both are
gitignored. Copy the directory to another machine and your leads, call history
and key come with it.

The app binds to `127.0.0.1` only. The stored key has billing attached — do not
expose this to a network.

## Licence

MIT — see [LICENSE](LICENSE).
