using Fullscope.Cloud.Data;
using Fullscope.Core;

namespace Fullscope.Cloud.Tests;

/// <summary>test_check.py</summary>
public class CheckTests
{
    const double T0 = 1_790_000_000.0;

    [Fact]
    public void AHealthyAndAQuietSystem()
    {
        var db = new Seeded();
        using (var s = db.Sessions.Open())
        {
            var home = s.Systems.Single(x => x.SiteId == "home");
            var outd = new Device { SystemId = home.Id, Node = "outdoor", Fw = "0.3.1", Rssi = -60, LastSeen = Db.Ts(T0 - 5) };
            var ind = new Device { SystemId = home.Id, Node = "indoor", LastSeen = Db.Ts(T0 - 3600) };
            s.Devices.AddRange(outd, ind);
            s.SaveChanges();
            for (int i = 0; i < 3; i++) s.Snapshots.Add(new Snapshot { SystemId = home.Id, Time = Db.Ts(T0 - 10 * i), Mode = "cooling", Data = "{}" });
            s.Commands.Add(new Command { DeviceId = outd.Id, Payload = """{"cmd": "status"}""", CreatedAt = Db.Ts(T0 - 600), Sent = true,
                                         Reply = """{"ok": true}""", RepliedAt = Db.Ts(T0 - 599) });
            s.Alerts.Add(new Alert { SystemId = home.Id, Code = "dt_low", Level = "warn", Text = "x", StartedAt = Db.Ts(T0 - 900),
                                     LastSeen = Db.Ts(T0), RaisedAt = Db.Ts(T0 - 600), EmailedAt = Db.Ts(T0 - 590) });
            s.Users.Add(new User { Email = "a@b.com", Role = "contractor", AccountId = home.AccountId });
            s.SaveChanges();
        }
        List<(string Level, string Text)> found;
        using (var s = db.Sessions.Open()) found = Check.Run(s, new Settings { DatabaseUrl = "sqlite://" }, T0, emailOn: true);
        string[] L(string w) => found.Where(f => f.Text.Contains(w)).Select(f => f.Level).ToArray();
        Assert.Equal(["OK"], L("outdoor node last heard 5 s ago, firmware 0.3.1, WiFi -60 dBm"));
        Assert.Equal(["FAIL"], L("indoor node last heard 60 min ago"));
        Assert.Equal(["OK"], L("Home (site home): newest reading"));
        Assert.Equal(["OK"], L("compressor running in 3 readings"));
        Assert.Equal(["WARN"], L("1 open alert"));
        Assert.Equal(["OK"], L("last alert dt_low raised 10 min ago, emailed"));
        Assert.Equal(["OK"], L("last command to the outdoor node (10 min ago) was answered"));
        Assert.Equal(["WARN"], L("two-step sign-in on for 0 of 1"));
        Assert.Equal(["FAIL"], L("Other (site other): no node has reported yet"));
        Assert.Equal(["WARN"], L("Other (site other): no cooling or heating call"));
    }
}
