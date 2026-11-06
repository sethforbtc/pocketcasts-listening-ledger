"""
Reporting layer for the Listening Ledger: statistics, top-show reports,
RSS feed lookup, and OPML / CSV / Excel export.

Shared by the dashboard (dashboard.py) and the collector (pc_history.py).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import time
from collections import Counter, defaultdict
from xml.sax.saxutils import escape, quoteattr

import requests

import podcast_index as pi

UA = "PocketCastsListeningLedger/1.0 (personal use)"

UNITS = {"weeks": 7.0, "months": 30.44, "years": 365.25}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def parse_utc(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def cutoff(period_n: int | None, unit: str | None) -> dt.datetime | None:
    """Start of the time window, or None for all time."""
    if not period_n or unit not in UNITS:
        return None
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=UNITS[unit] * int(period_n))


def period_label(period_n, unit) -> str:
    if not period_n or unit not in UNITS:
        return "all time"
    n = int(period_n)
    return f"last {n} {unit[:-1] if n == 1 else unit}"


# --------------------------------------------------------------------------- #
# Core dataset: one row per episode listened to inside the window
# --------------------------------------------------------------------------- #

def episodes_in_window(con, since: dt.datetime | None, include_initial: bool = True,
                       threshold: float = 0.75) -> list[dict]:
    rows = con.execute(
        """SELECT le.episode_uuid, le.window_end_utc, le.is_initial_import,
                  e.podcast_uuid, e.podcast_title, e.episode_title, e.author,
                  e.duration_sec, e.played_up_to_sec, e.playing_status, e.published_utc,
                  p.feed_url
           FROM listen_events le
           JOIN episodes e USING (episode_uuid)
           LEFT JOIN podcasts p ON p.podcast_uuid = e.podcast_uuid"""
    ).fetchall()
    eps: dict[str, dict] = {}
    for (uuid, when, initial, puuid, ptitle, title, author, dur, played,
         status, published, feed) in rows:
        if initial and not include_initial:
            continue
        t = parse_utc(when)
        if since and t < since:
            continue
        e = eps.get(uuid)
        if e is None:
            dur = dur or 0
            played = min(played or 0, dur) if dur else (played or 0)
            ratio = (played / dur) if dur else 0.0
            completed = status == 3 or ratio >= threshold
            if status == 3 and ratio < threshold and dur:
                played = dur  # app marks finished but reset the position
                ratio = 1.0
            e = eps[uuid] = dict(
                episode_uuid=uuid, podcast_uuid=puuid, podcast_title=ptitle or "(unknown)",
                author=author, episode_title=title, duration_sec=dur,
                played_sec=played, pct_played=round(100 * ratio, 1),
                completed=completed, status=status, published_utc=published,
                feed_url=feed, listens=0, first_listen=t, last_listen=t,
                initial_only=True,
            )
        e["listens"] += 1
        e["first_listen"] = min(e["first_listen"], t)
        e["last_listen"] = max(e["last_listen"], t)
        if not initial:
            e["initial_only"] = False
    return list(eps.values())


def _show_rows(eps: list[dict]) -> dict[str, dict]:
    shows: dict[str, dict] = {}
    authors: dict[str, Counter] = defaultdict(Counter)
    for e in eps:
        s = shows.setdefault(e["podcast_uuid"], dict(
            podcast_uuid=e["podcast_uuid"], podcast_title=e["podcast_title"],
            feed_url=e["feed_url"], episodes=0, completed=0, hours=0.0,
            last_listen=e["last_listen"],
        ))
        s["episodes"] += 1
        s["completed"] += 1 if e["completed"] else 0
        s["hours"] += e["played_sec"] / 3600
        s["last_listen"] = max(s["last_listen"], e["last_listen"])
        if e["author"]:
            authors[e["podcast_uuid"]][e["author"]] += 1
    for k, s in shows.items():
        s["hours"] = round(s["hours"], 2)
        s["author"] = authors[k].most_common(1)[0][0] if authors[k] else None
    return shows


# --------------------------------------------------------------------------- #
# The three show reports (each also feeds OPML export)
# --------------------------------------------------------------------------- #

REPORTS = {
    "top_completed": "Top shows by completed episodes",
    "top_hours": "Top shows by hours listened",
    "recent": "Shows from your most recent episodes",
}


def run_report(con, kind: str, n: int, period_n=None, unit=None,
               threshold: float = 0.75, include_initial: bool = True) -> dict:
    since = cutoff(period_n, unit)
    eps = episodes_in_window(con, since, include_initial, threshold)
    n = max(1, int(n))
    if kind == "top_completed":
        shows = [s for s in _show_rows(eps).values() if s["completed"] > 0]
        shows.sort(key=lambda s: (-s["completed"], -s["hours"]))
        rows = shows[:n]
        cols = ["rank", "podcast_title", "author", "completed", "episodes", "hours", "last_listen", "feed_url"]
        desc = (f"Top {n} shows by episodes completed (≥{round(threshold*100)}% played), "
                f"{period_label(period_n, unit)}")
    elif kind == "top_hours":
        shows = sorted(_show_rows(eps).values(), key=lambda s: (-s["hours"], -s["completed"]))
        rows = shows[:n]
        cols = ["rank", "podcast_title", "author", "hours", "episodes", "completed", "last_listen", "feed_url"]
        desc = f"Top {n} shows by hours listened, {period_label(period_n, unit)}"
    elif kind == "recent":
        latest = sorted(eps, key=lambda e: e["last_listen"], reverse=True)[:n]
        rows = sorted(_show_rows(latest).values(), key=lambda s: s["last_listen"], reverse=True)
        cols = ["rank", "podcast_title", "author", "episodes", "completed", "hours", "last_listen", "feed_url"]
        desc = (f"Every show in your {n} most recent episodes, {period_label(period_n, unit)} "
                f"({len(rows)} shows)")
    else:
        raise ValueError(f"Unknown report: {kind}")
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"kind": kind, "title": REPORTS[kind], "description": desc,
            "columns": cols, "rows": rows}


# --------------------------------------------------------------------------- #
# Dashboard statistics
# --------------------------------------------------------------------------- #

def stats(con, period_n=None, unit=None, threshold: float = 0.75) -> dict:
    since = cutoff(period_n, unit)
    eps_all = episodes_in_window(con, since, True, threshold)
    # Time-based charts skip the initial import: its real listen dates are unknown.
    eps_dated = [e for e in eps_all if not e["initial_only"]]

    total_listens = sum(e["listens"] for e in eps_all)
    hours = sum(e["played_sec"] for e in eps_all) / 3600
    completed = sum(1 for e in eps_all if e["completed"])
    sampled = sum(1 for e in eps_all if not e["completed"] and e["pct_played"] < 25)
    in_prog = len(eps_all) - completed - sampled
    shows = _show_rows(eps_all)

    # Hours per week (local time, week starting Monday), last 26 weeks.
    weekly: dict[str, float] = defaultdict(float)
    for e in eps_dated:
        d = e["last_listen"].astimezone().date()
        wk = d - dt.timedelta(days=d.weekday())
        weekly[wk.isoformat()] += e["played_sec"] / 3600
    today = dt.datetime.now().astimezone().date()
    this_wk = today - dt.timedelta(days=today.weekday())
    weeks = [(this_wk - dt.timedelta(weeks=i)).isoformat() for i in range(25, -1, -1)]
    if since:
        sd = since.astimezone().date()
        weeks = [w for w in weeks if dt.date.fromisoformat(w) >= sd - dt.timedelta(days=6)]

    # When you finish episodes: day-of-week x hour heatmap (local time).
    heat = [[0] * 24 for _ in range(7)]
    for e in eps_dated:
        t = e["last_listen"].astimezone()
        heat[t.weekday()][t.hour] += 1

    # New shows per month (month of first listen, from dated data only).
    first_seen: dict[str, dt.datetime] = {}
    for e in eps_dated:
        k = e["podcast_uuid"]
        if k not in first_seen or e["first_listen"] < first_seen[k]:
            first_seen[k] = e["first_listen"]
    new_by_month = Counter(t.astimezone().strftime("%Y-%m") for t in first_seen.values())

    top_hours = sorted(shows.values(), key=lambda s: -s["hours"])[:10]

    recent = sorted(eps_all, key=lambda e: e["last_listen"], reverse=True)[:50]
    in_progress = sorted(
        [e for e in eps_all if not e["completed"] and e["pct_played"] >= 5],
        key=lambda e: e["last_listen"], reverse=True)[:25]

    durations = [e["duration_sec"] for e in eps_all if e["duration_sec"]]
    tracking_since = con.execute("SELECT MIN(run_utc) FROM runs WHERE ok=1").fetchone()[0]

    return {
        "period": period_label(period_n, unit),
        "kpis": {
            "listens": total_listens,
            "episodes": len(eps_all),
            "shows": len(shows),
            "hours": round(hours, 1),
            "completed": completed,
            "completion_rate": round(100 * completed / len(eps_all), 1) if eps_all else None,
            "avg_episode_min": round(sum(durations) / len(durations) / 60) if durations else None,
            "tracking_since": tracking_since,
        },
        "completion": {"completed": completed, "in_progress": in_prog, "sampled": sampled},
        "weekly_hours": [{"week": w, "hours": round(weekly.get(w, 0), 2)} for w in weeks],
        "heatmap": heat,
        "new_shows_by_month": [{"month": m, "shows": c} for m, c in sorted(new_by_month.items())],
        "top_shows_hours": [{"podcast_title": s["podcast_title"], "hours": s["hours"],
                             "episodes": s["episodes"]} for s in top_hours],
        "recent": [_ep_out(e) for e in recent],
        "in_progress": [_ep_out(e) for e in in_progress],
        "undated_episodes": len(eps_all) - len(eps_dated),
    }


def _ep_out(e: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in e.items()
            if k in ("podcast_title", "episode_title", "pct_played", "completed",
                     "duration_sec", "played_sec", "last_listen", "listens", "initial_only")}


def health(con) -> dict:
    g = lambda k, d=None: (con.execute("SELECT value FROM state WHERE key=?", (k,)).fetchone() or [d])[0]  # noqa: E731
    last = con.execute("SELECT run_utc, ok, error FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    day_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    ok24, fail24 = con.execute(
        "SELECT COALESCE(SUM(ok),0), COALESCE(SUM(1-ok),0) FROM runs WHERE run_utc >= ?", (day_ago,)
    ).fetchone()
    feeds = con.execute(
        """SELECT COUNT(DISTINCT e.podcast_uuid),
                  COUNT(DISTINCT CASE WHEN p.feed_url IS NOT NULL THEN e.podcast_uuid END)
           FROM episodes e LEFT JOIN podcasts p USING (podcast_uuid)"""
    ).fetchone()
    return {
        "last_success_utc": g("last_success_utc"),
        "consecutive_failures": int(g("consecutive_failures", "0")),
        "last_run": {"run_utc": last[0], "ok": bool(last[1]), "error": last[2]} if last else None,
        "runs_24h": {"ok": ok24, "failed": fail24},
        "feeds": {"shows": feeds[0], "resolved": feeds[1]},
    }


# --------------------------------------------------------------------------- #
# RSS feed lookup (Pocket Casts history has no feed URLs; OPML needs them)
# --------------------------------------------------------------------------- #

def _norm(s: str | None) -> str:
    s = (s or "").lower().replace("&", "and")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\b(the|podcast|a)\b", " ", s)
    return " ".join(s.split())


def _match_key(title: str, author: str | None, cand_title: str | None,
               cand_authors: list[str | None]) -> tuple | None:
    """Score a directory result against a show. None means "not a confident match":
    when we know the author it must match; without one the title must match exactly."""
    nt, na, rt = _norm(title), _norm(author), _norm(cand_title)
    title_score = 3 if rt == nt else 1 if nt and rt and (nt in rt or rt in nt) else 0
    ras = [_norm(a) for a in cand_authors if a]
    author_ok = bool(na and any(ra and (na == ra or na in ra or ra in na) for ra in ras))
    if not title_score or (na and not author_ok) or (not na and title_score < 3):
        return None
    return (title_score + (2 if author_ok else 0), -abs(len(rt) - len(nt)))


def lookup_feed(title: str, author: str | None) -> dict | None:
    """Find a show's RSS feed via the free iTunes Search API."""
    r = requests.get(
        "https://itunes.apple.com/search",
        params={"term": title, "media": "podcast", "entity": "podcast", "limit": 15},
        headers={"User-Agent": UA}, timeout=20,
    )
    r.raise_for_status()
    best, best_key = None, None
    for res in r.json().get("results", []):
        if not res.get("feedUrl"):
            continue
        key = _match_key(title, author, res.get("collectionName"), [res.get("artistName")])
        if key and (best_key is None or key > best_key):
            best, best_key = res, key
    if best:
        return {"feed_url": best["feedUrl"], "site_url": best.get("collectionViewUrl"),
                "itunes_id": best.get("collectionId")}
    return None


def lookup_feed_podcastindex(title: str, author: str | None) -> dict | None:
    """Fallback: search Podcast Index, with the same confidence rules. Skips dead feeds."""
    best, best_key = None, None
    for f in pi.search(title):
        if not f.get("url") or f.get("dead"):
            continue
        key = _match_key(title, author, f.get("title"), [f.get("author"), f.get("ownerName")])
        if key and (best_key is None or key > best_key):
            best, best_key = f, key
    return _pi_fields(best) if best else None


def _pi_fields(f: dict) -> dict:
    return {"feed_url": f.get("url"), "site_url": f.get("link"),
            "itunes_id": f.get("itunesId") or None,
            "pi_feed_id": f.get("id"), "podcast_guid": f.get("podcastGuid") or None}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _seed_podcasts(con):
    con.execute(
        """INSERT OR IGNORE INTO podcasts(podcast_uuid, title)
           SELECT podcast_uuid, MAX(podcast_title) FROM episodes
           WHERE podcast_uuid IS NOT NULL GROUP BY podcast_uuid"""
    )
    con.commit()  # don't hold the write lock while we make network calls


def resolve_missing_feeds(con, limit: int = 10, verbose: bool = False,
                          only: list[str] | None = None) -> int:
    """Look up feed URLs for shows that don't have one yet. Returns how many were found.

    Apple's directory is tried first (up to 3 times per show). If it can't match
    a show and a Podcast Index key is set up, Podcast Index is searched once."""
    _seed_podcasts(con)
    authors = dict(con.execute(
        """SELECT podcast_uuid, author FROM episodes WHERE author IS NOT NULL
           GROUP BY podcast_uuid, author ORDER BY COUNT(*)"""
    ).fetchall())  # last (most common) author wins
    use_pi = pi.configured()
    sql = """SELECT podcast_uuid, title, attempts FROM podcasts
             WHERE feed_url IS NULL AND COALESCE(feed_source,'') != 'manual'
               AND (attempts < 3 OR (? AND pi_checked_utc IS NULL))"""
    params: list = [1 if use_pi else 0]
    if only:
        sql += f" AND podcast_uuid IN ({','.join('?' * len(only))})"
        params += list(only)
    todo = con.execute(sql + " ORDER BY attempts, title LIMIT ?", params + [limit]).fetchall()
    found = 0
    itunes_calls = 0
    for uuid, title, attempts in todo:
        author = authors.get(uuid)
        hit, source, tried_pi = None, None, False
        if attempts < 3:
            if itunes_calls:
                time.sleep(3)  # iTunes allows ~20 requests/minute
            itunes_calls += 1
            try:
                hit = lookup_feed(title or "", author)
                source = "itunes" if hit else None
            except requests.RequestException as e:
                if verbose:
                    print(f"  ! {title} (Apple): {e}")
                if not use_pi:
                    continue
        if not hit and use_pi:
            try:
                hit = lookup_feed_podcastindex(title or "", author)
                source = "podcastindex" if hit else None
                tried_pi = True
            except (requests.RequestException, PermissionError) as e:
                if verbose:
                    print(f"  ! {title} (Podcast Index): {e}")
        now = _now()
        if hit:
            found += 1
            con.execute(
                """UPDATE podcasts SET feed_url=?, site_url=?, itunes_id=?, author=?,
                   feed_source=?, attempts=attempts+1, resolved_utc=?,
                   pi_feed_id=COALESCE(?, pi_feed_id), podcast_guid=COALESCE(?, podcast_guid),
                   pi_checked_utc=CASE WHEN ? THEN ? ELSE pi_checked_utc END
                   WHERE podcast_uuid=?""",
                (hit["feed_url"], hit["site_url"], hit["itunes_id"], author, source, now,
                 hit.get("pi_feed_id"), hit.get("podcast_guid"),
                 1 if tried_pi else 0, now, uuid))
        else:
            con.execute(
                """UPDATE podcasts SET feed_source='notfound', author=?,
                   attempts=CASE WHEN attempts < 3 THEN attempts+1 ELSE attempts END,
                   resolved_utc=?, pi_checked_utc=CASE WHEN ? THEN ? ELSE pi_checked_utc END
                   WHERE podcast_uuid=?""", (author, now, 1 if tried_pi else 0, now, uuid))
        con.commit()
        if verbose:
            via = f" [{source}]" if hit else ""
            print(f"  {'✓' if hit else '✗'} {title}" + (f" -> {hit['feed_url']}{via}" if hit else ""))
    return found


def enrich_feed_ids(con, limit: int = 25, only: list[str] | None = None,
                    verbose: bool = False) -> int:
    """Record the Podcast Index feed id and podcast:guid for shows that already
    have a feed URL. Looks up by Apple id when known, otherwise by feed URL.
    Shows Podcast Index doesn't know are re-checked after 30 days."""
    if not pi.configured():
        return 0
    _seed_podcasts(con)
    month_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    sql = """SELECT podcast_uuid, title, feed_url, itunes_id FROM podcasts
             WHERE feed_url IS NOT NULL AND pi_feed_id IS NULL
               AND (pi_checked_utc IS NULL OR pi_checked_utc < ?)"""
    params: list = [month_ago]
    if only:
        sql += f" AND podcast_uuid IN ({','.join('?' * len(only))})"
        params += list(only)
    todo = con.execute(sql + " ORDER BY title LIMIT ?", params + [limit]).fetchall()
    found = 0
    for uuid, title, feed_url, itunes_id in todo:
        try:
            f = pi.feed_by_itunes_id(itunes_id) if itunes_id else None
            if not f:
                f = pi.feed_by_url(feed_url)
        except (requests.RequestException, PermissionError) as e:
            if verbose:
                print(f"  ! {title}: {e}")
            continue
        con.execute(
            """UPDATE podcasts SET pi_feed_id=?, podcast_guid=COALESCE(?, podcast_guid),
               pi_checked_utc=? WHERE podcast_uuid=?""",
            (f.get("id") if f else None, (f or {}).get("podcastGuid") or None, _now(), uuid))
        con.commit()
        found += 1 if f else 0
        if verbose:
            print(f"  {'✓' if f else '✗'} {title}" + (f" -> feed id {f.get('id')}" if f else ""))
    return found


def set_feed(con, podcast_uuid: str, feed_url: str | None):
    con.execute("INSERT OR IGNORE INTO podcasts(podcast_uuid, title) "
                "SELECT podcast_uuid, MAX(podcast_title) FROM episodes WHERE podcast_uuid=?",
                (podcast_uuid,))
    # A new URL may be a different feed, so forget the old Podcast Index ids.
    con.execute("""UPDATE podcasts SET feed_url=?, feed_source=?, resolved_utc=?,
                   pi_feed_id=NULL, podcast_guid=NULL, pi_checked_utc=NULL
                   WHERE podcast_uuid=?""",
                (feed_url or None, "manual" if feed_url else None, _now(), podcast_uuid))
    con.commit()


def feed_list(con) -> list[dict]:
    rows = con.execute(
        """SELECT e.podcast_uuid, MAX(e.podcast_title), p.feed_url, p.feed_source, COUNT(*)
           FROM episodes e LEFT JOIN podcasts p USING (podcast_uuid)
           GROUP BY e.podcast_uuid ORDER BY (p.feed_url IS NOT NULL), MAX(e.podcast_title)"""
    ).fetchall()
    return [{"podcast_uuid": r[0], "podcast_title": r[1], "feed_url": r[2],
             "feed_source": r[3], "episodes": r[4]} for r in rows]


# --------------------------------------------------------------------------- #
# Export formats
# --------------------------------------------------------------------------- #

def _cell(v):
    if isinstance(v, dt.datetime):
        return v.astimezone().strftime("%Y-%m-%d %H:%M")
    return v


def to_opml(report: dict) -> tuple[str, list[str]]:
    """Returns (opml_text, titles_skipped_for_missing_feed)."""
    skipped, outlines = [], []
    for r in report["rows"]:
        if not r.get("feed_url"):
            skipped.append(r["podcast_title"])
            continue
        outlines.append(
            f'      <outline type="rss" text={quoteattr(r["podcast_title"])} '
            f'title={quoteattr(r["podcast_title"])} xmlUrl={quoteattr(r["feed_url"])}/>')
    now = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">\n  <head>\n'
        f"    <title>{escape(report['description'])}</title>\n"
        f"    <dateCreated>{now}</dateCreated>\n"
        "    <docs>http://opml.org/spec2.opml</docs>\n  </head>\n  <body>\n"
        f"    <outline text={quoteattr(report['title'])}>\n"
        + "\n".join(outlines) + "\n    </outline>\n  </body>\n</opml>\n"
    )
    return text, skipped


def to_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(report["columns"])
    for r in report["rows"]:
        w.writerow([_cell(r.get(c)) for c in report["columns"]])
    return "﻿" + buf.getvalue()  # BOM so Excel opens UTF-8 correctly


def listens_table(con) -> dict:
    cur = con.execute("SELECT * FROM v_listens ORDER BY event_id")
    cols = [d[0] for d in cur.description]
    return {"title": "All listens", "description": "Every logged listen", "columns": cols,
            "rows": [dict(zip(cols, r)) for r in cur]}


def to_xlsx(reports: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    for rep in reports:
        name = re.sub(r"[\[\]:*?/\\]", "", rep["title"])[:31] or "Sheet"
        base, i = name, 2
        while name in used:
            name = f"{base[:28]} {i}"
            i += 1
        used.add(name)
        ws = wb.create_sheet(name)
        start = 1
        if rep.get("description") and rep["title"] != "All listens":
            ws.cell(1, 1, rep["description"]).font = Font(italic=True)
            start = 3
        for j, c in enumerate(rep["columns"], 1):
            ws.cell(start, j, c).font = Font(bold=True)
        widths = [len(c) for c in rep["columns"]]
        for i, r in enumerate(rep["rows"], start + 1):
            for j, c in enumerate(rep["columns"], 1):
                v = _cell(r.get(c))
                ws.cell(i, j, v)
                widths[j - 1] = max(widths[j - 1], min(len(str(v)) if v is not None else 0, 60))
        for j, wdt in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = wdt + 2
        ws.freeze_panes = ws.cell(start + 1, 1)
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(rep['columns']))}{start + len(rep['rows'])}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
