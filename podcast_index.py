"""
Small client for the Podcast Index API (https://podcastindex.org).

Used for two things:
  * finding RSS feeds that Apple's directory can't match, and recording each
    show's Podcast Index feed id and podcast:guid (a stable id that survives
    a show moving to a new host);
  * the new-episode digest, which checks the shows you actually listen to.

Needs a free API key and secret from https://api.podcastindex.org/signup.
They are kept in your OS credential store, like your Pocket Casts password:

    python pc_history.py --set-podcastindex

Environment variables PODCASTINDEX_KEY / PODCASTINDEX_SECRET override the
stored values. Everything here is optional: with no key, the ledger works as
before and these features are skipped.
"""

from __future__ import annotations

import hashlib
import os
import time

import keyring
import requests

BASE = "https://api.podcastindex.org/api/1.0"
KEYRING_SERVICE = "PocketCastsLogger"
# Podcast Index asks every app to identify itself honestly in the User-Agent.
UA = "PocketCastsListeningLedger/1.1 (+https://github.com/sethforbtc/pocketcasts-listening-ledger)"

# Space requests at least this far apart (seconds), so a first-time catch-up
# over a few hundred shows stays well inside Podcast Index's fair-use limits.
MIN_INTERVAL = 1.0
_last_request = 0.0


class NotConfigured(RuntimeError):
    pass


def credentials() -> tuple[str | None, str | None]:
    key = os.environ.get("PODCASTINDEX_KEY")
    secret = os.environ.get("PODCASTINDEX_SECRET")
    if key and secret:
        return key, secret
    try:
        return (keyring.get_password(KEYRING_SERVICE, "podcastindex_key"),
                keyring.get_password(KEYRING_SERVICE, "podcastindex_secret"))
    except Exception:  # noqa: BLE001 - no credential backend available
        return None, None


def configured() -> bool:
    key, secret = credentials()
    return bool(key and secret)


def save_credentials(key: str, secret: str):
    keyring.set_password(KEYRING_SERVICE, "podcastindex_key", key)
    keyring.set_password(KEYRING_SERVICE, "podcastindex_secret", secret)


def clear_credentials():
    for name in ("podcastindex_key", "podcastindex_secret"):
        try:
            keyring.delete_password(KEYRING_SERVICE, name)
        except Exception:  # noqa: BLE001
            pass


def _headers(key: str, secret: str) -> dict:
    now = str(int(time.time()))
    return {
        "User-Agent": UA,
        "X-Auth-Key": key,
        "X-Auth-Date": now,
        "Authorization": hashlib.sha1((key + secret + now).encode("utf-8")).hexdigest(),
    }


def get(path: str, key: str | None = None, secret: str | None = None, **params) -> dict:
    """GET an API path (e.g. 'podcasts/byitunesid'). Raises NotConfigured or requests errors."""
    if not (key and secret):
        key, secret = credentials()
    if not (key and secret):
        raise NotConfigured("No Podcast Index API key. Run:  python pc_history.py --set-podcastindex")
    params = {k: v for k, v in params.items() if v is not None}
    global _last_request
    for attempt in range(2):
        wait = _last_request + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        r = requests.get(f"{BASE}/{path}", params=params, headers=_headers(key, secret), timeout=30)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(int(r.headers.get("Retry-After", "5") or 5), 30))
            continue
        break
    if r.status_code in (401, 403):
        raise PermissionError(f"Podcast Index rejected the API key (HTTP {r.status_code})")
    r.raise_for_status()
    # A not-found lookup comes back as 200 with status "false"; callers treat
    # an empty feed/feeds/items field as "no match".
    return r.json()


def _feed(data: dict) -> dict | None:
    f = data.get("feed")
    if isinstance(f, list):  # some lookups return a list
        f = f[0] if f else None
    return f if f and f.get("id") else None


def feed_by_itunes_id(itunes_id) -> dict | None:
    return _feed(get("podcasts/byitunesid", id=itunes_id))


def feed_by_url(url: str) -> dict | None:
    return _feed(get("podcasts/byfeedurl", url=url))


def search(term: str, max_results: int = 20) -> list[dict]:
    return get("search/byterm", q=term, max=max_results).get("feeds") or []


def episodes_by_feed_ids(feed_ids: list[int], since_epoch: int, max_items: int = 200) -> list[dict]:
    """Episodes published since `since_epoch` for up to ~100 feeds at once, newest first."""
    out: list[dict] = []
    ids = [str(i) for i in feed_ids if i]
    for i in range(0, len(ids), 100):
        data = get("episodes/byfeedid", id=",".join(ids[i:i + 100]), since=since_epoch, max=max_items)
        out.extend(data.get("items") or [])
    out.sort(key=lambda e: e.get("datePublished") or 0, reverse=True)
    return out


def test_credentials(key: str, secret: str) -> tuple[bool, str]:
    try:
        get("search/byterm", key=key, secret=secret, q="podcasting 2.0", max=1)
        return True, "OK"
    except PermissionError as e:
        return False, str(e)
    except requests.RequestException as e:
        return False, repr(e)
