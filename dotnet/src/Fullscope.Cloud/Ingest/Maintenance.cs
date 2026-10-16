using System.Globalization;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;

namespace Fullscope.Cloud.Ingest;

/// <summary>
/// Nightly upkeep for the SQLite database: backup, then prune (port of hvaccloud/maintenance.py).
/// - Backup: a consistent copy of dev.db (SQLite's online backup, safe while ingest writes) into
///   BACKUP_DIR as dev-YYYYMMDD-HHMM.db, keeping the newest BACKUP_KEEP.
/// - Prune: snapshots older than FULL_DETAIL_DAYS are averaged into one row per minute; raw
///   telemetry older than that is deleted (except each node's newest message); everything older
///   than KEEP_DAYS is deleted. On PostgreSQL TimescaleDB's retention policy does this instead.
/// </summary>
public static class Maintenance
{
    const string BackupPattern = "dev-*.db";
    public const int NightlyHour = 3;           // local time
    const int OverdueHours = 26;                // PC was off at 3 AM: catch up on the next start

    public static List<string> Backups(string backupDir) =>
        Directory.Exists(backupDir) ? Directory.GetFiles(backupDir, BackupPattern).OrderBy(f => f, StringComparer.Ordinal).ToList() : [];

    /// <summary>Copy the SQLite database into backupDir and delete all but the newest `keep` copies.
    /// Returns the new file's path, or null when the database is not a SQLite file.</summary>
    public static string? Backup(string dbUrl, string backupDir, int keep, ILogger? log = null, double? now = null)
    {
        var src = Db.SqlitePath(dbUrl);
        if (src is null)
        {
            log?.LogInformation("backup skipped: not a SQLite file (use pg_dump for PostgreSQL)");
            return null;
        }
        Directory.CreateDirectory(backupDir);
        var local = now is double t ? Time.FromEpoch(t).ToLocalTime() : DateTime.Now;
        var dest = Path.Combine(backupDir, $"dev-{local.ToString("yyyyMMdd-HHmm", CultureInfo.InvariantCulture)}.db");
        using (var from = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = src, DefaultTimeout = 30, Pooling = false }.ToString()))
        using (var to = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = dest, Pooling = false }.ToString()))
        {
            from.Open();
            to.Open();
            from.BackupDatabase(to);
        }
        if (keep > 0)
            foreach (var old in Backups(backupDir).SkipLast(keep)) File.Delete(old);
        log?.LogInformation("backup written: {Path} ({Mb:F1} MB)", dest, new FileInfo(dest).Length / 1e6);
        return dest;
    }

    /// <summary>True at NightlyHour when today's backup hasn't run, or whenever the last one is overdue.</summary>
    public static bool NightlyDue(string backupDir, double? now = null)
    {
        double t = now ?? Time.Now();
        var files = Backups(backupDir);
        if (files.Count == 0) return true;
        double last = Time.ToEpoch(File.GetLastWriteTimeUtc(files[^1]));
        if (t - last >= OverdueHours * 3600) return true;
        var local = Time.FromEpoch(t).ToLocalTime();
        return local.Hour == NightlyHour && Time.FromEpoch(last).ToLocalTime().Date < local.Date;
    }

    /// <summary>One snapshot for a minute: the middle row, with every numeric value replaced by the
    /// minute's average (booleans, mode, flags and notes are taken from that middle row).</summary>
    public static JsonObject AverageRows(List<JsonObject> rows)
    {
        var b = (JsonObject)rows[rows.Count / 2].DeepClone();
        var sums = new Dictionary<string, double>();
        var counts = new Dictionary<string, int>();
        var order = new List<string>();
        foreach (var r in rows)
        {
            foreach (var (k, v) in r)
            {
                if (Py.NumberOf(v) is not Num n) continue;
                if (!sums.ContainsKey(k)) { order.Add(k); sums[k] = 0.0; counts[k] = 0; }
                sums[k] += n.V;
                counts[k]++;
            }
        }
        foreach (var k in order) b[k] = Py.Round(sums[k] / counts[k], 2);
        return b;
    }

    public sealed record PruneStats(int SnapshotsRemoved, int TelemetryRemoved, int Expired);

    /// <summary>Thin and expire old rows, one system-day at a time so each transaction stays short;
    /// `step` runs each piece (the ingest worker passes one that holds its lock).</summary>
    public static PruneStats Prune(DbFactory sessions, int fullDays, int keepDays, double? now = null,
                                   Func<Func<int>, int>? step = null, ILogger? log = null)
    {
        double t = now ?? Time.Now();
        step ??= f => f();
        var detailCut = Db.Ts(Py.FloorDiv(t - fullDays * 86400.0, 60) * 60);    // on a minute boundary
        var keepCut = Db.Ts(t - keepDays * 86400.0);
        step(() =>
        {
            using var s = sessions.Open();                    // the per-minute history rows (C# only)
            return s.SnapshotMinutes.Where(x => x.Minute < keepCut).ExecuteDelete();
        });
        if (!sessions.IsSqlite)
        {
            log?.LogInformation("prune skipped: TimescaleDB retention handles PostgreSQL");
            return new PruneStats(0, 0, 0);
        }
        int expired = step(() =>
        {
            using var s = sessions.Open();
            int n = s.Snapshots.Where(x => x.Time < keepCut).ExecuteDelete();
            n += s.Telemetry.Where(x => x.Time < keepCut).ExecuteDelete();
            return n;
        });
        List<int> systemIds, deviceIds;
        using (var s = sessions.Open())
        {
            systemIds = s.Systems.Select(x => x.Id).ToList();
            deviceIds = s.Devices.Select(x => x.Id).ToList();
        }

        // Newest day first. A day that has data and needed no thinning was done on an earlier night,
        // and so was everything before it, so the nightly run only touches the day that just aged out.
        int snapshotsRemoved = 0;
        foreach (var sid in systemIds)
        {
            DateTime? first;
            using (var s = sessions.Open())
                first = s.Snapshots.Where(x => x.SystemId == sid).OrderBy(x => x.Time).Select(x => (DateTime?)x.Time).FirstOrDefault();
            var end = detailCut;
            while (first is DateTime f && end > f)
            {
                var start = end.AddDays(-1);
                bool hadRows = false;
                int removed = step(() =>
                {
                    var (r, had) = ThinSnapshots(sessions, sid, start, end);
                    hadRows = had;
                    return r;
                });
                snapshotsRemoved += removed;
                if (hadRows && removed == 0) break;
                end = start;
            }
        }

        int telemetryRemoved = 0;
        foreach (var did in deviceIds)
        {
            telemetryRemoved += step(() =>
            {
                using var s = sessions.Open();
                var newest = s.Telemetry.Where(x => x.DeviceId == did).OrderByDescending(x => x.Time).Select(x => (DateTime?)x.Time).FirstOrDefault();
                var q = s.Telemetry.Where(x => x.DeviceId == did && x.Time < detailCut);
                if (newest is DateTime nt) q = q.Where(x => x.Time != nt);
                return q.ExecuteDelete();
            });
        }
        var stats = new PruneStats(snapshotsRemoved, telemetryRemoved, expired);
        log?.LogInformation("prune: {Stats}", stats);
        return stats;
    }

    /// <summary>Average each minute in [start, end) that has more than one snapshot into a single row.
    /// Returns (rows removed, whether the range had any rows).</summary>
    static (int Removed, bool HadRows) ThinSnapshots(DbFactory sessions, int systemId, DateTime start, DateTime end)
    {
        int removed = 0;
        using var s = sessions.Open();
        using var tx = s.Database.BeginTransaction();
        var rows = s.Snapshots.AsNoTracking().Where(x => x.SystemId == systemId && x.Time >= start && x.Time < end)
                    .OrderBy(x => x.Time).ToList();
        var minutes = new Dictionary<long, List<Snapshot>>();
        var order = new List<long>();
        foreach (var r in rows)
        {
            long minute = (long)Py.FloorDiv(Db.Epoch(r.Time), 60);
            if (!minutes.TryGetValue(minute, out var g)) { minutes[minute] = g = []; order.Add(minute); }
            g.Add(r);
        }
        foreach (var minute in order)
        {
            var group = minutes[minute];
            if (group.Count < 2) continue;
            var data = AverageRows(group.Select(g => Db.Parse(g.Data) as JsonObject ?? []).ToList());
            var mid = group[group.Count / 2];
            var times = group.Select(g => g.Time).ToList();
            s.Snapshots.Where(x => x.SystemId == systemId && times.Contains(x.Time)).ExecuteDelete();
            s.Snapshots.Add(new Snapshot { SystemId = systemId, Time = mid.Time, Mode = mid.Mode, Data = Db.Json(data) });
            s.SaveChanges();
            s.ChangeTracker.Clear();
            removed += group.Count - 1;
        }
        tx.Commit();
        return (removed, rows.Count > 0);
    }
}
