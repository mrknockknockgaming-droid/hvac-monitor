using System.Text.Json.Nodes;

namespace Fullscope.Core.Tests;

public class SnapshotTests
{
    const string File = "snapshot_vectors.json";

    public static IEnumerable<object[]> Cases() => Vectors.Indexes(File);

    public static EquipmentInfo? Equip(JsonNode? e) => e is null ? null : new EquipmentInfo
    {
        SystemType = J.Str(e, "system_type"), Metering = J.Str(e, "metering"),
        Tonnage = J.Num(e, "tonnage")?.V, ScTarget = J.Num(e, "sc_target")?.V, ScTolerance = J.Num(e, "sc_tolerance")?.V,
        MaxEsp = J.Num(e, "max_esp")?.V, ElevationFt = J.Num(e, "elevation_ft")?.V, CompRla = J.Num(e, "comp_rla")?.V, CompLra = J.Num(e, "comp_lra")?.V,
        FanFla = J.Num(e, "fan_fla")?.V, CapHermUf = J.Num(e, "cap_herm_uf")?.V, CapFanUf = J.Num(e, "cap_fan_uf")?.V,
        VoltMin = J.Num(e, "volt_min")?.V, VoltMax = J.Num(e, "volt_max")?.V,
    };

    [Theory]
    [MemberData(nameof(Cases))]
    public void MatchesPython(int index)
    {
        var c = Vectors.Load(File)[index]!;
        var snap = c["snap"]!.DeepClone().AsObject();
        var eq = Equip(c["equipment"]);
        snap = Psychro.Apply(snap, c["indoor"]?.DeepClone(), (double)c["atm"]!);
        snap = Equipment.Apply(snap, eq);
        snap = Airflow.Apply(snap, c["indoor"]?.DeepClone(), eq);
        snap = Electrical.Apply(snap, c["electrical"]?.DeepClone() as JsonObject, eq);
        snap = Thermostat.Apply(snap, c["thermostat"]?.DeepClone() as JsonObject);
        Assert.Equal((string)c["expected"]!, Py.Dumps(snap, sortKeys: true));
    }

    [Theory]
    [InlineData(75.0, 50.0, 14.696, 62.6, 55.1, 28.1)]
    [InlineData(55.0, 90.0, 14.696, 53.3, 52.1, 22.2)]
    public void PsychroMatchesTheChart(double t, double rh, double atm, double wb, double dp, double h)
    {
        var p = Psychro.Point(Num.Float(t), Num.Float(rh), atm)!;
        Assert.Equal([wb, dp, h], new[] { (double)p["wb"]!, (double)p["dp"]!, (double)p["h"]! });
        Assert.Null(Psychro.Point(Num.Float(t), Num.Float(0), atm));
    }

    [Theory]
    [InlineData(0, 14.7)]
    [InlineData(1240, 14.05)]
    [InlineData(5280, 12.1)]
    public void AtmFromElevation(double ft, double psia) => Assert.Equal(psia, Equipment.AtmFromElevation(ft));
}

public class ThermostatTests
{
    const string File = "thermostat_vectors.json";

    public static IEnumerable<object[]> Cases() => Vectors.Indexes(File);

    [Theory]
    [MemberData(nameof(Cases))]
    public void MatchesPython(int index)
    {
        var c = Vectors.Load(File)[index]!;
        var settings = c["settings"]!;
        var expected = JsonNode.Parse((string)c["result"]!)!.AsObject();
        if (expected.ContainsKey("crash"))
        {
            Assert.ThrowsAny<Exception>(() => Thermostat.ApplyRequest(settings, c["request"]));
        }
        else
        {
            JsonObject got;
            try { got = new JsonObject { ["ok"] = Thermostat.ApplyRequest(settings, c["request"]) }; }
            catch (ThermostatSettingsException e) { got = new JsonObject { ["error"] = e.Message }; }
            Assert.Equal((string)c["result"]!, Py.Dumps(got, sortKeys: true));
        }

        if (c["validate"] is JsonArray verrs)
            Assert.Equal(verrs.Select(x => (string)x!), Thermostat.Validate(settings));
        else
            Assert.ThrowsAny<Exception>(() => Thermostat.Validate(settings));

        var when = Thermostat.ParseIsoLocal((string)c["when"]!)!.Value;
        JsonNode? sched = null;
        if (JsonNode.Parse((string)c["schedule"]!) is JsonObject expectedSched)
        {
            var (heat, cool, source, next) = Thermostat.ScheduleNow(settings, when);
            sched = new JsonObject
            {
                ["heat"] = heat, ["cool"] = cool, ["source"] = source, ["next"] = next,
                ["hold_next"] = Thermostat.HoldUntilNext(settings, when, J.Copy(c["hold_heat"])),
            };
            Assert.Equal((string)c["schedule"]!, Py.Dumps(sched, sortKeys: true));
        }

        Assert.Equal(c["validate_tech"]!.AsArray().Select(x => (string)x!), Thermostat.ValidateTech(c["tech"]!.AsObject()));
    }
}
