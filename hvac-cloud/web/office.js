/* Fullscope Office (office.html): the business side for Fullscope's own staff. Inbox (order requests,
   website contact messages, contractors' support requests), Customers (contractor companies and
   their accounts), Devices (kits, boards, QR labels and warranty claims) and Flash (writes firmware to a
   pair of boards over USB from Chrome or Edge, saves the kit code on them and registers the kit).  Signs in through the web app (/app/); the server allows staff only. */
(function () {
"use strict";

var $ = function (s, r) { return (r || document).querySelector(s); };
var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
var root = $("#root");

function save(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
$("#theme").addEventListener("click", function () {
  var r = document.documentElement;
  r.dataset.theme = r.dataset.theme === "light" ? "dark" : "light";
  save("fs_theme", r.dataset.theme);
});

var S = { me: null, summary: null, view: null, status: "open", kind: "all", tickets: null, ticket: null,
          customers: null, customer: null, kits: null, kit: null, find: "", draft: "", msg: null, busy: false, error: null };

function api(path, opts) {
  opts = opts || {};
  var headers = { "X-Requested-With": "fullscope" };
  if (opts.body) headers["Content-Type"] = "application/json";
  return fetch(path, { method: opts.method || "GET", headers: headers, credentials: "same-origin", body: opts.body ? JSON.stringify(opts.body) : undefined })
    .then(function (r) {
      if (r.status === 401) { toSignIn(); throw new Error("401"); }
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (b) {
        var err = new Error(typeof b.detail === "string" ? b.detail : r.status + " " + r.statusText);
        err.status = r.status; throw err;
      });
      return r.json();
    });
}
// Sign in on the web app's page, which sends the browser back here afterwards.
function toSignIn() {
  try { sessionStorage.setItem("fs_return", location.href); } catch (e) {}
  location.href = "/app/";
}

function esc(s) { return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
function st(cls, word) { return '<span class="st ' + cls + '">' + esc(word) + "</span>"; }
function when(t) {
  if (!t) return "—";
  var d = new Date(t), now = new Date();
  var time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return "Today " + time;
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" }) + ", " + time;
}
var LOGO = '<a class="brand logo" href="/app/" title="Fullscope web app"><img src="fullscope-wordmark.webp" alt="Fullscope"></a>';
var STATUS = { "new": ["caution", "New"], open: ["advisory", "Open"], waiting: ["offline", "Waiting on customer"], closed: ["ok", "Closed"] };
var KIND = { order: "Order", contact: "Contact", support: "Support" };
var KSTATUS = { stock: ["offline", "In stock"], shipped: ["advisory", "Shipped"], installed: ["ok", "Installed"] };
var BSTATUS = { stock: ["offline", "In stock"], shipped: ["advisory", "Shipped"], installed: ["ok", "Installed"], rma: ["caution", "RMA"], retired: ["offline", "Retired"] };
var WSTATUS = { open: ["caution", "Open"], replaced: ["ok", "Replaced"], repaired: ["ok", "Repaired"], denied: ["offline", "Denied"] };
var KINDNAME = { outdoor: "Outdoor", indoor: "Indoor" };
var CSTATUS = { lead: ["advisory", "Lead"], active: ["ok", "Active"], paused: ["caution", "Paused"], closed: ["offline", "Closed"] };

// ---------------------------------------------------------------- routing
function route() {
  if (!S.me) return start();
  var h = location.hash, m;
  S.msg = null;
  if ((m = h.match(/^#\/inbox(?:\/(\d+))?$/))) {
    S.view = "inbox";
    var id = m[1] ? +m[1] : null;
    if (!id) S.ticket = null;
    else if (!S.ticket || S.ticket.id !== id) { S.ticket = { id: id, loading: true }; S.draft = ""; loadTicket(id); }
    loadInbox();
    if (!S.customers) loadCustomers();           // for linking a ticket to a customer
  } else if ((m = h.match(/^#\/customers(?:\/(\d+|new))?$/))) {
    S.view = "customers";
    S.customer = m[1] === "new" ? { id: "new" } : m[1] ? { id: +m[1], loading: true } : null;
    if (S.customer && S.customer.loading) loadCustomer(S.customer.id);
    loadCustomers();
  } else if ((m = h.match(/^#\/devices(?:\/(\d+|new))?$/))) {
    S.view = "devices";
    S.kit = m[1] === "new" ? { id: "new" } : m[1] ? (S.kit && S.kit.id === +m[1] && !S.kit.error ? S.kit : { id: +m[1], loading: true }) : null;
    if (S.kit && S.kit.loading) loadKit(S.kit.id);
    loadKits();
    if (!S.customers) loadCustomers();           // for shipping a kit to a customer
  } else if (h === "#/flash") {
    S.view = "flash";
    if (!S.kits) loadKits();                     // to make up a kit code nobody has
  } else {
    location.replace("#/inbox");
    return;
  }
  render();
}
window.addEventListener("hashchange", route);

function start() {
  root.innerHTML = '<div class="of-gate"><div class="empty">Loading…</div></div>';
  api("/api/auth/me").then(function (me) {
    S.me = me;
    if (!me.staff) return renderGate();
    loadSummary();
    route();
    setInterval(function () { if (!document.hidden) { loadSummary(); if (S.view === "inbox") loadInbox(); } }, 30000);
  }).catch(function (e) { if (e.message !== "401") { root.innerHTML = '<div class="of-gate"><div class="empty">Can\'t reach the server: ' + esc(e.message) + "</div></div>"; } });
}
function renderGate() {
  root.innerHTML = '<div class="of-gate"><div class="h-card login" style="margin:0;max-width:none"><div style="padding:18px"><h1 style="margin:0 0 6px;font-size:20px">Fullscope Office</h1>' +
    '<p class="muted">These pages are for Fullscope staff. You are signed in as ' + esc((S.me.user && S.me.user.email) || "an API key") + ".</p>" +
    '<a class="h-btn" href="/app/">Back to the app</a></div></div></div>';
}

// ---------------------------------------------------------------- data
function loadSummary() { api("/api/office/summary").then(function (d) { S.summary = d; render(); }).catch(function () {}); }
function loadInbox() {
  loadSummary();
  api("/api/office/inbox?status=" + S.status + "&kind=" + S.kind).then(function (d) { S.tickets = d; S.error = null; render(); })
    .catch(function (e) { if (e.message !== "401") { S.error = e.message; render(); } });
}
function loadTicket(id) {
  api("/api/office/inbox/" + id).then(function (d) { if (S.ticket && S.ticket.id === id) { S.ticket = d; render(); loadInbox(); } })
    .catch(function (e) { if (S.ticket && S.ticket.id === id) { S.ticket = { id: id, error: e.message }; render(); } });
}
function loadCustomers() {
  api("/api/office/customers").then(function (d) { S.customers = d; render(); })
    .catch(function (e) { if (e.message !== "401") { S.error = e.message; render(); } });
}
function loadKits() {
  api("/api/office/kits").then(function (d) { S.kits = d; render(); })
    .catch(function (e) { if (e.message !== "401") { S.error = e.message; render(); } });
}
function loadKit(id) {
  api("/api/office/kits/" + id).then(function (d) { if (S.kit && S.kit.id === id) { S.kit = d; render(); } })
    .catch(function (e) { if (S.kit && S.kit.id === id) { S.kit = { id: id, error: e.message }; render(); } });
}
function loadCustomer(id) {
  api("/api/office/customers/" + id).then(function (d) { if (S.customer && S.customer.id === id) { S.customer = d; render(); } })
    .catch(function (e) { if (S.customer && S.customer.id === id) { S.customer = { id: id, error: e.message }; render(); } });
}

// ---------------------------------------------------------------- page frame
function header() {
  var sm = S.summary || { inbox: {}, customers: {} }, ib = sm.inbox || {}, cu = sm.customers || {};
  var waiting = (ib["new"] || 0);
  var counts = [];
  if (ib["new"]) counts.push(st("caution", ib["new"] + " new"));
  if (ib.open) counts.push(st("advisory", ib.open + " open"));
  if (ib.waiting) counts.push(st("offline", ib.waiting + " waiting"));
  var who = S.me.user ? (S.me.user.name || S.me.user.email) : "";
  function nav(v, label, n) {
    return v === S.view ? '<a aria-current="page" href="#/' + v + '">' + label + (n ? '<span class="of-count">' + n + "</span>" : "") + "</a>"
      : '<a href="#/' + v + '">' + label + (n ? '<span class="of-count">' + n + "</span>" : "") + "</a>";
  }
  return '<header class="top"><div class="top-row">' + LOGO + '<div class="ident"><b>Fullscope Office</b><span>' + esc(who) + "</span></div>" +
    '<div class="top-fields"><div class="tf"><span>Customers</span><b>' + ((cu.active || 0) + " active · " + (cu.lead || 0) + (cu.lead === 1 ? " lead" : " leads")) + "</b></div>" +
    '<div class="tf alerts"><span>Inbox</span><div class="row">' + (counts.join("") || st("ok", "All answered")) + "</div></div></div></div>" +
    '<nav class="nav" aria-label="Sections">' + nav("inbox", "Inbox", waiting) + nav("customers", "Customers") + nav("devices", "Devices") + nav("flash", "Flash") +
    '<span class="spacer"></span><div class="tools"><a class="btn" href="/app/" style="text-decoration:none">Open the app</a>' +
    '<button class="btn" type="button" data-signout>Sign out</button></div></nav></header>' +
    (S.error ? '<div class="stale-banner">Can\'t reach the server: ' + esc(S.error) + "</div>" : "");
}

function render() {
  if (!S.me || !S.me.staff || !S.view) return;
  var y = window.scrollY, act = document.activeElement, focusId = act && act.id && root.contains(act) ? act.id : null;
  var sel = focusId && [act.selectionStart, act.selectionEnd];
  // what is being typed survives the re-render, unless another page or item opened or it was just saved
  var key = S.view + ":" + (S.ticket && S.ticket.id) + ":" + (S.customer && S.customer.id) + ":" + (S.kit && S.kit.id), keep = {};
  if (key === S.formKey && !S.reset) $$("input[id^='f-'], textarea[id^='f-']").forEach(function (i) { keep[i.id] = i.value; });
  S.formKey = key; S.reset = false;
  root.innerHTML = header() + '<main class="page">' + (S.view === "inbox" ? inboxPage() : S.view === "customers" ? customersPage() : S.view === "flash" ? flashPage() : devicesPage()) + "</main>";
  Object.keys(keep).forEach(function (k) { var i = document.getElementById(k); if (i) i.value = keep[k]; });
  var f = focusId && document.getElementById(focusId);
  if (f) { f.focus(); try { f.setSelectionRange(sel[0], sel[1]); } catch (e) {} }
  window.scrollTo(0, y);
  bind();
}

// ---------------------------------------------------------------- inbox
function seg(name, opts, cur) {
  return '<div class="seg" role="group">' + opts.map(function (o) {
    return '<button type="button" data-' + name + '="' + o[0] + '" aria-pressed="' + (o[0] === cur) + '">' + o[1] + "</button>";
  }).join("") + "</div>";
}
function inboxPage() {
  var list = S.tickets, cur = S.ticket && S.ticket.id;
  var rows = !list ? '<tr><td colspan="3" class="faint">Loading…</td></tr>'
    : !list.length ? '<tr><td colspan="3" class="faint">' + (S.status === "open" ? "Nothing waiting. New order requests, contact messages and support requests show up here." : "No tickets.") + "</td></tr>"
    : list.map(function (t) {
      var s = STATUS[t.status] || ["offline", t.status];
      return '<tr data-ticket="' + t.id + '"' + (t.id === cur ? ' aria-current="true"' : "") + "><td>" + st(s[0], s[1]) + '<span class="who">' + esc(KIND[t.kind] || t.kind) + " #" + t.id + "</span></td>" +
        '<td><span class="subj">' + esc(t.subject) + '</span><span class="who">' + esc(t.name || t.email) + (t.company && t.company !== t.name ? " · " + esc(t.company) : "") +
        (t.customer ? ' · <span class="faint">customer</span>' : "") + "</span></td>" +
        '<td class="num"><span class="who" style="white-space:nowrap">' + esc(when(t.updated_at)) + "</span>" + (t.messages ? '<span class="who">' + t.messages + (t.messages === 1 ? " message" : " messages") + "</span>" : "") + "</td></tr>";
    }).join("");
  return '<div class="of-split' + (cur ? " has-sel" : "") + '"><section class="panel" aria-label="Inbox"><div class="ph"><h2>Inbox</h2><span class="sub">Orders, contact messages and support requests · newest activity first</span>' +
    '<div class="right">' + seg("status", [["open", "Open"], ["closed", "Closed"], ["all", "All"]], S.status) +
    seg("kind", [["all", "All"], ["order", "Orders"], ["contact", "Contact"], ["support", "Support"]], S.kind) + "</div></div>" +
    '<table class="dt of-list"><thead><tr><th style="width:1%">Status</th><th>Subject</th><th class="num">Last activity</th></tr></thead><tbody>' + rows + "</tbody></table></section>" +
    ticketPanel() + "</div>";
}
function ticketPanel() {
  var t = S.ticket;
  if (!t) return '<section class="panel"><div class="empty">Pick a ticket on the left to read it and reply.</div></section>';
  if (t.loading) return '<section class="panel"><div class="empty">Loading…</div></section>';
  if (t.error) return '<section class="panel"><div class="empty">' + esc(t.error) + "</div></section>";
  var s = STATUS[t.status] || ["offline", t.status];
  var meta = [["From", esc(t.name || "—")], ["Email", '<a href="mailto:' + esc(t.email) + '">' + esc(t.email) + "</a>"]];
  if (t.phone) meta.push(["Phone", '<a href="tel:' + esc(t.phone) + '">' + esc(t.phone) + "</a>"]);
  if (t.company) meta.push(["Company", esc(t.company)]);
  if (t.order) meta.push(["Systems ordered", '<span class="n">' + t.order.systems + "</span>"]);
  if (t.account) meta.push(["Account", esc(t.account.name)]);
  if (t.system) meta.push(["System", esc(t.system.name) + ' <span class="faint mono">' + esc(t.system.site_id) + "</span>"]);
  meta.push(["Received", esc(when(t.created_at))]);
  var cust = t.customer ? '<a href="#/customers/' + t.customer.id + '">' + esc(t.customer.company) + "</a>"
    : '<span class="faint">Not linked</span> <button class="btn" type="button" data-make-customer>Make customer</button>' + linkPicker();
  meta.push(["Customer", cust]);
  var thread = '<div class="of-msg"><div class="mh"><b>' + esc(t.name || t.email) + "</b><span>" + esc(when(t.created_at)) + '</span></div><div class="mb">' + (t.body ? esc(t.body) : '<span class="faint">No message.</span>') + "</div></div>";
  t.thread.forEach(function (m) {
    var note = m.kind === "note";
    thread += '<div class="of-msg' + (note ? " note" : "") + '"><div class="mh"><b>' + esc(m.author || "Fullscope") + "</b><span>" + (note ? "Internal note" : m.emailed ? "Reply, emailed" : "Reply, not emailed (email is off)") +
      " · " + esc(when(m.at)) + '</span></div><div class="mb">' + esc(m.body) + "</div></div>";
  });
  return '<section class="panel" aria-label="Ticket"><div class="ph"><h2>' + esc(t.subject) + '</h2><span class="sub">' + esc(KIND[t.kind] || t.kind) + " #" + t.id + "</span>" +
    '<div class="right">' + st(s[0], s[1]) + '<select class="sel" id="f-status" aria-label="Status">' +
    Object.keys(STATUS).map(function (k) { return '<option value="' + k + '"' + (k === t.status ? " selected" : "") + ">" + STATUS[k][1] + "</option>"; }).join("") + "</select></div></div>" +
    '<div class="of-body"><div class="of-meta">' + meta.map(function (x) { return "<div><span>" + x[0] + "</span>" + x[1] + "</div>"; }).join("") + "</div>" +
    thread +
    '<form class="of-form" id="compose"><label for="f-draft">Reply to ' + esc(t.email) + '</label><textarea id="f-draft" maxlength="4000" placeholder="Write a reply, or a note only Fullscope staff can see"></textarea>' +
    '<div class="acts"><button class="btn primary" type="submit" data-kind="reply">Send reply by email</button><button class="btn" type="submit" data-kind="note">Add internal note</button>' +
    (S.msg ? '<span class="msg ' + S.msg[0] + '">' + esc(S.msg[1]) + "</span>" : "") + "</div></form></div></section>";
}
function linkPicker() {
  var cs = S.customers || [];
  if (!cs.length) return "";
  return ' <select class="sel" id="f-link" aria-label="Link to a customer"><option value="">or link to…</option>' +
    cs.map(function (c) { return '<option value="' + c.id + '">' + esc(c.company) + "</option>"; }).join("") + "</select>";
}

// ---------------------------------------------------------------- customers
function customersPage() {
  var list = S.customers, cur = S.customer && S.customer.id;
  var rows = !list ? '<tr><td colspan="5" class="faint">Loading…</td></tr>'
    : !list.length ? '<tr><td colspan="5" class="faint">No customers yet. Make one from an order request in the inbox, or add one here.</td></tr>'
    : list.map(function (c) {
      var s = CSTATUS[c.status] || ["offline", c.status];
      return '<tr data-customer="' + c.id + '"' + (c.id === cur ? ' aria-current="true"' : "") + '><td><span class="subj">' + esc(c.company) + '</span><span class="who">' + esc(c.contact || c.email || "") + "</span></td>" +
        "<td>" + st(s[0], s[1]) + '</td><td class="num"><span class="n">' + c.systems + '</span></td><td class="num"><span class="n">' + c.open_tickets + "</span></td>" +
        "<td>" + (c.account_id ? "Yes" : '<span class="faint">No</span>') + "</td></tr>";
    }).join("");
  return '<div class="of-split' + (cur ? " has-sel" : "") + '"><section class="panel" aria-label="Customers"><div class="ph"><h2>Customers</h2><span class="sub">Contractor companies</span>' +
    '<div class="right"><a class="btn" href="#/customers/new" style="text-decoration:none">New customer</a></div></div>' +
    '<table class="dt of-list"><thead><tr><th>Company</th><th>Status</th><th class="num">Systems</th><th class="num">Open tickets</th><th>Account</th></tr></thead><tbody>' + rows + "</tbody></table></section>" +
    customerPanel() + "</div>";
}
function field(id, label, value, type) {
  return '<label for="f-' + id + '">' + label + '<input id="f-' + id + '" type="' + (type || "text") + '" value="' + esc(value || "") + '"></label>';
}
function customerForm(c) {
  var isNew = c.id === "new";
  return '<form class="of-form" id="cust-form">' + field("company", "Company", c.company) +
    '<div class="two">' + field("contact", "Contact", c.contact) + field("phone", "Phone", c.phone, "tel") + "</div>" +
    '<div class="two">' + field("email", "Email", c.email, "email") +
    '<label for="f-cstatus">Status<select id="f-cstatus">' + Object.keys(CSTATUS).map(function (k) { return '<option value="' + k + '"' + (k === (c.status || "lead") ? " selected" : "") + ">" + CSTATUS[k][1] + "</option>"; }).join("") + "</select></label></div>" +
    field("address", "Address", c.address) +
    '<label for="f-notes">Notes<textarea id="f-notes" maxlength="4000">' + esc(c.notes || "") + "</textarea></label>" +
    '<div class="acts"><button class="btn primary" type="submit">' + (isNew ? "Add customer" : "Save") + "</button>" +
    (S.msg && S.msg[2] === "cust" ? '<span class="msg ' + S.msg[0] + '">' + esc(S.msg[1]) + "</span>" : "") + "</div></form>";
}
function customerPanel() {
  var c = S.customer;
  if (!c) return '<section class="panel"><div class="empty">Pick a customer to see their account, systems and tickets.</div></section>';
  if (c.id === "new") return '<section class="panel"><div class="ph"><h2>New customer</h2></div><div class="of-body">' + customerForm(c) + "</div></section>";
  if (c.loading) return '<section class="panel"><div class="empty">Loading…</div></section>';
  if (c.error) return '<section class="panel"><div class="empty">' + esc(c.error) + "</div></section>";
  var s = CSTATUS[c.status] || ["offline", c.status];
  var acct;
  if (!c.account) {
    acct = '<form class="of-form" id="acct-form"><p class="muted" style="margin:0">No Fullscope account yet. Creating one emails the contractor an invite to sign in as the account\'s admin.</p>' +
      '<div class="acts"><input id="f-acct-email" type="email" placeholder="Admin\'s email" value="' + esc(c.email || "") + '" style="max-width:280px">' +
      '<button class="btn primary" type="submit">Create account and invite</button>' + (S.msg && S.msg[2] === "acct" ? '<span class="msg ' + S.msg[0] + '">' + esc(S.msg[1]) + "</span>" : "") + "</div></form>";
  } else {
    var people = c.people.map(function (p) { return "<tr><td>" + esc(p.name || p.email) + ' <span class="who">' + esc(p.email) + "</span></td><td>" + (p.admin ? "Admin" : "Technician") + "</td><td>" + (p.last_login ? esc(when(p.last_login)) : '<span class="faint">Never</span>') + "</td></tr>"; })
      .concat(c.invites.map(function (i) { return "<tr><td>" + esc(i.email) + "</td><td>" + (i.admin ? "Admin" : "Technician") + '</td><td class="faint">' + (i.expired ? "Invite expired" : "Invited") + "</td></tr>"; })).join("");
    var systems = c.systems_list.map(function (x) {
      return "<tr><td>" + esc(x.name) + ' <span class="faint mono">' + esc(x.site_id) + '</span></td><td class="num"><span class="n">' + x.nodes_online + "/" + Math.max(2, x.nodes) + '</span></td><td class="num"><span class="n">' + x.homeowners +
        "</span></td><td>" + (x.last_reading ? esc(when(x.last_reading)) : '<span class="faint">Never</span>') + "</td></tr>";
    }).join("");
    acct = '<div class="of-meta"><div><span>Account</span>' + esc(c.account.name) + "</div><div><span>Account email</span>" + esc(c.account.email) + "</div><div><span>Since</span>" + esc(when(c.account.created_at)) + "</div></div>" +
      '<table class="dt"><thead><tr><th>People</th><th>Access</th><th>Last sign-in</th></tr></thead><tbody>' + (people || '<tr><td colspan="3" class="faint">Nobody yet</td></tr>') + "</tbody></table>" +
      '<table class="dt"><thead><tr><th>Systems</th><th class="num">Nodes online</th><th class="num">Homeowners</th><th>Last reading</th></tr></thead><tbody>' + (systems || '<tr><td colspan="4" class="faint">No systems yet</td></tr>') + "</tbody></table>";
  }
  var tickets = c.tickets.map(function (t) {
    var ts = STATUS[t.status] || ["offline", t.status];
    return '<tr data-open-ticket="' + t.id + '" style="cursor:pointer"><td>' + st(ts[0], ts[1]) + "</td><td>" + esc(t.subject) + '<span class="who">' + esc(KIND[t.kind] || t.kind) + " #" + t.id + "</span></td><td>" + esc(when(t.updated_at)) + "</td></tr>";
  }).join("");
  return '<section class="panel" aria-label="Customer"><div class="ph"><h2>' + esc(c.company) + "</h2>" + st(s[0], s[1]) + "</div>" +
    '<div class="of-body">' + customerForm(c) + "</div>" +
    '<div class="sh3">Fullscope account</div><div class="of-body">' + acct + "</div>" +
    '<div class="sh3">Tickets</div><table class="dt of-list"><tbody>' + (tickets || '<tr><td class="faint">None</td></tr>') + "</tbody></table></section>";
}

// ---------------------------------------------------------------- devices: kits, boards, QR labels, warranty
function kitMatches(k, q) {
  if (!q) return true;
  q = q.toLowerCase();
  var hex = q.replace(/[\s:.-]/g, "");
  return k.code.toLowerCase().indexOf(q) >= 0 || (k.customer && k.customer.company.toLowerCase().indexOf(q) >= 0) ||
    (k.system && k.system.name.toLowerCase().indexOf(q) >= 0) ||
    (hex.length >= 2 && k.boards.some(function (b) { return b.serial.replace(/:/g, "").toLowerCase().indexOf(hex) >= 0; }));
}
function devicesPage() {
  var list = S.kits, cur = S.kit && S.kit.id;
  var shown = (list || []).filter(function (k) { return kitMatches(k, S.find); });
  var rows = !list ? '<tr><td colspan="4" class="faint">Loading…</td></tr>'
    : !list.length ? '<tr><td colspan="4" class="faint">No kits yet. Add one after flashing a pair of boards.</td></tr>'
    : !shown.length ? '<tr><td colspan="4" class="faint">No kit matches.</td></tr>'
    : shown.map(function (k) {
      var s = KSTATUS[k.status] || ["offline", k.status];
      return '<tr data-kit="' + k.id + '"' + (k.id === cur ? ' aria-current="true"' : "") + '><td><span class="subj mono">' + esc(k.code) + '</span><span class="who mono">' +
        k.boards.map(function (b) { return esc(b.serial); }).join(" · ") + "</span></td><td>" + st(s[0], s[1]) + "</td>" +
        "<td>" + (k.customer ? esc(k.customer.company) : '<span class="faint">—</span>') + "</td>" +
        "<td>" + (k.system ? esc(k.system.name) + '<span class="who">' + esc(k.system.address || "") + "</span>" : '<span class="faint">—</span>') + "</td></tr>";
    }).join("");
  return '<div class="of-split' + (cur ? " has-sel" : "") + '"><section class="panel" aria-label="Kits"><div class="ph"><h2>Kits</h2><span class="sub">One QR label per kit</span>' +
    '<div class="right"><input class="of-find" id="f-find" type="search" placeholder="Code, serial, customer" value="' + esc(S.find) + '" aria-label="Find a kit">' +
    '<a class="btn" href="#/devices/new" style="text-decoration:none">New kit</a></div></div>' +
    '<table class="dt of-list"><thead><tr><th>Kit</th><th>Status</th><th>Customer</th><th>Installed at</th></tr></thead><tbody>' + rows + "</tbody></table></section>" +
    kitPanel() + "</div>";
}
function kitForm() {
  function board(kind) {
    return '<div class="two">' + field(kind + "-serial", KINDNAME[kind] + " board serial (MAC)", "") + field(kind + "-fw", KINDNAME[kind] + " firmware", "") + "</div>";
  }
  return '<section class="panel"><div class="ph"><h2>New kit</h2><span class="sub">Register a flashed pair of boards and print its label</span></div><div class="of-body">' +
    '<form class="of-form" id="kit-form"><div class="two">' + field("code", "Kit code (leave empty to make one up)", "") + field("hwrev", "Hardware revision", "B") + "</div>" +
    board("outdoor") + board("indoor") +
    '<label for="f-knotes">Notes<textarea id="f-knotes" maxlength="1000"></textarea></label>' +
    '<p class="muted" style="margin:0;font-size:12px">Until firmware 0.4.0, flash both boards with SITE_ID set to the kit code in lower case (config.h). The serial is the MAC the board prints at boot.</p>' +
    '<div class="acts"><button class="btn primary" type="submit">Add kit</button>' + (S.msg && S.msg[2] === "kit" ? '<span class="msg ' + S.msg[0] + '">' + esc(S.msg[1]) + "</span>" : "") + "</div></form></div></section>";
}
function qrSvg(text) {
  try { var q = qrcode(0, "M"); q.addData(text); q.make(); return q.createSvgTag({ cellSize: 4, margin: 0, scalable: true }); } catch (e) { return ""; }
}
function kitLabel(k) {
  var claim = k.claim_url.split("#/claim/")[1] || "";
  return '<div class="of-label" id="label"><div class="lq">' + qrSvg(k.claim_url) + '</div><div class="lt"><i>FULLSCOPE</i>' +
    '<b class="mono">' + esc(k.code) + "</b><span>Contractor: scan to add this system to your account.</span>" +
    '<span class="mono small">' + k.boards.map(function (b) { return esc((KINDNAME[b.kind] || b.kind).charAt(0)) + " " + esc(b.serial); }).join("<br>") + "</span>" +
    '<span class="mono small">Code ' + esc(claim) + "</span></div></div>";
}
function msgFor(tag) { return S.msg && S.msg[2] === tag ? '<span class="msg ' + S.msg[0] + '">' + esc(S.msg[1]) + "</span>" : ""; }
function kitPanel() {
  var k = S.kit;
  if (!k) return '<section class="panel"><div class="empty">Pick a kit to print its label, ship it or open a warranty claim.</div></section>';
  if (k.id === "new") return kitForm();
  if (k.loading) return '<section class="panel"><div class="empty">Loading…</div></section>';
  if (k.error) return '<section class="panel"><div class="empty">' + esc(k.error) + "</div></section>";
  var s = KSTATUS[k.status] || ["offline", k.status], claimed = !!k.claimed_at;
  var custSel = '<select class="sel" id="f-kcust" aria-label="Customer"' + (claimed ? " disabled" : "") + '><option value="0">No customer</option>' + (S.customers || []).map(function (c) {
    return '<option value="' + c.id + '"' + (k.customer && k.customer.id === c.id ? " selected" : "") + ">" + esc(c.company) + "</option>";
  }).join("") + "</select>";
  var meta = [["Customer", claimed ? (k.customer ? '<a href="#/customers/' + k.customer.id + '">' + esc(k.customer.company) + "</a>" : "—") : custSel],
    ["Shipped", k.shipped_at ? esc(when(k.shipped_at)) : '<span class="faint">Not yet</span>'],
    ["Reports as", '<span class="mono">' + esc(k.site_id) + "</span>"],
    ["Hardware", esc(k.hw_rev || "—")]];
  if (claimed) {
    meta.push(["Set up", esc(when(k.claimed_at)) + (k.claimed_by ? " by " + esc(k.claimed_by) : "")]);
    meta.push(["System", k.system ? esc(k.system.name) + (k.system.address ? '<span class="who">' + esc(k.system.address) + "</span>" : "") : "—"]);
  }
  var boards = k.boards.map(function (b) {
    var bs = BSTATUS[b.status] || ["offline", b.status], w = b.warranty;
    var warranty = !w.start ? '<span class="faint">Starts at setup</span>' : (w.active ? st("ok", "Until " + w.ends) : st("offline", "Ended " + w.ends));
    var seen = b.seen ? esc(when(b.seen.last_seen)) + '<span class="who">fw ' + esc(b.seen.fw || "?") + (b.seen.rssi !== null && b.seen.rssi !== undefined ? " · " + b.seen.rssi + " dBm" : "") + "</span>" : '<span class="faint">Never</span>';
    return "<tr><td>" + esc(KINDNAME[b.kind] || b.kind) + '<span class="who mono">' + esc(b.serial) + "</span></td><td>" + st(bs[0], bs[1]) + "</td><td>" + warranty + "</td><td>" + seen + "</td>" +
      '<td class="num">' + (b.status === "rma" ? "" : '<button class="btn" type="button" data-claim-board="' + b.id + '">Warranty claim</button>') + "</td></tr>";
  }).join("");
  var opening = S.claimBoard && k.boards.some(function (b) { return b.id === S.claimBoard; });
  var openForm = opening ? '<form class="of-form" id="wc-form"><label for="f-problem">What\'s wrong with the ' + esc(KINDNAME[k.boards.filter(function (b) { return b.id === S.claimBoard; })[0].kind].toLowerCase()) + " board?" +
    '<textarea id="f-problem" maxlength="2000" placeholder="For example: suction transducer reads open circuit"></textarea></label>' +
    '<div class="acts"><button class="btn primary" type="submit">Open claim</button><button class="btn" type="button" data-cancel-claim>Cancel</button>' + msgFor("wc") + "</div></form>" : "";
  var claims = k.claims.map(function (c) {
    var cs = WSTATUS[c.status] || ["offline", c.status];
    var settle = c.status !== "open" ? "" : '<form class="of-form" data-settle="' + c.id + '"><div class="two"><label>Outcome<select class="sel" name="status">' +
      '<option value="replaced">Replaced the board</option><option value="repaired">Repaired it</option><option value="denied">Denied</option></select></label>' +
      '<label>Replacement serial<input name="serial" placeholder="Only when replaced"></label></div>' +
      '<label>Resolution<input name="resolution" maxlength="2000"></label><div class="acts"><button class="btn" type="submit">Close claim</button>' + msgFor("settle" + c.id) + "</div></form>";
    return '<div class="of-msg"><div class="mh">' + st(cs[0], cs[1]) + "<b>" + esc(KINDNAME[c.board.kind] || c.board.kind) + ' <span class="mono">' + esc(c.board.serial) + "</span></b><span>" + esc(when(c.opened_at)) + "</span></div>" +
      '<div class="mb">' + esc(c.problem) + (c.resolution ? '\n<span class="faint">' + esc(c.resolution) + "</span>" : "") +
      (c.replacement ? '\n<span class="faint">Replaced by <span class="mono">' + esc(c.replacement.serial) + "</span></span>" : "") + "</div>" +
      (settle ? '<div class="mb">' + settle + "</div>" : "") + "</div>";
  }).join("");
  return '<section class="panel" aria-label="Kit"><div class="ph"><h2 class="mono">' + esc(k.code) + "</h2>" + st(s[0], s[1]) +
    '<div class="right">' + (claimed ? '<button class="btn" type="button" data-release>Release</button>'
      : '<button class="btn" type="button" data-ship="' + (k.shipped_at ? "0" : "1") + '">' + (k.shipped_at ? "Mark not shipped" : "Mark shipped") + "</button>") + "</div></div>" +
    '<div class="of-body">' + (claimed ? "" : kitLabel(k) + '<div class="acts" style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" type="button" data-print>Print label</button>' +
      '<button class="btn" type="button" data-new-code>New QR code</button><span class="faint" style="font-size:12px;align-self:center">A new code stops the old label working.</span></div>') +
    '<div class="of-meta">' + meta.map(function (x) { return "<div><span>" + x[0] + "</span>" + x[1] + "</div>"; }).join("") + "</div>" + msgFor("kit") + "</div>" +
    '<div class="sh3">Boards</div><table class="dt"><thead><tr><th>Board</th><th>Status</th><th>Warranty</th><th>Last heard</th><th></th></tr></thead><tbody>' + boards + "</tbody></table>" +
    (openForm ? '<div class="of-body">' + openForm + "</div>" : "") +
    '<div class="sh3">Warranty claims</div><div class="of-body">' + (claims || '<span class="faint">None</span>') + "</div>" +
    '<div class="sh3">Notes</div><div class="of-body"><form class="of-form" id="kn-form"><textarea id="f-kitnotes" maxlength="1000">' + esc(k.notes || "") + "</textarea>" +
    '<div class="acts"><button class="btn" type="submit">Save notes</button>' + msgFor("notes") + "</div></form></div></section>";
}
function kitAction(path, method, body, tag, done) {
  var id = S.kit.id;
  return api("/api/office/kits/" + id + path, { method: method, body: body }).then(function (d) {
    if (S.kit && S.kit.id === id) S.kit = d;
    S.msg = done ? ["ok", done, tag] : null; S.reset = true; loadKits(); render();
  }).catch(function (e) { S.msg = ["err", e.message, tag]; render(); });
}

// ---------------------------------------------------------------- flash: firmware + kit code onto a pair of boards
// Over USB with Web Serial (Chrome or Edge on a computer). esptool-js writes the factory image (one file at
// 0x0, made by the firmware build: <env>-<version>-factory.bin) and reads the board's MAC, which is its
// serial here. Then the page talks to the running firmware on the same port: {"cmd":"identity"} and
// {"cmd":"provision"} (firmware 0.4.0+) save the kit code as the board's site and the broker it reports to.
var ESPTOOL = "vendor/esptool-js-0.5.7.js";
var ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ";
var F = { code: null, hw: "B", erase: true, files: {}, boards: { outdoor: {}, indoor: {} }, log: [], busy: null };
function load(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
function newKitCode() {
  var taken = (S.kits || []).map(function (k) { return k.code; }), code;
  do {
    code = "FS-";
    var r = new Uint32Array(5); crypto.getRandomValues(r);
    for (var i = 0; i < 5; i++) code += ALPHABET[r[i] % ALPHABET.length];
  } while (taken.indexOf(code) >= 0);
  return code;
}
function flog(line) {
  F.log.push(line);
  if (F.log.length > 400) F.log.splice(0, F.log.length - 400);
  var el = $("#flog");
  if (el) { el.textContent = F.log.join("\n"); el.scrollTop = el.scrollHeight; }
}
function flashPage() {
  if (!F.code) F.code = newKitCode();
  var serial = !!navigator.serial;
  function fileRow(kind) {
    var f = F.files[kind];
    return '<label for="f-file-' + kind + '">' + KINDNAME[kind] + ' firmware (factory .bin)<input id="f-file-' + kind + '" type="file" accept=".bin">' +
      '<span class="faint" style="font-size:11.5px">' + (f ? esc(f.name) + " · " + Math.round(f.size / 1024) + " kB" : "From the firmware build: .pio/build/" + kind + "/" + kind + "-&lt;version&gt;-factory.bin") + "</span></label>";
  }
  function boardRow(kind) {
    var b = F.boards[kind], state = b.error ? st("fault", "Failed") : b.done ? st("ok", "Ready") : b.step ? st("advisory", b.step) : st("offline", "Not done");
    return "<tr><td>" + KINDNAME[kind] + "</td><td>" + state + (b.error ? '<span class="who">' + esc(b.error) + "</span>" : "") +
      (b.progress !== undefined && !b.done && !b.error ? '<div class="of-bar"><i id="bar-' + kind + '" style="width:' + b.progress + '%"></i></div>' : "") + "</td>" +
      '<td class="mono">' + esc(b.mac || "—") + (b.fw ? '<span class="who">fw ' + esc(b.fw) + "</span>" : "") + "</td>" +
      '<td class="num"><button class="btn primary" type="button" data-flash="' + kind + '"' + (F.busy || !serial ? " disabled" : "") + ">Flash</button> " +
      '<button class="btn" type="button" data-provision="' + kind + '"' + (F.busy || !serial ? " disabled" : "") + ' title="Board already has firmware 0.4.0 or newer">Only save the kit code</button></td></tr>';
  }
  var ready = ["outdoor", "indoor"].filter(function (k) { return F.boards[k].done; });
  return '<div class="of-split"><section class="panel" aria-label="Flash a kit"><div class="ph"><h2>Flash a kit</h2><span class="sub">Firmware and kit code onto an outdoor and an indoor board over USB</span></div>' +
    '<div class="of-body">' + (serial ? "" : '<div class="stale-banner" style="margin:0">Flashing needs Chrome or Edge on a computer (Web Serial). Everything else in Office works here.</div>') +
    '<form class="of-form" id="flash-form" onsubmit="return false"><div class="two">' + field("fcode", "Kit code (goes on both boards)", F.code) + field("fhw", "Hardware revision", F.hw) + "</div>" +
    '<div class="two">' + fileRow("outdoor") + fileRow("indoor") + "</div>" +
    '<div class="two">' + field("mhost", "Broker the boards report to", load("fs_flash_host", "")) + field("mport", "Port (8883 needs a build with MQTT_TLS 1)", load("fs_flash_port", "1883"), "number") + "</div>" +
    '<div class="two">' + field("muser", "Broker user (empty if none)", load("fs_flash_user", "")) + field("mpass", "Broker password", "", "password") + "</div>" +
    '<label style="flex-direction:row;align-items:center;gap:8px"><input id="f-erase" type="checkbox" style="width:auto"' + (F.erase ? " checked" : "") + '> Erase the whole board first (new boards; clears saved calibration and WiFi)</label></form>' +
    '<p class="muted" style="margin:0;font-size:12px">Unplug 24 VAC from a board before connecting USB. Plug in one board, press Flash and pick its port. If it won\'t connect, hold BOOT while it starts.</p></div>' +
    '<table class="dt"><thead><tr><th>Board</th><th>Status</th><th>Serial (MAC)</th><th></th></tr></thead><tbody>' + boardRow("outdoor") + boardRow("indoor") + "</tbody></table>" +
    '<div class="of-body"><div class="acts" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="btn primary" type="button" data-add-flashed' + (ready.length && !F.busy ? "" : " disabled") + ">Add kit and print label</button>" +
    '<button class="btn" type="button" data-new-flash' + (F.busy ? " disabled" : "") + ">Start another kit</button>" + msgFor("flash") + "</div></div></section>" +
    '<section class="panel" aria-label="Log"><div class="ph"><h2>Log</h2><span class="sub">What the boards said</span></div><pre class="of-log" id="flog">' + esc(F.log.join("\n") || "Nothing yet.") + "</pre></section></div>";
}
function flashSettings() {
  var v = function (id) { var e = $("#f-" + id); return e ? e.value.trim() : ""; };
  var code = v("fcode").toUpperCase(), host = v("mhost"), port = +v("mport") || 1883, user = v("muser");
  if (!/^FS-[A-Z0-9]{3,10}$/.test(code)) throw new Error("A kit code looks like FS-7K3QX.");
  if (!host) throw new Error("Enter the broker the boards report to.");
  try { localStorage.setItem("fs_flash_host", host); localStorage.setItem("fs_flash_port", String(port)); localStorage.setItem("fs_flash_user", user); } catch (e) {}
  F.code = code; F.hw = v("fhw");
  return { site: code.toLowerCase(), mqtt_host: host, mqtt_port: port, mqtt_user: user, mqtt_pass: v("mpass") };
}
// Lines from the running firmware on an open port; send(obj) writes one JSON command.
function serialLink(port) {
  var reader = port.readable.getReader(), writer = port.writable.getWriter(), dec = new TextDecoder(), buf = "", waiters = [], closed = false;
  (function pump() {
    reader.read().then(function (r) {
      if (r.done) return;
      buf += dec.decode(r.value, { stream: true });
      var i;
      while ((i = buf.indexOf("\n")) >= 0) {
        var line = buf.slice(0, i).replace(/\r$/, ""); buf = buf.slice(i + 1);
        if (line.indexOf("[reply] ") === 0) {
          var rep; try { rep = JSON.parse(line.slice(8)); } catch (e) { rep = null; }
          waiters = waiters.filter(function (w) { if (rep && rep.cmd === w.cmd) { w.resolve(rep); return false; } return true; });
        }
        if (line && line.charAt(0) !== "{") flog(line);         // skip the readings, keep the boot messages
      }
      pump();
    }).catch(function () {});
  })();
  return {
    ask: function (cmd, ms) {
      return new Promise(function (resolve, reject) {
        var w = { cmd: cmd.cmd, resolve: resolve };
        waiters.push(w);
        writer.write(new TextEncoder().encode(JSON.stringify(cmd) + "\n")).catch(reject);
        setTimeout(function () { waiters = waiters.filter(function (x) { return x !== w; }); reject(new Error("timeout")); }, ms || 2000);
      });
    },
    close: function () {
      if (closed) return Promise.resolve(); closed = true;
      return reader.cancel().catch(function () {}).then(function () { reader.releaseLock(); writer.releaseLock(); return port.close(); }).catch(function () {});
    }
  };
}
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
// Ask until the firmware answers: right after a flash or reset it is still booting.
function askUntil(link, cmd, ms) {
  var end = Date.now() + ms;
  function attempt() {
    return link.ask(cmd, 1500).catch(function (e) { if (Date.now() > end) throw new Error("The board didn't answer. Is it running firmware 0.4.0 or newer?"); return sleep(500).then(attempt); });
  }
  return attempt();
}
function setStep(kind, step, progress) {
  var b = F.boards[kind]; b.step = step; b.progress = progress;
  var bar = $("#bar-" + kind);
  if (bar && progress !== undefined && step === b.shown) { bar.style.width = progress + "%"; return; }
  b.shown = step; render();
}
function readFile(file) {
  return new Promise(function (resolve, reject) {
    var r = new FileReader();
    r.onload = function () { resolve(new Uint8Array(r.result)); };
    r.onerror = function () { reject(new Error("Couldn't read " + file.name)); };
    r.readAsArrayBuffer(file);
  });
}
function runBoard(kind, flash) {
  var b = F.boards[kind], prov, port, link;
  S.msg = null;
  try { prov = flashSettings(); } catch (e) { S.msg = ["err", e.message, "flash"]; return render(); }
  var file = F.files[kind];
  if (flash && !file) { S.msg = ["err", "Pick the " + kind + " firmware file first.", "flash"]; return render(); }
  F.boards[kind] = b = {};
  F.busy = kind;
  flog("--- " + KINDNAME[kind] + " board, kit " + F.code + " ---");
  navigator.serial.requestPort().then(function (p) {
    port = p;
    if (!flash) return null;
    return readFile(file).then(function (img) {
      if (img.length < 0x10000 || img[0x1000] !== 0xE9) throw new Error(file.name + " isn't a factory image (it should start with the bootloader). Use <env>-<version>-factory.bin.");
      setStep(kind, "Connecting", 0);
      return import("./" + ESPTOOL).then(function (esp) {
        var transport = new esp.Transport(port, false);
        var term = { clean: function () {}, writeLine: function (t) { flog(t); }, write: function (t) { flog(t); } };
        var loader = new esp.ESPLoader({ transport: transport, baudrate: 460800, romBaudrate: 115200, terminal: term });
        var bin = "", i;
        for (i = 0; i < img.length; i += 0x8000) bin += String.fromCharCode.apply(null, img.subarray(i, i + 0x8000));
        return loader.main().then(function (chip) {
          flog("Chip: " + chip);
          return loader.chip.readMac(loader);
        }).then(function (mac) {
          b.mac = mac.toUpperCase();
          setStep(kind, F.erase ? "Erasing" : "Writing", 0);
          return loader.writeFlash({ fileArray: [{ data: bin, address: 0 }], flashSize: "keep", flashMode: "keep", flashFreq: "keep",
            eraseAll: F.erase, compress: true, reportProgress: function (_, done, total) { setStep(kind, "Writing", Math.round(100 * done / total)); } });
        }).then(function () { return loader.after("hard_reset"); })
          .then(function () { return transport.disconnect(); });
      });
    });
  }).then(function () {
    setStep(kind, "Saving kit code");
    return port.open({ baudRate: 115200 }).then(function () {
      return port.setSignals({ dataTerminalReady: false, requestToSend: false }).catch(function () {});   // run, not bootloader
    }).then(function () {
      link = serialLink(port);
      return askUntil(link, { cmd: "identity" }, 20000);
    }).then(function (id) {
      if (id.node && id.node !== kind) throw new Error("This board runs the " + id.node + " firmware.");
      b.mac = (id.mac || b.mac || "").toUpperCase(); b.fw = id.fw;
      return link.ask(Object.assign({ cmd: "provision" }, prov), 3000);
    }).then(function (rep) {
      if (!rep.ok) throw new Error(rep.error || "The board refused the kit code.");
      flog("Saved kit " + F.code + ", broker " + prov.mqtt_host + ":" + prov.mqtt_port + ". Restarting the board.");
      return link.ask({ cmd: "reboot" }, 1500).catch(function () {});
    });
  }).then(function () {
    b.done = true; b.step = null;
  }).catch(function (e) {
    if (e && e.name === "NotFoundError") { F.boards[kind] = {}; return; }       // closed the port picker
    b.error = (e && e.message) || String(e); flog("Error: " + b.error);
  }).then(function () {
    F.busy = null;
    return (link ? link.close() : port && port.readable ? port.close() : null);
  }).catch(function () {}).then(render);
  render();
}
function addFlashedKit() {
  var boards = ["outdoor", "indoor"].filter(function (k) { return F.boards[k].done; })
    .map(function (k) { return { kind: k, serial: F.boards[k].mac, fw: F.boards[k].fw || null }; });
  api("/api/office/kits", { method: "POST", body: { code: F.code, hw_rev: F.hw || null, boards: boards } }).then(function (d) {
    F.boards = { outdoor: {}, indoor: {} }; F.code = null; F.log = [];
    S.kit = d; S.msg = ["ok", "Kit added. Print its label.", "kit"]; loadKits(); location.hash = "#/devices/" + d.id;
  }).catch(function (e) { S.msg = ["err", e.message, "flash"]; render(); });
}
function bindFlash() {
  ["outdoor", "indoor"].forEach(function (kind) {
    var inp = $("#f-file-" + kind);
    if (inp) inp.addEventListener("change", function () { if (inp.files[0]) { F.files[kind] = inp.files[0]; S.reset = false; render(); } });
  });
  var er = $("#f-erase");
  if (er) er.addEventListener("change", function () { F.erase = er.checked; });
  $$("[data-flash]").forEach(function (b) { b.addEventListener("click", function () { runBoard(b.dataset.flash, true); }); });
  $$("[data-provision]").forEach(function (b) { b.addEventListener("click", function () { runBoard(b.dataset.provision, false); }); });
  var add = $("[data-add-flashed]");
  if (add) add.addEventListener("click", function () { try { flashSettings(); } catch (e) { S.msg = ["err", e.message, "flash"]; return render(); } addFlashedKit(); });
  var nf = $("[data-new-flash]");
  if (nf) nf.addEventListener("click", function () { F.boards = { outdoor: {}, indoor: {} }; F.code = null; F.log = []; S.msg = null; S.reset = true; render(); });
}

// ---------------------------------------------------------------- actions
function bind() {
  $$("[data-signout]").forEach(function (b) {
    b.addEventListener("click", function () {
      fetch("/api/auth/logout", { method: "POST", headers: { "X-Requested-With": "fullscope" }, credentials: "same-origin" })
        .catch(function () {}).then(function () { location.href = "/app/"; });
    });
  });
  $$("[data-status]").forEach(function (b) { b.addEventListener("click", function () { S.status = b.dataset.status; S.tickets = null; loadInbox(); render(); }); });
  $$("[data-kind]").forEach(function (b) { if (b.tagName === "BUTTON" && b.type === "button") b.addEventListener("click", function () { S.kind = b.dataset.kind; S.tickets = null; loadInbox(); render(); }); });
  $$("[data-ticket]").forEach(function (tr) { tr.addEventListener("click", function () { location.hash = "#/inbox/" + tr.dataset.ticket; }); });
  $$("[data-open-ticket]").forEach(function (tr) { tr.addEventListener("click", function () { location.hash = "#/inbox/" + tr.dataset.openTicket; }); });
  $$("[data-customer]").forEach(function (tr) { tr.addEventListener("click", function () { location.hash = "#/customers/" + tr.dataset.customer; }); });
  $$("[data-kit]").forEach(function (tr) { tr.addEventListener("click", function () { location.hash = "#/devices/" + tr.dataset.kit; }); });
  bindDevices();
  bindFlash();
  var t = S.ticket;
  var stSel = $("#f-status");
  if (stSel) stSel.addEventListener("change", function () { patchTicket({ status: stSel.value }); });
  var link = $("#f-link");
  if (link) link.addEventListener("change", function () { if (link.value) patchTicket({ customer_id: +link.value }); });
  var mk = $("[data-make-customer]");
  if (mk) mk.addEventListener("click", function () {
    mk.disabled = true;
    api("/api/office/inbox/" + t.id + "/customer", { method: "POST" }).then(function (d) { S.ticket = d; loadCustomers(); loadInbox(); render(); })
      .catch(function (e) { mk.disabled = false; S.msg = ["err", e.message]; render(); });
  });
  var draft = $("#f-draft");
  if (draft) { draft.value = S.draft; draft.addEventListener("input", function () { S.draft = draft.value; }); }
  var compose = $("#compose");
  if (compose) compose.addEventListener("submit", function (e) {
    e.preventDefault();
    var kind = (e.submitter && e.submitter.dataset.kind) || "note", body = draft.value.trim();
    if (!body) { S.msg = ["err", "Write something first."]; return render(); }
    $$("button", compose).forEach(function (b) { b.disabled = true; });
    api("/api/office/inbox/" + t.id + "/messages", { method: "POST", body: { kind: kind, body: body } }).then(function (d) {
      S.ticket = d; S.draft = "";
      var last = d.thread[d.thread.length - 1];
      S.msg = kind === "note" ? ["ok", "Note added."] : last && last.emailed ? ["ok", "Reply emailed to " + d.email + "."] : ["err", "Saved, but not emailed: email is off on the server."];
      loadInbox(); render();
    }).catch(function (err) { S.msg = ["err", err.message]; render(); });
  });
  var cf = $("#cust-form");
  if (cf) cf.addEventListener("submit", function (e) {
    e.preventDefault();
    var c = S.customer, body = { company: $("#f-company").value, contact: $("#f-contact").value, phone: $("#f-phone").value,
      email: $("#f-email").value, status: $("#f-cstatus").value, address: $("#f-address").value, notes: $("#f-notes").value };
    var isNew = c.id === "new";
    api(isNew ? "/api/office/customers" : "/api/office/customers/" + c.id, { method: isNew ? "POST" : "PATCH", body: body }).then(function (d) {
      S.customer = d; S.msg = ["ok", "Saved.", "cust"]; S.reset = true; loadCustomers();
      if (isNew) location.hash = "#/customers/" + d.id; else render();
    }).catch(function (err) { S.msg = ["err", err.message, "cust"]; render(); });
  });
  var af = $("#acct-form");
  if (af) af.addEventListener("submit", function (e) {
    e.preventDefault();
    var c = S.customer, btn = $("button", af);
    btn.disabled = true;
    api("/api/office/customers/" + c.id + "/account", { method: "POST", body: { email: $("#f-acct-email").value } }).then(function (d) {
      S.customer = d; S.reset = true;
      S.msg = ["ok", d.invite.emailed ? "Account created. Invite emailed to " + d.invite.email + "." : "Account created. Email is off, so send this link yourself: " + d.invite.url, "acct"];
      loadCustomers(); render();
    }).catch(function (err) { btn.disabled = false; S.msg = ["err", err.message, "acct"]; render(); });
  });
}
function bindDevices() {
  var find = $("#f-find");
  if (find) find.addEventListener("input", function () { S.find = find.value; render(); });
  var kf = $("#kit-form");
  if (kf) kf.addEventListener("submit", function (e) {
    e.preventDefault();
    var boards = ["outdoor", "indoor"].map(function (kind) {
      return { kind: kind, serial: $("#f-" + kind + "-serial").value.trim(), fw: $("#f-" + kind + "-fw").value.trim() || null };
    }).filter(function (b) { return b.serial; });
    if (!boards.length) { S.msg = ["err", "Enter at least one board serial.", "kit"]; return render(); }
    var body = { code: $("#f-code").value.trim() || null, hw_rev: $("#f-hwrev").value.trim() || null, notes: $("#f-knotes").value, boards: boards };
    $("button", kf).disabled = true;
    api("/api/office/kits", { method: "POST", body: body }).then(function (d) {
      S.kit = d; S.msg = ["ok", "Kit added. Print its label.", "kit"]; S.reset = true; loadKits(); location.hash = "#/devices/" + d.id;
    }).catch(function (err) { S.msg = ["err", err.message, "kit"]; render(); });
  });
  var k = S.kit;
  if (!k || !k.code) return;
  var cust = $("#f-kcust");
  if (cust) cust.addEventListener("change", function () { kitAction("", "PATCH", { customer_id: +cust.value }, "kit"); });
  var ship = $("[data-ship]");
  if (ship) ship.addEventListener("click", function () { kitAction("", "PATCH", { shipped: ship.dataset.ship === "1" }, "kit"); });
  var nc = $("[data-new-code]");
  if (nc) nc.addEventListener("click", function () { kitAction("/new-code", "POST", null, "kit", "New code made. Print a new label."); });
  var rel = $("[data-release]");
  if (rel) rel.addEventListener("click", function () {
    if (!confirm("Release " + k.code + "? The contractor keeps the system they made, but it stops getting this kit's readings, and the kit can be set up again with a new label.")) return;
    kitAction("/release", "POST", null, "kit", "Released. Print a new label before it ships again.");
  });
  var pr = $("[data-print]");
  if (pr) pr.addEventListener("click", function () { window.print(); });
  var nf = $("#kn-form");
  if (nf) nf.addEventListener("submit", function (e) { e.preventDefault(); kitAction("", "PATCH", { notes: $("#f-kitnotes").value }, "notes", "Saved."); });
  $$("[data-claim-board]").forEach(function (b) { b.addEventListener("click", function () { S.claimBoard = +b.dataset.claimBoard; S.msg = null; render(); var p = $("#f-problem"); if (p) p.focus(); }); });
  var cc = $("[data-cancel-claim]");
  if (cc) cc.addEventListener("click", function () { S.claimBoard = null; render(); });
  var wc = $("#wc-form");
  if (wc) wc.addEventListener("submit", function (e) {
    e.preventDefault();
    api("/api/office/boards/" + S.claimBoard + "/claims", { method: "POST", body: { problem: $("#f-problem").value } }).then(function () {
      S.claimBoard = null; S.msg = null; S.reset = true; loadKit(k.id);
    }).catch(function (err) { S.msg = ["err", err.message, "wc"]; render(); });
  });
  $$("[data-settle]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var id = +f.dataset.settle, status = f.elements.status.value;
      var body = { status: status, resolution: f.elements.resolution.value || null };
      if (status === "replaced") body.replacement_serial = f.elements.serial.value;
      api("/api/office/claims/" + id, { method: "PATCH", body: body }).then(function () { S.msg = null; loadKit(k.id); loadKits(); })
        .catch(function (err) { S.msg = ["err", err.message, "settle" + id]; render(); });
    });
  });
}
function patchTicket(body) {
  var id = S.ticket.id;
  api("/api/office/inbox/" + id, { method: "PATCH", body: body }).then(function (d) { S.ticket = d; loadInbox(); render(); })
    .catch(function (e) { S.msg = ["err", e.message]; render(); });
}

route();
})();
