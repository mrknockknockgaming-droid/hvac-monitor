"""Generate test/test_core/vectors.h: the Python reference Controller (hvac-cloud/hvaccloud/
thermostat.py) run through scripted scenarios, step by step. The C++ port must reproduce every
output (pio test -e native). Re-run after changing either side:

    ..\\..\\hvac-cloud\\.venv\\Scripts\\python.exe tools\\make_vectors.py
"""
import datetime as dt
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "hvac-cloud"))
from hvaccloud import thermostat as th  # noqa: E402

T0 = 1_791_200_000.0
BASE = dt.datetime(2026, 10, 5, 5, 0)          # a Monday, 5 AM local
STEP = 10
CALL = {None: 0, "heat": 1, "cool": 2}
WAIT = {None: 0, "min_on": 1, "min_off": 2, "max_starts": 3, "sensor": 4}
SOURCE = {"manual": 0, "schedule": 1, "hold": 2}
WEEKDAYS = [0, 1, 2, 3, 4]
SCHEDULE = [{"days": WEEKDAYS, "at": "06:00", "heat": 68, "cool": 75}, {"days": WEEKDAYS, "at": "08:30", "heat": 62, "cool": 82},
            {"days": WEEKDAYS, "at": "17:00", "heat": 68, "cool": 75}, {"days": [0, 1, 2, 3, 4, 5, 6], "at": "22:00", "heat": 65, "cool": 78},
            {"days": [5, 6], "at": "07:00", "heat": 69, "cool": 76}]


def local(now):
    return BASE + dt.timedelta(seconds=now - T0)


class House:
    def __init__(self, room, rng):
        self.room, self.rng, self.last_call = room, rng, None

    def step(self, oat, out, call):
        self.last_call = call or self.last_call       # a minimum run continues the last call
        self.room += STEP * (oat - self.room) / (4 * 3600)
        if out["Y"]:
            self.room += STEP * (-0.4 if self.last_call == "cool" else 0.3) / 60
        if out["W"]:
            self.room += STEP * 0.35 / 60
        self.room += self.rng.uniform(-0.03, 0.03)
        return self.room


def scenario(name, settings, tech, hours, outdoor_fn, seed, events=(), room0=74.0, glitches=False):
    """events: [(hour, settings_changes or None, tech_changes or None, 'hold'|'resume'|None)]"""
    rng = random.Random(seed)
    settings, tech = th.merged(settings), th.merged_tech(tech)
    ctrl, house = th.Controller(settings, tech), House(room0, rng)
    configs, steps = [json.dumps({"ver": 1, "settings": settings, "tech": tech})], []
    pending = sorted(events, key=lambda e: e[0])
    cfg_for_step = 0
    n = int(hours * 3600 / STEP)
    for i in range(n):
        now = T0 + i * STEP
        when = local(now)
        while pending and i * STEP >= pending[0][0] * 3600:
            _, s_chg, t_chg, act = pending.pop(0)
            if s_chg:
                settings = {**settings, **s_chg}
            if t_chg:
                tech = {**tech, **t_chg}
            if act == "hold":
                settings = {**settings, "hold": th.hold_until_next(settings, when, cool=settings["cool_sp"] - 2, heat=settings["heat_sp"] + 1)}
            if act == "hold_forever":
                settings = {**settings, "hold": {"heat": 70, "cool": 73, "until": None}}
            if act == "resume":
                settings = {**settings, "hold": None}
            ctrl.configure(settings, tech)
            configs.append(json.dumps({"ver": len(configs) + 1, "settings": settings, "tech": tech}))
            cfg_for_step = len(configs) - 1
        oat = outdoor_fn(i, rng)
        room = round(house.room, 2)
        if glitches and rng.random() < 0.004:
            room = rng.choice([None, 200.0, 20.0])
        rep = ctrl.step(room, oat, now, when)
        house.step(oat if oat is not None else 60, rep["out"], rep["call"])
        steps.append((now, room, oat, when, cfg_for_step, rep))
        cfg_for_step = -1
    return name, configs, steps


def c_num(v):
    if v is None:
        return "NAN"
    return repr(float(v))


def main():
    scenarios = [
        scenario("cooling_with_schedule_and_holds", {"mode": "cool", "schedule": SCHEDULE}, {}, 30,
                 lambda i, r: 88 + 14 * math.sin(i / 1500), 1,
                 events=[(4, None, None, "hold"), (7, None, None, "resume"), (12, None, {"min_off_s": 180, "max_starts_h": 3}, None),
                         (16, None, None, "hold_forever"), (20, None, None, "resume"), (25, {"fan": "circulate"}, None, None)]),
        scenario("heat_pump_winter", {"mode": "heat", "heat_sp": 70, "cool_sp": 76}, {"aux_delay_s": 300}, 24,
                 lambda i, r: None if (i // 300) % 7 == 3 else 25 + 25 * math.sin(i / 1200), 2, room0=64.0,
                 events=[(6, {"mode": "emergency_heat"}, None, None), (8, {"mode": "heat"}, None, None),
                         (10, None, {"has_aux": False}, None), (14, None, {"has_aux": True, "ob_energized": "heat"}, None)]),
        scenario("auto_with_mode_changes_and_sensor_glitches", {"mode": "auto", "heat_sp": 68, "cool_sp": 74}, {"min_on_s": 120}, 24,
                 lambda i, r: 60 + 30 * math.sin(i / 2000), 3, glitches=True,
                 events=[(3, {"mode": "off"}, None, None), (4, {"mode": "auto", "fan": "on"}, None, None),
                         (9, {"fan": "auto"}, {"differential": 2.0}, None), (15, {"mode": "cool"}, None, None),
                         (18, {"mode": "heat", "heat_sp": 72}, None, None)]),
        scenario("furnace_and_ac", {"mode": "heat", "heat_sp": 69, "cool_sp": 75}, {"heat_pump": False, "has_aux": False}, 12,
                 lambda i, r: 40 + 10 * math.sin(i / 900) if i < 2160 else 97, 4, room0=66.0,
                 events=[(6, {"mode": "cool", "cool_sp": 72}, None, None)]),
    ]
    lines = ["// Generated by tools/make_vectors.py from hvaccloud/thermostat.py. Do not edit.", "#pragma once", "#include <cmath>", "",
             "struct VStep { double now, room, outdoor; int year, mon, day, hour, min, sec, wday; int cfg;",
             "               bool Y, W, G, OB; int call, wait; double heat, cool; int source; double call_min; };",
             "struct VScenario { const char* name; int cfg_first; int first, count; };", ""]
    all_cfg, all_steps, scen = [], [], []
    for name, configs, steps in scenarios:
        scen.append((name, len(all_cfg), len(all_steps), len(steps)))
        base = len(all_cfg)
        all_cfg += configs
        for now, room, oat, when, cfg, rep in steps:
            o = rep["out"]
            all_steps.append("{%s,%s,%s,%d,%d,%d,%d,%d,%d,%d,%d,%s,%s,%s,%s,%d,%d,%s,%s,%d,%s}" % (
                c_num(now), c_num(room), c_num(oat), when.year, when.month, when.day, when.hour, when.minute, when.second,
                when.weekday(), base + cfg if cfg >= 0 else -1, *("true" if o[k] else "false" for k in ("Y", "W", "G", "OB")),
                CALL[rep["call"]], WAIT[rep["wait"]], c_num(rep["sp"]["heat"]), c_num(rep["sp"]["cool"]), SOURCE[rep["source"]],
                c_num(rep["call_min"])))
    # holds made on the screen: hold_until_next at assorted times (month / year ends included)
    sched = th.merged({"mode": "auto", "schedule": SCHEDULE})
    lines.append("static const char* const H_SETTINGS = " + json.dumps(json.dumps({"ver": 1, "settings": sched, "tech": {}})) + ";")
    lines.append("struct HVec { int year, mon, day, hour, min, sec, wday; bool has_until; int uy, um, ud, uh, umin; double heat, cool; };")
    lines.append("static const HVec H_VECS[] = {")
    rng = random.Random(9)
    times = [dt.datetime(2026, 12, 31, 23, 10), dt.datetime(2027, 2, 28, 22, 30), dt.datetime(2028, 2, 28, 23, 0), dt.datetime(2026, 10, 9, 22, 1)]
    times += [dt.datetime(2026, 10, 5) + dt.timedelta(minutes=rng.randrange(0, 60 * 24 * 400), seconds=rng.randrange(60)) for _ in range(60)]
    for w in times:
        h = th.hold_until_next(sched, w, cool=74)
        h_heat = min(h["heat"], 74 - th.AUTO_DEADBAND)
        u = dt.datetime.fromisoformat(h["until"]) if h["until"] else None
        lines.append("    {%d,%d,%d,%d,%d,%d,%d,%s,%d,%d,%d,%d,%d,%s,%s}," % (
            w.year, w.month, w.day, w.hour, w.minute, w.second, w.weekday(), "true" if u else "false",
            u.year if u else 0, u.month if u else 0, u.day if u else 0, u.hour if u else 0, u.minute if u else 0,
            repr(float(h_heat)), repr(float(h["cool"]))))
    lines += ["};", ""]
    lines.append("static const char* const V_CONFIGS[] = {")
    lines += ["    " + json.dumps(c) + "," for c in all_cfg]
    lines += ["};", "", "static const VStep V_STEPS[] = {"]
    lines += ["    " + s + "," for s in all_steps]
    lines += ["};", "", "static const VScenario V_SCENARIOS[] = {"]
    lines += ['    {"%s", %d, %d, %d},' % s for s in scen]
    lines += ["};", ""]
    out = os.path.join(HERE, "..", "test", "test_core", "vectors.h")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"{len(all_steps)} steps, {len(all_cfg)} configs -> {os.path.relpath(out)}")
    for name, _, _, steps in [(n, c, 0, s) for n, c, s in scenarios]:
        ys = sum(1 for s in steps if s[5]["out"]["Y"])
        ws = sum(1 for s in steps if s[5]["out"]["W"])
        waits = {w for s in steps for w in [s[5]["wait"]] if w}
        print(f"  {name}: {len(steps)} steps, Y on {ys}, W on {ws}, waits {sorted(waits)}")


if __name__ == "__main__":
    main()
