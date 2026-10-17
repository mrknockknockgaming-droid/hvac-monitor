using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;

namespace Fullscope.Cloud.Api;

/// <summary>
/// Gauge checks: the technician's own gauge and thermometer readings next to what the system
/// measured (port of hvaccloud/gauge.py). The gauge side's saturation, superheat, subcooling and
/// delta-T are worked out the way Calc.Compute does it; the view shows system minus gauge.
/// Nothing here changes how the system runs; it only records and compares.
/// </summary>
public static class Gauge
{
    public static readonly string[] Entered = ["p_low", "p_high", "t_suc", "t_liq", "t_ret", "t_sup"];
    public static readonly string[] Compared = ["p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc", "t_ret", "t_sup", "dt"];
    public const double NearestS = 180;          // the snapshot compared must be this close to the time the gauges were read

    static Num? R1(double? v) => v is double d ? Num.Float(Py.Round(d, 1)) : null;

    /// <summary>The gauge side: entered values plus saturation, superheat, subcooling and delta-T.</summary>
    public static JsonObject Derive(IReadOnlyDictionary<string, double?> entered, string refrigerant, double atmPsia, RefrigerantTables tables)
    {
        double? E(string k) => entered.TryGetValue(k, out var v) && v is double d && double.IsFinite(d) ? d : null;
        double? Abs(double? p) => p is double x ? x + atmPsia : null;
        double? pLow = E("p_low"), pHigh = E("p_high"), tSuc = E("t_suc"), tLiq = E("t_liq"), tRet = E("t_ret"), tSup = E("t_sup");
        Num? satLow = R1(tables.DewF(refrigerant, Abs(pLow)));
        Num? satHigh = R1(tables.BubbleF(refrigerant, Abs(pHigh)));
        double? satLiq = tables.BubbleF(refrigerant, Abs(pHigh));
        var g = new JsonObject();
        foreach (var k in Entered) g[k] = J.Node(E(k));
        g["sat_low"] = J.Node(satLow);
        g["sat_high"] = J.Node(satHigh);
        g["sh"] = tSuc is double ts && satLow is Num sl ? J.Node(R1(ts - sl.V)) : null;
        g["sc"] = satLiq is double sq && tLiq is double tl ? J.Node(R1(sq - tl)) : null;
        g["dt"] = tRet is double tr && tSup is double tp ? J.Node(R1(tr - tp)) : null;
        return g;
    }

    /// <summary>What the system measured, from a snapshot's data.</summary>
    public static JsonObject SystemSide(JsonNode? snap)
    {
        var o = new JsonObject();
        foreach (var k in Compared) o[k] = J.Node(J.Num(snap, k));
        o["mode"] = J.Copy(J.Get(snap, "mode"));
        o["run_min"] = J.Node(J.Num(snap, "run_min"));
        return o;
    }

    /// <summary>System minus gauge for every value both sides have.</summary>
    public static JsonObject Differences(JsonNode? system, JsonNode? gauge)
    {
        var o = new JsonObject();
        foreach (var k in Compared)
        {
            Num? a = J.Num(system, k), b = J.Num(gauge, k);
            o[k] = a is Num x && b is Num y ? R1Num(x - y) : null;
        }
        return o;
    }

    static JsonNode R1Num(Num n) => (n.IsInt ? n : Num.Float(Py.Round(n.V, 1))).ToNode();

    /// <summary>Per value: how many checks had both sides, the average and the largest difference (by size).</summary>
    public static JsonObject Summary(IReadOnlyList<JsonObject> views)
    {
        var o = new JsonObject();
        foreach (var k in Compared)
        {
            var ds = views.Select(v => J.Num(J.Get(v, "diff"), k)).OfType<Num>().ToList();
            JsonNode? mean = null, max = null;
            if (ds.Count > 0)
            {
                var sum = new PySum();
                foreach (var d in ds) sum.Add(d);
                mean = R1Num(Num.Float(sum.Value / ds.Count));
                max = ds.MaxBy(d => Math.Abs(d.V)).ToNode();
            }
            o[k] = new JsonObject { ["n"] = ds.Count, ["mean"] = mean, ["max"] = max };
        }
        return o;
    }

    public static JsonObject CheckView(GaugeCheck c)
    {
        var gauge = PyJson.Loads(c.GaugeData ?? "null");
        var system = PyJson.Loads(c.SystemData ?? "null");
        return new JsonObject
        {
            ["id"] = c.Id, ["time"] = ApiServer.Iso(c.Time), ["technician"] = c.Technician, ["note"] = c.Note,
            ["refrigerant"] = c.Refrigerant, ["atm_psia"] = c.AtmPsia,
            ["gauge"] = gauge, ["system"] = system, ["diff"] = Differences(system, gauge),
        };
    }
}
