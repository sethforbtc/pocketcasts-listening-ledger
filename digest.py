"""
New-episode digest: a once-a-day push of new episodes from the shows you
actually listen to (your top shows by hours), not everything you subscribe to.

Needs a Podcast Index API key (python pc_history.py --set-podcastindex) and an
ntfy topic (python pc_history.py --set-ntfy TOPIC). Turn it on with:

    python pc_history.py --digest on [--digest-shows 15] [--digest-hour 7]

The hourly collector sends it on its first run at or after the chosen hour.
"""

from __future__ import annotations

import datetime as dt
import logging

import ledger_reports as lr
import podcast_index as pi

log = logging.getLogger("pc_history")

DEFAULTS = {"digest_enabled": False, "digest_shows": 15, "digest_months": 3, "digest_hour": 7}
MAX_LINES = 20


def settings(cfg: dict) -> dict:
    out = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    out["digest_shows"] = max(1, min(100, int(out["digest_shows"])))
    out["digest_months"] = max(1, min(60, int(out["digest_months"])))
    out["digest_hour"] = max(0, min(23, int(out["digest_hour"])))
    out["digest_enabled"] = bool(out["digest_enabled"])
    return out


def _state(con, key, default=None):
    row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_state(con, key, value):
    con.execute("INSERT INTO state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def build(con, shows: int = 15, months: int = 3, since_epoch: int | None = None) -> dict:
    """Find episodes published since `since_epoch` (default: last digest, or 24 hours)
    for your top shows by hours over the last `months` months."""
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    if since_epoch is None:
        last = _state(con, "last_digest_epoch")
        since_epoch = int(last) if last else now - 86400
    since_epoch = max(since_epoch, now - 7 * 86400)  # never look back more than a week

    rep = lr.run_report(con, "top_hours", shows, months, "months")
    top = [r for r in rep["rows"] if r.get("podcast_uuid")]
    uuids = [r["podcast_uuid"] for r in top]
    if uuids:  # make sure these shows have feed ids (cheap; usually already done)
        lr.resolve_missing_feeds(con, limit=len(uuids), only=uuids)
        lr.enrich_feed_ids(con, limit=len(uuids), only=uuids)
    ids = dict(con.execute(
        f"SELECT podcast_uuid, pi_feed_id FROM podcasts WHERE podcast_uuid IN ({','.join('?' * len(uuids))})",
        uuids).fetchall()) if uuids else {}
    by_feed = {ids[r["podcast_uuid"]]: r for r in top if ids.get(r["podcast_uuid"])}
    missing = [r["podcast_title"] for r in top if not ids.get(r["podcast_uuid"])]

    items = pi.episodes_by_feed_ids(list(by_feed), since_epoch) if by_feed else []

    # Skip anything already in your Pocket Casts history (matched by show + title).
    heard = {(p, lr._norm(t)) for p, t in con.execute(
        "SELECT podcast_uuid, episode_title FROM episodes")}
    episodes, seen = [], set()
    for it in items:
        show = by_feed.get(it.get("feedId"))
        if not show:
            continue
        key = (show["podcast_uuid"], lr._norm(it.get("title")))
        if key in heard or key in seen:
            continue
        seen.add(key)
        episodes.append({
            "podcast_title": show["podcast_title"],
            "episode_title": it.get("title") or "(untitled)",
            "published_utc": dt.datetime.fromtimestamp(it.get("datePublished") or 0, dt.timezone.utc).isoformat(),
            "duration_sec": it.get("duration") or None,
            "link": it.get("link") or None,
            "hours_rank": show.get("rank"),
        })
    episodes.sort(key=lambda e: (e["hours_rank"] or 999, e["published_utc"]))
    return {"since_epoch": since_epoch, "checked_epoch": now,
            "shows_checked": len(by_feed), "shows_unmatched": missing,
            "period": rep["description"], "episodes": episodes}


def format_message(d: dict) -> str:
    eps = d["episodes"]
    lines = []
    for e in eps[:MAX_LINES]:
        mins = f" ({round(e['duration_sec'] / 60)} min)" if e.get("duration_sec") else ""
        lines.append(f"• {e['podcast_title']}: {e['episode_title']}{mins}")
    if len(eps) > MAX_LINES:
        lines.append(f"…and {len(eps) - MAX_LINES} more")
    return "\n".join(lines)


def send(con, notify, shows: int = 15, months: int = 3, record: bool = True) -> dict:
    """Build and push the digest. Sends nothing when there's nothing new.
    `notify` is pc_history.notify. Returns the digest plus 'sent' and 'detail'."""
    d = build(con, shows, months)
    d["sent"], d["detail"] = False, "Nothing new"
    if d["episodes"]:
        n = len(d["episodes"])
        ok, detail = notify(format_message(d), title=f"{n} new episode{'s' * (n != 1)} from your shows",
                            tags="headphones")
        d["sent"], d["detail"] = ok, detail
        if not ok:
            return d  # keep the window so the next attempt includes these
    if record:
        _set_state(con, "last_digest_epoch", str(d["checked_epoch"]))
        _set_state(con, "last_digest_date", dt.date.today().isoformat())
        con.commit()
    return d


def maybe_send(con, cfg: dict, notify) -> dict | None:
    """Called after each successful hourly run. Sends at most one digest a day,
    on the first run at or after the configured local hour."""
    s = settings(cfg)
    if not s["digest_enabled"] or not pi.configured():
        return None
    now = dt.datetime.now()
    if now.hour < s["digest_hour"] or _state(con, "last_digest_date") == now.date().isoformat():
        return None
    d = send(con, notify, s["digest_shows"], s["digest_months"])
    log.info("Digest: %d new episode(s) from %d shows; %s",
             len(d["episodes"]), d["shows_checked"], d["detail"])
    return d
