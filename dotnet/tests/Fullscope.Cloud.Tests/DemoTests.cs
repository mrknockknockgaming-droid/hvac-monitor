using Fullscope.Cloud.Data;
using Fullscope.Cloud.Tools;

namespace Fullscope.Cloud.Tests;

public class DemoTests
{
    [Fact]
    public void SetupThenResetLeavesOtherAccountsAlone()
    {
        var home = Path.Combine(Path.GetTempPath(), $"fullscope-demo-{Guid.NewGuid():N}");
        Directory.CreateDirectory(home);
        var dbPath = Path.Combine(home, "dev.db");
        try
        {
            var sessions = DbFactory.FromUrl("sqlite:///" + dbPath).Init();
            using (var s = sessions.Open())
            {
                var acct = new Account { Name = "Tyler", Email = "t@example.com" };
                s.Accounts.Add(acct);
                s.SaveChanges();
                s.Systems.Add(new HvacSystem { AccountId = acct.Id, Name = "Home", SiteId = "home" });
                s.SaveChanges();
            }
            var demo = new Demo(sessions, new Settings { Home = home }, new Random(1));
            var lines = new List<string>();
            demo.Setup(backfillHours: 2, say: lines.Add);
            Assert.True(File.Exists(demo.LoginFile));
            using (var s = sessions.Open())
            {
                var acct = s.Accounts.Single(a => a.Email == Demo.AccountEmail);
                var ids = s.Systems.Where(x => x.AccountId == acct.Id).Select(x => x.Id).ToList();
                Assert.Equal(Demo.Homes.Length, ids.Count);
                Assert.True(s.Snapshots.Count(x => ids.Contains(x.SystemId)) > 7 * 100);
                var raised = s.Alerts.Where(a => ids.Contains(a.SystemId) && a.RaisedAt != null && a.ClearedAt == null).Select(a => a.Code).ToList();
                Assert.Contains("dt_low", raised);      // Thompson's dirty filter
                Assert.Contains("sh_low", raised);      // Brooks's floodback
                Assert.Contains("cap_herm", raised);    // Rivera's weak capacitor
                Assert.Equal(2, s.ThermostatConfigs.Count());
                Assert.Equal(2, s.Users.Count());
            }
            Assert.Contains("already exists", Assert.Throws<Demo.DemoExit>(() => demo.Setup(1, _ => { })).Message);

            demo.Reset(lines.Add);
            Assert.False(File.Exists(demo.LoginFile));
            using (var s = sessions.Open())
            {
                Assert.Equal("Tyler", s.Accounts.Single().Name);
                Assert.Equal("home", s.Systems.Single().SiteId);
                Assert.Empty(s.Users);
                Assert.Empty(s.Snapshots);
                Assert.Empty(s.Devices);
                Assert.Empty(s.SnapshotMinutes);
            }
        }
        finally
        {
            Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
            Directory.Delete(home, true);
        }
    }
}
