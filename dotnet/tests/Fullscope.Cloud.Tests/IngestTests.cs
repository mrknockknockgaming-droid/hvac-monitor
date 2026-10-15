using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;

namespace Fullscope.Cloud.Tests;

/// <summary>test_ingest.py and test_reading_time.py.</summary>
public class IngestTests
{
    const double T0 = 1_790_000_000.0;

    static JsonNode? Msg(Ingest.Ingest i, string site, string node, string kind, JsonNode data, double now) =>
        i.Handle($"hvac/{site}/{node}/{kind}", Py.Dumps(data), now);

    [Fact]
    public void UnknownSiteAndBadTopicsIgnored()
    {
        var db = new Seeded();
        var i = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        Assert.Null(Msg(i, "nobody", "outdoor", "telemetry", Readings.CoolOut(), T0));
        Assert.Null(i.Handle("hvac/home/attic/telemetry", "{}", T0));
        Assert.Null(i.Handle("hvac/home/outdoor/telemetry", "not json", T0));
        using var s = db.Sessions.Open();
        Assert.Equal(0, s.Telemetry.Count());
    }

    [Fact]
    public void TelemetryStoresAndDerives()
    {
        var db = new Seeded();
        var i = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        Msg(i, "home", "indoor", "telemetry", Readings.CoolIn(), T0);
        var snap = Msg(i, "home", "outdoor", "telemetry", Readings.CoolOut(), T0 + 1)!;
        Assert.Equal("cooling", (string)snap["mode"]!);
        Assert.NotNull(snap["sh"]);
        Assert.Equal(19.0, (double)snap["dt"]!);
        using var s = db.Sessions.Open();
        Assert.Equal(["indoor", "outdoor"], s.Devices.Select(d => d.Node).Order().ToList());
        Assert.Equal(2, s.Snapshots.Count());
        Assert.NotNull(s.Systems.Single(x => x.SiteId == "home").RunStartedAt);
    }

    [Fact]
    public void RunTimeSurvivesRestartAndStaleNodesDrop()
    {
        var db = new Seeded();
        var first = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        Msg(first, "home", "indoor", "telemetry", Readings.CoolIn(), T0);
        for (int k = 0; k < 66; k++)                                  // outdoor keeps reporting for 11 min; indoor goes quiet
            Msg(first, "home", "outdoor", "telemetry", Readings.CoolOut(), T0 + 1 + k * 10);
        var fresh = new Ingest.Ingest(db.Sessions, Seeded.Tables);    // restarted worker, empty cache
        var snap = Msg(fresh, "home", "outdoor", "telemetry", Readings.CoolOut(), T0 + 11 * 60)!;
        Assert.True((double)snap["run_min"]! >= 11);                   // run start came from the database
        Assert.Contains(snap["flags"]!.AsArray(), f => ((string)f!["text"]!).Contains("Indoor node is offline"));
    }

    [Fact]
    public void RunTimeRestartsAfterAnOutage()
    {
        var db = new Seeded();
        var i = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        Msg(i, "home", "indoor", "telemetry", Readings.CoolIn(), T0);
        Msg(i, "home", "outdoor", "telemetry", Readings.CoolOut(), T0 + 1);
        var later = T0 + 3 * 86400;                                   // nodes back after 3 days, still cooling
        Msg(i, "home", "indoor", "telemetry", Readings.CoolIn(), later);
        var snap = Msg(i, "home", "outdoor", "telemetry", Readings.CoolOut(), later + 1)!;
        Assert.True((double)snap["run_min"]! < 1);                     // a new cycle, not a 3-day one
        Assert.DoesNotContain(snap["flags"]!.AsArray(), f => (string)f!["code"]! is "dt_low" or "sh_low" or "sh_high");
    }

    [Fact]
    public void StatusAndWill()
    {
        var db = new Seeded();
        var i = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        Msg(i, "home", "outdoor", "status", JsonNode.Parse("""{"online": true, "fw": "0.1.0", "ip": "192.168.1.247", "rssi": -59}""")!, T0);
        Msg(i, "home", "outdoor", "status", JsonNode.Parse("""{"online": false}""")!, T0 + 5);     // last will
        using var s = db.Sessions.Open();
        var d = s.Devices.Single();
        Assert.Equal("0.1.0", d.Fw);
        Assert.Equal("192.168.1.247", d.Ip);
        Assert.False(d.Connected);
    }

    [Fact]
    public void ReplyPairsWithCommand()
    {
        var db = new Seeded();
        var i = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        Msg(i, "home", "outdoor", "telemetry", Readings.CoolOut(), T0);
        using (var s = db.Sessions.Open())
        {
            var d = s.Devices.Single();
            s.Commands.AddRange(new Command { DeviceId = d.Id, Payload = """{"cmd": "cal_zero", "ch": "p_liq"}""", Sent = true },
                                new Command { DeviceId = d.Id, Payload = """{"cmd": "fitted", "ch": "t_tsuc", "on": true}""", Sent = true });
            s.SaveChanges();
        }
        Msg(i, "home", "outdoor", "reply", JsonNode.Parse("""{"cmd": "fitted", "ok": true}""")!, T0 + 2);
        Msg(i, "home", "outdoor", "reply", JsonNode.Parse("""{"cmd": "restart", "ok": true}""")!, T0 + 3);    // nobody asked
        using var s2 = db.Sessions.Open();
        var rows = s2.Commands.ToList().ToDictionary(c => (string?)J.Get(Db.Parse(c.Payload), "cmd") ?? "");
        Assert.Equal("""{"cmd": "fitted", "ok": true}""", rows["fitted"].Reply);
        Assert.Null(rows["cal_zero"].Reply);
        Assert.Equal("restart", (string)Db.Parse(rows[""].Reply)!["cmd"]!);
    }

    [Fact]
    public void ReplySkipsUnsentAndLostCommands()
    {
        var db = new Seeded();
        var i = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        Msg(i, "home", "outdoor", "telemetry", Readings.CoolOut(), T0);
        using (var s = db.Sessions.Open())
        {
            var d = s.Devices.Single();
            s.Commands.AddRange(new Command { DeviceId = d.Id, Payload = """{"cmd": "cal_zero", "n": 1}""", Sent = false },   // never went out
                                new Command { DeviceId = d.Id, Payload = """{"cmd": "cal_zero", "n": 2}""", Sent = true },    // lost
                                new Command { DeviceId = d.Id, Payload = """{"cmd": "cal_zero", "n": 3}""", Sent = true });
            s.SaveChanges();
        }
        Msg(i, "home", "outdoor", "reply", JsonNode.Parse("""{"cmd": "cal_zero", "ok": true}""")!, T0 + 1);
        using var s2 = db.Sessions.Open();
        Assert.Equal([3], s2.Commands.Where(c => c.Reply != null).AsEnumerable().Select(c => (int)Db.Parse(c.Payload)!["n"]!).ToList());
    }

    [Fact]
    public void PlausibleTimestampsAreUsedAndOddOnesIgnored()
    {
        const double now = T0;
        Assert.Equal(now - 300, Ingest.Ingest.ReadingTime(new JsonObject { ["ts"] = now - 300 }, now));    // kept during an outage
        Assert.Equal(now, Ingest.Ingest.ReadingTime(new JsonObject { ["ts"] = now + 20 }, now));           // clock slightly ahead
        foreach (var bad in new JsonNode?[] { now + 3600, now - 2 * 86400, "1790000000", true, null })
            Assert.Equal(now, Ingest.Ingest.ReadingTime(new JsonObject { ["ts"] = bad }, now));
        Assert.Equal(now, Ingest.Ingest.ReadingTime([], now));                                          // firmware 0.1 sends no ts
    }

    [Fact]
    public void OutageBacklogLandsWhereItBelongs()
    {
        var db = new Seeded();
        var i = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        const double now = T0;
        void Send(string node, JsonObject b, double at, double t) => Msg(i, "home", node, "telemetry", b.With("ts", at), t);
        Send("indoor", Readings.CoolIn(), now - 400, now - 400);       // live before the outage
        Send("outdoor", Readings.CoolOut(), now - 399, now - 399);
        for (int k = 0; k < 60; k++)                                   // 5 min kept on the nodes, all sent at now
        {
            var at = now - 300 + k * 5;
            Send("indoor", Readings.CoolIn(), at, now);
            Send("outdoor", Readings.CoolOut(), at, now);               // same second as indoor: must not collide
        }
        using var s = db.Sessions.Open();
        var times = s.Snapshots.OrderBy(x => x.Time).Select(x => x.Time).AsEnumerable().Select(Db.Epoch).ToList();
        Assert.Equal(122, times.Count);
        Assert.Equal(122, times.Distinct().Count());
        Assert.True(Math.Abs(times[^1] - (now - 5)) < 0.01);
        Assert.Equal(now - 400, times.Min());
        Assert.True(times.Zip(times.Skip(1), (a, b) => b - a).Max() < 100);   // no hole where the outage was
        Assert.All(s.Devices.ToList(), d => Assert.True(Db.Epoch(d.LastSeen!.Value) > now - 6));
    }

    [Fact]
    public void ALateReadingDoesNotMoveLastSeenBack()
    {
        var db = new Seeded();
        var i = new Ingest.Ingest(db.Sessions, Seeded.Tables);
        Msg(i, "home", "outdoor", "telemetry", Readings.CoolOut().With("ts", T0), T0);
        Msg(i, "home", "outdoor", "telemetry", Readings.CoolOut().With("ts", T0 - 200), T0 + 1);   // an old one arriving late
        using var s = db.Sessions.Open();
        Assert.Equal(T0, Db.Epoch(s.Devices.Single().LastSeen!.Value));
    }
}
