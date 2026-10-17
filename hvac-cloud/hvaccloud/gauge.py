"""Gauge checks: the technician's own gauge and thermometer readings next to what the system measured.

Phase 5 of the roadmap validates the diagnostics against gauges. The technician enters what the
gauges and probes read; the cloud works out saturation, superheat, subcooling and delta-T from
those numbers the same way calc.compute does (dew point for superheat, bubble point for
subcooling, the system's refrigerant and atmospheric pressure), stores them with the snapshot
nearest the moment the gauges were read, and shows system minus gauge for each value. The
summary over many checks shows whether a sensor reads consistently high or low.

Gauge "low" and "high" are the snapshot's p_low / p_high: suction (vapor line) and liquid line in
cooling. Nothing here changes how the system runs; it only records and compares.
"""
from .calc import num, r1
from .db import as_utc

ENTERED = ("p_low", "p_high", "t_suc", "t_liq", "t_ret", "t_sup")
COMPARED = ("p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc", "t_ret", "t_sup", "dt")
NEAREST_S = 180              # the snapshot compared must be this close to the time the gauges were read


def derive(entered, refrigerant, atm_psia, tables):
    """The gauge side: entered values plus saturation, superheat, subcooling and delta-T."""
    g = {k: num(entered.get(k)) for k in ENTERED}
    absolute = lambda p: None if p is None else p + atm_psia
    g["sat_low"] = r1(tables.dew_f(refrigerant, absolute(g["p_low"])))
    g["sat_high"] = r1(tables.bubble_f(refrigerant, absolute(g["p_high"])))
    g["sh"] = r1(g["t_suc"] - g["sat_low"]) if g["t_suc"] is not None and g["sat_low"] is not None else None
    sat_liq = tables.bubble_f(refrigerant, absolute(g["p_high"]))
    g["sc"] = r1(sat_liq - g["t_liq"]) if sat_liq is not None and g["t_liq"] is not None else None
    g["dt"] = r1(g["t_ret"] - g["t_sup"]) if g["t_ret"] is not None and g["t_sup"] is not None else None
    return g


def system_side(snap):
    """What the system measured, from a snapshot's data."""
    out = {k: num(snap.get(k)) for k in COMPARED}
    out["mode"] = snap.get("mode")
    out["run_min"] = num(snap.get("run_min"))
    return out


def differences(system, gauge):
    """System minus gauge for every value both sides have."""
    out = {}
    for k in COMPARED:
        a, b = num((system or {}).get(k)), num((gauge or {}).get(k))
        out[k] = r1(a - b) if a is not None and b is not None else None
    return out


def summary(views):
    """Per value: how many checks had both sides, the average and the largest difference (by size)."""
    out = {}
    for k in COMPARED:
        ds = [v["diff"][k] for v in views if v["diff"][k] is not None]
        out[k] = {"n": len(ds), "mean": r1(sum(ds) / len(ds)) if ds else None,
                  "max": max(ds, key=abs) if ds else None}
    return out


def check_view(c):
    return {"id": c.id, "time": as_utc(c.time).isoformat(), "technician": c.technician, "note": c.note,
            "refrigerant": c.refrigerant, "atm_psia": c.atm_psia,
            "gauge": c.gauge, "system": c.system, "diff": differences(c.system, c.gauge)}
