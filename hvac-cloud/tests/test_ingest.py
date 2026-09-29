import json

from sqlalchemy import func, select

from hvaccloud.db import Command, Device, Snapshot, System, Telemetry
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

T0 = 1_790_000_000.0


def msg(i, site, node, kind, data, now):
    return i.handle(f"hvac/{site}/{node}/{kind}", json.dumps(data).encode(), now=now)


def test_unknown_site_and_bad_topics_ignored(sessions, seeded, tables):
    i = Ingest(sessions, tables)
    assert msg(i, "nobody", "outdoor", "telemetry", COOL_OUT, T0) is None
    assert i.handle("hvac/home/attic/telemetry", b"{}", now=T0) is None
    assert i.handle("hvac/home/outdoor/telemetry", b"not json", now=T0) is None
    with sessions() as s:
        assert s.scalar(select(func.count()).select_from(Telemetry)) == 0


def test_telemetry_stores_and_derives(sessions, seeded, tables):
    i = Ingest(sessions, tables)
    msg(i, "home", "indoor", "telemetry", COOL_IN, T0)
    snap = msg(i, "home", "outdoor", "telemetry", COOL_OUT, T0 + 1)
    assert snap["mode"] == "cooling" and snap["sh"] is not None and snap["dt"] == 19.0
    with sessions() as s:
        assert {d.node for d in s.scalars(select(Device))} == {"indoor", "outdoor"}
        assert s.scalar(select(func.count()).select_from(Snapshot)) == 2
        assert s.scalar(select(System).where(System.site_id == "home")).run_started_at is not None


def test_run_time_survives_restart_and_stale_nodes_drop(sessions, seeded, tables):
    msg(Ingest(sessions, tables), "home", "indoor", "telemetry", COOL_IN, T0)
    msg(Ingest(sessions, tables), "home", "outdoor", "telemetry", COOL_OUT, T0 + 1)
    fresh = Ingest(sessions, tables)                       # restarted worker, empty cache
    snap = msg(fresh, "home", "outdoor", "telemetry", COOL_OUT, T0 + 11 * 60)
    assert snap["run_min"] >= 11                           # run start came from the database
    assert any("Indoor node is offline" in f["text"] for f in snap["flags"])   # indoor reading is 11 min old


def test_status_and_will(sessions, seeded, tables):
    i = Ingest(sessions, tables)
    msg(i, "home", "outdoor", "status", {"online": True, "fw": "0.1.0", "ip": "192.168.1.247", "rssi": -59}, T0)
    msg(i, "home", "outdoor", "status", {"online": False}, T0 + 5)     # last will
    with sessions() as s:
        d = s.scalar(select(Device))
        assert d.fw == "0.1.0" and d.ip == "192.168.1.247" and d.connected is False


def test_reply_pairs_with_command(sessions, seeded, tables):
    i = Ingest(sessions, tables)
    msg(i, "home", "outdoor", "telemetry", COOL_OUT, T0)
    with sessions() as s, s.begin():
        d = s.scalar(select(Device))
        s.add_all([Command(device_id=d.id, payload={"cmd": "cal_zero", "ch": "p_liq"}, sent=True),
                   Command(device_id=d.id, payload={"cmd": "fitted", "ch": "t_tsuc", "on": True}, sent=True)])
    msg(i, "home", "outdoor", "reply", {"cmd": "fitted", "ok": True}, T0 + 2)
    msg(i, "home", "outdoor", "reply", {"cmd": "restart", "ok": True}, T0 + 3)     # nobody asked
    with sessions() as s:
        rows = {(c.payload or {}).get("cmd"): c for c in s.scalars(select(Command))}
        assert rows["fitted"].reply == {"cmd": "fitted", "ok": True}
        assert rows["cal_zero"].reply is None
        assert rows[None].reply["cmd"] == "restart"


def test_reply_skips_unsent_and_lost_commands(sessions, seeded, tables):
    i = Ingest(sessions, tables)
    msg(i, "home", "outdoor", "telemetry", COOL_OUT, T0)
    with sessions() as s, s.begin():
        d = s.scalar(select(Device))
        s.add_all([Command(device_id=d.id, payload={"cmd": "cal_zero", "n": 1}, sent=False),   # never went out
                   Command(device_id=d.id, payload={"cmd": "cal_zero", "n": 2}, sent=True),    # lost
                   Command(device_id=d.id, payload={"cmd": "cal_zero", "n": 3}, sent=True)])
    msg(i, "home", "outdoor", "reply", {"cmd": "cal_zero", "ok": True}, T0 + 1)
    with sessions() as s:
        replied = [c.payload["n"] for c in s.scalars(select(Command)) if c.reply]
        assert replied == [3]
