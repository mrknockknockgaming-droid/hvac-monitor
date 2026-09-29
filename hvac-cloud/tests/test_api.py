import json
import time

import pytest
from fastapi.testclient import TestClient

from hvaccloud.api import create_app
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT


@pytest.fixture
def world(sessions, seeded, tables):
    sent = []
    client = TestClient(create_app(sessions, publisher=lambda topic, payload: sent.append((topic, payload)) or True))
    ingest = Ingest(sessions, tables)
    now = time.time()
    for k in range(3):   # three cycles of real-looking messages, the last one current
        ingest.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN).encode(), now=now - 20 + k * 10)
        ingest.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=now - 19 + k * 10)
    return {"client": client, "sent": sent, "ingest": ingest, "h1": {"X-API-Key": seeded["key1"]},
            "h2": {"X-API-Key": seeded["key2"]}}


def test_needs_a_valid_key(world):
    c = world["client"]
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/api/systems").status_code == 422                     # header missing
    assert c.get("/api/systems", headers={"X-API-Key": "hvk_nope"}).status_code == 401


def test_accounts_only_see_their_own_systems(world):
    c = world["client"]
    mine = c.get("/api/systems", headers=world["h1"]).json()
    assert [s["site_id"] for s in mine] == ["home"]
    assert c.get(f"/api/systems/{mine[0]['id']}", headers=world["h2"]).status_code == 404


def test_system_latest_history_and_export(world):
    c, h = world["client"], world["h1"]
    sid = c.get("/api/systems", headers=h).json()[0]["id"]
    detail = c.get(f"/api/systems/{sid}", headers=h).json()
    assert {d["node"]: d["online"] for d in detail["devices"]} == {"indoor": True, "outdoor": True}
    latest = c.get(f"/api/systems/{sid}/latest", headers=h).json()
    assert latest["derived"]["mode"] == "cooling" and latest["derived"]["dt"] == 19.0
    hist = c.get(f"/api/systems/{sid}/history?minutes=5", headers=h).json()
    assert hist and all("ts" in row for row in hist) and hist[-1]["dt"] == 19.0
    csv_text = c.get(f"/api/systems/{sid}/export.csv?minutes=5", headers=h).text
    assert csv_text.splitlines()[0].startswith("time_utc,mode,p_low") and len(csv_text.splitlines()) == 7


def test_settings_validation(world):
    c, h = world["client"], world["h1"]
    sid = c.get("/api/systems", headers=h).json()[0]["id"]
    ok = c.patch(f"/api/systems/{sid}", headers=h, json={"refrigerant": "R-454B", "atm_psia": 14.1})
    assert ok.status_code == 200 and ok.json()["refrigerant"] == "R-454B"
    assert c.patch(f"/api/systems/{sid}", headers=h, json={"refrigerant": "R-12"}).status_code == 422
    assert c.patch(f"/api/systems/{sid}", headers=h, json={"atm_psia": 30}).status_code == 422


def test_command_round_trip(world):
    c, h = world["client"], world["h1"]
    sid = c.get("/api/systems", headers=h).json()[0]["id"]
    r = c.post(f"/api/systems/{sid}/commands", headers=h,
               json={"node": "outdoor", "cmd": {"cmd": "cal_zero", "ch": "p_liq"}})
    assert r.status_code == 200 and r.json()["sent"] is True
    assert world["sent"] == [("hvac/home/outdoor/cmd", {"cmd": "cal_zero", "ch": "p_liq"})]
    world["ingest"].handle("hvac/home/outdoor/reply", b'{"cmd":"cal_zero","ok":true}')
    cmds = c.get(f"/api/systems/{sid}/commands", headers=h).json()
    assert cmds[0]["reply"] == {"cmd": "cal_zero", "ok": True} and cmds[0]["replied_at"]
    assert c.post(f"/api/systems/{sid}/commands", headers=h,
                  json={"node": "outdoor", "cmd": {"ch": "p_liq"}}).status_code == 422


def test_latest_includes_raw_node_data(world):
    c, h = world["client"], world["h1"]
    sid = c.get("/api/systems", headers=h).json()[0]["id"]
    latest = c.get(f"/api/systems/{sid}/latest", headers=h).json()
    assert latest["nodes"]["outdoor"]["data"]["p"]["liq"] == 360.0
    assert latest["nodes"]["indoor"]["data"]["air"]["supply"] == 57.0


def test_summary_counts_runtime_and_cycles(sessions, seeded, tables):
    """Two 10-minute runs 20 minutes apart, one message every 30 s."""
    client = TestClient(create_app(sessions, publisher=lambda *a: True))
    ingest = Ingest(sessions, tables)
    off_out = {**COOL_OUT, "mode": {"Y": False, "OB": False}}
    off_in = {**COOL_IN, "mode": {}}
    start = time.time() - 50 * 60
    for k in range(100):                                   # 50 min
        t = start + k * 30
        m = k * 0.5
        on = m < 10 or 30 <= m < 40
        ingest.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN if on else off_in).encode(), now=t)
        ingest.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT if on else off_out).encode(), now=t + 1)
    h = {"X-API-Key": seeded["key1"]}
    sid = client.get("/api/systems", headers=h).json()[0]["id"]
    sm = client.get(f"/api/systems/{sid}/summary?hours=1", headers=h).json()
    assert sm["cycles"] == 2
    assert 0.3 < sm["runtime_hours"] < 0.36            # ~20 min
    assert 9 < sm["avg_on_minutes"] < 11
    assert sm["outside_high"] == 95.0 and sm["inside_avg"] == 76.0


def test_web_app_is_served(world):
    c = world["client"]
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
    page = c.get("/app/")
    assert page.status_code == 200 and "app.js" in page.text
    assert c.get("/app/app.js").status_code == 200 and c.get("/app/fullscope.css").status_code == 200
