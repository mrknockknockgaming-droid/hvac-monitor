using System.Text.Json.Nodes;

namespace Fullscope.Core;

/// <summary>
/// Indoor air humidity: relative humidity, wet bulb, dew point and enthalpy at the return and supply,
/// from the indoor node's two SHT45 probes (port of hvac-cloud/hvaccloud/psychro.py; the snapshot
/// vectors replay the Python results, so change both together). Nodes without the probes send no
/// rh_* keys and the snapshot gets no "air_rh". Applied after Calc.Compute and before Equipment.Apply,
/// which uses the return wet bulb for a piston's charging-chart target superheat.
/// </summary>
public static class Psychro
{
    const double KpaPerPsi = 6.894757;
    static readonly string[] Sides = ["ret", "sup"];

    /// <summary>Saturation vapour pressure over water, kPa, at c deg C (Magnus, Alduchov and Eskridge).</summary>
    public static double PsatKpa(double c) => 0.61094 * Math.Exp(17.625 * c / (c + 243.04));

    static double Ratio(double pw, double p) => 0.621945 * pw / (p - pw);

    /// <summary>Thermodynamic wet bulb, deg C: solves the psychrometric equation for humidity ratio w.</summary>
    public static double WetBulbC(double c, double w, double p)
    {
        double WAt(double wb)
        {
            double ws = Ratio(PsatKpa(wb), p);
            return ((2501 - 2.326 * wb) * ws - 1.006 * (c - wb)) / (2501 + 1.86 * c - 4.186 * wb);
        }
        double lo = c - 60.0, hi = c;
        for (int i = 0; i < 60; i++)
        {
            double mid = (lo + hi) / 2;
            if (WAt(mid) > w) hi = mid;
            else lo = mid;
        }
        return (lo + hi) / 2;
    }

    static double R1(double v) => Py.Round(v, 1);

    /// <summary>{"rh", "wb", "dp", "h"} for one probe (deg F, Btu/lb), or null without a usable reading.</summary>
    public static JsonObject? Point(Num? tF, Num? rhN, double? atmPsia)
    {
        if (tF is not Num tn || rhN is not Num rn || !(rn.V > 0 && rn.V <= 100) || atmPsia is not double atm) return null;
        double t = tn.V, rh = rn.V;
        double c = (t - 32) / 1.8;
        double p = atm * KpaPerPsi;
        double pw = rh / 100 * PsatKpa(c);
        double w = Ratio(pw, p);
        double g = Math.Log(pw / 0.61094);
        double dp = 243.04 * g / (17.625 - g);
        double wb = Math.Min(WetBulbC(c, w, p), c);
        double h = 0.240 * t + w * (1061 + 0.444 * t);
        return new JsonObject { ["rh"] = R1(rh), ["wb"] = R1(wb * 1.8 + 32), ["dp"] = R1(dp * 1.8 + 32), ["h"] = R1(h) };
    }

    /// <summary>Add snap["air_rh"] = {"ret", "sup", "dh"} when the online indoor node reports humidity.</summary>
    /// <param name="indoor">{"online", "data"} as Calc gets it, or null.</param>
    public static JsonObject Apply(JsonObject snap, JsonNode? indoor, double? atmPsia)
    {
        var data = J.Truthy(indoor, "online") ? J.Get(indoor, "data") : null;
        if (J.Get(data, "air") is not JsonObject air || !Sides.Any(k => air.ContainsKey("rh_" + k))) return snap;
        var outp = new JsonObject();
        foreach (var k in Sides)
        {
            var t = J.Num(air, "t_rh_" + k) ?? J.Num(snap, k == "ret" ? "t_ret" : "t_sup");
            outp[k] = Point(t, J.Num(air, "rh_" + k), atmPsia);
        }
        var ret = outp["ret"] as JsonObject;
        var sup = outp["sup"] as JsonObject;
        outp["dh"] = ret is not null && sup is not null ? R1((double)ret["h"]! - (double)sup["h"]!) : null;
        snap["air_rh"] = outp;
        return snap;
    }
}
