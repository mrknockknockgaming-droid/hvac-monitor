import datetime as dt
import random

import pytest

from hvaccloud import thermostat as th

MON_9AM = dt.datetime(2026, 10, 5, 9, 0)       # a Monday
T0 = MON_9AM.timestamp()


def run(ctrl, temps, outdoor=95.0, start=T0, step=10):
    """Feed a series of room temperatures; returns the reports."""
    out = []
    for k, room in enumerate(temps):
        now = start + k * step
        out.append(ctrl.step(room, outdoor(now) if callable(outdoor) else outdoor, now, dt.datetime.fromtimestamp(now)))
    return out


def test_cooling_with_hysteresis_and_blower():
    c = th.Controller({"mode": "cool", "cool_sp": 76})
    r = run(c, [76.2, 76.6])                     # starts at setpoint + 0.5 (differential 1.0)
    assert r[0]["out"]["Y"] is False and r[1]["out"]["Y"] is True and r[1]["out"]["G"] is True
    assert r[1]["out"]["OB"] is True             # O energized in cooling (the default)
    r = run(c, [76.0] * 40 + [75.4], start=T0 + 20)
    assert r[-2]["out"]["Y"] is True and r[-1]["out"]["Y"] is False   # runs until setpoint - 0.5


def test_minimum_off_and_on_times_and_starts_per_hour():
    tech = {"min_on_s": 300, "min_off_s": 300, "max_starts_h": 2}
    c = th.Controller({"mode": "cool", "cool_sp": 76}, tech)
    r = run(c, [77.0, 75.0])                     # satisfied 10 s after starting
    assert r[1]["out"]["Y"] is True and r[1]["wait"] == "min_on"
    r = run(c, [75.0] * 31, start=T0 + 20)       # runs out its 5 min, then stops
    assert r[-1]["out"]["Y"] is False
    r = run(c, [77.0] * 3, start=T0 + 330)       # wants to restart at once
    assert all(x["out"]["Y"] is False and x["wait"] == "min_off" for x in r)
    r = run(c, [77.0], start=T0 + 640)
    assert r[0]["out"]["Y"] is True              # second start of the hour
    run(c, [75.0] * 32, start=T0 + 650)
    r = run(c, [77.0], start=T0 + 1300)
    assert r[0]["out"]["Y"] is False and r[0]["wait"] == "max_starts"


def test_mode_off_stops_at_once_and_bad_sensor_fails_safe():
    c = th.Controller({"mode": "cool", "cool_sp": 76})
    run(c, [78.0])
    c.configure({"mode": "off"})
    assert run(c, [78.0], start=T0 + 10)[0]["out"] == {"Y": False, "W": False, "G": True, "OB": True}  # fan purge only
    c = th.Controller({"mode": "cool", "cool_sp": 76})
    run(c, [78.0])
    r = run(c, [None, 200.0], start=T0 + 10)
    assert all(x["out"]["Y"] is False and x["out"]["G"] is False and x["wait"] == "sensor" for x in r)


def test_heat_pump_heating_aux_and_lockouts():
    tech = {"aux_droop_f": 2.0, "aux_delay_s": 600, "aux_lockout_f": 35.0, "comp_lockout_f": 5.0}
    c = th.Controller({"mode": "heat", "heat_sp": 70}, tech)
    r = run(c, [69.0], outdoor=40.0)
    assert r[0]["out"]["Y"] and not r[0]["out"]["W"] and r[0]["out"]["OB"] is False   # O off in heating
    r = run(c, [67.5] * 70, outdoor=40.0, start=T0 + 10)            # drooping, but 40 F > aux lockout
    assert not any(x["out"]["W"] for x in r)
    c = th.Controller({"mode": "heat", "heat_sp": 70}, tech)
    r = run(c, [67.5] * 70, outdoor=25.0)                            # cold enough: aux joins after 10 min
    assert not r[30]["out"]["W"] and r[-1]["out"]["W"] and r[-1]["out"]["Y"]
    c = th.Controller({"mode": "heat", "heat_sp": 70}, tech)
    r = run(c, [68.0], outdoor=-2.0)                                  # below compressor lockout: aux only
    assert r[0]["out"] == {"Y": False, "W": True, "G": True, "OB": False}
    c = th.Controller({"mode": "emergency_heat", "heat_sp": 70}, tech)
    r = run(c, [68.0], outdoor=50.0)
    assert r[0]["out"]["W"] and not r[0]["out"]["Y"]


def test_reversing_valve_never_switches_while_running():
    c = th.Controller({"mode": "auto", "heat_sp": 68, "cool_sp": 76}, {"min_on_s": 60, "min_off_s": 60})
    rng = random.Random(4)
    temps, t = [], 72.0
    for _ in range(3000):
        t += rng.uniform(-0.6, 0.6)
        temps.append(max(60, min(84, t)))
    prev = None
    for r in run(c, temps):
        if prev and prev["out"]["Y"] and r["out"]["Y"]:
            assert r["out"]["OB"] == prev["out"]["OB"]
        prev = r


@pytest.mark.parametrize("seed", range(5))
def test_safety_holds_over_a_random_day(seed):
    """Whatever the room and outdoor temperatures do, the rules hold at every step."""
    tech = {"min_on_s": 180, "min_off_s": 300, "max_starts_h": 4}
    c = th.Controller({"mode": "auto", "heat_sp": 68, "cool_sp": 75}, tech)
    rng = random.Random(seed)
    room, outdoor, now = 72.0, 60.0, T0
    last_on = last_off = None
    starts = []
    for _ in range(8640):                               # a day at 10 s
        room = max(55, min(90, room + rng.uniform(-0.4, 0.4)))
        outdoor = max(-10, min(110, outdoor + rng.uniform(-0.3, 0.3)))
        was = c.out["Y"]
        r = c.step(room, outdoor, now, dt.datetime.fromtimestamp(now))
        y = r["out"]["Y"]
        if y and not was:
            assert last_off is None or now - last_off >= tech["min_off_s"]
            starts = [s for s in starts if now - s < 3600] + [now]
            assert len(starts) <= tech["max_starts_h"]
            last_on = now
        if was and not y:
            assert now - last_on >= tech["min_on_s"]
            last_off = now
        if r["out"]["W"]:
            assert outdoor <= 35.0                       # aux only below its lockout
        assert not (r["out"]["Y"] and not r["out"]["G"])  # blower with the compressor
        now += 10


def test_schedule_and_holds():
    s = {"mode": "cool", "schedule": [
        {"days": [0, 1, 2, 3, 4], "at": "06:00", "heat": 68, "cool": 76},
        {"days": [0, 1, 2, 3, 4], "at": "08:30", "heat": 62, "cool": 82},
        {"days": [0, 1, 2, 3, 4], "at": "17:00", "heat": 68, "cool": 76},
        {"days": [0, 1, 2, 3, 4, 5, 6], "at": "22:00", "heat": 66, "cool": 78}]}
    assert th.validate(s) == []
    heat, cool, src, nxt = th.schedule_now(s, MON_9AM)
    assert (heat, cool, src) == (62, 82, "schedule") and nxt == "2026-10-05T17:00:00"
    assert th.schedule_now(s, dt.datetime(2026, 10, 5, 3, 0))[1] == 78            # Monday 3 AM: Sunday 22:00 still on
    hold = th.hold_until_next(s, MON_9AM, cool=74)
    assert hold == {"heat": 62, "cool": 74, "until": "2026-10-05T17:00:00"}
    held = {**s, "hold": hold}
    assert th.schedule_now(held, MON_9AM)[1:3] == (74, "hold")
    assert th.schedule_now(held, dt.datetime(2026, 10, 5, 17, 1))[1:3] == (76, "schedule")   # hold expired
    assert th.schedule_now({"mode": "cool", "hold": {"heat": 66, "cool": 72, "until": None}}, MON_9AM)[1:3] == (72, "hold")


def test_validation():
    assert th.validate({"mode": "cool", "heat_sp": 74, "cool_sp": 75}) != []      # under the 3 F gap
    assert th.validate({"mode": "party"}) != [] and th.validate({"cool_sp": 99}) != []
    assert th.validate({"schedule": [{"days": [9], "at": "25:00", "heat": 68, "cool": 76}]}) != []


def test_cloud_flags():
    hot = th.flags({"room": {"t": 93.0}}, {})
    assert [f["code"] for f in hot] == ["room_hot"]
    slow = th.flags({"room": {"t": 80.5}, "call": "cool", "call_min": 120, "sp": {"cool": 76}}, {})
    assert [f["code"] for f in slow] == ["setpoint_not_reached"]
    assert th.flags({"room": {"t": 77.0}, "call": "cool", "call_min": 120, "sp": {"cool": 76}}, {}) == []
    mismatch = th.flags({"room": {"t": 75.0}, "out": {"Y": True}}, {"Y": False})
    assert [f["code"] for f in mismatch] == ["call_mismatch"]
    s = th.apply({"flags": []}, {"online": False, "data": None})
    assert [(f["code"], f["node"]) for f in s["flags"]] == [("node_offline", "thermostat")]
    assert th.apply({"flags": []}, None) == {"flags": [], "tstat": None}


# ---------------------------------------------------------------- cloud side
def test_settings_holds_and_tech_settings_over_the_api(sessions, tables):
    import json as _json
    import time
    from fastapi.testclient import TestClient
    from hvaccloud.api import create_app
    from hvaccloud.ingest import Ingest
    from tests.test_auth import XRW, login
    from tests.test_calc import COOL_IN, COOL_OUT
    from hvaccloud import auth
    from hvaccloud.db import Account, System, SystemMember, User

    with sessions() as s, s.begin():
        a = Account(name="A", email="a@example.com")
        s.add(a)
        s.flush()
        home = System(account_id=a.id, name="Home", site_id="home")
        s.add(home)
        s.flush()
        owner = User(email="owner@example.com", role="homeowner", password_hash=auth.hash_password("correct horse battery"))
        tech = User(email="tech@example.com", role="contractor", account_id=a.id,
                    password_hash=auth.hash_password("correct horse battery"))
        s.add_all([owner, tech])
        s.flush()
        s.add(SystemMember(system_id=home.id, user_id=owner.id))
        sid = home.id
    sent = []
    app = create_app(sessions, publisher=lambda topic, payload, retain=False: sent.append((topic, payload, retain)) or True)
    owner_c, tech_c = TestClient(app), TestClient(app)
    login(owner_c, "owner@example.com")
    login(tech_c, "tech@example.com")
    url = f"/api/systems/{sid}/thermostat"

    v = owner_c.get(url).json()
    assert v["present"] is False and v["version"] == 0 and v["settings"]["mode"] == "cool"
    r = owner_c.put(url, json={"mode": "auto", "heat_sp": 67, "cool_sp": 77}, headers=XRW)
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["sent"]
    topic, payload, retain = sent[-1]
    assert topic == "hvac/home/thermostat/config" and retain and payload["ver"] == 1
    assert payload["settings"]["mode"] == "auto" and payload["tech"]["min_off_s"] == 300
    assert owner_c.put(url, json={"heat_sp": 76}, headers=XRW).status_code == 422          # too close to cooling
    assert owner_c.put(url, json={"mode": "party"}, headers=XRW).status_code == 422

    r = owner_c.post(url + "/hold", json={"cool": 74}, headers=XRW).json()
    assert r["settings"]["hold"] == {"heat": 67, "cool": 74, "until": None}                  # no schedule: until resumed
    assert r["now"]["source"] == "hold" and r["now"]["cool"] == 74
    r = owner_c.post(url + "/hold", json={"heat": 76}, headers=XRW).json()
    assert r["settings"]["hold"]["cool"] == 79                                              # pushed along, not refused
    r = owner_c.delete(url + "/hold", headers=XRW).json()
    assert r["settings"]["hold"] is None and r["version"] == 4

    assert owner_c.put(url + "/tech", json={"min_off_s": 120}, headers=XRW).status_code == 403   # technician only
    assert tech_c.put(url + "/tech", json={"min_off_s": 30}, headers=XRW).status_code == 422     # below the floor
    assert tech_c.put(url + "/tech", json={"bogus": 1}, headers=XRW).status_code == 422
    r = tech_c.put(url + "/tech", json={"min_off_s": 240, "aux_lockout_f": 30, "tz": "America/Denver"}, headers=XRW).json()
    assert r["tech"]["min_off_s"] == 240 and r["tech"]["tz"] == "America/Denver"
    assert sent[-1][1]["tech"]["aux_lockout_f"] == 30

    # the thermostat reports in; the next indoor/outdoor reading carries its state and flags
    ingest = Ingest(sessions, tables)
    now = time.time()
    report = {"node": "thermostat", "cfg_ver": 5, "room": {"t": 94.0}, "mode": "auto", "fan": "auto",
              "sp": {"heat": 67, "cool": 77}, "source": "manual", "out": {"Y": True, "W": False, "G": True, "OB": True},
              "call": "cool", "call_min": 3.0, "wait": None, "err": []}
    assert ingest.handle("hvac/home/thermostat/telemetry", _json.dumps(report).encode(), now=now - 2) == report
    ingest.handle("hvac/home/indoor/telemetry", _json.dumps(COOL_IN).encode(), now=now - 1)
    snap = ingest.handle("hvac/home/outdoor/telemetry", _json.dumps(COOL_OUT).encode(), now=now)
    assert snap["tstat"]["sp"] == {"heat": 67, "cool": 77}
    assert "room_hot" in {f["code"] for f in snap["flags"]}
    v = owner_c.get(url).json()
    assert v["present"] and v["applied"] and v["report"]["room"]["t"] == 94.0
    assert owner_c.get(f"/api/systems/{sid}/latest").json()["nodes"]["thermostat"]["data"]["cfg_ver"] == 5


def test_systems_without_a_thermostat_are_unchanged(sessions, seeded, tables):
    import json as _json
    from hvaccloud.ingest import Ingest
    from tests.test_calc import COOL_IN
    snap = Ingest(sessions, tables).handle("hvac/home/indoor/telemetry", _json.dumps(COOL_IN).encode(), now=1.8e9)
    assert snap["tstat"] is None and all(f.get("node") != "thermostat" for f in snap["flags"])
