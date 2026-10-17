using System.Text.Json.Nodes;
using Fullscope.Core;

namespace Fullscope.Cloud.Api;

/// <summary>
/// Threshold report (port of hvaccloud/report.py): the spread of superheat, subcooling, delta-T,
/// condensing over ambient and approach in steady cooling (cooling, run_min >= 10, as Calc.Flags
/// judges), next to the alert limits, with the share of steady time each limit would flag.
/// Percentiles are nearest-rank: index floor(p * (n - 1) + 0.5).
/// </summary>
public static class Report
{
    public const double SteadyMin = 10;          // Calc.Flags judges only after this many minutes of running
    public const double DtLow = 12.0;            // Calc.Flags: dt_low (cooling, blower on)
    public const double CtoaHigh = 35.0;         // Calc.Flags: ctoa_high (cooling)
    public const double GapCapS = 60;            // a longer gap between steady samples counts as this much steady time
    static readonly int[] Pcts = [5, 25, 50, 75, 95];
    public static readonly string[] Metrics = ["sh", "sc", "dt", "ctoa", "approach"];

    static Num Pct(List<Num> sorted, int p) => sorted[Math.Min(sorted.Count - 1, (int)Math.Floor(p / 100.0 * (sorted.Count - 1) + 0.5))];

    static (double? Lo, double? Hi, string? Source) Limits(string m, EquipmentInfo? eq)
    {
        switch (m)
        {
            case "sh": return (Equipment.GenericShLo, Equipment.GenericShHi, "generic");
            case "sc":
                var (lo, hi, src) = Equipment.ScBand(eq, "cooling");
                return (lo, hi, src);
            case "dt": return (DtLow, null, "generic");
            case "ctoa": return (null, CtoaHigh, "generic");
            default: return (null, null, null);
        }
    }

    /// <param name="rows">(epoch seconds, snapshot data), oldest first.</param>
    /// <param name="alertCounts">Alerts raised in the window, by code.</param>
    public static JsonObject Build(IEnumerable<(double T, JsonNode? D)> rows, EquipmentInfo? eq, IDictionary<string, int> alertCounts, JsonNode days)
    {
        var vals = Metrics.ToDictionary(m => m, _ => new List<Num>());
        double steadyS = 0;
        double? prev = null;
        foreach (var (t, d) in rows)
        {
            if (J.Str(d, "mode") != "cooling" || (J.Num(d, "run_min")?.V ?? 0) < SteadyMin)
            {
                prev = null;
                continue;
            }
            if (prev is double p) steadyS += Math.Min(t - p, GapCapS);
            prev = t;
            foreach (var m in Metrics)
            {
                if (J.Num(d, m) is not Num v || (m == "dt" && !J.Truthy(d, "G"))) continue;
                vals[m].Add(v);
            }
        }
        var metrics = new JsonObject();
        foreach (var m in Metrics)
        {
            var v = vals[m].OrderBy(x => x.V).ToList();
            var (lo, hi, src) = Limits(m, eq);
            var row = new JsonObject
            {
                ["n"] = v.Count, ["min"] = null, ["max"] = null, ["pct"] = null,
                ["limit_low"] = lo, ["limit_high"] = hi, ["limit_source"] = src, ["below"] = null, ["above"] = null,
            };
            if (v.Count > 0)
            {
                row["min"] = v[0].ToNode();
                row["max"] = v[^1].ToNode();
                var pct = new JsonObject();
                foreach (var p in Pcts) pct[p.ToString(System.Globalization.CultureInfo.InvariantCulture)] = Pct(v, p).ToNode();
                row["pct"] = pct;
                if (lo is double l) row["below"] = Py.Round((double)v.Count(x => x.V < l) / v.Count, 3);
                if (hi is double h) row["above"] = Py.Round((double)v.Count(x => x.V > h) / v.Count, 3);
            }
            metrics[m] = row;
        }
        var alerts = new JsonObject();
        foreach (var kv in alertCounts.OrderBy(kv => kv.Key, StringComparer.Ordinal)) alerts[kv.Key] = kv.Value;
        return new JsonObject
        {
            ["days"] = days.DeepClone(), ["steady_hours"] = Py.Round(steadyS / 3600, 2), ["metrics"] = metrics, ["alerts"] = alerts,
        };
    }
}
