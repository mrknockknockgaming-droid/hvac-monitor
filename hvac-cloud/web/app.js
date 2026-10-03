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
  key: load("fs_key"), me: null, loginMode: load("fs_key") ? "key" : "password", systems: null, sys: null, view: null,
  latest: null, summary: null, history: {}, alerts: null, alertSettings: null, service: null, commands: null, pending: {}, form: {}, msg: null, range: load("fs_range") || "1h",
  preset: load("fs_preset") || "refrigerant", off: {}, lastOk: null, error: null, timers: []
};
var LIVE_MS = 5000, STALE_S = 30, OUTAGE_S = 90;   // a gap longer than OUTAGE_S breaks chart lines

// ---------------------------------------------------------------- API
// Signed in with the session cookie (sent automatically), or with an API key kept in this browser.
// X-Requested-With marks requests as coming from this app (the server's cross-site check).
function api(path, opts) {
  opts = opts || {};
  var headers = { "X-Requested-With": "fullscope" };
  if (S.key) headers["X-API-Key"] = S.key;
  if (opts.body) headers["Content-Type"] = "application/json";
  return fetch(path, { method: opts.method || "GET", headers: headers, credentials: "same-origin", body: opts.body ? JSON.stringify(opts.body) : undefined })
    .then(function (r) {
      if (r.status === 401 && !opts.keep401) {
        signOut(S.key ? "That API key was not accepted." : S.me ? "You were signed out. Please sign in again." : null);
        throw new Error("401");
      }
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (b) {
        var err = new Error(typeof b.detail === "string" ? b.detail : r.status + " " + r.statusText);
        err.status = r.status; throw err;
      });
      return opts.raw ? r : r.json();
    });
}
function isTech() { return S.me && S.me.role === "contractor"; }

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
  if (!S.me) return start();
  if (location.hash === "#/account") { stopTimers(); S.view = null; S.sys = null; return renderAccount(); }
  var m = location.hash.match(/^#\/(home|monitor|setup)\/(\d+)/);
  if (!S.systems) return loadSystems();
  if (!m) {
    if (!S.systems.length) return renderNoSystems();
    location.replace("#/home/" + S.systems[0].id);
    return;
  }
  var id = +m[2], sys = S.systems.filter(function (x) { return x.id === id; })[0];
  if (!sys) { location.replace("#/home/" + S.systems[0].id); return; }
  if (m[1] !== "home" && !isTech()) { location.replace("#/home/" + id); return; }   // technician pages are for contractors
  var changed = !S.sys || S.sys.id !== id;
  S.view = m[1];
  S.sys = sys;
  if (changed) { S.latest = null; S.summary = null; S.history = {}; S.alerts = null; S.alertSettings = null; S.service = null; S.commands = null; S.pending = {}; S.form = {}; stopTimers(); startTimers(); }
  if (S.view === "setup") pollCommands();
  if (!changed && S.view !== "home" && !S.alertSettings) pollAlerts();   // the home page doesn't load them
  render();
}
window.addEventListener("hashchange", route);

// Who is signed in (cookie or saved API key); a 401 shows the sign-in form.
function start() {
  root.innerHTML = '<div class="login"><div class="empty">Loading…</div></div>';
  api("/api/auth/me").then(function (me) { S.me = me; S.systems = null; route(); })
    .catch(function (e) { if (e.message !== "401") { S.error = e.message; renderLogin(); } });
}
function loadSystems() {
  root.innerHTML = '<div class="login"><div class="empty">Loading…</div></div>';
  api("/api/systems").then(function (list) { S.systems = list; route(); })
    .catch(function (e) { if (e.message !== "401") { S.error = e.message; renderLogin(); } });
}

function stopTimers() { S.timers.forEach(clearInterval); S.timers = []; }
function startTimers() {
  pollLatest(); pollSummary(); pollHistory(); pollAlerts(); pollService();
  S.timers.push(setInterval(pollLatest, LIVE_MS));
  S.timers.push(setInterval(pollSummary, 60000));
  S.timers.push(setInterval(pollAlerts, 30000));
  S.timers.push(setInterval(pollService, 60000));
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
  if (S.view !== "home") api("/api/systems/" + id + "/alert-settings").then(function (d) { if (S.sys && S.sys.id === id) { S.alertSettings = d; render(); } }).catch(function () {});
}
function pollService() {
  var id = S.sys.id;
  api("/api/systems/" + id + "/service").then(function (d) { if (S.sys && S.sys.id === id) { S.service = d; render(); } }).catch(function () {});
}
function serviceItem(kind) { return ((S.service && S.service.items) || []).filter(function (x) { return x.kind === kind; })[0] || null; }
function localDate() { var d = new Date(), p = function (x) { return (x < 10 ? "0" : "") + x; }; return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()); }
function daysAgo(n) { return n <= 0 ? "today" : n === 1 ? "yesterday" : n + " days ago"; }
function dateWord(iso, withYear) {   // "2026-09-01" -> "Sep 1" (as a local calendar date, not UTC midnight)
  var p = iso.split("-"), d = new Date(+p[0], +p[1] - 1, +p[2]);
  return d.toLocaleDateString([], withYear ? { month: "short", year: "numeric" } : { month: "short", day: "numeric" });
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
  if (S.me && S.me.via === "session") {        // plain fetch: this must not loop back through api()'s 401 handling
    fetch("/api/auth/logout", { method: "POST", headers: { "X-Requested-With": "fullscope" }, credentials: "same-origin" }).catch(function () {});
  }
  S.key = null; S.me = null; S.systems = null; S.sys = null; S.view = null; S.error = msg || null;
  save("fs_key", null);
  renderLogin();
}

// ---------------------------------------------------------------- sign-in
function renderLogin() {
  root.className = "fs home";
  var byKey = S.loginMode === "key";
  root.innerHTML =
    '<header class="h-top">' + LOGO + TAGLINE + "</header>" +
    '<div class="login"><div class="h-card"><h1>Sign in</h1>' +
    (byKey
      ? "<p>Paste the API key for your account (from <span class=\"mono\">manage.py create-key</span>).</p>" +
        '<form id="lf"><input id="k" type="password" autocomplete="off" placeholder="hvk_…" aria-label="API key">'
      : '<form id="lf"><label class="lbl" for="em">Email</label><input id="em" type="email" autocomplete="username" aria-label="Email">' +
        '<label class="lbl" for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" aria-label="Password">') +
    '<div class="row"><button class="h-btn primary" type="submit">Sign in</button></div>' +
    '<div class="err" role="alert">' + esc(S.error || "") + "</div></form>" +
    '<p class="alt"><a class="muted" href="#" id="mode">' + (byKey ? "Sign in with email and password" : "Use an API key instead") + "</a></p></div></div>";
  $("#mode").addEventListener("click", function (e) { e.preventDefault(); S.loginMode = byKey ? "password" : "key"; S.error = null; renderLogin(); });
  $("#lf").addEventListener("submit", function (e) {
    e.preventDefault();
    if (byKey) {
      var k = $("#k").value.trim();
      if (!k) return;
      S.key = k; S.error = null; save("fs_key", k); S.me = null; S.systems = null;
      route();
      return;
    }
    var em = $("#em").value.trim(), pw = $("#pw").value, btn = $("#lf button");
    if (!em || !pw) return;
    btn.disabled = true;
    api("/api/auth/login", { method: "POST", body: { email: em, password: pw }, keep401: true }).then(function (me) {
      S.me = me; S.error = null; S.systems = null; route();
    }).catch(function (err) { S.error = err.message; renderLogin(); $("#em").value = em; $("#pw").focus(); });
  });
  var first = $("#k") || $("#em");
  if (first) first.focus();
}

// ---------------------------------------------------------------- account (change password)
function renderAccount() {
  root.className = "fs home";
  var me = S.me, u = me.user, back = S.systems && S.systems.length ? "#/home/" + S.systems[0].id : "#/";
  var who = u ? esc(u.name || u.email) + ' <span class="muted">· ' + esc(u.email) + "</span>" : "Signed in with an API key" + (me.account ? " for " + esc(me.account.name) : "");
  root.innerHTML =
    '<header class="h-top">' + LOGO + TAGLINE + '<div class="upd"><a class="muted" href="' + back + '" style="font-size:12px">← Back</a>' +
    '<a class="muted" href="#" data-signout style="font-size:12px">Sign out</a></div></header>' +
    '<div class="login"><div class="h-card"><h1>Your account</h1><p>' + who + "<br>" + (me.role === "contractor" ? "Contractor" : "Homeowner") + "</p>" +
    (u ? '<form id="pf"><label class="lbl" for="cur">Current password</label><input id="cur" type="password" autocomplete="current-password">' +
      '<label class="lbl" for="np">New password (at least 10 characters)</label><input id="np" type="password" autocomplete="new-password">' +
      '<label class="lbl" for="np2">New password again</label><input id="np2" type="password" autocomplete="new-password">' +
      '<div class="row"><button class="h-btn primary" type="submit">Change password</button></div><div class="err" role="alert" id="pe"></div></form>'
      : "<p class=\"muted\">To sign in with a password, ask for a user to be created with <span class=\"mono\">manage.py create-user</span>.</p>") +
    "</div></div>";
  bindCommon();
  var f = $("#pf");
  if (f) f.addEventListener("submit", function (e) {
    e.preventDefault();
    var pe = $("#pe");
    if ($("#np").value !== $("#np2").value) { pe.textContent = "The new passwords don't match."; return; }
    api("/api/auth/password", { method: "POST", body: { current: $("#cur").value, "new": $("#np").value }, keep401: true }).then(function () {
      f.reset(); pe.style.color = "var(--ok)"; pe.textContent = "Password changed.";
    }).catch(function (err) { pe.style.color = ""; pe.textContent = err.message; });
  });
}

function renderNoSystems() {
  root.className = "fs home";
  root.innerHTML = '<div class="login"><div class="h-card"><h1>No systems yet</h1>' +
    (isTech() ? "<p>Create one with <span class=\"mono\">manage.py create-system</span>, then reload.</p>"
      : "<p>No system is linked to your sign-in yet. Your contractor can add it.</p>") +
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
  else if (top) {
    h1 = "Your system is " + (MODE_WORD[mode] || "running").toLowerCase() + ", but " + top.title.charAt(0).toLowerCase() + top.title.slice(1); p = top.why;
    var fi = serviceItem("filter");
    if (top.area === "cooling" && fi && fi.days_since !== null) p += " Your filter was last changed " + daysAgo(fi.days_since) + ".";
  }
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
    (isTech() ? '<a class="muted" href="#/monitor/' + S.sys.id + '" style="font-size:12px">Details for your technician →</a>' : "") +
    '<a class="muted" href="#/account" style="font-size:12px">Account</a>' +
    '<a class="muted" href="#" data-signout style="font-size:12px">Sign out</a></div></header>' +
    '<main class="h-wrap">' +
    '<section class="h-card h-hero" aria-label="System status" style="border-top-color:' + heroColor + '"><div class="msg">' +
    '<div class="kicker">' + kicker + "<span>" + esc(runTxt) + "</span></div>" +
    "<h1>" + esc(h1) + "</h1><p>" + esc(p) + "</p>" +
    (top ? '<div class="acts"><a class="h-btn primary" href="#" data-open="h-issue-0">What should I do?</a>' + requestBtn() + "</div>" : "") +
    '</div><div class="h-now">' +
    "<div><span>Inside</span><b>" + big(d.t_ret) + "</b><em>Air returning to the system</em></div>" +
    "<div><span>Outside</span><b>" + big(d.oat) + "</b><em>" + (isNum(d.orh) ? "Humidity " + Math.round(d.orh) + " %" : "At the outdoor unit") + "</em></div>" +
    "<div><span>Air from vents</span><b>" + big(d.t_sup) + "</b><em>" + esc(dtTxt) + "</em></div>" +
    "<div><span>Running, last 24 h</span><b>" + (isNum(sm.runtime_hours) ? fmt(sm.runtime_hours, 1) + "<small>hr</small>" : "—") + "</b><em>" +
    (isNum(sm.cycles) ? sm.cycles + " cycle" + (sm.cycles === 1 ? "" : "s") : "") + "</em></div>" +
    "</div></section>" +
    '<div class="h-grid"><div class="h-col">' + homeNoticed(fresh) + homeMaint() + homeAlerts() + "</div>" +
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
  var fd = $("[data-filter-done]");
  if (fd) fd.addEventListener("click", function () {
    if (!confirm("Mark the air filter as changed today?")) return;
    fd.disabled = true;
    api("/api/systems/" + S.sys.id + "/service/items/filter/done", { method: "POST", body: { date: localDate() } })
      .then(function (d) { S.service = d; render(); }).catch(function (e) { fd.disabled = false; alert("Couldn't save: " + e.message); });
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

// ---------- maintenance + contractor (design: .h-maint, .h-pro)
var MAINT_ST = { due: ["caution", "Due now", "var(--caution)"], soon: ["advisory", "Due soon", "var(--advisory)"],
  ok: ["ok", "Up to date", "var(--ok)"], unset: ["offline", "Not set", "var(--offline)"] };
function homeMaint() {
  var sv = S.service;
  var h = '<section class="h-card" aria-label="Maintenance"><h2>Maintenance</h2>';
  if (!sv) return h + '<div class="empty">Loading…</div></section>';
  h += '<ul class="h-maint">' + sv.items.map(function (x) {
    var m = MAINT_ST[x.status], txt, btn = "";
    if (x.kind === "filter") {
      txt = x.last_done ? "Changed " + dateWord(x.last_done) + " · " + daysAgo(x.days_since) +
        (x.interval_run_hours ? " · " + Math.round(x.run_hours_since) + " of " + Math.round(x.interval_run_hours) + " hours of use" : "") +
        (x.next_due && x.status !== "due" ? " · next about " + dateWord(x.next_due) : "")
        : "Tell us when you change it and we'll remind you next time.";
      btn = '<button class="h-btn" type="button" data-filter-done>' + "I changed it" + "</button>";
    } else {
      txt = x.last_done ? "Last visit " + dateWord(x.last_done, true) + (x.next_due ? " · next due " + dateWord(x.next_due, true) : "")
        : "Your contractor records tune-ups here.";
    }
    var pct = x.progress === null ? 0 : Math.min(100, Math.round(x.progress * 100));
    return "<li><b>" + esc(x.label) + "</b>" + st(m[0], m[1]) + "<span>" + esc(txt) + "</span>" + (btn || "<span></span>") +
      '<div class="bar"><i style="width:' + pct + "%;background:" + m[2] + '"></i></div></li>';
  }).join("") + "</ul>";
  var c = sv.contractor;
  if (c) {
    var initials = (c.name || "?").replace(/&/g, " ").split(/\s+/).filter(Boolean).slice(0, 2).map(function (w) { return w.charAt(0).toUpperCase(); }).join("");
    h += '<div class="h-pro"><div class="mono" aria-hidden="true">' + esc(initials) + "</div><div><b>" + esc(c.name || "Your contractor") + "</b><span>Your service contractor" +
      (c.phone ? ' · <a class="muted" href="tel:' + esc(c.phone.replace(/[^0-9+]/g, "")) + '">' + esc(c.phone) + "</a>" : "") + "</span></div>" + requestBtn() + "</div>";
  } else {
    h += '<div class="h-pro"><div><span>No service contractor added yet. Your installer can add their details.</span></div></div>';
  }
  return h + "</section>";
}
// Request service opens the homeowner's own email (or phone) with the current issues filled in.
function requestBtn() {
  var c = S.service && S.service.contractor;
  if (!c || !(c.email || c.phone)) return "";
  if (!c.email) return '<a class="h-btn" href="tel:' + esc(c.phone.replace(/[^0-9+]/g, "")) + '">Call for service</a>';
  var lines = flags().map(function (f) { var i = flagInfo(f); return "- " + i.title + " (" + f.text + ")"; });
  var fi = serviceItem("filter");
  var body = "Hello,\n\nI'd like to request a service visit for " + S.sys.name + ".\n\n" +
    (lines.length ? "Fullscope is currently showing:\n" + lines.join("\n") + "\n\n" : "") +
    (fi && fi.last_done ? "Air filter last changed " + dateWord(fi.last_done) + " (" + daysAgo(fi.days_since) + ").\n\n" : "") +
    "Thank you.";
  var href = "mailto:" + encodeURIComponent(c.email) + "?subject=" + encodeURIComponent("Service request: " + S.sys.name) + "&body=" + encodeURIComponent(body);
  return '<a class="h-btn" href="' + esc(href) + '">Request service</a>';
}

function homeAlerts() {
  var list = S.alerts;
  var h = '<section class="h-card" aria-label="Recent alerts"><h2>Recent alerts</h2>' +
    '<div class="h-sub">Problems that lasted at least 5 minutes, last 7 days</div>';
  if (!list) return h + '<div class="empty">Loading…</div></section>';
  if (!list.length) return h + '<ul class="h-health"><li><b>None</b>' + st("ok", "Good") + "<p>Nothing needed attention in the last 7 days.</p></li></ul></section>";
  return h + '<ul class="h-health">' + list.slice(0, 8).map(function (a) {
    var i = flagInfo(a);
    return "<li><b>" + esc(i.title) + "</b>" + (a.open ? (a.acked_at ? st("advisory", "Being handled") : st(i.level, "Ongoing")) : st("ok", "Cleared")) +
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
    (page === "monitor" ? '<button class="btn" type="button" data-export>Export CSV</button>' : "") + '<a class="btn" href="#/account" style="text-decoration:none">Account</a><button class="btn" type="button" data-signout>Sign out</button></div></nav></header>' +
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

function mutedUntil(code) {
  var m = ((S.alertSettings && S.alertSettings.mutes) || []).filter(function (x) { return x.code === code; })[0];
  return m ? m.until : null;
}
function alertLog() {
  var list = S.alerts, body, seenCode = {};
  if (!list) body = '<tr><td colspan="6" class="faint">Loading…</td></tr>';
  else if (!list.length) body = '<tr class="normal"><td>' + st("ok", "Normal") + '</td><td class="cond" colspan="5">No alerts in the last 7 days.</td></tr>';
  else body = list.map(function (a) {
    var i = flagInfo(a), sensor = ["sensor_issue", "node_offline", "no_data"].indexOf(a.code) >= 0;
    var state = !a.open ? '<span class="n">' + esc(lasted(a)) + "</span>"
      : (a.acked_at ? st("ok", "Handling") : st(i.level === "advisory" ? "advisory" : "caution", "Active")) + ' <span class="faint n">' + esc(lasted(a)) + "</span>";
    var acts = [];
    if (a.open) acts.push('<button class="btn" type="button" data-alert-act="ack" data-id="' + a.id + '" data-on="' + (a.acked_at ? 0 : 1) + '">' + (a.acked_at ? "Undo" : "Acknowledge") + "</button>");
    if (!seenCode[a.code]) {                      // mute is per kind of alert: offer it on the newest row of each kind
      seenCode[a.code] = true;
      var mu = mutedUntil(a.code);
      acts.push(mu ? '<span class="faint">Muted to ' + esc(when(mu)) + '</span><button class="btn" type="button" data-alert-act="mute" data-code="' + esc(a.code) + '" data-hours="0">Unmute</button>'
        : '<select class="sel" aria-label="Mute emails for this kind of alert" data-alert-mute="' + esc(a.code) + '"><option value="">Mute emails…</option>' +
          '<option value="24">for 1 day</option><option value="168">for 7 days</option><option value="720">for 30 days</option></select>');
    }
    return "<tr><td>" + st(sensor ? "sensor" : i.level, sensor ? "Sensor" : TECH_LEVEL[i.level]) + '</td><td class="cond">' + esc(a.text) + "</td>" +
      '<td class="n">' + esc(when(a.started_at)) + "</td><td>" + state + "</td>" +
      '<td class="faint n">' + (a.emailed_at ? "Emailed " + esc(ampm(a.emailed_at)) : "—") + '</td><td><div class="acts-cell">' + acts.join("") + "</div></td></tr>";
  }).join("");
  return '<section class="panel" id="alert-log" aria-label="Alert log"><div class="ph"><h2>Alert log</h2><span class="sub">Last 7 days · raised after 5 min, cleared after 5 min without the condition</span>' +
    '<span class="right sub">' + esc(emailSummary()) + ' · <a class="muted" href="#/setup/' + S.sys.id + '">change</a></span></div>' +
    '<table class="dt diag"><thead><tr><th>Severity</th><th>Condition</th><th>Started</th><th>Duration</th><th>Email</th><th></th></tr></thead><tbody>' + body + "</tbody></table></section>";
}
function emailSummary() {
  var a = S.alertSettings;
  if (!a) return "";
  if (!a.email_enabled) return "Email is off (no SMTP set up)";
  var who = [];
  if (a.email_owner && a.owner_email) who.push("you");
  if (a.email_contractor && a.contractor_email) who.push("contractor");
  return who.length ? "Emailed to " + who.join(" and ") : "Nobody gets alert emails";
}
function alertEmailPanel() {
  var a = S.alertSettings;
  if (!a) return '<section class="panel" aria-label="Alert email"><div class="ph"><h2>Alert email</h2></div><div class="empty">Loading…</div></section>';
  function row(key, label, addr, missing) {
    return '<tr><th><label><input type="checkbox" data-alert-pref="' + key + '"' + (a[key] ? " checked" : "") + (addr ? "" : " disabled") + "> " + label + "</label></th>" +
      '<td class="faint">' + esc(addr || missing) + "</td></tr>";
  }
  var mutes = a.mutes.map(function (m) {
    return "<tr><th>" + esc(flagInfo(m).title) + ' <span class="faint mono">' + esc(m.code) + '</span></th><td><div class="acts-cell"><span class="faint">until ' + esc(when(m.until)) +
      '</span><button class="btn" type="button" data-alert-act="mute" data-code="' + esc(m.code) + '" data-hours="0">Unmute</button></div></td></tr>';
  }).join("");
  var note = a.email_enabled
    ? "The owner gets plain-language emails with what to do; the contractor gets the technical reading and a link to this view. Acknowledged alerts send no “cleared” email."
    : "Email is off until SMTP_HOST, SMTP_USER and SMTP_PASS are set in hvac-cloud/.env (see the README). Choices here are kept.";
  return '<section class="panel" aria-label="Alert email"><div class="ph"><h2>Alert email</h2><span class="sub">Who is emailed when an alert is raised</span></div>' +
    '<table class="dt"><tbody>' + row("email_owner", "Account owner", a.owner_email, "no email on the account") +
    row("email_contractor", "Service contractor", a.contractor_email, "add the contractor's email above") +
    (mutes ? '<tr class="sec"><td colspan="2">Muted</td></tr>' + mutes : "") + "</tbody></table>" +
    '<div class="na-note">' + esc(note) + "</div></section>";
}
function saveAlertSettings(path, body) {
  return api("/api/systems/" + S.sys.id + path, { method: "PUT", body: body }).then(function (d) { S.alertSettings = d; render(); })
    .catch(function (e) { alert("Couldn't save: " + e.message); });
}
root.addEventListener("click", function (e) {
  var b = e.target.closest && e.target.closest("[data-alert-act]");
  if (!b || !S.sys) return;
  if (b.dataset.alertAct === "ack") {
    b.disabled = true;
    api("/api/systems/" + S.sys.id + "/alerts/" + b.dataset.id + "/ack", { method: "POST", body: { ack: b.dataset.on === "1" } })
      .then(function () { pollAlerts(); }).catch(function (err) { b.disabled = false; alert("Couldn't save: " + err.message); });
  } else if (b.dataset.alertAct === "mute") {
    saveAlertSettings("/alert-mutes/" + encodeURIComponent(b.dataset.code), { hours: +b.dataset.hours });
  }
});
root.addEventListener("change", function (e) {
  var t = e.target;
  if (!S.sys || !t.dataset) return;
  if (t.dataset.alertMute && t.value) saveAlertSettings("/alert-mutes/" + encodeURIComponent(t.dataset.alertMute), { hours: +t.value });
  if (t.dataset.alertPref) { var body = {}; body[t.dataset.alertPref] = t.checked; saveAlertSettings("/alert-settings", body); }
});

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
    '<div class="cfg">' + contractorPanel() + maintPanel() + alertEmailPanel() + "</div>" +
    commandLog() + "</main>";
  root.innerHTML = html;
  bindCommon();
}

// ---------- service contractor + maintenance schedule (saved in the cloud, not on a node)
function textField(id, value, label, type) {
  return '<label class="field wide"><input id="' + id + '" type="' + (type || "text") + '" autocomplete="off" aria-label="' + esc(label) + '" value="' + esc(value || "") + '"></label>';
}
function contractorPanel() {
  var c = (S.service && S.service.contractor) || {};
  return '<section class="panel" aria-label="Service contractor"><div class="ph"><h2>Service contractor</h2><span class="sub">Shown to the homeowner with a Request service button</span></div>' +
    '<table class="dt"><tbody>' +
    "<tr><th>Company</th><td>" + textField("f-ctr-name", c.name, "Contractor name") + "</td></tr>" +
    "<tr><th>Phone</th><td>" + textField("f-ctr-phone", c.phone, "Contractor phone", "tel") + "</td></tr>" +
    "<tr><th>Email</th><td>" + textField("f-ctr-email", c.email, "Contractor email", "email") + "</td></tr>" +
    '<tr><td colspan="2"><div class="acts-cell"><button class="btn" type="button" data-svc="contractor">Save contractor</button>' +
    '<span class="faint">Service requests open the homeowner\'s email to this address</span></div></td></tr></tbody></table></section>';
}
function maintPanel() {
  var items = (S.service && S.service.items) || [];
  var rows = items.map(function (x) {
    var k = x.kind, m = MAINT_ST[x.status];
    return "<tr><th>" + esc(x.label) + "</th>" +
      "<td>" + field("f-days-" + k, "days", "", x.label + " interval in days").replace("<input ", '<input value="' + (x.interval_days || "") + '" ') + "</td>" +
      "<td>" + (k === "filter" ? field("f-hours-" + k, "h", "off", x.label + " interval in run hours").replace("<input ", '<input value="' + (x.interval_run_hours || "") + '" ') : '<span class="faint">—</span>') + "</td>" +
      "<td>" + textField("f-last-" + k, x.last_done, x.label + " last done", "date") + "</td>" +
      "<td>" + st(m[0], m[1]) + (x.run_hours_since !== null && k === "filter" ? ' <span class="faint n">' + Math.round(x.run_hours_since) + " h</span>" : "") + "</td>" +
      '<td><button class="btn" type="button" data-svc="item" data-kind="' + k + '">Save</button></td></tr>';
  }).join("");
  return '<section class="panel" aria-label="Maintenance schedule"><div class="ph"><h2>Maintenance schedule</h2><span class="sub">Due after the days or the blower run hours, whichever comes first</span></div>' +
    '<table class="dt"><thead><tr><th>Item</th><th>Every</th><th>Or every</th><th>Last done</th><th>Status</th><th></th></tr></thead><tbody>' +
    (rows || '<tr><td colspan="6" class="faint">Loading…</td></tr>') + "</tbody></table>" +
    '<div class="na-note">The homeowner can mark the filter changed from their page. Leave run hours empty to go by days only.</div></section>';
}
function saveService(path, method, body, keys) {
  api("/api/systems/" + S.sys.id + "/service" + path, { method: method, body: body }).then(function (d) {
    S.service = d; keys.forEach(function (k) { delete S.form[k]; }); S.msg = "Saved."; render();
  }).catch(function (e) { S.msg = "Couldn't save: " + e.message; render(); });
}
root.addEventListener("click", function (e) {
  var b = e.target.closest && e.target.closest("[data-svc]");
  if (!b || S.view !== "setup") return;
  function cur(id) { var el = document.getElementById(id); return el ? el.value.trim() : ""; }
  S.msg = null;
  if (b.dataset.svc === "contractor") {
    saveService("/contractor", "PUT", { name: cur("f-ctr-name"), phone: cur("f-ctr-phone"), email: cur("f-ctr-email") },
      ["f-ctr-name", "f-ctr-phone", "f-ctr-email"]);
    return;
  }
  var k = b.dataset.kind, days = parseInt(cur("f-days-" + k), 10), body = {};
  if (!(days >= 1 && days <= 3650)) { S.msg = "Enter how many days between visits (1 to 3650)."; render(); return; }
  body.interval_days = days;
  if (k === "filter") {
    var hrs = cur("f-hours-" + k);
    if (hrs === "") body.interval_run_hours = null;
    else if (!(parseFloat(hrs) > 0 && parseFloat(hrs) <= 20000)) { S.msg = "Enter run hours between 1 and 20000, or leave it empty."; render(); return; }
    else body.interval_run_hours = parseFloat(hrs);
  }
  var last = cur("f-last-" + k);
  if (last) {
    if (last > localDate()) { S.msg = "The last-done date can't be in the future."; render(); return; }
    body.last_done = last;
  }
  saveService("/items/" + k, "PATCH", body, ["f-days-" + k, "f-hours-" + k, "f-last-" + k]);
});

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
