import datetime as dt
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth
from hvaccloud.api import create_app
from hvaccloud.db import System, SystemMember, User
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}
TODAY = dt.date.today()


@pytest.fixture
def world(sessions, seeded):
    with sessions() as s, s.begin():
        home = s.scalar(select(System).where(System.site_id == "home"))
        s.add(User(email="tech@example.com", name="Tyler", role="contractor", account_id=home.account_id,
                   password_hash=auth.hash_password(PW)))
        owner = User(email="pat@example.com", role="homeowner", password_hash=auth.hash_password(PW))
        s.add(owner)
        s.flush()
        s.add(SystemMember(system_id=home.id, user_id=owner.id))
        sid = home.id
    app = create_app(sessions, publisher=lambda *a: True)
    tech, owner = TestClient(app), TestClient(app)
    tech.post("/api/auth/login", json={"email": "tech@example.com", "password": PW})
    owner.post("/api/auth/login", json={"email": "pat@example.com", "password": PW})
    return {"tech": tech, "owner": owner, "sid": sid, "key2": {"X-API-Key": seeded["key2"]}}


def visit(**kw):
    return {"date": TODAY.isoformat(), "kind": "repair", "work": "Replaced the run capacitor (45/5 µF).", **kw}


def test_log_list_and_delete(world):
    t, sid = world["tech"], world["sid"]
    v = t.post(f"/api/systems/{sid}/visits", json=visit(technician="Tyler"), headers=XRW).json()
    assert v["kind_label"] == "Repair" and v["readings"] is None
    t.post(f"/api/systems/{sid}/visits", json=visit(date=(TODAY - dt.timedelta(days=30)).isoformat(), kind="inspection", work="Checked"), headers=XRW)
    listed = world["owner"].get(f"/api/systems/{sid}/visits").json()                # homeowners can read them
    assert [x["kind"] for x in listed] == ["repair", "inspection"]                  # newest first
    assert world["owner"].post(f"/api/systems/{sid}/visits", json=visit(), headers=XRW).status_code == 403
    assert world["owner"].delete(f"/api/systems/{sid}/visits/{v['id']}", headers=XRW).status_code == 403
    assert t.delete(f"/api/systems/{sid}/visits/{v['id']}", headers=XRW).status_code == 200
    assert len(t.get(f"/api/systems/{sid}/visits").json()) == 1
    assert t.get(f"/api/systems/{sid}/visits", headers=world["key2"]).status_code == 404


def test_tuneup_and_filter_reset_the_reminders_but_never_go_back(world):
    t, sid = world["tech"], world["sid"]
    t.post(f"/api/systems/{sid}/visits", json=visit(kind="tuneup", work="Spring tune-up", filter_changed=True), headers=XRW)
    items = {i["kind"]: i for i in t.get(f"/api/systems/{sid}/service").json()["items"]}
    assert items["tuneup"]["last_done"] == TODAY.isoformat() and items["filter"]["last_done"] == TODAY.isoformat()
    old = (TODAY - dt.timedelta(days=100)).isoformat()                               # an old visit logged late
    t.post(f"/api/systems/{sid}/visits", json=visit(kind="tuneup", date=old, work="Last year's"), headers=XRW)
    items = {i["kind"]: i for i in t.get(f"/api/systems/{sid}/service").json()["items"]}
    assert items["tuneup"]["last_done"] == TODAY.isoformat()


def test_validation(world):
    t, sid = world["tech"], world["sid"]
    future = (TODAY + dt.timedelta(days=10)).isoformat()
    assert t.post(f"/api/systems/{sid}/visits", json=visit(date=future), headers=XRW).status_code == 422
    assert t.post(f"/api/systems/{sid}/visits", json=visit(work=""), headers=XRW).status_code == 422
    assert t.post(f"/api/systems/{sid}/visits", json=visit(kind="party"), headers=XRW).status_code == 422


def test_attach_readings_needs_fresh_data(world, sessions, tables):
    t, sid = world["tech"], world["sid"]
    assert t.post(f"/api/systems/{sid}/visits", json=visit(attach_readings=True), headers=XRW).status_code == 409
    i, now = Ingest(sessions, tables), time.time()
    i.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN).encode(), now=now - 2)
    i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=now - 1)
    v = t.post(f"/api/systems/{sid}/visits", json=visit(attach_readings=True), headers=XRW).json()
    r = v["readings"]
    assert r["mode"] == "cooling" and r["sh"] is not None and r["sc"] is not None and r["time"]
