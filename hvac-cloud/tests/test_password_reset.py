import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth
from hvaccloud.api import create_app
from hvaccloud.db import PasswordReset, System, SystemMember, User, utcnow

PW, NEW = "correct horse battery", "a much newer password"
XRW = {"X-Requested-With": "fullscope"}


class FakeMailer:
    def __init__(self, enabled=True):
        self.enabled, self.sent = enabled, []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


@pytest.fixture
def world(sessions, seeded):
    with sessions() as s, s.begin():
        home = s.scalar(select(System).where(System.site_id == "home"))
        s.add(User(email="tech@example.com", role="contractor", account_id=home.account_id, password_hash=auth.hash_password(PW)))
        owner = User(email="pat@example.com", role="homeowner", password_hash=auth.hash_password(PW))
        s.add(owner)
        s.flush()
        s.add(SystemMember(system_id=home.id, user_id=owner.id))
        ids = {"home": home.id, "owner": owner.id}
    mailer = FakeMailer()
    app = create_app(sessions, publisher=lambda *a: True, mailer=mailer, send_async=False)
    return {"app": app, "mailer": mailer, **ids}


def link_token(text):
    return text.split("#/reset/")[1].split()[0]


def test_forgot_password_emails_a_link_that_works_once(world, sessions):
    c, m = TestClient(world["app"]), world["mailer"]
    old = TestClient(world["app"])
    old.post("/api/auth/login", json={"email": "pat@example.com", "password": PW})
    assert c.post("/api/auth/forgot", json={"email": " Pat@Example.com "}).json() == {"ok": True, "email_enabled": True}
    to, subject, body = m.sent[0]
    assert to == "pat@example.com" and "Reset" in subject
    token = link_token(body)
    with sessions() as s:
        assert s.scalar(select(PasswordReset.token_hash)) != token          # only the hash is stored
    assert c.post("/api/auth/reset", json={"token": token, "password": "short"}).status_code == 422
    me = c.post("/api/auth/reset", json={"token": token, "password": NEW}).json()
    assert me["role"] == "homeowner" and c.get("/api/systems").status_code == 200   # signed in
    assert old.get("/api/systems").status_code == 401                               # old session ended
    assert c.post("/api/auth/reset", json={"token": token, "password": NEW + "!"}).status_code == 410   # used up
    fresh = TestClient(world["app"])
    assert fresh.post("/api/auth/login", json={"email": "pat@example.com", "password": PW}).status_code == 401
    assert fresh.post("/api/auth/login", json={"email": "pat@example.com", "password": NEW}).status_code == 200


def test_unknown_emails_get_the_same_answer_and_no_mail(world):
    c = TestClient(world["app"])
    assert c.post("/api/auth/forgot", json={"email": "nobody@example.com"}).json() == {"ok": True, "email_enabled": True}
    assert world["mailer"].sent == []


def test_reset_links_expire_and_are_limited_per_hour(world, sessions):
    c = TestClient(world["app"])
    for _ in range(5):
        c.post("/api/auth/forgot", json={"email": "pat@example.com"})
    assert len(world["mailer"].sent) == 3                                  # 3 an hour per address
    with sessions() as s:
        assert s.scalar(select(PasswordReset).where(PasswordReset.used_at.is_(None))) is not None
    token = link_token(world["mailer"].sent[-1][2])
    with sessions() as s, s.begin():
        for r in s.scalars(select(PasswordReset)):
            r.expires_at = utcnow() - dt.timedelta(minutes=1)
    assert c.post("/api/auth/reset", json={"token": token, "password": NEW}).status_code == 410


def test_contractor_can_make_a_link_without_email(sessions, seeded, world):
    app = create_app(sessions, publisher=lambda *a: True, mailer=FakeMailer(enabled=False), send_async=False)
    tech = TestClient(app)
    tech.post("/api/auth/login", json={"email": "tech@example.com", "password": PW})
    assert tech.post("/api/auth/forgot", json={"email": "pat@example.com"}).json()["email_enabled"] is False
    r = tech.post(f"/api/systems/{world['home']}/members/{world['owner']}/reset-link", headers=XRW).json()
    assert r["email"] == "pat@example.com" and "#/reset/" in r["url"]
    owner = TestClient(app)
    assert owner.post("/api/auth/reset", json={"token": r["url"].split("#/reset/")[1], "password": NEW}).status_code == 200
    # not for people outside the system, and not for homeowners themselves
    assert tech.post(f"/api/systems/{world['home']}/members/999/reset-link", headers=XRW).status_code == 404
    assert owner.post(f"/api/systems/{world['home']}/members/{world['owner']}/reset-link", headers=XRW).status_code == 403
