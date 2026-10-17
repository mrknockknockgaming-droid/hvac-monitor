"""Indoor air humidity: relative humidity, wet bulb, dew point and enthalpy at the return and
supply, from the indoor node's two SHT45 probes (roadmap phase 6).

The indoor node reports air.rh_ret / air.rh_sup (% RH) and air.t_rh_ret / air.t_rh_sup (the
SHT45's own temperature, deg F; RH is relative to it, so the psychrometrics use it and fall back
to the DS18B20 dry bulb). Nodes without the probes send none of these keys, and the snapshot then
gets no "air_rh" at all, so older data and the PC dashboard are unchanged.

Psychrometrics at the site's own atmospheric pressure (ASHRAE Fundamentals, ch. 1): saturation
pressure over water from the Magnus form (Alduchov and Eskridge), humidity ratio, wet bulb by
bisection on the psychrometric equation, enthalpy in Btu/lb dry air. Applied in ingest after
calc.compute and before equipment.apply, which uses the return wet bulb for a piston's target
superheat. dotnet/src/Fullscope.Core/Psychro.cs is the C# port; change both together.
"""
import math

from .calc import num, r1

KPA_PER_PSI = 6.894757
SIDES = ("ret", "sup")


def psat_kpa(c):
    """Saturation vapour pressure over water, kPa, at c deg C."""
    return 0.61094 * math.exp(17.625 * c / (c + 243.04))


def ratio(pw, p):
    """Humidity ratio (lb water per lb dry air) from vapour and total pressure, kPa."""
    return 0.621945 * pw / (p - pw)


def wet_bulb_c(c, w, p):
    """Thermodynamic wet bulb, deg C: solves the psychrometric equation for humidity ratio w."""
    def w_at(wb):
        ws = ratio(psat_kpa(wb), p)
        return ((2501 - 2.326 * wb) * ws - 1.006 * (c - wb)) / (2501 + 1.86 * c - 4.186 * wb)
    lo, hi = c - 60.0, c
    for _ in range(60):
        mid = (lo + hi) / 2
        if w_at(mid) > w:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def point(t_f, rh, atm_psia):
    """{"rh", "wb", "dp", "h"} for one probe (deg F, Btu/lb), or None without a usable reading."""
    t_f, rh = num(t_f), num(rh)
    if t_f is None or rh is None or not 0 < rh <= 100 or atm_psia is None:
        return None
    c = (t_f - 32) / 1.8
    p = atm_psia * KPA_PER_PSI
    pw = rh / 100 * psat_kpa(c)
    w = ratio(pw, p)
    g = math.log(pw / 0.61094)
    dp = 243.04 * g / (17.625 - g)
    wb = min(wet_bulb_c(c, w, p), c)
    h = 0.240 * t_f + w * (1061 + 0.444 * t_f)
    return {"rh": r1(float(rh)), "wb": r1(wb * 1.8 + 32), "dp": r1(dp * 1.8 + 32), "h": r1(h)}


def apply(snap, indoor, atm_psia):
    """Add snap["air_rh"] = {"ret": point|None, "sup": point|None, "dh": Btu/lb|None} when the
    online indoor node reports humidity. indoor: {"online", "data"} as calc gets it."""
    data = (indoor or {}).get("data") if (indoor or {}).get("online") else None
    air = (data or {}).get("air")
    if not isinstance(air, dict) or not any(f"rh_{k}" in air for k in SIDES):
        return snap
    dry = {"ret": snap.get("t_ret"), "sup": snap.get("t_sup")}
    out = {}
    for k in SIDES:
        t = num(air.get(f"t_rh_{k}"))
        out[k] = point(t if t is not None else dry[k], air.get(f"rh_{k}"), atm_psia)
    ret, sup = out["ret"], out["sup"]
    out["dh"] = r1(ret["h"] - sup["h"]) if ret and sup else None
    snap["air_rh"] = out
    return snap
