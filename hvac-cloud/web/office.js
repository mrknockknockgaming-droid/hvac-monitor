/* Fullscope Office (office.html): the business side for Fullscope's own staff. Inbox (order requests,
   website contact messages, contractors' support requests) and Customers (contractor companies and
   their accounts). Signs in through the web app (/app/); the server allows staff only. */
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
          customers: null, customer: null, draft: "", msg: null, busy: false, error: null };

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
    '<nav class="nav" aria-label="Sections">' + nav("inbox", "Inbox", waiting) + nav("customers", "Customers") +
    '<span class="spacer"></span><div class="tools"><a class="btn" href="/app/" style="text-decoration:none">Open the app</a>' +
    '<button class="btn" type="button" data-signout>Sign out</button></div></nav></header>' +
    (S.error ? '<div class="stale-banner">Can\'t reach the server: ' + esc(S.error) + "</div>" : "");
}

function render() {
  if (!S.me || !S.me.staff || !S.view) return;
  var y = window.scrollY, act = document.activeElement, focusId = act && act.id && root.contains(act) ? act.id : null;
  var sel = focusId && [act.selectionStart, act.selectionEnd];
  // what is being typed survives the re-render, unless another page or item opened or it was just saved
  var key = S.view + ":" + (S.ticket && S.ticket.id) + ":" + (S.customer && S.customer.id), keep = {};
  if (key === S.formKey && !S.reset) $$("input[id^='f-'], textarea[id^='f-']").forEach(function (i) { keep[i.id] = i.value; });
  S.formKey = key; S.reset = false;
  root.innerHTML = header() + '<main class="page">' + (S.view === "inbox" ? inboxPage() : customersPage()) + "</main>";
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
function patchTicket(body) {
  var id = S.ticket.id;
  api("/api/office/inbox/" + id, { method: "PATCH", body: body }).then(function (d) { S.ticket = d; loadInbox(); render(); })
    .catch(function (e) { S.msg = ["err", e.message]; render(); });
}

route();
})();
