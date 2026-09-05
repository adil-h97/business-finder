"""Lead sources. Google Places (New) is the default; Apify is the fallback
for higher volume or when you don't want a card on file.

Both emit the same `Place` shape, so swapping is a flag, not a rewrite.
"""

import math
import time
from dataclasses import dataclass, asdict

import httpx

import cache
from grid import KM_PER_DEG_LAT

PAGE_SIZE = 20  # API maximum for searchText and searchNearby

# A blank search returns every place type in the circle, including things
# nobody sells a website to. Filtered locally on primaryType rather than with
# the API's excludedTypes, because one bad type name errors the whole request.
NOISE_TYPES = {
    "bus_stop", "bus_station", "transit_station", "transit_depot",
    "train_station", "subway_station", "light_rail_station", "taxi_stand",
    "airport", "international_airport", "heliport", "ferry_terminal",
    "parking", "rest_stop", "atm", "electric_vehicle_charging_station",
    "park", "national_park", "state_park", "dog_park", "playground",
    "cemetery", "city_hall", "courthouse", "embassy", "fire_station",
    "police", "post_office", "local_government_office",
    "primary_school", "secondary_school", "school", "university",
    "church", "mosque", "synagogue", "hindu_temple", "place_of_worship",
    "hospital", "historical_landmark", "monument", "tourist_attraction",
    "apartment_complex", "apartment_building", "housing_complex",
    "parking_lot", "parking_garage", "stadium", "arena", "museum",
    "public_bathroom", "storage", "self_storage",
}


def is_noise(place) -> bool:
    """primaryType is the reliable signal, but Google omits it for some places
    (a car park slipped through that way), so fall back to the display
    category normalised to the same shape."""
    if place.primary_type and place.primary_type in NOISE_TYPES:
        return True
    if not place.primary_type and place.category:
        return place.category.strip().lower().replace(" ", "_") in NOISE_TYPES
    return False

# Every one of these is an Enterprise-tier field, so the whole request bills at
# Enterprise (highest tier in the mask wins). rating/userRatingCount/websiteUri
# are the entire point of this tool, so there is no cheaper mask available.
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.businessStatus",
        "places.googleMapsUri",
        "places.primaryTypeDisplayName",
        "places.primaryType",
        "nextPageToken",
    ]
)


@dataclass
class Place:
    place_id: str
    name: str
    address: str
    lat: float
    lng: float
    rating: float | None
    review_count: int
    website: str | None
    phone: str | None
    category: str
    maps_uri: str
    status: str
    primary_type: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class BudgetExhausted(Exception):
    pass


class GooglePlaces:
    """Places API (New). Free tier is 1,000 Enterprise requests/month.

    Set a hard quota cap in GCP (APIs & Services -> Quotas) as well; `budget`
    here protects a single run, the GCP cap protects your wallet.
    """

    SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
    NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self, api_key: str, budget: int = 100, use_cache: bool = True):
        self.api_key = api_key
        self.budget = budget
        self.spent = 0
        # Text Search Enterprise and Nearby Search Enterprise are distinct SKUs
        # with separate free allowances. Geocoding is a third. Counted apart.
        self.spend = {"text": 0, "nearby": 0, "geocode": 0}
        self.cache_hits = 0
        self.use_cache = use_cache
        self.client = httpx.Client(timeout=30.0)

    def _spend(self, n: int = 1, sku: str = "text") -> None:
        self.spend[sku] += n
        if sku == "geocode":
            return  # its own SKU with a far larger allowance; not budget-capped
        if self.spent + n > self.budget:
            self.spend[sku] -= n
            raise BudgetExhausted(
                f"request budget of {self.budget} exhausted "
                f"(raise with --max-requests if you have allowance left)"
            )
        self.spent += n

    def geocode(self, place_name: str, radius_km: float | None = None):
        """Place name -> bounding box."""
        return self.geocode_details(place_name, radius_km)["rect"]

    def geocode_details(self, place_name: str, radius_km: float | None = None) -> dict:
        """Resolve a place and say what was actually found.

        Google's viewport varies enormously with how specific the query is - a
        postcode gives a tight box, a city name a huge one - so the caller
        needs to see what it got. radius_km overrides the viewport with a fixed
        box around the centre when you want a predictable area.
        """
        from grid import Rect

        key = f"geocode:{place_name}"
        cached = cache.get("geocode", key, cache.SEARCH_TTL) if self.use_cache else None
        if cached:
            return self._finish_geocode(cached, radius_km)

        self._spend(sku="geocode")
        r = self.client.get(
            self.GEOCODE_URL, params={"address": place_name, "key": self.api_key}
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "OK" or not data.get("results"):
            raise RuntimeError(
                f"could not geocode {place_name!r}: {data.get('status')} "
                f"{data.get('error_message', '')}"
            )

        top = data["results"][0]
        vp = top["geometry"]["viewport"]
        loc = top["geometry"]["location"]
        blob = {
            "south": vp["southwest"]["lat"], "west": vp["southwest"]["lng"],
            "north": vp["northeast"]["lat"], "east": vp["northeast"]["lng"],
            "formatted": top.get("formatted_address", place_name),
            "types": top.get("types", []),
            "lat": loc["lat"], "lng": loc["lng"],
            "alternatives": [r.get("formatted_address", "")
                             for r in data["results"][1:4]],
        }
        if self.use_cache:
            cache.set("geocode", key, blob)
        return self._finish_geocode(blob, radius_km)

    @staticmethod
    def _finish_geocode(blob: dict, radius_km: float | None) -> dict:
        from grid import Rect

        lat = blob.get("lat", (blob["south"] + blob["north"]) / 2)
        lng = blob.get("lng", (blob["west"] + blob["east"]) / 2)
        if radius_km and radius_km > 0:
            dlat = radius_km / KM_PER_DEG_LAT
            dlng = radius_km / (111.320 * math.cos(math.radians(lat)) or 1e-9)
            rect = Rect(lat - dlat, lng - dlng, lat + dlat, lng + dlng)
        else:
            rect = Rect(blob["south"], blob["west"], blob["north"], blob["east"])
        return {
            "rect": rect,
            "formatted": blob.get("formatted", ""),
            "types": blob.get("types", []),
            "alternatives": [a for a in blob.get("alternatives", []) if a],
            "lat": lat, "lng": lng,
            "width_km": round(rect.width_km(), 1),
            "height_km": round(rect.height_km(), 1),
            "maps_url": f"https://www.google.com/maps/@{lat},{lng},13z",
            "radius_km": radius_km or None,
        }

    def _search(self, query: str, rect, page_token: str | None = None):
        body = {
            "textQuery": query,
            "locationRestriction": {"rectangle": rect.to_api()},
            "pageSize": PAGE_SIZE,
        }
        if page_token:
            body["pageToken"] = page_token

        cache_key = f"{query}|{rect}|{page_token or ''}"
        if self.use_cache:
            cached = cache.get("search", cache_key, cache.SEARCH_TTL)
            if cached is not None:
                self.cache_hits += 1
                return [Place(**p) for p in cached["places"]], cached["next"]

        self._spend()
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }

        for attempt in range(4):
            resp = self.client.post(self.SEARCH_URL, json=body, headers=headers)
            if resp.status_code in (429, 500, 502, 503):
                time.sleep(2**attempt)
                continue
            break

        if resp.status_code != 200:
            raise RuntimeError(f"Places API {resp.status_code}: {resp.text[:400]}")

        data = resp.json()
        places = [self._parse(p) for p in data.get("places", [])]
        next_token = data.get("nextPageToken")

        if self.use_cache:
            cache.set(
                "search",
                cache_key,
                {"places": [p.as_dict() for p in places], "next": next_token},
            )
        return places, next_token

    @staticmethod
    def _parse(p: dict) -> Place:
        loc = p.get("location", {})
        return Place(
            place_id=p.get("id", ""),
            name=p.get("displayName", {}).get("text", ""),
            address=p.get("formattedAddress", ""),
            lat=loc.get("latitude", 0.0),
            lng=loc.get("longitude", 0.0),
            rating=p.get("rating"),
            review_count=p.get("userRatingCount", 0),
            website=p.get("websiteUri"),
            phone=p.get("nationalPhoneNumber"),
            category=p.get("primaryTypeDisplayName", {}).get("text", ""),
            maps_uri=p.get("googleMapsUri", ""),
            status=p.get("businessStatus", "UNKNOWN"),
            primary_type=p.get("primaryType", ""),
        )

    def _search_nearby(self, rect):
        """Blank search: every business inside the cell, no keyword.

        Nearby Search caps at 20 with no pagination, so unlike text search
        there's no way to squeeze more out of a saturated cell except to make
        the cell smaller.
        """
        mid_lat, mid_lng = rect.center
        # Circumscribe the cell so nothing in the corners is missed. Overlaps
        # neighbouring cells slightly; dedupe by place_id handles that.
        radius = min(50000.0, math.hypot(rect.width_km(), rect.height_km()) * 500)
        body = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": mid_lat, "longitude": mid_lng},
                    "radius": radius,
                }
            },
            "maxResultCount": PAGE_SIZE,
        }

        cache_key = f"__nearby__|{rect}|{radius:.0f}"
        if self.use_cache:
            cached = cache.get("search", cache_key, cache.SEARCH_TTL)
            if cached is not None:
                self.cache_hits += 1
                return [Place(**pl) for pl in cached["places"]]

        self._spend(sku="nearby")
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": FIELD_MASK.replace(",nextPageToken", ""),
        }
        for attempt in range(4):
            resp = self.client.post(self.NEARBY_URL, json=body, headers=headers)
            if resp.status_code in (429, 500, 502, 503):
                time.sleep(2**attempt)
                continue
            break
        if resp.status_code != 200:
            raise RuntimeError(f"Nearby Search {resp.status_code}: {resp.text[:400]}")

        places = [self._parse(pl) for pl in resp.json().get("places", [])]
        if self.use_cache:
            cache.set("search", cache_key, {"places": [pl.as_dict() for pl in places]})
        return places

    def harvest(self, query: str, root, min_cell_km: float = 2.0,
                verbose: bool = True, on_cell=None):
        """Adaptive sweep: split a cell only when it comes back full.

        A full page means the cell is saturated and hiding results, so it earns
        subdivision. A cell returning 6 of 20 is fully covered for 1 request.
        At minimum cell size we paginate instead, since we can't split further.
        """
        blank = not (query or "").strip()
        found: dict[str, Place] = {}
        queue = [root]
        cells = 0
        dropped = 0

        while queue:
            rect = queue.pop(0)
            try:
                if blank:
                    places, token = self._search_nearby(rect), None
                    before = len(places)
                    places = [p for p in places if not is_noise(p)]
                    dropped += before - len(places)
                else:
                    places, token = self._search(query, rect)
            except BudgetExhausted as e:
                if verbose:
                    print(f"  ! {e}")
                    print(f"  ! stopped with {len(queue)} cells unexplored")
                break

            cells += 1
            new = sum(1 for p in places if p.place_id not in found)
            for p in places:
                found[p.place_id] = p

            saturated = (before if blank else len(places)) >= PAGE_SIZE
            if verbose:
                flag = "FULL" if saturated else "    "
                print(
                    f"  [{self.spent:>4}/{self.budget}] {flag} {rect} "
                    f"-> {len(places):>2} ({new:>2} new) | total {len(found)}"
                )

            if on_cell:
                on_cell({
                    "cell": str(rect), "found": len(places), "new": new,
                    "total": len(found), "spent": self.spent,
                    "budget": self.budget, "queued": len(queue),
                    "saturated": saturated,
                })

            if not saturated:
                continue

            if rect.width_km() > min_cell_km:
                queue.extend(rect.quarters())
            elif blank:
                continue  # nearby has no pagination; nothing more to get here
            else:
                # Can't subdivide further; page through the remaining 40.
                pages = 0
                while token and pages < 2:
                    try:
                        more, token = self._search(query, rect, page_token=token)
                    except BudgetExhausted:
                        break
                    for p in more:
                        found[p.place_id] = p
                    pages += 1

        if verbose:
            extra = f", {dropped} non-business places filtered" if blank else ""
            print(
                f"\n  swept {cells} cells | {self.spent} requests spent, "
                f"{self.cache_hits} served from cache | "
                f"{len(found)} unique businesses{extra}"
            )
        return list(found.values())


class Apify:
    """Fallback source: Apify's Google Maps scraper.

    Free plan is $5/month in credits and needs no card, which is roughly
    1,000-3,000 places. Use when you'd rather not enable GCP billing.
    """

    ACTOR = "compass~crawler-google-places"
    URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

    def __init__(self, token: str, max_places: int = 500):
        self.token = token
        self.max_places = max_places
        self.spent = 0
        self.cache_hits = 0
        self.budget = 1
        self.client = httpx.Client(timeout=600.0)

    def harvest_by_location(self, query: str, location: str, verbose: bool = True):
        if verbose:
            print(f"  Apify: {query!r} in {location!r}, cap {self.max_places}")
        resp = self.client.post(
            self.URL.format(actor=self.ACTOR),
            params={"token": self.token},
            json={
                "searchStringsArray": [query],
                "locationQuery": location,
                "maxCrawledPlacesPerSearch": self.max_places,
                "language": "en",
                "skipClosedPlaces": True,
            },
        )
        resp.raise_for_status()
        self.spent = 1

        out = []
        for it in resp.json():
            out.append(
                Place(
                    place_id=it.get("placeId") or it.get("fid", ""),
                    name=it.get("title", ""),
                    address=it.get("address", ""),
                    lat=(it.get("location") or {}).get("lat", 0.0),
                    lng=(it.get("location") or {}).get("lng", 0.0),
                    rating=it.get("totalScore"),
                    review_count=it.get("reviewsCount") or 0,
                    website=it.get("website"),
                    phone=it.get("phone"),
                    category=it.get("categoryName", ""),
                    maps_uri=it.get("url", ""),
                    status="CLOSED_PERMANENTLY"
                    if it.get("permanentlyClosed")
                    else "OPERATIONAL",
                )
            )
        if verbose:
            print(f"  Apify returned {len(out)} places")
        return out
