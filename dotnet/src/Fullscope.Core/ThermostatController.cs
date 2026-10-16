using System.Text.Json.Nodes;

namespace Fullscope.Core;

/// <summary>
/// One thermostat's control state (Controller in hvaccloud/thermostat.py, which the firmware also
/// implements). Call Step() every few seconds with the room temperature, the outdoor temperature
/// (null if unknown) and the time (epoch seconds); it returns the outputs and why. The C# demo
/// tools run it; tests replay the firmware's Python vectors through it.
/// </summary>
public sealed class ThermostatController
{
    public const double RoomMin = 32.0, RoomMax = 120.0;     // outside this the sensor is broken: all outputs off

    JsonObject _settings, _tech;
    bool _y, _w, _g, _ob;
    string? _call;                    // "heat" | "cool" | null
    double? _callSince;
    double? _yOnSince;
    double _yOffSince = -1e12;        // long ago: free to start
    List<double> _starts = [];        // compressor start times (last hour)
    double _purgeUntil;
    double? _belowSince;              // heating: when the room first sat aux_droop below setpoint
    string? _wait;

    public ThermostatController(JsonNode? settings = null, JsonNode? tech = null)
    {
        _settings = Thermostat.Merged(settings);
        _tech = Thermostat.MergedTech(tech);
    }

    public JsonObject Settings => _settings;

    public void Configure(JsonNode? settings = null, JsonNode? tech = null)
    {
        if (settings is not null) _settings = Thermostat.Merged(settings);
        if (tech is not null) _tech = Thermostat.MergedTech(tech);
    }

    double T(string key) => J.Num(_tech, key)!.Value.V;
    string Mode => J.Str(_settings, "mode")!;

    static double V(JsonNode? n) => Py.NumberOf(n)!.Value.V;

    /// <summary>Which call the room wants, with hysteresis around each setpoint.</summary>
    string? Demand(double room, double heatSp, double coolSp)
    {
        string mode = Mode;
        double half = T("differential") / 2;
        if (mode == "off") return null;
        bool heatingOk = mode is "heat" or "auto" or "emergency_heat", coolingOk = mode is "cool" or "auto";
        if (_call == "cool" && coolingOk && room > coolSp - half) return "cool";
        if (_call == "heat" && heatingOk && room < heatSp + half) return "heat";
        if (coolingOk && room >= coolSp + half) return "cool";
        if (heatingOk && room <= heatSp - half) return "heat";
        return null;
    }

    public JsonObject Step(Num? room, double? outdoor, double now, DateTime? localTime = null)
    {
        string? wait = null;
        var local = localTime ?? Time.FromEpoch(now).ToLocalTime();
        var (heatNode, coolNode, source, _) = Thermostat.ScheduleNow(_settings, local);
        _starts = _starts.Where(x => now - x < 3600).ToList();
        if (room is not Num r || !(RoomMin <= r.V && r.V <= RoomMax))
        {
            AllOff(now);                                         // broken sensor: fail safe
            _wait = "sensor";
            return Report(room, heatNode, coolNode, source, now);
        }
        double rv = r.V, heatSp = V(heatNode), coolSp = V(coolNode);

        var demand = Demand(rv, heatSp, coolSp);
        if (demand != _call) (_call, _callSince) = (demand, demand is not null ? now : null);
        bool emergency = Mode == "emergency_heat";
        bool heatPump = J.Truthy(_tech, "heat_pump");

        // --- compressor (Y)
        bool wantY = demand == "cool" || (demand == "heat" && heatPump && !emergency
                                          && (outdoor is null || outdoor >= T("comp_lockout_f")));
        bool y = _y;
        if (y && !wantY)
        {
            if (Mode != "off" && now - _yOnSince!.Value < T("min_on_s")) wait = "min_on";     // finish the minimum run
            else
            {
                y = false;
                (_yOffSince, _yOnSince) = (now, null);
            }
        }
        else if (!y && wantY)
        {
            if (now - _yOffSince < T("min_off_s")) wait = "min_off";
            else if (_starts.Count >= T("max_starts_h")) wait = "max_starts";
            else
            {
                // the reversing valve is set before the compressor starts, never while it runs
                if (heatPump) _ob = (demand == "cool") == (J.Str(_tech, "ob_energized") == "cool");
                y = true;
                _yOnSince = now;
                _starts.Add(now);
            }
        }

        // --- auxiliary / primary heat (W)
        bool w = false;
        if (demand == "heat")
        {
            if (!heatPump) w = true;                             // furnace or electric heat is the heat
            else if (J.Truthy(_tech, "has_aux"))
            {
                bool outdoorOk = outdoor is null || outdoor <= T("aux_lockout_f");
                bool compLocked = outdoor is not null && outdoor < T("comp_lockout_f");
                if (rv <= heatSp - T("aux_droop_f")) _belowSince = _belowSince is double b && b != 0 ? b : now;
                else _belowSince = null;
                bool drooping = _belowSince is double since && now - since >= T("aux_delay_s");
                w = emergency || compLocked || (drooping && outdoorOk);
            }
        }
        else _belowSince = null;

        // --- blower (G)
        bool calling = y || w;
        if ((_y && !y) || (_w && !w)) _purgeUntil = now + T("fan_purge_s");
        var fan = J.Str(_settings, "fan");
        bool circulate = fan == "circulate" && local.Minute < T("circulate_min_h");
        bool g = calling || fan == "on" || circulate || now < _purgeUntil;

        (_y, _w, _g) = (y, w, g);
        _wait = wait;
        return Report(room, heatNode, coolNode, source, now);
    }

    void AllOff(double now)
    {
        if (_y) (_yOffSince, _yOnSince) = (now, null);
        (_y, _w, _g) = (false, false, false);
        (_call, _callSince, _belowSince) = (null, null, null);
    }

    JsonObject Report(Num? room, JsonNode? heat, JsonNode? cool, string source, double now) => new()
    {
        ["mode"] = _settings["mode"]?.DeepClone(), ["fan"] = _settings["fan"]?.DeepClone(),
        ["sp"] = new JsonObject { ["heat"] = heat?.DeepClone(), ["cool"] = cool?.DeepClone() },
        ["source"] = source, ["out"] = new JsonObject { ["Y"] = _y, ["W"] = _w, ["G"] = _g, ["OB"] = _ob },
        ["call"] = _call,
        ["call_min"] = _callSince is double cs && cs != 0 ? Py.Round((now - cs) / 60, 1) : null,
        ["wait"] = _wait, ["room"] = new JsonObject { ["t"] = room?.ToNode() },
    };
}
