using System.Globalization;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;

namespace Fullscope.Cloud.Api;

/// <summary>
/// Maintenance reminders (air filter, tune-up) and the system's service contractor (port of
/// hvaccloud/service.py). An item is due after interval_days or interval_run_hours of blower time
/// since it was last done, whichever comes first. Blower time comes from runtime_days.
/// </summary>
public static class Service
{
    public sealed record Kind(string Label, int IntervalDays, double? IntervalRunHours);

    public static readonly (string Key, Kind Cfg)[] Kinds =
    [
        ("filter", new Kind("Air filter", 90, 500.0)),
        ("tuneup", new Kind("Tune-up", 182, null)),
    ];
    public const double Soon = 0.85;            // "Due soon" from 85 % of the interval
    public const int RecentDays = 14;           // recent blower use, to estimate when the run-hour limit is reached

    public static readonly (string Key, string Label)[] VisitKinds =
        [("tuneup", "Tune-up"), ("repair", "Repair"), ("install", "Installation"), ("inspection", "Inspection"), ("other", "Other")];
    public static readonly string[] ReadingKeys = ["mode", "p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc", "oat", "t_ret", "t_sup", "dt"];
    public const double ReadingsMaxAgeS = 15 * 60;   // "attach current readings" only uses a snapshot this fresh

    public static Kind? KindOf(string key) => Kinds.FirstOrDefault(k => k.Key == key).Cfg;

    public static string IsoDate(DateOnly d) => d.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    public static double BlowerHours(Db s, int systemId, DateOnly since, DateOnly? until = null)
    {
        var q = s.RuntimeDays.Where(r => r.SystemId == systemId && r.Day >= since);
        if (until is DateOnly u) q = q.Where(r => r.Day <= u);
        return (q.Select(r => (double?)r.BlowerS).Sum() ?? 0.0) / 3600;
    }

    public static JsonObject ItemView(Db s, int systemId, string kind, MaintenanceItem? row, DateOnly today)
    {
        var cfg = KindOf(kind)!;
        int? daysInt = row is not null ? row.IntervalDays : cfg.IntervalDays;
        double? hoursInt = row is not null ? row.IntervalRunHours : cfg.IntervalRunHours;
        var last = row?.LastDone;
        var outObj = new JsonObject
        {
            ["kind"] = kind, ["label"] = cfg.Label, ["interval_days"] = daysInt, ["interval_run_hours"] = hoursInt,
            ["last_done"] = last is DateOnly l ? IsoDate(l) : null, ["days_since"] = null, ["run_hours_since"] = null,
            ["progress"] = null, ["status"] = "unset", ["next_due"] = null,
        };
        if (last is not DateOnly done) return outObj;
        int days = today.DayNumber - done.DayNumber;
        double hours = BlowerHours(s, systemId, done);
        var parts = new List<double>();
        var dueDates = new List<DateOnly>();
        if (daysInt is int di && di != 0)
        {
            parts.Add((double)days / di);
            dueDates.Add(done.AddDays(di));
        }
        if (hoursInt is double hi && hi != 0)
        {
            parts.Add(hours / hi);
            double recent = BlowerHours(s, systemId, today.AddDays(-(RecentDays - 1)), today) / RecentDays;
            if (hours >= hi) dueDates.Add(today);
            else if (recent > 0) dueDates.Add(today.AddDays((int)Math.Truncate((hi - hours) / recent)));
        }
        double? progress = parts.Count > 0 ? parts.Max() : null;
        outObj["days_since"] = days;
        outObj["run_hours_since"] = Py.Round(hours, 1);
        outObj["progress"] = progress is double p ? Py.Round(p, 3) : null;
        outObj["status"] = progress is not double pr ? "unset" : pr >= 1 ? "due" : pr >= Soon ? "soon" : "ok";
        outObj["next_due"] = dueDates.Count > 0 ? IsoDate(dueDates.Min()) : null;
        return outObj;
    }

    public static JsonObject ServiceView(Db s, int systemId, DateOnly? today = null)
    {
        var day = today ?? Time.Today();
        var info = s.ServiceInfo.Find(systemId);
        var rows = s.MaintenanceItems.Where(r => r.SystemId == systemId).ToList()
                    .GroupBy(r => r.Kind).ToDictionary(g => g.Key, g => g.Last());
        JsonObject? contractor = null;
        if (info is not null && (!string.IsNullOrEmpty(info.Name) || !string.IsNullOrEmpty(info.Phone) || !string.IsNullOrEmpty(info.Email)))
            contractor = new JsonObject { ["name"] = info.Name, ["phone"] = info.Phone, ["email"] = info.Email };
        return new JsonObject
        {
            ["contractor"] = contractor,
            ["items"] = new JsonArray(Kinds.Select(k => (JsonNode?)ItemView(s, systemId, k.Key, rows.GetValueOrDefault(k.Key), day)).ToArray()),
        };
    }

    /// <summary>Move an item's last-done date forward (never back: an older visit logged late changes nothing).</summary>
    public static void MarkDone(Db s, int systemId, string kind, DateOnly date)
    {
        var row = GetItem(s, systemId, kind);
        if (row.LastDone is null || date > row.LastDone) row.LastDone = date;
    }

    public static JsonObject VisitView(ServiceVisit v) => new()
    {
        ["id"] = v.Id, ["date"] = IsoDate(v.Date), ["kind"] = v.Kind,
        ["kind_label"] = VisitKinds.FirstOrDefault(k => k.Key == v.Kind).Label ?? v.Kind,
        ["technician"] = v.Technician, ["work"] = v.Work, ["filter_changed"] = v.FilterChanged,
        ["readings"] = PyJson.Loads(v.Readings ?? "null"),
    };

    /// <summary>The stored row for `kind`, created with the defaults on first change.</summary>
    public static MaintenanceItem GetItem(Db s, int systemId, string kind)
    {
        var row = s.MaintenanceItems.Local.FirstOrDefault(r => r.SystemId == systemId && r.Kind == kind)
                  ?? s.MaintenanceItems.FirstOrDefault(r => r.SystemId == systemId && r.Kind == kind);
        if (row is null)
        {
            var cfg = KindOf(kind)!;
            row = new MaintenanceItem { SystemId = systemId, Kind = kind, IntervalDays = cfg.IntervalDays, IntervalRunHours = cfg.IntervalRunHours };
            s.MaintenanceItems.Add(row);
        }
        return row;
    }
}
