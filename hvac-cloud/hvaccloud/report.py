"""Threshold report: how a system's diagnostics actually behave in steady cooling, next to the alert limits.

Phase 5 of the roadmap tunes the alert thresholds from real data. calc.flags judges superheat,
subcooling, delta-T and condensing-over-ambient only after 10 minutes of running; this report takes
the snapshots that meet the same test (cooling, run_min >= 10) over the last few days and gives,
per value, its spread (percentiles), the limits in use and how much of that steady time each limit
would flag. Alerts raised in the same window are counted by code. Nothing is changed: the limits
stay where they are until someone decides to move them.

Percentiles are nearest-rank on the sorted samples: index floor(p * (n - 1) + 0.5).
"""
import datetime as dt
import math

from . import equipment
from .calc import num

STEADY_MIN = 10              # calc.flags judges only after this many minutes of running
DT_LOW = 12.0                # calc.flags: dt_low (cooling, blower on)
CTOA_HIGH = 35.0             # calc.flags: ctoa_high (cooling)
GAP_CAP_S = 60               # a longer gap between steady samples counts as this much steady time
PCTS = (5, 25, 50, 75, 95)
METRICS = ("sh", "sc", "dt", "ctoa", "approach")


def pct(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(math.floor(p / 100 * (len(sorted_vals) - 1) + 0.5)))]


def limits(eq):
    """{metric: (low, high, source)} with None for a side that isn't checked."""
    sc_lo, sc_hi, sc_src = equipment.sc_band(eq, "cooling")
    return {"sh": (equipment.GENERIC_SH[0], equipment.GENERIC_SH[1], "generic"),
            "sc": (sc_lo, sc_hi, sc_src),
            "dt": (DT_LOW, None, "generic"),
            "ctoa": (None, CTOA_HIGH, "generic"),
            "approach": (None, None, None)}


def build(rows, eq, alert_counts, days):
    """rows: (epoch seconds, snapshot data) oldest first. alert_counts: {code: raised in the window}."""
    vals = {m: [] for m in METRICS}
    steady_s, prev = 0.0, None
    for t, d in rows:
        if d.get("mode") != "cooling" or (num(d.get("run_min")) or 0) < STEADY_MIN:
            prev = None
            continue
        if prev is not None:
            steady_s += min(t - prev, GAP_CAP_S)
        prev = t
        for m in METRICS:
            v = num(d.get(m))
            if v is None or (m == "dt" and not d.get("G")):
                continue
            vals[m].append(v)
    lim = limits(eq)
    out = {}
    for m in METRICS:
        v = sorted(vals[m])
        lo, hi, src = lim[m]
        row = {"n": len(v), "min": None, "max": None, "pct": None, "limit_low": lo, "limit_high": hi, "limit_source": src,
               "below": None, "above": None}
        if v:
            row.update(min=v[0], max=v[-1], pct={str(p): pct(v, p) for p in PCTS})
            if lo is not None:
                row["below"] = round(sum(1 for x in v if x < lo) / len(v), 3)
            if hi is not None:
                row["above"] = round(sum(1 for x in v if x > hi) / len(v), 3)
        out[m] = row
    return {"days": days, "steady_hours": round(steady_s / 3600, 2), "metrics": out,
            "alerts": dict(sorted(alert_counts.items()))}
