"""Display thermostat (roadmap phase 11): settings, schedule, control logic and diagnostics.

The thermostat runs its own control loop: heating and cooling must keep working without the
internet. `Controller` below is the reference for that loop: the firmware implements the same
rules, and the simulator and tests run this one. The cloud stores the settings and schedule,
sends them to the thermostat as a retained, versioned config (hvac/<site>/thermostat/config),
and shows what the thermostat reports (hvac/<site>/thermostat/telemetry):

    {"node": "thermostat", "uptime": 312, "cfg_ver": 4,
     "room": {"t": 75.2, "rh": 41.0},
     "mode": "cool", "fan": "auto", "sp": {"heat": 68.0, "cool": 76.0}, "source": "schedule",
     "out": {"Y": true, "W": false, "G": true, "OB": true},
     "call": "cool", "call_min": 12.0, "wait": null, "err": []}

Safety rules (all enforced by the thermostat itself):
- compressor minimum off time and minimum run time, and a limit on starts per hour;
- auxiliary heat locked out above an outdoor temperature (except emergency heat);
- heat-pump compressor locked out below an outdoor temperature (aux heat takes over);
- everything off if the room sensor reads nonsense;
- the reversing valve only changes while the compressor is off.
"""
import copy
import datetime as dt
import zoneinfo

from .calc import num

MODES = ("off", "heat", "cool", "auto", "emergency_heat")
FANS = ("auto", "on", "circulate")
ROOM_VALID = (32.0, 120.0)            # outside this the sensor is broken: all outputs off

DEFAULT_SETTINGS = {
    "mode": "cool", "fan": "auto", "heat_sp": 68.0, "cool_sp": 76.0,
    "schedule": [],                    # [{"days": [0..6] (Mon=0), "at": "HH:MM", "heat": 68, "cool": 78}]
    "hold": None,                      # {"heat": .., "cool": .., "until": ISO time | None (permanent)}
}
DEFAULT_TECH = {                       # technician-only (Thermostat page)
    "heat_pump": True, "ob_energized": "cool",   # sent from the system's Equipment page settings
    "has_aux": True,
    "tz": "America/Phoenix",           # the house's time zone, for the schedule
    "service_pin": "0000",             # opens the technician page on the thermostat's screen
    "differential": 1.0,               # F: a call starts at setpoint +/- half of this and ends at -/+ half
    "min_on_s": 300, "min_off_s": 300, "max_starts_h": 4,
    "aux_lockout_f": 35.0,             # no aux heat above this outdoor temperature (emergency heat excepted)
    "comp_lockout_f": 5.0,             # no heat-pump compressor below this outdoor temperature
    "aux_droop_f": 2.0, "aux_delay_s": 600,   # aux joins if the room stays this far below setpoint this long
    "fan_purge_s": 90,                 # blower keeps running after a call ends
    "circulate_min_h": 15,             # "circulate" fan: minutes per hour
}
AUTO_DEADBAND = 3.0                    # auto mode needs cool setpoint >= heat setpoint + this
TECH_LIMITS = {                        # what a technician may set; the firmware clamps to the same
    "differential": (0.5, 4.0), "min_on_s": (60, 1800), "min_off_s": (120, 1800), "max_starts_h": (2, 8),
    "aux_lockout_f": (-20.0, 60.0), "comp_lockout_f": (-30.0, 50.0), "aux_droop_f": (1.0, 10.0),
    "aux_delay_s": (0, 3600), "fan_purge_s": (0, 300), "circulate_min_h": (0, 60),
}


# ---------------------------------------------------------------- settings
def merged(settings):
    out = copy.deepcopy(DEFAULT_SETTINGS)
    out.update({k: v for k, v in (settings or {}).items() if k in DEFAULT_SETTINGS})
    return out


def merged_tech(tech):
    out = dict(DEFAULT_TECH)
    out.update({k: v for k, v in (tech or {}).items() if k in DEFAULT_TECH})
    return out


def validate(settings):
    """Problems with user settings, as a list of sentences (empty = fine)."""
    s, errs = merged(settings), []
    if s["mode"] not in MODES:
        errs.append(f"mode must be one of {', '.join(MODES)}")
    if s["fan"] not in FANS:
        errs.append(f"fan must be one of {', '.join(FANS)}")
    for k in ("heat_sp", "cool_sp"):
        if num(s[k]) is None or not 50 <= s[k] <= 90:
            errs.append(f"{k} must be between 50 and 90 F")
    if not errs and s["cool_sp"] < s["heat_sp"] + AUTO_DEADBAND:
        errs.append(f"the cooling setpoint must be at least {AUTO_DEADBAND:g} F above the heating setpoint")
    for p in s["schedule"] or []:
        try:
            h, m = (int(x) for x in str(p["at"]).split(":"))
            assert 0 <= h < 24 and 0 <= m < 60
            assert p["days"] and all(int(d) in range(7) for d in p["days"])
            assert 50 <= p["heat"] <= 90 and 50 <= p["cool"] <= 90 and p["cool"] >= p["heat"] + AUTO_DEADBAND
        except Exception:
            errs.append(f"schedule period {p!r} needs days 0-6, at HH:MM and heat/cool 50-90 F, cool {AUTO_DEADBAND:g} F above heat")
            break
    return errs


def validate_tech(tech):
    errs = []
    for k, (lo, hi) in TECH_LIMITS.items():
        v = num(tech.get(k)) if k in tech else None
        if k in tech and (v is None or isinstance(tech[k], bool) or not lo <= v <= hi):
            errs.append(f"{k} must be between {lo:g} and {hi:g}")
    if "has_aux" in tech and not isinstance(tech["has_aux"], bool):
        errs.append("has_aux must be true or false")
    if "service_pin" in tech and not (isinstance(tech["service_pin"], str) and tech["service_pin"].isdigit()
                                      and 4 <= len(tech["service_pin"]) <= 6):
        errs.append("service_pin must be 4 to 6 digits")
    if "tz" in tech:
        try:
            zoneinfo.ZoneInfo(str(tech["tz"]))
        except Exception:
            errs.append("tz must be a time zone name such as America/Phoenix")
    return errs


def validate_hold(hold):
    errs = []
    for k in ("heat", "cool"):
        v = num(hold.get(k))
        if v is None or not 50 <= v <= 90:
            errs.append(f"hold {k} must be between 50 and 90 F")
    if not errs and hold["cool"] < hold["heat"] + AUTO_DEADBAND:
        errs.append(f"the cooling setpoint must be at least {AUTO_DEADBAND:g} F above the heating setpoint")
    return errs


def local_now(tech, now=None):
    """The house's local time (naive), which the schedule is written in."""
    try:
        tz = zoneinfo.ZoneInfo(merged_tech(tech)["tz"])
    except Exception:
        tz = zoneinfo.ZoneInfo(DEFAULT_TECH["tz"])
    now = dt.datetime.now(dt.timezone.utc) if now is None else dt.datetime.fromtimestamp(now, dt.timezone.utc)
    return now.astimezone(tz).replace(tzinfo=None)


def config_payload(version, settings, tech, system):
    """What is published (retained) to hvac/<site>/thermostat/config. The heat pump and reversing-valve
    settings come from the system's Equipment page, so they are set in one place."""
    t = merged_tech(tech)
    t["heat_pump"], t["ob_energized"] = bool(system.heat_pump), system.ob_energized
    return {"ver": version, "settings": merged(settings), "tech": t}


REQUEST_KEYS = ("mode", "fan", "heat_sp", "cool_sp")


def apply_request(settings, req):
    """A change made on the thermostat's own screen (hvac/<site>/thermostat/request):
    {"mode"?, "fan"?, "heat_sp"?, "cool_sp"?, "hold"?: {"heat", "cool", "until"}, "resume"?: true}.
    The thermostat has already applied it; this merges it into the stored settings. Returns the new
    settings, or raises ValueError (the stored settings are then left as they are)."""
    if not isinstance(req, dict):
        raise ValueError("not an object")
    s = merged(settings)
    s.update({k: req[k] for k in REQUEST_KEYS if k in req})
    if req.get("resume"):
        s["hold"] = None
    elif isinstance(req.get("hold"), dict):
        h = req["hold"]
        hold = {"heat": h.get("heat", s["heat_sp"]), "cool": h.get("cool", s["cool_sp"]), "until": h.get("until")}
        if hold["until"] is not None:
            try:
                dt.datetime.fromisoformat(str(hold["until"]))
            except ValueError:
                raise ValueError("hold until must be an ISO local time") from None
        errs = validate_hold(hold)
        if errs:
            raise ValueError("; ".join(errs))
        s["hold"] = hold
    errs = validate(s)
    if errs:
        raise ValueError("; ".join(errs))
    return s


def schedule_now(settings, when):
    """(heat, cool, source, next_change) at local time `when`: a hold wins, then the schedule
    period that started most recently (wrapping round the week), else the base setpoints."""
    s = merged(settings)
    hold = s.get("hold")
    if hold:
        until = hold.get("until")
        if until is None or dt.datetime.fromisoformat(until) > when:
            return hold.get("heat", s["heat_sp"]), hold.get("cool", s["cool_sp"]), "hold", until
    periods = []
    for p in s["schedule"] or []:
        h, m = (int(x) for x in p["at"].split(":"))
        for d in p["days"]:
            periods.append((int(d) * 1440 + h * 60 + m, p))
    if not periods:
        return s["heat_sp"], s["cool_sp"], "manual", None
    periods.sort(key=lambda x: x[0])
    minute = when.weekday() * 1440 + when.hour * 60 + when.minute
    current = max((x for x in periods if x[0] <= minute), default=periods[-1], key=lambda x: x[0])
    nxt = min((x for x in periods if x[0] > minute), default=periods[0], key=lambda x: x[0])
    ahead = (nxt[0] - minute) % (7 * 1440) or 7 * 1440
    return current[1]["heat"], current[1]["cool"], "schedule", (when + dt.timedelta(minutes=ahead)).replace(second=0, microsecond=0).isoformat()


def hold_until_next(settings, when, heat=None, cool=None):
    """A temporary hold of new setpoints until the next scheduled change (permanent without a schedule)."""
    s = merged(settings)
    base = {**s, "hold": None}
    h, c, source, nxt = schedule_now(base, when)
    return {"heat": heat if heat is not None else h, "cool": cool if cool is not None else c,
            "until": nxt if source == "schedule" else None}


# ---------------------------------------------------------------- the control loop
class Controller:
    """One thermostat's control state. Call step() every few seconds with the room temperature,
    the outdoor temperature (None if unknown) and the time (epoch seconds); it returns the outputs
    and why. This is the reference the firmware implements."""

    def __init__(self, settings=None, tech=None):
        self.settings, self.tech = merged(settings), merged_tech(tech)
        self.out = {"Y": False, "W": False, "G": False, "OB": False}
        self.call = None                 # "heat" | "cool" | None
        self.call_since = None
        self.y_on_since = None
        self.y_off_since = -1e12         # long ago: free to start
        self.starts = []                 # compressor start times (last hour)
        self.purge_until = 0.0
        self.below_since = None          # heating: when the room first sat aux_droop below setpoint
        self.wait = None

    def configure(self, settings=None, tech=None):
        if settings is not None:
            self.settings = merged(settings)
        if tech is not None:
            self.tech = merged_tech(tech)

    def _demand(self, room, heat_sp, cool_sp):
        """Which call the room wants, with hysteresis around each setpoint."""
        mode, half = self.settings["mode"], self.tech["differential"] / 2
        if mode == "off":
            return None
        heating_ok, cooling_ok = mode in ("heat", "auto", "emergency_heat"), mode in ("cool", "auto")
        if self.call == "cool" and cooling_ok and room > cool_sp - half:
            return "cool"
        if self.call == "heat" and heating_ok and room < heat_sp + half:
            return "heat"
        if cooling_ok and room >= cool_sp + half:
            return "cool"
        if heating_ok and room <= heat_sp - half:
            return "heat"
        return None

    def step(self, room, outdoor, now, local_time=None):
        t, wait = self.tech, None
        local_time = local_time or dt.datetime.fromtimestamp(now)
        heat_sp, cool_sp, source, _ = schedule_now(self.settings, local_time)
        self.starts = [x for x in self.starts if now - x < 3600]
        if room is None or not ROOM_VALID[0] <= room <= ROOM_VALID[1]:
            self._all_off(now)                                   # broken sensor: fail safe
            self.wait = "sensor"
            return self.report(room, heat_sp, cool_sp, source, now)

        demand = self._demand(room, heat_sp, cool_sp)
        if demand != self.call:
            self.call, self.call_since = demand, (now if demand else None)
        emergency = self.settings["mode"] == "emergency_heat"

        # --- compressor (Y)
        want_y = demand == "cool" or (demand == "heat" and t["heat_pump"] and not emergency
                                      and (outdoor is None or outdoor >= t["comp_lockout_f"]))
        y = self.out["Y"]
        if y and not want_y:
            if self.settings["mode"] != "off" and now - self.y_on_since < t["min_on_s"]:
                wait = "min_on"                                  # finish the minimum run
            else:
                y = False
                self.y_off_since, self.y_on_since = now, None
        elif not y and want_y:
            if now - self.y_off_since < t["min_off_s"]:
                wait = "min_off"
            elif len(self.starts) >= t["max_starts_h"]:
                wait = "max_starts"
            else:
                # the reversing valve is set before the compressor starts, never while it runs
                if t["heat_pump"]:
                    self.out["OB"] = (demand == "cool") == (t["ob_energized"] == "cool")
                y = True
                self.y_on_since = now
                self.starts.append(now)

        # --- auxiliary / primary heat (W)
        w = False
        if demand == "heat":
            if not t["heat_pump"]:
                w = True                                         # furnace or electric heat is the heat
            elif t["has_aux"]:
                outdoor_ok = outdoor is None or outdoor <= t["aux_lockout_f"]
                comp_locked = outdoor is not None and outdoor < t["comp_lockout_f"]
                if room <= heat_sp - t["aux_droop_f"]:
                    self.below_since = self.below_since or now
                else:
                    self.below_since = None
                drooping = self.below_since is not None and now - self.below_since >= t["aux_delay_s"]
                w = emergency or comp_locked or (drooping and outdoor_ok)
        else:
            self.below_since = None

        # --- blower (G)
        calling = y or w
        if self.out["Y"] and not y or self.out["W"] and not w:
            self.purge_until = now + t["fan_purge_s"]
        fan = self.settings["fan"]
        circulate = fan == "circulate" and (local_time.minute < t["circulate_min_h"])
        g = calling or fan == "on" or circulate or now < self.purge_until

        self.out.update(Y=y, W=w, G=g)
        self.wait = wait
        return self.report(room, heat_sp, cool_sp, source, now)

    def _all_off(self, now):
        if self.out["Y"]:
            self.y_off_since, self.y_on_since = now, None
        self.out.update(Y=False, W=False, G=False)
        self.call, self.call_since, self.below_since = None, None, None

    def report(self, room, heat_sp, cool_sp, source, now):
        return {"mode": self.settings["mode"], "fan": self.settings["fan"], "sp": {"heat": heat_sp, "cool": cool_sp},
                "source": source, "out": dict(self.out), "call": self.call,
                "call_min": round((now - self.call_since) / 60, 1) if self.call_since else None,
                "wait": self.wait, "room": {"t": room}}


# ---------------------------------------------------------------- cloud diagnostics
NODE = "thermostat"
HOT_F, COLD_F = 90.0, 50.0
NOT_REACHED_MIN, NOT_REACHED_F = 90.0, 2.0


def flags(data, snap):
    """Problems visible from the thermostat's report and the other nodes."""
    f = []
    room = num((data.get("room") or {}).get("t"))
    if room is not None and room >= HOT_F:
        f.append({"level": "alert", "code": "room_hot", "text": f"Room temperature {room:g} F"})
    if room is not None and room <= COLD_F:
        f.append({"level": "alert", "code": "room_cold", "text": f"Room temperature {room:g} F"})
    call, minutes, sp = data.get("call"), num(data.get("call_min")), data.get("sp") or {}
    if call in ("cool", "heat") and minutes is not None and minutes >= NOT_REACHED_MIN and room is not None:
        target = num(sp.get("cool" if call == "cool" else "heat"))
        gap = None if target is None else (room - target) if call == "cool" else (target - room)
        if gap is not None and gap >= NOT_REACHED_F:
            f.append({"level": "warn", "code": "setpoint_not_reached",
                      "text": f"{call.title()}ing for {minutes:g} min and the room is still {gap:.1f} F from {target:g} F"})
    out = data.get("out") or {}
    seen_y = snap.get("Y")
    if out.get("Y") is not None and seen_y is not None and bool(out["Y"]) != bool(seen_y):   # held 5 min before it alerts
        f.append({"level": "warn", "code": "call_mismatch",
                  "text": f"Thermostat output Y is {'on' if out['Y'] else 'off'} but the equipment sees it "
                          f"{'on' if seen_y else 'off'}: check the wiring or the thermostat's relay"})
    if data.get("err"):
        f.append({"level": "warn", "code": "sensor_issue", "node": NODE, "text": f"Thermostat sensor issue: {', '.join(data['err'])}"})
    return f


def apply(snap, state):
    """Add the thermostat's report and flags to a snapshot (state as in electrical.apply)."""
    if state is None:
        snap["tstat"] = None
        return snap
    if not state.get("online") or not state.get("data"):
        snap["tstat"] = None
        snap["flags"] = snap.get("flags", []) + [{"level": "warn", "code": "node_offline", "node": NODE,
                                                  "text": "Thermostat is offline"}]
        return snap
    data = state["data"]
    snap["tstat"] = {k: data.get(k) for k in ("mode", "fan", "sp", "source", "out", "call", "call_min", "wait", "room", "cfg_ver")}
    snap["flags"] = snap.get("flags", []) + flags(data, snap)
    return snap
