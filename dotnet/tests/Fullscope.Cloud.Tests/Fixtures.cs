using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;

namespace Fullscope.Cloud.Tests;

/// <summary>conftest.py: an in-memory database with two accounts; account 1 owns system "home"
/// (R-410A, 14.0 psia), account 2 owns "other".</summary>
public sealed class Seeded
{
    public DbFactory Sessions { get; } = DbFactory.InMemory().Init();
    public static readonly RefrigerantTables Tables = RefrigerantTables.Load();
    public string Key1 { get; }
    public string Key2 { get; }

    public Seeded()
    {
        using var s = Sessions.Open();
        var a1 = new Account { Name = "Tyler", Email = "t@example.com" };
        var a2 = new Account { Name = "Other", Email = "o@example.com" };
        s.Accounts.AddRange(a1, a2);
        s.SaveChanges();
        s.Systems.AddRange(new HvacSystem { AccountId = a1.Id, Name = "Home", SiteId = "home", AtmPsia = 14.0 },
                           new HvacSystem { AccountId = a2.Id, Name = "Other", SiteId = "other" });
        var (k1, key1) = ApiKey.New(a1.Id);
        var (k2, key2) = ApiKey.New(a2.Id);
        s.ApiKeys.AddRange(k1, k2);
        s.SaveChanges();
        Key1 = key1;
        Key2 = key2;
    }

    public int HomeId
    {
        get
        {
            using var s = Sessions.Open();
            return s.Systems.Single(x => x.SiteId == "home").Id;
        }
    }
}

/// <summary>test_calc.py's readings.</summary>
public static class Readings
{
    public static JsonObject CoolOut() => JsonNode.Parse("""
        {"mode": {"Y": true, "OB": true}, "p": {"liq": 360.0, "vap": 120.0, "tsuc": null},
         "t": {"suc": 52.0, "liq": 101.0, "tsuc": null, "dis": null}, "air": {"t": 95.0, "rh": 12.0}, "err": []}
        """)!.AsObject();

    public static JsonObject CoolIn() => JsonNode.Parse("""
        {"mode": {"Y": true, "W": false, "G": true, "OB": true}, "air": {"supply": 57.0, "return": 76.0, "dt": null}, "err": []}
        """)!.AsObject();

    public static JsonObject With(this JsonObject o, string key, JsonNode? value)
    {
        var c = (JsonObject)o.DeepClone();
        c[key] = value;
        return c;
    }
}
