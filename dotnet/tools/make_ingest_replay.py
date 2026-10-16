"""Generate tests/Fullscope.Cloud.Tests/vectors/replay_seed.db and replay.json: the Python ingest
worker (hvac-cloud/hvaccloud/ingest.py) run over four simulated hours of MQTT messages for three
systems (one with equipment, an electrical module and a display thermostat), with faults, outages,
thermostat screen changes, command replies, bad messages and the once-a-minute sweep.

replay_seed.db is the database before the first message (made by the Python code, so the C# test
also proves it can use a Python-made database). replay.json lists every message with a hash of what
ingest returned, the emails and MQTT publishes it made, and hashes of every row it left in the
database. The C# test replays the same messages on a copy of the seed and must match all of it.

Run from the dotnet folder (any time zone; emails are rendered in UTC):

    ..\\hvac-cloud\\.venv\\Scripts\\python.exe tools\\make_ingest_replay.py
"""
import datetime as dt
import hashlib
import json
import math
import os
import random
import sys
import time

os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "hvac-cloud"))
from sqlalchemy import create_engine, select  # noqa: E402

from hvaccloud import alerts  # noqa: E402
from hvaccloud.db import (Account, AlertMute, AlertPrefs, Command, Device, Equipment, ServiceInfo, System,  # noqa: E402
                          SystemMember, ThermostatConfig, User, init_db, session_factory)
from hvaccloud.ingest import Ingest  # noqa: E402
from hvaccloud.refrigerants import Tables  # noqa: E402

OUT = os.path.join(HERE, "..", "tests", "Fullscope.Cloud.Tests", "vectors")
T0 = 1_791_241_200.0          # 2026-10-05 23:00 UTC: crosses midnight, so runtime spans two days
HOURS = 4
STEP = 10
APP_URL = "http://localhost:8000/app/"


def h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def iso(t):
    return None if t is None else t.isoformat(sep=" ")


def seed(path):
    if os.path.exists(path):
        os.remove(path)
    engine = create_engine("sqlite:///" + path)
    init_db(engine)
    sessions = session_factory(engine)
    with sessions() as s, s.begin():
        a = Account(name="Desert Air", email="office@desertair.example")
        s.add(a)
        s.flush()
        home = System(account_id=a.id, name="Home", site_id="home", refrigerant="R-410A", atm_psia=14.0)
        plain = System(account_id=a.id, name="Plain", site_id="plain", refrigerant="R-32", heat_pump=False, atm_psia=14.7)
        pump = System(account_id=a.id, name="Pump", site_id="pump", refrigerant="R-454B", ob_energized="heat", atm_psia=13.6)
        s.add_all([home, plain, pump])
        s.flush()
        s.add(Equipment(system_id=home.id, metering="txv", sc_target=10.0, sc_tolerance=3.0, comp_rla=14.1, fan_fla=1.4,
                        cap_herm_uf=45.0, cap_fan_uf=5.0))
        s.add(Equipment(system_id=pump.id, metering="piston", sc_tolerance=3.0))
        u1 = User(email="owner@home.example", name="Owner", role="homeowner")
        u2 = User(email="spouse@home.example", name="Spouse", role="homeowner")
        s.add_all([u1, u2])
        s.flush()
        s.add_all([SystemMember(system_id=home.id, user_id=u1.id), SystemMember(system_id=home.id, user_id=u2.id),
                   SystemMember(system_id=pump.id, user_id=u1.id)])
        s.add(ServiceInfo(system_id=home.id, name="Desert Air", phone="555-0100", email="tech@desertair.example"))
        s.add(AlertPrefs(system_id=plain.id, email_owner=True, email_contractor=False))
        s.add(AlertMute(system_id=home.id, code="cap_fan", until=dt.datetime.fromtimestamp(T0 + 7200, dt.timezone.utc)))
        s.add(ThermostatConfig(system_id=home.id, settings={"mode": "cool", "cool_sp": 75.0,
                               "schedule": [{"days": [0, 1, 2, 3, 4], "at": "06:00", "heat": 68, "cool": 75}]},
                               tech={"tz": "America/Phoenix"}, version=3, sent=True))
    engine.dispose()


class Mailer:
    enabled = True

    def __init__(self, log):
        self.log = log

    def send(self, to, subject, body):
        self.log.append({"to": to, "subject": subject, "body": body})
        return True


def r(rng, x, spread, digits=1):
    return round(x + rng.uniform(-spread, spread), digits)


def outdoor(rng, on, warm, oat, fault):
    p_vap = (150 if fault == "low_sh" else 120) + rng.uniform(-1, 1) if on else 300
    return {"node": "outdoor", "fw": "0.2.0", "rssi": rng.choice([-61, -62, -60]),
            "mode": {"Y": on, "OB": on},
            "p": {"liq": r(rng, 345 + 15 * warm, 2) if on else 300, "vap": round(p_vap, 1), "tsuc": None},
            "t": {"suc": r(rng, 52, 0.5) if on else round(oat - 5, 1), "liq": r(rng, oat + 6, 0.5) if on else round(oat - 2, 1),
                  "tsuc": None, "dis": None},
            "air": {"t": round(oat, 1), "rh": 12.0}, "err": ["sht30"] if fault == "sht" else []}


def indoor(rng, on, warm, fault):
    return {"node": "indoor", "fw": "0.2.0", "rssi": -55, "mode": {"Y": on, "W": False, "G": on, "OB": on},
            "air": {"supply": r(rng, 76 - (9 if fault == "dirty_filter" else 19) * warm, 0.3) if on else 77,
                    "return": r(rng, 76, 0.3), "dt": None}, "err": []}


def electrical(rng, on, warm, fault):
    comp = 0.0 if not on or fault == "comp_tripped" else 10.8 + 1.5 * warm + rng.uniform(-0.2, 0.2)
    fan = 0.0 if not on else 1.1 + rng.uniform(-0.03, 0.03)
    cap_v = 370.0 + rng.uniform(-3, 3) if on else 0.0
    herm_uf = 38.0 if fault == "weak_cap" else 45.0
    return {"node": "electrical", "contactor": on,
            "v": {"line": r(rng, 240 - (3 if on else 0), 1), "contactor": round(0.05 + rng.uniform(0, 0.02), 2) if on else None},
            "i": {"comp": round(comp, 2), "fan": round(fan, 2)},
            "cap": {"herm": {"v": round(cap_v, 1), "i": round(herm_uf * cap_v / 2652.6, 3) if comp else 0.0},
                    "fan": {"v": round(cap_v - 5, 1) if on else 0.0, "i": round(4.5 * (cap_v - 5) / 2652.6, 3) if fan else 0.0}},
            "start": {"peak_a": 58.0, "ms": 1450 if fault == "slow_start" else 230}, "err": []}


def thermostat(rng, on, room, k):
    return {"node": "thermostat", "fw": "0.1.0", "cfg_ver": 3 + k, "room": {"t": round(room, 1), "rh": 41.0},
            "mode": "cool", "fan": "auto", "sp": {"heat": 68.0, "cool": 75.0}, "source": "schedule",
            "out": {"Y": on, "W": False, "G": on, "OB": True}, "call": "cool" if on else None,
            "call_min": None, "wait": None, "err": []}


def script(rng):
    """(time, topic, payload) for every message, plus "sweep" entries once a minute."""
    msgs = []
    n = int(HOURS * 3600 / STEP)
    room = 77.0
    for i in range(n):
        t = T0 + i * STEP + rng.uniform(0, 0.2)
        k = i * STEP / 60                                   # minutes
        oat = 95 + 4 * math.sin(k / 40)
        for site, period, on_min, offset in (("home", 25, 20, 0), ("plain", 30, 18, 7), ("pump", 25, 12, 3)):
            if site == "plain" and 100 <= k < 125:        # outage: no data, then back
                continue
            if site == "pump" and 160 <= k < 172 and i % 2:  # flaky indoor: every other indoor message lost below
                pass
            phase = (k + offset) % period
            on = phase < on_min
            warm = min(1.0, phase / 4) if on else 0.0
            fault = None
            if site == "home" and 40 <= k < 70:
                fault = "low_sh"
            if site == "home" and 130 <= k < 150:
                fault = "weak_cap"
            if site == "home" and 180 <= k < 200:
                fault = "comp_tripped"
            if site == "plain" and 60 <= k < 90:
                fault = "dirty_filter"
            if site == "pump" and 200 <= k < 215:
                fault = "sht"
            base = f"hvac/{site}/"
            if site == "home":
                room += STEP * ((oat - room) / 18000 - (0.35 / 60 if on else 0) * warm)
                if i % 3 == 0:
                    msgs.append((t, base + "thermostat/telemetry", thermostat(rng, on, room, 0 if k < 120 else 1)))
                if not (210 <= k < 215):                   # electrical module drops out for 5 min
                    msgs.append((t + 0.01, base + "electrical/telemetry", electrical(rng, on, warm, fault)))
            out = outdoor(rng, on, warm, oat, fault)
            if i % 7 == 0:
                out["ts"] = round(t - rng.choice([0.5, 3, 90000, -5000]), 1)    # node's own time: some implausible
            msgs.append((t + 0.02, base + "outdoor/telemetry", out))
            if not (site == "pump" and 160 <= k < 172 and i % 2):
                msgs.append((t + 0.03, base + "indoor/telemetry", indoor(rng, on, warm, fault)))
        if i % 6 == 5:
            msgs.append((T0 + i * STEP + 5, "sweep", None))
        if i == 30:
            msgs.append((T0 + i * STEP + 1, "hvac/home/outdoor/status", {"online": True, "fw": "0.2.0", "ip": "192.168.1.247",
                                                                            "mac": "AA:BB:CC:DD:EE:01", "rssi": -60}))
            msgs.append((T0 + i * STEP + 2, "hvac/home/indoor/status", {"online": True, "fw": "0.2.0", "ip": "192.168.1.121"}))
        if i == 400:
            msgs.append((T0 + i * STEP + 1, "hvac/plain/outdoor/status", {"online": False}))
        if i == 50:
            msgs.append((T0 + i * STEP + 3, "hvac/home/outdoor/reply", {"cmd": "status", "ok": True}))
            msgs.append((T0 + i * STEP + 4, "hvac/home/outdoor/reply", {"cmd": "cal_zero", "ch": "p_liq", "ok": True}))
            msgs.append((T0 + i * STEP + 4.5, "hvac/home/outdoor/reply", {"cmd": "restart", "ok": True}))
        if i == 90:
            msgs.append((T0 + i * STEP + 3, "hvac/home/thermostat/request", {"cool_sp": 74.0}))
        if i == 95:
            msgs.append((T0 + i * STEP + 3, "hvac/home/thermostat/request", {"hold": {"heat": 66, "cool": 72, "until": "2026-10-06T08:30"}}))
        if i == 96:
            msgs.append((T0 + i * STEP + 3, "hvac/home/thermostat/request", {"cool_sp": 99}))
        if i == 97:
            msgs.append((T0 + i * STEP + 3, "hvac/home/thermostat/request", {"resume": True, "mode": "auto"}))
        if i == 120:
            msgs.append((T0 + i * STEP + 3, "hvac/nobody/outdoor/telemetry", {"x": 1}))
            msgs.append((T0 + i * STEP + 3, "hvac/home/attic/telemetry", {"x": 1}))
            msgs.append((T0 + i * STEP + 3, "hvac/home/outdoor/telemetry", "not json"))
            msgs.append((T0 + i * STEP + 3, "hvac/home/outdoor/telemetry", [1, 2]))
            msgs.append((T0 + i * STEP + 3, "hvac/home/outdoor/request", {"x": 1}))
    msgs.sort(key=lambda m: m[0])
    return msgs


def dump(sessions):
    """Every row ingest can write, hashed, with columns that hold wall-clock time left out."""
    from hvaccloud.db import Alert, RuntimeDay, Snapshot, Telemetry
    out = {}
    with sessions() as s:
        out["systems"] = [{"id": x.id, "run_started_at": iso(x.run_started_at)} for x in s.scalars(select(System).order_by(System.id))]
        out["devices"] = [{"id": d.id, "system_id": d.system_id, "node": d.node, "fw": d.fw, "ip": d.ip, "mac": d.mac,
                           "rssi": d.rssi, "connected": d.connected, "last_seen": iso(d.last_seen), "last_status": d.last_status}
                          for d in s.scalars(select(Device).order_by(Device.id))]
        out["telemetry"] = h([[t.device_id, iso(t.time), t.data] for t in s.scalars(select(Telemetry).order_by(Telemetry.device_id, Telemetry.time))])
        out["snapshots"] = [[x.system_id, iso(x.time), x.mode, h(x.data)] for x in s.scalars(select(Snapshot).order_by(Snapshot.system_id, Snapshot.time))]
        out["alerts"] = [{"id": a.id, "system_id": a.system_id, "code": a.code, "node": a.node, "level": a.level, "text": a.text,
                          "started_at": iso(a.started_at), "last_seen": iso(a.last_seen), "raised_at": iso(a.raised_at),
                          "cleared_at": iso(a.cleared_at), "emailed": a.emailed_at is not None, "acked_at": iso(a.acked_at)}
                         for a in s.scalars(select(Alert).order_by(Alert.id))]
        out["runtime_days"] = [[x.system_id, x.day.isoformat(), round(x.blower_s, 6), round(x.compressor_s, 6)]
                               for x in s.scalars(select(RuntimeDay).order_by(RuntimeDay.system_id, RuntimeDay.day))]
        out["commands"] = [{"id": c.id, "device_id": c.device_id, "payload": c.payload, "sent": c.sent, "reply": c.reply,
                            "replied_at": iso(c.replied_at)} for c in s.scalars(select(Command).order_by(Command.id))]
        out["thermostat_configs"] = [{"system_id": c.system_id, "settings": c.settings, "tech": c.tech, "version": c.version,
                                      "sent": c.sent} for c in s.scalars(select(ThermostatConfig).order_by(ThermostatConfig.system_id))]
    return out


def main():
    seed_path = os.path.join(OUT, "replay_seed.db")
    seed(seed_path)
    work = os.path.join(OUT, "replay_work.db")
    with open(seed_path, "rb") as a, open(work, "wb") as b:
        b.write(a.read())
    sessions = session_factory(create_engine("sqlite:///" + work))
    with sessions() as s, s.begin():                     # commands the web app sent earlier
        d = Device(system_id=1, node="outdoor")
        s.add(d)
        s.flush()
        s.add_all([Command(device_id=d.id, payload={"cmd": "cal_zero", "ch": "p_liq"}, sent=True),
                   Command(device_id=d.id, payload={"cmd": "status"}, sent=False),
                   Command(device_id=d.id, payload={"cmd": "status"}, sent=True)])
    with open(work, "rb") as a, open(seed_path, "wb") as b:   # the seed includes them
        b.write(a.read())

    emails, published = [], []
    i = Ingest(sessions, Tables(lambda *a: None), mailer=Mailer(emails))
    i.spawn = lambda fn, *a: fn(*a)
    i.publish = lambda topic, payload: published.append({"topic": topic, "payload": payload})
    alerts.settings.APP_URL = APP_URL
    steps = []
    for t, topic, payload in script(random.Random(20261015)):
        e0, p0 = len(emails), len(published)
        if topic == "sweep":
            i.sweep(now=t)
            result = None
            raw = None
        else:
            raw = payload if isinstance(payload, str) else json.dumps(payload)
            result = i.handle(topic, raw.encode(), now=t)
        steps.append({"t": t, "topic": topic, "payload": raw, "result": None if result is None else h(result),
                      "emails": emails[e0:], "published": [{"topic": p["topic"], "payload": h(p["payload"])} for p in published[p0:]]})
    final = dump(sessions)
    sessions.kw["bind"].dispose()
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(work + ext):
            os.remove(work + ext)
    with open(os.path.join(OUT, "replay.json"), "w") as f:
        json.dump({"app_url": APP_URL, "steps": steps, "final": final}, f, indent=0)
    print(f"{len(steps)} steps, {sum(len(s['emails']) for s in steps)} emails, {sum(len(s['published']) for s in steps)} publishes")


if __name__ == "__main__":
    main()
