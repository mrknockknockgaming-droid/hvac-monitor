using System.Text.Json.Nodes;

namespace Fullscope.Core;

/// <summary>The Equipment page's values (all optional), as the diagnostics need them.</summary>
public sealed record EquipmentInfo
{
    public string? SystemType { get; init; }
    public string? Metering { get; init; }
    public double? Tonnage { get; init; }
    public double? ScTarget { get; init; }
    public double? ScTolerance { get; init; }
    public double? RatedBtuh { get; init; }
    public double? RatedCfm { get; init; }
    public double? MaxEsp { get; init; }
    public double? ElevationFt { get; init; }
    public double? CompRla { get; init; }
    public double? CompLra { get; init; }
    public double? FanFla { get; init; }
    public double? CapHermUf { get; init; }
    public double? CapFanUf { get; init; }
    public double? VoltMin { get; init; }
    public double? VoltMax { get; init; }
}

/// <summary>
/// Equipment details and the targets the diagnostics judge against (port of hvaccloud/equipment.py).
/// Calc uses generic limits (subcooling 3-20 F, superheat 3-30 F). Once the equipment is entered,
/// subcooling is judged against the nameplate target for TXV and EEV systems in cooling. Superheat
/// keeps the generic limits for a TXV or EEV, which regulates it; a piston system in cooling is judged
/// against the charging-chart target from the return wet bulb (Psychro) and the outdoor dry bulb.
/// </summary>
public static class Equipment
{
    public static readonly IReadOnlyDictionary<string, string> SystemTypes = new Dictionary<string, string>
    {
        ["split_hp"] = "Split heat pump", ["split_ac"] = "Split air conditioner",
        ["packaged_hp"] = "Packaged heat pump", ["packaged_ac"] = "Packaged air conditioner",
    };
    public static readonly IReadOnlyDictionary<string, string> MeteringNames = new Dictionary<string, string>
    {
        ["txv"] = "TXV", ["eev"] = "EEV", ["piston"] = "Fixed orifice / piston",
    };
    public const double GenericScLo = 3.0, GenericScHi = 20.0, GenericShLo = 3.0, GenericShHi = 30.0;
    public const double DefaultScTol = 3.0;
    public const double ChartShTol = 5.0, ChartWbLo = 50.0, ChartWbHi = 76.0, ChartOdbLo = 55.0, ChartOdbHi = 115.0;
    public const double ChartShMin = 5.0;

    /// <summary>Standard-atmosphere pressure at an elevation, psia (14.0 at about 1,300 ft).</summary>
    public static double AtmFromElevation(double ft) => Py.Round(14.696 * Math.Pow(1 - 6.8754e-6 * ft, 5.2559), 2);

    /// <summary>(low, high, "nameplate" | "generic") subcooling limits for this equipment and mode.</summary>
    public static (double Lo, double Hi, string Source) ScBand(EquipmentInfo? eq, string? mode)
    {
        if (eq is not null && eq.Metering is "txv" or "eev" && eq.ScTarget is double target && mode == "cooling")
        {
            double tol = eq.ScTolerance ?? DefaultScTol;
            return (Py.Round(target - tol, 1), Py.Round(target + tol, 1), "nameplate");
        }
        return (GenericScLo, GenericScHi, "generic");
    }

    /// <summary>Fixed-orifice charging-chart target superheat from the indoor (return) wet bulb and the
    /// outdoor dry bulb: (3 x WB - 80 - ODB) / 2. Null outside the chart.</summary>
    public static double? ChartSuperheat(double? wb, double? odb)
    {
        if (wb is not double w || odb is not double o || !(ChartWbLo <= w && w <= ChartWbHi) || !(ChartOdbLo <= o && o <= ChartOdbHi))
            return null;
        double target = Py.Round((3 * w - 80 - o) / 2, 1);
        return target >= ChartShMin ? target : null;
    }

    /// <summary>(low, high, "chart" | "generic", target) superheat limits for this equipment and snapshot.</summary>
    public static (double Lo, double Hi, string Source, double? Target) ShBand(EquipmentInfo? eq, JsonObject snap)
    {
        if (eq?.Metering == "piston" && J.Str(snap, "mode") == "cooling"
            && ChartSuperheat(J.Num(J.Get(J.Get(snap, "air_rh"), "ret"), "wb")?.V, J.Num(snap, "oat")?.V) is double target)
            return (Py.Round(target - ChartShTol, 1), Py.Round(target + ChartShTol, 1), "chart", target);
        return (GenericShLo, GenericShHi, "generic", null);
    }

    public static string SuperheatNote(EquipmentInfo? eq)
    {
        if (eq?.Metering is null) return "Generic limits until the metering device is set.";
        if (eq.Metering == "piston")
            return "Charging-chart target from the return wet bulb and outdoor temperature, ± 5 °F, while cooling; "
                 + "generic limits when indoor humidity isn't measured or is outside the chart.";
        return $"The {MeteringNames[eq.Metering]} sets superheat; flagged only outside the generic limits.";
    }

    /// <summary>Judge subcooling against the equipment's target (replaces calc's generic SC flags) and
    /// record the targets on the snapshot so the technician view can show them.</summary>
    public static JsonObject Apply(JsonObject snap, EquipmentInfo? eq)
    {
        string? mode = J.Str(snap, "mode");
        var (lo, hi, source) = ScBand(eq, mode);
        var (shLo, shHi, shSource, shTarget) = ShBand(eq, snap);
        var sh = new JsonObject { ["lo"] = shLo, ["hi"] = shHi, ["source"] = shSource };
        if (shSource == "chart") sh["target"] = shTarget;
        snap["targets"] = new JsonObject
        {
            ["sc"] = new JsonObject { ["lo"] = lo, ["hi"] = hi, ["source"] = source,
                                      ["target"] = source == "nameplate" ? eq!.ScTarget : null },
            ["sh"] = sh,
        };
        bool running = mode is "cooling" or "heating" && (J.Num(snap, "run_min")?.V ?? 0) >= 10;
        if (shSource == "chart") snap["flags"] = ChartShFlags(snap, running, shLo, shHi, shTarget!.Value);
        if (source == "generic") return snap;
        var flags = new JsonArray(J.Flags(snap).Where(f => J.Str(f, "code") is not ("sc_low" or "sc_high"))
                                              .Select(J.Copy).ToArray());
        var sc = J.Num(snap, "sc");
        if (running && sc is Num v)
        {
            string tgt = $"{Py.FormatG(eq!.ScTarget!.Value)} ± {Py.FormatG((hi - lo) / 2)}°F";
            if (v.V < lo)
                flags.Add(J.Flag("warn", "sc_low", $"Subcooling {v}°F is below the nameplate target {tgt}: possible undercharge"));
            else if (v.V > hi)
                flags.Add(J.Flag("warn", "sc_high", $"Subcooling {v}°F is above the nameplate target {tgt}: possible overcharge or restriction"));
        }
        snap["flags"] = flags;
        return snap;
    }

    /// <summary>Replace Calc's generic superheat flags with the charging-chart band; Calc's floodback
    /// alert (below 3 F) stays whatever the chart says.</summary>
    static JsonArray ChartShFlags(JsonObject snap, bool running, double lo, double hi, double target)
    {
        var old = J.Flags(snap);
        var flags = new JsonArray(old.Where(f => J.Str(f, "code") is not ("sh_low" or "sh_high")).Select(J.Copy).ToArray());
        if (!running || J.Num(snap, "sh") is not Num sh) return flags;
        string tgt = $"{Py.FormatG(target)} ± {Py.FormatG(ChartShTol)}°F";
        if (sh.V < GenericShLo)
            foreach (var f in old.Where(f => J.Str(f, "code") == "sh_low")) flags.Add(J.Copy(f));
        else if (sh.V < lo)
            flags.Add(J.Flag("warn", "sh_low", $"Superheat {sh}°F is below the charging-chart target {tgt}: possible overcharge or low airflow"));
        else if (sh.V > hi)
            flags.Add(J.Flag("warn", "sh_high", $"Superheat {sh}°F is above the charging-chart target {tgt}: possible undercharge or restriction"));
        return flags;
    }
}
