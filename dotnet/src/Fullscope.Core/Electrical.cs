using System.Text.Json.Nodes;

namespace Fullscope.Core;

/// <summary>
/// Electrical module (roadmap phase 11): amps, line voltage, contactor drop and run capacitors.
/// Port of hvaccloud/electrical.py. The module reports as a third node, "electrical"; its readings
/// make no snapshot of their own: the next outdoor/indoor snapshot adds "elec" and these flags,
/// judged against the nameplate values on the Equipment page (rules needing a missing rating are skipped).
/// </summary>
public static class Electrical
{
    public const string Node = "electrical";
    public const double CapConstant = 2652.6;      // uF = amps x 2652.6 / volts at 60 Hz
    public const double CapTolerance = 0.06;       // run capacitors are rated +/-6 %
    public const double ContactorWarnV = 1.0, ContactorReplaceV = 2.0;
    public const double SlowStartMs = 700;
    public const double DefaultVoltsLo = 197.0, DefaultVoltsHi = 253.0;
    public const double SettleMin = 1.0;           // minutes of compressor call before amps are judged

    static Num? R1(Num? v) => v is Num n ? (n.IsInt ? n : Num.Float(Py.Round(n.V, 1))) : null;

    /// <summary>Run capacitor uF under load, or null when its motor isn't running.</summary>
    public static double? Capacitance(JsonNode? section)
    {
        var v = J.Num(section, "v");
        var i = J.Num(section, "i");
        if (v is not Num vv || i is not Num ii || vv.V < 50 || ii.V < 0.05) return null;
        return Py.Round(ii.V * CapConstant / vv.V, 1);
    }

    /// <summary>The values the technician view shows ("elec" in a snapshot).</summary>
    public static JsonObject Derive(JsonNode data, EquipmentInfo? eq)
    {
        var v = J.Obj(data, "v");
        var i = J.Obj(data, "i");
        var cap = J.Obj(data, "cap");
        var start = J.Obj(data, "start");
        double? rla = eq?.CompRla, fla = eq?.FanFla;
        Num? comp = J.Num(i, "comp"), fan = J.Num(i, "fan");
        double? herm = Capacitance(J.Get(cap, "herm")), fanc = Capacitance(J.Get(cap, "fan"));
        double? ratedH = eq?.CapHermUf, ratedF = eq?.CapFanUf;
        bool contactor = J.Truthy(data, "contactor");
        return new JsonObject
        {
            ["contactor"] = contactor,
            ["line_v"] = J.Node(R1(J.Num(v, "line"))),
            ["contactor_v"] = contactor ? J.Node(J.Num(v, "contactor")) : null,
            ["comp_a"] = J.Node(R1(comp)),
            ["fan_a"] = J.Node(R1(fan)),
            ["comp_pct_rla"] = comp is Num c && rla is double r && r != 0 ? Py.RoundInt(100 * c.V / r) : null,
            ["fan_pct_fla"] = fan is Num f && fla is double fl && fl != 0 ? Py.RoundInt(100 * f.V / fl) : null,
            ["cap_herm_uf"] = herm,
            ["cap_fan_uf"] = fanc,
            ["cap_herm_pct"] = herm is double h && ratedH is double rh && rh != 0 ? Py.Round(100 * h / rh, 1) : null,
            ["cap_fan_pct"] = fanc is double fc && ratedF is double rf && rf != 0 ? Py.Round(100 * fc / rf, 1) : null,
            ["start_peak_a"] = J.Node(R1(J.Num(start, "peak_a"))),
            ["start_ms"] = J.Node(J.Num(start, "ms")),
        };
    }

    /// <summary>Diagnostic flags from derived electrical values (same shape as calc's flags).</summary>
    public static List<JsonObject> Flags(JsonObject e, JsonObject snap, EquipmentInfo? eq)
    {
        var f = new List<JsonObject>();
        bool calling = J.Truthy(snap, "Y");
        bool settled = calling && (J.Num(snap, "run_min")?.V ?? 0) >= SettleMin;
        double? rla = eq?.CompRla is double r0 && r0 != 0 ? r0 : null;
        double? fla = eq?.FanFla is double f0 && f0 != 0 ? f0 : null;
        Num? comp = J.Num(e, "comp_a"), fan = J.Num(e, "fan_a");
        bool contactor = J.Truthy(e, "contactor");
        if (settled && !contactor)
            f.Add(J.Flag("alert", "contactor_open",
                         "Cooling call but the contactor isn't closed: check the coil and the low-voltage circuit"));
        double runningFloor = rla is double r1 ? 0.1 * r1 : 1.0;
        if (settled && contactor && comp is Num c1 && c1.V < runningFloor)
            f.Add(J.Flag("alert", "comp_not_running",
                         $"Contactor closed but the compressor draws {c1} A: overload tripped, failed capacitor or compressor"));
        bool compressorOn = comp is Num c2 && c2.V >= runningFloor;
        if (settled && compressorOn && fan is Num f1 && f1.V < 0.2)
            f.Add(J.Flag("alert", "fan_not_running",
                         $"Compressor running but the condenser fan draws {f1} A: fan motor or its capacitor"));
        if (settled && compressorOn && rla is double r2 && comp!.Value.V > r2)
            f.Add(J.Flag("warn", "comp_amps_high", $"Compressor draws {comp} A, above its {Py.FormatG(r2)} A RLA"));
        if (settled && fla is double fl && fan is Num f2 && f2.V > fl * 1.1)
            f.Add(J.Flag("warn", "fan_amps_high", $"Condenser fan draws {f2} A, above its {Py.FormatG(fl)} A FLA"));
        foreach (var (sec, label, rated) in new[] { ("herm", "compressor (HERM)", eq?.CapHermUf), ("fan", "fan", eq?.CapFanUf) })
        {
            var pct = J.Num(e, $"cap_{sec}_pct");
            if (compressorOn && pct is Num p && Math.Abs(p.V - 100) > CapTolerance * 100)
                f.Add(J.Flag("warn", $"cap_{sec}",
                             $"Run capacitor {label} section measures {J.Num(e, $"cap_{sec}_uf")} uF, rated {Py.FormatG(rated!.Value)} uF (±6 %)"));
        }
        if (J.Num(e, "contactor_v") is Num cv && cv.V > ContactorWarnV)
            f.Add(J.Flag("warn", "contactor_drop",
                         $"{Py.FormatG(cv.V)} V across the closed contactor: pitted contacts"
                         + (cv.V > ContactorReplaceV ? " (replace)" : " (watch; replace above 2 V)")));
        double lo = eq?.VoltMin is double vmin && vmin != 0 ? vmin : DefaultVoltsLo;
        double hi = eq?.VoltMax is double vmax && vmax != 0 ? vmax : DefaultVoltsHi;
        if (J.Num(e, "line_v") is Num lv && contactor && !(lo <= lv.V && lv.V <= hi))
            f.Add(J.Flag("warn", "voltage",
                         $"Line voltage {Py.FormatG(lv.V)} V while running, outside the unit's {Py.FormatG(lo)}-{Py.FormatG(hi)} V range"));
        if (J.Num(e, "start_ms") is Num ms && ms.V > SlowStartMs)
            f.Add(J.Flag("warn", "slow_start",
                         $"Compressor took {Py.FormatG(ms.V)} ms to start (peak {Py.ReprNode(J.Get(e, "start_peak_a"))} A): weak capacitor, low voltage or a hard-starting compressor"));
        return f;
    }

    /// <summary>Add the module's values and flags to a snapshot. state is its latest {"online", "data"}
    /// (null when no module has ever reported: then nothing is added).</summary>
    public static JsonObject Apply(JsonObject snap, JsonObject? state, EquipmentInfo? eq)
    {
        if (state is null)
        {
            snap["elec"] = null;
            return snap;
        }
        var data = J.Get(state, "data");
        if (!J.Truthy(state, "online") || !Py.Truthy(data))
        {
            snap["elec"] = null;
            J.FlagList(snap).Add(J.Flag("warn", "node_offline", "Electrical module is offline", Node));
            return snap;
        }
        var e = Derive(data!, eq);
        snap["elec"] = e;
        var extra = Flags(e, snap, eq);
        if (J.Truthy(data, "err"))
            extra.Add(J.Flag("warn", "sensor_issue", $"Electrical sensor issue: {J.JoinErrors(J.Get(data, "err"))}", Node));
        var flags = J.FlagList(snap);
        foreach (var x in extra) flags.Add(x);
        return snap;
    }
}
