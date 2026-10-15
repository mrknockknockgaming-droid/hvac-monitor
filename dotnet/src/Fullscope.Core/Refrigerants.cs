using System.Text.Json;

namespace Fullscope.Core;

/// <summary>
/// Saturation (pressure-temperature) tables for common residential refrigerants, from the same
/// refrigerant_tables.json the Python code builds with CoolProp (port of hvaccloud/refrigerants.py).
/// Blends with temperature glide have separate bubble (liquid) and dew (vapor) curves:
/// superheat uses the DEW point of the suction pressure, subcooling the BUBBLE point of the liquid pressure.
/// </summary>
public sealed class RefrigerantTables
{
    /// <summary>Supported refrigerants in display order.</summary>
    public static readonly string[] Fluids = ["R-410A", "R-32", "R-454B", "R-22", "R-134a", "R-407C"];

    // name -> rows of (psia, bubble °F, dew °F), psia ascending
    readonly Dictionary<string, double[][]> _data;
    readonly Dictionary<string, double[]> _psia;

    public RefrigerantTables(Dictionary<string, double[][]> data)
    {
        _data = data;
        _psia = data.ToDictionary(kv => kv.Key, kv => kv.Value.Select(r => r[0]).ToArray());
    }

    /// <summary>Tables embedded in this assembly.</summary>
    public static RefrigerantTables Load()
    {
        using var s = typeof(RefrigerantTables).Assembly.GetManifestResourceStream("refrigerant_tables.json")
                      ?? throw new InvalidOperationException("refrigerant_tables.json is not embedded");
        return FromJson(s);
    }

    public static RefrigerantTables FromJson(Stream json) =>
        new(JsonSerializer.Deserialize<Dictionary<string, double[][]>>(json)
            ?? throw new InvalidDataException("empty refrigerant tables"));

    public IReadOnlyList<string> Available() => Fluids.Where(_data.ContainsKey).ToList();

    double? Interp(string name, double? psia, int col)
    {
        if (psia is not double p || !_data.TryGetValue(name, out var rows) || rows.Length == 0) return null;
        var ps = _psia[name];
        if (p < ps[0] || p > ps[^1]) return null;
        int i = BisectLeft(ps, p);
        if (i == 0) return rows[0][col];
        double p0 = rows[i - 1][0], p1 = rows[i][0], t0 = rows[i - 1][col], t1 = rows[i][col];
        return t0 + (t1 - t0) * (p - p0) / (p1 - p0);
    }

    static int BisectLeft(double[] a, double x)
    {
        int lo = 0, hi = a.Length;
        while (lo < hi)
        {
            int mid = (lo + hi) / 2;
            if (a[mid] < x) lo = mid + 1; else hi = mid;
        }
        return lo;
    }

    public double? BubbleF(string name, double? psia) => Interp(name, psia, 1);

    public double? DewF(string name, double? psia) => Interp(name, psia, 2);
}
