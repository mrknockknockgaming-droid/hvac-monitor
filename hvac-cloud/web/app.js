/* Fullscope web app: homeowner (#/home/<id>) and technician (#/monitor/<id>) views over the cloud API.
   Layout and styles follow design/fullscope-home-mockup.html and fullscope-monitor-mockup.html; values
   the hardware does not measure yet (static pressure, humidity, capacity) are shown as not installed. */
(function () {
"use strict";

var $ = function (s, r) { return (r || document).querySelector(s); };
var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
var root = $("#root");
var NS = "http://www.w3.org/2000/svg";

// ---------------------------------------------------------------- storage (per-viewer convenience only)
function load(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function save(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} }

$("#theme").addEventListener("click", function () {
  var r = document.documentElement;
  r.dataset.theme = r.dataset.theme === "light" ? "dark" : "light";
  save("fs_theme", r.dataset.theme);
  if (S.view) render();
});

// ---------------------------------------------------------------- state
var S = {
  key: load("fs_key"), systems: null, sys: null, view: null,
  latest: null, summary: null, history: {}, alerts: null, commands: null, pending: {}, form: {}, msg: null, range: load("fs_range") || "1h",
  preset: load("fs_preset") || "refrigerant", off: {}, lastOk: null, error: null, timers: []
};
var LIVE_MS = 5000, STALE_S = 30, OUTAGE_S = 90;   // a gap longer than OUTAGE_S breaks chart lines

// ---------------------------------------------------------------- API
function api(path, opts) {
  opts = opts || {};
  var headers = { "X-API-Key": S.key };
  if (opts.body) headers["Content-Type"] = "application/json";
  return fetch(path, { method: opts.method || "GET", headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined })
    .then(function (r) {
      if (r.status === 401) { signOut("That API key was not accepted."); throw new Error("401"); }
      if (!r.ok) throw new Error(r.status + " " + r.statusText);
      return opts.raw ? r : r.json();
    });
}

// ---------------------------------------------------------------- formatting
function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
function isNum(v) { return typeof v === "number" && isFinite(v); }
function fmt(v, d) { return isNum(v) ? v.toFixed(d === undefined ? 1 : d) : "—"; }
function num(v, unit, d) { return isNum(v) ? '<span class="n">' + fmt(v, d) + (unit ? "<small>" + unit + "</small>" : "") + "</span>" : '<span class="n na">—</span>'; }
function clock(t, secs) {
  var d = t instanceof Date ? t : new Date(t), p = function (x) { return (x < 10 ? "0" : "") + x; };
  return p(d.getHours()) + ":" + p(d.getMinutes()) + (secs ? ":" + p(d.getSeconds()) : "");
}
function ampm(t) { return new Date(t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
function when(t) { return new Date(t).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }) + ", " + ampm(t); }
function lasted(a) { return ago(((a.cleared_at ? new Date(a.cleared_at) : new Date()).getTime() - new Date(a.started_at).getTime()) / 1000); }
function ago(s) { return !isNum(s) ? "—" : s < 90 ? Math.round(s) + " s" : s < 5400 ? Math.round(s / 60) + " min" : (s / 3600).toFixed(1) + " h"; }
var LOGO = '<div class="brand logo" title="Fullscope — Continuous diagnostics &amp; control"><img src="fullscope-wordmark.webp" alt="Fullscope"></div>';
var TAGLINE = '<div class="tagline caps">Continuous diagnostics &amp; control</div>';
function st(cls, word) { return '<span class="st ' + cls + '">' + esc(word) + "</span>"; }
var MODE_WORD = { cooling: "Cooling", heating: "Heating", heat_aux: "Aux heat", fan: "Fan only", idle: "Idle" };

// ---------------------------------------------------------------- flags -> meaning
// level: fault (needs service), caution (check soon), advisory (good to know)
var FLAG_INFO = {
  sh_low: { level: "fault", area: "refrigerant", title: "Refrigerant flow needs a check",
    sub: "Call your contractor soon.", why: "Liquid refrigerant may be reaching the compressor, which can damage it over time.",
    todo: ["Request a service visit.", "If it keeps showing, ask whether to run the system less until then."] },
  sh_high: { level: "caution", area: "refrigerant", title: "Refrigerant may be low or restricted",
    sub: "A technician should take a look.", why: "Your system works harder and cools less. A leak or a restriction are common causes.",
    todo: ["Request a service visit.", "Check that the outdoor unit isn't blocked by plants or debris."] },
  sc_low: { level: "caution", area: "refrigerant", title: "Refrigerant charge may be low",
    sub: "A technician should take a look.", why: "Low charge reduces cooling and efficiency, and usually means a leak.",
    todo: ["Request a service visit."] },
  sc_high: { level: "caution", area: "refrigerant", title: "Refrigerant may be overcharged or restricted",
    sub: "A technician should take a look.", why: "Too much charge or a restriction raises pressures and strains the compressor.",
    todo: ["Request a service visit."] },
  dt_low: { level: "caution", area: "cooling", title: "Air from your vents isn't as cool as it should be",
    sub: "Often a dirty filter or restricted airflow.", why: "Your system runs longer and uses more electricity for the same comfort.",
    todo: ["Check your air filter and replace it if it looks grey or dusty.", "Make sure vents and return grilles aren't closed or blocked.", "If it's still showing tomorrow, request a service visit."] },
  ctoa_high: { level: "caution", area: "outdoor", title: "Outdoor unit isn't releasing heat well",
    sub: "Often a dirty coil or blocked airflow outside.", why: "The compressor runs hotter and uses more electricity.",
    todo: ["Clear leaves, plants and debris from around the outdoor unit (keep about 2 ft clear).", "Request a service visit to clean the coil and check the fan."] },
  node_offline: { level: "advisory", area: "monitoring", title: "A monitor isn't reporting",
    sub: "Doesn't affect heating or cooling.", why: "One of the Fullscope monitors has stopped sending readings, so some checks are paused.",
    todo: ["Nothing to do on your own; your contractor can check it."] },
  sensor_issue: { level: "advisory", area: "monitoring", title: "A monitoring sensor isn't reporting",
    sub: "Doesn't affect heating or cooling.", why: "One sensor isn't giving a reading. Your system runs the same; it only gives your technician extra information.",
    todo: ["Nothing to do on your own; your contractor can check it."] },
  no_data: { level: "advisory", area: "monitoring", title: "We lost contact with your monitors",
    sub: "Usually WiFi or power.", why: "Neither monitor was sending readings, so no checks could run.",
    todo: ["Check that your WiFi is working.", "If it keeps happening, your contractor can check the monitors."] }
};
var LEVEL = { fault: { word: "Service needed", rank: 3 }, caution: { word: "Check soon", rank: 2 }, advisory: { word: "Good to know", rank: 1 }, ok: { word: "Good", rank: 0 } };
var TECH_LEVEL = { fault: "Fault", caution: "Warning", advisory: "Advisory" };

function flags() { return ((S.latest && S.latest.derived) || {}).flags || []; }
function flagInfo(f) { return FLAG_INFO[f.code] || { level: f.level === "alert" ? "fault" : "caution", area: "other", title: f.text, sub: "", why: f.text, todo: [] }; }
function hasFlag(code) { return flags().some(function (f) { return f.code === code; }); }
function worstLevel() {
  var w = "ok";
  flags().forEach(function (f) { var l = flagInfo(f).level; if (LEVEL[l].rank > LEVEL[w].rank) w = l; });
  return w;
}
function nodesOnline() {
  var n = (S.latest && S.latest.nodes) || {};
  return ["outdoor", "indoor"].filter(function (k) { return n[k] && n[k].online; }).length;
}
function dataFresh() {
  return S.latest && S.latest.time && (Date.now() - new Date(S.latest.time).getTime()) / 1000 <= STALE_S;
}

// ---------------------------------------------------------------- routing / loop
function route() {
  var m = location.hash.match(/^#\/(home|monitor|setup)\/(\d+)/);
  if (!S.key) return renderLogin();
  if (!S.systems) return start();
  if (!m) {
    if (!S.systems.length) return renderNoSystems();
    location.replace("#/home/" + S.systems[0].id);
    return;
  }
  var id = +m[2], sys = S.systems.filter(function (x) { return x.id === id; })[0];
  if (!sys) { location.replace("#/home/" + S.systems[0].id); return; }
  var changed = !S.sys || S.sys.id !== id;
  S.view = m[1];
  S.sys = sys;
  if (changed) { S.latest = null; S.summary = null; S.history = {}; S.alerts = null; S.commands = null; S.pending = {}; S.form = {}; stopTimers(); startTimers(); }
  if (S.view === "setup") pollCommands();
  render();
}
window.addEventListener("hashchange", route);

function start() {
  root.innerHTML = '<div class="login"><div class="empty">Loading…</div></div>';
  api("/api/systems").then(function (list) { S.systems = list; route(); })
    .catch(function (e) { if (e.message !== "401") { S.error = e.message; renderLogin(); } });
}

function stopTimers() { S.timers.forEach(clearInterval); S.timers = []; }
function startTimers() {
  pollLatest(); pollSummary(); pollHistory(); pollAlerts();
  S.timers.push(setInterval(pollLatest, LIVE_MS));
  S.timers.push(setInterval(pollSummary, 60000));
  S.timers.push(setInterval(pollAlerts, 30000));
  S.timers.push(setInterval(function () { if (S.view === "setup") pollCommands(); }, 2500));
  S.timers.push(setInterval(pollHistory, 30000));
}
function pollLatest() {
  var id = S.sys.id;
  api("/api/systems/" + id + "/latest").then(function (d) {
    if (!S.sys || S.sys.id !== id) return;
    S.latest = d; S.lastOk = Date.now(); S.error = null; render();
  }).catch(function (e) { if (e.message !== "401") { S.error = e.message; render(); } });
}
function pollSummary() {
  var id = S.sys.id;
  api("/api/systems/" + id + "/summary?hours=24").then(function (d) { if (S.sys && S.sys.id === id) { S.summary = d; render(); } }).catch(function () {});
}
function pollAlerts() {
  var id = S.sys.id;
  api("/api/systems/" + id + "/alerts?days=7").then(function (d) { if (S.sys && S.sys.id === id) { S.alerts = d; render(); } }).catch(function () {});
}
var RANGES = { "15m": { min: 15, label: "15 min" }, "1h": { min: 60, label: "1 hr" }, "6h": { min: 360, label: "6 hr" }, "24h": { min: 1440, label: "24 hr" }, "7d": { min: 10080, label: "7 days" } };
function pollHistory() {
  var id = S.sys.id, want = S.view === "home" ? ["24h"] : [S.range, "24h"];
  want.forEach(function (r) {
    api("/api/systems/" + id + "/history?minutes=" + RANGES[r].min).then(function (rows) {
      if (S.sys && S.sys.id === id) { S.history[r] = rows; render(); }
    }).catch(function () {});
  });
}

function signOut(msg) {
  stopTimers();
  S.key = null; S.systems = null; S.sys = null; S.view = null; S.error = msg || null;
  save("fs_key", null);
  renderLogin();
}

// ---------------------------------------------------------------- login
function renderLogin() {
  root.className = "fs home";
  root.innerHTML =
    '<header class="h-top">' + LOGO + TAGLINE + "</header>" +
    '<div class="login"><div class="h-card">' +
    "<h1>Sign in</h1><p>Paste the API key for your account (from <span class=\"mono\">manage.py create-key</span>).</p>" +
    '<form id="lf"><input id="k" type="password" autocomplete="off" placeholder="hvk_…" aria-label="API key">' +
    '<div class="row"><button class="h-btn primary" type="submit">Sign in</button></div>' +
    '<div class="err" role="alert">' + esc(S.error || "") + "</div></form></div></div>";
  $("#lf").addEventListener("submit", function (e) {
    e.preventDefault();
    var k = $("#k").value.trim();
    if (!k) return;
    S.key = k; S.error = null; save("fs_key", k); S.systems = null;
    route();
  });
  $("#k").focus();
}

function renderNoSystems() {
  root.className = "fs home";
  root.innerHTML = '<div class="login"><div class="h-card"><h1>No systems yet</h1>' +
    "<p>Create one with <span class=\"mono\">manage.py create-system</span>, then reload.</p>" +
    '<div class="row"><button class="h-btn" type="button" id="so">Sign out</button></div></div></div>';
  $("#so").addEventListener("click", function () { signOut(); });
}

// ---------------------------------------------------------------- render dispatch (keeps scroll + open details)
function render() {
  if (!S.view || !S.sys) return;
  var open = $$("details[open]").map(function (d) { return d.id; });
  var y = window.scrollY;
  var act = document.activeElement, focusId = act && act.id && root.contains(act) ? act.id : null;
  var selStart = focusId && act.selectionStart, selEnd = focusId && act.selectionEnd;
  if (S.view === "home") renderHome(); else if (S.view === "setup") renderSetup(); else renderMonitor();
  open.forEach(function (id) { var d = document.getElementById(id); if (d) d.open = true; });
  // re-rendering every 5 s must not wipe what the technician is typing
  $$("input[id^='f-']", root).forEach(function (i) { if (S.form[i.id] !== undefined) i.value = S.form[i.id]; });
  var f = focusId && document.getElementById(focusId);
  if (f) { f.focus(); try { if (selStart !== null && selStart !== undefined) f.setSelectionRange(selStart, selEnd); } catch (e) {} }
  window.scrollTo(0, y);
}

// ================================================================ HOMEOWNER VIEW
function renderHome() {
  root.className = "fs home";
  var L = S.latest, d = (L && L.derived) || {}, sm = S.summary || {};
  var fresh = dataFresh(), level = fresh ? worstLevel() : "offline";
  var mode = d.mode || "idle", running = mode === "cooling" || mode === "heating";

  // hero message
  var h1, p;
  var top = flags().map(flagInfo).filter(function (i) { return i.level !== "advisory"; })
    .sort(function (a, b) { return LEVEL[b.level].rank - LEVEL[a.level].rank; })[0];
  if (!L) { h1 = "Connecting to your system…"; p = ""; }
  else if (!fresh) { h1 = "We can't reach your system's monitors"; p = "No readings for " + ago((Date.now() - new Date(L.time).getTime()) / 1000) + ". This is usually a WiFi or power interruption; heating and cooling are not affected."; }
  else if (top) { h1 = "Your system is " + (MODE_WORD[mode] || "running").toLowerCase() + ", but " + top.title.charAt(0).toLowerCase() + top.title.slice(1); p = top.why; }
  else if (running) { h1 = "Your system is " + MODE_WORD[mode].toLowerCase() + " normally"; p = "Everything we check looks right for today's conditions."; }
  else if (mode === "fan") { h1 = "Your fan is running"; p = "The blower is on without heating or cooling."; }
  else if (mode === "heat_aux") { h1 = "Your auxiliary heat is on"; p = "Backup electric heat is running."; }
  else { h1 = "Your system is idle"; p = "It's waiting for the thermostat to call for heating or cooling. We check everything each time it runs."; }

  var kicker = fresh ? st(level, LEVEL[level].word) : st("offline", "Offline");
  var runTxt = running ? MODE_WORD[mode] + " · running for " + Math.round(d.run_min || 0) + " min" : MODE_WORD[mode] || "";
  var heroColor = { ok: "var(--ok)", advisory: "var(--advisory)", caution: "var(--caution)", fault: "var(--fault)", offline: "var(--offline)" }[level];
  var dtTxt = isNum(d.t_ret) && isNum(d.t_sup) ? Math.abs(Math.round(d.t_ret - d.t_sup)) + "° " + (d.t_sup < d.t_ret ? "cooler" : "warmer") + " than inside" : "Supply air";

  var html =
    '<header class="h-top">' + LOGO + TAGLINE +
    '<div class="who"><b>' + esc(S.sys.name) + "</b><span>" + esc(S.sys.refrigerant) + (S.systems.length > 1 ? "" : "") + "</span></div>" +
    '<div class="upd">' + (fresh ? '<span class="live">LIVE</span>' : st("offline", "Offline")) +
    "<span>" + (L && L.time ? "Updated " + ampm(L.time) : "") + "</span>" +
    systemPicker("home") +
    '<a class="muted" href="#/monitor/' + S.sys.id + '" style="font-size:12px">Details for your technician →</a>' +
    '<a class="muted" href="#" data-signout style="font-size:12px">Sign out</a></div></header>' +
    '<main class="h-wrap">' +
    '<section class="h-card h-hero" aria-label="System status" style="border-top-color:' + heroColor + '"><div class="msg">' +
    '<div class="kicker">' + kicker + "<span>" + esc(runTxt) + "</span></div>" +
    "<h1>" + esc(h1) + "</h1><p>" + esc(p) + "</p>" +
    (top ? '<div class="acts"><a class="h-btn primary" href="#" data-open="h-issue-0">What should I do?</a></div>' : "") +
    '</div><div class="h-now">' +
    "<div><span>Inside</span><b>" + big(d.t_ret) + "</b><em>Air returning to the system</em></div>" +
    "<div><span>Outside</span><b>" + big(d.oat) + "</b><em>" + (isNum(d.orh) ? "Humidity " + Math.round(d.orh) + " %" : "At the outdoor unit") + "</em></div>" +
    "<div><span>Air from vents</span><b>" + big(d.t_sup) + "</b><em>" + esc(dtTxt) + "</em></div>" +
    "<div><span>Running, last 24 h</span><b>" + (isNum(sm.runtime_hours) ? fmt(sm.runtime_hours, 1) + "<small>hr</small>" : "—") + "</b><em>" +
    (isNum(sm.cycles) ? sm.cycles + " cycle" + (sm.cycles === 1 ? "" : "s") : "") + "</em></div>" +
    "</div></section>" +
    '<div class="h-grid"><div class="h-col">' + homeNoticed(fresh) + homeAlerts() + "</div>" +
    '<div class="h-col">' + homeHealth(fresh, running) + homeChart() + "</div></div>" +
    '<div class="h-foot"><span class="foot-brand"><b>Fullscope</b><span class="caps">Continuous diagnostics &amp; control</span></span><span>Checks run every 5 seconds while the system is on</span></div>' +
    "</main>";
  root.innerHTML = html;
  bindCommon();
  $$("[data-open]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      var el = document.getElementById(a.dataset.open);
      if (el) { el.open = true; el.scrollIntoView({ behavior: "smooth", block: "start" }); }
    });
  });
  drawHomeChart();
}

function big(v) { return isNum(v) ? Math.round(v) + "<small>°F</small>" : "—"; }

function homeNoticed(fresh) {
  var items = flags().map(function (f) { return { f: f, i: flagInfo(f) }; })
    .sort(function (a, b) { return LEVEL[b.i.level].rank - LEVEL[a.i.level].rank; });
  var h = '<section class="h-card" aria-label="What we noticed"><h2>What we noticed</h2>' +
    '<div class="h-sub">Checked continuously while your system runs</div><div class="h-diag">';
  if (!fresh) return h + '<div class="empty">Waiting for readings from your system.</div></div></section>';
  items.forEach(function (x, n) {
    h += '<details id="h-issue-' + n + '"' + (n === 0 && x.i.level !== "advisory" ? " open" : "") + "><summary>" +
      '<span class="lvl">' + st(x.i.level, LEVEL[x.i.level].word) + "</span>" +
      '<span class="t">' + esc(x.i.title) + "<span>" + esc(x.i.sub) + "</span></span>" +
      '<span class="chev">▶</span></summary><div class="body">' +
      "<h4>Why it matters</h4><p>" + esc(x.i.why) + "</p>" +
      (x.i.todo.length ? "<h4>What you can do</h4><ol>" + x.i.todo.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ol>" : "") +
      '<div class="note">For your contractor: ' + esc(x.f.text) + ".</div></div></details>";
  });
  var okAreas = [];
  if (!items.some(function (x) { return x.i.area === "refrigerant"; })) okAreas.push("Refrigerant");
  if (!items.some(function (x) { return x.i.area === "outdoor"; })) okAreas.push("outdoor unit");
  if (!items.some(function (x) { return x.i.area === "cooling"; })) okAreas.push("airflow temperature");
  if (okAreas.length) h += '<div class="ok-row"><span class="st ok"></span>' + esc(okAreas.join(", ").replace(/^./, function (c) { return c.toUpperCase(); })) + " checks are normal.</div>";
  return h + "</div></section>";
}

function homeAlerts() {
  var list = S.alerts;
  var h = '<section class="h-card" aria-label="Recent alerts"><h2>Recent alerts</h2>' +
    '<div class="h-sub">Problems that lasted at least 5 minutes, last 7 days</div>';
  if (!list) return h + '<div class="empty">Loading…</div></section>';
  if (!list.length) return h + '<ul class="h-health"><li><b>None</b>' + st("ok", "Good") + "<p>Nothing needed attention in the last 7 days.</p></li></ul></section>";
  return h + '<ul class="h-health">' + list.slice(0, 8).map(function (a) {
    var i = flagInfo(a);
    return "<li><b>" + esc(i.title) + "</b>" + (a.open ? st(i.level, "Ongoing") : st("ok", "Cleared")) +
      "<p>" + esc(when(a.started_at)) + " · " + (a.open ? "for " : "lasted ") + esc(lasted(a)) + "</p></li>";
  }).join("") + "</ul></section>";
}

function homeHealth(fresh, running) {
  var d = (S.latest && S.latest.derived) || {}, steady = running && (d.run_min || 0) >= 10;
  var online = nodesOnline();
  function area(name, code, goodText, badCls, badWord, badText) {
    var bad = code.some(hasFlag);
    if (!fresh) return { name: name, cls: "offline", word: "No data", text: "Waiting for readings." };
    if (bad) return { name: name, cls: badCls, word: badWord, text: badText };
    if (!running) return { name: name, cls: "ok", word: "Good", text: "No problems found. Checked each time your system runs." };
    if (!steady) return { name: name, cls: "ok", word: "Good", text: "No problems found so far. The full check runs after 10 minutes of steady running." };
    return { name: name, cls: "ok", word: "Good", text: goodText };
  }
  var dtT = isNum(d.dt) ? "Air from your vents is about " + Math.round(Math.abs(d.dt)) + "° " + (d.mode === "heating" ? "warmer" : "cooler") + " than the air going in." : "Temperature change across the indoor coil looks right.";
  var rows = [
    area(d.mode === "heating" ? "Heating" : "Cooling", ["dt_low"], dtT, "caution", "Check soon", "Air from your vents isn't changing temperature as much as it should."),
    { name: "Airflow", cls: "offline", word: "Not measured",
      text: "Needs static pressure sensors, planned for a later phase. A dirty filter can still show up as weak cooling above." },
    area("Refrigerant", ["sh_low", "sh_high", "sc_low", "sc_high"], "Pressures and temperatures look normal for today's weather.",
      hasFlag("sh_low") ? "fault" : "caution", hasFlag("sh_low") ? "Service needed" : "Check soon", "Refrigerant readings are outside the normal range. See above."),
    area("Outdoor unit", ["ctoa_high"], "Releasing heat normally.", "caution", "Check soon", "It isn't releasing heat as well as it should. See above."),
    { name: "Monitoring", cls: online === 2 && !hasFlag("sensor_issue") ? "ok" : online ? "advisory" : "offline",
      word: online === 2 ? "Connected" : online ? "Partly connected" : "Offline",
      text: online === 2 ? (hasFlag("sensor_issue") ? "Both monitors online. One sensor isn't reporting." : "Both monitors online.") : online ? "One monitor isn't reporting." : "Neither monitor is reporting." }
  ];
  var good = rows.filter(function (r) { return r.cls === "ok"; }).length;
  return '<section class="h-card" aria-label="System health"><h2>System health</h2>' +
    '<div class="h-sub">' + good + " of " + rows.length + " areas are good</div>" +
    '<div class="h-meter" aria-hidden="true">' + rows.map(function (r) { return "<i" + (r.cls === "ok" ? "" : r.cls === "offline" ? ' class="o"' : ' class="c"') + "></i>"; }).join("") + "</div>" +
    '<ul class="h-health">' + rows.map(function (r) { return "<li><b>" + esc(r.name) + "</b>" + st(r.cls, r.word) + "<p>" + esc(r.text) + "</p></li>"; }).join("") + "</ul></section>";
}

function homeChart() {
  var sm = S.summary || {};
  return '<section class="h-card" aria-label="Last 24 hours"><h2>Last 24 hours</h2>' +
    '<div class="h-sub">Inside and outside temperature, and when your system ran</div>' +
    '<div class="hc-plot" id="hc"></div>' +
    '<div class="hc-leg"><span><i class="sw c-ret"></i> Inside</span><span><i class="sw c-out"></i> Outside</span><span><i class="runsw"></i> System running</span></div>' +
    '<div class="h-facts"><div><span>Ran</span><b>' + (isNum(sm.runtime_hours) ? fmt(sm.runtime_hours, 1) + " hr" : "—") + "</b></div>" +
    "<div><span>Outside high</span><b>" + (isNum(sm.outside_high) ? Math.round(sm.outside_high) + " °F" : "—") + "</b></div>" +
    "<div><span>Inside average</span><b>" + (isNum(sm.inside_avg) ? Math.round(sm.inside_avg) + " °F" : "—") + "</b></div></div></section>";
}

function drawHomeChart() {
  var box = $("#hc"), rows = S.history["24h"];
  if (!box) return;
  if (!rows || !rows.length) { box.innerHTML = '<div class="empty">No readings in the last 24 hours yet.</div>'; return; }
  var W = box.clientWidth || 500, H = box.clientHeight || 210, pl = 34, pr = 8, pt = 8, pb = 36;
  var t1 = Date.now() / 1000, t0 = t1 - 86400;
  var vals = [];
  rows.forEach(function (r) { ["t_ret", "oat"].forEach(function (k) { if (isNum(r[k])) vals.push(r[k]); }); });
  var lo = vals.length ? Math.floor(Math.min.apply(null, vals) / 5) * 5 - 5 : 60, hi = vals.length ? Math.ceil(Math.max.apply(null, vals) / 5) * 5 + 5 : 100;
  var x = function (t) { return pl + (t - t0) / (t1 - t0) * (W - pl - pr); };
  var y = function (v) { return pt + (hi - v) / (hi - lo) * (H - pt - pb); };
  var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, "aria-label": "Inside and outside temperature, last 24 hours" });
  for (var v = lo; v <= hi; v += (hi - lo) > 40 ? 20 : 10) {
    svgEl("line", { x1: pl, x2: W - pr, y1: y(v), y2: y(v), "class": "gridl" }, svg);
    svgEl("text", { x: pl - 6, y: y(v) + 4, "text-anchor": "end", "class": "axis" }, svg).textContent = v + "°";
  }
  $$("text.axis", svg).forEach(function (t) { t.setAttribute("style", "fill:var(--ink-faint);font:400 10.5px var(--font-sans)"); });
  var gap = maxGap(1440);
  rows.forEach(function (r, i) {
    if (r._on > 0.5) svgEl("rect", { x: x(r.ts), y: H - pb + 6, width: Math.max(1, x(nextTs(rows, i, gap)) - x(r.ts)), height: 8, "class": "h-run" }, svg);
  });
  [["t_ret", "c-ret"], ["oat", "c-out"]].forEach(function (s) { path(svg, rows, s[0], x, y, "s h-line " + s[1], gap); });
  for (var h = 0; h <= 24; h += 6) {
    var tt = t0 + h * 3600;
    var tx = svgEl("text", { x: x(tt), y: H - 8, "text-anchor": h === 0 ? "start" : h === 24 ? "end" : "middle", style: "fill:var(--ink-faint);font:400 10.5px var(--font-sans)" }, svg);
    tx.textContent = h === 24 ? "now" : ampm(tt * 1000);
  }
  box.innerHTML = "";
  box.appendChild(svg);
}

// ================================================================ TECHNICIAN VIEW
// Top bar + section nav shared by the technician pages ("monitor", "setup").
function techHeader(page) {
  var L = S.latest, d = (L && L.derived) || {}, n = (L && L.nodes) || {}, sm = S.summary || {};
  var fresh = dataFresh(), fl = flags();
  var counts = { fault: 0, caution: 0, advisory: 0, sensor: 0 };
  fl.forEach(function (f) { if (f.code === "sensor_issue" || f.code === "node_offline") counts.sensor++; else counts[flagInfo(f).level]++; });
  var diagSum = [counts.fault ? st("fault", counts.fault + " fault") : "", counts.caution ? st("caution", counts.caution + " warning") : "",
    counts.advisory ? st("advisory", counts.advisory + " advisory") : "", counts.sensor ? st("sensor", counts.sensor + " sensor") : ""].join("") || st("ok", "Normal");
  var running = d.mode === "cooling" || d.mode === "heating";
  var ob = d.OB, obWord = S.sys.ob_energized === "heat" ? "B" : "O";

  var diagHref = page === "monitor" ? "#" : "#/monitor/" + S.sys.id;
  return (
    '<header class="top"><div class="top-row">' + LOGO +
    '<div class="ident"><b>' + esc(S.sys.name) + "</b><span>site " + esc(S.sys.site_id) + " · " + esc(S.sys.refrigerant) + " · " + fmt(S.sys.atm_psia, 2) + " psia" + (S.sys.heat_pump ? " · heat pump" : "") + "</span></div>" +
    '<div class="top-fields">' +
    '<div class="tf"><span>Mode</span><div class="mode"><span class="word">' + esc(MODE_WORD[d.mode] || "—") + '</span><span class="calls" aria-label="Thermostat calls">' +
    call("Y", d.Y, "compressor") + call("G", d.G, "blower") + call(obWord, ob, "reversing valve") + call("W", d.W, "aux heat") + "</span></div></div>" +
    nodeTf("Indoor node", n.indoor) + nodeTf("Outdoor node", n.outdoor) +
    '<div class="tf"><span>Last update</span><b class="n">' + (L && L.time ? clock(L.time, true) : "—") + "</b></div>" +
    '<div class="tf"><span>Outdoor air</span><b>' + num(d.oat, "°F") + "</b></div>" +
    '<div class="tf"><span>Runtime</span><b>' + (running ? num(d.run_min, "min", 0) + ' <span class="muted" style="font-weight:400">cycle</span> · ' : "") +
    num(sm.runtime_hours, "h", 1) + ' <span class="muted" style="font-weight:400">24 h</span></b></div>' +
    '<a class="tf alerts" href="' + diagHref + '"' + (page === "monitor" ? ' data-jump="diagnostics"' : "") + ' style="text-decoration:none;color:inherit"><span>Diagnostics</span><div class="row">' + diagSum + "</div></a>" +
    "</div></div>" +
    '<nav class="nav" aria-label="Sections">' + navLink("monitor", "Monitor", page) + navLink("setup", "Sensors &amp; calibration", page) +
    '<a href="#/home/' + S.sys.id + '">Homeowner view</a>' +
    '<span class="spacer"></span><div class="tools">' +
    (fresh ? '<span class="live">LIVE · 5 s</span>' : st("offline", "Offline")) + systemPicker(page) +
    (page === "monitor" ? '<button class="btn" type="button" data-export>Export CSV</button>' : "") + '<button class="btn" type="button" data-signout>Sign out</button></div></nav></header>' +
    (S.error ? '<div class="stale-banner">Can\'t reach the server: ' + esc(S.error) + "</div>" : !fresh && L ? '<div class="stale-banner">No readings for ' + ago((Date.now() - new Date(L.time).getTime()) / 1000) + ". Values below are the last ones received.</div>" : ""));
}
function navLink(page, label, cur) {
  return page === cur ? '<a aria-current="page">' + label + "</a>" : '<a href="#/' + page + "/" + S.sys.id + '">' + label + "</a>";
}

function renderMonitor() {
  root.className = "fs";
  var L = S.latest, d = (L && L.derived) || {}, n = (L && L.nodes) || {}, sm = S.summary || {};
  var fl = flags(), obWord = S.sys.ob_energized === "heat" ? "B" : "O";
  var html = techHeader("monitor") +
    '<main class="page">' + strip(d) +
    '<div class="grid-a"><section class="panel" data-chart aria-label="Live trend"><div class="ph"><h2>Live trend</h2>' +
    '<span class="sub">Shaded bar = compressor running · hover for values</span><div class="right">' +
    seg("preset", [["refrigerant", "Refrigerant"], ["air", "Air side"], ["pressure", "Pressure"], ["temperature", "Temperature"], ["all", "All"]], S.preset) +
    seg("range", Object.keys(RANGES).map(function (k) { return [k, RANGES[k].label]; }), S.range) +
    '</div></div><div class="chart-wrap"><div class="plot" id="plot"><div class="tip" role="status"></div></div><div class="legend" id="legend" aria-label="Channels"></div></div></section>' +
    '<div class="rail">' + opState(d, sm, obWord) + sensorHealth(n) + "</div></div>" +
    '<div class="grid-b">' + refrigTable(d) + airTable(d) + diagPanel(fl, d) + "</div>" + alertLog() +
    '<div class="foot-legend"><span><span class="src">meas</span> measured by a sensor</span><span><span class="src calc">calc</span> calculated</span>' +
    "<span>Judged after 10 min of steady running · inferences, not confirmed diagnoses</span></div></main>";
  root.innerHTML = html;
  bindCommon();
  $$("[data-preset]").forEach(function (b) { b.addEventListener("click", function () { S.preset = b.dataset.preset; S.off = {}; save("fs_preset", S.preset); render(); }); });
  $$("[data-range]").forEach(function (b) { b.addEventListener("click", function () { S.range = b.dataset.range; save("fs_range", S.range); render(); pollHistory(); }); });
  $$("[data-jump]").forEach(function (a) { a.addEventListener("click", function (e) { e.preventDefault(); var t = document.getElementById(a.dataset.jump); if (t) t.scrollIntoView({ behavior: "smooth" }); }); });
  $$("tr.x").forEach(function (tr) {
    tr.addEventListener("click", function () {
      var det = tr.nextElementSibling, open = tr.getAttribute("aria-expanded") === "true";
      tr.setAttribute("aria-expanded", String(!open)); det.hidden = open;
      S.openRows = S.openRows || {}; S.openRows[tr.dataset.row] = !open;
    });
    if (S.openRows && S.openRows[tr.dataset.row]) { tr.setAttribute("aria-expanded", "true"); tr.nextElementSibling.hidden = false; }
  });
  var ex = $("[data-export]");
  if (ex) ex.addEventListener("click", exportCsv);
  drawTrend();
}

function call(letter, on, what) { return '<i class="' + (on ? "on" : "") + '" title="' + esc(letter + " " + what + ": " + (on ? "on" : "off")) + '">' + letter + "</i>"; }
function nodeTf(label, node) {
  if (!node) return '<div class="tf"><span>' + label + "</span><div>" + st("offline", "Never seen") + "</div></div>";
  return '<div class="tf"><span>' + label + "</span><div>" + (node.online ? st("ok", "Online") : st("offline", "Offline")) + ' <span class="faint">' + ago(node.age) + "</span></div></div>";
}
function seg(kind, opts, cur) {
  return '<div class="seg" role="group">' + opts.map(function (o) { return '<button type="button" data-' + kind + '="' + o[0] + '" aria-pressed="' + (o[0] === cur) + '">' + o[1] + "</button>"; }).join("") + "</div>";
}
function systemPicker(view) {
  if (!S.systems || S.systems.length < 2) return "";
  return '<select class="sel" aria-label="System" data-sys data-view="' + view + '">' + S.systems.map(function (x) {
    return '<option value="' + x.id + '"' + (x.id === S.sys.id ? " selected" : "") + ">" + esc(x.name) + "</option>";
  }).join("") + "</select>";
}
function bindCommon() {
  $$("[data-signout]").forEach(function (a) { a.addEventListener("click", function (e) { e.preventDefault(); signOut(); }); });
  var sel = $("[data-sys]");
  if (sel) sel.addEventListener("change", function () { location.hash = "#/" + sel.dataset.view + "/" + sel.value; });
}

// ---------- summary strip
function cell(label, src, v, unit, stateCls, stateWord, meta, digits) {
  var ds = stateCls === "caution" || stateCls === "fault" || stateCls === "advisory" ? ' data-state="' + stateCls + '"' : "";
  return '<div class="cell"' + ds + '><div class="lbl"><span>' + label + '</span><span class="src' + (src === "calc" ? " calc" : "") + '">' + src + "</span></div>" +
    '<div class="val">' + (isNum(v) ? fmt(v, digits === undefined ? 1 : digits) + "<small>" + unit + "</small>" : '<span class="faint">—</span>') + "</div>" +
    '<div class="meta">' + (stateWord ? st(stateCls, stateWord) : "<span></span>") + "<span>" + meta + "</span></div></div>";
}
function judged(v, codesBad, d) {
  var running = d.mode === "cooling" || d.mode === "heating";
  if (!isNum(v)) return [null, running ? "" : "Not running"];
  var bad = codesBad.filter(hasFlag)[0];
  if (bad) return [flagInfo({ code: bad }).level, bad.indexOf("low") > 0 ? "Low" : "High"];
  if ((d.run_min || 0) < 10) return ["advisory", "Settling"];
  return ["ok", "Normal"];
}
function strip(d) {
  var sh = judged(d.sh, ["sh_low", "sh_high"], d), sc = judged(d.sc, ["sc_low", "sc_high"], d), dt = judged(d.dt, ["dt_low"], d), ct = judged(d.ctoa, ["ctoa_high"], d);
  var cr = isNum(d.p_high) && isNum(d.p_low) ? (d.p_high + S.sys.atm_psia) / (d.p_low + S.sys.atm_psia) : null;
  return '<section class="strip" aria-label="System summary" style="grid-template-columns:repeat(6,minmax(0,1fr))">' +
    cell("Superheat", "calc", d.sh, "°F", sh[0], sh[1], "Flag &lt; 3 or &gt; 30") +
    cell("Subcooling", "calc", d.sc, "°F", sc[0], sc[1], "Flag &lt; 3 or &gt; 20") +
    cell("Delta-T", "calc", d.dt, "°F", dt[0], dt[1], "Flag &lt; 12 (cooling)") +
    cell("Cond. over ambient", "calc", d.ctoa, "°F", ct[0], ct[1], "Flag &gt; 35") +
    cell("Compression ratio", "calc", cr, ": 1", null, "", "Absolute pressures", 2) +
    cell("Outdoor ambient", "meas", d.oat, "°F", null, "", isNum(d.orh) ? "RH " + Math.round(d.orh) + " %" : "") +
    "</section>";
}

// ---------- operating state
function opState(d, sm, obWord) {
  var running = d.mode === "cooling" || d.mode === "heating";
  var rvCool = S.sys.ob_energized === "cool";
  var rv = d.OB ? "Energized" : "Off", rvMeans = S.sys.heat_pump ? (d.OB === rvCool ? "→ cooling" : "→ heating") : "";
  var cs = sm.cycle_started_at && running ? clock(sm.cycle_started_at, true) : "—";
  return '<section class="panel" aria-label="Operating state"><div class="ph"><h2>Operating state</h2><span class="right">' +
    (running ? st("ok", MODE_WORD[d.mode]) : st("offline", MODE_WORD[d.mode] || "—")) + "</span></div>" +
    '<table class="dt calls-t"><tbody>' +
    '<tr><th><span class="mono">Y</span>&nbsp; Compressor</th><td>' + (d.Y ? st("ok", "On") + ' <span class="faint n">' + fmt(d.run_min, 0) + " min</span>" : st("offline", "Off")) + "</td></tr>" +
    '<tr><th><span class="mono">G</span>&nbsp; Blower</th><td>' + (d.G ? st("ok", "On") : st("offline", "Off")) + "</td></tr>" +
    '<tr><th><span class="mono">' + obWord + "</span>&nbsp; Reversing valve</th><td>" + rv + ' <span class="faint">' + rvMeans + "</span></td></tr>" +
    '<tr><th><span class="mono">W</span>&nbsp; Aux heat</th><td>' + (d.W ? st("caution", "On") : st("offline", "Off")) + "</td></tr>" +
    '</tbody></table><div class="kv"><div><span>Cycle start</span><b>' + cs + "</b></div>" +
    "<div><span>Cycles, 24 h</span><b>" + (isNum(sm.cycles) ? sm.cycles : "—") + "</b></div>" +
    "<div><span>Avg on-time</span><b>" + (isNum(sm.avg_on_minutes) ? Math.round(sm.avg_on_minutes) + "<small>min</small>" : "—") + "</b></div></div></section>";
}

// ---------- sensor health (from the nodes' own telemetry and error codes)
function sensorHealth(n) {
  var od = (n.outdoor && n.outdoor.data) || {}, id = (n.indoor && n.indoor.data) || {};
  var oerr = od.err || [], ierr = id.err || [];
  function nodeRow(key, label, node) {
    if (!node) return '<tr><th>' + label + "</th><td>" + st("offline", "Never seen") + '</td><td class="num faint"></td></tr>';
    var dd = node.data || {}, bits = ["fw " + (node.fw || "?"), node.ip, isNum(node.rssi) ? node.rssi + " dBm" : null,
      isNum(dd.uptime) ? "up " + (dd.uptime / 86400 >= 1 ? (dd.uptime / 86400).toFixed(1) + " d" : (dd.uptime / 3600).toFixed(1) + " h") : null,
      isNum(dd.v5) ? "5 V rail " + dd.v5.toFixed(2) + " V" : null].filter(Boolean);
    return '<tr class="x" data-row="' + key + '" aria-expanded="false"><th>' + label + "</th><td>" + (node.online ? st("ok", "Online") : st("offline", "Offline")) +
      '</td><td class="num faint">' + ago(node.age) + '</td></tr><tr class="det" hidden><td colspan="3"><span class="mono">' + esc(bits.join(" · ")) + "</span></td></tr>";
  }
  // a channel is OK with a number, "Not installed" when the firmware marks it unfitted (null) and no error
  function ch(label, v, errKey, errList, optional) {
    var bad = errKey && errList.indexOf(errKey) >= 0;
    var s = bad ? st("sensor", "Error") : isNum(v) ? st("ok", "OK") : st("offline", optional ? "Not installed" : "No reading");
    return "<tr><th>" + label + "</th><td>" + s + '</td><td class="num faint">' + (optional ? "optional" : "") + "</td></tr>";
  }
  var p = od.p || {}, t = od.t || {}, a = od.air || {}, ia = id.air || {};
  var other = oerr.concat(ierr).filter(function (e) { return ["ads1", "ads2", "sht30", "ds18b20_missing"].indexOf(e) < 0; });
  return '<section class="panel" aria-label="Sensor health"><div class="ph"><h2>Sensor health</h2><span class="right sub">Click a node for detail</span></div>' +
    '<table class="dt health"><tbody><tr class="sec"><td colspan="3">Nodes</td></tr>' +
    nodeRow("in", "Indoor node", n.indoor) + nodeRow("out", "Outdoor node", n.outdoor) +
    '<tr class="sec"><td colspan="3">Pressure (ADS1115 0x48)</td></tr>' +
    ch("Liquid line", p.liq, "ads1", oerr) + ch("Vapor port", p.vap, "ads1", oerr) + ch("True suction", p.tsuc, null, oerr, true) +
    '<tr class="sec"><td colspan="3">Temperature</td></tr>' +
    ch("Suction line", t.suc, "ads2", oerr) + ch("Liquid line", t.liq, "ads2", oerr) + ch("True suction line", t.tsuc, null, oerr, true) +
    ch("Discharge", t.dis, null, oerr, true) + ch("Supply / return air", isNum(ia.supply) && isNum(ia.return) ? 1 : null, "ds18b20_missing", ierr) +
    ch("Outdoor air (SHT30)", a.t, "sht30", oerr) +
    '<tr class="sec"><td colspan="3">Humidity &amp; static</td></tr>' +
    '<tr class="unavail"><th>Indoor humidity, static pressure</th><td>' + st("offline", "Not installed") + '</td><td class="num faint">phase 6</td></tr>' +
    (other.length ? '<tr class="sec"><td colspan="3">Other errors</td></tr><tr><th colspan="3"><span class="mono">' + esc(other.join(", ")) + "</span></th></tr>" : "") +
    "</tbody></table></section>";
}

// ---------- refrigerant / air / diagnostics
function refrigTable(d) {
  var heat = d.mode === "heating", sh = judged(d.sh, ["sh_low", "sh_high"], d), sc = judged(d.sc, ["sc_low", "sc_high"], d);
  var cr = isNum(d.p_high) && isNum(d.p_low) ? (d.p_high + S.sys.atm_psia) / (d.p_low + S.sys.atm_psia) : null;
  var lowT = heat ? d.t_tsuc : d.t_suc;
  return '<section class="panel" aria-label="Refrigerant performance"><div class="ph"><h2>Refrigerant performance</h2><span class="sub">' +
    esc(S.sys.refrigerant) + " · " + esc(MODE_WORD[d.mode] || "—") + "</span></div>" +
    '<table class="dt"><thead><tr><th>Measurement</th><th class="tagc"></th><th class="num col-low">Low side · suction</th><th class="num col-high">High side · liquid</th></tr></thead><tbody>' +
    '<tr><th>Pressure</th><td class="tagc"><span class="src">meas</span></td><td class="num">' + num(d.p_low, "psig") + '<span class="tgt">' + (heat ? "True suction" : "Vapor service port") + '</span></td><td class="num">' + num(d.p_high, "psig") + '<span class="tgt">' + (heat ? "Vapor port" : "Liquid line") + "</span></td></tr>" +
    '<tr><th>Saturation temp</th><td class="tagc"><span class="src calc">calc</span></td><td class="num">' + num(d.sat_low, "°F") + '<span class="tgt">Evaporator · dew</span></td><td class="num">' + num(d.sat_high, "°F") + '<span class="tgt">Condenser · bubble</span></td></tr>' +
    '<tr><th>Line temp</th><td class="tagc"><span class="src">meas</span></td><td class="num">' + num(lowT, "°F") + '</td><td class="num">' + num(d.t_liq, "°F") + "</td></tr>" +
    '<tr class="key"><th>Superheat / subcooling</th><td class="tagc"><span class="src calc">calc</span></td>' +
    '<td class="num"><span class="muted" style="font-size:12px">SH</span> ' + num(d.sh, "°F") + '<span class="tgt">' + (sh[1] ? st(sh[0] || "offline", sh[1]) : "") + "</span></td>" +
    '<td class="num"><span class="muted" style="font-size:12px">SC</span> ' + num(d.sc, "°F") + '<span class="tgt">' + (sc[1] ? st(sc[0] || "offline", sc[1]) : "") + "</span></td></tr>" +
    '</tbody></table><div class="kv">' +
    "<div><span>Cond. over ambient</span><b>" + (isNum(d.ctoa) ? fmt(d.ctoa) + "<small>°F</small>" : "—") + "</b></div>" +
    "<div><span>Liquid approach</span><b>" + (isNum(d.approach) ? fmt(d.approach) + "<small>°F</small>" : "—") + "</b></div>" +
    "<div><span>Compression ratio</span><b>" + (isNum(cr) ? cr.toFixed(2) + "<small>: 1</small>" : "—") + "</b></div>" +
    "<div><span>Refrigerant</span><b>" + esc(S.sys.refrigerant) + "</b></div>" +
    "<div><span>Atmospheric</span><b>" + fmt(S.sys.atm_psia, 2) + "<small>psia</small></b></div>" +
    '<div><span>True suction</span><b class="' + (isNum(d.p_tsuc) ? "" : "faint") + '" style="font-weight:' + (isNum(d.p_tsuc) ? 500 : 400) + '">' + (isNum(d.p_tsuc) ? fmt(d.p_tsuc) + "<small>psig</small>" : "Not installed") + "</b></div>" +
    "</div>" + (d.notes && d.notes.length ? '<div class="na-note">' + esc(d.notes.join(" ")) + "</div>" : "") + "</section>";
}

function airTable(d) {
  var dt = judged(d.dt, ["dt_low"], d);
  return '<section class="panel" aria-label="Air-side performance"><div class="ph"><h2>Air-side performance</h2><span class="sub">Supply and return air (DS18B20)</span></div>' +
    '<table class="dt"><thead><tr><th>Measurement</th><th class="tagc"></th><th class="num">Return</th><th class="num">Supply</th><th class="num">Difference</th></tr></thead><tbody>' +
    '<tr class="key"><th>Dry bulb</th><td class="tagc"><span class="src">meas</span></td><td class="num">' + num(d.t_ret, "°F") + '</td><td class="num">' + num(d.t_sup, "°F") +
    '</td><td class="num"><span class="muted" style="font-size:12px">ΔT</span> ' + num(d.dt, "°F") + '<span class="tgt">' + (dt[1] ? st(dt[0] || "offline", dt[1]) : "") + "</span></td></tr>" +
    '<tr class="unavail"><th>Relative humidity, wet bulb, enthalpy</th><td class="tagc"></td><td class="num" colspan="3">Not installed</td></tr>' +
    '<tr class="unavail"><th>Static pressure (return, supply, TESP)</th><td class="tagc"></td><td class="num" colspan="3">Not installed</td></tr>' +
    '<tr class="unavail"><th>Capacity (needs airflow + humidity)</th><td class="tagc"></td><td class="num" colspan="3">Not available</td></tr>' +
    '</tbody></table><div class="na-note">Indoor humidity and static pressure sensors are planned for roadmap phase 6.</div></section>';
}

function diagPanel(fl, d) {
  var rows = fl.map(function (f) {
    var i = flagInfo(f), sensor = f.code === "sensor_issue" || f.code === "node_offline";
    var cls = sensor ? "sensor" : i.level;
    return "<tr><td>" + st(cls, sensor ? "Sensor" : TECH_LEVEL[i.level]) + "</td><td class=\"cond\">" + esc(f.text) + '</td><td class="cause">' + esc(i.sub) + "</td></tr>";
  }).join("");
  var running = d.mode === "cooling" || d.mode === "heating";
  var note = !running ? "Refrigerant and airflow rules run while the compressor is on." : (d.run_min || 0) < 10 ? "Running " + fmt(d.run_min, 0) + " min: refrigerant and airflow rules start after 10 min." : "All rules active.";
  return '<section class="panel" id="diagnostics" aria-label="System diagnostics"><div class="ph"><h2>System diagnostics</h2><span class="sub">Current</span></div>' +
    '<table class="dt diag"><thead><tr><th>Severity</th><th>Condition</th><th class="cause">What it usually means</th></tr></thead><tbody>' +
    (rows || '<tr class="normal"><td>' + st("ok", "Normal") + '</td><td class="cond" colspan="2">No conditions flagged.</td></tr>') +
    '</tbody></table><div class="na-note">' + esc(note) + "</div></section>";
}

function alertLog() {
  var list = S.alerts, body;
  if (!list) body = '<tr><td colspan="5" class="faint">Loading…</td></tr>';
  else if (!list.length) body = '<tr class="normal"><td>' + st("ok", "Normal") + '</td><td class="cond" colspan="4">No alerts in the last 7 days.</td></tr>';
  else body = list.map(function (a) {
    var i = flagInfo(a), sensor = ["sensor_issue", "node_offline", "no_data"].indexOf(a.code) >= 0;
    return "<tr><td>" + st(sensor ? "sensor" : i.level, sensor ? "Sensor" : TECH_LEVEL[i.level]) + '</td><td class="cond">' + esc(a.text) + "</td>" +
      '<td class="n">' + esc(when(a.started_at)) + "</td><td>" + (a.open ? st(i.level === "advisory" ? "advisory" : "caution", "Active") + ' <span class="faint n">' + esc(lasted(a)) + "</span>" : '<span class="n">' + esc(lasted(a)) + "</span>") +
      '</td><td class="faint n">' + (a.emailed_at ? "Emailed " + esc(ampm(a.emailed_at)) : "—") + "</td></tr>";
  }).join("");
  return '<section class="panel" id="alert-log" aria-label="Alert log"><div class="ph"><h2>Alert log</h2><span class="sub">Last 7 days · raised after 5 min, cleared after 5 min without the condition</span></div>' +
    '<table class="dt diag"><thead><tr><th>Severity</th><th>Condition</th><th>Started</th><th>Duration</th><th>Email</th></tr></thead><tbody>' + body + "</tbody></table></section>";
}

// ================================================================ SENSORS & CALIBRATION VIEW
// Sends the firmware's own commands (hvac-firmware/src/node_outdoor.cpp, node_indoor.cpp) and shows
// each node's reply. Settings shown here come from the node's status message, refreshed after a change.
var PCH = [["p_liq", "liq", "Liquid line", "J3"], ["p_vap", "vap", "Vapor port", "J4"], ["p_tsuc", "tsuc", "True suction", "J5"]];
var TCH = [["t_suc", "suc", "Suction line", "J6 · TH1"], ["t_liq", "liq", "Liquid line", "J7 · TH2"], ["t_tsuc", "tsuc", "True suction line", "J9 · TH3"]];
var QUIET = { status: 1 };                                  // sent automatically; kept out of the log
var REFRESH_AFTER = { fitted: 1, cal_zero: 1, cal_span: 1, cal_ref: 1, cal_reset: 1, range: 1, ntc_b: 1, v33: 1, ds_swap: 1, rescan: 1 };
var REPLY_WAIT_S = 20;

function pollCommands() {
  var id = S.sys.id;
  api("/api/systems/" + id + "/commands?limit=40").then(function (list) {
    if (!S.sys || S.sys.id !== id) return;
    S.commands = list;
    list.forEach(function (c) {
      var p = S.pending[c.id];
      if (!p || !c.reply) return;
      delete S.pending[c.id];
      if (c.reply.ok && REFRESH_AFTER[c.cmd.cmd]) sendCmd(c.node, { cmd: "status" });
      setTimeout(pollLatest, 1500);                       // show the effect without waiting for the next poll
    });
    render();
  }).catch(function () {});
}

function sendCmd(node, cmd) {
  return api("/api/systems/" + S.sys.id + "/commands", { method: "POST", body: { node: node, cmd: cmd } }).then(function (c) {
    S.pending[c.id] = true;
    if (!QUIET[cmd.cmd]) S.msg = c.sent ? null : "The cloud couldn't reach the MQTT broker, so the command was not sent.";
    pollCommands();
  }).catch(function (e) { S.msg = "Command failed: " + e.message; render(); });
}

function nodeOf(name) { return ((S.latest && S.latest.nodes) || {})[name] || null; }
function numIn(id) { var v = parseFloat(String(S.form[id] === undefined ? "" : S.form[id]).replace(",", ".")); return isFinite(v) ? v : null; }
function field(id, unit, ph, label) {
  return '<label class="field"><input id="' + id + '" inputmode="decimal" autocomplete="off" placeholder="' + esc(ph || "") + '" aria-label="' + esc(label) + '">' +
    (unit ? '<span class="u">' + unit + "</span>" : "") + "</label>";
}
function actBtn(act, label, node, ch, extra) {
  var on = nodeOf(node) && nodeOf(node).online;
  return '<button class="btn" type="button" data-act="' + act + '" data-node="' + node + '"' + (ch ? ' data-ch="' + ch + '"' : "") + (extra || "") + (on ? "" : " disabled") + ">" + label + "</button>";
}
function fitSeg(node, ch, fit) {
  var on = nodeOf(node) && nodeOf(node).online, dis = on ? "" : " disabled";
  if (fit === undefined || fit === null) return '<span class="faint">—</span>';
  return '<div class="seg" role="group" aria-label="Installed">' +
    '<button type="button" data-act="fit" data-on="1" data-node="' + node + '" data-ch="' + ch + '" aria-pressed="' + !!fit + '"' + dis + ">On</button>" +
    '<button type="button" data-act="fit" data-on="0" data-node="' + node + '" data-ch="' + ch + '" aria-pressed="' + !fit + '"' + dis + ">Off</button></div>";
}
function offsetTxt(v, digits) { return isNum(v) ? (v > 0 ? "+" : "") + v.toFixed(digits === undefined ? 1 : digits) : "—"; }
function reading(v, unit, fit, errKey, errs, digits) {
  if (fit === false) return '<span class="faint">Off</span>';
  if (errs.indexOf(errKey) >= 0) return st("sensor", "No reading");
  return num(v, unit, digits);
}
function nodeSub(node, label) {
  var n = nodeOf(node);
  if (!n) return st("offline", "Never seen") + ' <span class="faint">' + label + " has not reported to the cloud yet</span>";
  if (!n.online) return st("offline", "Offline") + ' <span class="faint">commands can\'t reach it until it reports again</span>';
  return st("ok", "Online") + ' <span class="faint">fw ' + esc(n.fw || "?") + "</span>";
}

function renderSetup() {
  root.className = "fs";
  var out = nodeOf("outdoor"), ind = nodeOf("indoor");
  var od = (out && out.data) || {}, os = (out && out.status) || {}, oc = os.cal || {}, oerr = od.err || [], raw = od.raw || {};
  var id = (ind && ind.data) || {}, is = (ind && ind.status) || {}, ic = is.cal || {}, ierr = id.err || [], air = id.air || {};

  var pRows = PCH.map(function (c) {
    var k = c[0], cal = oc[k] || {}, fit = cal.fit;
    return "<tr><th>" + c[2] + ' <span class="faint mono">' + c[3] + "</span></th><td>" + fitSeg("outdoor", k, fit) + "</td>" +
      '<td class="num">' + reading((od.p || {})[c[1]], "psig", fit, k, oerr) + '</td><td class="num faint n">' + (isNum(raw[k]) ? raw[k].toFixed(2) + " V" : "—") + "</td>" +
      '<td class="num n">' + offsetTxt(cal.o) + ' <span class="faint">psi</span> · ×' + (isNum(cal.s) ? cal.s.toFixed(3) : "—") + "</td>" +
      '<td><div class="acts-cell">' + actBtn("zero", "Zero", "outdoor", k) + field("f-span-" + k, "psig", "gauge", c[2] + " reference pressure") + actBtn("span", "Span", "outdoor", k) + actBtn("reset", "Reset", "outdoor", k) + "</div></td>" +
      '<td><div class="acts-cell">' + field("f-range-" + k, "bar", isNum(cal.fs_bar) ? String(cal.fs_bar) : "", c[2] + " full-scale range") + actBtn("range", "Set", "outdoor", k) + "</div></td></tr>";
  }).join("");

  var tRows = TCH.map(function (c) {
    var k = c[0], cal = oc[k] || {}, fit = cal.fit;
    return "<tr><th>" + c[2] + ' <span class="faint mono">' + c[3] + "</span></th><td>" + fitSeg("outdoor", k, fit) + "</td>" +
      '<td class="num">' + reading((od.t || {})[c[1]], "°F", fit, k, oerr) + '</td><td class="num faint n">' + (isNum(raw[k]) ? Math.round(raw[k]).toLocaleString() + " Ω" : "—") + "</td>" +
      '<td class="num n">' + offsetTxt(cal.o) + ' <span class="faint">°F</span></td>' +
      '<td><div class="acts-cell">' + actBtn("ice", "Ice bath 32 °F", "outdoor", k) + field("f-ref-" + k, "°F", "known", c[2] + " reference temperature") + actBtn("ref", "Set", "outdoor", k) + actBtn("reset", "Reset", "outdoor", k) + "</div></td></tr>";
  }).join("");

  var aRows = [["t_sup", "supply", "Supply air"], ["t_ret", "return", "Return air"]].map(function (c) {
    var k = c[0], cal = ic[c[0]] || {};
    return "<tr><th>" + c[2] + '</th><td class="num">' + reading(air[c[1]], "°F", true, k, ierr) + "</td>" +
      '<td class="num n">' + offsetTxt(cal.o) + ' <span class="faint">°F</span></td>' +
      '<td><div class="acts-cell">' + actBtn("ice", "Ice bath 32 °F", "indoor", k) + field("f-ref-" + k, "°F", "known", c[2] + " reference temperature") + actBtn("ref", "Set", "indoor", k) + actBtn("reset", "Reset", "indoor", k) + "</div></td></tr>";
  }).join("");

  var html = techHeader("setup") +
    (S.msg ? '<div class="stale-banner" role="status">' + esc(S.msg) + "</div>" : "") +
    '<main class="page">' +
    '<section class="panel" aria-label="Pressure transducers"><div class="ph"><h2>Pressure transducers</h2><span class="sub">Outdoor node</span><span class="right">' + nodeSub("outdoor", "The outdoor node") + "</span></div>" +
    '<table class="dt"><thead><tr><th>Channel</th><th>Installed</th><th class="num">Reading</th><th class="num">Sensor</th><th class="num">Calibration</th><th>Zero · span</th><th>Range</th></tr></thead><tbody>' + pRows + "</tbody></table>" +
    '<ol class="howto"><li><b>Zero:</b> with the transducer open to air (removed, or its port depressurized), press Zero.</li>' +
    "<li><b>Span:</b> pressurize with nitrogen to at least 50 psig, read your reference gauge, enter that value and press Span.</li>" +
    "<li><b>Range:</b> the XDB307's full scale in bar, from its label (50 for the line sensors, 35 for true suction).</li></ol></section>" +

    '<section class="panel" aria-label="Line thermistors"><div class="ph"><h2>Line thermistors</h2><span class="sub">Outdoor node</span><span class="right">' + nodeSub("outdoor", "The outdoor node") + "</span></div>" +
    '<table class="dt"><thead><tr><th>Channel</th><th>Installed</th><th class="num">Reading</th><th class="num">Resistance</th><th class="num">Offset</th><th>Calibrate</th></tr></thead><tbody>' + tRows + "</tbody></table>" +
    '<div class="kv setup-kv"><div><span>Thermistor B-value</span><div class="acts-cell">' + field("f-ntcb", "", isNum(os.ntc_b) ? String(os.ntc_b) : "3950", "Thermistor B-value") + actBtn("ntcb", "Set", "outdoor") + "</div></div>" +
    '<div><span>Measured 3.3 V rail</span><div class="acts-cell">' + field("f-v33", "V", isNum(os.v33) ? os.v33.toFixed(2) : "3.30", "Measured 3.3 V rail") + actBtn("v33", "Set", "outdoor") + "</div></div>" +
    '<div><span>5 V rail (live)</span><b>' + (isNum(od.v5) ? od.v5.toFixed(2) + "<small>V</small>" : "—") + "</b></div></div>" +
    '<ol class="howto"><li><b>Ice bath:</b> fill a cup with crushed ice and a little water, stir, hold the probe in it for 3 minutes, then press Ice bath.</li>' +
    "<li><b>Set:</b> or compare against a trusted thermometer at any temperature, enter its reading and press Set.</li>" +
    "<li><b>3.3 V rail:</b> measure between 3V3 and GND on the ESP32 with a meter; entering it makes every thermistor more accurate.</li></ol></section>" +

    '<div class="cfg">' +
    '<section class="panel" aria-label="Air probes"><div class="ph"><h2>Air probes</h2><span class="sub">Indoor node · DS18B20</span><span class="right">' + nodeSub("indoor", "The indoor node") + "</span></div>" +
    '<table class="dt"><thead><tr><th>Probe</th><th class="num">Reading</th><th class="num">Offset</th><th>Calibrate</th></tr></thead><tbody>' + aRows + "</tbody></table>" +
    '<div class="kv setup-kv"><div><span>Probes found</span><b>' + (is.probes ? is.probes.length + " of 2" : "—") + "</b></div>" +
    '<div><span>Supply / return</span><div class="acts-cell">' + actBtn("swap", "Swap them", "indoor") + "</div></div>" +
    '<div><span>Search the bus</span><div class="acts-cell">' + actBtn("rescan", "Rescan probes", "indoor") + "</div></div></div>" +
    '<ol class="howto"><li><b>Swap them</b> if supply reads warmer than return while cooling.</li><li>DS18B20s are accurate to ±0.9 °F out of the box; calibrate only if they disagree with a reference.</li></ol></section>' +

    '<section class="panel" aria-label="Nodes"><div class="ph"><h2>Nodes</h2></div><table class="dt"><tbody>' +
    ["outdoor", "indoor"].map(function (nm) {
      var nd = nodeOf(nm);
      return "<tr><th>" + (nm === "outdoor" ? "Outdoor" : "Indoor") + " node</th><td>" + nodeSub(nm, "This node") + "</td></tr>" +
        '<tr><td colspan="2"><div class="acts-cell">' + field("f-int-" + nm, "s", "5", nm + " reporting interval") + actBtn("interval", "Set interval", nm) +
        actBtn("status", "Reload settings", nm) + actBtn("reboot", "Reboot", nm) + (nd && nd.ip ? '<span class="faint mono">' + esc(nd.ip) + "</span>" : "") + "</div></td></tr>";
    }).join("") + "</tbody></table></section></div>" +
    commandLog() + "</main>";
  root.innerHTML = html;
  bindCommon();
}

function cmdText(c) {
  var parts = [];
  for (var k in c) if (k !== "cmd") parts.push(k + " " + (typeof c[k] === "object" ? JSON.stringify(c[k]) : c[k]));
  return c.cmd + (parts.length ? " · " + parts.join(", ") : "");
}
function commandLog() {
  var list = (S.commands || []).filter(function (c) { return c.cmd && !QUIET[c.cmd.cmd]; }).slice(0, 15), body;
  if (!S.commands) body = '<tr><td colspan="4" class="faint">Loading…</td></tr>';
  else if (!list.length) body = '<tr><td colspan="4" class="faint">No commands sent yet.</td></tr>';
  else body = list.map(function (c) {
    var age = (Date.now() - new Date(c.created_at).getTime()) / 1000, r = c.reply, res;
    if (!c.sent) res = st("fault", "Not sent");
    else if (r && r.ok) res = st("ok", "Done") + (isNum(r.offset) ? ' <span class="faint n">offset ' + offsetTxt(r.offset, 2) + (isNum(r.scale) && r.scale !== 1 ? " · ×" + r.scale.toFixed(3) : "") + "</span>" : "");
    else if (r) res = st("caution", "Refused") + ' <span class="faint">' + esc(r.error || "") + "</span>";
    else if (age < REPLY_WAIT_S) res = st("advisory", "Waiting for the node…");
    else res = st("offline", "No reply");
    return '<tr><td class="n faint">' + clock(c.created_at, true) + "</td><td>" + esc(c.node) + '</td><td class="mono">' + esc(cmdText(c.cmd)) + "</td><td>" + res + "</td></tr>";
  }).join("");
  return '<section class="panel" aria-label="Command log"><div class="ph"><h2>Command log</h2><span class="sub">Each change is confirmed by the node, then its settings are reloaded</span></div>' +
    '<table class="dt"><thead><tr><th>Sent</th><th>Node</th><th>Command</th><th>Result</th></tr></thead><tbody>' + body + "</tbody></table></section>";
}

// one delegated listener: the page is re-rendered every few seconds
root.addEventListener("input", function (e) { if (e.target.id && e.target.id.indexOf("f-") === 0) S.form[e.target.id] = e.target.value; });
root.addEventListener("click", function (e) {
  var b = e.target.closest && e.target.closest("[data-act]");
  if (!b || S.view !== "setup" || b.disabled) return;
  var act = b.dataset.act, node = b.dataset.node, ch = b.dataset.ch, nd = nodeOf(node) || {}, name = b.closest("tr") ? (b.closest("tr").querySelector("th") || {}).textContent : "";
  var cmd = null, v;
  S.msg = null;
  function need(id, lo, hi, what) {
    v = numIn(id);
    if (v === null || v < lo || v > hi) { S.msg = "Enter " + what + " between " + lo + " and " + hi + " first."; render(); return false; }
    return true;
  }
  if (act === "fit") {
    var on = b.dataset.on === "1";
    if (!on && !confirm("Turn off " + name + "? It will stop reporting until it is turned back on.")) return;
    cmd = { cmd: "fitted", ch: ch, on: on };
  } else if (act === "zero") {
    var psig = (((nd.data || {}).p) || {})[ch.slice(2)];
    var warn = isNum(psig) && Math.abs(psig) > 25 ? "\n\nIt reads " + psig.toFixed(1) + " psig right now. Zeroing it under pressure would make every reading wrong." : "";
    if (!confirm("Zero " + name + "?\n\nOnly do this with the transducer open to air (0 psig)." + warn)) return;
    cmd = { cmd: "cal_zero", ch: ch };
  } else if (act === "span") {
    if (!need("f-span-" + ch, 50, 800, "the reference gauge pressure (psig)")) return;
    cmd = { cmd: "cal_span", ch: ch, ref: v };
  } else if (act === "ice") {
    if (!confirm("Calibrate " + name + " to 32.0 °F?\n\nThe probe should have been in stirred ice water for 3 minutes.")) return;
    cmd = { cmd: "cal_ref", ch: ch, ref: 32 };
  } else if (act === "ref") {
    if (!need("f-ref-" + ch, -20, 250, "the reference temperature (°F)")) return;
    cmd = { cmd: "cal_ref", ch: ch, ref: v };
  } else if (act === "reset") {
    if (!confirm("Clear the calibration of " + name + "?")) return;
    cmd = { cmd: "cal_reset", ch: ch };
  } else if (act === "range") {
    if (!need("f-range-" + ch, 5, 100, "the full-scale range (bar)")) return;
    cmd = { cmd: "range", ch: ch, bar: v };
  } else if (act === "ntcb") {
    if (!need("f-ntcb", 2000, 5000, "the B-value")) return;
    cmd = { cmd: "ntc_b", value: v };
  } else if (act === "v33") {
    if (!need("f-v33", 3.0, 3.6, "the measured voltage")) return;
    cmd = { cmd: "v33", value: v };
  } else if (act === "swap") {
    if (!confirm("Swap which probe is supply and which is return?")) return;
    cmd = { cmd: "ds_swap" };
  } else if (act === "rescan") {
    cmd = { cmd: "rescan" };
  } else if (act === "interval") {
    if (!need("f-int-" + node, 1, 600, "the interval (seconds)")) return;
    cmd = { cmd: "interval", ms: Math.round(v * 1000) };
  } else if (act === "status") {
    cmd = { cmd: "status" };
  } else if (act === "reboot") {
    if (!confirm("Reboot the " + node + " node? It will be offline for about 10 seconds.")) return;
    cmd = { cmd: "reboot" };
  }
  if (cmd) { b.disabled = true; sendCmd(node, cmd); }
});

// ---------------------------------------------------------------- CSV export
function exportCsv() {
  var mins = RANGES[S.range].min < 1440 ? 1440 : RANGES[S.range].min;
  api("/api/systems/" + S.sys.id + "/export.csv?minutes=" + mins, { raw: true }).then(function (r) { return r.blob(); }).then(function (b) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = S.sys.site_id + "-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }).catch(function (e) { alert("Export failed: " + e.message); });
}

// ---------------------------------------------------------------- trend chart
var CH = [
  { k: "p_low", n: "Suction pressure", u: "psig", lane: "press", c: "c-low" },
  { k: "p_high", n: "Liquid pressure", u: "psig", lane: "press", c: "c-high" },
  { k: "t_suc", n: "Suction line", u: "°F", lane: "temp", c: "c-low" },
  { k: "sat_low", n: "Evap. saturation", u: "°F", lane: "temp", c: "c-low", dash: 1 },
  { k: "t_liq", n: "Liquid line", u: "°F", lane: "temp", c: "c-high" },
  { k: "sat_high", n: "Cond. saturation", u: "°F", lane: "temp", c: "c-high", dash: 1 },
  { k: "t_ret", n: "Return air", u: "°F", lane: "temp", c: "c-ret" },
  { k: "t_sup", n: "Supply air", u: "°F", lane: "temp", c: "c-sup" },
  { k: "oat", n: "Outdoor air", u: "°F", lane: "temp", c: "c-out" },
  { k: "sh", n: "Superheat", u: "°F", lane: "calc", c: "c-low" },
  { k: "sc", n: "Subcooling", u: "°F", lane: "calc", c: "c-high" },
  { k: "dt", n: "Delta-T", u: "°F", lane: "calc", c: "c-sup" }
];
var LANES = { press: { t: "Pressure", u: "psig", w: 1 }, temp: { t: "Temperature", u: "°F", w: 1.35 }, calc: { t: "Calculated", u: "°F", w: 0.95 } };
var LANE_ORDER = ["press", "temp", "calc"];
var GROUPS = [["Refrigerant — raw", ["p_low", "p_high", "t_suc", "t_liq"]], ["Refrigerant — calculated", ["sat_low", "sat_high", "sh", "sc"]], ["Air side", ["t_ret", "t_sup", "dt", "oat"]]];
var PRESETS = {
  refrigerant: ["p_low", "p_high", "t_suc", "sat_low", "t_liq", "sat_high", "sh", "sc"],
  air: ["t_ret", "t_sup", "oat", "dt"],
  pressure: ["p_low", "p_high"],
  temperature: ["t_suc", "sat_low", "t_liq", "sat_high", "t_ret", "t_sup", "oat"],
  all: CH.map(function (c) { return c.k; })
};
function chOn(k) { return PRESETS[S.preset].indexOf(k) >= 0 && !S.off[k]; }

function svgEl(tag, attrs, parent) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }

// History rows are averaged into buckets of minutes*60/600 s (see the API); a gap longer than
// two buckets, and at least OUTAGE_S, is missing data and breaks lines and run bars.
function maxGap(minutes) { return Math.max(OUTAGE_S, minutes * 60 / 600 * 2); }
function nextTs(rows, i, gap) {
  var r = rows[i], n = rows[i + 1];
  return n && n.ts - r.ts <= gap ? n.ts : r.ts + gap / 2;
}

function path(svg, rows, key, x, y, cls, maxGap) {
  var dstr = "", prev = null;
  rows.forEach(function (r) {           // a missing value is skipped; only a long time gap breaks the line
    var v = r[key];
    if (!isNum(v)) return;
    dstr += (prev === null || r.ts - prev > maxGap ? "M" : "L") + x(r.ts).toFixed(1) + " " + y(v).toFixed(1);
    prev = r.ts;
  });
  if (dstr) svgEl("path", { d: dstr, "class": cls }, svg);
}

function drawTrend() {
  var plot = $("#plot"), legend = $("#legend");
  if (!plot) return;
  var rows = S.history[S.range], tip = $(".tip", plot);
  var latest = (S.latest && S.latest.derived) || {};
  // legend
  var lh = "";
  GROUPS.forEach(function (g) {
    var inPreset = g[1].filter(function (k) { return PRESETS[S.preset].indexOf(k) >= 0; });
    if (!inPreset.length) return;
    lh += '<div class="grp">' + g[0] + "</div>";
    inPreset.forEach(function (k) {
      var c = CH.filter(function (x) { return x.k === k; })[0];
      lh += '<button type="button" data-ch="' + k + '" aria-pressed="' + chOn(k) + '"><span class="ck"></span><i class="sw ' + c.c + (c.dash ? " dash" : "") + '"></i><span>' + c.n +
        '</span><span class="lv">' + (isNum(latest[k]) ? fmt(latest[k]) + "<small>" + c.u + "</small>" : "—") + "</span></button>";
    });
  });
  legend.innerHTML = lh + '<div class="foot">Latest values · ' + RANGES[S.range].label + " window</div>";
  $$("[data-ch]", legend).forEach(function (b) { b.addEventListener("click", function () { S.off[b.dataset.ch] = chOn(b.dataset.ch); drawTrend(); }); });

  $$("svg", plot).forEach(function (s) { s.remove(); });
  if (!rows) { tip.style.display = "none"; return; }
  if (!rows.length) { var e = svgEl("svg", {}, plot); svgEl("text", { x: 20, y: 30, style: "fill:var(--ink-muted);font:400 13px var(--font-sans)" }, e).textContent = "No readings in this window yet."; return; }

  var W = plot.clientWidth || 800, H = plot.clientHeight || 486, pl = 52, pr = 12, top = 22, bot = 26, runH = 10, gap = 18;
  var t1 = Date.now() / 1000, t0 = t1 - RANGES[S.range].min * 60;
  var lanes = LANE_ORDER.filter(function (l) { return CH.some(function (c) { return c.lane === l && chOn(c.k); }); });
  var wsum = lanes.reduce(function (a, l) { return a + LANES[l].w; }, 0) || 1;
  var avail = H - top - bot - runH - gap * lanes.length;
  var x = function (t) { return pl + (t - t0) / (t1 - t0) * (W - pl - pr); };
  var svg = svgEl("svg", { viewBox: "0 0 " + W + " " + H, "aria-label": "Live system trend" }, plot);
  var axisStyle = "fill:var(--ink-faint);font:400 10.5px var(--font-sans)";

  // compressor run bar
  svgEl("text", { x: pl - 8, y: top + runH - 1, "text-anchor": "end", style: axisStyle }, svg).textContent = "Run";
  var breakGap = maxGap(RANGES[S.range].min);
  rows.forEach(function (r, i) { svgEl("rect", { x: x(r.ts), y: top, width: Math.max(1, x(nextTs(rows, i, breakGap)) - x(r.ts)), height: runH, "class": "runbar" + (r._on > 0.5 ? " on" : "") }, svg); });

  var yPos = top + runH + gap, scales = {};
  lanes.forEach(function (l) {
    var hgt = avail * LANES[l].w / wsum, keys = CH.filter(function (c) { return c.lane === l && chOn(c.k); });
    var vals = [];
    rows.forEach(function (r) { keys.forEach(function (c) { if (isNum(r[c.k])) vals.push(r[c.k]); }); });
    var lo = vals.length ? Math.min.apply(null, vals) : 0, hi = vals.length ? Math.max.apply(null, vals) : 1;
    var pad = Math.max((hi - lo) * 0.08, l === "press" ? 5 : 1);
    lo -= pad; hi += pad;
    var y0 = yPos, y = function (v) { return y0 + (hi - v) / (hi - lo) * hgt; };
    scales[l] = y;
    svgEl("text", { x: pl, y: y0 - 5, style: "fill:var(--ink-muted);font:500 11px var(--font-sans)" }, svg).textContent = LANES[l].t + " · " + LANES[l].u;
    for (var i = 0; i <= 3; i++) {
      var v = lo + (hi - lo) * i / 3;
      svgEl("line", { x1: pl, x2: W - pr, y1: y(v), y2: y(v), "class": "gridl" }, svg);
      svgEl("text", { x: pl - 6, y: y(v) + 4, "text-anchor": "end", style: axisStyle }, svg).textContent = v.toFixed(l === "press" ? 0 : 1);
    }
    keys.forEach(function (c) { path(svg, rows, c.k, x, y, "s " + c.c + (c.dash ? " dash" : ""), breakGap); });
    yPos += hgt + gap;
  });
  // time axis
  var ticks = 6;
  for (var j = 0; j <= ticks; j++) {
    var tt = t0 + (t1 - t0) * j / ticks;
    var label = RANGES[S.range].min > 1440 ? new Date(tt * 1000).toLocaleDateString([], { weekday: "short" }) + " " + clock(tt * 1000) : clock(tt * 1000);
    svgEl("text", { x: x(tt), y: H - 8, "text-anchor": j === 0 ? "start" : j === ticks ? "end" : "middle", style: axisStyle }, svg).textContent = j === ticks ? "now" : label;
  }
  // hover
  var cross = svgEl("line", { y1: top, y2: H - bot, "class": "cross", visibility: "hidden" }, svg);
  svg.addEventListener("mousemove", function (ev) {
    var b = svg.getBoundingClientRect(), mx = (ev.clientX - b.left) * W / b.width;
    var t = t0 + (mx - pl) / (W - pl - pr) * (t1 - t0), best = null;
    rows.forEach(function (r) { if (!best || Math.abs(r.ts - t) < Math.abs(best.ts - t)) best = r; });
    if (!best || mx < pl) { cross.setAttribute("visibility", "hidden"); tip.style.display = "none"; return; }
    cross.setAttribute("x1", x(best.ts)); cross.setAttribute("x2", x(best.ts)); cross.setAttribute("visibility", "visible");
    var h = '<div class="t"><span>' + clock(best.ts * 1000, true) + "</span><span>" + (best._on > 0.5 ? "running" : "off") + "</span></div>";
    CH.forEach(function (c) { if (chOn(c.k)) h += '<div class="r"><i class="sw ' + c.c + (c.dash ? " dash" : "") + '"></i><span>' + c.n + '</span><span class="v">' + (isNum(best[c.k]) ? fmt(best[c.k]) + " " + c.u : "—") + "</span></div>"; });
    tip.innerHTML = h;
    tip.style.display = "block";
    var px = (ev.clientX - b.left) + 14;
    tip.style.left = (px + 200 > b.width ? px - 214 : px) + "px";
    tip.style.top = "12px";
  });
  svg.addEventListener("mouseleave", function () { cross.setAttribute("visibility", "hidden"); tip.style.display = "none"; });
}

window.addEventListener("resize", function () { if (S.view === "monitor") drawTrend(); else if (S.view === "home") drawHomeChart(); });
route();
})();
