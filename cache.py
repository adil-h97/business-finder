"""Disk cache, so iterating on scoring never costs API requests.

The free Enterprise tier is 1,000 requests/month. Without a cache you burn it
re-running the same query while tuning. Keyed by a hash of the request.

Note on TTL: Google's terms permit caching place IDs indefinitely but limit
other place content to 30 days. Default is 25 days to stay inside that.
"""

import hashlib
import json
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent / ".cache"
SEARCH_TTL = 25 * 86400   # stay under Google's 30-day content cache limit
AUDIT_TTL = 14 * 86400    # our own data, but sites change


def _path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    return CACHE_DIR / namespace / f"{digest}.json"


def get(namespace: str, key: str, ttl: int):
    p = _path(namespace, key)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - blob.get("at", 0) > ttl:
        return None
    return blob.get("value")


def set(namespace: str, key: str, value) -> None:
    p = _path(namespace, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": time.time(), "value": value}))


def stats() -> dict[str, int]:
    if not CACHE_DIR.exists():
        return {}
    return {
        d.name: len(list(d.glob("*.json")))
        for d in CACHE_DIR.iterdir()
        if d.is_dir()
    }
