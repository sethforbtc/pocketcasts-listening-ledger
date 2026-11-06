"""
Listening Ledger dashboard: a small local web app for your Pocket Casts history.

    python dashboard.py              # http://127.0.0.1:8770 (opens your browser)
    python dashboard.py --port 9000 --no-browser

It only listens on 127.0.0.1, so it's reachable from this computer only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import re
import threading
import webbrowser
import zipfile
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory

import digest
import ledger_reports as lr
import pc_history as pc
import podcast_index as pi

WEB_DIR = Path(__file__).parent / "web"
app = Flask(__name__, static_folder=None)


def db():
    return pc.connect()


def params():
    a = request.args
    unit = a.get("unit") or None
    period_n = a.get("period_n", type=int) if unit and unit != "all" else None
    return dict(
        period_n=period_n,
        unit=unit if period_n else None,
        threshold=max(0.05, min(1.0, a.get("threshold", 75, type=float) / 100)),
    )


def report_args(kind_override=None, n_key="n"):
    p = params()
    a = request.args
    return dict(
        kind=kind_override or a.get("kind", "top_hours"),
        n=a.get(n_key, a.get("n", 10, type=int), type=int),
        include_initial=a.get("include_initial", "1") != "0",
        **p,
    )


def stamp():
    return dt.datetime.now().strftime("%Y%m%d")


def download(body, filename, mimetype):
    return Response(body, mimetype=mimetype,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def jsonable(rep):
    rows = [{k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in r.items()}
            for r in rep["rows"]]
    return {**rep, "rows": rows}


# --------------------------------------------------------------------------- #
# Pages + JSON API
# --------------------------------------------------------------------------- #

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(WEB_DIR, name)


@app.get("/api/stats")
def api_stats():
    p = params()
    con = db()
    return jsonify({"stats": lr.stats(con, p["period_n"], p["unit"], p["threshold"]),
                    "health": lr.health(con)})


@app.get("/api/report")
def api_report():
    try:
        rep = lr.run_report(db(), **report_args())
    except ValueError as e:
        abort(400, str(e))
    return jsonify(jsonable(rep))


@app.get("/api/feeds")
def api_feeds():
    return jsonify(lr.feed_list(db()))


@app.post("/api/feeds/resolve")
def api_feeds_resolve():
    body = request.get_json(silent=True) or {}
    only = body.get("podcast_uuids") or None
    limit = min(int(body.get("limit", 15)), 40)
    found = lr.resolve_missing_feeds(db(), limit=limit, only=only)
    return jsonify({"found": found})


@app.post("/api/feeds/<uuid>")
def api_feed_set(uuid):
    url = ((request.get_json(silent=True) or {}).get("feed_url") or "").strip()
    if url and not re.match(r"^https?://", url):
        abort(400, "Feed URL must start with http:// or https://")
    lr.set_feed(db(), uuid, url or None)
    return jsonify({"ok": True})


@app.get("/api/settings")
def api_settings():
    topic, server, token = pc.ntfy_settings()
    con = db()
    last = con.execute("SELECT value FROM state WHERE key='last_digest_date'").fetchone()
    return jsonify({"ntfy_topic": topic or "", "ntfy_server": server,
                    "ntfy_token_set": bool(token), "data_dir": str(pc.DATA_DIR),
                    "podcastindex_configured": pi.configured(),
                    "last_digest_date": last[0] if last else None,
                    **digest.settings(pc.load_config())})


@app.post("/api/settings")
def api_settings_save():
    body = request.get_json(silent=True) or {}
    cfg = pc.load_config()
    if "ntfy_topic" in body:
        topic = body["ntfy_topic"].strip()
        if topic and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic):
            abort(400, "Topic may only contain letters, numbers, - and _ (max 64).")
        cfg["ntfy_topic"] = topic or None
    if body.get("ntfy_server"):
        cfg["ntfy_server"] = body["ntfy_server"].strip().rstrip("/")
    if "ntfy_token" in body:
        cfg["ntfy_token"] = body["ntfy_token"].strip() or None
    if "digest_enabled" in body:
        cfg["digest_enabled"] = bool(body["digest_enabled"])
    for k, lo, hi in (("digest_shows", 1, 100), ("digest_hour", 0, 23)):
        if k in body:
            try:
                v = int(body[k])
            except (TypeError, ValueError):
                abort(400, f"{k} must be a whole number")
            if not lo <= v <= hi:
                abort(400, f"{k} must be between {lo} and {hi}")
            cfg[k] = v
    pc.save_config(cfg)
    return api_settings()


@app.post("/api/podcastindex")
def api_podcastindex():
    body = request.get_json(silent=True) or {}
    if body.get("clear"):
        pi.clear_credentials()
        return jsonify({"ok": True, "configured": False})
    key, secret = (body.get("key") or "").strip(), (body.get("secret") or "").strip()
    if not (key and secret):
        abort(400, "Enter both the API key and the secret.")
    ok, detail = pi.test_credentials(key, secret)
    if not ok:
        abort(400, f"That key didn't work: {detail}")
    pi.save_credentials(key, secret)
    con = db()
    found = lr.resolve_missing_feeds(con, limit=40)
    matched = lr.enrich_feed_ids(con, limit=40)  # the hourly runs pick up the rest
    return jsonify({"ok": True, "configured": True, "found": found, "matched": matched})


def _digest_json(d):
    return {k: d[k] for k in ("shows_checked", "shows_unmatched", "period", "episodes",
                              "sent", "detail") if k in d}


@app.post("/api/digest/<action>")
def api_digest(action):
    if not pi.configured():
        abort(400, "Add a Podcast Index API key first.")
    s = digest.settings(pc.load_config())
    con = db()
    if action == "preview":
        return jsonify(_digest_json(digest.build(con, s["digest_shows"], s["digest_months"])))
    if action == "send":
        return jsonify(_digest_json(digest.send(con, pc.notify, s["digest_shows"], s["digest_months"])))
    abort(404)


@app.post("/api/notify/test")
def api_notify_test():
    ok, detail = pc.notify("Test from your Pocket Casts Listening Ledger dashboard. Alerts are working.",
                           title="Pocket Casts Ledger test", tags="bell")
    return jsonify({"ok": ok, "detail": detail})


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #

@app.get("/export/report.<fmt>")
def export_report(fmt):
    con = db()
    rep = lr.run_report(con, **report_args())
    name = f"{rep['kind']}_{stamp()}"
    if fmt == "opml":
        text, skipped = lr.to_opml(rep)
        if skipped:
            note = "\n".join(f"     - {t}" for t in skipped)
            text = text.replace(
                "<body>", f"<!-- Skipped (no RSS feed URL on file):\n{note}\n  -->\n  <body>", 1)
        return download(text, f"{name}.opml", "text/x-opml")
    if fmt == "csv":
        return download(lr.to_csv(rep), f"{name}.csv", "text/csv")
    if fmt == "xlsx":
        return download(lr.to_xlsx([rep]), f"{name}.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    abort(404)


@app.get("/export/bundle.<fmt>")
def export_bundle(fmt):
    """Several reports at once. ?reports=top_completed,top_hours,recent&listens=1
    Each report can have its own size: n_top_completed, n_top_hours, n_recent."""
    con = db()
    kinds = [k for k in request.args.get("reports", "").split(",") if k in lr.REPORTS]
    reps = [lr.run_report(con, **report_args(k, f"n_{k}")) for k in kinds]
    if request.args.get("listens") == "1":
        reps.append(lr.listens_table(con))
    if not reps:
        abort(400, "Pick at least one report")
    if fmt == "xlsx":
        return download(lr.to_xlsx(reps), f"listening_ledger_{stamp()}.xlsx",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for r in reps:
                z.writestr(f"{r.get('kind', 'all_listens')}.csv", lr.to_csv(r))
        return download(buf.getvalue(), f"listening_ledger_{stamp()}.zip", "application/zip")
    abort(404)


def main():
    ap = argparse.ArgumentParser(description="Listening Ledger dashboard")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = f"http://127.0.0.1:{a.port}"
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Listening Ledger dashboard on {url}  (Ctrl+C to stop)")
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=a.port, threads=4)
    except ImportError:
        app.run(host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
