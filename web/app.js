/* Listening Ledger dashboard -- no build step, no external libraries. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt1 = (n) => (n == null ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 1 }));
const fmtInt = (n) => (n == null ? "—" : Number(n).toLocaleString());
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SVGNS = "http://www.w3.org/2000/svg";

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.text()).replace(/<[^>]+>/g, " ").trim() || r.statusText);
  return r.json();
}
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function when(iso, withTime = true) {
  if (!iso) return "—";
  const d = new Date(iso);
  return withTime ? d.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })
                  : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
function ago(iso) {
  if (!iso) return "never";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}
const mins = (s) => (s == null ? "—" : `${Math.round(s / 60)} min`);

/* ------------------------------ tooltip ------------------------------ */
const tip = $("#tooltip");
function showTip(e, html) {
  tip.innerHTML = html; tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > innerWidth - 8) x = e.clientX - w - pad;
  if (y + h > innerHeight - 8) y = e.clientY - h - pad;
  tip.style.left = `${x}px`; tip.style.top = `${y}px`;
}
const hideTip = () => (tip.hidden = true);

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), f = v / p;
  return [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((c) => f <= c) * p;
}
// Bar path with 4px rounded top, square base on the axis.
function barPath(x, y, w, h, r = 4) {
  r = Math.min(r, w / 2, h);
  if (h <= 0) return "";
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function hbarPath(x, y, w, h, r = 4) {
  r = Math.min(r, h / 2, w);
  if (w <= 0) return "";
  return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
}

/* ------------------------------ charts ------------------------------ */
function columnChart(host, data, { height = 220, yFmt = fmt1, labelEvery = 1, tipFn }) {
  host.innerHTML = "";
  if (!data.length || data.every((d) => !d.value)) { host.innerHTML = `<div class="empty">Nothing logged in this period yet.</div>`; return; }
  const W = Math.max(320, host.clientWidth), H = height, m = { t: 10, r: 6, b: 26, l: 40 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const max = niceMax(Math.max(...data.map((d) => d.value)) / 4) * 4;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, host);
  for (let i = 0; i <= 4; i++) {
    const y = m.t + ih - (ih * i) / 4;
    el("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, class: i ? "gridline" : "axis" }, svg);
    el("text", { x: m.l - 6, y: y + 4, "text-anchor": "end" }, svg).textContent = yFmt((max * i) / 4);
  }
  const step = iw / data.length, bw = Math.max(2, Math.min(36, step - 2));
  data.forEach((d, i) => {
    const x = m.l + i * step + (step - bw) / 2, h = (d.value / max) * ih, y = m.t + ih - h;
    const bar = el("path", { d: barPath(x, y, bw, h), class: "bar" }, svg);
    const hit = el("rect", { x: m.l + i * step, y: m.t, width: step, height: ih, class: "hit" }, svg);
    svg.insertBefore(hit, bar);
    hit.addEventListener("mousemove", (e) => { bar.classList.add("hl"); showTip(e, tipFn(d)); });
    hit.addEventListener("mouseleave", () => { bar.classList.remove("hl"); hideTip(); });
    if (i % labelEvery === 0)
      el("text", { x: m.l + i * step + step / 2, y: H - 8, "text-anchor": "middle" }, svg).textContent = d.label;
  });
}

function barList(host, data, { valFmt = fmt1, tipFn }) {
  host.innerHTML = "";
  if (!data.length) { host.innerHTML = `<div class="empty">Nothing logged in this period yet.</div>`; return; }
  const W = Math.max(320, host.clientWidth), row = 26, labelW = Math.min(210, W * 0.42), valW = 46;
  const H = data.length * row + 4;
  const max = Math.max(...data.map((d) => d.value)) || 1;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, host);
  const iw = W - labelW - valW - 8;
  data.forEach((d, i) => {
    const y = i * row + 4, w = (d.value / max) * iw;
    let label = d.label; if (label.length > 30) label = label.slice(0, 29) + "…";
    el("text", { x: labelW - 8, y: y + 13, "text-anchor": "end" }, svg).textContent = label;
    const bar = el("path", { d: hbarPath(labelW, y + 3, w, 14), class: "bar" }, svg);
    el("text", { x: labelW + w + 6, y: y + 14, class: "val" }, svg).textContent = valFmt(d.value);
    const hit = el("rect", { x: 0, y, width: W, height: row, class: "hit" }, svg);
    hit.addEventListener("mousemove", (e) => { bar.classList.add("hl"); showTip(e, tipFn(d)); });
    hit.addEventListener("mouseleave", () => { bar.classList.remove("hl"); hideTip(); });
  });
}

function heatmap(host, matrix) {
  host.innerHTML = "";
  const max = Math.max(0, ...matrix.flat());
  if (!max) { host.innerHTML = `<div class="empty">Not enough dated listens yet. This fills in as hourly runs log new episodes.</div>`; return; }
  const W = Math.max(360, host.clientWidth), m = { t: 4, l: 36, b: 20, r: 4 };
  const cw = (W - m.l - m.r) / 24, ch = Math.min(26, cw * 0.9), H = m.t + ch * 7 + m.b;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" }, host);
  const steps = 7;
  matrix.forEach((row, d) => {
    el("text", { x: m.l - 6, y: m.t + d * ch + ch / 2 + 4, "text-anchor": "end" }, svg).textContent = DAYS[d];
    row.forEach((v, h) => {
      const lvl = v ? Math.max(1, Math.ceil((v / max) * steps)) : 0;
      const r = el("rect", { x: m.l + h * cw + 1, y: m.t + d * ch + 1, width: cw - 2, height: ch - 2, rx: 3,
        fill: `var(--seq-${lvl})` }, svg);
      r.addEventListener("mousemove", (e) => showTip(e, `<b>${DAYS[d]} ${hourLabel(h)}–${hourLabel(h + 1)}</b>${v} episode${v === 1 ? "" : "s"}`));
      r.addEventListener("mouseleave", hideTip);
    });
  });
  for (let h = 0; h < 24; h += 3)
    el("text", { x: m.l + h * cw + cw / 2, y: H - 5, "text-anchor": "middle" }, svg).textContent = hourLabel(h);
  const lg = document.createElement("div");
  lg.className = "legend";
  lg.innerHTML = "Fewer " + [1, 2, 3, 4, 5, 6, 7].map((i) => `<i style="background:var(--seq-${i})"></i>`).join("") + " More";
  host.appendChild(lg);
}
const hourLabel = (h) => { h %= 24; return h === 0 ? "12a" : h < 12 ? `${h}a` : h === 12 ? "12p" : `${h - 12}p`; };

function table(host, cols, rows, empty = "Nothing here yet.") {
  if (!rows.length) { host.innerHTML = `<div class="empty">${empty}</div>`; return; }
  host.innerHTML = `<table><thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead><tbody>${
    rows.map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "num" : ""} ${c.wrap ? "wrap" : ""}">${c.html ? c.html(r) : esc(c.get(r))}</td>`).join("")}</tr>`).join("")
  }</tbody></table>`;
}

/* ------------------------------ tabs ------------------------------ */
$$(".tabs button").forEach((b) => b.addEventListener("click", () => {
  $$(".tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
  $$(".tab-panel").forEach((p) => (p.hidden = p.id !== `tab-${b.dataset.tab}`));
  ({ overview: loadOverview, reports: loadReport, feeds: loadFeeds, settings: loadSettings })[b.dataset.tab]();
  location.hash = b.dataset.tab;
}));

/* ------------------------------ overview ------------------------------ */
let lastStats = null;
function periodQS(sel) {
  const v = sel.value;
  if (v === "all") return "unit=all";
  const [n, u] = v.split(":");
  return `period_n=${n}&unit=${u}`;
}
async function loadOverview() {
  const qs = `${periodQS($("#ov-period"))}&threshold=${$("#ov-threshold").value}`;
  const { stats, health } = await api(`/api/stats?${qs}`);
  lastStats = stats;
  renderHealthLine(health);
  renderOverview();
}
function renderOverview() {
  const s = lastStats; if (!s) return;
  const k = s.kpis;
  $("#kpis").innerHTML = [
    ["Hours listened", fmt1(k.hours)], ["Episodes", fmtInt(k.episodes)], ["Shows", fmtInt(k.shows)],
    ["Completion rate", k.completion_rate == null ? "—" : `${k.completion_rate}%`],
    ["Avg episode length", k.avg_episode_min == null ? "—" : `${k.avg_episode_min} min`],
    ["Tracking since", k.tracking_since ? new Date(k.tracking_since).toLocaleDateString(undefined, { month: "short", year: "numeric" }) : "—"],
  ].map(([l, v]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  const note = $("#undated-note");
  note.hidden = !s.undated_episodes;
  note.textContent = `${s.undated_episodes} episode(s) came from the initial import and have no reliable listen date. They count in the totals but not in the time-based charts.`;

  const weeks = s.weekly_hours;
  columnChart($("#ch-weekly"), weeks.map((w) => ({ label: new Date(w.week + "T00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }), value: w.hours, week: w.week })), {
    labelEvery: Math.ceil(weeks.length / 9),
    tipFn: (d) => `<b>Week of ${esc(d.label)}</b>${fmt1(d.value)} hours`,
  });
  $("#top-sub").textContent = `Hours played per show, ${s.period}.`;
  barList($("#ch-top"), s.top_shows_hours.map((r) => ({ label: r.podcast_title, value: r.hours, eps: r.episodes })), {
    tipFn: (d) => `<b>${esc(d.label)}</b>${fmt1(d.value)} hours · ${d.eps} episode${d.eps === 1 ? "" : "s"}`,
  });
  const c = s.completion, tot = c.completed + c.in_progress + c.sampled || 1;
  $("#completion").innerHTML = `<div class="split">
      <div><div class="v">${fmtInt(c.completed)}</div><div class="l">Completed · ${Math.round(100 * c.completed / tot)}%</div></div>
      <div><div class="v">${fmtInt(c.in_progress)}</div><div class="l">Partly played · ${Math.round(100 * c.in_progress / tot)}%</div></div>
      <div><div class="v">${fmtInt(c.sampled)}</div><div class="l">Sampled · ${Math.round(100 * c.sampled / tot)}%</div></div>
    </div>
    <div class="bar100" aria-hidden="true"><span style="flex:${c.completed}"></span><span style="flex:${c.in_progress}"></span><span style="flex:${c.sampled}"></span></div>`;
  heatmap($("#ch-heat"), s.heatmap);
  columnChart($("#ch-new"), s.new_shows_by_month.slice(-18).map((r) => ({ label: new Date(r.month + "-01T00:00").toLocaleDateString(undefined, { month: "short" }) + " ’" + r.month.slice(2, 4), value: r.shows })), {
    height: 200, yFmt: (v) => (Number.isInteger(v) ? v : ""), labelEvery: 1,
    tipFn: (d) => `<b>${esc(d.label)}</b>${d.value} new show${d.value === 1 ? "" : "s"}`,
  });
  table($("#tbl-progress"), [
    { label: "Episode", wrap: true, html: (r) => `<b>${esc(r.episode_title)}</b><br><span class="muted">${esc(r.podcast_title)}</span>` },
    { label: "Played", num: true, get: (r) => `${Math.floor(r.pct_played)}%` },
    { label: "Last played", get: (r) => (r.initial_only ? "before tracking" : when(r.last_listen, false)) },
  ], s.in_progress, "No unfinished episodes. Nice.");
  table($("#tbl-recent"), [
    { label: "Logged", get: (r) => (r.initial_only ? "initial import" : when(r.last_listen)) },
    { label: "Show", get: (r) => r.podcast_title },
    { label: "Episode", wrap: true, get: (r) => r.episode_title },
    { label: "Length", num: true, get: (r) => mins(r.duration_sec) },
    { label: "Played", num: true, get: (r) => `${Math.round(r.pct_played)}%` },
    { label: "Plays", num: true, get: (r) => r.listens },
  ], s.recent);
}
$("#ov-period").addEventListener("change", loadOverview);
$("#ov-threshold").addEventListener("change", loadOverview);
let rz; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(renderOverview, 150); });

function renderHealthLine(h) {
  const line = $("#health-line");
  if (h.consecutive_failures >= 3) line.innerHTML = `<span class="status bad">Collector failing: ${h.consecutive_failures} runs in a row</span>`;
  else if (h.consecutive_failures) line.innerHTML = `<span class="status warn">Last run failed</span> · last success ${ago(h.last_success_utc)}`;
  else line.innerHTML = `<span class="status good">Collector OK</span> · last checked ${ago(h.last_success_utc)}`;
}

/* ------------------------------ reports ------------------------------ */
function reportQS() {
  const unit = $("#rp-unit").value;
  const q = new URLSearchParams({
    kind: $("#rp-kind").value, n: $("#rp-n").value, unit,
    threshold: $("#rp-threshold").value, include_initial: $("#rp-initial").checked ? "1" : "0",
  });
  if (unit !== "all") q.set("period_n", $("#rp-period-n").value);
  return q;
}
let lastReport = null;
async function loadReport() {
  const kind = $("#rp-kind").value;
  $("#rp-n-label").textContent = kind === "recent" ? "Most recent episodes" : "How many shows";
  $("#rp-threshold-wrap").style.visibility = kind === "top_completed" ? "visible" : "hidden";
  $("#rp-period-n").disabled = $("#rp-unit").value === "all";
  const rep = (lastReport = await api(`/api/report?${reportQS()}`));
  $("#rp-desc").textContent = rep.description;
  const missing = rep.rows.filter((r) => !r.feed_url);
  const warn = $("#rp-feedwarn");
  warn.hidden = !missing.length;
  if (missing.length) {
    warn.innerHTML = `<span>${missing.length} of ${rep.rows.length} show(s) have no RSS feed on file yet. They'll be left out of the OPML file.</span>
      <button id="rp-resolve">Look them up now</button>`;
    $("#rp-resolve").onclick = async (e) => {
      e.target.disabled = true; e.target.textContent = `Looking up (about ${missing.length * 3}s)…`;
      try { await post("/api/feeds/resolve", { podcast_uuids: missing.map((r) => r.podcast_uuid), limit: 40 }); }
      finally { loadReport(); }
    };
  }
  const COLS = {
    rank: { label: "#", num: true }, podcast_title: { label: "Show" }, author: { label: "By" },
    completed: { label: "Completed", num: true }, episodes: { label: "Episodes", num: true },
    hours: { label: "Hours", num: true, f: fmt1 }, last_listen: { label: "Last listened", f: (v) => when(v, false) },
    feed_url: { label: "Feed", html: true },
  };
  table($("#rp-table"), rep.columns.map((c) => ({
    label: COLS[c].label, num: COLS[c].num,
    ...(c === "feed_url" ? { html: (r) => (r.feed_url ? `<span class="pill ok">on file</span>` : `<span class="pill miss">missing</span>`) }
                         : { get: (r) => (COLS[c].f ? COLS[c].f(r[c]) : r[c] ?? "—") }),
  })), rep.rows, "No listens match this period.");
}
["#rp-kind", "#rp-n", "#rp-period-n", "#rp-unit", "#rp-threshold", "#rp-initial"].forEach((s) => $(s).addEventListener("change", loadReport));
$$("[data-dl]").forEach((b) => b.addEventListener("click", () => { location.href = `/export/report.${b.dataset.dl}?${reportQS()}`; }));

function bundleQS() {
  const q = reportQS(); q.delete("kind"); q.delete("n");
  const picked = $$("input[name=bundle]:checked").map((x) => x.value);
  q.set("reports", picked.join(","));
  picked.forEach((k) => q.set(`n_${k}`, $(`#bn-${k}`).value));
  if ($("#bn-listens").checked) q.set("listens", "1");
  if (!picked.length && !$("#bn-listens").checked) { alert("Pick at least one thing to export."); return null; }
  return q;
}
$("#bn-xlsx").onclick = () => { const q = bundleQS(); if (q) location.href = `/export/bundle.xlsx?${q}`; };
$("#bn-zip").onclick = () => { const q = bundleQS(); if (q) location.href = `/export/bundle.zip?${q}`; };

/* ------------------------------ feeds ------------------------------ */
async function loadFeeds() {
  const rows = await api("/api/feeds");
  const have = rows.filter((r) => r.feed_url).length;
  $("#fd-status").textContent = `${have} of ${rows.length} shows have a feed URL.`;
  table($("#fd-table"), [
    { label: "Show", wrap: true, get: (r) => r.podcast_title },
    { label: "Episodes", num: true, get: (r) => r.episodes },
    { label: "Source", html: (r) => (r.feed_url ? `<span class="pill ok">${esc(r.feed_source || "found")}</span>` : `<span class="pill miss">${r.feed_source === "notfound" ? "no match" : "not looked up"}</span>`) },
    { label: "RSS feed URL", html: (r) => `<input class="feed-input" data-uuid="${esc(r.podcast_uuid)}" value="${esc(r.feed_url || "")}" placeholder="https://… paste feed URL">` },
  ], rows, "No shows logged yet.");
  $$(".feed-input").forEach((inp) => inp.addEventListener("change", async () => {
    try { await post(`/api/feeds/${inp.dataset.uuid}`, { feed_url: inp.value.trim() }); inp.style.borderColor = "var(--good)"; }
    catch (e) { inp.style.borderColor = "var(--critical)"; alert(e.message); }
  }));
}
$("#fd-resolve").onclick = async (e) => {
  e.target.disabled = true; $("#fd-status").textContent = "Looking up up to 40 shows (about 3 seconds each)…";
  try { const r = await post("/api/feeds/resolve", { limit: 40 }); $("#fd-status").textContent = `Found ${r.found}.`; }
  catch (err) { $("#fd-status").textContent = err.message; }
  finally { e.target.disabled = false; loadFeeds(); }
};

/* ------------------------------ settings ------------------------------ */
async function loadSettings() {
  const [{ health: h }, cfg] = await Promise.all([api("/api/stats?unit=all"), api("/api/settings")]);
  renderHealthLine(h);
  const st = h.consecutive_failures >= 3 ? ["bad", "Failing"] : h.consecutive_failures ? ["warn", "Last run failed"] : ["good", "Healthy"];
  $("#health").innerHTML = `<dl class="health">
    <dt>Status</dt><dd><span class="status ${st[0]}">${st[1]}</span></dd>
    <dt>Last success</dt><dd>${when(h.last_success_utc)} (${ago(h.last_success_utc)})</dd>
    <dt>Failures in a row</dt><dd>${h.consecutive_failures}</dd>
    <dt>Last 24 hours</dt><dd>${h.runs_24h.ok} OK · ${h.runs_24h.failed} failed</dd>
    <dt>Feeds on file</dt><dd>${h.feeds.resolved} of ${h.feeds.shows} shows</dd>
    <dt>Data folder</dt><dd><code>${esc(cfg.data_dir)}</code></dd></dl>
    ${h.last_run && !h.last_run.ok ? `<pre class="err">${esc(h.last_run.error)}</pre>` : ""}`;
  $("#ntfy-topic").value = cfg.ntfy_topic;
  $("#ntfy-server").value = cfg.ntfy_server;
}
$("#ntfy-gen").onclick = () => {
  const a = new Uint8Array(10); crypto.getRandomValues(a);
  $("#ntfy-topic").value = "ledger-" + [...a].map((b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");
};
$("#ntfy-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await post("/api/settings", { ntfy_topic: $("#ntfy-topic").value, ntfy_server: $("#ntfy-server").value || "https://ntfy.sh" });
    $("#ntfy-status").textContent = "Saved.";
  } catch (err) { $("#ntfy-status").textContent = err.message; }
};
$("#ntfy-test").onclick = async () => {
  $("#ntfy-status").textContent = "Sending…";
  const r = await post("/api/notify/test");
  $("#ntfy-status").textContent = r.ok ? "Sent. Check your phone." : `Failed: ${r.detail}. Did you save a topic first?`;
};

/* ------------------------------ boot ------------------------------ */
api("/api/stats?unit=all").then((d) => renderHealthLine(d.health)).catch(() => ($("#health-line").textContent = "Can't reach the database."));
const start = (location.hash || "#overview").slice(1);
($(`.tabs button[data-tab="${start}"]`) || $(".tabs button")).click();
