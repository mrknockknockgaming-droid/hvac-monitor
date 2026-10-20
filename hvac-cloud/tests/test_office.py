"""Fullscope Office: staff-only inbox (orders, contact messages, support requests) and customers."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth, settings
from hvaccloud.api import create_app
from hvaccloud.db import Account, Customer, Invite, Order, System, Ticket, User, ensure_order_tickets, ensure_staff

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}
ORDER = {"company": "Desert Air Co.", "name": "Sam", "email": "sam@desertair.example", "phone": "480-555-0100",
         "systems": 5, "notes": "Pilot on maintenance-agreement customers"}
CONTACT = {"name": "Pat", "email": " Pat@Cool.example ", "company": "Cool Co", "topic": "sales", "message": "Do you ship to Texas?"}


class FakeMailer:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


def make(sessions, monkeypatch):
    monkeypatch.setattr(settings, "ORDER_EMAIL", "owner@example.com")
    monkeypatch.setattr(settings, "APP_URL", "https://app.example.com/app/")
    with sessions() as s, s.begin():
        a1, a2 = Account(name="Fullscope", email="t@example.com"), Account(name="Other HVAC", email="o@example.com")
        s.add_all([a1, a2])
        s.flush()
        s.add(System(account_id=a2.id, name="Garcia home", site_id="garcia"))
        s.add_all([User(email="tyler@example.com", name="Tyler", role="contractor", account_id=a1.id, admin=True,
                        password_hash=auth.hash_password(PW)),
                   User(email="boss@other.example", name="Boss", role="contractor", account_id=a2.id, admin=True,
                        password_hash=auth.hash_password(PW)),
                   User(email="tech@other.example", role="contractor", account_id=a2.id, password_hash=auth.hash_password(PW))])
        s.flush()
        ensure_staff(s)
    mailer = FakeMailer()
    app = create_app(sessions, publisher=lambda *a: True, mailer=mailer, send_async=False)
    return app, mailer


def signed_in(app, email):
    c = TestClient(app, headers=XRW)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def test_first_admin_becomes_staff_once(sessions, monkeypatch):
    app, _ = make(sessions, monkeypatch)
    with sessions() as s:
        assert [u.email for u in s.scalars(select(User).where(User.staff.is_(True)))] == ["tyler@example.com"]
    staff, boss = signed_in(app, "tyler@example.com"), signed_in(app, "boss@other.example")
    assert staff.get("/api/auth/me").json()["staff"] is True
    assert boss.get("/api/auth/me").json()["staff"] is False
    with sessions() as s, s.begin():                     # nobody else is promoted while someone is staff
        ensure_staff(s)
        assert s.scalar(select(User).where(User.email == "boss@other.example")).staff is None


def test_office_is_for_staff_only(sessions, monkeypatch):
    app, _ = make(sessions, monkeypatch)
    boss = signed_in(app, "boss@other.example")
    for path in ("/api/office/summary", "/api/office/inbox", "/api/office/customers"):
        assert boss.get(path).status_code == 403
    assert TestClient(app).get("/api/office/inbox").status_code == 401


def test_orders_and_contact_messages_land_in_the_inbox(sessions, monkeypatch):
    app, mailer = make(sessions, monkeypatch)
    anon = TestClient(app)
    assert anon.post("/api/orders", json=ORDER).status_code == 200
    r = anon.post("/api/contact", json=CONTACT)
    assert r.status_code == 200
    to, subject, body = mailer.sent[-1]
    assert to == "owner@example.com" and subject == f"[Fullscope #{r.json()['id']}] Sales question from Cool Co"
    assert "Do you ship to Texas?" in body and "office.html#/inbox/" in body
    staff = signed_in(app, "tyler@example.com")
    rows = staff.get("/api/office/inbox").json()
    assert [(t["kind"], t["status"], t["email"]) for t in rows] == [("contact", "new", "pat@cool.example"),
                                                                     ("order", "new", "sam@desertair.example")]
    assert rows[1]["subject"] == "Order request for 5 systems"
    assert staff.get("/api/office/summary").json()["inbox"] == {"new": 2, "open": 0, "waiting": 0, "closed": 0}
    detail = staff.get(f"/api/office/inbox/{rows[1]['id']}").json()
    assert detail["status"] == "open" and detail["order"]["systems"] == 5 and detail["phone"] == "480-555-0100"


def test_contact_form_checks_fields_bots_and_floods(sessions, monkeypatch):
    app, mailer = make(sessions, monkeypatch)
    anon = TestClient(app)
    assert anon.post("/api/contact", json={**CONTACT, "email": "nope"}).status_code == 422
    assert anon.post("/api/contact", json={**CONTACT, "message": "   "}).status_code == 422
    assert anon.post("/api/contact", json={**CONTACT, "topic": "spam"}).status_code == 422
    assert anon.post("/api/contact", json={**CONTACT, "website": "http://x"}).json() == {"ok": True}
    for _ in range(5):
        assert anon.post("/api/contact", json=CONTACT).status_code == 200
    assert anon.post("/api/contact", json=CONTACT).status_code == 429
    with sessions() as s:
        assert len(s.scalars(select(Ticket)).all()) == 5


def test_reply_is_emailed_and_notes_stay_internal(sessions, monkeypatch):
    app, mailer = make(sessions, monkeypatch)
    tid = TestClient(app).post("/api/contact", json=CONTACT).json()["id"]
    staff = signed_in(app, "tyler@example.com")
    assert staff.post(f"/api/office/inbox/{tid}/messages", json={"kind": "note", "body": "Call back Monday"}).status_code == 200
    n = len(mailer.sent)
    d = staff.post(f"/api/office/inbox/{tid}/messages", json={"kind": "reply", "body": "Yes, we ship anywhere."}).json()
    assert len(mailer.sent) == n + 1
    to, subject, body = mailer.sent[-1]
    assert to == "pat@cool.example" and subject == f"Re: Sales question from Cool Co [Fullscope #{tid}]"
    assert body.startswith("Yes, we ship anywhere.")
    assert d["status"] == "waiting"
    assert [(m["kind"], m["emailed"], m["author"]) for m in d["thread"]] == [("note", False, "Tyler"), ("reply", True, "Tyler")]
    assert staff.post(f"/api/office/inbox/{tid}/messages", json={"kind": "reply", "body": "  "}).status_code == 422
    d = staff.patch(f"/api/office/inbox/{tid}", json={"status": "closed"}).json()
    assert d["status"] == "closed"
    assert staff.get("/api/office/inbox").json() == []
    assert [t["id"] for t in staff.get("/api/office/inbox?status=closed").json()] == [tid]


def test_support_requests_come_from_contractors_and_show_replies(sessions, monkeypatch):
    app, mailer = make(sessions, monkeypatch)
    tech = signed_in(app, "tech@other.example")
    with sessions() as s:
        garcia = s.scalar(select(System).where(System.site_id == "garcia")).id
    r = tech.post("/api/support", json={"subject": "Indoor node offline", "message": "Since this morning", "system_id": garcia})
    assert r.status_code == 200
    tid = r.json()["id"]
    assert "System: Garcia home (garcia)" in r.json()["body"]
    assert tech.post("/api/support", json={"subject": "x", "message": "y", "system_id": 999}).status_code == 404
    staff = signed_in(app, "tyler@example.com")
    staff.post(f"/api/office/inbox/{tid}/messages", json={"kind": "note", "body": "internal"})
    staff.post(f"/api/office/inbox/{tid}/messages", json={"kind": "reply", "body": "Power-cycle it please"})
    mine = signed_in(app, "boss@other.example").get("/api/support").json()     # the whole account sees them
    assert [(m["subject"], m["status"], [x["body"] for x in m["replies"]]) for m in mine] == \
        [("Indoor node offline", "open", ["Power-cycle it please"])]
    d = staff.get(f"/api/office/inbox/{tid}").json()
    assert d["account"]["name"] == "Other HVAC" and d["system"]["site_id"] == "garcia" and d["company"] == "Other HVAC"


def test_customers_from_orders_and_their_accounts(sessions, monkeypatch):
    app, mailer = make(sessions, monkeypatch)
    TestClient(app).post("/api/orders", json=ORDER)
    staff = signed_in(app, "tyler@example.com")
    tid = staff.get("/api/office/inbox").json()[0]["id"]
    d = staff.post(f"/api/office/inbox/{tid}/customer").json()
    cid = d["customer"]["id"]
    assert d["customer"]["company"] == "Desert Air Co."
    assert staff.post(f"/api/office/inbox/{tid}/customer").status_code == 409
    c = staff.get(f"/api/office/customers/{cid}").json()
    assert (c["contact"], c["email"], c["status"], c["account"], len(c["tickets"])) == ("Sam", "sam@desertair.example", "lead", None, 1)
    c = staff.patch(f"/api/office/customers/{cid}", json={"address": "1 Main St, Mesa AZ", "notes": "Met at ACCA"}).json()
    assert (c["address"], c["notes"], c["contact"]) == ("1 Main St, Mesa AZ", "Met at ACCA", "Sam")
    assert staff.patch(f"/api/office/customers/{cid}", json={"company": " "}).status_code == 422
    assert staff.post(f"/api/office/customers/{cid}/account", json={"email": "boss@other.example"}).status_code == 409
    c = staff.post(f"/api/office/customers/{cid}/account", json={"email": "Sam@DesertAir.example"}).json()
    assert c["status"] == "active" and c["account"]["name"] == "Desert Air Co." and c["invite"]["emailed"] is True
    assert "#/invite/" in c["invite"]["url"] and [i["email"] for i in c["invites"]] == ["sam@desertair.example"]
    assert mailer.sent[-1][0] == "sam@desertair.example"
    with sessions() as s:
        inv = s.scalar(select(Invite).where(Invite.email == "sam@desertair.example"))
        assert (inv.role, inv.admin, inv.account_id) == ("contractor", True, c["account"]["id"])
    assert staff.post(f"/api/office/customers/{cid}/account", json={"email": "x@y.example"}).status_code == 409
    rows = staff.get("/api/office/customers").json()
    assert [(r["company"], r["systems"], r["open_tickets"]) for r in rows] == [("Desert Air Co.", 0, 1)]
    new = staff.post("/api/office/customers", json={"company": "Acme Air", "phone": "602-555-0111"}).json()
    assert (new["company"], new["status"], new["phone"]) == ("Acme Air", "lead", "602-555-0111")
    assert staff.post("/api/office/customers", json={"phone": "1"}).status_code == 422
    d = staff.patch(f"/api/office/inbox/{tid}", json={"customer_id": 0}).json()
    assert d["customer"] is None
    assert staff.patch(f"/api/office/inbox/{tid}", json={"customer_id": 999}).status_code == 404


def test_older_orders_get_inbox_tickets(sessions):
    with sessions() as s, s.begin():
        s.add(Order(company="Early Bird", name="E", email="e@x.example", systems=1))
    with sessions() as s, s.begin():
        ensure_order_tickets(s)
        ensure_order_tickets(s)                          # once only
    with sessions() as s:
        (t,) = s.scalars(select(Ticket)).all()
        assert (t.kind, t.subject, t.company) == ("order", "Order request for 1 system", "Early Bird")
        assert s.scalar(select(Customer)) is None
