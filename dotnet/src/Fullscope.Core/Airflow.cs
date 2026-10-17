using System.Text.Json.Nodes;

namespace Fullscope.Core;

/// <summary>
/// Static pressure from the indoor node's three SDP810 sensors (port of hvac-cloud/hvaccloud/airflow.py;
/// the snapshot vectors replay the Python results, so change both together). The node reports
/// air.sp_ret, sp_sup and sp_filt in in. w.c.; without them the snapshot gets no "static". TESP =
/// supply - return. While the blower runs, TESP above the rated maximum raises tesp_high and a filter
/// drop above FilterDropMax raises filter_dirty.
/// </summary>
public static class Airflow
{
    public const double FilterDropMax = 0.30;
    static readonly string[] Keys = ["sp_ret", "sp_sup", "sp_filt"];

    static JsonNode? R2(Num? v) => v is Num n ? JsonValue.Create(Py.Round(n.V, 2)) : null;

    /// <param name="indoor">{"online", "data"} as Calc gets it, or null.</param>
    public static JsonObject Apply(JsonObject snap, JsonNode? indoor, EquipmentInfo? eq)
    {
        var data = J.Truthy(indoor, "online") ? J.Get(indoor, "data") : null;
        if (J.Get(data, "air") is not JsonObject air || !Keys.Any(air.ContainsKey)) return snap;
        Num? ret = J.Num(air, "sp_ret"), sup = J.Num(air, "sp_sup"), filt = J.Num(air, "sp_filt");
        double? tesp = sup is Num s && ret is Num r ? Py.Round(s.V - r.V, 2) : null;
        double? rated = eq?.MaxEsp is double m && double.IsFinite(m) ? m : null;
        snap["static"] = new JsonObject
        {
            ["ret"] = R2(ret), ["sup"] = R2(sup), ["filter"] = R2(filt), ["tesp"] = J.Node(tesp), ["max"] = J.Node(rated),
        };
        if (J.Str(snap, "mode") is not ("cooling" or "heating" or "heat_aux" or "fan")) return snap;
        var flags = J.FlagList(snap);
        if (tesp is double t && rated is double mx && t > mx)
            flags.Add(J.Flag("warn", "tesp_high",
                $"Total external static {Py.Repr(t)} in. w.c. is above the rated {Py.FormatG(mx)}: check the filter, coil and ductwork"));
        if (filt is Num fl && Py.Round(fl.V, 2) is var f && f > FilterDropMax)
            flags.Add(J.Flag("warn", "filter_dirty",
                $"Filter pressure drop {Py.Repr(f)} in. w.c. is over {Py.FormatG(FilterDropMax)}: the filter is loading up"));
        return snap;
    }
}
