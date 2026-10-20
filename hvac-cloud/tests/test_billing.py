"""Fullscope Office billing: prices per customer, monthly drafts, sending, paid and void."""
import datetime as dt
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth, billing, settings
from hvaccloud.api import create_app
from hvaccloud.db import Account, Customer, Kit, System, User, ensure_staff, utcnow

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}


class FakeMailer:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


def make(sessions, monkeypatch, tmp_path, prices=None):
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(prices or {"kit": None, "monitoring": None}))
    monkeypatch.setattr(billing, "PRICING_FILE", str(pricing))
    monkeypatch.setattr(settings, "INVOICE_NOTE", "Pay by check to Fullscope.")
    with sessions() as s, s.begin():
        a1, a2 = Account(name="Fullscope", email="t@example.com"), Account(name="Desert Air", email="d@example.com")
        s.add_all([a1, a2])
        s.flush()
        s.add_all([User(email="tyler@example.com", name="Tyler", role="contractor", account_id=a1.id, admin=True,
                        password_hash=auth.hash_password(PW)),
                   User(email="sam@desertair.example", name="Sam", role="contractor", account_id=a2.id, admin=True,
                        password_hash=auth.hash_password(PW)),
                   System(account_id=a2.id, name="Lee home", site_id="fs-aaaaa", atm_psia=14.7),
                   System(account_id=a2.id, name="Garcia home", site_id="fs-bbbbb", atm_psia=14.7),
                   Customer(company="Desert Air", status="active", account_id=a2.id, email="billing@desertair.example",
                            address="1 Main St, Mesa AZ"),
                   Customer(company="Lead Co", status="lead")])
        s.flush()
        desert = s.scalar(select(Customer).where(Customer.company == "Desert Air"))
        s.add_all([Kit(code="FS-AAAAA", claim_code="X", customer_id=desert.id, shipped_at=utcnow()),
                   Kit(code="FS-CCCCC", claim_code="Y", customer_id=desert.id)])            # not shipped yet
        ensure_staff(s)
    mailer = FakeMailer()
    return create_app(sessions, publisher=lambda *a: True, mailer=mailer, send_async=False), mailer


def signed_in(app, email):
    c = TestClient(app, headers=XRW)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def desert_id(sessions):
    with sessions() as s:
        return s.scalar(select(Customer.id).where(Customer.company == "Desert Air"))


def test_money():
    assert billing.money(123456) == "$1,234.56" and billing.money(-500) == "-$5.00" and billing.money(7) == "$0.07"
    assert billing.month_name("2026-10") == "October 2026"


def test_monthly_run_and_prices(sessions, monkeypatch, tmp_path):
    app, _ = make(sessions, monkeypatch, tmp_path, {"kit": 349.99, "monitoring": 19})
    staff = signed_in(app, "tyler@example.com")
    assert signed_in(app, "sam@desertair.example").get("/api/office/invoices").status_code == 403
    cid = desert_id(sessions)
    c = staff.get(f"/api/office/customers/{cid}").json()
    assert c["list_prices"] == {"kit_cents": 34999, "monthly_cents": 1900} and c["kit_cents"] is None
    c = staff.patch(f"/api/office/customers/{cid}", json={"monthly_cents": 1500}).json()
    assert c["monthly_cents"] == 1500 and c["kit_cents"] is None

    assert staff.post("/api/office/invoices/run", json={"period": "2026-13"}).status_code == 422
    r = staff.post("/api/office/invoices/run", json={"period": "2026-10"}).json()
    assert r["missing_prices"] is False and r["skipped"] == []                           # Lead Co isn't active
    (row,) = r["created"]
    assert row["customer"]["company"] == "Desert Air" and row["status"] == "draft" and row["number"] == 1000 + row["id"]
    assert row["total_cents"] == 34999 + 2 * 1500 and row["lines"] == 3
    inv = staff.get(f"/api/office/invoices/{row['id']}").json()
    assert [x["description"] for x in inv["lines"]] == ["Fullscope kit FS-AAAAA", "Monitoring, October 2026: Garcia home",
                                                        "Monitoring, October 2026: Lee home"]
    assert inv["bill_to"]["email"] == "billing@desertair.example" and inv["pay_note"] == "Pay by check to Fullscope."

    again = staff.post("/api/office/invoices/run", json={"period": "2026-10"}).json()
    assert again["created"] == [] and again["skipped"][0]["reason"] == "Already has an invoice for October 2026."
    nov = staff.post("/api/office/invoices/run", json={"period": "2026-11", "customer_id": cid}).json()
    assert [x["description"] for x in staff.get(f"/api/office/invoices/{nov['created'][0]['id']}").json()["lines"]] == [
        "Monitoring, November 2026: Garcia home", "Monitoring, November 2026: Lee home"]          # the kit is billed once
    assert len(staff.get(f"/api/office/customers/{cid}").json()["invoices"]) == 2


def test_missing_prices_and_nothing_to_bill(sessions, monkeypatch, tmp_path):
    app, _ = make(sessions, monkeypatch, tmp_path)
    staff = signed_in(app, "tyler@example.com")
    with sessions() as s:
        lead = s.scalar(select(Customer.id).where(Customer.company == "Lead Co"))
    r = staff.post("/api/office/invoices/run", json={"period": "2026-10"}).json()
    assert r["missing_prices"] is True and r["created"][0]["total_cents"] == 0
    r = staff.post("/api/office/invoices/run", json={"period": "2026-10", "customer_id": lead}).json()
    assert r["created"] == [] and r["skipped"][0]["reason"] == "Nothing to bill."


def test_edit_send_pay_void(sessions, monkeypatch, tmp_path):
    app, mailer = make(sessions, monkeypatch, tmp_path)
    staff = signed_in(app, "tyler@example.com")
    cid = desert_id(sessions)
    inv = staff.post("/api/office/invoices", json={"customer_id": cid}).json()
    assert inv["status"] == "draft" and inv["lines"] == [] and inv["total_cents"] == 0
    assert staff.post(f"/api/office/invoices/{inv['id']}/send").json()["detail"] == "Add a line first."
    lines = [{"description": "Fullscope kit", "qty": 2, "unit_cents": 34900}, {"description": "Discount", "unit_cents": -5000}]
    inv = staff.patch(f"/api/office/invoices/{inv['id']}", json={"lines": lines, "notes": "Thanks!"}).json()
    assert inv["total_cents"] == 64800 and [x["amount_cents"] for x in inv["lines"]] == [69800, -5000]
    assert staff.patch(f"/api/office/invoices/{inv['id']}", json={"lines": [{"description": " ", "unit_cents": 1}]}).status_code == 422

    inv = staff.post(f"/api/office/invoices/{inv['id']}/send").json()
    today = dt.date.today()
    assert inv["status"] == "sent" and inv["issued"] == today.isoformat() and inv["emailed"] is True
    assert inv["due"] == (today + dt.timedelta(days=settings.INVOICE_DAYS)).isoformat()
    to, subject, body = mailer.sent[-1]
    assert to == "billing@desertair.example" and subject == f"Fullscope invoice {inv['number']} for Desert Air"
    assert "Fullscope kit: 2 x $349.00 = $698.00" in body and "Total due: $648.00" in body and "Pay by check" in body
    assert staff.get("/api/office/summary").json()["invoices"] == {"draft": 0, "sent": 1, "overdue": 0, "unpaid_cents": 64800}

    assert staff.patch(f"/api/office/invoices/{inv['id']}", json={"notes": "x"}).status_code == 409
    assert staff.delete(f"/api/office/invoices/{inv['id']}").status_code == 409
    past = (today - dt.timedelta(days=1)).isoformat()
    inv = staff.patch(f"/api/office/invoices/{inv['id']}", json={"due": past}).json()
    assert inv["overdue"] is True and staff.get("/api/office/summary").json()["invoices"]["overdue"] == 1
    future = (today + dt.timedelta(days=1)).isoformat()
    assert staff.patch(f"/api/office/invoices/{inv['id']}", json={"status": "paid", "paid_on": future}).status_code == 422
    inv = staff.patch(f"/api/office/invoices/{inv['id']}", json={"status": "paid", "paid_note": "Check 1042"}).json()
    assert inv["status"] == "paid" and inv["paid_on"] == today.isoformat() and inv["paid_note"] == "Check 1042" and not inv["overdue"]
    assert staff.patch(f"/api/office/invoices/{inv['id']}", json={"status": "void"}).status_code == 409
    inv = staff.patch(f"/api/office/invoices/{inv['id']}", json={"status": "sent"}).json()        # paid by mistake
    assert inv["paid_on"] is None and inv["paid_note"] is None
    inv = staff.patch(f"/api/office/invoices/{inv['id']}", json={"status": "void"}).json()
    assert [x["id"] for x in staff.get("/api/office/invoices?status=void").json()] == [inv["id"]]
    assert staff.get("/api/office/invoices").json() == []

    draft = staff.post("/api/office/invoices", json={"customer_id": cid, "lines": [{"description": "Setup", "unit_cents": 100}]}).json()
    assert staff.delete(f"/api/office/invoices/{draft['id']}").json() == {"ok": True}
    assert staff.get(f"/api/office/invoices/{draft['id']}").status_code == 404


def test_send_needs_email(sessions, monkeypatch, tmp_path):
    app, mailer = make(sessions, monkeypatch, tmp_path)
    staff = signed_in(app, "tyler@example.com")
    with sessions() as s:
        lead = s.scalar(select(Customer.id).where(Customer.company == "Lead Co"))
    inv = staff.post("/api/office/invoices", json={"customer_id": lead, "lines": [{"description": "Kit", "unit_cents": 100}]}).json()
    assert "no email address" in staff.post(f"/api/office/invoices/{inv['id']}/send").json()["detail"]
    mailer.enabled = False
    cid = desert_id(sessions)
    inv = staff.post("/api/office/invoices", json={"customer_id": cid, "lines": [{"description": "Kit", "unit_cents": 100}]}).json()
    assert staff.post(f"/api/office/invoices/{inv['id']}/send").status_code == 503
    inv = staff.get(f"/api/office/invoices/{inv['id']}").json()
    assert inv["status"] == "draft" and inv["issued"] is None                         # nothing changed
    inv = staff.patch(f"/api/office/invoices/{inv['id']}", json={"status": "sent"}).json()  # printed and posted
    assert inv["status"] == "sent" and inv["emailed"] is None
