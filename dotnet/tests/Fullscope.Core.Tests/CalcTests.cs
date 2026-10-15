using System.Text.Json.Nodes;
using Fullscope.Core;

namespace Fullscope.Core.Tests;

public class CalcTests
{
    static readonly RefrigerantTables Tables = RefrigerantTables.Load();

    static readonly Lazy<JsonArray> Cases = new(() =>
        JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "vectors", "calc_vectors.json")))!.AsArray());

    public static IEnumerable<object[]> Vectors() => Enumerable.Range(0, Cases.Value.Count).Select(i => new object[] { i });

    [Theory]
    [MemberData(nameof(Vectors))]
    public void MatchesPython(int index)
    {
        var c = Cases.Value[index]!;
        var cfgNode = c["cfg"]!;
        var cfg = new CalcConfig((string)cfgNode["refrigerant"]!, (bool)cfgNode["heat_pump"]!,
                                 (string)cfgNode["ob_energized"]!, (double)cfgNode["atm_psia"]!);
        var nodes = c["nodes"]!.AsObject();
        double? runStart = c["run_start"] is null ? null : (double)c["run_start"]!;
        var (snap, after) = Calc.Compute(cfg, nodes, runStart, (double)c["now"]!, Tables);
        Assert.Equal((string)c["expected"]!, Py.Dumps(snap, sortKeys: true));
        Assert.Equal(c["run_start_after"] is null ? null : (double)c["run_start_after"]!, after);
    }

    [Fact]
    public void CoolingValues()
    {
        // 120 psig suction at 14.0 psia is about 41 F dew on R-410A (same check as test_calc.py)
        var nodes = JsonNode.Parse("""
            {"outdoor": {"online": true, "data": {"mode": {"Y": true, "OB": true}, "p": {"liq": 360.0, "vap": 120.0, "tsuc": null},
                         "t": {"suc": 52.0, "liq": 101.0, "tsuc": null, "dis": null}, "air": {"t": 95.0, "rh": 12.0}, "err": []}},
             "indoor": {"online": true, "data": {"mode": {"Y": true, "W": false, "G": true, "OB": true},
                        "air": {"supply": 57.0, "return": 76.0, "dt": null}, "err": []}}}
            """)!.AsObject();
        double now = 1_791_200_000;
        var (s, _) = Calc.Compute(new CalcConfig("R-410A", true, "cool", 14.0), nodes, now - 900, now, Tables);
        Assert.Equal("cooling", (string)s["mode"]!);
        Assert.InRange((double)s["sat_low"]!, 40, 43);
        Assert.InRange((double)s["sh"]!, 10, 12);
        Assert.Equal(19.0, (double)s["dt"]!);
        Assert.Empty(s["flags"]!.AsArray());
    }

    [Fact]
    public void AllSixRefrigerantsLoad() => Assert.Equal(RefrigerantTables.Fluids, Tables.Available());
}

public class PyTests
{
    [Theory]
    [InlineData(0.1, "0.1")]
    [InlineData(19.0, "19.0")]
    [InlineData(-0.0, "-0.0")]
    [InlineData(1e16, "1e+16")]
    [InlineData(1234567890123456.0, "1234567890123456.0")]
    [InlineData(0.0001, "0.0001")]
    [InlineData(0.00001, "1e-05")]
    [InlineData(1.5e-7, "1.5e-07")]
    [InlineData(123.456, "123.456")]
    public void Repr(double x, string expected) => Assert.Equal(expected, Py.Repr(x));

    [Theory]
    [InlineData(0.25, 0.2)]     // exact half rounds to even
    [InlineData(0.35, 0.3)]     // 0.35 is stored just below .35
    [InlineData(2.675, 2.7)]
    [InlineData(-1.25, -1.2)]
    [InlineData(11.349999, 11.3)]
    public void Round(double x, double expected) => Assert.Equal(expected, Py.Round(x, 1));

    [Fact]
    public void DumpsLikePython() =>
        Assert.Equal("{\"a\": [1, 2.0, true, null], \"b\": \"\\u00b0F\"}",
                     Py.Dumps(JsonNode.Parse("{\"b\": \"°F\", \"a\": [1, 2.0, true, null]}"), sortKeys: true));
}
