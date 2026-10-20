"""Three access levels: admins (fleet page, team and technician pages), technicians (technician
pages only) and homeowners (their home page only). Each level is checked by the server, not just
hidden in the web app."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from hvaccloud import auth
from hvaccloud.api import create_app
from hvaccloud.db import Account, System, SystemMember, User, ensure_admins, init_db, session_factory

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}


@pytest.fixture
def world(sessions, seeded):
    with sessions() as s, s.begin():
        home = s.scalar(select(System).where(System.site_id == "home"))
        boss = User(email="boss@example.com", role="contractor", account_id=home.account_id, admin=True,
                    password_hash=auth.hash_password(PW))
        tech = User(email="tech@example.com", role="contractor", account_id=home.account_id,
                    password_hash=auth.hash_password(PW))
        owner = User(email="owner@example.com", role="homeowner", password_hash=auth.hash_password(PW))
        s.add_all([boss, tech, owner])
        s.flush()
        s.add(SystemMember(system_id=home.id, user_id=owner.id))
        ids = {"home": home.id, "boss": boss.id, "tech": tech.id}
    app = create_app(sessions, publisher=lambda *a: True)

    def browser(email):
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
        return c
    return {**ids, "browser": browser}


def test_each_sign_in_says_its_level(world):
    for email, level in (("boss@example.com", "admin"), ("tech@example.com", "technician"), ("owner@example.com", "homeowner")):
        assert world["browser"](email).get("/api/auth/me").json()["level"] == level


def test_fleet_and_team_are_for_admins_only(world):
    boss, tech, owner = (world["browser"](e) for e in ("boss@example.com", "tech@example.com", "owner@example.com"))
    assert boss.get("/api/fleet").status_code == 200
    assert boss.get("/api/account/people").status_code == 200
    for c in (tech, owner):
        assert c.get("/api/fleet").status_code == 403
        assert c.get("/api/account/people").status_code == 403
        assert c.post("/api/account/invites", json={"email": "x@example.com"}, headers=XRW).status_code == 403
        assert c.delete(f"/api/account/users/{world['boss']}", headers=XRW).status_code == 403
        assert c.patch(f"/api/account/users/{world['tech']}", json={"admin": True}, headers=XRW).status_code == 403


def test_technicians_keep_the_technician_pages(world):
    tech, sid = world["browser"]("tech@example.com"), world["home"]
    assert [x["id"] for x in tech.get("/api/systems").json()] == [sid]
    assert tech.get(f"/api/systems/{sid}/equipment").status_code == 200
    assert tech.post(f"/api/systems/{sid}/commands", json={"node": "outdoor", "cmd": {"cmd": "status"}},
                     headers=XRW).status_code != 403
    r = tech.post(f"/api/systems/{sid}/invites", json={"email": "new-owner@example.com"}, headers=XRW).json()
    assert r["status"] == "invited"                                   # homeowner links are technician work
    invite_id = tech.get(f"/api/systems/{sid}/people").json()["invites"][0]["id"]
    assert tech.delete(f"/api/invites/{invite_id}", headers=XRW).status_code == 200


def test_technicians_cannot_revoke_teammate_invites(world):
    boss, tech = world["browser"]("boss@example.com"), world["browser"]("tech@example.com")
    boss.post("/api/account/invites", json={"email": "new@example.com"}, headers=XRW)
    invite_id = boss.get("/api/account/people").json()["invites"][0]["id"]
    assert tech.delete(f"/api/invites/{invite_id}", headers=XRW).status_code == 404
    assert boss.delete(f"/api/invites/{invite_id}", headers=XRW).status_code == 200


def test_admin_invite_joins_as_admin(world):
    boss = world["browser"]("boss@example.com")
    r = boss.post("/api/account/invites", json={"email": "office@example.com", "admin": True}, headers=XRW).json()
    assert boss.get("/api/account/people").json()["invites"][0]["admin"] is True
    guest = TestClient(boss.app)
    me = guest.post("/api/invites/accept", json={"token": r["url"].split("#/invite/")[1], "password": PW}).json()
    assert me["level"] == "admin" and guest.get("/api/fleet").status_code == 200

    r = boss.post("/api/account/invites", json={"email": "field@example.com"}, headers=XRW).json()
    guest = TestClient(boss.app)
    me = guest.post("/api/invites/accept", json={"token": r["url"].split("#/invite/")[1], "password": PW}).json()
    assert me["level"] == "technician" and guest.get("/api/fleet").status_code == 403


def test_admins_promote_and_demote_but_keep_one(world):
    boss = world["browser"]("boss@example.com")
    assert boss.patch(f"/api/account/users/{world['boss']}", json={"admin": False}, headers=XRW).status_code == 409
    assert boss.patch(f"/api/account/users/{world['tech']}", json={"admin": True}, headers=XRW).json()["admin"] is True
    tech = world["browser"]("tech@example.com")
    assert tech.get("/api/fleet").status_code == 200
    people = {u["email"]: u["admin"] for u in boss.get("/api/account/people").json()["users"]}
    assert people == {"boss@example.com": True, "tech@example.com": True}
    assert tech.patch(f"/api/account/users/{world['boss']}", json={"admin": False}, headers=XRW).json()["admin"] is False
    assert boss.get("/api/fleet").status_code == 403                 # takes effect at once


def test_other_accounts_teammates_are_out_of_reach(world, sessions):
    with sessions() as s, s.begin():
        other = s.scalar(select(System).where(System.site_id == "other")).account_id
        stranger = User(email="stranger@example.com", role="contractor", account_id=other, password_hash=auth.hash_password(PW))
        s.add(stranger)
        s.flush()
        sid = stranger.id
    boss = world["browser"]("boss@example.com")
    assert boss.patch(f"/api/account/users/{sid}", json={"admin": True}, headers=XRW).status_code == 404


def test_existing_accounts_get_their_first_contractor_as_admin():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    init_db(engine)
    sessions = session_factory(engine)
    with sessions() as s, s.begin():
        acct = Account(name="Old", email="old@example.com")
        s.add(acct)
        s.flush()
        s.add_all([User(email="first@example.com", role="contractor", account_id=acct.id),
                   User(email="second@example.com", role="contractor", account_id=acct.id),
                   User(email="home@example.com", role="homeowner")])
    init_db(engine)                                                     # what a restart after the upgrade does
    with sessions() as s:
        admins = {u.email: bool(u.admin) for u in s.scalars(select(User))}
        assert admins == {"first@example.com": True, "second@example.com": False, "home@example.com": False}
        ensure_admins(s)                                                # already has one: nothing changes
        assert sum(bool(u.admin) for u in s.scalars(select(User))) == 1
