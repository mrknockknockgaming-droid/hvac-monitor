using System.Globalization;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace Fullscope.Core;

/// <summary>A thermostat request or setting the cloud won't accept; Message lists the problems.</summary>
public sealed class ThermostatSettingsException(string message) : Exception(message);

/// <summary>
/// Display thermostat (roadmap phase 11): settings, schedule and cloud diagnostics. Port of the
/// cloud parts of hvaccloud/thermostat.py. The control loop (Controller) stays in Python as the
/// reference the thermostat firmware is tested against; the cloud never runs it.
/// </summary>
public static partial class Thermostat
{
    public const string Node = "thermostat";
    public static readonly string[] Modes = ["off", "heat", "cool", "auto", "emergency_heat"];
    public static readonly string[] Fans = ["auto", "on", "circulate"];
    public const double AutoDeadband = 3.0;
    public const double HotF = 90.0, ColdF = 50.0, NotReachedMin = 90.0, NotReachedF = 2.0;
    public static readonly string[] RequestKeys = ["mode", "fan", "heat_sp", "cool_sp"];
    static readonly string[] TstatKeys = ["mode", "fan", "sp", "source", "out", "call", "call_min", "wait", "room", "cfg_ver"];

    public static JsonObject DefaultSettings() => new()
    {
        ["mode"] = "cool", ["fan"] = "auto", ["heat_sp"] = 68.0, ["cool_sp"] = 76.0,
        ["schedule"] = new JsonArray(),     // [{"days": [0..6] (Mon=0), "at": "HH:MM", "heat": 68, "cool": 78}]
        ["hold"] = null,                    // {"heat", "cool", "until": ISO local time | null (permanent)}
    };

    public static JsonObject DefaultTech() => new()
    {
        ["heat_pump"] = true, ["ob_energized"] = "cool",     // sent from the system's Equipment page settings
        ["has_aux"] = true,
        ["tz"] = "America/Phoenix",
        ["service_pin"] = "0000",
        ["differential"] = 1.0,
        ["min_on_s"] = 300, ["min_off_s"] = 300, ["max_starts_h"] = 4,
        ["aux_lockout_f"] = 35.0,
        ["comp_lockout_f"] = 5.0,
        ["aux_droop_f"] = 2.0, ["aux_delay_s"] = 600,
        ["fan_purge_s"] = 90,
        ["circulate_min_h"] = 15,
    };

    /// <summary>What a technician may set; the firmware clamps to the same.</summary>
    public static readonly (string Key, double Lo, double Hi)[] TechLimits =
    [
        ("differential", 0.5, 4.0), ("min_on_s", 60, 1800), ("min_off_s", 120, 1800), ("max_starts_h", 2, 8),
        ("aux_lockout_f", -20.0, 60.0), ("comp_lockout_f", -30.0, 50.0), ("aux_droop_f", 1.0, 10.0),
        ("aux_delay_s", 0, 3600), ("fan_purge_s", 0, 300), ("circulate_min_h", 0, 60),
    ];

    static JsonObject MergeInto(JsonObject defaults, JsonNode? given)
    {
        if (given is JsonObject g)
            foreach (var (k, v) in g)
                if (defaults.ContainsKey(k)) defaults[k] = J.Copy(v);
        return defaults;
    }

    public static JsonObject Merged(JsonNode? settings) => MergeInto(DefaultSettings(), settings);

    public static JsonObject MergedTech(JsonNode? tech) => MergeInto(DefaultTech(), tech);

    /// <summary>A number for comparisons, as Python compares it (bools are 0 and 1); null where Python raises.</summary>
    static double? Comparable(JsonNode? n) => n is JsonValue v && Py.Classify(v) == Py.Kind.Bool
        ? (v.GetValue<bool>() ? 1 : 0)
        : Py.NumberOf(n)?.V;

    static string G(double x) => Py.FormatG(x);

    /// <summary>Problems with user settings, as sentences (empty = fine).</summary>
    public static List<string> Validate(JsonNode? settings)
    {
        var s = Merged(settings);
        var errs = new List<string>();
        if (J.Str(s, "mode") is not string m || !Modes.Contains(m))
            errs.Add($"mode must be one of {string.Join(", ", Modes)}");
        if (J.Str(s, "fan") is not string f || !Fans.Contains(f))
            errs.Add($"fan must be one of {string.Join(", ", Fans)}");
        foreach (var k in new[] { "heat_sp", "cool_sp" })
            if (J.Num(s, k) is not Num v || !(50 <= v.V && v.V <= 90))
                errs.Add($"{k} must be between 50 and 90 F");
        if (errs.Count == 0 && J.Num(s, "cool_sp")!.Value.V < J.Num(s, "heat_sp")!.Value.V + AutoDeadband)
            errs.Add($"the cooling setpoint must be at least {G(AutoDeadband)} F above the heating setpoint");
        foreach (var p in Iterate(J.Get(s, "schedule")))
        {
            if (!PeriodOk(p))
            {
                errs.Add($"schedule period {Py.ReprNode(p)} needs days 0-6, at HH:MM and heat/cool 50-90 F, cool {G(AutoDeadband)} F above heat");
                break;
            }
        }
        return errs;
    }

    /// <summary>Python's `for x in (value or [])`: list items, dict keys, string characters.</summary>
    static IEnumerable<JsonNode?> Iterate(JsonNode? n)
    {
        if (!Py.Truthy(n)) return [];
        return n switch
        {
            JsonArray a => a,
            JsonObject o => o.Select(kv => (JsonNode?)JsonValue.Create(kv.Key)),
            JsonValue v when Py.Classify(v) == Py.Kind.String => v.GetValue<string>().Select(c => (JsonNode?)JsonValue.Create(c.ToString())),
            _ => throw new InvalidOperationException($"'{Py.ReprNode(n)}' is not iterable"),
        };
    }

    static bool PeriodOk(JsonNode? p)
    {
        if (p is not JsonObject o || !o.ContainsKey("at") || !o.ContainsKey("days")) return false;
        var parts = Py.Str(o["at"]).Split(':');
        if (parts.Length != 2 || !Py.TryParseInt(parts[0], out var h) || !Py.TryParseInt(parts[1], out var m)) return false;
        if (!(0 <= h && h < 24 && 0 <= m && m < 60)) return false;
        var days = o["days"];
        if (!Py.Truthy(days)) return false;
        if (days is JsonValue dv && Py.Classify(dv) != Py.Kind.String) return false;   // a number isn't iterable
        foreach (var d in Iterate(days))
            if (!Py.TryInt(d, out var day) || day < 0 || day > 6) return false;
        if (!o.ContainsKey("heat") || !o.ContainsKey("cool")) return false;
        if (Comparable(o["heat"]) is not double heat || Comparable(o["cool"]) is not double cool) return false;
        return 50 <= heat && heat <= 90 && 50 <= cool && cool <= 90 && cool >= heat + AutoDeadband;
    }

    public static List<string> ValidateTech(JsonObject tech)
    {
        var errs = new List<string>();
        foreach (var (k, lo, hi) in TechLimits)
        {
            if (!tech.ContainsKey(k)) continue;
            if (J.Num(tech, k) is not Num v || !(lo <= v.V && v.V <= hi))
                errs.Add($"{k} must be between {G(lo)} and {G(hi)}");
        }
        if (tech.ContainsKey("has_aux") && !(tech["has_aux"] is JsonValue b && Py.Classify(b) == Py.Kind.Bool))
            errs.Add("has_aux must be true or false");
        if (tech.ContainsKey("service_pin") &&
            !(J.Str(tech, "service_pin") is string pin && pin.Length is >= 4 and <= 6 && pin.All(char.IsDigit)))
            errs.Add("service_pin must be 4 to 6 digits");
        if (tech.ContainsKey("tz") && FindZone(Py.Str(tech["tz"])) is null)
            errs.Add("tz must be a time zone name such as America/Phoenix");
        return errs;
    }

    public static List<string> ValidateHold(JsonObject hold)
    {
        var errs = new List<string>();
        foreach (var k in new[] { "heat", "cool" })
            if (J.Num(hold, k) is not Num v || !(50 <= v.V && v.V <= 90))
                errs.Add($"hold {k} must be between 50 and 90 F");
        if (errs.Count == 0 && J.Num(hold, "cool")!.Value.V < J.Num(hold, "heat")!.Value.V + AutoDeadband)
            errs.Add($"the cooling setpoint must be at least {G(AutoDeadband)} F above the heating setpoint");
        return errs;
    }

    public static TimeZoneInfo? FindZone(string name)
    {
        if (string.IsNullOrWhiteSpace(name) || name != name.Trim()) return null;
        try { return TimeZoneInfo.FindSystemTimeZoneById(name); }
        catch (Exception) { return null; }
    }

    /// <summary>The house's local time (no zone), which the schedule is written in.</summary>
    public static DateTime LocalNow(JsonNode? tech, double? now = null)
    {
        var tz = FindZone(J.Str(MergedTech(tech), "tz") ?? "") ?? FindZone("America/Phoenix")!;
        var utc = now is double t ? Time.FromEpoch(t) : Time.UtcNow();
        return DateTime.SpecifyKind(TimeZoneInfo.ConvertTimeFromUtc(utc, tz), DateTimeKind.Unspecified);
    }

    /// <summary>What is published (retained) to hvac/&lt;site&gt;/thermostat/config. The heat pump and
    /// reversing-valve settings come from the system's Equipment page, so they are set in one place.</summary>
    public static JsonObject ConfigPayload(int version, JsonNode? settings, JsonNode? tech, bool heatPump, string obEnergized)
    {
        var t = MergedTech(tech);
        t["heat_pump"] = heatPump;
        t["ob_energized"] = obEnergized;
        return new JsonObject { ["ver"] = version, ["settings"] = Merged(settings), ["tech"] = t };
    }

    /// <summary>A change made on the thermostat's own screen, merged into the stored settings.
    /// Throws ThermostatSettingsException (the stored settings are then left as they are).</summary>
    public static JsonObject ApplyRequest(JsonNode? settings, JsonNode? req)
    {
        if (req is not JsonObject r) throw new ThermostatSettingsException("not an object");
        var s = Merged(settings);
        foreach (var k in RequestKeys)
            if (r.ContainsKey(k)) s[k] = J.Copy(r[k]);
        if (J.Truthy(r, "resume"))
        {
            s["hold"] = null;
        }
        else if (J.Get(r, "hold") is JsonObject h)
        {
            var hold = new JsonObject
            {
                ["heat"] = h.ContainsKey("heat") ? J.Copy(h["heat"]) : J.Copy(s["heat_sp"]),
                ["cool"] = h.ContainsKey("cool") ? J.Copy(h["cool"]) : J.Copy(s["cool_sp"]),
                ["until"] = J.Copy(J.Get(h, "until")),
            };
            if (hold["until"] is not null && ParseIsoLocal(Py.Str(hold["until"])) is null)
                throw new ThermostatSettingsException("hold until must be an ISO local time");
            var herrs = ValidateHold(hold);
            if (herrs.Count > 0) throw new ThermostatSettingsException(string.Join("; ", herrs));
            s["hold"] = hold;
        }
        var errs = Validate(s);
        if (errs.Count > 0) throw new ThermostatSettingsException(string.Join("; ", errs));
        return s;
    }

    [GeneratedRegex(@"^(\d{4})-?(\d{2})-?(\d{2})(?:[T ](\d{2})(?::?(\d{2})(?::?(\d{2})(?:[.,](\d{1,6}))?)?)?(Z|[+-]\d{2}(?::?\d{2})?)?)?$")]
    private static partial Regex IsoRegex();

    /// <summary>datetime.fromisoformat for the forms the thermostat and web app send; null if invalid.
    /// A zone suffix is accepted and ignored (hold times are house-local).</summary>
    public static DateTime? ParseIsoLocal(string s)
    {
        var m = IsoRegex().Match(s);
        if (!m.Success) return null;
        int I(int g) => m.Groups[g].Success ? int.Parse(m.Groups[g].Value, CultureInfo.InvariantCulture) : 0;
        try
        {
            var d = new DateTime(I(1), I(2), I(3), I(4), I(5), I(6), DateTimeKind.Unspecified);
            if (m.Groups[7].Success) d = d.AddTicks(int.Parse(m.Groups[7].Value.PadRight(6, '0'), CultureInfo.InvariantCulture) * 10L);
            return d;
        }
        catch (ArgumentOutOfRangeException)
        {
            return null;
        }
    }

    public static string IsoFormat(DateTime d) =>
        d.ToString(d.Ticks % TimeSpan.TicksPerSecond == 0 ? "yyyy-MM-dd'T'HH:mm:ss" : "yyyy-MM-dd'T'HH:mm:ss.ffffff", CultureInfo.InvariantCulture);

    /// <summary>(heat, cool, source, next change) at local time `when`: a hold wins, then the schedule
    /// period that started most recently (wrapping round the week), else the base setpoints.</summary>
    public static (JsonNode? Heat, JsonNode? Cool, string Source, string? Next) ScheduleNow(JsonNode? settings, DateTime when)
    {
        var s = Merged(settings);
        var hold = J.Get(s, "hold");
        if (Py.Truthy(hold))
        {
            var until = J.Get(hold, "until");
            if (until is null || (ParseIsoLocal(Py.Str(until)) is DateTime u && u > when))
                return (J.Copy(J.Has(hold, "heat") ? J.Get(hold, "heat") : s["heat_sp"]),
                        J.Copy(J.Has(hold, "cool") ? J.Get(hold, "cool") : s["cool_sp"]), "hold",
                        until is null ? null : Py.Str(until));
        }
        var periods = new List<(long Minute, JsonNode P)>();
        foreach (var p in Iterate(J.Get(s, "schedule")))
        {
            var parts = Py.Str(J.Get(p, "at")).Split(':');
            Py.TryParseInt(parts[0], out var h);
            Py.TryParseInt(parts.Length > 1 ? parts[1] : "0", out var m);
            foreach (var d in Iterate(J.Get(p, "days")))
            {
                Py.TryInt(d, out var day);
                periods.Add((day * 1440 + h * 60 + m, p!));
            }
        }
        if (periods.Count == 0) return (J.Copy(s["heat_sp"]), J.Copy(s["cool_sp"]), "manual", null);
        periods = periods.OrderBy(x => x.Minute).ToList();         // stable, like list.sort
        long minute = Weekday(when) * 1440 + when.Hour * 60 + when.Minute;
        var current = periods[^1];
        bool found = false;
        foreach (var x in periods)
            if (x.Minute <= minute && (!found || x.Minute > current.Minute)) { current = x; found = true; }
        var next = periods[0];
        found = false;
        foreach (var x in periods)
            if (x.Minute > minute && (!found || x.Minute < next.Minute)) { next = x; found = true; }
        long ahead = Mod(next.Minute - minute, 7 * 1440);
        if (ahead == 0) ahead = 7 * 1440;
        var at = when.AddMinutes(ahead);
        at = new DateTime(at.Year, at.Month, at.Day, at.Hour, at.Minute, 0, DateTimeKind.Unspecified);
        return (J.Copy(J.Get(current.P, "heat")), J.Copy(J.Get(current.P, "cool")), "schedule", IsoFormat(at));
    }

    static long Mod(long a, long n) => ((a % n) + n) % n;

    /// <summary>Monday = 0, as Python's weekday().</summary>
    public static int Weekday(DateTime d) => ((int)d.DayOfWeek + 6) % 7;

    /// <summary>A temporary hold of new setpoints until the next scheduled change (permanent without a schedule).</summary>
    public static JsonObject HoldUntilNext(JsonNode? settings, DateTime when, JsonNode? heat = null, JsonNode? cool = null)
    {
        var b = Merged(settings);
        b["hold"] = null;
        var (h, c, source, next) = ScheduleNow(b, when);
        return new JsonObject
        {
            ["heat"] = heat is not null ? J.Copy(heat) : h,
            ["cool"] = cool is not null ? J.Copy(cool) : c,
            ["until"] = source == "schedule" ? next : null,
        };
    }

    static string Title(string s) => s.Length == 0 ? s : char.ToUpperInvariant(s[0]) + s[1..].ToLowerInvariant();

    /// <summary>Problems visible from the thermostat's report and the other nodes.</summary>
    public static List<JsonObject> Flags(JsonNode data, JsonObject snap)
    {
        var f = new List<JsonObject>();
        var room = J.Num(J.Get(data, "room"), "t");
        if (room is Num r1 && r1.V >= HotF) f.Add(J.Flag("alert", "room_hot", $"Room temperature {G(r1.V)} F"));
        if (room is Num r2 && r2.V <= ColdF) f.Add(J.Flag("alert", "room_cold", $"Room temperature {G(r2.V)} F"));
        string? call = J.Str(data, "call");
        var minutes = J.Num(data, "call_min");
        var sp = J.Get(data, "sp");
        if (call is "cool" or "heat" && minutes is Num mins && mins.V >= NotReachedMin && room is Num r3)
        {
            var target = J.Num(sp, call == "cool" ? "cool" : "heat");
            double? gap = target is Num t ? (call == "cool" ? r3.V - t.V : t.V - r3.V) : null;
            if (gap is double g && g >= NotReachedF)
                f.Add(J.Flag("warn", "setpoint_not_reached",
                             $"{Title(call)}ing for {G(mins.V)} min and the room is still {Py.FormatFixed(g, 1)} F from {G(target!.Value.V)} F"));
        }
        var outY = J.Get(J.Get(data, "out"), "Y");
        var seenY = J.Get(snap, "Y");
        if (outY is not null && seenY is not null && Py.Truthy(outY) != Py.Truthy(seenY))
            f.Add(J.Flag("warn", "call_mismatch",
                         $"Thermostat output Y is {(Py.Truthy(outY) ? "on" : "off")} but the equipment sees it "
                         + $"{(Py.Truthy(seenY) ? "on" : "off")}: check the wiring or the thermostat's relay"));
        if (J.Truthy(data, "err"))
            f.Add(J.Flag("warn", "sensor_issue", $"Thermostat sensor issue: {J.JoinErrors(J.Get(data, "err"))}", Node));
        return f;
    }

    /// <summary>Add the thermostat's report and flags to a snapshot (state as in Electrical.Apply).</summary>
    public static JsonObject Apply(JsonObject snap, JsonObject? state)
    {
        if (state is null)
        {
            snap["tstat"] = null;
            return snap;
        }
        var data = J.Get(state, "data");
        if (!J.Truthy(state, "online") || !Py.Truthy(data))
        {
            snap["tstat"] = null;
            J.FlagList(snap).Add(J.Flag("warn", "node_offline", "Thermostat is offline", Node));
            return snap;
        }
        var tstat = new JsonObject();
        foreach (var k in TstatKeys) tstat[k] = J.Copy(J.Get(data, k));
        snap["tstat"] = tstat;
        var extra = Flags(data!, snap);
        var flags = J.FlagList(snap);
        foreach (var x in extra) flags.Add(x);
        return snap;
    }
}
