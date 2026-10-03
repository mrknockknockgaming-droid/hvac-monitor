"""Equipment details and the targets the diagnostics judge against.

calc.flags (shared with the PC dashboard) uses generic limits: subcooling 3-20 F, superheat
3-30 F. Once a technician enters the equipment, the cloud judges subcooling against the
nameplate target instead, for TXV and EEV systems in cooling. Superheat keeps the generic
limits: a TXV or EEV regulates it, and a piston's target superheat needs the indoor wet bulb,
which the hardware doesn't measure until the humidity sensors (roadmap phase 6).
"""
from .db import Equipment

SYSTEM_TYPES = {"split_hp": "Split heat pump", "split_ac": "Split air conditioner",
                "packaged_hp": "Packaged heat pump", "packaged_ac": "Packaged air conditioner"}
METERING = {"txv": "TXV", "eev": "EEV", "piston": "Fixed orifice / piston"}
GENERIC_SC = (3.0, 20.0)
GENERIC_SH = (3.0, 30.0)
DEFAULT_SC_TOL = 3.0
SC_CODES = ("sc_low", "sc_high")


def atm_from_elevation(ft):
    """Standard-atmosphere pressure at an elevation, psia (14.0 at about 1,300 ft)."""
    return round(14.696 * (1 - 6.8754e-6 * ft) ** 5.2559, 2)


def sc_band(eq, mode):
    """(low, high, "nameplate" | "generic") subcooling limits for this equipment and mode."""
    if eq is not None and eq.metering in ("txv", "eev") and eq.sc_target is not None and mode == "cooling":
        tol = eq.sc_tolerance if eq.sc_tolerance is not None else DEFAULT_SC_TOL
        return round(eq.sc_target - tol, 1), round(eq.sc_target + tol, 1), "nameplate"
    return GENERIC_SC[0], GENERIC_SC[1], "generic"


def superheat_note(eq):
    if eq is None or eq.metering is None:
        return "Generic limits until the metering device is set."
    if eq.metering == "piston":
        return "A piston's target superheat needs indoor humidity (phase 6); generic limits until then."
    return f"The {METERING[eq.metering]} sets superheat; flagged only outside the generic limits."


def apply(snap, eq):
    """Judge subcooling against the equipment's target (replaces calc's generic SC flags) and
    record the targets on the snapshot so the technician view can show them."""
    lo, hi, source = sc_band(eq, snap.get("mode"))
    snap["targets"] = {"sc": {"lo": lo, "hi": hi, "source": source,
                              "target": eq.sc_target if source == "nameplate" else None},
                       "sh": {"lo": GENERIC_SH[0], "hi": GENERIC_SH[1], "source": "generic"}}
    if source == "generic":
        return snap                                  # calc.flags already used these limits
    flags = [f for f in snap.get("flags", []) if f.get("code") not in SC_CODES]
    sc = snap.get("sc")
    running = snap.get("mode") in ("cooling", "heating") and (snap.get("run_min") or 0) >= 10
    if running and sc is not None:
        tgt = f"{eq.sc_target:g} ± {(hi - lo) / 2:g}°F"
        if sc < lo:
            flags.append({"level": "warn", "code": "sc_low", "text": f"Subcooling {sc}°F is below the nameplate target {tgt}: possible undercharge"})
        elif sc > hi:
            flags.append({"level": "warn", "code": "sc_high", "text": f"Subcooling {sc}°F is above the nameplate target {tgt}: possible overcharge or restriction"})
    snap["flags"] = flags
    return snap


def view(eq, system):
    """Equipment page data: the stored details plus the system's own settings and the targets in use."""
    lo, hi, source = sc_band(eq, "cooling")
    fields = ["system_type", "metering", "tonnage", "sc_target", "sc_tolerance", "rated_btuh", "rated_cfm",
              "max_esp", "elevation_ft"]
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
