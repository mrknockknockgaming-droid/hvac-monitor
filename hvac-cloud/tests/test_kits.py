"""Fullscope Office kits, boards and warranty, and the contractor's QR claim."""
import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth, settings
from hvaccloud.api import create_app
from hvaccloud.db import Account, Board, Customer, Invite, System, User, ensure_staff
from hvaccloud.kits import add_months, serial_of, split_code

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}
KIT = {"code": "fs-7k3qx", "hw_rev": "B", "boards": [{"kind": "outdoor", "serial": "246f28abcd01", "fw": "0.3.2"},
                                                     {"kind": "indoor", "serial": "24-6F-28-AB-CD-02"}]}


class FakeMailer:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


def make(sessions, monkeypatch):
    monkeypatch.setattr(settings, "APP_URL", "https://app.example.com/app/")
    with sessions() as s, s.begin():
        a1, a2, a3 = (Account(name="Fullscope", email="t@example.com"), Account(name="Desert Air", email="d@example.com"),
                      Account(name="Other HVAC", email="o@example.com"))
        s.add_all([a1, a2, a3])
        s.flush()
        s.add_all([User(email="tyler@example.com", name="Tyler", role="contractor", account_id=a1.id, admin=True,
                        password_hash=auth.hash_password(PW)),
                   User(email="sam@desertair.example", name="Sam", role="contractor", account_id=a2.id, admin=True,
                        password_hash=auth.hash_password(PW)),
                   User(email="boss@other.example", role="contractor", account_id=a3.id, admin=True,
                        password_hash=auth.hash_password(PW)),
                   Customer(company="Desert Air", status="active", account_id=a2.id),
                   Customer(company="Other HVAC", status="active", account_id=a3.id)])
        s.flush()
        ensure_staff(s)
    mailer = FakeMailer()
    return create_app(sessions, publisher=lambda *a: True, mailer=mailer, send_async=False), mailer


def signed_in(app, email):
    c = TestClient(app, headers=XRW)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def qr_code(kit):
    return kit["claim_url"].rsplit("#/claim/", 1)[1]


def test_helpers():
    assert serial_of("24:6f:28:ab:cd:ef") == serial_of("246F28ABCDEF") == "24:6F:28:AB:CD:EF"
    assert serial_of("24:6f:28") is None
    assert split_code(" fs-7k3qx-abcd2345 ") == ("FS-7K3QX", "ABCD2345")
    assert add_months(dt.date(2026, 1, 31), 1) == dt.date(2026, 2, 28)
    assert add_months(dt.date(2026, 10, 20), 12) == dt.date(2027, 10, 20)


def test_staff_register_kits(sessions, monkeypatch):
    app, _ = make(sessions, monkeypatch)
    staff = signed_in(app, "tyler@example.com")
    assert signed_in(app, "sam@desertair.example").get("/api/office/kits").status_code == 403
    r = staff.post("/api/office/kits", json=KIT)
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["code"] == "FS-7K3QX" and k["site_id"] == "fs-7k3qx" and k["status"] == "stock"
    assert [b["kind"] for b in k["boards"]] == ["outdoor", "indoor"]
    assert [b["serial"] for b in k["boards"]] == ["24:6F:28:AB:CD:01", "24:6F:28:AB:CD:02"]
    assert k["boards"][0]["fw"] == "0.3.2" and k["boards"][0]["hw_rev"] == "B" and k["boards"][0]["seen"] is None
    assert k["claim_url"].startswith("https://app.example.com/app/#/claim/FS-7K3QX-") and len(qr_code(k)) == 17
    assert staff.post("/api/office/kits", json=KIT).status_code == 409                        # same code
    again = {"code": "FS-AAAA", "boards": [{"kind": "outdoor", "serial": "246F28ABCD01"}]}
    assert "already in another kit" in staff.post("/api/office/kits", json=again).json()["detail"]
    two = {"boards": [{"kind": "indoor", "serial": "246F28ABCD03"}, {"kind": "indoor", "serial": "246F28ABCD04"}]}
    assert staff.post("/api/office/kits", json=two).status_code == 422
    assert staff.post("/api/office/kits", json={"boards": [{"kind": "indoor", "serial": "nope"}]}).status_code == 422
    assert staff.post("/api/office/kits", json={"code": "kit1", "boards": [{"kind": "indoor", "serial": "246F28ABCD05"}]}).status_code == 422
    made = staff.post("/api/office/kits", json={"boards": [{"kind": "indoor", "serial": "246F28ABCD05"}]}).json()
    assert made["code"].startswith("FS-") and len(made["code"]) == 8
    assert [x["code"] for x in staff.get("/api/office/kits").json()] == [made["code"], "FS-7K3QX"]
    assert [b["serial"] for b in staff.get("/api/office/boards", params={"q": "cd:0"}).json()] == [
        "24:6F:28:AB:CD:05", "24:6F:28:AB:CD:02", "24:6F:28:AB:CD:01"]
    old = qr_code(k)
    assert qr_code(staff.post(f"/api/office/kits/{k['id']}/new-code").json()) != old


def test_contractor_claims_a_kit(sessions, monkeypatch):
    app, mailer = make(sessions, monkeypatch)
    staff, sam = signed_in(app, "tyler@example.com"), signed_in(app, "sam@desertair.example")
    k = staff.post("/api/office/kits", json=KIT).json()
    with sessions() as s:
        desert = s.scalar(select(Customer).where(Customer.company == "Desert Air"))
    k = staff.patch(f"/api/office/kits/{k['id']}", json={"customer_id": desert.id, "shipped": True}).json()
    assert k["status"] == "shipped" and k["customer"]["company"] == "Desert Air"
    assert {b["status"] for b in k["boards"]} == {"shipped"}
    code = qr_code(k)

    assert sam.post("/api/claim/lookup", json={"code": "FS-7K3QX-WRONG"}).status_code == 404
    boss = signed_in(app, "boss@other.example")                    # shipped to Desert Air, not to them
    assert boss.post("/api/claim/lookup", json={"code": code}).json()["status"] == "taken"
    assert boss.post("/api/claim", json={"code": code, "system_name": "Mine"}).status_code == 409
    look = sam.post("/api/claim/lookup", json={"code": code.lower()}).json()
    assert look["status"] == "available" and look["kit"] == "FS-7K3QX" and len(look["boards"]) == 2

    r = sam.post("/api/claim", json={"code": code, "system_name": "Garcia home", "address": "12 Palm Ln, Mesa AZ",
                                     "homeowner_email": "Ana@Garcia.example"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["system"]["site_id"] == "fs-7k3qx" and out["system"]["address"] == "12 Palm Ln, Mesa AZ"
    assert out["invite"]["email"] == "ana@garcia.example"
    assert mailer.sent[-1][0] == "ana@garcia.example"
    assert [x["name"] for x in sam.get("/api/systems").json()] == ["Garcia home"]
    with sessions() as s:
        assert s.scalar(select(Invite).where(Invite.email == "ana@garcia.example")).system_id == out["system"]["id"]
    assert sam.post("/api/claim/lookup", json={"code": code}).json()["status"] == "yours"
    assert sam.post("/api/claim", json={"code": code, "system_name": "Again"}).status_code == 409

    k = staff.get(f"/api/office/kits/{k['id']}").json()
    assert k["status"] == "installed" and k["claimed_by"] == "Sam" and k["system"]["name"] == "Garcia home"
    w = k["boards"][0]["warranty"]
    assert w["months"] == settings.WARRANTY_MONTHS and w["active"] is True
    assert w["ends"] == add_months(dt.date.fromisoformat(w["start"]), settings.WARRANTY_MONTHS).isoformat()

    k = staff.post(f"/api/office/kits/{k['id']}/release").json()       # claimed by mistake
    assert k["status"] == "shipped" and k["system"] is None and qr_code(k) != code
    assert {b["warranty"]["start"] for b in k["boards"]} == {None}
    with sessions() as s:
        assert s.scalar(select(System).where(System.name == "Garcia home")).site_id.startswith("fs-7k3qx-old")


def test_homeowners_cannot_claim(sessions, monkeypatch):
    app, _ = make(sessions, monkeypatch)
    with sessions() as s, s.begin():
        s.add(User(email="ana@garcia.example", role="homeowner", password_hash=auth.hash_password(PW)))
    ana = signed_in(app, "ana@garcia.example")
    assert ana.post("/api/claim/lookup", json={"code": "FS-AAA-BBB"}).status_code == 403


def test_wrong_codes_are_throttled(sessions, monkeypatch):
    app, _ = make(sessions, monkeypatch)
    sam = signed_in(app, "sam@desertair.example")
    for _ in range(10):
        assert sam.post("/api/claim/lookup", json={"code": "FS-AAA-BBB"}).status_code == 404
    assert sam.post("/api/claim/lookup", json={"code": "FS-AAA-BBB"}).status_code == 429


def test_warranty_claim_replacement(sessions, monkeypatch):
    app, _ = make(sessions, monkeypatch)
    staff, sam = signed_in(app, "tyler@example.com"), signed_in(app, "sam@desertair.example")
    k = staff.post("/api/office/kits", json=KIT).json()
    sam.post("/api/claim", json={"code": qr_code(k), "system_name": "Garcia home"})
    outdoor = k["boards"][0]
    c = staff.post(f"/api/office/boards/{outdoor['id']}/claims", json={"problem": "Suction sensor reads open"}).json()
    assert c["status"] == "open" and c["board"]["serial"] == outdoor["serial"]
    assert staff.get(f"/api/office/kits/{k['id']}").json()["boards"][0]["status"] == "rma"
    bad = staff.patch(f"/api/office/claims/{c['id']}", json={"status": "replaced", "replacement_serial": "x"})
    assert bad.status_code == 422
    c = staff.patch(f"/api/office/claims/{c['id']}", json={"status": "replaced", "replacement_serial": "246F28ABCD99",
                                                         "resolution": "Sent a new outdoor board"}).json()
    assert c["status"] == "replaced" and c["replacement"]["serial"] == "24:6F:28:AB:CD:99" and c["closed_at"]
    k2 = staff.get(f"/api/office/kits/{k['id']}").json()
    assert [b["serial"] for b in k2["boards"]] == ["24:6F:28:AB:CD:99", "24:6F:28:AB:CD:02"]
    new = k2["boards"][0]
    assert new["status"] == "installed" and new["warranty"] == k2["boards"][1]["warranty"]    # keeps the old board's warranty
    assert len(k2["claims"]) == 1
    with sessions() as s:
        assert s.get(Board, outdoor["id"]).status == "retired" and s.get(Board, outdoor["id"]).kit_id is None

    indoor = k2["boards"][1]
    c = staff.post(f"/api/office/boards/{indoor['id']}/claims", json={"problem": "Loose terminal"}).json()
    c = staff.patch(f"/api/office/claims/{c['id']}", json={"status": "repaired"}).json()
    assert c["status"] == "repaired"
    assert staff.get(f"/api/office/kits/{k['id']}").json()["boards"][1]["status"] == "installed"
