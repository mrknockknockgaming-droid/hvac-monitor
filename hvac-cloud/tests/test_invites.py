import datetime as dt
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import alerts, auth
from hvaccloud.api import create_app
from hvaccloud.db import Invite, System, SystemMember, User, utcnow
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}


class FakeMailer:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


@pytest.fixture
def world(sessions, seeded):
    with sessions() as s, s.begin():
        home = s.scalar(select(System).where(System.site_id == "home"))
        s.add(User(email="tech@example.com", role="contractor", account_id=home.account_id, password_hash=auth.hash_password(PW)))
        sid, other = home.id, s.scalar(select(System.id).where(System.site_id == "other"))
    mailer = FakeMailer()
    c = TestClient(create_app(sessions, publisher=lambda *a: True, mailer=mailer, send_async=False))
    c.post("/api/auth/login", json={"email": "tech@example.com", "password": PW})
    return {"c": c, "mailer": mailer, "home": sid, "other": other, "key2": {"X-API-Key": seeded["key2"]}}


def token_of(url):
    return url.split("#/invite/")[1]


def test_homeowner_invite_round_trip(world, sessions):
    c, sid = world["c"], world["home"]
    r = c.post(f"/api/systems/{sid}/invites", json={"email": " Owner@Example.com "}, headers=XRW).json()
    assert r["status"] == "invited" and r["emailed"] is True and "#/invite/" in r["url"]
    to, subject, body = world["mailer"].sent[0]
    assert to == "owner@example.com" and "Home" in subject and r["url"] in body
    with sessions() as s:
        assert r["url"].split("/")[-1] not in [i.token_hash for i in s.scalars(select(Invite))]   # only a hash stored
    people = c.get(f"/api/systems/{sid}/people").json()
    assert people["members"] == [] and [i["email"] for i in people["invites"]] == ["owner@example.com"]

    guest = TestClient(c.app)                                          # the homeowner's browser
    info = guest.post("/api/invites/lookup", json={"token": token_of(r['url'])}).json()
    assert info == {**info, "email": "owner@example.com", "role": "homeowner", "system": "Home", "has_user": False}
    assert guest.post("/api/invites/accept", json={"token": token_of(r['url']), "password": "short"}).status_code == 422
    me = guest.post("/api/invites/accept", json={"token": token_of(r['url']), "name": "Pat", "password": PW}).json()
    assert me["role"] == "homeowner" and me["user"]["name"] == "Pat"
    assert [x["id"] for x in guest.get("/api/systems").json()] == [sid]           # signed in, sees the system
    assert guest.post("/api/invites/lookup", json={"token": token_of(r['url'])}).status_code == 404     # used up
    people = c.get(f"/api/systems/{sid}/people").json()
    assert [m["email"] for m in people["members"]] == ["owner@example.com"] and people["invites"] == []


def test_existing_homeowner_is_added_directly_and_can_be_removed(world, sessions):
    c, sid = world["c"], world["home"]
    with sessions() as s, s.begin():
        u = User(email="pat@example.com", role="homeowner", password_hash=auth.hash_password(PW))
        s.add(u)
        s.flush()
        uid = u.id
    assert c.post(f"/api/systems/{sid}/invites", json={"email": "pat@example.com"}, headers=XRW).json()["status"] == "added"
    assert world["mailer"].sent == []
    assert c.delete(f"/api/systems/{sid}/members/{uid}", headers=XRW).status_code == 200
    with sessions() as s:
        assert s.get(SystemMember, (sid, uid)) is None
    assert c.post(f"/api/systems/{sid}/invites", json={"email": "tech@example.com"}, headers=XRW).status_code == 409
    assert c.post(f"/api/systems/{sid}/invites", json={"email": "not-an-email"}, headers=XRW).status_code == 422


def test_expired_revoked_and_foreign_invites(world, sessions):
    c, sid = world["c"], world["home"]
    url = c.post(f"/api/systems/{sid}/invites", json={"email": "a@example.com"}, headers=XRW).json()["url"]
    with sessions() as s, s.begin():
        s.scalar(select(Invite)).expires_at = utcnow() - dt.timedelta(minutes=1)
    assert TestClient(c.app).post("/api/invites/lookup", json={"token": token_of(url)}).status_code == 410
    url2 = c.post(f"/api/systems/{sid}/invites", json={"email": "a@example.com"}, headers=XRW).json()["url"]
    assert TestClient(c.app).post("/api/invites/lookup", json={"token": token_of(url)}).status_code == 404   # replaced by the new one
    iid = c.get(f"/api/systems/{sid}/people").json()["invites"][0]["id"]
    assert c.delete(f"/api/invites/{iid}", headers=world["key2"]).status_code == 404     # another account
    assert c.delete(f"/api/invites/{iid}", headers=XRW).status_code == 200
    assert TestClient(c.app).post("/api/invites/lookup", json={"token": token_of(url2)}).status_code == 404
    assert c.post(f"/api/systems/{world['other']}/invites", json={"email": "b@example.com"}, headers=XRW).status_code == 404


def test_teammate_invite(world):
    c = world["c"]
    r = c.post("/api/account/invites", json={"email": "helper@example.com"}, headers=XRW).json()
    guest = TestClient(c.app)
    me = guest.post("/api/invites/accept", json={"token": token_of(r['url']), "password": PW}).json()
    assert me["role"] == "contractor" and me["account"]["name"] == "Tyler"
    assert [x["site_id"] for x in guest.get("/api/systems").json()] == ["home"]
    assert {u["email"] for u in c.get("/api/account/people").json()["users"]} == {"tech@example.com", "helper@example.com"}
    assert c.post("/api/account/invites", json={"email": "helper@example.com"}, headers=XRW).status_code == 409


def test_homeowners_cannot_invite_or_see_the_fleet(world, sessions):
    c, sid = world["c"], world["home"]
    url = c.post(f"/api/systems/{sid}/invites", json={"email": "o@example.com"}, headers=XRW).json()["url"]
    owner = TestClient(c.app)
    owner.post("/api/invites/accept", json={"token": token_of(url), "password": PW})
    assert owner.post(f"/api/systems/{sid}/invites", json={"email": "x@example.com"}, headers=XRW).status_code == 403
    assert owner.post("/api/account/invites", json={"email": "x@example.com"}, headers=XRW).status_code == 403
    assert owner.get("/api/fleet").status_code == 403


def test_fleet_sorts_by_attention(world, sessions, tables):
    c = world["c"]
    with sessions() as s, s.begin():
        acct = s.scalar(select(System).where(System.site_id == "home")).account_id
        s.add_all([System(account_id=acct, name="Alpha", site_id="alpha"), System(account_id=acct, name="Zulu", site_id="zulu")])
    i, now = Ingest(sessions, tables), time.time()
    for site in ("home", "alpha"):                                     # zulu never reports
        i.handle(f"hvac/{site}/indoor/telemetry", json.dumps(COOL_IN).encode(), now=now - 3)
        i.handle(f"hvac/{site}/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=now - 2)
    with sessions() as s, s.begin():                                   # alpha has a raised, unhandled fault
        alerts.sync(s, s.scalar(select(System).where(System.site_id == "alpha")),
                    [{"level": "alert", "code": "sh_low", "text": "Superheat 1.5°F"}], now, 0)
    rows = c.get("/api/fleet").json()
    assert [(r["site_id"], r["level"]) for r in rows] == [("alpha", "fault"), ("zulu", "offline"), ("home", "ok")]
    assert rows[0]["open_alerts"][0]["code"] == "sh_low" and rows[2]["nodes"] == {"seen": 2, "online": 2}
    assert rows[2]["mode"] == "cooling" and rows[2]["maintenance"]["filter"]["status"] == "unset"
