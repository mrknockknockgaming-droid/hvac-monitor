import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import equipment
from hvaccloud.api import create_app
from hvaccloud.db import Equipment, Snapshot, System
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

T0 = 1_790_000_000.0


def snap(sc, mode="cooling", run_min=15, flags=None):
    return {"mode": mode, "run_min": run_min, "sc": sc, "flags": list(flags or [])}


def txv(target=10.0, tol=3.0, metering="txv"):
    return Equipment(system_id=1, metering=metering, sc_target=target, sc_tolerance=tol)


def codes(s):
    return [f["code"] for f in s["flags"]]


def test_atmosphere_from_elevation():
    assert equipment.atm_from_elevation(0) == 14.7
    assert 14.0 <= equipment.atm_from_elevation(1240) <= 14.1          # Mesa, AZ


def test_generic_limits_leave_calc_flags_alone():
    s = equipment.apply(snap(22, flags=[{"code": "sc_high", "text": "x"}]), None)
    assert codes(s) == ["sc_high"] and s["targets"]["sc"]["source"] == "generic"
    for eq in (txv(metering="piston"), Equipment(system_id=1, metering="txv")):   # piston, or no target entered
        assert equipment.apply(snap(5), eq)["targets"]["sc"]["source"] == "generic"


def test_nameplate_target_replaces_generic_subcooling_flags():
    low = equipment.apply(snap(5.5), txv())                             # generic 3-20 would call this fine
    assert codes(low) == ["sc_low"] and "nameplate target 10 ± 3°F" in low["flags"][0]["text"]
    ok = equipment.apply(snap(12, flags=[{"code": "sc_high", "text": "generic"}]), txv(target=14))
    assert codes(ok) == []
    high = equipment.apply(snap(18, flags=[{"code": "dt_low", "text": "keep me"}]), txv())
    assert codes(high) == ["dt_low", "sc_high"]
    assert high["targets"]["sc"] == {"lo": 7.0, "hi": 13.0, "source": "nameplate", "target": 10.0}


def test_target_only_judged_in_steady_cooling():
    assert equipment.apply(snap(5, run_min=5), txv())["flags"] == []                       # still settling
    assert equipment.apply(snap(5, mode="heating"), txv())["targets"]["sc"]["source"] == "generic"


def test_ingest_uses_the_equipment_target(sessions, seeded, tables):
    with sessions() as s, s.begin():
        sid = s.scalar(select(System.id).where(System.site_id == "home"))
        s.add(Equipment(system_id=sid, metering="txv", sc_target=12.0, sc_tolerance=2.0))
    i = Ingest(sessions, tables)
    for k in range(72):                                                  # 12 min of steady cooling
        i.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN).encode(), now=T0 + k * 10)
        last = i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=T0 + k * 10 + 5)
    assert last["sc"] < 10 and "sc_low" in codes(last)                    # inside 3-20 but under 12 ± 2
    with sessions() as s:
        stored = s.scalars(select(Snapshot.data).order_by(Snapshot.time.desc())).first()
    assert stored["targets"]["sc"]["source"] == "nameplate"


def test_equipment_api(sessions, seeded):
    c = TestClient(create_app(sessions, publisher=lambda *a: True))
    h = {"X-API-Key": seeded["key1"]}
    sid = c.get("/api/systems", headers=h).json()[0]["id"]
    v = c.get(f"/api/systems/{sid}/equipment", headers=h).json()
    assert v["metering"] is None and v["sc_tolerance"] == 3.0 and v["refrigerant"] == "R-410A"
    body = {"system_type": "split_hp", "metering": "txv", "tonnage": 3.5, "sc_target": 10, "sc_tolerance": None,
            "rated_btuh": 41500, "rated_cfm": 1400, "max_esp": 0.5, "elevation_ft": 1240,
            "refrigerant": "R-410A", "heat_pump": True, "ob_energized": "cool", "atm_psia": None}
    v = c.put(f"/api/systems/{sid}/equipment", headers=h, json=body).json()
    assert v["targets"]["sc"] == {"lo": 7.0, "hi": 13.0, "source": "nameplate"} and 14.0 <= v["atm_psia"] <= 14.1
    v = c.put(f"/api/systems/{sid}/equipment", headers=h, json={**body, "system_type": "split_ac", "elevation_ft": None, "atm_psia": 14.5}).json()
    assert v["heat_pump"] is False and v["atm_psia"] == 14.5
    assert c.put(f"/api/systems/{sid}/equipment", headers=h, json={**body, "refrigerant": "R-12"}).status_code == 422
    assert c.put(f"/api/systems/{sid}/equipment", headers=h, json={**body, "metering": "capillary"}).status_code == 422
    assert c.get(f"/api/systems/{sid}/equipment", headers={"X-API-Key": seeded["key2"]}).status_code == 404
