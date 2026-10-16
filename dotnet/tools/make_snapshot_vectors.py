"""Generate tests/Fullscope.Core.Tests/vectors/snapshot_vectors.json and thermostat_vectors.json:
the Python reference run over random inputs, for the C# port to reproduce byte for byte.

- snapshot: calc.compute, then equipment.apply, electrical.apply and thermostat.apply, in the
  order the ingest worker applies them.
- thermostat: apply_request, validate, validate_tech, schedule_now and hold_until_next.

Re-run after changing either side, from the dotnet folder:

    ..\\hvac-cloud\\.venv\\Scripts\\python.exe tools\\make_snapshot_vectors.py
"""
import copy
import datetime as dt
import json
import os
import random
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "hvac-cloud"))
from hvaccloud import calc, electrical, equipment, refrigerants, thermostat as th  # noqa: E402
from make_calc_vectors import NOW, node  # noqa: E402

OUT = os.path.join(HERE, "..", "tests", "Fullscope.Core.Tests", "vectors")
EQ_FIELDS = ["system_type", "metering", "tonnage", "sc_target", "sc_tolerance", "rated_btuh", "rated_cfm", "max_esp",
             "elevation_ft", "comp_rla", "comp_lra", "fan_fla", "cap_herm_uf", "cap_fan_uf", "volt_min", "volt_max"]


def f(rng, lo, hi, none=0.25):
    if rng.random() < none:
        return None
    return float(round(rng.uniform(lo, hi), rng.choice([0, 1, 2])))


def equip(rng):
    if rng.random() < 0.15:
        return None
    return {"system_type": rng.choice([None, "split_hp", "split_ac"]), "metering": rng.choice([None, "txv", "eev", "piston"]),
            "tonnage": f(rng, 1.5, 5), "sc_target": f(rng, 6, 14), "sc_tolerance": f(rng, 1, 5),
            "rated_btuh": None, "rated_cfm": None, "max_esp": None, "elevation_ft": f(rng, 0, 7000),
            "comp_rla": f(rng, 5, 25), "comp_lra": f(rng, 40, 120), "fan_fla": f(rng, 0.5, 3, 0.2),
            "cap_herm_uf": f(rng, 25, 60), "cap_fan_uf": f(rng, 3, 10), "volt_min": f(rng, 190, 210, 0.6),
            "volt_max": f(rng, 240, 260, 0.6)}


def num(rng, lo, hi, none=0.1):
    r = rng.random()
    if r < none:
        return None
    if r < 0.25:
        return rng.randint(int(lo), int(hi))
    return round(rng.uniform(lo, hi), rng.choice([1, 2]))


def elec_state(rng):
    r = rng.random()
    if r < 0.2:
        return None
    if r < 0.3:
        return {"online": rng.random() < 0.5, "data": None if rng.random() < 0.5 else {}}
    contactor = rng.random() < 0.8
    data = {"node": "electrical", "contactor": contactor,
            "v": {"line": num(rng, 180, 265), "contactor": num(rng, 0, 3) if contactor else None},
            "i": {"comp": num(rng, 0, 20), "fan": num(rng, 0, 3)},
            "cap": {"herm": {"v": num(rng, 0, 400), "i": num(rng, 0, 8)} if rng.random() < 0.8 else None,
                    "fan": {"v": num(rng, 0, 400), "i": num(rng, 0, 1.2)}},
            "start": {"peak_a": num(rng, 20, 90), "ms": num(rng, 100, 1500)},
            "err": [] if rng.random() < 0.85 else ["ct_fan"]}
    return {"online": rng.random() < 0.9, "data": data}


def tstat_state(rng):
    r = rng.random()
    if r < 0.3:
        return None
    if r < 0.38:
        return {"online": False, "data": {"room": {"t": 75}}}
    call = rng.choice([None, "cool", "heat"])
    data = {"node": "thermostat", "cfg_ver": rng.randint(0, 9), "room": {"t": num(rng, 45, 95), "rh": 40.0},
            "mode": rng.choice(["cool", "heat", "auto"]), "fan": "auto",
            "sp": {"heat": num(rng, 60, 72, 0.05), "cool": num(rng, 72, 82, 0.05)}, "source": "schedule",
            "out": {"Y": rng.choice([True, False, None, 1, 0]), "W": False, "G": True, "OB": True},
            "call": call, "call_min": num(rng, 0, 200), "wait": None, "err": [] if rng.random() < 0.9 else ["sht"]}
    if rng.random() < 0.2:
        data.pop("out")
    return {"online": rng.random() < 0.92, "data": data}


def snapshots(rng, tables):
    cases = []
    for _ in range(1500):
        cfg = {"refrigerant": rng.choice(tables.available()), "heat_pump": rng.random() < 0.6,
               "ob_energized": "cool", "atm_psia": 14.0}
        nodes = {k: node(rng, k) for k in calc.NODES}
        for n in nodes.values():                       # mostly running, so the steady checks fire
            if n["data"] and rng.random() < 0.7:
                n["data"]["mode"] = {"Y": True, "G": True, "OB": True}
        run_start = rng.choice([None, NOW - rng.uniform(0, 1800), NOW - 30])
        snap, _ = calc.compute(cfg, copy.deepcopy(nodes), run_start, NOW, tables)
        eq, es, ts = equip(rng), elec_state(rng), tstat_state(rng)
        eqo = SimpleNamespace(**eq) if eq else None
        out = equipment.apply(copy.deepcopy(snap), eqo)
        out = electrical.apply(out, copy.deepcopy(es), eqo)
        out = th.apply(out, copy.deepcopy(ts))
        cases.append({"snap": snap, "equipment": eq, "electrical": es, "thermostat": ts,
                      "expected": json.dumps(out, sort_keys=True)})
    return cases


def any_value(rng):
    return rng.choice([None, True, 3, 2.5, "x", "07:30", [], {}, [1, 2], {"a": 1}, "", 0])


def period(rng):
    if rng.random() < 0.15:
        return any_value(rng)
    p = {"days": rng.choice([[0, 1, 2, 3, 4], [5, 6], [rng.randint(0, 6)], [7], [], "012", [1.5], ["3"], [True], None]),
         "at": rng.choice(["06:00", "8:30", "22:00", "24:00", "6", "06:60", " 7:05", "+7:00", "1_0:00", 630, None]),
         "heat": num(rng, 45, 75, 0.05), "cool": num(rng, 65, 92, 0.05)}
    if rng.random() < 0.1:
        p.pop(rng.choice(list(p)))
    if rng.random() < 0.05:
        p["heat"] = rng.choice([True, "68", None])
    return p


def settings(rng):
    s = {}
    if rng.random() < 0.8:
        s["mode"] = rng.choice(list(th.MODES) + ["dry", None, 3])
    if rng.random() < 0.6:
        s["fan"] = rng.choice(list(th.FANS) + ["high"])
    for k, lo, hi in (("heat_sp", 45, 80), ("cool_sp", 60, 95)):
        if rng.random() < 0.7:
            s[k] = num(rng, lo, hi, 0.05) if rng.random() < 0.95 else rng.choice([True, "70"])
    if rng.random() < 0.6:
        s["schedule"] = [period(rng) for _ in range(rng.randint(0, 4))] if rng.random() < 0.95 else rng.choice(["", {}, None, "ab"])
    if rng.random() < 0.3:
        s["hold"] = {"heat": 66.0, "cool": 79.0, "until": rng.choice([None, "2026-10-06T08:30:00", "2026-10-01T00:00:00"])}
    if rng.random() < 0.1:
        s["junk"] = 1
    return s


def request(rng):
    r = {}
    for k in th.REQUEST_KEYS:
        if rng.random() < 0.3:
            r[k] = {"mode": rng.choice(list(th.MODES) + ["dry"]), "fan": rng.choice(list(th.FANS) + ["x"]),
                    "heat_sp": num(rng, 45, 80, 0.05), "cool_sp": num(rng, 62, 95, 0.05)}[k]
    x = rng.random()
    if x < 0.15:
        r["resume"] = rng.choice([True, 1, False, 0, "yes"])
    elif x < 0.5:
        h = {}
        if rng.random() < 0.8:
            h["heat"] = num(rng, 45, 80, 0.1)
        if rng.random() < 0.8:
            h["cool"] = num(rng, 62, 95, 0.1)
        if rng.random() < 0.7:
            h["until"] = rng.choice([None, "2026-10-06T08:30:00", "2026-10-06 08:30", "2026-10-06", "20261006T0830",
                                     "tomorrow", "2026-13-01T00:00", "2026-10-06T08:30:00.5", 5])
        r["hold"] = h if rng.random() < 0.9 else "68"
    if rng.random() < 0.03:
        return rng.choice([[1], "x", None])
    return r


def tech(rng):
    t = {}
    for k, (lo, hi) in th.TECH_LIMITS.items():
        if rng.random() < 0.15:
            t[k] = rng.choice([lo, hi, lo - 1, hi + 1, (lo + hi) / 2, True, "5", None])
    if rng.random() < 0.15:
        t["has_aux"] = rng.choice([True, False, 1, "yes"])
    if rng.random() < 0.15:
        t["service_pin"] = rng.choice(["0000", "123456", "123", "1234567", "12a4", 1234])
    if rng.random() < 0.15:
        t["tz"] = rng.choice(["America/Phoenix", "America/Denver", "UTC", "Mars/Olympus", "", 7])
    return t


def thermostat_cases(rng):
    cases = []
    base = dt.datetime(2026, 10, 5, 0, 0)
    for _ in range(1500):
        st = settings(rng)
        req = request(rng)
        try:
            res = {"ok": th.apply_request(st, req)}
        except ValueError as e:
            res = {"error": str(e)}
        except Exception as e:                       # Python raises something else; C# need only refuse it
            res = {"crash": type(e).__name__}
        when = base + dt.timedelta(minutes=rng.randint(0, 7 * 1440 * 2), seconds=rng.randint(0, 59))
        valid = not th.validate(st) if isinstance(st.get("schedule", []), (list, type(None))) else False
        sched, hold_heat = None, rng.choice([None, 70.0])
        if valid:
            heat, cool, source, nxt = th.schedule_now(st, when)
            sched = {"heat": heat, "cool": cool, "source": source, "next": nxt,
                     "hold_next": th.hold_until_next(st, when, hold_heat, None)}
        try:
            verrs = th.validate(st)
        except Exception as e:
            verrs = {"crash": type(e).__name__}
        tc = tech(rng)
        cases.append({"settings": st, "request": req, "result": json.dumps(res, sort_keys=True),
                      "validate": verrs, "when": when.isoformat(), "hold_heat": hold_heat, "schedule": json.dumps(sched, sort_keys=True),
                      "tech": tc, "validate_tech": th.validate_tech(tc)})
    return cases


def main():
    rng = random.Random(20261015)
    tables = refrigerants.Tables(log=lambda *_: None)
    for name, cases in (("snapshot_vectors.json", snapshots(rng, tables)), ("thermostat_vectors.json", thermostat_cases(rng))):
        with open(os.path.join(OUT, name), "w") as fh:
            json.dump(cases, fh, indent=0)
        print(f"wrote {len(cases)} cases to {name}")


if __name__ == "__main__":
    main()
