"""Fullscope Office quotes: emailed with a link the customer opens to accept or decline."""
import datetime as dt
import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth, settings
from hvaccloud.api import create_app
from hvaccloud.db import Account, Customer, User, ensure_staff

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}
LINES = [{"description": "Fullscope kit", "qty": 3, "unit_cents": 34900}, {"description": "Install visit", "unit_cents": 15000}]


class FakeMailer:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


def make(sessions, monkeypatch):
    monkeypatch.setattr(settings, "APP_URL", "https://app.example/app/")
    monkeypatch.setattr(settings, "ORDER_EMAIL", "office@fullscope.example")
    with sessions() as s, s.begin():
        a = Account(name="Fullscope", email="t@example.com")
        s.add(a)
        s.flush()
        s.add_all([User(email="tyler@example.com", name="Tyler", role="contractor", account_id=a.id, admin=True,
                        password_hash=auth.hash_password(PW)),
                   Customer(company="Desert Air", status="lead", email="sam@desertair.example", contact="Sam Lee"),
                   Customer(company="No Email Co", status="lead")])
        ensure_staff(s)
    mailer = FakeMailer()
    app = create_app(sessions, publisher=lambda *a: True, mailer=mailer, send_async=False)
    staff = TestClient(app, headers=XRW)
    assert staff.post("/api/auth/login", json={"email": "tyler@example.com", "password": PW}).status_code == 200
    return app, staff, mailer


def customer(sessions, company):
    with sessions() as s:
        return s.scalar(select(Customer.id).where(Customer.company == company))


def token_of(body):
    return re.search(r"quote\.html#/quote/([A-Za-z0-9_-]+)", body).group(1)


def test_send_and_accept(sessions, monkeypatch):
    app, staff, mailer = make(sessions, monkeypatch)
    cid = customer(sessions, "Desert Air")
    q = staff.post("/api/office/quotes", json={"customer_id": cid, "lines": LINES, "notes": " Ships in a week "}).json()
    assert q["status"] == "draft" and q["number"] == f"Q-{1000 + q['id']}" and q["total_cents"] == 3 * 34900 + 15000
    assert q["notes"] == "Ships in a week" and q["has_link"] is False

    q = staff.post(f"/api/office/quotes/{q['id']}/send").json()
    today = dt.date.today()
    assert q["status"] == "sent" and q["issued"] == today.isoformat() and q["emailed"] is True
    assert q["valid_until"] == (today + dt.timedelta(days=settings.QUOTE_DAYS)).isoformat()
    to, subject, body = mailer.sent[-1]
    assert to == "sam@desertair.example" and subject == f"Fullscope quote {q['number']} for Desert Air"
    assert "Fullscope kit: 3 x $349.00 = $1,047.00" in body and "Total: $1,197.00" in body
    assert q["link"] in body and q["link"].startswith("https://app.example/app/quote.html#/quote/")
    token = token_of(body)
    assert staff.patch(f"/api/office/quotes/{q['id']}", json={"notes": "x"}).status_code == 409

    anon = TestClient(app)
    seen = anon.post("/api/quote/view", json={"token": token}).json()
    assert seen["number"] == q["number"] and seen["total_cents"] == 119700 and seen["to"]["contact"] == "Sam Lee"
    assert "viewed_at" not in seen and "id" not in seen
    assert staff.get(f"/api/office/quotes/{q['id']}").json()["viewed_at"] is not None
    assert anon.post("/api/quote/view", json={"token": "nope"}).status_code == 404

    assert anon.post("/api/quote/answer", json={"token": token, "accept": True, "name": "  "}).json()["detail"] == "Type your name to accept."
    done = anon.post("/api/quote/answer", json={"token": token, "accept": True, "name": " Sam Lee "}, headers={"CF-Connecting-IP": "1.2.3.4"})
    assert done.json()["status"] == "accepted" and done.json()["answered_name"] == "Sam Lee"
    to, subject, body = mailer.sent[-1]
    assert to == "office@fullscope.example" and subject == f"[Fullscope] Quote {q['number']} accepted by Desert Air"
    assert f"office.html#/quotes/{q['id']}" in body
    assert anon.post("/api/quote/answer", json={"token": token, "accept": False}).status_code == 409
    assert anon.post("/api/quote/view", json={"token": token}).json()["status"] == "accepted"      # the link still shows it

    assert staff.get("/api/office/summary").json()["quotes"] == {"draft": 0, "sent": 0, "accepted": 1}
    r = staff.post(f"/api/office/quotes/{q['id']}/invoice").json()
    inv = staff.get(f"/api/office/invoices/{r['invoice_id']}").json()
    assert inv["status"] == "draft" and inv["total_cents"] == 119700 and inv["notes"] == f"Quote {q['number']}\nShips in a week"
    assert staff.post(f"/api/office/quotes/{q['id']}/invoice").status_code == 409
    assert staff.patch(f"/api/office/quotes/{q['id']}", json={"status": "sent"}).status_code == 409
    assert staff.get("/api/office/summary").json()["quotes"]["accepted"] == 0
    assert staff.get("/api/office/quotes").json() == []                                          # invoiced: no longer open
    assert [x["id"] for x in staff.get(f"/api/office/customers/{cid}").json()["quotes"]] == [q["id"]]


def test_decline_new_link_expiry_and_by_hand(sessions, monkeypatch):
    app, staff, mailer = make(sessions, monkeypatch)
    anon = TestClient(app)
    cid = customer(sessions, "Desert Air")
    q = staff.post("/api/office/quotes", json={"customer_id": cid}).json()
    assert staff.post(f"/api/office/quotes/{q['id']}/send").json()["detail"] == "Add a line first."
    staff.patch(f"/api/office/quotes/{q['id']}", json={"lines": LINES})
    first = token_of(staff.post(f"/api/office/quotes/{q['id']}/send").json()["link"])
    second = token_of(staff.post(f"/api/office/quotes/{q['id']}/send", json={"email": False}).json()["link"])
    assert len(mailer.sent) == 1                                                                  # the link only, no email
    assert anon.post("/api/quote/view", json={"token": first}).status_code == 404                 # replaced

    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    assert staff.patch(f"/api/office/quotes/{q['id']}", json={"valid_until": past}).json()["expired"] is True
    r = anon.post("/api/quote/answer", json={"token": second, "accept": True, "name": "Sam"})
    assert r.status_code == 410 and past in r.json()["detail"]
    staff.patch(f"/api/office/quotes/{q['id']}", json={"valid_until": dt.date.today().isoformat()})
    r = anon.post("/api/quote/answer", json={"token": second, "accept": False, "reason": " Too pricey "}).json()
    assert r["status"] == "declined" and "declined by Desert Air" in mailer.sent[-1][1] and "Reason: Too pricey" in mailer.sent[-1][2]

    q = staff.patch(f"/api/office/quotes/{q['id']}", json={"status": "draft"}).json()               # revise it
    assert q["answered_at"] is None and q["has_link"] is False and q["decline_reason"] is None
    assert anon.post("/api/quote/view", json={"token": second}).status_code == 404
    staff.post(f"/api/office/quotes/{q['id']}/send", json={"email": False})
    q = staff.patch(f"/api/office/quotes/{q['id']}", json={"status": "accepted", "answered_name": "Sam, by phone"}).json()
    assert q["status"] == "accepted" and q["answered_name"] == "Sam, by phone"
    assert staff.patch(f"/api/office/quotes/{q['id']}", json={"status": "void"}).status_code == 409
    assert staff.delete(f"/api/office/quotes/{q['id']}").status_code == 409


def test_staff_only_email_off_and_delete(sessions, monkeypatch):
    app, staff, mailer = make(sessions, monkeypatch)
    assert TestClient(app, headers=XRW).get("/api/office/quotes").status_code == 401
    none = customer(sessions, "No Email Co")
    q = staff.post("/api/office/quotes", json={"customer_id": none, "lines": LINES}).json()
    assert "Get link" in staff.post(f"/api/office/quotes/{q['id']}/send").json()["detail"]
    assert staff.post(f"/api/office/quotes/{q['id']}/send", json={"email": False}).json()["status"] == "sent"
    mailer.enabled = False
    q2 = staff.post("/api/office/quotes", json={"customer_id": customer(sessions, "Desert Air"), "lines": LINES}).json()
    assert staff.post(f"/api/office/quotes/{q2['id']}/send").status_code == 503
    q2 = staff.get(f"/api/office/quotes/{q2['id']}").json()
    assert q2["status"] == "draft" and q2["issued"] is None and q2["has_link"] is False            # nothing changed
    assert staff.post("/api/office/quotes", json={"customer_id": 999}).status_code == 404
    assert staff.post("/api/office/quotes", json={"customer_id": none, "lines": [{"description": " ", "unit_cents": 1}]}).status_code == 422
    assert staff.delete(f"/api/office/quotes/{q2['id']}").json() == {"ok": True}
    assert staff.delete(f"/api/office/quotes/{q['id']}").status_code == 409                          # sent: void it
    assert [x["status"] for x in staff.get("/api/office/quotes?status=all").json()] == ["sent"]
