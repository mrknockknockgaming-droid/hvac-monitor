"""Tests for the fixes from the October 2026 review of the cloud code."""
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth
from hvaccloud.api import create_app
from hvaccloud.db import Invite, System, User, UserSession

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}


@pytest.fixture
def app(sessions, seeded):
    with sessions() as s, s.begin():
        acct = s.scalar(select(System).where(System.site_id == "home")).account_id
        for email in ("tech@example.com", "helper@example.com"):
            s.add(User(email=email, role="contractor", account_id=acct, password_hash=auth.hash_password(PW)))
    return create_app(sessions, publisher=lambda *a: True)


def signed_in(app, email="tech@example.com"):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def test_changing_the_password_signs_out_other_sessions(app, sessions):
    laptop, phone = signed_in(app), signed_in(app)
    assert laptop.post("/api/auth/password", json={"current": PW, "new": "a brand new one"}, headers=XRW).status_code == 200
    assert laptop.get("/api/auth/me").status_code == 200                 # the browser that changed it stays in
    assert phone.get("/api/auth/me").status_code == 401                  # everyone else is out


def test_removing_a_teammate_deletes_the_sign_in_and_signs_them_out(app, sessions):
    tech, helper = signed_in(app), signed_in(app, "helper@example.com")
    with sessions() as s:
        hid = s.scalar(select(User.id).where(User.email == "helper@example.com"))
        me = s.scalar(select(User.id).where(User.email == "tech@example.com"))
    helper.post("/api/account/invites", json={"email": "new@example.com"}, headers=XRW)    # leaves an invite behind
    assert tech.delete(f"/api/account/users/{me}", headers=XRW).status_code == 409        # not yourself
    assert tech.delete(f"/api/account/users/{hid}", headers=XRW).status_code == 200
    assert helper.get("/api/systems").status_code == 401
    with sessions() as s:
        assert s.get(User, hid) is None and s.scalar(select(UserSession).where(UserSession.user_id == hid)) is None
        assert s.scalar(select(Invite.invited_by)) is None                # the invite they sent still works
    assert TestClient(app).post("/api/auth/login", json={"email": "helper@example.com", "password": PW}).status_code == 401


def test_teammates_of_other_accounts_and_homeowners_cannot_be_removed(app, sessions, seeded):
    with sessions() as s, s.begin():
        owner = User(email="o@example.com", role="homeowner", password_hash=auth.hash_password(PW))
        s.add(owner)
        s.flush()
        oid = owner.id
        hid = s.scalar(select(User.id).where(User.email == "helper@example.com"))
    other = TestClient(app)
    assert other.delete(f"/api/account/users/{hid}", headers={"X-API-Key": seeded["key2"]}).status_code == 404
    assert signed_in(app).delete(f"/api/account/users/{oid}", headers=XRW).status_code == 404


def test_invite_tokens_are_not_in_urls(app):
    paths = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/api/invites/lookup" in paths and "/api/invites/accept" in paths
    assert not any("{token}" in p for p in paths)


def test_unknown_email_costs_the_same_as_a_wrong_password(app, monkeypatch):
    checked = []
    real = auth.check_password
    monkeypatch.setattr(auth, "check_password", lambda pw, stored: checked.append(stored) or real(pw, stored))
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"}).status_code == 401
    assert checked == [auth.DUMMY_HASH]                                  # a real scrypt check still ran


def test_done_dates_cannot_be_in_the_future(app):
    c = signed_in(app)
    sid = c.get("/api/systems").json()[0]["id"]
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    assert c.post(f"/api/systems/{sid}/service/items/filter/done", json={"date": future}, headers=XRW).status_code == 422
    assert c.patch(f"/api/systems/{sid}/service/items/tuneup", json={"last_done": "1999-01-01"}, headers=XRW).status_code == 422
    today = dt.date.today().isoformat()
    assert c.post(f"/api/systems/{sid}/service/items/filter/done", json={"date": today}, headers=XRW).status_code == 200


def test_throttle_forgets_old_entries():
    t = auth.Throttle(limit=3, window=60)
    for k in range(1200):
        t.fail(f"user{k}@example.com", now=0)
    t.fail("late@example.com", now=1000)                                 # every earlier failure has aged out
    assert len(t._fails) == 1


def test_history_is_limited_to_a_month(app):
    c = signed_in(app)
    sid = c.get("/api/systems").json()[0]["id"]
    assert c.get(f"/api/systems/{sid}/history?minutes={60 * 24 * 31}").status_code == 200
    assert c.get(f"/api/systems/{sid}/history?minutes={60 * 24 * 32}").status_code == 422
