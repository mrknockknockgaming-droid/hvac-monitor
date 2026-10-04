/* Browser preview of the display thermostat's screen (800 x 480).
   Home: room temperature, setpoints, mode. Alerts: the monitoring system's alerts in plain words
   and the System health areas. Service (PIN): key numbers, a pressure / air trend with markers
   where the electrical module found a problem, and the open diagnostics in technical words.
   Data: /api/systems/<id>/thermostat (controls, the thermostat's report) and
   /api/systems/<id>/thermostat/display (the feed the cloud also publishes to the thermostat). */
"use strict";
var screenEl = document.getElementById("screen");
var T = { sys: null, tstat: null, feed: null, tab: "home", detail: null, pin: "", unlocked: 0, range: 6, msg: null, err: null };
var LEVEL_WORD = { fault: "Service needed", caution: "Check soon", advisory: "Good to know", ok: "Good", offline: "No data" };
var MODE_WORDS = [["cool", "Cool"], ["heat", "Heat"], ["auto", "Auto"], ["off", "Off"]];
var WAIT_WORD = { min_off: "Protecting the compressor: starting in a few minutes", min_on: "Finishing a minimum run",
  max_starts: "Resting the compressor", sensor: "Room sensor problem: heating and cooling are off" };
var LOCK_MS = 5 * 60 * 1000;

function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
function isNum(v) { return typeof v === "number" && isFinite(v); }
function fmt(v, d) { return isNum(v) ? v.toFixed(d === undefined ? 1 : d) : "—"; }
function st(cls, word) { return '<span class="st ' + cls + '">' + esc(word) + "</span>"; }
function hhmm(epoch) { return new Date(epoch * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }

function api(path, opts) {
  opts = opts || {};
  var h = { "X-Requested-With": "fullscope" };
  if (opts.body) h["Content-Type"] = "application/json";
  return fetch(path, { method: opts.method || "GET", headers: h, credentials: "same-origin", body: opts.body ? JSON.stringify(opts.body) : undefined })
    .then(function (r) {
      if (r.status === 401) throw new Error("401");
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (b) { throw new Error(typeof b.detail === "string" ? b.detail : r.status); });
      return r.json();
    });
}

// ---------------------------------------------------------------- loading
function start() {
  var m = location.hash.match(/^#(\d+)/);
  (m ? Promise.resolve(+m[1]) : api("/api/systems").then(function (l) { return l.length ? l[0].id : null; }))
    .then(function (id) {
      if (!id) { T.err = "No system to show."; return draw(); }
      T.sys = id; poll(); pollFeed();
      setInterval(poll, 5000); setInterval(pollFeed, 15000); setInterval(draw, 30000);
    }).catch(fail);
}
function fail(e) { T.err = e.message === "401" ? "signin" : e.message; draw(); }
function poll() { api("/api/systems/" + T.sys + "/thermostat").then(function (d) { T.tstat = d; T.err = null; draw(); }).catch(fail); }
function pollFeed() { api("/api/systems/" + T.sys + "/thermostat/display").then(function (d) { T.feed = d; draw(); }).catch(fail); }
function send(path, method, body) {
  return api("/api/systems/" + T.sys + "/thermostat" + path, { method: method, body: body })
    .then(function (d) { T.tstat = d; draw(); })
    .catch(function (e) { toast("Not changed: " + e.message); poll(); });
}
function toast(m) { T.msg = m; draw(); setTimeout(function () { T.msg = null; draw(); }, 3500); }

// ---------------------------------------------------------------- frame
function draw() {
  if (T.err === "signin") { screenEl.innerHTML = '<div class="ts-pinwrap"><div class="ts-head">Sign in first</div><div class="ts-empty">Open <a href="./" style="color:#7fb2e5">the Fullscope web app</a>, sign in, then come back.</div></div>'; return; }
  if (T.err && !T.tstat) { screenEl.innerHTML = '<div class="ts-pinwrap"><div class="ts-empty">' + esc(T.err) + "</div></div>"; return; }
  if (!T.tstat) { screenEl.innerHTML = '<div class="ts-pinwrap"><div class="ts-empty">Starting…</div></div>'; return; }
  if (T.unlocked && Date.now() - T.unlocked > LOCK_MS) { T.unlocked = 0; if (T.tab === "service") T.tab = "home"; }
  var f = T.feed, rep = T.tstat.report || {}, n = f ? f.alerts.length : 0;
  var oat = f && f.tech.now.oat;
  var top = '<div class="ts-top"><span class="clock">' + new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) + "</span>" +
    "<span>Outside " + (isNum(oat) ? Math.round(oat) + "°" : "—") + "</span>" +
    (isNum(rep.room && rep.room.rh) ? "<span>Humidity " + Math.round(rep.room.rh) + " %</span>" : "") +
    '<span class="sp"></span>' +
    (f ? '<button class="chip" data-tab="alerts">' + st(f.status, f.status === "ok" ? "System OK" : LEVEL_WORD[f.status]) + "</button>" : "") + "</div>";
  var badge = n ? '<span class="badge lvl-' + f.alerts[0].level + '">' + n + "</span>" : "";
  var tabs = '<div class="ts-tabs" role="tablist">' +
    tabBtn("home", "Home") + tabBtn("alerts", "Alerts" + badge) + tabBtn("service", "Service") + "</div>";
  var body = T.tab === "alerts" ? alertsTab() : T.tab === "service" ? serviceTab() : homeTab();
  screenEl.innerHTML = top + '<div class="ts-body">' + body + "</div>" + tabs + (T.msg ? '<div class="ts-msg">' + esc(T.msg) + "</div>" : "");
  if (T.tab === "service" && T.unlocked) drawGraph();
}
function tabBtn(id, label) { return '<button role="tab" data-tab="' + id + '" aria-selected="' + (T.tab === id) + '">' + label + "</button>"; }

// ---------------------------------------------------------------- home
function homeTab() {
  var t = T.tstat, rep = t.report || {}, now = t.now, mode = t.settings.mode, out = rep.out || {};
  var room = rep.room && rep.room.t;
  var doing = !t.present ? "Thermostat not reporting" : rep.wait ? WAIT_WORD[rep.wait] || rep.wait :
    rep.call === "cool" ? "Cooling to " + Math.round(now.cool) + "°" : rep.call === "heat" ? "Heating to " + Math.round(now.heat) + "°" + (out.W ? " · backup heat" : "") :
    mode === "off" ? "System off" : "Holding temperature";
  var src = now.source === "hold" ? (now.next_change ? "Held until " + new Date(now.next_change).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "Held") + '<button data-resume>Resume schedule</button>' :
    now.source === "schedule" ? "Following your schedule" : "";
  var steps = (mode === "heat" || mode === "auto" || mode === "emergency_heat" ? stepper("heat", "Heat to") : "") +
    (mode === "cool" || mode === "auto" ? stepper("cool", "Cool to") : "");
  return '<div class="ts-home"><div class="ts-room"><span class="lbl">Inside</span>' +
    '<div class="big">' + (isNum(room) ? Math.round(room) + "<small>°</small>" : "—") + "</div>" +
    '<div class="doing">' + esc(doing) + "</div>" + '<div class="src">' + src + "</div></div>" +
    '<div class="ts-side">' + (steps || '<div class="ts-empty">Heating and cooling are off.</div>') +
    '<div class="ts-modes">' + MODE_WORDS.map(function (m) { return '<button data-mode="' + m[0] + '" aria-pressed="' + (mode === m[0]) + '">' + m[1] + "</button>"; }).join("") + "</div></div></div>";
}
function stepper(key, label) {
  return '<div class="ts-step"><span class="t">' + label + "</span>" +
    '<button data-sp="' + key + '" data-d="-1" aria-label="' + label + ' down">−</button><b>' + Math.round(T.tstat.now[key]) + "°</b>" +
    '<button data-sp="' + key + '" data-d="1" aria-label="' + label + ' up">+</button></div>';
}

// ---------------------------------------------------------------- alerts (homeowner)
function alertsTab() {
  var f = T.feed;
  if (!f) return '<div class="ts-pinwrap"><div class="ts-empty">Loading…</div></div>';
  if (T.detail !== null) {
    var a = f.alerts.filter(function (x) { return x.id === T.detail; })[0];
    if (a) return '<div class="ts-detail">' + st(a.level, a.word) + "<h3>" + esc(a.title) + "</h3>" +
      "<h4>Why it matters</h4><p>" + esc(a.why) + "</p><h4>What you can do</h4><p>" + esc(a.todo) + "</p>" +
      '<div class="tech">For your contractor: ' + esc(a.tech) + " · since " + hhmm(a.since) + "</div>" +
      '<button class="ts-btn" data-back>Back</button></div>';
    T.detail = null;
  }
  var good = f.health.filter(function (r) { return r.level === "ok"; }).length;
  var cards = f.alerts.length ? f.alerts.map(function (x) {
    return '<button class="ts-card ' + x.level + '" data-alert="' + x.id + '"><span class="t">' + esc(x.title) + '</span><span class="w">' + esc(x.word) + " · tap for what to do</span></button>";
  }).join("") : '<div class="ts-empty">No alerts. Fullscope checks your system continuously while it runs.</div>';
  var head = !f.fresh || !f.alerts.length ? f.headline : f.alerts.length === 1 ? "One thing needs attention" : f.alerts.length + " things need attention";
  return '<div class="ts-alerts"><div class="ts-col"><div class="ts-head">' + esc(head) + "</div>" +
    '<div class="ts-sub">' + st(f.status, LEVEL_WORD[f.status]) + (f.updated ? " · checked " + hhmm(f.updated) : "") + "</div>" + cards + "</div>" +
    '<div class="ts-col"><div class="ts-head" style="font-size:18px">System health</div><div class="ts-sub">' + good + " of " + f.health.length + " areas are good</div>" +
    '<ul class="ts-health">' + f.health.map(function (r) { return "<li><b>" + esc(r.name) + "</b>" + st(r.level, r.word) + "<p>" + esc(r.text) + "</p></li>"; }).join("") + "</ul></div></div>";
}

// ---------------------------------------------------------------- service (technician)
function serviceTab() {
  if (!T.unlocked) {
    var dots = "";
    for (var i = 0; i < 6; i++) if (i < Math.max(4, T.pin.length)) dots += "<i" + (i < T.pin.length ? ' class="on"' : "") + "></i>";
    var keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "OK"];
    return '<div class="ts-pinwrap"><div class="ts-head" style="font-size:19px">Service PIN</div><div class="ts-dots">' + dots + "</div>" +
      '<div class="ts-pin">' + keys.map(function (k) { return '<button data-key="' + k + '">' + k + "</button>"; }).join("") + "</div></div>";
  }
  var f = T.feed;
  if (!f) return '<div class="ts-pinwrap"><div class="ts-empty">Loading…</div></div>';
  var k = f.tech.now, codes = f.alerts.map(function (a) { return a.code; });
  function chip(label, v, unit, d, bad) {
    var cls = bad && bad.some(function (c) { return codes.indexOf(c) >= 0; }) ? (bad[0] === "fault" ? "badf" : "bad") : "";
    return '<div class="' + cls + '"><span>' + label + "</span><b>" + (isNum(v) ? fmt(v, d) + "<small>" + unit + "</small>" : "—") + "</b></div>";
  }
  var running = k.mode === "cooling" || k.mode === "heating";
  var chips = '<div class="ts-chips"><div><span>Mode</span><b>' + esc((k.mode || "—").replace("_", " ")) + (running && isNum(k.run_min) ? "<small>" + Math.round(k.run_min) + " min</small>" : "") + "</b></div>" +
    chip("Superheat", k.sh, "°F", 1, ["sh_low", "sh_high"]) + chip("Subcooling", k.sc, "°F", 1, ["sc_low", "sc_high"]) +
    chip("Delta-T", k.dt, "°F", 1, ["dt_low"]) + chip("Suction", k.p_low, "psig", 0) + chip("Liquid", k.p_high, "psig", 0) +
    (f.tech.has_elec ? chip("Line", k.line_v, "V", 0, ["voltage"]) + chip("Compressor", k.comp_a, "A", 1, ["comp_amps_high", "comp_not_running"]) : "") + "</div>";
  var diags = f.alerts.length ? f.alerts.slice(0, 4).map(function (a) {
    return '<div class="' + a.level + '"><b>' + esc(a.tech_word) + " · " + esc(a.code) + "</b><span>" + esc(a.tech) + "</span></div>";
  }).join("") : '<div><b>No open diagnostics</b><span>All checks normal</span></div>';
  return '<div class="ts-svc">' + chips +
    '<div class="ts-gbar"><span class="ts-key"><i style="background:var(--side-low)"></i>Suction</span><span class="ts-key"><i style="background:var(--side-high)"></i>Liquid</span>' +
    '<span class="ts-key"><i style="background:var(--series-return)"></i>Return</span><span class="ts-key"><i style="background:var(--series-supply)"></i>Supply</span>' +
    '<span class="ts-key"><i style="background:var(--series-outdoor)"></i>Outdoor</span><span class="ts-key"><i style="background:#e2a33b;height:10px;width:10px;opacity:.6"></i>Electrical issue</span>' +
    '<span class="sp"></span><span class="ts-seg"><button data-range="1" aria-pressed="' + (T.range === 1) + '">1 h</button><button data-range="6" aria-pressed="' + (T.range === 6) + '">6 h</button></span>' +
    '<button class="ts-btn" style="height:28px;font-size:13px;padding:0 10px" data-lock>Lock</button></div>' +
    '<div class="ts-graph"><canvas id="graph"></canvas></div><div class="ts-diag">' + diags + "</div></div>";
}

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function drawGraph() {
  var c = document.getElementById("graph");
  if (!c || !T.feed) return;
  var tr = T.feed.tech.trend, marks = T.feed.tech.markers;
  var W = c.clientWidth, H = c.clientHeight, dpr = window.devicePixelRatio || 1;
  c.width = W * dpr; c.height = H * dpr;
  var g = c.getContext("2d");
  g.scale(dpr, dpr);
  var n = tr.on.length, first = Math.max(0, n - Math.round(T.range * 3600 / tr.step));
  var x0 = tr.t0 + first * tr.step, x1 = tr.t0 + (n - 1) * tr.step;
  var L = 40, R = W - 8, barH = 6, gap = 14, labelH = 16;
  var plotH = (H - labelH - barH - gap - 18) / 2;
  var top1 = labelH, top2 = labelH + plotH + gap;
  function X(t) { return L + (t - x0) / (x1 - x0 || 1) * (R - L); }
  g.font = "11px " + cssVar("--font-sans");

  // markers first, under the lines: a shaded span from start to end (or now) and a numbered tag
  marks.forEach(function (m, i) {
    var a = Math.max(X(m.start), L), b = Math.min(X(m.end || x1 + tr.step), R);
    if (b < L || a > R) return;
    var col = m.level === "fault" ? "#f06a60" : "#e2a33b";
    g.fillStyle = col; g.globalAlpha = 0.14; g.fillRect(a, top1, Math.max(b - a, 2), top2 + plotH - top1); g.globalAlpha = 1;
    g.strokeStyle = col; g.lineWidth = 1.5; g.beginPath(); g.moveTo(a, top1); g.lineTo(a, top2 + plotH); g.stroke();
    var tag = (i + 1) + " " + m.label, w = g.measureText(tag).width + 10;
    var tx = Math.min(a, R - w);
    g.fillStyle = col; g.fillRect(tx, 0, w, labelH - 2);
    g.fillStyle = "#0d1013"; g.fillText(tag, tx + 5, labelH - 6);
  });

  function plot(top, keys, colors, unit) {
    var lo = Infinity, hi = -Infinity;
    keys.forEach(function (k) { tr.series[k].slice(first).forEach(function (v) { if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); });
    if (lo === Infinity) { g.fillStyle = "#848f99"; g.fillText("No readings in this window", L + 8, top + plotH / 2); return; }
    var pad = Math.max((hi - lo) * 0.12, 2); lo -= pad; hi += pad;
    function Y(v) { return top + plotH - (v - lo) / (hi - lo) * plotH; }
    g.strokeStyle = "#262d34"; g.lineWidth = 1; g.fillStyle = "#848f99";
    for (var i = 0; i <= 2; i++) {
      var v = lo + (hi - lo) * i / 2, y = Y(v);
      g.beginPath(); g.moveTo(L, y); g.lineTo(R, y); g.stroke();
      g.fillText(Math.round(v), 4, y + 4);
    }
    g.fillStyle = "#0d1013"; g.fillRect(L + 2, top + 1, g.measureText(unit).width + 8, 14);
    g.fillStyle = "#9ea8b1"; g.fillText(unit, L + 6, top + 12);
    keys.forEach(function (k, j) {
      g.strokeStyle = colors[j]; g.lineWidth = 2; g.beginPath();
      var pen = false;
      for (var i = first; i < n; i++) {
        var v = tr.series[k][i];
        if (v === null) { pen = false; continue; }
        var x = X(tr.t0 + i * tr.step), y = Y(v);
        if (pen) g.lineTo(x, y); else g.moveTo(x, y);
        pen = true;
      }
      g.stroke();
    });
  }
  plot(top1, ["p_low", "p_high"], [cssVar("--side-low"), cssVar("--side-high")], "psig");
  plot(top2, ["t_ret", "t_sup", "oat"], [cssVar("--series-return"), cssVar("--series-supply"), cssVar("--series-outdoor")], "°F");

  // compressor running strip + time axis
  var by = top2 + plotH + 4;
  g.fillStyle = "#1b2026"; g.fillRect(L, by, R - L, barH);
  g.fillStyle = "#5cb87a";
  for (var i = first; i < n; i++) if (tr.on[i]) g.fillRect(X(tr.t0 + i * tr.step), by, Math.max(X(tr.t0 + (i + 1) * tr.step) - X(tr.t0 + i * tr.step), 1), barH);
  g.fillStyle = "#848f99";
  var ticks = T.range === 1 ? 4 : 6;
  for (var k = 0; k <= ticks; k++) {
    var t = x0 + (x1 - x0) * k / ticks, label = hhmm(t), w = g.measureText(label).width;
    g.fillText(label, Math.min(Math.max(X(t) - w / 2, L), R - w), H - 2);
  }
}

// ---------------------------------------------------------------- touch
screenEl.addEventListener("click", function (e) {
  var b = e.target.closest("button");
  if (!b) return;
  if (T.unlocked) T.unlocked = Date.now();             // activity keeps the service page open
  if (b.dataset.tab) { T.tab = b.dataset.tab; T.detail = null; if (T.tab !== "service") T.pin = ""; draw(); return; }
  if (b.dataset.sp) {
    var key = b.dataset.sp, v = Math.round(T.tstat.now[key]) + (+b.dataset.d), body = {};
    if (v < 50 || v > 90) return;
    body[key] = v; T.tstat.now[key] = v; draw();
    send("/hold", "POST", body); return;
  }
  if (b.hasAttribute("data-resume")) { send("/hold", "DELETE"); return; }
  if (b.dataset.mode) { send("", "PUT", { mode: b.dataset.mode }); return; }
  if (b.dataset.alert) { T.detail = +b.dataset.alert; draw(); return; }
  if (b.hasAttribute("data-back")) { T.detail = null; draw(); return; }
  if (b.dataset.range) { T.range = +b.dataset.range; draw(); return; }
  if (b.hasAttribute("data-lock")) { T.unlocked = 0; T.pin = ""; T.tab = "home"; draw(); return; }
  if (b.dataset.key) {
    var k = b.dataset.key;
    if (k === "⌫") T.pin = T.pin.slice(0, -1);
    else if (k === "OK") {
      if (T.pin === String(T.tstat.tech.service_pin)) { T.unlocked = Date.now(); pollFeed(); }
      else toast("Wrong PIN");
      T.pin = "";
    } else if (T.pin.length < 6) T.pin += k;
    draw();
  }
});

// scale the 832 x 512 device to fit narrow windows
function fit() {
  var box = document.getElementById("fit"), dev = box.firstElementChild, s = Math.min(1, box.clientWidth / 832);
  dev.style.transform = "scale(" + s + ")"; box.style.height = 512 * s + "px";
}
window.addEventListener("resize", fit);
fit();
start();
