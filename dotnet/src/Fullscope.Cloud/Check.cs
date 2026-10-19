using System.Globalization;
using Fullscope.Cloud.Data;
using Fullscope.Cloud.Ingest;
using Fullscope.Core;

namespace Fullscope.Cloud;

/// <summary>
/// `fullscope manage check` (port of hvaccloud/check.py): one line per finding, OK / WARN / FAIL, from what
/// the database shows: nodes heard from, fresh readings and history, a compressor call seen lately, alerts
/// and how they went out, remote commands answered, email and notification setup, backups, the HTTPS cookie.
/// </summary>
public static class Check
{
    const double NodeLateS = 120, ReadingLateS = 300, BackupLateH = 36;
    const int CallDays = 7;

    static string F(double v, string fmt) => v.ToString(fmt, CultureInfo.InvariantCulture);

    public static string Ago(double? seconds) => seconds switch
    {
        null => "never",
        < 90 => $"{F(seconds.Value, "F0")} s ago",
        < 5400 => $"{F(seconds.Value / 60, "F0")} min ago",
        < 2 * 86400 => $"{F(seconds.Value / 3600, "F1")} h ago",
        _ => $"{F(seconds.Value / 86400, "F0")} days ago",
    };

    static double? Age(DateTime? t, double now) => t is DateTime d ? now - Time.ToEpoch(d) : null;

    public static List<(string Level, string Text)> Run(Db s, Settings settings, double? now = null, bool? emailOn = null)
    {
        double t = now ?? Time.Now();
        var outList = new List<(string, string)>();
        var systems = s.Systems.OrderBy(x => x.Id).ToList();
        if (systems.Count == 0) outList.Add(("FAIL", "no systems registered (fullscope manage create-system)"));
        var dayAgo = Time.FromEpoch(t - 86400);
        var callsSince = Time.FromEpoch(t - CallDays * 86400);
        foreach (var sy in systems)
        {
            var tag = $"{sy.Name} (site {sy.SiteId})";
            var devices = s.Devices.Where(d => d.SystemId == sy.Id).OrderBy(d => d.Node).ToList();
            if (devices.Count == 0) outList.Add(("FAIL", $"{tag}: no node has reported yet"));
            foreach (var d in devices)
            {
                var a = Age(d.LastSeen, t);
                outList.Add((a is double x && x <= NodeLateS ? "OK" : "FAIL",
                             $"{tag}: {d.Node} node last heard {Ago(a)}" + (d.Fw is { Length: > 0 } ? $", firmware {d.Fw}" : "") +
                             (d.Rssi is int r ? $", WiFi {r} dBm" : "")));
            }
            var snaps = s.Snapshots.Where(x => x.SystemId == sy.Id);
            var latest = snaps.Max(x => (DateTime?)x.Time);
            var oldest = snaps.Min(x => (DateTime?)x.Time);
            int day = snaps.Count(x => x.Time >= dayAgo);
            var la = Age(latest, t);
            outList.Add((la is double l && l <= ReadingLateS ? "OK" : "FAIL",
                         $"{tag}: newest reading {Ago(la)}, {day} in the last 24 h, history since " +
                         (oldest is DateTime o ? o.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) : "-")));
            int calls = snaps.Count(x => (x.Mode == "cooling" || x.Mode == "heating") && x.Time >= callsSince);
            outList.Add(calls > 0
                ? ("OK", $"{tag}: compressor running in {calls} readings over {CallDays} days")
                : ("WARN", $"{tag}: no cooling or heating call seen in {CallDays} days; superheat, subcooling and delta-T only " +
                           "run during a call, so check the Y (and O/B) wires if the system has run"));
            var open = s.Alerts.Where(x => x.SystemId == sy.Id && x.RaisedAt != null && x.ClearedAt == null).OrderBy(x => x.RaisedAt).ToList();
            outList.Add((open.Count == 0 ? "OK" : "WARN", $"{tag}: {open.Count} open alert(s)" +
                         string.Concat(open.Take(3).Select(x => $"; {x.Code} since {Ago(Age(x.RaisedAt, t))}"))));
            var last = s.Alerts.Where(x => x.SystemId == sy.Id && x.RaisedAt != null).OrderByDescending(x => x.RaisedAt).FirstOrDefault();
            if (last is not null)
            {
                var how = new[] { ("emailed", last.EmailedAt), ("sent to phones", last.PushedAt) }.Where(x => x.Item2 is not null).Select(x => x.Item1).ToList();
                outList.Add((how.Count > 0 || emailOn != true ? "OK" : "WARN",
                             $"{tag}: last alert {last.Code} raised {Ago(Age(last.RaisedAt, t))}, " +
                             (how.Count > 0 ? string.Join(" and ", how) : "not emailed or sent to phones")));
            }
            var ids = devices.Select(d => d.Id).ToList();
            var cmd = s.Commands.Where(c => ids.Contains(c.DeviceId)).OrderByDescending(c => c.CreatedAt).Take(20).AsEnumerable()
                       .FirstOrDefault(c => !string.IsNullOrEmpty(c.Payload) && c.Payload != "null");   // not replies nobody asked for
            if (cmd is null)
                outList.Add(("WARN", $"{tag}: no command sent to a node yet; press Refresh on Sensors & calibration to test it"));
            else
            {
                var node = devices.First(d => d.Id == cmd.DeviceId).Node;
                var ca = Age(cmd.CreatedAt, t);
                if (cmd.RepliedAt is not null) outList.Add(("OK", $"{tag}: last command to the {node} node ({Ago(ca)}) was answered"));
                else outList.Add((ca is double c && c < 60 ? "WARN" : "FAIL",
                                  $"{tag}: last command to the {node} node ({Ago(ca)}) has no reply" + (cmd.Sent ? "" : "; it never reached the broker")));
            }
        }
        if (emailOn is bool on) outList.Add((on ? "OK" : "WARN", on ? "alert email is on" : "alert email is off (SMTP_* not set)"));
        bool keys = s.ServerKeys.Any(k => k.Name == "vapid_public");
        int subs = s.PushSubscriptions.Count();
        outList.Add(("OK", keys ? $"phone notifications: {subs} device(s) turned on" : "phone notifications: not set up yet (turn them on from the Account page)"));
        int users = s.Users.Count(), tfa = s.Users.Count(u => u.TotpOn != null);
        outList.Add((tfa > 0 || users == 0 ? "OK" : "WARN", $"two-step sign-in on for {tfa} of {users} user(s)"));
        if (Db.SqlitePath(settings.DatabaseUrl) is not null)
        {
            var files = Maintenance.Backups(settings.BackupDir);
            if (files.Count == 0) outList.Add(("WARN", $"no nightly database backup in {settings.BackupDir} yet"));
            else
            {
                double b = t - Time.ToEpoch(File.GetLastWriteTimeUtc(files[^1]));
                outList.Add((b <= BackupLateH * 3600 ? "OK" : "WARN", $"newest nightly database backup {Ago(b)} ({files.Count} kept)"));
            }
        }
        if (settings.AppUrl.StartsWith("https://", StringComparison.Ordinal) && !settings.CookieSecure)
            outList.Add(("FAIL", "APP_URL is https but COOKIE_SECURE is off"));
        return outList;
    }
}
