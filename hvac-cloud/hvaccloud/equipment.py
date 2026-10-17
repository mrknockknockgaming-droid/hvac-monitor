"""Equipment details and the targets the diagnostics judge against.

calc.flags (shared with the PC dashboard) uses generic limits: subcooling 3-20 F, superheat
3-30 F. Once a technician enters the equipment, the cloud judges subcooling against the
nameplate target instead, for TXV and EEV systems in cooling. Superheat keeps the generic
limits for a TXV or EEV, which regulates it. A piston (fixed orifice) system in cooling is judged
against the charging-chart target superheat from the return wet bulb (indoor SHT45, see
psychro.py) and the outdoor dry bulb, when both are measured and inside the chart.
"""
from .db import Equipment

SYSTEM_TYPES = {"split_hp": "Split heat pump", "split_ac": "Split air conditioner",
                "packaged_hp": "Packaged heat pump", "packaged_ac": "Packaged air conditioner"}
METERING = {"txv": "TXV", "eev": "EEV", "piston": "Fixed orifice / piston"}
GENERIC_SC = (3.0, 20.0)
GENERIC_SH = (3.0, 30.0)
DEFAULT_SC_TOL = 3.0
SC_CODES = ("sc_low", "sc_high")
SH_CODES = ("sh_low", "sh_high")
CHART_SH_TOL = 5.0          # deg F either side of the charging-chart target
CHART_WB = (50.0, 76.0)     # indoor wet bulb and outdoor dry bulb the chart covers, deg F
CHART_ODB = (55.0, 115.0)
CHART_SH_MIN = 5.0          # below this the chart says don't charge by superheat


def atm_from_elevation(ft):
    """Standard-atmosphere pressure at an elevation, psia (14.0 at about 1,300 ft)."""
    return round(14.696 * (1 - 6.8754e-6 * ft) ** 5.2559, 2)


def sc_band(eq, mode):
    """(low, high, "nameplate" | "generic") subcooling limits for this equipment and mode."""
    if eq is not None and eq.metering in ("txv", "eev") and eq.sc_target is not None and mode == "cooling":
        tol = eq.sc_tolerance if eq.sc_tolerance is not None else DEFAULT_SC_TOL
        return round(eq.sc_target - tol, 1), round(eq.sc_target + tol, 1), "nameplate"
    return GENERIC_SC[0], GENERIC_SC[1], "generic"


def chart_superheat(wb, odb):
    """Fixed-orifice charging-chart target superheat, deg F, from the indoor (return) wet bulb and the
    outdoor dry bulb: the common chart approximation (3 x WB - 80 - ODB) / 2. None outside the chart."""
    if wb is None or odb is None or not CHART_WB[0] <= wb <= CHART_WB[1] or not CHART_ODB[0] <= odb <= CHART_ODB[1]:
        return None
    target = round((3 * wb - 80 - odb) / 2, 1)
    return target if target >= CHART_SH_MIN else None


def sh_band(eq, snap):
    """(low, high, "chart" | "generic", target) superheat limits for this equipment and snapshot."""
    if eq is not None and eq.metering == "piston" and snap.get("mode") == "cooling":
        wb = ((snap.get("air_rh") or {}).get("ret") or {}).get("wb")
        target = chart_superheat(wb, snap.get("oat"))
        if target is not None:
            return round(target - CHART_SH_TOL, 1), round(target + CHART_SH_TOL, 1), "chart", target
    return GENERIC_SH[0], GENERIC_SH[1], "generic", None


def superheat_note(eq):
    if eq is None or eq.metering is None:
        return "Generic limits until the metering device is set."
    if eq.metering == "piston":
        return ("Charging-chart target from the return wet bulb and outdoor temperature, ± 5 °F, while cooling; "
                "generic limits when indoor humidity isn't measured or is outside the chart.")
    return f"The {METERING[eq.metering]} sets superheat; flagged only outside the generic limits."


def apply(snap, eq):
    """Judge subcooling against the equipment's target (replaces calc's generic SC flags) and
    record the targets on the snapshot so the technician view can show them."""
    lo, hi, source = sc_band(eq, snap.get("mode"))
    sh_lo, sh_hi, sh_source, sh_target = sh_band(eq, snap)
    snap["targets"] = {"sc": {"lo": lo, "hi": hi, "source": source,
                              "target": eq.sc_target if source == "nameplate" else None},
                       "sh": {"lo": sh_lo, "hi": sh_hi, "source": sh_source}}
    if sh_source == "chart":
        snap["targets"]["sh"]["target"] = sh_target
    running = snap.get("mode") in ("cooling", "heating") and (snap.get("run_min") or 0) >= 10
    if sh_source == "chart":
        snap["flags"] = _chart_sh_flags(snap, running, sh_lo, sh_hi, sh_target)
    if source == "generic":
        return snap                                  # calc.flags already used these limits
    flags = [f for f in snap.get("flags", []) if f.get("code") not in SC_CODES]
    sc = snap.get("sc")
    if running and sc is not None:
        tgt = f"{eq.sc_target:g} ± {(hi - lo) / 2:g}°F"
        if sc < lo:
            flags.append({"level": "warn", "code": "sc_low", "text": f"Subcooling {sc}°F is below the nameplate target {tgt}: possible undercharge"})
        elif sc > hi:
            flags.append({"level": "warn", "code": "sc_high", "text": f"Subcooling {sc}°F is above the nameplate target {tgt}: possible overcharge or restriction"})
    snap["flags"] = flags
    return snap


def _chart_sh_flags(snap, running, lo, hi, target):
    """Replace calc's generic superheat flags with the charging-chart band; calc's floodback alert
    (below 3 F) stays whatever the chart says."""
    old = snap.get("flags", [])
    flags = [f for f in old if f.get("code") not in SH_CODES]
    sh = snap.get("sh")
    if not running or sh is None:
        return flags
    tgt = f"{target:g} ± {CHART_SH_TOL:g}°F"
    if sh < GENERIC_SH[0]:
        flags += [f for f in old if f.get("code") == "sh_low"]
    elif sh < lo:
        flags.append({"level": "warn", "code": "sh_low", "text": f"Superheat {sh}°F is below the charging-chart target {tgt}: possible overcharge or low airflow"})
    elif sh > hi:
        flags.append({"level": "warn", "code": "sh_high", "text": f"Superheat {sh}°F is above the charging-chart target {tgt}: possible undercharge or restriction"})
    return flags


def view(eq, system):
    """Equipment page data: the stored details plus the system's own settings and the targets in use."""
    lo, hi, source = sc_band(eq, "cooling")
    fields = ["system_type", "metering", "tonnage", "sc_target", "sc_tolerance", "rated_btuh", "rated_cfm",
              "max_esp", "elevation_ft", "comp_rla", "comp_lra", "fan_fla", "cap_herm_uf", "cap_fan_uf",
              "volt_min", "volt_max"]
    out = {k: getattr(eq, k) if eq is not None else None for k in fields}
    if out["sc_tolerance"] is None:
        out["sc_tolerance"] = DEFAULT_SC_TOL
    out.update(refrigerant=system.refrigerant, heat_pump=system.heat_pump, ob_energized=system.ob_energized,
               atm_psia=system.atm_psia,
               targets={"sc": {"lo": lo, "hi": hi, "source": source},
                        "sh": {"lo": GENERIC_SH[0], "hi": GENERIC_SH[1], "note": superheat_note(eq)}})
    return out


def get_or_new(s, system_id):
    eq = s.get(Equipment, system_id)
    if eq is None:
        eq = Equipment(system_id=system_id, sc_tolerance=DEFAULT_SC_TOL)
        s.add(eq)
    return eq
