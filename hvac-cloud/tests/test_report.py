import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import report
from hvaccloud.api import create_app
from hvaccloud.db import Equipment, System
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT


def snap(mode="cooling", run_min=15, G=True, **v):
    return {"mode": mode, "run_min": run_min, "G": G, **v}


def test_only_steady_cooling_counts():
    rows = [(0, snap(sh=1.0, run_min=5)),                    # not steady yet
            (10, snap(sh=10.0)), (20, snap(sh=12.0)), (30, snap(sh=2.0)),
            (40, snap(mode="idle", sh=50.0)),                # not running
            (500, snap(sh=14.0, dt=11.0, G=False))]           # dt without the blower is not judged
    r = report.build(rows, None, {"sh_low": 2}, 7)
    sh = r["metrics"]["sh"]
    assert sh["n"] == 4 and sh["min"] == 2.0 and sh["max"] == 14.0
    assert sh["pct"]["50"] == 12.0 and sh["pct"]["5"] == 2.0 and sh["pct"]["95"] == 14.0
    assert sh["below"] == 0.25 and sh["above"] == 0.0 and sh["limit_low"] == 3.0
    assert r["metrics"]["dt"]["n"] == 0 and r["metrics"]["dt"]["pct"] is None
    assert r["steady_hours"] == round(20 / 3600, 2)          # 10 s + 10 s; the gap after idle restarts
    assert r["alerts"] == {"sh_low": 2}


def test_nameplate_subcooling_band():
    eq = Equipment(system_id=1, metering="txv", sc_target=10.0, sc_tolerance=2.0)
    r = report.build([(0, snap(sc=7.0)), (5, snap(sc=10.0)), (10, snap(sc=13.0))], eq, {}, 14)
    sc = r["metrics"]["sc"]
    assert (sc["limit_low"], sc["limit_high"], sc["limit_source"]) == (8.0, 12.0, "nameplate")
    assert sc["below"] == round(1 / 3, 3) and sc["above"] == round(1 / 3, 3)
    assert r["metrics"]["approach"]["limit_source"] is None and r["metrics"]["approach"]["below"] is None


def test_endpoint(sessions, seeded, tables):
    with sessions() as s:
        sid = s.scalar(select(System.id).where(System.site_id == "home"))
    i, now = Ingest(sessions, tables), time.time()
    for k in range(0, 15 * 60, 30):                          # 15 minutes of cooling every 30 s
        i.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN).encode(), now=now - 900 + k)
        i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=now - 900 + k + 1)
    c = TestClient(create_app(sessions, publisher=lambda *a: True))
    h = {"X-API-Key": seeded["key1"]}
    r = c.get(f"/api/systems/{sid}/threshold-report?days=1", headers=h).json()
    assert r["days"] == 1.0 and r["metrics"]["sh"]["n"] > 0 and 0 < r["steady_hours"] < 0.1
    assert r["metrics"]["sc"]["limit_source"] == "generic"
    assert c.get(f"/api/systems/{sid}/threshold-report?days=32", headers=h).status_code == 422
    assert c.get(f"/api/systems/{sid}/threshold-report", headers={"X-API-Key": seeded["key2"]}).status_code == 404
