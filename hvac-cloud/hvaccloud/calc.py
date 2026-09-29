"""Operating mode, saturation temperatures, superheat, subcooling and fault flags.

A port of Hub.compute() / Hub.flags() in hvac-monitor-app/server.py, written as pure
functions so the cloud can run them per system. tests/test_calc.py checks both give
identical results; change them together.
"""
import math

NODES = ("outdoor", "indoor")


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def r1(v):
    return None if v is None else round(v, 1)


def compute(cfg, nodes, run_start, now, tables):
    """cfg: refrigerant, heat_pump, ob_energized, atm_psia.
    nodes: {"outdoor": {"online": bool, "data": dict|None}, "indoor": {...}}; only online data is used.
    run_start: epoch seconds the current compressor call began, or None.
    Returns (snapshot, new run_start)."""
    ref, T = cfg["refrigerant"], tables
    od = (nodes.get("outdoor") or {}).get("data") if (nodes.get("outdoor") or {}).get("online") else None
    ind = (nodes.get("indoor") or {}).get("data") if (nodes.get("indoor") or {}).get("online") else None
    od, ind = od or {}, ind or {}
    p, t = od.get("p", {}), od.get("t", {})
    air_o, air_i = od.get("air", {}), ind.get("air", {})
    om, im = od.get("mode", {}), ind.get("mode", {})

    def call(k):
        if k in om: return bool(om[k])
        if k in im: return bool(im[k])
        return False
    Y, W, G, OB = call("Y"), call("W"), call("G"), call("OB")

    heating_rv = cfg["heat_pump"] and (OB == (cfg["ob_energized"] == "heat"))
    if Y and heating_rv:
        mode = "heating"
    elif Y:
        mode = "cooling"
    elif W:
        mode = "heat_aux"
    elif G:
        mode = "fan"
    else:
        mode = "idle"

    if mode in ("cooling", "heating"):
        if run_start is None:
            run_start = now
    else:
        run_start = None
    run_min = (now - run_start) / 60 if run_start else 0

    atm = cfg["atm_psia"]
    abs_ = lambda g: None if g is None else g + atm
    p_liq, p_vap, p_tsuc = num(p.get("liq")), num(p.get("vap")), num(p.get("tsuc"))
    t_suc, t_liq, t_tsuc, t_dis = num(t.get("suc")), num(t.get("liq")), num(t.get("tsuc")), num(t.get("dis"))
    oat, orh = num(air_o.get("t")), num(air_o.get("rh"))
    t_sup, t_ret = num(air_i.get("supply")), num(air_i.get("return"))

    s = {"mode": mode, "run_min": round(run_min, 1), "Y": Y, "W": W, "G": G, "OB": OB,
         "p_liq": p_liq, "p_vap": p_vap, "p_tsuc": p_tsuc,
         "t_suc": t_suc, "t_liq": t_liq, "t_tsuc": t_tsuc, "t_dis": t_dis,
         "oat": oat, "orh": orh, "t_sup": t_sup, "t_ret": t_ret,
         "dt": r1(t_ret - t_sup) if t_ret is not None and t_sup is not None else None,
         "p_low": None, "p_high": None, "sat_low": None, "sat_high": None,
         "sh": None, "sc": None, "ctoa": None, "approach": None, "notes": []}

    if mode == "heating":
        low_p, low_t = p_tsuc, t_tsuc
        high_p = p_vap if p_vap is not None else p_liq
        if p_tsuc is None:
            s["notes"].append("Heating-mode superheat needs the true-suction transducer and thermistor.")
    else:
        low_p, low_t, high_p = p_vap, t_suc, p_liq

    s["p_low"], s["p_high"] = low_p, high_p
    s["sat_low"] = r1(T.dew_f(ref, abs_(low_p)))
    s["sat_high"] = r1(T.bubble_f(ref, abs_(high_p)))
    sat_liq = T.bubble_f(ref, abs_(p_liq))

    if mode in ("cooling", "heating"):
        if s["sat_low"] is not None and low_t is not None:
            s["sh"] = r1(low_t - s["sat_low"])
        if sat_liq is not None and t_liq is not None:
            s["sc"] = r1(sat_liq - t_liq)
        if mode == "cooling" and oat is not None:
            if s["sat_high"] is not None:
                s["ctoa"] = r1(s["sat_high"] - oat)
            if t_liq is not None:
                s["approach"] = r1(t_liq - oat)
    s["flags"] = flags(s, nodes)
    return s, run_start


def flags(s, nodes):
    """Conservative first-pass checks. Only judged after 10 minutes of steady running."""
    f = []
    for node in NODES:
        n = nodes.get(node) or {}
        if not n.get("online"):
            f.append({"level": "warn", "code": "node_offline", "node": node, "text": f"{node.title()} node is offline"})
        elif (n.get("data") or {}).get("err"):
            f.append({"level": "warn", "code": "sensor_issue", "node": node, "text": f"{node.title()} sensor issue: {', '.join(n['data']['err'])}"})
    if s["mode"] not in ("cooling", "heating") or s["run_min"] < 10:
        return f
    sh, sc, dt = s["sh"], s["sc"], s["dt"]
    if sh is not None and sh < 3:
        f.append({"level": "alert", "code": "sh_low", "text": f"Superheat {sh}°F: risk of liquid floodback"})
    if sh is not None and sh > 30:
        f.append({"level": "warn", "code": "sh_high", "text": f"Superheat {sh}°F is high: possible undercharge or restriction"})
    if sc is not None and sc < 3:
        f.append({"level": "warn", "code": "sc_low", "text": f"Subcooling {sc}°F is low: possible undercharge"})
    if sc is not None and sc > 20:
        f.append({"level": "warn", "code": "sc_high", "text": f"Subcooling {sc}°F is high: possible overcharge or restriction"})
    if s["mode"] == "cooling":
        if dt is not None and s["G"] and dt < 12:
            f.append({"level": "warn", "code": "dt_low", "text": f"Air delta-T {dt}°F is low"})
        if s["ctoa"] is not None and s["ctoa"] > 35:
            f.append({"level": "warn", "code": "ctoa_high", "text": f"Condensing {s['ctoa']}°F over ambient: check condenser coil and fan"})
    return f
