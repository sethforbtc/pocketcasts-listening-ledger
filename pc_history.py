"""
Pocket Casts Listening Ledger.

Polls the (unofficial) Pocket Casts web-player API, and keeps a permanent
local log of everything that appears in your Listening History in SQLite.

Usage:
    python pc_history.py --setup          # store email + password in Windows Credential Manager
    python pc_history.py                  # one polling run (what Task Scheduler calls)
    python pc_history.py --stats          # quick summary of what's been logged
    python pc_history.py --export-csv out.csv

Data lives in %LOCALAPPDATA%\\PocketCastsLogger by default
(override with the PC_LOGGER_DIR environment variable).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import keyring
import requests

API = "https://api.pocketcasts.com"
KEYRING_SERVICE = "PocketCastsLogger"
UA = "PocketCastsListeningLedger/1.0 (personal use)"

DATA_DIR = Path(
    os.environ.get("PC_LOGGER_DIR")
    or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PocketCastsLogger"
)
DB_PATH = DATA_DIR / "pocketcasts_history.db"
LOG_PATH = DATA_DIR / "logger.log"

# Optional: push a phone notification on failure via https://ntfy.sh
# Set NTFY_TOPIC to a hard-to-guess topic name and subscribe to it in the ntfy iOS app.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")

log = logging.getLogger("pc_history")


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_utc         TEXT NOT NULL,
    ok              INTEGER NOT NULL,
    episodes_returned INTEGER,
    new_episodes    INTEGER,
    listen_events   INTEGER,
    error           TEXT
);

-- One row per distinct episode ever seen in history. Latest metadata wins.
CREATE TABLE IF NOT EXISTS episodes (
    episode_uuid    TEXT PRIMARY KEY,
    podcast_uuid    TEXT,
    podcast_title   TEXT,
    episode_title   TEXT,
    author          TEXT,
    published_utc   TEXT,
    duration_sec    INTEGER,
    episode_type    TEXT,
    season          INTEGER,
    episode_number  INTEGER,
    audio_url       TEXT,
    file_type       TEXT,
    played_up_to_sec INTEGER,
    playing_status  INTEGER,     -- 1 = not played, 2 = in progress, 3 = completed
    starred         INTEGER,
    first_seen_utc  TEXT NOT NULL,
    last_seen_utc   TEXT NOT NULL,
    raw_json        TEXT         -- full API record, in case fields change later
);

-- One row each time an episode moves to the top of your history
-- (first listen OR a re-listen). The listen happened somewhere in
-- (window_start_utc, window_end_utc] -- i.e. between two polling runs.
CREATE TABLE IF NOT EXISTS listen_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_uuid    TEXT NOT NULL REFERENCES episodes(episode_uuid),
    window_start_utc TEXT,       -- NULL on the very first run (unknown)
    window_end_utc  TEXT NOT NULL,
    order_in_batch  INTEGER NOT NULL,  -- 0 = most recent within that run
    played_up_to_sec INTEGER,
    playing_status  INTEGER,
    is_initial_import INTEGER NOT NULL DEFAULT 0,
    run_id          INTEGER REFERENCES runs(run_id)
);

-- Ordered snapshot of the last successful fetch, used to detect what's new.
CREATE TABLE IF NOT EXISTS last_snapshot (
    position        INTEGER PRIMARY KEY,
    episode_uuid    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE VIEW IF NOT EXISTS v_listens AS
SELECT
    le.event_id,
    le.window_end_utc           AS logged_utc,
    le.window_start_utc,
    le.is_initial_import,
    e.podcast_title,
    e.episode_title,
    e.published_utc,
    e.duration_sec,
    le.played_up_to_sec,
    CASE WHEN e.duration_sec > 0
         THEN ROUND(100.0 * le.played_up_to_sec / e.duration_sec, 1) END AS pct_played_at_log,
    CASE le.playing_status WHEN 3 THEN 'completed'
                           WHEN 2 THEN 'in progress'
                           WHEN 1 THEN 'not played' END                  AS status_at_log,
    e.played_up_to_sec          AS played_up_to_sec_latest,
    CASE e.playing_status WHEN 3 THEN 'completed'
                          WHEN 2 THEN 'in progress'
                          WHEN 1 THEN 'not played' END                   AS status_latest,
    e.episode_uuid,
    e.podcast_uuid
FROM listen_events le
JOIN episodes e USING (episode_uuid);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def get_state(con, key, default=None):
    row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_state(con, key, value):
    con.execute(
        "INSERT INTO state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

class AuthError(Exception):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Same origin the web player sends; some endpoints check it.
        "Origin": "https://pocketcasts.com",
        "Referer": "https://pocketcasts.com/",
    })
    return s


def login(s: requests.Session, email: str, password: str) -> str:
    """Log in like the web player does. Tries the current and legacy endpoints."""
    attempts = [
        ("/user/login_pocket_casts", {"email": email, "password": password, "scope": "webplayer"}),
        ("/user/login", {"email": email, "password": password, "scope": "webplayer"}),
        ("/user/login_pocket_casts", {"email": email, "password": password}),
    ]
    errors, all_auth = [], True
    for path, body in attempts:
        try:
            r = s.post(API + path, json=body, timeout=30)
        except requests.RequestException as e:
            errors.append(f"{path}: {e!r}")
            all_auth = False
            continue
        if r.ok:
            try:
                data = r.json()
            except ValueError:
                data = {}
            token = data.get("accessToken") or data.get("token")
            if token:
                log.info("Login OK via %s", path)
                return token
            errors.append(f"{path}: 200 but no token (keys: {list(data)})")
            all_auth = False
            continue
        # Server error text never contains your password; safe to show.
        errors.append(f"{path} {sorted(body)}: HTTP {r.status_code} {r.text[:200]!r}")
        if r.status_code not in (400, 401, 403):
            all_auth = False
    msg = "Login failed on every endpoint:\n  " + "\n  ".join(errors)
    raise (AuthError if all_auth else RuntimeError)(msg)


def fetch_history(s: requests.Session, token: str) -> list[dict]:
    r = s.post(
        API + "/user/history",
        json={},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if r.status_code in (401, 403):
        raise AuthError("Token expired or rejected")
    r.raise_for_status()
    data = r.json()
    episodes = data.get("episodes")
    if episodes is None:
        raise RuntimeError(f"Unexpected history response keys: {list(data)}")
    return episodes


def get_history_with_auth(con) -> list[dict]:
    email = keyring.get_password(KEYRING_SERVICE, "email")
    password = keyring.get_password(KEYRING_SERVICE, email) if email else None
    if not (email and password):
        raise RuntimeError("No credentials stored. Run:  python pc_history.py --setup")

    s = _session()
    token = keyring.get_password(KEYRING_SERVICE, "token")
    if token:
        try:
            return fetch_history(s, token)
        except AuthError:
            log.info("Cached token rejected; logging in again")

    token = login(s, email, password)
    keyring.set_password(KEYRING_SERVICE, "token", token)
    return fetch_history(s, token)


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #

def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _int(v):
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def upsert_episode(con, ep: dict, now: str) -> bool:
    """Insert or update an episode. Returns True if it was new."""
    uuid = ep["uuid"]
    exists = con.execute(
        "SELECT 1 FROM episodes WHERE episode_uuid=?", (uuid,)
    ).fetchone()
    vals = dict(
        episode_uuid=uuid,
        podcast_uuid=ep.get("podcastUuid"),
        podcast_title=ep.get("podcastTitle"),
        episode_title=ep.get("title"),
        author=ep.get("author"),
        published_utc=ep.get("published"),
        duration_sec=_int(ep.get("duration")),
        episode_type=ep.get("episodeType"),
        season=_int(ep.get("episodeSeason")),
        episode_number=_int(ep.get("episodeNumber")),
        audio_url=ep.get("url"),
        file_type=ep.get("fileType"),
        played_up_to_sec=_int(ep.get("playedUpTo")),
        playing_status=_int(ep.get("playingStatus")),
        starred=1 if ep.get("starred") else 0,
        last_seen_utc=now,
        raw_json=json.dumps(ep, ensure_ascii=False),
    )
    if exists:
        sets = ", ".join(f"{k}=:{k}" for k in vals if k != "episode_uuid")
        con.execute(f"UPDATE episodes SET {sets} WHERE episode_uuid=:episode_uuid", vals)
        return False
    vals["first_seen_utc"] = now
    cols = ", ".join(vals)
    params = ", ".join(f":{k}" for k in vals)
    con.execute(f"INSERT INTO episodes ({cols}) VALUES ({params})", vals)
    return True


def detect_new_plays(current: list[str], previous: list[str]) -> list[int]:
    """
    History is ordered most-recent-first, and a re-played episode jumps back
    to the top. Everything that now sits *above* the episodes we already knew
    about (in their known order) has been played since the last run.

    Returns the indexes in `current` that represent new plays.
    """
    if not previous:
        return list(range(len(current)))
    prev_set = set(previous)
    # Walk down until we hit the first previously-known episode whose relative
    # order is unchanged -- i.e. the old top that hasn't been re-played.
    for i, uuid in enumerate(current):
        if uuid in prev_set:
            # Verify the tail from here matches the previous list's order
            # (allowing for items that dropped off the end or moved to the top).
            tail = [u for u in current[i:] if u in prev_set]
            moved_up = set(current[:i])
            expected = [u for u in previous if u not in moved_up]
            if tail == expected[: len(tail)]:
                return list(range(i))
            # Otherwise this known episode was re-played; keep walking.
    # Nothing lines up (e.g. history was cleared): treat all as new plays.
    return list(range(len(current)))


def run_once(con, episodes: list[dict] | None = None) -> dict:
    now = utcnow()
    if episodes is None:
        episodes = get_history_with_auth(con)

    current = [e["uuid"] for e in episodes if e.get("uuid")]
    previous = [
        r[0] for r in con.execute("SELECT episode_uuid FROM last_snapshot ORDER BY position")
    ]
    first_run = get_state(con, "last_success_utc") is None
    window_start = get_state(con, "last_success_utc")

    by_uuid = {e["uuid"]: e for e in episodes if e.get("uuid")}
    new_eps = sum(upsert_episode(con, by_uuid[u], now) for u in current)

    cur = con.execute(
        "INSERT INTO runs(run_utc, ok, episodes_returned) VALUES(?, 1, ?)",
        (now, len(current)),
    )
    run_id = cur.lastrowid

    new_play_idx = detect_new_plays(current, previous)
    # Insert oldest-first so event_id increases in listening order.
    for order, idx in reversed(list(enumerate(new_play_idx))):
        ep = by_uuid[current[idx]]
        con.execute(
            """INSERT INTO listen_events
               (episode_uuid, window_start_utc, window_end_utc, order_in_batch,
                played_up_to_sec, playing_status, is_initial_import, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                current[idx], window_start, now, order,
                _int(ep.get("playedUpTo")), _int(ep.get("playingStatus")),
                1 if first_run else 0, run_id,
            ),
        )

    con.execute("DELETE FROM last_snapshot")
    con.executemany(
        "INSERT INTO last_snapshot(position, episode_uuid) VALUES(?, ?)",
        list(enumerate(current)),
    )
    con.execute(
        "UPDATE runs SET new_episodes=?, listen_events=? WHERE run_id=?",
        (new_eps, len(new_play_idx), run_id),
    )
    set_state(con, "last_success_utc", now)
    set_state(con, "consecutive_failures", "0")
    con.commit()
    return {"returned": len(current), "new_episodes": new_eps, "listen_events": len(new_play_idx)}


def notify(msg: str):
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode("utf-8"),
            headers={"Title": "Pocket Casts logger"},
            timeout=15,
        )
    except requests.RequestException:
        pass


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def cmd_setup():
    email = input("Pocket Casts email: ").strip()
    raw = getpass.getpass("Pocket Casts password (input is hidden; paste with right-click if Ctrl+V fails): ")
    # Pasting into a hidden Windows prompt can insert control characters
    # (e.g. Ctrl+V becomes '\x16') or a trailing newline. Strip them.
    password = "".join(ch for ch in raw if ch.isprintable())
    removed = len(raw) - len(password)
    print(f"Password received: {len(password)} characters"
          + (f" ({removed} invisible control character(s) removed)" if removed else "")
          + ". Compare that count with your password manager.")
    s = _session()
    try:
        token = login(s, email, password)
    except (AuthError, RuntimeError) as e:
        print(f"\n{e}")
        sys.exit(1)
    eps = fetch_history(s, token)
    keyring.set_password(KEYRING_SERVICE, "email", email)
    keyring.set_password(KEYRING_SERVICE, email, password)
    keyring.set_password(KEYRING_SERVICE, "token", token)
    print(f"Login OK. Your history currently has {len(eps)} episodes.")
    print("Credentials saved to Windows Credential Manager.")
    con = connect()
    res = run_once(con, eps)
    print(f"Initial import: {res['listen_events']} episodes logged to {DB_PATH}")


def cmd_poll():
    con = connect()
    try:
        res = run_once(con)
        log.info("OK: %s", res)
    except Exception as e:  # noqa: BLE001
        con.rollback()
        fails = int(get_state(con, "consecutive_failures", "0")) + 1
        con.execute(
            "INSERT INTO runs(run_utc, ok, error) VALUES(?, 0, ?)", (utcnow(), repr(e)[:1000])
        )
        set_state(con, "consecutive_failures", str(fails))
        con.commit()
        log.exception("Run failed (%d in a row)", fails)
        # Alert on the 3rd consecutive failure, then every 24 failures (~daily if hourly).
        if fails == 3 or (fails > 3 and fails % 24 == 0):
            notify(f"Logger has failed {fails} times in a row. Last error: {e}")
        sys.exit(1)


def cmd_stats():
    con = connect()
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    print(f"Database:        {DB_PATH}")
    print(f"Episodes logged: {q('SELECT COUNT(*) FROM episodes')}")
    print(f"Listen events:   {q('SELECT COUNT(*) FROM listen_events')}")
    print(f"Shows:           {q('SELECT COUNT(DISTINCT podcast_uuid) FROM episodes')}")
    print(f"Last success:    {get_state(con, 'last_success_utc')}")
    print(f"Failures in row: {get_state(con, 'consecutive_failures', '0')}")
    print("\nMost recent listens:")
    for row in con.execute(
        "SELECT logged_utc, podcast_title, episode_title FROM v_listens "
        "ORDER BY event_id DESC LIMIT 10"
    ):
        print(f"  {row[0]}  {row[1]} - {row[2]}")


def cmd_export(path: str):
    con = connect()
    cur = con.execute("SELECT * FROM v_listens ORDER BY event_id")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur)
    print(f"Exported to {path}")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--setup", action="store_true", help="store credentials and do the first import")
    p.add_argument("--stats", action="store_true", help="print a summary")
    p.add_argument("--export-csv", metavar="PATH", help="export all listens to CSV")
    a = p.parse_args()
    if a.setup:
        cmd_setup()
    elif a.stats:
        cmd_stats()
    elif a.export_csv:
        cmd_export(a.export_csv)
    else:
        cmd_poll()


if __name__ == "__main__":
    main()
