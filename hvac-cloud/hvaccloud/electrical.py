"""Electrical module (roadmap phase 11): amps, line voltage, contactor drop and run capacitors.

The module reports as a third node, "electrical", on the same MQTT topics as the others
(hvac/<site>/electrical/telemetry). Whether it is its own board or extra channels on the outdoor
ESP32 doesn't matter here. Its telemetry:

    {"node": "electrical", "uptime": 312, "contactor": true,
     "v":   {"line": 241.0, "contactor": 0.05},          # V; drop across the closed contacts (null when open)
     "i":   {"comp": 11.8, "fan": 1.1},                  # A; compressor common wire, condenser fan
     "cap": {"herm": {"v": 372.0, "i": 6.3},             # V across each run-capacitor section and A through it
             "fan":  {"v": 368.0, "i": 0.69}},
     "start": {"peak_a": 58.0, "ms": 240},               # the latest compressor start
     "err": []}

Electrical readings don't make snapshots of their own: the next outdoor/indoor snapshot picks up
the newest one (they arrive every few seconds), adding "elec" and these flags. Judged against
the nameplate values on the Equipment page; rules that need a missing rating are skipped.
"""
from .calc import num, r1

NODE = "electrical"
CAP_CONSTANT = 2652.6         # uF = amps x 2652.6 / volts at 60 Hz (1e6 / 2 pi 60)
CAP_TOLERANCE = 0.06          # run capacitors are rated +/-6 %
CONTACTOR_WARN_V = 1.0        # over 1 V across closed contacts: pitted; over 2 V: replace
CONTACTOR_REPLACE_V = 2.0
SLOW_START_MS = 700           # a healthy compressor is up to speed in a fraction of a second
DEFAULT_VOLTS = (197.0, 253.0)   # a 208/230 V unit's allowed range, when the nameplate isn't entered
SETTLE_MIN = 1.0              # minutes of compressor call before amps are judged


def capacitance(section):
    """Run capacitor uF under load from the voltage across it and the current through it. None
    when its motor isn't running (no current through the capacitor, so nothing to measure)."""
    v, i = num((section or {}).get("v")), num((section or {}).get("i"))
    if v is None or i is None or v < 50 or i < 0.05:
        return None
    return round(i * CAP_CONSTANT / v, 1)


def derive(data, eq):
    """The values the technician view shows ("elec" in a snapshot)."""
    v, i, cap, start = data.get("v") or {}, data.get("i") or {}, data.get("cap") or {}, data.get("start") or {}
    rla, fla = getattr(eq, "comp_rla", None), getattr(eq, "fan_fla", None)
    comp, fan = num(i.get("comp")), num(i.get("fan"))
    herm, fanc = capacitance(cap.get("herm")), capacitance(cap.get("fan"))
    rated_h, rated_f = getattr(eq, "cap_herm_uf", None), getattr(eq, "cap_fan_uf", None)
    return {
        "contactor": bool(data.get("contactor")), "line_v": r1(num(v.get("line"))),
        "contactor_v": num(v.get("contactor")) if data.get("contactor") else None,
        "comp_a": r1(comp), "fan_a": r1(fan),
        "comp_pct_rla": round(100 * comp / rla) if comp is not None and rla else None,
        "fan_pct_fla": round(100 * fan / fla) if fan is not None and fla else None,
        "cap_herm_uf": herm, "cap_fan_uf": fanc,
        "cap_herm_pct": round(100 * herm / rated_h, 1) if herm is not None and rated_h else None,
        "cap_fan_pct": round(100 * fanc / rated_f, 1) if fanc is not None and rated_f else None,
        "start_peak_a": r1(num(start.get("peak_a"))), "start_ms": num(start.get("ms")),
    }


def flags(e, snap, eq):
    """Diagnostic flags from derived electrical values (same shape as calc.flags)."""
    f = []
    calling = bool(snap.get("Y"))
    settled = calling and (snap.get("run_min") or 0) >= SETTLE_MIN
    rla, fla = getattr(eq, "comp_rla", None), getattr(eq, "fan_fla", None)
    comp, fan = e["comp_a"], e["fan_a"]
    if settled and not e["contactor"]:
        f.append({"level": "alert", "code": "contactor_open",
                  "text": "Cooling call but the contactor isn't closed: check the coil and the low-voltage circuit"})
    running_floor = 0.1 * rla if rla else 1.0
    if settled and e["contactor"] and comp is not None and comp < running_floor:
        f.append({"level": "alert", "code": "comp_not_running",
                  "text": f"Contactor closed but the compressor draws {comp} A: overload tripped, failed capacitor or compressor"})
    compressor_on = comp is not None and comp >= running_floor
    if settled and compressor_on and fan is not None and fan < 0.2:
        f.append({"level": "alert", "code": "fan_not_running",
                  "text": f"Compressor running but the condenser fan draws {fan} A: fan motor or its capacitor"})
    if settled and compressor_on and rla and comp > rla:
        f.append({"level": "warn", "code": "comp_amps_high",
                  "text": f"Compressor draws {comp} A, above its {rla:g} A RLA"})
    if settled and fla and fan is not None and fan > fla * 1.1:
        f.append({"level": "warn", "code": "fan_amps_high", "text": f"Condenser fan draws {fan} A, above its {fla:g} A FLA"})
    for sec, label in (("herm", "compressor (HERM)"), ("fan", "fan")):
        pct = e[f"cap_{sec}_pct"]
        if compressor_on and pct is not None and abs(pct - 100) > CAP_TOLERANCE * 100:
            rated = getattr(eq, f"cap_{sec}_uf")
            f.append({"level": "warn", "code": f"cap_{sec}",
                      "text": f"Run capacitor {label} section measures {e[f'cap_{sec}_uf']} uF, rated {rated:g} uF (±6 %)"})
    if e["contactor_v"] is not None and e["contactor_v"] > CONTACTOR_WARN_V:
        f.append({"level": "warn", "code": "contactor_drop",
                  "text": f"{e['contactor_v']:g} V across the closed contactor: pitted contacts"
                          + (" (replace)" if e["contactor_v"] > CONTACTOR_REPLACE_V else " (watch; replace above 2 V)")})
    lo, hi = (getattr(eq, "volt_min", None) or DEFAULT_VOLTS[0]), (getattr(eq, "volt_max", None) or DEFAULT_VOLTS[1])
    if e["line_v"] is not None and e["contactor"] and not lo <= e["line_v"] <= hi:
        f.append({"level": "warn", "code": "voltage",
                  "text": f"Line voltage {e['line_v']:g} V while running, outside the unit's {lo:g}-{hi:g} V range"})
    if e["start_ms"] is not None and e["start_ms"] > SLOW_START_MS:
        f.append({"level": "warn", "code": "slow_start",
                  "text": f"Compressor took {e['start_ms']:g} ms to start (peak {e['start_peak_a']} A): weak capacitor, low voltage or a hard-starting compressor"})
    return f


def apply(snap, state, eq):
    """Add the electrical module's values and flags to a snapshot. state is the node's latest
    {"online", "data"} (None when no module has ever reported: then nothing is added)."""
    if state is None:
        snap["elec"] = None
        return snap
    if not state.get("online") or not state.get("data"):
        snap["elec"] = None
        snap["flags"] = snap.get("flags", []) + [{"level": "warn", "code": "node_offline", "node": NODE,
                                                  "text": "Electrical module is offline"}]
        return snap
    data = state["data"]
    e = derive(data, eq)
    snap["elec"] = e
    extra = flags(e, snap, eq)
    if data.get("err"):
        extra.append({"level": "warn", "code": "sensor_issue", "node": NODE,
                      "text": f"Electrical sensor issue: {', '.join(data['err'])}"})
    snap["flags"] = snap.get("flags", []) + extra
    return snap
