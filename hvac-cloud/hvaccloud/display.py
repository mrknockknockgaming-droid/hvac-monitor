"""What the display thermostat shows besides its own controls (roadmap phase 11).

- Homeowners: the monitoring system's alerts in plain words, and the same System health areas
  as the homeowner web page.
- Technicians (behind the service PIN): a few key numbers, the open diagnostics in technical
  words, and a trend of pressures and air temperatures with markers where the electrical
  module found a problem.

The cloud builds this "display feed" so the thermostat only has to draw it. Ingest publishes it
retained on hvac/<site>/thermostat/display (at most once a minute, and at once when an alert is
raised or cleared). GET /api/systems/{id}/thermostat/display returns the same, which the browser
prototype of the screen (web/thermostat.html) uses. The thermostat's heating and cooling never
depend on it.

Alerts here are the raised ones (held 5 minutes), not every passing flag, so the wall display
doesn't flicker.
"""
import datetime as dt

from sqlalchemy import select

from . import alerts, calc, settings
from .db import Alert, Device, Snapshot, as_utc

FEED_VERSION = 1
TREND_HOURS = 6
TREND_STEP_S = 120                   # 180 points per series: small enough for one MQTT message
TREND_KEYS = ("p_low", "p_high", "t_ret", "t_sup", "oat")
STEADY_MIN = 10

ELEC_LABEL = {                       # short technician labels for the trend markers
    "comp_not_running": "Compressor not running", "fan_not_running": "Condenser fan not running",
    "contactor_open": "Contactor not closing", "comp_amps_high": "Compressor amps high",
    "fan_amps_high": "Fan amps high", "cap_herm": "Compressor capacitor weak", "cap_fan": "Fan capacitor weak",
    "contactor_drop": "Contactor pitted", "voltage": "Line voltage", "slow_start": "Slow compressor start",
}
FAULT_CODES = ("comp_not_running", "fan_not_running", "contactor_open")
WORD = {"fault": "Service needed", "caution": "Check soon", "advisory": "Good to know", "ok": "Good",
        "offline": "No data"}
TECH_WORD = {"fault": "Fault", "caution": "Warning", "advisory": "Advisory"}
RANK = {"fault": 3, "caution": 2, "advisory": 1, "ok": 0}


def is_electrical(code, node=None):
    return code in ELEC_LABEL or node == "electrical"


def level_of(code):
    return alerts.LEVEL.get(code, "caution")


def _alert_item(a):
    title, why, todo = alerts.PLAIN.get(a.code, (a.text, a.text, ""))
    lvl = level_of(a.code)
    return {"id": a.id, "code": a.code, "node": a.node, "level": lvl, "word": WORD[lvl], "title": title,
            "why": why, "todo": todo, "tech": a.text, "tech_word": TECH_WORD.get(lvl, "Warning"),
            "since": as_utc(a.started_at).timestamp(), "acked": a.acked_at is not None}


def health(snap, fresh, codes, online, nodes_expected=2):
    """The System health rows, as on the homeowner web page (homeHealth in web/app.js)."""
    d = snap or {}
    mode = d.get("mode") or "idle"
    running = mode in ("cooling", "heating")
    steady = running and (d.get("run_min") or 0) >= STEADY_MIN

    def area(name, bad_codes, good, bad_level, bad_text):
        if not fresh:
            return {"name": name, "level": "offline", "word": WORD["offline"], "text": "Waiting for readings."}
        if any(c in codes for c in bad_codes):
            return {"name": name, "level": bad_level, "word": WORD[bad_level], "text": bad_text}
        text = (good if steady else "No problems found so far. The full check runs after 10 minutes of steady running."
                if running else "No problems found. Checked each time your system runs.")
        return {"name": name, "level": "ok", "word": WORD["ok"], "text": text}

    dt_ = calc.num(d.get("dt"))
    dt_text = (f"Air from your vents is about {round(abs(dt_))}° {'warmer' if mode == 'heating' else 'cooler'} than the air going in."
               if dt_ is not None else "Temperature change across the indoor coil looks right.")
    refr_fault = "sh_low" in codes
    outdoor_fault = any(c in codes for c in FAULT_CODES)
    elec = [c for c in ELEC_LABEL if c in codes]
    rows = [
        area("Heating" if mode == "heating" else "Cooling", ["dt_low"], dt_text, "caution",
             "Air from your vents isn't changing temperature as much as it should."),
        area("Comfort", ["room_hot", "room_cold", "setpoint_not_reached", "call_mismatch"],
             "The house is reaching your settings.", "fault" if ("room_hot" in codes or "room_cold" in codes) else "caution",
             "The house isn't reaching your settings. See the alerts."),
        area("Refrigerant", ["sh_low", "sh_high", "sc_low", "sc_high"], "Pressures and temperatures look normal for today's weather.",
             "fault" if refr_fault else "caution", "Refrigerant readings are outside the normal range."),
        area("Outdoor unit", ["ctoa_high"] + list(ELEC_LABEL), "Running normally.", "fault" if outdoor_fault else "caution",
             "Something in it needs attention." if elec else "It isn't releasing heat as well as it should."),
    ]
    sensor = "sensor_issue" in codes
    rows.append({"name": "Monitoring",
                 "level": "ok" if online >= nodes_expected and not sensor else "advisory" if online else "offline",
                 "word": "Connected" if online >= nodes_expected else "Partly connected" if online else "Offline",
                 "text": ("Monitors online." + (" One sensor isn't reporting." if sensor else "")) if online >= nodes_expected
                 else "A monitor isn't reporting." if online else "The monitors aren't reporting."})
    return rows


def trend(s, system, now, hours=TREND_HOURS, step=TREND_STEP_S):
    """Pressures and air temperatures averaged into `step`-second buckets, plus when it ran."""
    t0 = int((now - hours * 3600) // step * step)
    n = int((now - t0) // step) + 1
    sums = {k: [0.0] * n for k in TREND_KEYS}
    counts = {k: [0] * n for k in TREND_KEYS}
    on, seen = [0] * n, [0] * n
    rows = s.execute(select(Snapshot.time, Snapshot.data).where(
        Snapshot.system_id == system.id, Snapshot.time >= dt.datetime.fromtimestamp(t0, dt.timezone.utc))
        .order_by(Snapshot.time))
    for t, d in rows:
        i = int((as_utc(t).timestamp() - t0) // step)
        if not 0 <= i < n:
            continue
        seen[i] += 1
        on[i] += 1 if d.get("mode") in ("cooling", "heating") else 0
        for k in TREND_KEYS:
            v = calc.num(d.get(k))
            if v is not None:
                sums[k][i] += v
                counts[k][i] += 1
    series = {k: [round(sums[k][i] / counts[k][i], 1) if counts[k][i] else None for i in range(n)] for k in TREND_KEYS}
    return {"t0": t0, "step": step, "series": series,
            "on": [1 if seen[i] and on[i] * 2 >= seen[i] else 0 for i in range(n)]}


def markers(s, system, now, hours=TREND_HOURS):
    """Electrical problems in the trend window: when each started and (if it has) ended."""
    since = dt.datetime.fromtimestamp(now - hours * 3600, dt.timezone.utc)
    q = (select(Alert).where(Alert.system_id == system.id, Alert.raised_at.is_not(None))
         .where((Alert.cleared_at.is_(None)) | (Alert.cleared_at >= since)).order_by(Alert.started_at))
    out = []
    for a in s.scalars(q):
        if not is_electrical(a.code, a.node):
            continue
        out.append({"code": a.code, "level": level_of(a.code),
                    "label": ELEC_LABEL.get(a.code) or ("Electrical module offline" if a.code == "node_offline" else "Electrical sensor"),
                    "text": a.text, "start": as_utc(a.started_at).timestamp(),
                    "end": as_utc(a.cleared_at).timestamp() if a.cleared_at else None})
    return out


def build(s, system, now, stale=settings.STALE_SECONDS):
    """The whole feed for one system."""
    row = s.execute(select(Snapshot.time, Snapshot.data).where(Snapshot.system_id == system.id)
                    .order_by(Snapshot.time.desc()).limit(1)).first()
    snap = row.data if row else None
    fresh = bool(row) and now - as_utc(row.time).timestamp() <= max(stale, 90)
    open_ = list(s.scalars(select(Alert).where(Alert.system_id == system.id, Alert.raised_at.is_not(None),
                                                 Alert.cleared_at.is_(None))))
    items = sorted((_alert_item(a) for a in open_), key=lambda x: (-RANK[x["level"]], -x["since"]))
    codes = {a.code for a in open_}
    devices = s.scalars(select(Device).where(Device.system_id == system.id, Device.node.in_(calc.NODES)))
    online = sum(1 for d in devices if d.connected and d.last_seen and now - as_utc(d.last_seen).timestamp() <= stale)
    worst = max((x["level"] for x in items), key=lambda lvl: RANK[lvl], default="ok")
    if not fresh:
        headline, status = "We can't reach your system's monitors", "offline"
    elif items:
        headline, status = items[0]["title"], worst
    else:
        headline, status = "Everything looks good", "ok"
    d = snap or {}
    elec = d.get("elec") or {}
    key = {"mode": d.get("mode"), "run_min": d.get("run_min"), "sh": d.get("sh"), "sc": d.get("sc"), "dt": d.get("dt"),
           "p_low": d.get("p_low"), "p_high": d.get("p_high"), "oat": d.get("oat"),
           "line_v": elec.get("line_v"), "comp_a": elec.get("comp_a"), "fan_a": elec.get("fan_a")}
    return {"ver": FEED_VERSION, "time": now, "updated": as_utc(row.time).timestamp() if row else None,
            "fresh": fresh, "status": status, "word": WORD[status], "headline": headline,
            "alerts": items, "health": health(snap, fresh, codes, online),
            "tech": {"now": key, "has_elec": bool(d.get("elec")), "trend": trend(s, system, now),
                     "markers": markers(s, system, now)}}
