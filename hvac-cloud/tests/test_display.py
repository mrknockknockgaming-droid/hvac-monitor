import json

from sqlalchemy import select

from hvaccloud import display
from hvaccloud.db import Equipment, System
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT
from tests.test_electrical import reading

T0 = 1_790_000_000.0
TSTAT = {"node": "thermostat", "cfg_ver": 1, "room": {"t": 75.5}, "mode": "cool", "fan": "auto",
         "sp": {"heat": 68, "cool": 76}, "source": "manual", "out": {"Y": True, "W": False, "G": True, "OB": True},
         "call": "cool", "call_min": 20, "wait": None, "err": []}
WEAK_CAP = {"cap": {"herm": {"v": 370.0, "i": 5.5}}}       # 39 uF of 45


def feed_world(sessions, tables, thermostat=True):
    with sessions() as s, s.begin():
        sid = s.scalar(select(System.id).where(System.site_id == "home"))
        s.add(Equipment(system_id=sid, comp_rla=14.1, comp_lra=73.0, fan_fla=1.4, cap_herm_uf=45.0, cap_fan_uf=5.0))
    sent = []
    ingest = Ingest(sessions, tables, hold=0)
    ingest.publish = lambda topic, payload: sent.append((topic, payload))
    ingest.spawn = lambda fn, *a: fn(*a)

    def cycle(t, elec):
        if thermostat:
            ingest.handle("hvac/home/thermostat/telemetry", json.dumps(TSTAT).encode(), now=t)
        ingest.handle("hvac/home/electrical/telemetry", json.dumps(elec).encode(), now=t + 0.5)
        ingest.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN).encode(), now=t + 1)
        ingest.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=t + 2)
    return sid, sent, cycle


def test_feed_has_plain_alerts_health_and_electrical_markers(sessions, seeded, tables):
    sid, sent, cycle = feed_world(sessions, tables)
    for k in range(30):                              # 5 min healthy, then the capacitor weakens
        cycle(T0 + 10 * k, reading())
    for k in range(30, 60):
        cycle(T0 + 10 * k, reading(**WEAK_CAP))
    now = T0 + 600
    with sessions() as s:
        f = display.build(s, s.get(System, sid), now)
    assert f["fresh"] and f["status"] == "caution" and f["word"] == "Check soon"
    a = f["alerts"][0]
    assert a["code"] == "cap_herm" and a["title"].startswith("A part in your outdoor unit") and "capacitor" in a["tech"].lower()
    rows = {r["name"]: r for r in f["health"]}
    assert rows["Outdoor unit"]["level"] == "caution" and rows["Refrigerant"]["level"] == "ok"
    assert rows["Monitoring"]["word"] == "Connected"
    m = f["tech"]["markers"]
    assert [x["code"] for x in m] == ["cap_herm"] and m[0]["label"] == "Compressor capacitor weak"
    assert abs(m[0]["start"] - (T0 + 300)) < 5 and m[0]["end"] is None
    tr = f["tech"]["trend"]
    assert len(tr["series"]["p_low"]) == len(tr["on"]) == display.TREND_HOURS * 3600 // display.TREND_STEP_S + 1
    assert tr["series"]["p_high"][-2] is not None and tr["on"][-2] == 1 and tr["series"]["p_low"][0] is None
    assert f["tech"]["now"]["line_v"] == 240.0 and f["tech"]["has_elec"]
    assert len(json.dumps(f)) < 12000                 # fits one MQTT message for the thermostat

    # published retained to the thermostat: once a minute, and at once when the alert was raised
    topics = {t for t, _ in sent}
    assert topics == {"hvac/home/thermostat/display"}
    raised = [p for _, p in sent if any(x["code"] == "cap_herm" for x in p["alerts"])]
    assert raised and min(p["time"] for p in raised) < T0 + 305          # at once, not at the next minute
    assert len(sent) <= 12                                               # 240 messages in 10 min: ~once a minute


def test_no_thermostat_no_feed_and_offline_feed(sessions, seeded, tables):
    sid, sent, cycle = feed_world(sessions, tables, thermostat=False)
    for k in range(10):
        cycle(T0 + 10 * k, reading())
    assert sent == []
    with sessions() as s:
        f = display.build(s, s.get(System, sid), T0 + 3600)
    assert not f["fresh"] and f["status"] == "offline" and all(r["level"] in ("offline",) for r in f["health"][:4])
