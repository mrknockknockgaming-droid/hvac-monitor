import json

from sqlalchemy import select

from hvaccloud import airflow, alerts
from hvaccloud.db import Equipment, System
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

T0 = 1_790_000_000.0


def indoor(**air):
    return {"online": True, "data": {"air": {"supply": 55.0, "return": 75.0, **air}}}


def codes(s):
    return [f["code"] for f in s.get("flags", [])]


def test_no_sensors_no_static():
    assert "static" not in airflow.apply({"mode": "cooling"}, indoor(), None)
    assert "static" not in airflow.apply({"mode": "cooling"}, {**indoor(sp_ret=-0.2), "online": False}, None)


def test_tesp_and_flags_while_the_blower_runs():
    eq = Equipment(system_id=1, max_esp=0.5)
    s = airflow.apply({"mode": "cooling", "flags": []}, indoor(sp_ret=-0.25, sp_sup=0.3, sp_filt=0.12), eq)
    assert s["static"] == {"ret": -0.25, "sup": 0.3, "filter": 0.12, "tesp": 0.55, "max": 0.5}
    assert codes(s) == ["tesp_high"] and "above the rated 0.5" in s["flags"][0]["text"]
    s = airflow.apply({"mode": "fan"}, indoor(sp_ret=-0.1, sp_sup=0.2, sp_filt=0.34), None)
    assert codes(s) == ["filter_dirty"] and s["static"]["max"] is None          # no rated max: TESP not judged
    idle = airflow.apply({"mode": "idle", "flags": []}, indoor(sp_ret=-0.5, sp_sup=0.5, sp_filt=0.4), eq)
    assert idle["flags"] == [] and idle["static"]["tesp"] == 1.0
    part = airflow.apply({"mode": "cooling"}, indoor(sp_ret=None, sp_sup=0.4), eq)
    assert part["static"]["tesp"] is None and codes(part) == []


def test_airflow_alerts_stay_open_while_the_blower_is_off():
    assert alerts.AIRFLOW_CODES <= alerts.unjudged({"mode": "idle", "static": {"tesp": 0.4}})
    assert alerts.AIRFLOW_CODES <= alerts.unjudged({"mode": "fan"})                         # no sensors reporting
    assert not alerts.AIRFLOW_CODES & alerts.unjudged({"mode": "fan", "static": {"tesp": 0.4}})


def test_ingest_adds_static(sessions, seeded, tables):
    with sessions() as s, s.begin():
        sid = s.scalar(select(System.id).where(System.site_id == "home"))
        s.add(Equipment(system_id=sid, max_esp=0.5))
    i = Ingest(sessions, tables)
    ind = {**COOL_IN, "air": {**COOL_IN["air"], "sp_ret": -0.31, "sp_sup": 0.42, "sp_filt": 0.18}}
    i.handle("hvac/home/indoor/telemetry", json.dumps(ind).encode(), now=T0)
    last = i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=T0 + 5)
    assert last["static"]["tesp"] == 0.73 and "tesp_high" in codes(last)
