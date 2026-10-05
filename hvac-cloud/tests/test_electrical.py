import json

import pytest
from sqlalchemy import select

from hvaccloud import electrical
from hvaccloud.db import Equipment, Snapshot, System
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

T0 = 1_790_000_000.0
EQ = Equipment(system_id=1, comp_rla=14.1, comp_lra=73.0, fan_fla=1.4, cap_herm_uf=45.0, cap_fan_uf=5.0)
RUNNING = {"mode": "cooling", "Y": True, "run_min": 12, "flags": []}


def reading(**kw):
    """A healthy reading: 240 V, 11.8 A compressor, 1.1 A fan, caps at their rating."""
    d = {"node": "electrical", "uptime": 100, "contactor": True,
         "v": {"line": 240.0, "contactor": 0.05}, "i": {"comp": 11.8, "fan": 1.1},
         "cap": {"herm": {"v": 370.0, "i": round(45.0 * 370 / 2652.6, 3)}, "fan": {"v": 365.0, "i": round(5.0 * 365 / 2652.6, 3)}},
         "start": {"peak_a": 60.0, "ms": 250}, "err": []}
    for k, v in kw.items():
        d[k] = {**d[k], **v} if isinstance(v, dict) and isinstance(d.get(k), dict) else v
    return d


def run(data, snap=None, eq=EQ):
    s = electrical.apply(dict(snap or RUNNING), {"online": True, "data": data}, eq)
    return s, [f["code"] for f in s["flags"]]


def test_capacitance_formula():
    assert electrical.capacitance({"v": 370, "i": 6.277}) == pytest.approx(45.0, abs=0.1)
    assert electrical.capacitance({"v": 20, "i": 1}) is None            # not running: can't measure
    assert electrical.capacitance({"v": 365, "i": 0.0}) is None         # motor stopped: no current through it


def test_dead_fan_is_not_also_blamed_on_its_capacitor():
    _, codes = run(reading(i={"fan": 0.0}, cap={"fan": {"v": 365.0, "i": 0.0}}))
    assert "fan_not_running" in codes and "cap_fan" not in codes


def test_healthy_unit_has_no_flags_and_derived_values():
    s, codes = run(reading())
    assert codes == []
    e = s["elec"]
    assert e["comp_pct_rla"] == 84 and e["cap_herm_uf"] == pytest.approx(45.0, abs=0.2) and e["cap_herm_pct"] == pytest.approx(100, abs=0.5)
    assert e["contactor_v"] == 0.05 and e["line_v"] == 240.0


@pytest.mark.parametrize("change, code", [
    ({"i": {"comp": 0.2}}, "comp_not_running"),
    ({"i": {"fan": 0.0}}, "fan_not_running"),
    ({"contactor": False, "i": {"comp": 0.0, "fan": 0.0}}, "contactor_open"),
    ({"i": {"comp": 15.0}}, "comp_amps_high"),
    ({"i": {"fan": 1.7}}, "fan_amps_high"),
    ({"cap": {"herm": {"v": 370.0, "i": 5.5}}}, "cap_herm"),           # 39.4 uF of 45: -12 %
    ({"cap": {"fan": {"v": 365.0, "i": 0.6}}}, "cap_fan"),             # 4.4 uF of 5
    ({"v": {"contactor": 1.4}}, "contactor_drop"),
    ({"v": {"line": 190.0}}, "voltage"),
    ({"start": {"peak_a": 72.0, "ms": 1400}}, "slow_start"),
])
def test_each_fault(change, code):
    _, codes = run(reading(**change))
    assert code in codes, codes


def test_rules_wait_and_skip_missing_ratings():
    settling = {**RUNNING, "run_min": 0.5}
    assert run(reading(i={"comp": 0.2}), settling)[1] == []             # first minute: not judged yet
    idle = {"mode": "idle", "Y": False, "run_min": 0, "flags": []}
    assert run(reading(contactor=False, i={"comp": 0.0, "fan": 0.0}), idle)[1] == []
    bare = Equipment(system_id=1)                                         # nothing entered on the Equipment page
    s, codes = run(reading(i={"comp": 25.0}), eq=bare)
    assert "comp_amps_high" not in codes and s["elec"]["comp_pct_rla"] is None
    assert "comp_not_running" in run(reading(i={"comp": 0.4}), eq=bare)[1]   # 1 A floor without an RLA


def test_offline_module_and_no_module():
    s = electrical.apply(dict(RUNNING), None, EQ)
    assert s["elec"] is None and s["flags"] == []                          # never installed: nothing
    s = electrical.apply(dict(RUNNING), {"online": False, "data": None}, EQ)
    assert [(f["code"], f.get("node")) for f in s["flags"]] == [("node_offline", "electrical")]


def test_ingest_joins_electrical_readings_to_snapshots(sessions, seeded, tables):
    with sessions() as s, s.begin():
        sid = s.scalar(select(System.id).where(System.site_id == "home"))
        s.add(Equipment(system_id=sid, comp_rla=14.1, fan_fla=1.4, cap_herm_uf=45.0))
    i = Ingest(sessions, tables)
    for k in range(72):                                                   # 12 min of cooling, weak HERM capacitor
        t = T0 + k * 10
        i.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN).encode(), now=t)
        i.handle("hvac/home/electrical/telemetry", json.dumps(reading(cap={"herm": {"v": 370.0, "i": 5.5}})).encode(), now=t + 2)
        last = i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=t + 5)
    assert last["elec"]["cap_herm_uf"] == pytest.approx(39.4, abs=0.2)
    assert "cap_herm" in [f["code"] for f in last["flags"]]
    with sessions() as s:
        n_snaps = len(list(s.scalars(select(Snapshot.time))))
    assert n_snaps == 144                                                 # electrical readings add no snapshots
