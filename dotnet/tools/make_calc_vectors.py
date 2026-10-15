"""Generate tests/Fullscope.Core.Tests/vectors/calc_vectors.json: the Python reference
(hvac-cloud/hvaccloud/calc.py) run over random readings. The C# port in src/Fullscope.Core must
reproduce every snapshot byte for byte (json.dumps with sorted keys). Re-run after changing either
side, from the dotnet folder:

    ..\\hvac-cloud\\.venv\\Scripts\\python.exe tools\\make_calc_vectors.py
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hvac-cloud"))
from hvaccloud import calc, refrigerants  # noqa: E402

OUT = os.path.join(HERE, "..", "tests", "Fullscope.Core.Tests", "vectors", "calc_vectors.json")
COUNT = 1500
NOW = 1_791_200_000.0


def maybe(rng, lo, hi):
    """A reading as the firmware might send it: float, whole number, null or missing."""
    r = rng.random()
    if r < 0.08:
        return "missing"
    if r < 0.16:
        return None
    if r < 0.3:
        return rng.randint(int(lo), int(hi))
    return round(rng.uniform(lo, hi), rng.choice([1, 2, 3]))


def group(rng, spec):
    out = {}
    for key, (lo, hi) in spec.items():
        v = maybe(rng, lo, hi)
        if v != "missing":
            out[key] = v
    return out


def modes(rng, keys):
    out = {}
    for k in keys:
        r = rng.random()
        if r < 0.15:
            continue
        out[k] = rng.choice([True, False, True, 1, 0])
    return out


def node(rng, kind):
    if rng.random() < 0.1:
        return {"online": rng.random() < 0.5, "data": None}
    if kind == "outdoor":
        data = {"mode": modes(rng, ("Y", "OB")),
                "p": group(rng, {"liq": (150, 520), "vap": (40, 340), "tsuc": (30, 150)}),
                "t": group(rng, {"suc": (25, 75), "liq": (70, 125), "tsuc": (20, 70), "dis": (100, 220)}),
                "air": group(rng, {"t": (20, 118), "rh": (5, 95)})}
    else:
        data = {"mode": modes(rng, ("Y", "W", "G", "OB")),
                "air": group(rng, {"supply": (45, 110), "return": (60, 85)})}
    r = rng.random()
    data["err"] = [] if r < 0.75 else rng.choice([["sht30"], ["ads1115", "ds18b20"], "x"])
    if rng.random() < 0.1:
        data.pop("mode")
    return {"online": rng.random() < 0.85, "data": data}


def main():
    rng = random.Random(20261015)
    tables = refrigerants.Tables(log=lambda *_: None)
    refs = tables.available()
    cases = []
    for _ in range(COUNT):
        cfg = {"refrigerant": rng.choice(refs + ["R-999"]) if rng.random() < 0.97 else "R-999",
               "heat_pump": rng.random() < 0.6, "ob_energized": rng.choice(["cool", "heat"]),
               "atm_psia": rng.choice([14.0, 14.696, 13.2, round(rng.uniform(11.5, 14.7), 3)])}
        nodes = {}
        for kind in calc.NODES:
            if rng.random() < 0.93:
                nodes[kind] = node(rng, kind)
        run_start = rng.choice([None, NOW - rng.uniform(0, 3600), NOW - rng.randint(0, 40) * 60, NOW])
        snap, new_start = calc.compute(cfg, json.loads(json.dumps(nodes)), run_start, NOW, tables)
        cases.append({"cfg": cfg, "nodes": nodes, "run_start": run_start, "now": NOW,
                      "expected": json.dumps(snap, sort_keys=True), "run_start_after": new_start})
    with open(OUT, "w") as f:
        json.dump(cases, f, indent=0)
    print(f"wrote {len(cases)} cases to {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
