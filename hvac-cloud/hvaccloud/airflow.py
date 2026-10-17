"""Static pressure from the indoor node's three SDP810 sensors (roadmap phase 6).

The indoor node reports air.sp_ret (return plenum, negative), air.sp_sup (supply plenum, after
the coil) and air.sp_filt (drop across the filter), all in inches of water column. Nodes without
the sensors send none of these keys, and the snapshot then gets no "static", so older data and the
PC dashboard are unchanged.

Total external static pressure (TESP) = supply - return. While the blower runs, TESP above the
Equipment page's rated maximum raises tesp_high, and a filter drop above FILTER_DROP_MAX raises
filter_dirty. Applied in ingest after equipment.apply. dotnet/src/Fullscope.Core/Airflow.cs is the
C# port; change both together.
"""
from .calc import num

FILTER_DROP_MAX = 0.30                  # in. w.c.: a clean 1" pleated filter drops about 0.1
BLOWER_MODES = ("cooling", "heating", "heat_aux", "fan")
CODES = ("tesp_high", "filter_dirty")


def r2(v):
    return None if v is None else round(float(v), 2)


def apply(snap, indoor, eq):
    """Add snap["static"] = {"ret", "sup", "filter", "tesp", "max"} (in. w.c.) when the online indoor
    node reports static pressure, and the airflow flags while the blower runs."""
    data = (indoor or {}).get("data") if (indoor or {}).get("online") else None
    air = (data or {}).get("air")
    if not isinstance(air, dict) or not any(k in air for k in ("sp_ret", "sp_sup", "sp_filt")):
        return snap
    ret, sup, filt = num(air.get("sp_ret")), num(air.get("sp_sup")), num(air.get("sp_filt"))
    tesp = round(float(sup) - float(ret), 2) if sup is not None and ret is not None else None
    rated = num(eq.max_esp) if eq is not None else None
    rated = None if rated is None else float(rated)
    snap["static"] = {"ret": r2(ret), "sup": r2(sup), "filter": r2(filt), "tesp": tesp, "max": rated}
    if snap.get("mode") not in BLOWER_MODES:
        return snap
    flags = snap.setdefault("flags", [])
    if tesp is not None and rated is not None and tesp > rated:
        flags.append({"level": "warn", "code": "tesp_high",
                      "text": f"Total external static {tesp} in. w.c. is above the rated {rated:g}: check the filter, coil and ductwork"})
    f = r2(filt)
    if f is not None and f > FILTER_DROP_MAX:
        flags.append({"level": "warn", "code": "filter_dirty",
                      "text": f"Filter pressure drop {f} in. w.c. is over {FILTER_DROP_MAX:g}: the filter is loading up"})
    return snap
