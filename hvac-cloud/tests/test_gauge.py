import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import auth, calc, gauge
from hvaccloud.api import create_app
from hvaccloud.db import System, SystemMember, User
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

PW = "correct horse battery"
XRW = {"X-Requested-With": "fullscope"}
# What the gauges read for COOL_OUT / COOL_IN: suction 2 psi lower, liquid line 1 °F warmer, the rest equal
GAUGES = {"p_low": 118.0, "p_high": 360.0, "t_suc": 52.0, "t_liq": 102.0, "t_ret": 76.0, "t_sup": 57.0}


@pytest.fixture
def world(sessions, seeded, tables):
    with sessions() as s, s.begin():
        home = s.scalar(select(System).where(System.site_id == "home"))
        s.add(User(email="tech@example.com", name="Tyler", role="contractor", account_id=home.account_id,
                   password_hash=auth.hash_password(PW)))
        owner = User(email="pat@example.com", role="homeowner", password_hash=auth.hash_password(PW))
        s.add(owner)
        s.flush()
        s.add(SystemMember(system_id=home.id, user_id=owner.id))
        sid = home.id
    app = create_app(sessions, publisher=lambda *a: True, tables=tables)
    tech, owner = TestClient(app), TestClient(app)
    tech.post("/api/auth/login", json={"email": "tech@example.com", "password": PW})
    owner.post("/api/auth/login", json={"email": "pat@example.com", "password": PW})
    return {"tech": tech, "owner": owner, "sid": sid, "key2": {"X-API-Key": seeded["key2"]}}


def cooling(sessions, tables, at):
    i = Ingest(sessions, tables)
    i.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN).encode(), now=at - 1)
    i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=at)


def test_gauge_side_matches_calc(tables):
    """Gauges that read exactly what the sensors read give the same saturation, superheat and subcooling."""
    nodes = {"outdoor": {"online": True, "data": COOL_OUT}, "indoor": {"online": True, "data": COOL_IN}}
    cfg = {"refrigerant": "R-410A", "heat_pump": True, "ob_energized": "cool", "atm_psia": 14.0}
    snap, _ = calc.compute(cfg, nodes, None, 0, tables)
    same = {"p_low": 120.0, "p_high": 360.0, "t_suc": 52.0, "t_liq": 101.0, "t_ret": 76.0, "t_sup": 57.0}
    g = gauge.derive(same, "R-410A", 14.0, tables)
    assert all(g[k] == snap[k] for k in gauge.COMPARED)
    assert gauge.differences(gauge.system_side(snap), g) == {k: 0.0 for k in gauge.COMPARED}


def test_partial_readings(tables):
    g = gauge.derive({"t_ret": 75.5, "t_sup": 56.0}, "R-410A", 14.0, tables)
    assert g["dt"] == 19.5 and g["sh"] is None and g["sc"] is None and g["sat_low"] is None


def test_add_list_and_delete(world, sessions, tables):
    t, sid = world["tech"], world["sid"]
    cooling(sessions, tables, time.time() - 30)
    c = t.post(f"/api/systems/{sid}/gauge-checks", json={**GAUGES, "technician": " Tyler ", "note": "Fieldpiece SMAN"},
               headers=XRW).json()
    assert c["technician"] == "Tyler" and c["refrigerant"] == "R-410A" and c["atm_psia"] == 14.0
    assert c["system"]["mode"] == "cooling" and c["system"]["p_low"] == 120.0
    assert c["diff"]["p_low"] == 2.0 and c["diff"]["t_liq"] == -1.0 and c["diff"]["dt"] == 0.0
    assert c["diff"]["sh"] < 0 and c["diff"]["sc"] == 1.0          # lower gauge suction -> higher gauge superheat
    t.post(f"/api/systems/{sid}/gauge-checks", json={"p_low": 121.0}, headers=XRW)
    listed = t.get(f"/api/systems/{sid}/gauge-checks").json()
    assert [x["gauge"]["p_low"] for x in listed["checks"]] == [121.0, 118.0]       # newest first
    assert listed["summary"]["p_low"] == {"n": 2, "mean": 0.5, "max": 2.0}
    assert listed["summary"]["t_liq"] == {"n": 1, "mean": -1.0, "max": -1.0}
    assert listed["summary"]["sat_high"]["n"] == 1
    assert t.delete(f"/api/systems/{sid}/gauge-checks/{c['id']}", headers=XRW).status_code == 200
    assert len(t.get(f"/api/systems/{sid}/gauge-checks").json()["checks"]) == 1
    assert t.delete(f"/api/systems/{sid}/gauge-checks/{c['id']}", headers=XRW).status_code == 404


def test_minutes_ago_picks_the_nearest_snapshot(world, sessions, tables):
    t, sid = world["tech"], world["sid"]
    cooling(sessions, tables, time.time() - 10 * 60)
    assert t.post(f"/api/systems/{sid}/gauge-checks", json=GAUGES, headers=XRW).status_code == 409
    c = t.post(f"/api/systems/{sid}/gauge-checks", json={**GAUGES, "minutes_ago": 9}, headers=XRW)
    assert c.status_code == 200 and c.json()["system"]["p_low"] == 120.0


def test_validation_and_access(world, sessions, tables):
    t, sid = world["tech"], world["sid"]
    cooling(sessions, tables, time.time())
    assert t.post(f"/api/systems/{sid}/gauge-checks", json={"note": "nothing"}, headers=XRW).status_code == 422
    assert t.post(f"/api/systems/{sid}/gauge-checks", json={"p_low": 900}, headers=XRW).status_code == 422
    assert t.post(f"/api/systems/{sid}/gauge-checks", json={**GAUGES, "minutes_ago": 61}, headers=XRW).status_code == 422
    assert world["owner"].get(f"/api/systems/{sid}/gauge-checks").status_code == 403
    assert world["owner"].post(f"/api/systems/{sid}/gauge-checks", json=GAUGES, headers=XRW).status_code == 403
    assert t.get(f"/api/systems/{sid}/gauge-checks", headers=world["key2"]).status_code == 404
