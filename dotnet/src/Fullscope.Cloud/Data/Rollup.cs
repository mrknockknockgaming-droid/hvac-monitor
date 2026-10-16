using System.Text.Json;
using System.Text.Json.Nodes;
using Fullscope.Core;
using Microsoft.EntityFrameworkCore;

namespace Fullscope.Cloud.Data;

/// <summary>
/// One minute of a system's snapshots, added up (C# only; the Python server doesn't have this table).
/// Ingest rewrites a minute's row each time a snapshot lands in it, so long history ranges and the
/// runtime summary read about 1/6 as many rows as there are snapshots and never parse snapshot JSON.
/// First/last time and on-state let the summary join minutes exactly as it would join snapshots.
/// </summary>
public sealed class SnapshotMinute
{
    public int SystemId { get; set; }
    public DateTime Minute { get; set; }               // start of the minute, UTC
    public int N { get; set; }                         // snapshots
    public int OnN { get; set; }                       // ... with the compressor running (cooling/heating)
    public double FirstT { get; set; }                 // epoch of the first and last snapshot
    public bool FirstOn { get; set; }
    public double LastT { get; set; }
    public bool LastOn { get; set; }
    public int Cycles { get; set; }                    // off -> on between two snapshots inside the minute
    public double OnS { get; set; }                    // run seconds between snapshots inside the minute
    public double? OatMax { get; set; }
    public bool? OatMaxInt { get; set; }               // the reading was a whole number (shown as 95, not 95.0)
    public double? PLowSum { get; set; }
    public int PLowN { get; set; }
    public double? PHighSum { get; set; }
    public int PHighN { get; set; }
    public double? SatLowSum { get; set; }
    public int SatLowN { get; set; }
    public double? SatHighSum { get; set; }
    public int SatHighN { get; set; }
    public double? TSucSum { get; set; }
    public int TSucN { get; set; }
    public double? TLiqSum { get; set; }
    public int TLiqN { get; set; }
    public double? ShSum { get; set; }
    public int ShN { get; set; }
    public double? ScSum { get; set; }
    public int ScN { get; set; }
    public double? OatSum { get; set; }
    public int OatN { get; set; }
    public double? TSupSum { get; set; }
    public int TSupN { get; set; }
    public double? TRetSum { get; set; }
    public int TRetN { get; set; }
    public double? DtSum { get; set; }
    public int DtN { get; set; }

    public (double Sum, int N) Get(int i) => i switch
    {
        0 => (PLowSum ?? 0, PLowN), 1 => (PHighSum ?? 0, PHighN), 2 => (SatLowSum ?? 0, SatLowN),
        3 => (SatHighSum ?? 0, SatHighN), 4 => (TSucSum ?? 0, TSucN), 5 => (TLiqSum ?? 0, TLiqN),
        6 => (ShSum ?? 0, ShN), 7 => (ScSum ?? 0, ScN), 8 => (OatSum ?? 0, OatN),
        9 => (TSupSum ?? 0, TSupN), 10 => (TRetSum ?? 0, TRetN), 11 => (DtSum ?? 0, DtN),
        _ => throw new ArgumentOutOfRangeException(nameof(i)),
    };

    public void Set(int i, double sum, int n)
    {
        double? v = n > 0 ? sum : null;
        switch (i)
        {
            case 0: (PLowSum, PLowN) = (v, n); break;
            case 1: (PHighSum, PHighN) = (v, n); break;
            case 2: (SatLowSum, SatLowN) = (v, n); break;
            case 3: (SatHighSum, SatHighN) = (v, n); break;
            case 4: (TSucSum, TSucN) = (v, n); break;
            case 5: (TLiqSum, TLiqN) = (v, n); break;
            case 6: (ShSum, ShN) = (v, n); break;
            case 7: (ScSum, ScN) = (v, n); break;
            case 8: (OatSum, OatN) = (v, n); break;
            case 9: (TSupSum, TSupN) = (v, n); break;
            case 10: (TRetSum, TRetN) = (v, n); break;
            case 11: (DtSum, DtN) = (v, n); break;
            default: throw new ArgumentOutOfRangeException(nameof(i));
        }
    }
}

/// <summary>The values of a snapshot that history and the summary use, read without building a JSON tree.</summary>
public readonly record struct SnapshotPoint(double T, bool On, Num?[] Values, Num? Oat)
{
    public static SnapshotPoint Read(DateTime time, string? data)
    {
        var values = new Num?[Rollup.HistoryKeys.Length];
        bool on = false;
        Num? oat = null;
        if (!string.IsNullOrEmpty(data))
        {
            try
            {
                using var doc = JsonDocument.Parse(data);
                if (doc.RootElement.ValueKind == JsonValueKind.Object)
                {
                    foreach (var p in doc.RootElement.EnumerateObject())
                    {
                        if (p.Name == "mode")
                        {
                            on = p.Value.ValueKind == JsonValueKind.String && p.Value.GetString() is "cooling" or "heating";
                            continue;
                        }
                        int i = Array.IndexOf(Rollup.HistoryKeys, p.Name);
                        if (i < 0 || p.Value.ValueKind != JsonValueKind.Number) continue;
                        double v = p.Value.GetDouble();
                        if (!double.IsFinite(v)) continue;
                        values[i] = new Num(v, p.Value.GetRawText().IndexOfAny(['.', 'e', 'E']) < 0);   // a repeated key keeps the last value
                        if (i == Rollup.OatIndex) oat = values[i];
                    }
                }
            }
            catch (JsonException)
            {
                var d = PyJson.Loads(data) as JsonObject;            // NaN / Infinity tokens
                on = J.Str(d, "mode") is "cooling" or "heating";
                for (int i = 0; i < values.Length; i++) values[i] = J.Num(d, Rollup.HistoryKeys[i]);
                oat = J.Num(d, "oat");
            }
        }
        return new SnapshotPoint(Db.Epoch(time), on, values, oat);
    }
}

/// <summary>
/// History and the runtime summary (GET /history, /summary). Ranges of 10 hours or more read the
/// per-minute rollup with buckets rounded up to whole minutes; shorter ones average the snapshots
/// themselves, exactly as the Python server does.
/// </summary>
public static class Rollup
{
    public static readonly string[] HistoryKeys = ["p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc", "oat", "t_sup", "t_ret", "dt"];
    public static readonly int OatIndex = Array.IndexOf(HistoryKeys, "oat");
    public static readonly int RetIndex = Array.IndexOf(HistoryKeys, "t_ret");
    public const double RunGapS = 60;               // a gap longer than a minute is an outage, not runtime

    public static DateTime MinuteOf(DateTime t) => Db.Ts(Py.FloorDiv(Db.Epoch(t), 60) * 60);

    static DateTime NextMinute(DateTime t)
    {
        var m = MinuteOf(t);
        return m == t ? m : m.AddSeconds(60);
    }

    /// <summary>The row for one minute, from its snapshots (null when it has none).</summary>
    public static SnapshotMinute? Build(int systemId, DateTime minute, IReadOnlyList<SnapshotPoint> points)
    {
        if (points.Count == 0) return null;
        var m = new SnapshotMinute { SystemId = systemId, Minute = minute, N = points.Count };
        var sums = new PySum[HistoryKeys.Length];
        var counts = new int[HistoryKeys.Length];
        SnapshotPoint? prev = null;
        foreach (var p in points)
        {
            if (p.On) m.OnN++;
            if (prev is SnapshotPoint q)
            {
                if (q.On) m.OnS += Math.Min(p.T - q.T, RunGapS);
                if (p.On && !q.On) m.Cycles++;
            }
            for (int i = 0; i < HistoryKeys.Length; i++)
            {
                if (p.Values[i] is Num v)
                {
                    sums[i].Add(v);
                    counts[i]++;
                }
            }
            if (p.Oat is Num oat && (m.OatMax is null || oat.V > m.OatMax))
                (m.OatMax, m.OatMaxInt) = (oat.V, oat.IsInt);
            prev = p;
        }
        (m.FirstT, m.FirstOn) = (points[0].T, points[0].On);
        (m.LastT, m.LastOn) = (points[^1].T, points[^1].On);
        for (int i = 0; i < HistoryKeys.Length; i++) m.Set(i, sums[i].Value, counts[i]);
        return m;
    }

    internal static List<SnapshotPoint> Points(Db s, int systemId, DateTime from, DateTime? to) =>
        (to is DateTime end
            ? s.Snapshots.AsNoTracking().Where(x => x.SystemId == systemId && x.Time >= from && x.Time < end)
            : s.Snapshots.AsNoTracking().Where(x => x.SystemId == systemId && x.Time >= from))
        .OrderBy(x => x.Time).Select(x => new { x.Time, x.Data }).AsEnumerable()
        .Select(x => SnapshotPoint.Read(x.Time, x.Data)).ToList();

    /// <summary>Rewrite the minute that holds `time` from its snapshots (ingest calls this after each one).</summary>
    public static void Refresh(Db s, int systemId, DateTime time)
    {
        var minute = MinuteOf(time);
        var row = Build(systemId, minute, Points(s, systemId, minute, minute.AddSeconds(60)));
        var old = s.SnapshotMinutes.Find(systemId, minute);
        if (old is null && row is not null) s.SnapshotMinutes.Add(row);
        else if (old is not null && row is null) s.SnapshotMinutes.Remove(old);
        else if (old is not null) s.Entry(old).CurrentValues.SetValues(row!);
    }

    /// <summary>Fill in minutes that have snapshots but no row: everything on a database the Python
    /// ingest wrote, otherwise only from the newest row on. Returns the minutes written.</summary>
    public static int Backfill(DbFactory sessions, int systemId, TimeSpan? chunk = null)
    {
        var step = chunk ?? TimeSpan.FromDays(1);
        DateTime? from, newest;
        using (var s = sessions.Open())
        {
            from = s.SnapshotMinutes.Where(x => x.SystemId == systemId).OrderByDescending(x => x.Minute)
                    .Select(x => (DateTime?)x.Minute).FirstOrDefault()
                   ?? s.Snapshots.Where(x => x.SystemId == systemId).OrderBy(x => x.Time).Select(x => (DateTime?)x.Time).FirstOrDefault();
            newest = s.Snapshots.Where(x => x.SystemId == systemId).OrderByDescending(x => x.Time).Select(x => (DateTime?)x.Time).FirstOrDefault();
        }
        if (from is null || newest is null) return 0;
        int written = 0;
        for (var start = MinuteOf(from.Value); start <= newest; start += step)
        {
            var end = start + step;
            using var s = sessions.Open();
            using var tx = s.Database.BeginTransaction();
            s.SnapshotMinutes.Where(x => x.SystemId == systemId && x.Minute >= start && x.Minute < end).ExecuteDelete();
            foreach (var g in Points(s, systemId, start, end).GroupBy(p => Py.FloorDiv(p.T, 60)))
            {
                s.SnapshotMinutes.Add(Build(systemId, Db.Ts(g.Key * 60), g.ToList())!);
                written++;
            }
            s.SaveChanges();
            tx.Commit();
        }
        return written;
    }

    // ---------- history ----------

    /// <summary>Bucket length for a range: minutes*60/600 s, as the Python server and the web app
    /// expect, rounded up to whole minutes when the range is long enough to read the rollup.</summary>
    public static (long Bucket, bool FromRollup) BucketFor(double minutes)
    {
        long bucket = Math.Max(1, (long)(minutes * 60 / 600));
        return bucket >= 60 ? ((bucket + 59) / 60 * 60, true) : (bucket, false);
    }

    sealed class Acc
    {
        public readonly PySum[] Sums = new PySum[HistoryKeys.Length];
        public readonly int[] Counts = new int[HistoryKeys.Length];
        public readonly List<int> Order = [];              // keys in the order they first had a value
        public int N, OnN;

        public void Add(int i, Num sum, int n)
        {
            if (n == 0) return;
            if (Counts[i] == 0) Order.Add(i);
            Sums[i].Add(sum);
            Counts[i] += n;
        }

        public JsonObject Row(long ts)
        {
            var row = new JsonObject { ["ts"] = ts };
            foreach (var i in Order) row[HistoryKeys[i]] = Py.Round(Sums[i].Value / Counts[i], 1);
            row["_on"] = Py.Round((double)OnN / N, 1);
            return row;
        }
    }

    /// <summary>Averaged into at most ~600 points per series (GET /history). `raw` reads every
    /// snapshot even for long ranges (tests compare the two).</summary>
    public static JsonArray History(Db s, int systemId, double minutes, DateTime since, bool raw = false)
    {
        var (bucket, fromRollup) = BucketFor(minutes);
        if (raw) fromRollup = false;
        var outRows = new JsonArray();
        Acc? acc = null;
        long cur = 0;
        void Into(long b)
        {
            if (acc is not null && b != cur) outRows.Add(acc.Row(cur * bucket));
            if (acc is null || b != cur) acc = new Acc();
            cur = b;
        }
        void AddPoint(SnapshotPoint p)
        {
            Into((long)Py.FloorDiv(p.T, bucket));
            for (int i = 0; i < HistoryKeys.Length; i++)
                if (p.Values[i] is Num v) acc!.Add(i, v, 1);
            acc!.N++;
            if (p.On) acc.OnN++;
        }
        if (!fromRollup)
        {
            foreach (var p in Points(s, systemId, since, null)) AddPoint(p);
        }
        else
        {
            var head = NextMinute(since);
            foreach (var p in Points(s, systemId, since, head)) AddPoint(p);
            foreach (var m in s.SnapshotMinutes.AsNoTracking().Where(x => x.SystemId == systemId && x.Minute >= head).OrderBy(x => x.Minute))
            {
                Into((long)Py.FloorDiv(Db.Epoch(m.Minute), bucket));
                for (int i = 0; i < HistoryKeys.Length; i++)
                {
                    var (sum, n) = m.Get(i);
                    acc!.Add(i, Num.Float(sum), n);
                }
                acc!.N += m.N;
                acc.OnN += m.OnN;
            }
        }
        if (acc is not null) outRows.Add(acc.Row(cur * bucket));
        return outRows;
    }

    // ---------- summary ----------

    public sealed record SummaryResult(double OnS, int Cycles, Num? OatHigh, double RetSum, int RetN);

    /// <summary>Compressor run time and cycles, the outside high and the return-air sum since `since`.</summary>
    public static SummaryResult Summary(Db s, int systemId, DateTime since)
    {
        double onS = 0;
        var retSum = new PySum();
        int cycles = 0, retN = 0;
        double? prevT = null;
        bool prevOn = false;
        Num? oatHi = null;
        var head = NextMinute(since);
        foreach (var p in Points(s, systemId, since, head))
        {
            if (p.On && !prevOn) cycles++;
            if (prevOn && prevT is double pt) onS += Math.Min(p.T - pt, RunGapS);
            (prevT, prevOn) = (p.T, p.On);
            if (p.Oat is Num oat && (oatHi is null || oat.V > oatHi.Value.V)) oatHi = oat;
            if (p.Values[RetIndex] is Num r)
            {
                retSum.Add(r);
                retN++;
            }
        }
        foreach (var m in s.SnapshotMinutes.AsNoTracking().Where(x => x.SystemId == systemId && x.Minute >= head).OrderBy(x => x.Minute))
        {
            if (m.FirstOn && !prevOn) cycles++;
            if (prevOn && prevT is double pt) onS += Math.Min(m.FirstT - pt, RunGapS);
            cycles += m.Cycles;
            onS += m.OnS;
            (prevT, prevOn) = (m.LastT, m.LastOn);
            if (m.OatMax is double hi && (oatHi is null || hi > oatHi.Value.V)) oatHi = new Num(hi, m.OatMaxInt == true);
            var (sum, n) = m.Get(RetIndex);
            if (n > 0) retSum.Add(Num.Float(sum));
            retN += n;
        }
        return new SummaryResult(onS, cycles, oatHi, retSum.Value, retN);
    }
}

/// <summary>
/// Recent /history and /summary answers, for when several screens poll the same system: an answer
/// is reused for up to 10 seconds while no newer snapshot has arrived.
/// </summary>
public sealed class HistoryCache
{
    static readonly TimeSpan Keep = TimeSpan.FromSeconds(10);
    readonly Dictionary<(int, string, double), (DateTime? Newest, DateTime At, object Value)> _items = [];
    readonly Lock _lock = new();

    public T Get<T>(Db s, int systemId, string kind, double param, Func<T> compute) where T : class
    {
        var newest = s.Snapshots.Where(x => x.SystemId == systemId).OrderByDescending(x => x.Time).Select(x => (DateTime?)x.Time).FirstOrDefault();
        var key = (systemId, kind, param);
        var now = DateTime.UtcNow;
        lock (_lock)
        {
            if (_items.TryGetValue(key, out var hit) && hit.Newest == newest && now - hit.At < Keep && hit.Value is T value) return value;
            if (_items.Count > 500)
                foreach (var k in _items.Where(kv => now - kv.Value.At >= Keep).Select(kv => kv.Key).ToList()) _items.Remove(k);
        }
        var fresh = compute();
        lock (_lock) _items[key] = (newest, now, fresh);
        return fresh;
    }
}
