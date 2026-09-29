"""calc.compute must match the PC dashboard's Hub.compute exactly."""
import copy
import importlib
import os
import sys
import time

import pytest

from hvaccloud import calc

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "hvac-monitor-app")

COOL_OUT = {"mode": {"Y": True, "OB": True}, "p": {"liq": 360.0, "vap": 120.0, "tsuc": None},
            "t": {"suc": 52.0, "liq": 101.0, "tsuc": None, "dis": None}, "air": {"t": 95.0, "rh": 12.0}, "err": []}
COOL_IN = {"mode": {"Y": True, "W": False, "G": True, "OB": True},
           "air": {"supply": 57.0, "return": 76.0, "dt": None}, "err": []}
HEAT_OUT = {"mode": {"Y": True, "OB": False}, "p": {"liq": 300.0, "vap": 320.0, "tsuc": 115.0},
            "t": {"suc": 40.0, "liq": 90.0, "tsuc": 38.0, "dis": None}, "air": {"t": 35.0, "rh": 60.0}, "err": []}
BAD_OUT = {**COOL_OUT, "p": {"liq": 360.0, "vap": 150.0, "tsuc": None}, "t": {"suc": 51.0, "liq": 118.0},
           "err": ["sht30"]}

CASES = {
    "cooling steady": (COOL_OUT, COOL_IN, 15),
    "cooling just started": (COOL_OUT, COOL_IN, 2),
    "heating": (HEAT_OUT, {**COOL_IN, "mode": {"Y": True, "W": False, "G": True, "OB": False}}, 12),
    "idle": ({**COOL_OUT, "mode": {"Y": False, "OB": False}}, {**COOL_IN, "mode": {}}, None),
    "indoor offline": (COOL_OUT, None, 15),
    "faults": (BAD_OUT, {**COOL_IN, "air": {"supply": 70.0, "return": 76.0}}, 20),
}


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    sys.path.insert(0, APP)
    server = importlib.import_module("server")
    server.DB_PATH = str(tmp_path_factory.mktemp("dash") / "hvac_data.db")   # never touch the real one
    return server.Hub({**server.DEFAULTS, "atm_psia": 14.0})


@pytest.mark.parametrize("name", list(CASES))
def test_matches_dashboard(name, hub, tables):
    od, ind, run_min = CASES[name]
    now = time.time()
    run_start = now - run_min * 60 if run_min is not None else None
    hub.latest = {n: {"ts": now, "data": copy.deepcopy(d)} for n, d in (("outdoor", od), ("indoor", ind)) if d}
    hub.run_start = run_start
    expected = hub.compute(now)

    nodes = {n: {"online": d is not None, "data": copy.deepcopy(d)} for n, d in (("outdoor", od), ("indoor", ind))}
    got, new_start = calc.compute({k: hub.cfg[k] for k in ("refrigerant", "heat_pump", "ob_energized", "atm_psia")},
                                  nodes, run_start, now, tables)
    assert got == expected
    assert new_start == hub.run_start


def test_cooling_values(tables):
    """Sanity check against the R-410A table: 120 psig suction at 14.0 psia is about 41 F dew."""
    nodes = {"outdoor": {"online": True, "data": COOL_OUT}, "indoor": {"online": True, "data": COOL_IN}}
    cfg = {"refrigerant": "R-410A", "heat_pump": True, "ob_energized": "cool", "atm_psia": 14.0}
    s, _ = calc.compute(cfg, nodes, time.time() - 900, time.time(), tables)
    assert s["mode"] == "cooling"
    assert 40 < s["sat_low"] < 43 and 10 < s["sh"] < 12
    assert s["dt"] == 19.0 and s["flags"] == []
