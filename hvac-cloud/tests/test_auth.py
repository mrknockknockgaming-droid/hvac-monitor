import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth
from hvaccloud.api import create_app
from hvaccloud.db import System, SystemMember, User, UserSession, hash_key, utcnow

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}


@pytest.fixture
def people(sessions, seeded):
    """A contractor on account 1 (owns 'home') and a homeowner who is a member of 'home' only."""
    with sessions() as s, s.begin():
        home = s.scalar(select(System).where(System.site_id == "home"))
        tech = User(email="tech@example.com", role="contractor", account_id=home.account_id, password_hash=auth.hash_password(PW))
        owner = User(email="owner@example.com", role="homeowner", password_hash=auth.hash_password(PW))
        s.add_all([tech, owner])
        s.flush()
        s.add(SystemMember(system_id=home.id, user_id=owner.id))
        other = s.scalar(select(System).where(System.site_id == "other")).id
        return {"home": home.id, "other": other}


def client(sessions):
    return TestClient(create_app(sessions, publisher=lambda *a: True))


def login(c, email, password=PW):
    return c.post("/api/auth/login", json={"email": email, "password": password})


def test_password_hashing():
    h = auth.hash_password(PW)
    assert h.startswith("scrypt$") and PW not in h
    assert auth.check_password(PW, h) and not auth.check_password(PW + "x", h)
    assert not auth.check_password(PW, None) and not auth.check_password(PW, "garbage")
    assert auth.hash_password(PW) != h                                       # salted
    assert auth.password_problem("short") and auth.password_problem(" padded password ") and not auth.password_problem(PW)


def test_contractor_signs_in_and_sees_the_account(sessions, people):
    c = client(sessions)
    r = login(c, "Tech@Example.com ")                                      # case and spaces don't matter
    assert r.status_code == 200 and r.json()["role"] == "contractor"
    assert auth.COOKIE in r.cookies and "httponly" in r.headers["set-cookie"].lower()
    assert [x["site_id"] for x in c.get("/api/systems").json()] == ["home"]
    assert c.get("/api/auth/me").json()["user"]["email"] == "tech@example.com"
    cmd = {"node": "outdoor", "cmd": {"cmd": "status"}}
    assert c.post(f"/api/systems/{people['home']}/commands", json=cmd).status_code == 403   # no X-Requested-With
    assert c.get(f"/api/systems/{people['other']}").status_code == 404


def test_homeowner_is_read_only_except_the_filter(sessions, people):
    c = client(sessions)
    assert login(c, "owner@example.com").json()["role"] == "homeowner"
    sid = people["home"]
    assert [x["id"] for x in c.get("/api/systems").json()] == [sid]
    for path in ("", "/latest", "/summary", "/history", "/alerts", "/service"):
        assert c.get(f"/api/systems/{sid}{path}").status_code == 200, path
    for method, path, body in [("get", "/commands", None), ("get", "/alert-settings", None),
                               ("patch", "", {"atm_psia": 14.0}), ("put", "/service/contractor", {"name": "x"}),
                               ("post", "/commands", {"node": "outdoor", "cmd": {"cmd": "status"}}),
                               ("post", "/service/items/tuneup/done", {})]:
        r = c.request(method.upper(), f"/api/systems/{sid}{path}", json=body, headers=XRW)
        assert r.status_code == 403, (method, path, r.status_code)
    assert c.post(f"/api/systems/{sid}/service/items/filter/done", json={"date": "2026-10-01"}, headers=XRW).status_code == 200
    assert c.get(f"/api/systems/{people['other']}/latest").status_code == 404


def test_wrong_password_and_lockout(sessions, people):
    c = client(sessions)
    assert login(c, "tech@example.com", "nope").status_code == 401
    assert login(c, "nobody@example.com").status_code == 401             # same answer for unknown emails
    for _ in range(7):
        login(c, "tech@example.com", "nope")
    assert login(c, "tech@example.com").status_code == 429               # even the right password, for now


def test_logout_and_expired_sessions(sessions, people):
    c = client(sessions)
    login(c, "tech@example.com")
    assert c.post("/api/auth/logout", headers=XRW).status_code == 200
    assert c.get("/api/systems").status_code == 401
    with sessions() as s, s.begin():
        uid = s.scalar(select(User.id).where(User.email == "tech@example.com"))
        token = auth.new_session(s, uid, now=utcnow() - dt.timedelta(days=60))
    c.cookies.set(auth.COOKIE, token)
    assert c.get("/api/systems").status_code == 401
    with sessions() as s:
        assert s.get(UserSession, hash_key(token)) is None                 # cleaned up


def test_change_password(sessions, people):
    c = client(sessions)
    login(c, "owner@example.com")
    bad = c.post("/api/auth/password", json={"current": "wrong", "new": "another long one"}, headers=XRW)
    assert bad.status_code == 401
    assert c.post("/api/auth/password", json={"current": PW, "new": "short"}, headers=XRW).status_code == 422
    assert c.post("/api/auth/password", json={"current": PW, "new": "another long one"}, headers=XRW).status_code == 200
    c2 = client(sessions)
    assert login(c2, "owner@example.com").status_code == 401
    assert login(c2, "owner@example.com", "another long one").status_code == 200


def test_api_key_still_works_and_needs_no_extra_header(sessions, seeded, people):
    c = client(sessions)
    h = {"X-API-Key": seeded["key1"]}
    assert c.get("/api/auth/me", headers=h).json() == {"role": "contractor", "via": "key", "user": None,
                                                       "account": {"id": 1, "name": "Tyler"}}
    r = c.post(f"/api/systems/{people['home']}/commands", headers=h, json={"node": "outdoor", "cmd": {"cmd": "status"}})
    assert r.status_code != 403
