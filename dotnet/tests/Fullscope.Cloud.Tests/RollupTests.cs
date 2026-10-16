using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;

namespace Fullscope.Cloud.Tests;

/// <summary>Long /history and /summary ranges read the per-minute rollup; they must agree with
/// the same calculation over every snapshot (the Python server's way).</summary>
[Collection("clock")]
public class RollupTests
{
    [Theory]
    [InlineData(600.0)]    // the first range that uses the rollup: 60 s buckets
    [InlineData(1440.0)]   // a day: 180 s buckets (144 s rounded up)
    [InlineData(4320.0)]   // three days
    public void HistoryMatchesEverySnapshot(double minutes)
    {
        var sessions = ApiReplayTests.Seed(out var path);
        try
        {
            using var s = sessions.Open();
            foreach (var id in s.Systems.Select(x => x.Id).ToList())
            {
                var newest = s.Snapshots.Where(x => x.SystemId == id).Max(x => (DateTime?)x.Time);
                if (newest is null) continue;
                // Starts a little after a minute boundary, so the raw head of the window is used too.
                var since = newest.Value.AddMinutes(-minutes).AddSeconds(-17.25);
                Assert.True(Rollup.BucketFor(minutes).FromRollup);
                var fast = Rollup.History(s, id, minutes, since);
                var raw = Rollup.History(s, id, minutes, since, raw: true);
                Assert.Equal(raw.Count, fast.Count);
                Assert.True(fast.Count > 1);
                for (int i = 0; i < raw.Count; i++)
                {
                    JsonObject a = raw[i]!.AsObject(), b = fast[i]!.AsObject();
                    // Key order can differ (the rollup adds a minute's keys at once); the web app reads by name.
                    Assert.Equal(a.Select(kv => kv.Key).Order(), b.Select(kv => kv.Key).Order());
                    foreach (var (k, v) in a)
                        Assert.True(Math.Abs(Py.NumberOf(v)!.Value.V - Py.NumberOf(b[k])!.Value.V) <= 0.1 + 1e-9, $"system {id} row {i} {k}: {v} vs {b[k]}");
                }
            }
        }
        finally { Cleanup(path); }
    }

    [Theory]
    [InlineData(1.0)]
    [InlineData(24.0)]
    [InlineData(72.0)]
    public void SummaryMatchesEverySnapshot(double hours)
    {
        var sessions = ApiReplayTests.Seed(out var path);
        try
        {
            using var s = sessions.Open();
            foreach (var id in s.Systems.Select(x => x.Id).ToList())
            {
                var newest = s.Snapshots.Where(x => x.SystemId == id).Max(x => (DateTime?)x.Time);
                if (newest is null) continue;
                var since = newest.Value.AddHours(-hours).AddSeconds(-17.25);
                var got = Rollup.Summary(s, id, since);

                // hvaccloud/api.py summary(), over every snapshot.
                double onS = 0, retSum = 0;
                int cycles = 0, retN = 0;
                double? prevT = null, oatHi = null;
                bool prevOn = false;
                foreach (var p in Rollup.Points(s, id, since, null))
                {
                    if (p.On && !prevOn) cycles++;
                    if (prevOn && prevT is double pt) onS += Math.Min(p.T - pt, Rollup.RunGapS);
                    (prevT, prevOn) = (p.T, p.On);
                    if (p.Oat is Num o && (oatHi is null || o.V > oatHi)) oatHi = o.V;
                    if (p.Values[Rollup.RetIndex] is Num r) { retSum += r.V; retN++; }
                }
                Assert.Equal(cycles, got.Cycles);
                Assert.Equal(onS, got.OnS, 6);
                Assert.Equal(oatHi, got.OatHigh?.V);
                Assert.Equal(retN, got.RetN);
                Assert.Equal(retSum, got.RetSum, 6);
            }
        }
        finally { Cleanup(path); }
    }

    static void Cleanup(string path)
    {
        Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
        foreach (var ext in new[] { "", "-wal", "-shm" }) File.Delete(path + ext);
    }
}
