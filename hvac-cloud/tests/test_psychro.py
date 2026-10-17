import json

from sqlalchemy import select

from hvaccloud import equipment, psychro
from hvaccloud.db import Equipment, System
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

T0 = 1_790_000_000.0


def indoor(**air):
    return {"online": True, "data": {"air": {"supply": 55.0, "return": 75.0, **air}}}


def codes(s):
    return [f["code"] for f in s["flags"]]


def test_point_matches_the_ashrae_chart():
    assert psychro.point(75, 50, 14.696) == {"rh": 50.0, "wb": 62.6, "dp": 55.1, "h": 28.1}
    assert psychro.point(55, 90, 14.696) == {"rh": 90.0, "wb": 53.3, "dp": 52.1, "h": 22.2}
    hi = psychro.point(75, 50, 12.1)                   # 5,000 ft: same RH, lower wet bulb, more moisture
    assert hi["wb"] < 62.6 and hi["h"] > 28.1
    for bad in ((75, 0), (75, 101), (None, 50), (75, None), (75, True)):
        assert psychro.point(*bad, 14.0) is None


def test_apply_only_when_the_node_reports_humidity():
    snap = {"t_ret": 75.0, "t_sup": 55.0}
    assert "air_rh" not in psychro.apply(dict(snap), indoor(), 14.0)                    # no probes fitted
    assert "air_rh" not in psychro.apply(dict(snap), {**indoor(rh_ret=50.0), "online": False}, 14.0)
    s = psychro.apply(dict(snap), indoor(rh_ret=50.0, rh_sup=None, t_rh_ret=75.0), 14.696)
    assert s["air_rh"] == {"ret": {"rh": 50.0, "wb": 62.6, "dp": 55.1, "h": 28.1}, "sup": None, "dh": None}
    s = psychro.apply(dict(snap), indoor(rh_ret=50.0, rh_sup=90.0), 14.696)              # falls back to the DS18B20s
    assert s["air_rh"]["dh"] == 5.9


def test_chart_target():
    assert equipment.chart_superheat(63.0, 95.0) == 7.0            # (189 - 80 - 95) / 2
    assert equipment.chart_superheat(67.0, 80.0) == 20.5
    assert equipment.chart_superheat(55.0, 100.0) is None          # under 5: don't charge by superheat
    assert equipment.chart_superheat(80.0, 90.0) is None           # off the chart
    assert equipment.chart_superheat(None, 90.0) is None


def piston_snap(sh, wb=67.0, oat=80.0, run_min=15, flags=None):
    return {"mode": "cooling", "run_min": run_min, "sh": sh, "sc": 10.0, "oat": oat, "flags": list(flags or []),
            "air_rh": {"ret": {"rh": 55.0, "wb": wb, "dp": 60.0, "h": 31.5}, "sup": None, "dh": None}}


def test_piston_judged_against_the_chart():
    eq = Equipment(system_id=1, metering="piston")
    ok = equipment.apply(piston_snap(18.0, flags=[{"code": "dt_low", "text": "keep"}]), eq)
    assert codes(ok) == ["dt_low"]
    assert ok["targets"]["sh"] == {"lo": 15.5, "hi": 25.5, "source": "chart", "target": 20.5}
    low = equipment.apply(piston_snap(9.0), eq)                            # generic 3-30 would call it fine
    assert codes(low) == ["sh_low"] and "charging-chart target 20.5 ± 5°F" in low["flags"][0]["text"]
    high = equipment.apply(piston_snap(32.0, flags=[{"code": "sh_high", "text": "generic"}]), eq)
    assert codes(high) == ["sh_high"] and "charging-chart" in high["flags"][0]["text"]
    flood = equipment.apply(piston_snap(2.0, flags=[{"level": "alert", "code": "sh_low", "text": "floodback"}]), eq)
    assert flood["flags"] == [{"level": "alert", "code": "sh_low", "text": "floodback"}]
    assert equipment.apply(piston_snap(9.0, run_min=5), eq)["flags"] == []          # settling
    generic = equipment.apply(piston_snap(9.0, wb=52.0), eq)                         # target under 5
    assert generic["targets"]["sh"] == {"lo": 3.0, "hi": 30.0, "source": "generic"}
    txv = equipment.apply(piston_snap(9.0), Equipment(system_id=1, metering="txv"))
    assert txv["targets"]["sh"]["source"] == "generic"


def test_ingest_adds_humidity_and_chart_target(sessions, seeded, tables):
    with sessions() as s, s.begin():
        sid = s.scalar(select(System.id).where(System.site_id == "home"))
        s.add(Equipment(system_id=sid, metering="piston"))
    i = Ingest(sessions, tables)
    ind = {**COOL_IN, "air": {**COOL_IN["air"], "rh_ret": 50.0, "t_rh_ret": 76.0, "rh_sup": 92.0, "t_rh_sup": 57.0}}
    for k in range(72):
        i.handle("hvac/home/indoor/telemetry", json.dumps(ind).encode(), now=T0 + k * 10)
        last = i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=T0 + k * 10 + 5)
    a = last["air_rh"]
    assert 62 < a["ret"]["wb"] < 64 and a["sup"]["rh"] == 92.0 and a["dh"] > 0
    t = last["targets"]["sh"]
    assert t["source"] == "chart" and t["target"] == equipment.chart_superheat(a["ret"]["wb"], 95.0)
