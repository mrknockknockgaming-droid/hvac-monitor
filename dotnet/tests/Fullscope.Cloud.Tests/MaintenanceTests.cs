using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Cloud.Ingest;
using Fullscope.Core;
using Microsoft.Data.Sqlite;

namespace Fullscope.Cloud.Tests;

public class MaintenanceTests
{
    const double Now = 1_790_000_000.0;
    const double Day = 86400;

    static void AddMinute(Db s, int systemId, double start, int n = 12, double b = 10.0)
    {
        for (int i = 0; i < n; i++)
            s.Snapshots.Add(new Snapshot
            {
                SystemId = systemId, Time = Db.Ts(start + i * 5), Mode = "cooling",
                Data = Db.Json(new JsonObject { ["mode"] = "cooling", ["sh"] = b + i, ["Y"] = true, ["flags"] = new JsonArray(), ["t_sup"] = null }),
            });
    }

    static List<(DateTime Time, JsonNode? Data)> Snaps(DbFactory f, double lo, double hi)
    {
        using var s = f.Open();
        DateTime a = Db.Ts(lo), b = Db.Ts(hi);
        return s.Snapshots.Where(x => x.Time >= a && x.Time < b).OrderBy(x => x.Time).AsEnumerable()
                .Select(x => (x.Time, Db.Parse(x.Data))).ToList();
    }

    static double MinuteFloor(double t) => Math.Floor(t / 60) * 60;

    static int Seed(Seeded db)
    {
        using var s = db.Sessions.Open();
        var sid = s.Systems.Single(x => x.SiteId == "home").Id;
        var old = MinuteFloor(Now - 40 * Day);
        AddMinute(s, sid, old);
        AddMinute(s, sid, old + 60, b: 20.0);
        AddMinute(s, sid, MinuteFloor(Now - 1 * Day));                        // recent: untouched
        s.Snapshots.Add(new Snapshot { SystemId = sid, Time = Db.Ts(Now - 400 * Day), Mode = "idle", Data = "{\"mode\": \"idle\"}" });
        var d = new Device { SystemId = sid, Node = "outdoor" };
        s.Devices.Add(d);
        s.SaveChanges();
        for (int k = 0; k < 5; k++)                                           // all raw messages are old
            s.Telemetry.Add(new Telemetry { DeviceId = d.Id, Time = Db.Ts(Now - 50 * Day + k * 5), Data = $"{{\"k\": {k}}}" });
        s.SaveChanges();
        return sid;
    }

    [Fact]
    public void PruneThinsOldMinutesAndKeepsRecentDetail()
    {
        var db = new Seeded();
        Seed(db);
        var stats = Maintenance.Prune(db.Sessions, 30, 365, Now);
        var old = Snaps(db.Sessions, Now - 41 * Day, Now - 39 * Day);
        Assert.Equal(2, old.Count);                                           // one row per minute
        Assert.Equal(15.5, (double)old[0].Data!["sh"]!);                      // averages of 10..21 and 20..31
        Assert.Equal(25.5, (double)old[1].Data!["sh"]!);
        Assert.True((bool)old[0].Data!["Y"]!);
        Assert.Null(old[0].Data!["t_sup"]);
        Assert.Empty(old[0].Data!["flags"]!.AsArray());
        Assert.Equal(12, Snaps(db.Sessions, Now - 2 * Day, Now).Count);       // last 30 days at full detail
        Assert.Empty(Snaps(db.Sessions, Now - 401 * Day, Now - 399 * Day));   // past KEEP_DAYS
        using (var s = db.Sessions.Open())
            Assert.Equal(["{\"k\": 4}"], s.Telemetry.Select(t => t.Data!).ToList());   // newest message per node survives
        Assert.Equal(new Maintenance.PruneStats(22, 4, 1), stats);
    }

    [Fact]
    public void PruneAgainIsANoOp()
    {
        var db = new Seeded();
        Seed(db);
        Maintenance.Prune(db.Sessions, 30, 365, Now);
        Assert.Equal(new Maintenance.PruneStats(0, 0, 0), Maintenance.Prune(db.Sessions, 30, 365, Now + 60));
    }

    [Fact]
    public void PruneCrossesDaysWithoutData()
    {
        var db = new Seeded();
        var sid = Seed(db);
        using (var s = db.Sessions.Open())
        {
            AddMinute(s, sid, MinuteFloor(Now - 33 * Day), b: 50.0);         // days 34..39 have no data
            s.SaveChanges();
        }
        Maintenance.Prune(db.Sessions, 30, 365, Now);
        Assert.Single(Snaps(db.Sessions, Now - 34 * Day, Now - 32 * Day));
        Assert.Equal(2, Snaps(db.Sessions, Now - 41 * Day, Now - 39 * Day).Count);
    }

    [Fact]
    public void BackupIsReadableAndRotates()
    {
        var dir = Directory.CreateTempSubdirectory("fullscope-backup").FullName;
        try
        {
            var url = "sqlite:///" + Path.Combine(dir, "dev.db").Replace('\\', '/');
            var f = DbFactory.FromUrl(url).Init();
            using (var s = f.Open())
            {
                var acct = new Account { Name = "T", Email = "t@example.com" };
                s.Accounts.Add(acct);
                s.SaveChanges();
                s.Systems.Add(new HvacSystem { AccountId = acct.Id, Name = "Home", SiteId = "home" });
                s.SaveChanges();
            }
            var outDir = Path.Combine(dir, "backups");
            var paths = Enumerable.Range(0, 3).Select(k => Maintenance.Backup(url, outDir, 2, now: Now + k * Day)!).ToList();
            Assert.Equal(paths.Skip(1).Select(Path.GetFileName).Order(), Directory.GetFiles(outDir).Select(Path.GetFileName).Order());
            using var c = new SqliteConnection($"Data Source={paths[^1]};Pooling=False");
            c.Open();
            using var cmd = c.CreateCommand();
            cmd.CommandText = "select site_id from systems";
            Assert.Equal("home", cmd.ExecuteScalar());
        }
        finally
        {
            SqliteConnection.ClearAllPools();
            Directory.Delete(dir, true);
        }
    }

    [Fact]
    public void BackupSkipsNonSqlite() => Assert.Null(Maintenance.Backup("postgresql+psycopg://x@db/hvac", Path.GetTempPath(), 7));

    [Fact]
    public void NightlyDue()
    {
        var dir = Directory.CreateTempSubdirectory("fullscope-nightly").FullName;
        try
        {
            double Local(int y, int mo, int d, int h, int mi) => Time.ToEpoch(new DateTime(y, mo, d, h, mi, 0, DateTimeKind.Local).ToUniversalTime());
            Assert.True(Maintenance.NightlyDue(dir, Now));                    // never backed up
            var f = Path.Combine(dir, "dev-20260921-0300.db");
            File.WriteAllBytes(f, []);
            var made = Local(2026, 9, 21, 3, 5);
            File.SetLastWriteTimeUtc(f, Time.FromEpoch(made));
            Assert.False(Maintenance.NightlyDue(dir, made + 600));            // just ran
            Assert.False(Maintenance.NightlyDue(dir, Local(2026, 9, 21, 23, 0)));
            Assert.True(Maintenance.NightlyDue(dir, Local(2026, 9, 22, 3, 1)));   // next night
            Assert.False(Maintenance.NightlyDue(dir, Local(2026, 9, 22, 2, 0)));
            Assert.True(Maintenance.NightlyDue(dir, made + 27 * 3600));       // missed 3 AM: catch up
        }
        finally
        {
            Directory.Delete(dir, true);
        }
    }
}
