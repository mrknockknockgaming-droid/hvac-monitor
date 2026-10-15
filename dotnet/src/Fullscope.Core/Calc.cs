using System.Text.Json.Nodes;

namespace Fullscope.Core;

/// <summary>Per-system settings the calculation needs.</summary>
/// <param name="ObEnergized">"cool" or "heat": the mode in which the O/B reversing valve is energized.</param>
public sealed record CalcConfig(string Refrigerant, bool HeatPump, string ObEnergized, double AtmPsia);

/// <summary>
/// Operating mode, saturation temperatures, superheat, subcooling and fault flags. Port of
/// hvac-cloud/hvaccloud/calc.py (itself Hub.compute / Hub.flags of the PC dashboard); the tests
/// replay the Python results from tools/make_calc_vectors.py, so change both together.
/// </summary>
public static class Calc
{
    public static readonly string[] Nodes = ["outdoor", "indoor"];

    static Num? R1(Num? v) => v is Num n ? (n.IsInt ? n : Num.Float(Py.Round(n.V, 1))) : null;

    static Num? R1(double? v) => v is double d ? Num.Float(Py.Round(d, 1)) : null;

    static JsonNode? Node(Num? v) => v?.ToNode();

    static JsonNode? Get(JsonNode? o, string key) => o is JsonObject obj && obj.TryGetPropertyValue(key, out var v) ? v : null;

    static JsonObject Obj(JsonNode? n) => n as JsonObject ?? [];

    /// <summary>A finite number (not a bool), else null.</summary>
    static Num? NumOf(JsonNode? o, string key) => Py.NumberOf(Get(o, key)) is Num n && double.IsFinite(n.V) ? n : null;

    /// <param name="nodes">{"outdoor": {"online": bool, "data": object|null}, "indoor": {...}}; only online data is used.</param>
    /// <param name="runStart">Epoch seconds the current compressor call began, or null.</param>
    /// <returns>The snapshot and the new run start.</returns>
    public static (JsonObject Snapshot, double? RunStart) Compute(CalcConfig cfg, JsonObject nodes, double? runStart,
                                                                double now, RefrigerantTables tables)
    {
        string refr = cfg.Refrigerant;
        JsonObject Data(string name)
        {
            var n = Get(nodes, name);
            return Py.Truthy(Get(n, "online")) ? Obj(Get(n, "data")) : [];
        }
        var od = Data("outdoor");
        var ind = Data("indoor");
        var p = Get(od, "p");
        var t = Get(od, "t");
        var airO = Get(od, "air");
        var airI = Get(ind, "air");
        var om = Obj(Get(od, "mode"));
        var im = Obj(Get(ind, "mode"));

        bool Call(string k)
        {
            if (om.TryGetPropertyValue(k, out var v)) return Py.Truthy(v);
            if (im.TryGetPropertyValue(k, out var w)) return Py.Truthy(w);
            return false;
        }
        bool y = Call("Y"), w = Call("W"), g = Call("G"), ob = Call("OB");

        bool heatingRv = cfg.HeatPump && (ob == (cfg.ObEnergized == "heat"));
        string mode = y && heatingRv ? "heating" : y ? "cooling" : w ? "heat_aux" : g ? "fan" : "idle";
        bool running = mode is "cooling" or "heating";

        if (running) runStart ??= now;
        else runStart = null;
        Num runMin = runStart is double rs && rs != 0 ? Num.Float(Py.Round((now - rs) / 60, 1)) : new Num(0, true);

        double atm = cfg.AtmPsia;
        double? Abs(Num? gauge) => gauge is Num n ? n.V + atm : null;
        Num? pLiq = NumOf(p, "liq"), pVap = NumOf(p, "vap"), pTsuc = NumOf(p, "tsuc");
        Num? tSuc = NumOf(t, "suc"), tLiq = NumOf(t, "liq"), tTsuc = NumOf(t, "tsuc"), tDis = NumOf(t, "dis");
        Num? oat = NumOf(airO, "t"), orh = NumOf(airO, "rh");
        Num? tSup = NumOf(airI, "supply"), tRet = NumOf(airI, "return");

        var notes = new JsonArray();
        Num? lowP, lowT, highP;
        if (mode == "heating")
        {
            lowP = pTsuc;
            lowT = tTsuc;
            highP = pVap ?? pLiq;
            if (pTsuc is null) notes.Add("Heating-mode superheat needs the true-suction transducer and thermistor.");
        }
        else
        {
            lowP = pVap;
            lowT = tSuc;
            highP = pLiq;
        }

        Num? satLow = R1(tables.DewF(refr, Abs(lowP)));
        Num? satHigh = R1(tables.BubbleF(refr, Abs(highP)));
        double? satLiq = tables.BubbleF(refr, Abs(pLiq));
        Num? sh = null, sc = null, ctoa = null, approach = null;
        if (running)
        {
            if (satLow is Num sl && lowT is Num lt) sh = R1(lt - sl);
            if (satLiq is double sq && tLiq is Num tl) sc = R1(Num.Float(sq) - tl);
            if (mode == "cooling" && oat is Num o)
            {
                if (satHigh is Num shi) ctoa = R1(shi - o);
                if (tLiq is Num tl2) approach = R1(tl2 - o);
            }
        }
        Num? dt = tRet is Num r && tSup is Num s0 ? R1(r - s0) : null;

        var snap = new JsonObject
        {
            ["mode"] = mode, ["run_min"] = runMin.ToNode(), ["Y"] = y, ["W"] = w, ["G"] = g, ["OB"] = ob,
            ["p_liq"] = Node(pLiq), ["p_vap"] = Node(pVap), ["p_tsuc"] = Node(pTsuc),
            ["t_suc"] = Node(tSuc), ["t_liq"] = Node(tLiq), ["t_tsuc"] = Node(tTsuc), ["t_dis"] = Node(tDis),
            ["oat"] = Node(oat), ["orh"] = Node(orh), ["t_sup"] = Node(tSup), ["t_ret"] = Node(tRet),
            ["dt"] = Node(dt),
            ["p_low"] = Node(lowP), ["p_high"] = Node(highP), ["sat_low"] = Node(satLow), ["sat_high"] = Node(satHigh),
            ["sh"] = Node(sh), ["sc"] = Node(sc), ["ctoa"] = Node(ctoa), ["approach"] = Node(approach),
            ["notes"] = notes,
        };
        snap["flags"] = Flags(mode, runMin, g, sh, sc, dt, ctoa, nodes);
        return (snap, runStart);
    }

    static JsonObject Flag(string level, string code, string text, string? node = null) => J.Flag(level, code, text, node);

    static string Title(string s) => s.Length == 0 ? s : char.ToUpperInvariant(s[0]) + s[1..].ToLowerInvariant();

    static string JoinErrors(JsonNode? err) => J.JoinErrors(err);

    /// <summary>Conservative first-pass checks. Only judged after 10 minutes of steady running.</summary>
    static JsonArray Flags(string mode, Num runMin, bool g, Num? sh, Num? sc, Num? dt, Num? ctoa, JsonObject nodes)
    {
        var f = new JsonArray();
        foreach (var node in Nodes)
        {
            var n = Get(nodes, node);
            var err = Get(Get(n, "data"), "err");
            if (!Py.Truthy(Get(n, "online")))
                f.Add(Flag("warn", "node_offline", $"{Title(node)} node is offline", node));
            else if (Py.Truthy(err))
                f.Add(Flag("warn", "sensor_issue", $"{Title(node)} sensor issue: {JoinErrors(err)}", node));
        }
        if (mode is not ("cooling" or "heating") || runMin.V < 10) return f;
        if (sh is Num a && a.V < 3) f.Add(Flag("alert", "sh_low", $"Superheat {a}°F: risk of liquid floodback"));
        if (sh is Num b && b.V > 30) f.Add(Flag("warn", "sh_high", $"Superheat {b}°F is high: possible undercharge or restriction"));
        if (sc is Num c && c.V < 3) f.Add(Flag("warn", "sc_low", $"Subcooling {c}°F is low: possible undercharge"));
        if (sc is Num d && d.V > 20) f.Add(Flag("warn", "sc_high", $"Subcooling {d}°F is high: possible overcharge or restriction"));
        if (mode == "cooling")
        {
            if (dt is Num e && g && e.V < 12) f.Add(Flag("warn", "dt_low", $"Air delta-T {e}°F is low"));
            if (ctoa is Num h && h.V > 35) f.Add(Flag("warn", "ctoa_high", $"Condensing {h}°F over ambient: check condenser coil and fan"));
        }
        return f;
    }
}
