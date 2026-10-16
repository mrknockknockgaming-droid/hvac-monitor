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
/// keeps the generic limits: a TXV or EEV regulates it, and a piston's target needs indoor wet bulb.
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

    public static string SuperheatNote(EquipmentInfo? eq)
    {
        if (eq?.Metering is null) return "Generic limits until the metering device is set.";
        if (eq.Metering == "piston") return "A piston's target superheat needs indoor humidity (phase 6); generic limits until then.";
        return $"The {MeteringNames[eq.Metering]} sets superheat; flagged only outside the generic limits.";
    }

    /// <summary>Judge subcooling against the equipment's target (replaces calc's generic SC flags) and
    /// record the targets on the snapshot so the technician view can show them.</summary>
    public static JsonObject Apply(JsonObject snap, EquipmentInfo? eq)
    {
        string? mode = J.Str(snap, "mode");
        var (lo, hi, source) = ScBand(eq, mode);
        snap["targets"] = new JsonObject
        {
            ["sc"] = new JsonObject { ["lo"] = lo, ["hi"] = hi, ["source"] = source,
                                      ["target"] = source == "nameplate" ? eq!.ScTarget : null },
            ["sh"] = new JsonObject { ["lo"] = GenericShLo, ["hi"] = GenericShHi, ["source"] = "generic" },
        };
        if (source == "generic") return snap;
        var flags = new JsonArray(J.Flags(snap).Where(f => J.Str(f, "code") is not ("sc_low" or "sc_high"))
                                              .Select(J.Copy).ToArray());
        var sc = J.Num(snap, "sc");
        bool running = mode is "cooling" or "heating" && (J.Num(snap, "run_min")?.V ?? 0) >= 10;
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
}
