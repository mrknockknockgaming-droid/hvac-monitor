using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;

namespace Fullscope.Cloud.Ingest;

/// <summary>
/// What the display thermostat shows besides its own controls (port of hvaccloud/display.py):
/// homeowners get the raised alerts in plain words and the System health areas; technicians (behind
/// the service PIN) get key numbers, the diagnostics in technical words and a 6-hour trend with
/// electrical markers. Ingest publishes it retained on hvac/&lt;site&gt;/thermostat/display, and
/// GET /api/systems/{id}/thermostat/display returns the same.
/// </summary>
public static class Display
{
    public const int FeedVersion = 1;
    public const int TrendHours = 6;
    public const int TrendStepS = 120;
    static readonly string[] TrendKeys = ["p_low", "p_high", "t_ret", "t_sup", "oat"];
    const double SteadyMin = 10;

    public static readonly Dictionary<string, string> ElecLabel = new()
    {
        ["comp_not_running"] = "Compressor not running", ["fan_not_running"] = "Condenser fan not running",
        ["contactor_open"] = "Contactor not closing", ["comp_amps_high"] = "Compressor amps high",
        ["fan_amps_high"] = "Fan amps high", ["cap_herm"] = "Compressor capacitor weak", ["cap_fan"] = "Fan capacitor weak",
        ["contactor_drop"] = "Contactor pitted", ["voltage"] = "Line voltage", ["slow_start"] = "Slow compressor start",
    };
    static readonly string[] ElecOrder = ["comp_not_running", "fan_not_running", "contactor_open", "comp_amps_high", "fan_amps_high",
                                          "cap_herm", "cap_fan", "contactor_drop", "voltage", "slow_start"];
    static readonly string[] FaultCodes = ["comp_not_running", "fan_not_running", "contactor_open"];
    public static readonly Dictionary<string, string> Word = new()
    {
        ["fault"] = "Service needed", ["caution"] = "Check soon", ["advisory"] = "Good to know", ["ok"] = "Good", ["offline"] = "No data",
    };
    static readonly Dictionary<string, string> TechWord = new() { ["fault"] = "Fault", ["caution"] = "Warning", ["advisory"] = "Advisory" };
    static readonly Dictionary<string, int> Rank = new() { ["fault"] = 3, ["caution"] = 2, ["advisory"] = 1, ["ok"] = 0 };

    public static bool IsElectrical(string code, string? node = null) => ElecLabel.ContainsKey(code) || node == "electrical";

    public static string LevelOf(string code) => Alerts.Level.GetValueOrDefault(code, "caution");

    static JsonObject AlertItem(Alert a)
    {
        var (title, why, todo) = Alerts.Plain.TryGetValue(a.Code, out var p) ? p : (a.Text, a.Text, "");
        var lvl = LevelOf(a.Code);
        return new JsonObject
        {
            ["id"] = a.Id, ["code"] = a.Code, ["node"] = a.Node, ["level"] = lvl, ["word"] = Word[lvl], ["title"] = title,
            ["why"] = why, ["todo"] = todo, ["tech"] = a.Text, ["tech_word"] = TechWord.GetValueOrDefault(lvl, "Warning"),
            ["since"] = Db.Epoch(a.StartedAt), ["acked"] = a.AckedAt is not null,
        };
    }

    /// <summary>The System health rows, as on the homeowner web page (homeHealth in web/app.js).</summary>
    public static JsonArray Health(JsonObject? snap, bool fresh, IReadOnlySet<string> codes, int online, int nodesExpected = 2)
    {
        var d = snap ?? [];
        var mode = J.Str(d, "mode") is { Length: > 0 } m ? m : "idle";
        bool running = mode is "cooling" or "heating";
        bool steady = running && (J.Num(d, "run_min")?.V ?? 0) >= SteadyMin;

        JsonObject Area(string name, IEnumerable<string> badCodes, string good, string badLevel, string badText)
        {
            if (!fresh) return new JsonObject { ["name"] = name, ["level"] = "offline", ["word"] = Word["offline"], ["text"] = "Waiting for readings." };
            if (badCodes.Any(codes.Contains)) return new JsonObject { ["name"] = name, ["level"] = badLevel, ["word"] = Word[badLevel], ["text"] = badText };
            var text = steady ? good
                : running ? "No problems found so far. The full check runs after 10 minutes of steady running."
                : "No problems found. Checked each time your system runs.";
            return new JsonObject { ["name"] = name, ["level"] = "ok", ["word"] = Word["ok"], ["text"] = text };
        }

        var dt = J.Num(d, "dt");
        var dtText = dt is Num x
            ? $"Air from your vents is about {Py.RoundInt(Math.Abs(x.V))}° {(mode == "heating" ? "warmer" : "cooler")} than the air going in."
            : "Temperature change across the indoor coil looks right.";
        bool refrFault = codes.Contains("sh_low");
        bool outdoorFault = FaultCodes.Any(codes.Contains);
        bool elec = ElecOrder.Any(codes.Contains);
        var rows = new JsonArray
        {
            Area(mode == "heating" ? "Heating" : "Cooling", ["dt_low"], dtText, "caution",
                 "Air from your vents isn't changing temperature as much as it should."),
            Area("Comfort", ["room_hot", "room_cold", "setpoint_not_reached", "call_mismatch"],
                 "The house is reaching your settings.", codes.Contains("room_hot") || codes.Contains("room_cold") ? "fault" : "caution",
                 "The house isn't reaching your settings. See the alerts."),
            Area("Refrigerant", ["sh_low", "sh_high", "sc_low", "sc_high"], "Pressures and temperatures look normal for today's weather.",
                 refrFault ? "fault" : "caution", "Refrigerant readings are outside the normal range."),
            Area("Outdoor unit", ["ctoa_high", .. ElecOrder], "Running normally.", outdoorFault ? "fault" : "caution",
                 elec ? "Something in it needs attention." : "It isn't releasing heat as well as it should."),
        };
        bool sensor = codes.Contains("sensor_issue");
        bool all = online >= nodesExpected;
        rows.Add(new JsonObject
        {
            ["name"] = "Monitoring",
            ["level"] = all && !sensor ? "ok" : online != 0 ? "advisory" : "offline",
            ["word"] = all ? "Connected" : online != 0 ? "Partly connected" : "Offline",
            ["text"] = all ? "Monitors online." + (sensor ? " One sensor isn't reporting." : "")
                     : online != 0 ? "A monitor isn't reporting." : "The monitors aren't reporting.",
        });
        return rows;
    }

    /// <summary>Pressures and air temperatures averaged into `step`-second buckets, plus when it ran.</summary>
    public static JsonObject Trend(Db s, HvacSystem system, double now, int hours = TrendHours, int step = TrendStepS)
    {
        long t0 = (long)(Py.FloorDiv(now - hours * 3600, step) * step);
        int n = (int)Py.FloorDiv(now - t0, step) + 1;
        var sums = TrendKeys.ToDictionary(k => k, _ => new double[n]);
        var counts = TrendKeys.ToDictionary(k => k, _ => new int[n]);
        var on = new int[n];
        var seen = new int[n];
        var since = Db.Ts(t0);
        foreach (var row in s.Snapshots.Where(x => x.SystemId == system.Id && x.Time >= since).OrderBy(x => x.Time)
                              .Select(x => new { x.Time, x.Data }))
        {
            int i = (int)Py.FloorDiv(Db.Epoch(row.Time) - t0, step);
            if (i < 0 || i >= n) continue;
            var d = Db.Parse(row.Data);
            seen[i]++;
            if (J.Str(d, "mode") is "cooling" or "heating") on[i]++;
            foreach (var k in TrendKeys)
            {
                if (J.Num(d, k) is Num v)
                {
                    sums[k][i] += v.V;
                    counts[k][i]++;
                }
            }
        }
        var series = new JsonObject();
        foreach (var k in TrendKeys)
            series[k] = new JsonArray(Enumerable.Range(0, n)
                .Select(i => counts[k][i] > 0 ? (JsonNode?)JsonValue.Create(Py.Round(sums[k][i] / counts[k][i], 1)) : null).ToArray());
        return new JsonObject
        {
            ["t0"] = t0, ["step"] = step, ["series"] = series,
            ["on"] = new JsonArray(Enumerable.Range(0, n).Select(i => (JsonNode?)(seen[i] > 0 && on[i] * 2 >= seen[i] ? 1 : 0)).ToArray()),
        };
    }

    /// <summary>Electrical problems in the trend window: when each started and (if it has) ended.</summary>
    public static JsonArray Markers(Db s, HvacSystem system, double now, int hours = TrendHours)
    {
        var since = Db.Ts(now - hours * 3600);
        var outList = new JsonArray();
        foreach (var a in s.Alerts.Where(a => a.SystemId == system.Id && a.RaisedAt != null && (a.ClearedAt == null || a.ClearedAt >= since))
                                  .OrderBy(a => a.StartedAt).ThenBy(a => a.Id).ToList())
        {
            if (!IsElectrical(a.Code, a.Node)) continue;
            outList.Add(new JsonObject
            {
                ["code"] = a.Code, ["level"] = LevelOf(a.Code),
                ["label"] = ElecLabel.TryGetValue(a.Code, out var l) ? l : a.Code == "node_offline" ? "Electrical module offline" : "Electrical sensor",
                ["text"] = a.Text, ["start"] = Db.Epoch(a.StartedAt),
                ["end"] = a.ClearedAt is DateTime c ? Db.Epoch(c) : null,
            });
        }
        return outList;
    }

    /// <summary>The whole feed for one system.</summary>
    public static JsonObject Build(Db s, HvacSystem system, double now, double stale)
    {
        var row = s.Snapshots.Where(x => x.SystemId == system.Id).OrderByDescending(x => x.Time)
                   .Select(x => new { x.Time, x.Data }).FirstOrDefault();
        var snap = row is null ? null : Db.Parse(row.Data) as JsonObject;
        bool fresh = row is not null && now - Db.Epoch(row.Time) <= Math.Max(stale, 90);
        var open = s.Alerts.Where(a => a.SystemId == system.Id && a.RaisedAt != null && a.ClearedAt == null).OrderBy(a => a.Id).ToList();
        var items = open.Select(AlertItem)
                        .OrderBy(x => -Rank[(string)x["level"]!]).ThenBy(x => -(double)x["since"]!).ToList();
        var codes = open.Select(a => a.Code).ToHashSet();
        var nodes = Calc.Nodes;
        int online = s.Devices.Where(d => d.SystemId == system.Id && nodes.Contains(d.Node)).ToList()
                      .Count(d => d.Connected && d.LastSeen is DateTime ls && now - Db.Epoch(ls) <= stale);
        string worst = items.Count == 0 ? "ok"
            : items.Select(x => (string)x["level"]!).Aggregate((best, lvl) => Rank[lvl] > Rank[best] ? lvl : best);
        string headline, status;
        if (!fresh) (headline, status) = ("We can't reach your system's monitors", "offline");
        else if (items.Count > 0) (headline, status) = ((string)items[0]["title"]!, worst);
        else (headline, status) = ("Everything looks good", "ok");
        var d0 = snap ?? [];
        var elec = J.Get(d0, "elec") as JsonObject ?? [];
        var key = new JsonObject();
        foreach (var k in new[] { "mode", "run_min", "sh", "sc", "dt", "p_low", "p_high", "oat" }) key[k] = J.Copy(J.Get(d0, k));
        foreach (var k in new[] { "line_v", "comp_a", "fan_a" }) key[k] = J.Copy(J.Get(elec, k));
        return new JsonObject
        {
            ["ver"] = FeedVersion, ["time"] = now, ["updated"] = row is null ? null : Db.Epoch(row.Time),
            ["fresh"] = fresh, ["status"] = status, ["word"] = Word[status], ["headline"] = headline,
            ["alerts"] = new JsonArray(items.ToArray<JsonNode?>()), ["health"] = Health(snap, fresh, codes, online),
            ["tech"] = new JsonObject
            {
                ["now"] = key, ["has_elec"] = J.Truthy(d0, "elec"), ["trend"] = Trend(s, system, now), ["markers"] = Markers(s, system, now),
            },
        };
    }
}
