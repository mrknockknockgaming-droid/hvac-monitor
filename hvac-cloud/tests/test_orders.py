from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import settings
from hvaccloud.api import create_app
from hvaccloud.db import Order

ORDER = {"company": "Desert Air Co.", "name": "Sam", "email": " Sam@DesertAir.example ", "phone": "480-555-0100",
         "systems": 5, "notes": "Pilot on maintenance-agreement customers"}


class FakeMailer:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


def make(sessions, monkeypatch):
    monkeypatch.setattr(settings, "ORDER_EMAIL", "owner@example.com")
    mailer = FakeMailer()
    return TestClient(create_app(sessions, publisher=lambda *a: True, mailer=mailer, send_async=False)), mailer


def test_order_request_is_saved_and_emailed_to_the_owner(sessions, monkeypatch):
    c, mailer = make(sessions, monkeypatch)
    r = c.post("/api/orders", json=ORDER)                      # public: no sign-in
    assert r.status_code == 200 and r.json()["ok"] is True
    with sessions() as s:
        o = s.scalar(select(Order))
        assert (o.company, o.email, o.systems, o.phone) == ("Desert Air Co.", "sam@desertair.example", 5, "480-555-0100")
    (to, subject, body), = mailer.sent
    assert to == "owner@example.com" and "Desert Air Co." in subject
    assert "Systems: 5" in body and "No payment was taken" in body


def test_order_request_checks_its_fields(sessions, monkeypatch):
    c, mailer = make(sessions, monkeypatch)
    assert c.post("/api/orders", json={**ORDER, "email": "not-an-email"}).status_code == 422
    assert c.post("/api/orders", json={**ORDER, "systems": 0}).status_code == 422
    assert c.post("/api/orders", json={**ORDER, "company": "  "}).status_code == 422
    assert mailer.sent == []


def test_bots_and_floods_are_not_saved(sessions, monkeypatch):
    c, mailer = make(sessions, monkeypatch)
    assert c.post("/api/orders", json={**ORDER, "website": "http://spam.example"}).json() == {"ok": True}
    with sessions() as s:
        assert s.scalar(select(Order)) is None
    for _ in range(5):
        assert c.post("/api/orders", json=ORDER).status_code == 200
    assert c.post("/api/orders", json=ORDER).status_code == 429
    assert len(mailer.sent) == 5
