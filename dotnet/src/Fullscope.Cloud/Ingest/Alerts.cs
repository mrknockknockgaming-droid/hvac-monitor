using System.Globalization;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;
using Microsoft.EntityFrameworkCore;

namespace Fullscope.Cloud.Ingest;

/// <summary>One email to send: (alert id or null, to, subject, body).</summary>
public sealed record EmailJob(int? AlertId, string To, string Subject, string Body);

/// <summary>
/// Alerts: diagnostic flags that persist, and the emails about them (port of hvaccloud/alerts.py).
/// A flag opens a pending alert the first time it shows up. The alert is raised once the flag has
/// held for `hold` seconds, and cleared once the flag has been gone for `hold` seconds, so a value
/// hovering at a limit gives one alert instead of one per reading. Pending alerts that go away
/// before they are raised are deleted. Raised and cleared alerts are emailed.
/// </summary>
public static class Alerts
{
    public const string NoData = "no_data";
    static readonly HashSet<string> ClearAtOnce = [NoData];      // readings are back: no reason to wait
    const double SlackS = 0.001;                                 // stored times are rounded to microseconds
    static readonly Dictionary<string, string> LevelWord = new() { ["alert"] = "Fault", ["warn"] = "Warning" };

    // Checks that can only be judged while the equipment runs. Between cycles their alerts stay as
    // they are instead of "clearing" every time the compressor stops.
    public static readonly HashSet<string> SteadyCodes = ["sh_low", "sh_high", "sc_low", "sc_high", "dt_low", "ctoa_high"];
    public static readonly HashSet<string> SettledCodes = ["contactor_open", "comp_not_running", "fan_not_running", "comp_amps_high", "fan_amps_high"];
    public static readonly HashSet<string> RunningCodes = [.. SettledCodes, "cap_herm", "cap_fan", "contactor_drop", "voltage"];
    public static readonly HashSet<string> AirflowCodes = ["tesp_high", "filter_dirty"];       // Airflow: blower on
    const double SteadyMin = 10, SettleMin = 1.0;

    /// <summary>Codes whose checks could not run on this snapshot (see SteadyCodes).</summary>
    public static HashSet<string> Unjudged(JsonObject snap)
    {
        double runMin = J.Num(snap, "run_min")?.V ?? 0;
        var mode = J.Str(snap, "mode");
        var outSet = new HashSet<string>();
        if (mode is not ("cooling" or "heating") || runMin < SteadyMin) outSet.UnionWith(SteadyCodes);
        if (!J.Truthy(snap, "Y") || !J.Truthy(snap, "elec")) outSet.UnionWith(RunningCodes);
        else if (runMin < SettleMin) outSet.UnionWith(SettledCodes);
        if (mode is not ("cooling" or "heating" or "heat_aux" or "fan") || !J.Truthy(snap, "static"))
            outSet.UnionWith(AirflowCodes);                    // blower off, or no static sensors reporting
        if (J.Flags(snap).Any(f => J.Str(f, "code") == "node_offline" && J.Str(f, "node") is "outdoor" or "indoor"))
            outSet.UnionWith(SteadyCodes);                     // can't compute superheat etc. without both nodes
        return outSet;
    }

    /// <summary>Update the system's open alerts from the current flags. Returns (kind, alert) events.
    /// Raised alerts whose code is in `keep` (not judged this time) stay open.</summary>
    public static List<(string Kind, Alert Alert)> Sync(Db s, HvacSystem system, IEnumerable<JsonNode?> flags, double now,
                                                        double hold, IReadOnlySet<string>? keep = null)
    {
        keep ??= new HashSet<string>();
        var open = new Dictionary<(string, string?), Alert>();
        foreach (var a in s.Alerts.Where(a => a.SystemId == system.Id && a.ClearedAt == null).OrderBy(a => a.Id))
            open[(a.Code, a.Node)] = a;
        var events = new List<(string, Alert)>();
        var seen = new HashSet<(string, string?)>();
        var ts = Db.Ts(now);
        foreach (var f in flags)
        {
            var key = (J.Str(f, "code") ?? "", J.Str(f, "node"));
            seen.Add(key);
            if (!open.TryGetValue(key, out var a))
            {
                a = new Alert { SystemId = system.Id, Code = key.Item1, Node = key.Item2, StartedAt = ts };
                s.Alerts.Add(a);
                open[key] = a;
            }
            var text = J.Str(f, "text") ?? "";
            a.Level = J.Str(f, "level") ?? "";
            a.Text = text.Length > 300 ? text[..300] : text;
            a.LastSeen = ts;
            if (a.RaisedAt is null && now - Db.Epoch(a.StartedAt) >= hold - SlackS)
            {
                a.RaisedAt = ts;
                events.Add(("raised", a));
            }
        }
        foreach (var (key, a) in open)
        {
            if (seen.Contains(key) || (keep.Contains(a.Code) && a.RaisedAt is not null)) continue;
            double gone = now - Db.Epoch(a.LastSeen);
            if (ClearAtOnce.Contains(a.Code) || gone >= hold - SlackS)
            {
                if (a.RaisedAt is null)
                {
                    s.Alerts.Remove(a);
                }
                else
                {
                    a.ClearedAt = ts;
                    events.Add(("cleared", a));
                }
            }
        }
        s.SaveChanges();
        return events;
    }

    /// <summary>Flags for a system whose nodes have gone quiet: the last snapshot's flags (they can't be
    /// re-checked, so they stay open) plus a no-data flag. Null while readings are recent or never came.</summary>
    public static List<JsonNode?>? NoDataFlags(Db s, HvacSystem system, double now, double silence)
    {
        var row = s.Snapshots.Where(x => x.SystemId == system.Id).OrderByDescending(x => x.Time)
                   .Select(x => new { x.Time, x.Data }).FirstOrDefault();
        if (row is null) return null;
        double quiet = now - Db.Epoch(row.Time);
        if (quiet < silence) return null;
        var flags = J.Get(Db.Parse(row.Data), "flags") is JsonArray a ? a.Select(J.Copy).ToList() : [];
        flags.Add(J.Flag("warn", NoData, $"No readings from either node for {Py.RoundInt(quiet / 60)} min"));
        return flags;
    }

    /// <summary>Homeowner wording for owner emails. Keep in step with FLAG_INFO in web/app.js.</summary>
    public static readonly Dictionary<string, (string Title, string Why, string Todo)> Plain = new()
    {
        ["sh_low"] = ("Refrigerant flow needs a check", "Liquid refrigerant may be reaching the compressor, which can damage it over time.",
                      "Request a service visit."),
        ["sh_high"] = ("Refrigerant may be low or restricted", "Your system works harder and cools less.",
                       "Request a service visit, and check that the outdoor unit isn't blocked."),
        ["sc_low"] = ("Refrigerant charge may be low", "Low charge reduces cooling and usually means a leak.", "Request a service visit."),
        ["sc_high"] = ("Refrigerant may be overcharged or restricted", "This raises pressures and strains the compressor.",
                       "Request a service visit."),
        ["dt_low"] = ("Air from your vents isn't as cool as it should be", "Your system runs longer and uses more electricity.",
                      "Check your air filter and replace it if it looks grey or dusty, and make sure vents aren't blocked."),
        ["filter_dirty"] = ("Your air filter is getting clogged", "A clogged filter cuts airflow, so the system cools less and uses more electricity.",
                            "Replace the air filter."),
        ["tesp_high"] = ("Air isn't moving freely through your system", "Restricted airflow lowers comfort and strains the blower motor.",
                         "Replace the air filter if it looks dirty; if this keeps showing, request a service visit."),
        ["ctoa_high"] = ("Outdoor unit isn't releasing heat well", "The compressor runs hotter and uses more electricity.",
                         "Clear debris from around the outdoor unit (about 2 ft), then request a coil cleaning."),
        ["node_offline"] = ("A monitor isn't reporting", "Heating and cooling are not affected; some checks are paused.",
                            "Nothing to do on your own; your contractor can check it."),
        ["sensor_issue"] = ("A monitoring sensor isn't reporting", "Heating and cooling are not affected.",
                            "Nothing to do on your own; your contractor can check it."),
        ["room_hot"] = ("It's very hot inside", "The room has reached 90 F or more.",
                        "Check the thermostat is set to Cool, then request a service visit."),
        ["room_cold"] = ("It's very cold inside", "The room has dropped to 50 F or less; pipes can freeze.",
                         "Check the thermostat is set to Heat, then request a service visit."),
        ["setpoint_not_reached"] = ("Your system isn't keeping up", "It has run a long time without reaching your setting.",
                                    "Check your air filter and that windows and doors are shut; if it keeps happening, request a service visit."),
        ["call_mismatch"] = ("The thermostat and the equipment disagree", "The equipment isn't doing what the thermostat asks.",
                             "Request a service visit."),
        [NoData] = ("We lost contact with your monitors", "No checks can run until readings come back.",
                    "Check that your WiFi is working."),
        ["comp_not_running"] = ("Your outdoor unit isn't running", "The thermostat is calling for cooling but the compressor isn't on, so the house isn't being cooled.",
                                "Request a service visit. Turning the system off at the thermostat until then protects the equipment."),
        ["fan_not_running"] = ("The fan on your outdoor unit has stopped", "Without it the compressor overheats and can be damaged.",
                               "Turn the system off at the thermostat and request a service visit."),
        ["contactor_open"] = ("Your outdoor unit isn't switching on", "The thermostat is calling for cooling but the outdoor unit doesn't start.",
                              "Request a service visit."),
        ["comp_amps_high"] = ("Your outdoor unit is working harder than it should", "High current shortens the compressor's life and uses more electricity.",
                              "Request a service visit."),
        ["fan_amps_high"] = ("The outdoor fan motor is working hard", "A struggling fan motor often fails soon.", "Request a service visit."),
        ["cap_herm"] = ("A part in your outdoor unit is wearing out", "A weak run capacitor makes the compressor run hot and can stop it starting.",
                        "Mention it at your next service visit, or sooner if the unit struggles to start."),
        ["cap_fan"] = ("A part in your outdoor unit is wearing out", "A weak fan capacitor slows the outdoor fan and can stop it.",
                       "Mention it at your next service visit."),
        ["contactor_drop"] = ("A switch in your outdoor unit is wearing out", "Worn contacts heat up and can stop the unit from starting.",
                              "Mention it at your next service visit."),
        ["voltage"] = ("The power to your outdoor unit is outside its range", "Low or high voltage strains the motors.",
                       "Request a service visit; your contractor may involve the utility."),
        ["slow_start"] = ("Your outdoor unit is slow to start", "Hard starts wear the compressor and can trip breakers.",
                          "Request a service visit."),
    };

    /// <summary>Homeowner level of each flag code (FLAG_INFO in web/app.js): fault = service needed,
    /// caution = check soon, advisory = good to know. Used to sort the contractor's fleet page.</summary>
    public static readonly Dictionary<string, string> Level = new()
    {
        ["sh_low"] = "fault", ["sh_high"] = "caution", ["sc_low"] = "caution", ["sc_high"] = "caution", ["dt_low"] = "caution",
        ["ctoa_high"] = "caution", ["node_offline"] = "advisory", ["sensor_issue"] = "advisory", [NoData] = "advisory",
        ["room_hot"] = "fault", ["room_cold"] = "fault", ["setpoint_not_reached"] = "caution", ["call_mismatch"] = "caution",
        ["comp_not_running"] = "fault", ["fan_not_running"] = "fault", ["contactor_open"] = "fault",
        ["comp_amps_high"] = "caution", ["fan_amps_high"] = "caution", ["cap_herm"] = "caution", ["cap_fan"] = "caution",
        ["contactor_drop"] = "caution", ["voltage"] = "caution", ["slow_start"] = "caution",
        ["filter_dirty"] = "caution", ["tesp_high"] = "caution",
    };

    public static List<string> HomeownerEmails(Db s, int systemId) =>
        s.Users.Join(s.SystemMembers.Where(m => m.SystemId == systemId), u => u.Id, m => m.UserId, (u, m) => u.Email)
               .OrderBy(e => e).ToList();

    /// <summary>The contractor-panel email, else the contractor account's own email.</summary>
    public static string? ContractorEmail(Db s, HvacSystem system)
    {
        var info = s.ServiceInfo.Find(system.Id);
        if (info is not null && !string.IsNullOrEmpty(info.Email)) return info.Email;
        var acct = s.Accounts.Find(system.AccountId);
        return acct is not null && !string.IsNullOrEmpty(acct.Email) ? acct.Email : null;
    }

    /// <summary>(email the homeowners, email the contractor); both on unless changed.</summary>
    public static (bool Owners, bool Contractor) Wants(Db s, int systemId)
    {
        var prefs = s.AlertPrefs.Find(systemId);
        return prefs is null ? (true, true) : (prefs.EmailOwner, prefs.EmailContractor);
    }

    /// <summary>[(address, "owner" | "contractor")]: homeowners get plain language, the contractor the technical version.</summary>
    public static List<(string Address, string Who)> Recipients(Db s, HvacSystem system)
    {
        var (ownersOn, contractorOn) = Wants(s, system.Id);
        var outList = ownersOn ? HomeownerEmails(s, system.Id).Select(e => (e, "owner")).ToList() : [];
        var c = contractorOn ? ContractorEmail(s, system) : null;
        if (!string.IsNullOrEmpty(c) && outList.All(x => x.Item1 != c)) outList.Add((c, "contractor"));
        return outList;
    }

    public static bool Muted(Db s, int systemId, string code, double now)
    {
        var m = s.AlertMutes.Find(systemId, code);
        return m is not null && Db.Epoch(m.Until) > now;
    }

    /// <summary>(subject, body) for one recipient. Owners get plain words, contractors the technical text.</summary>
    public static (string Subject, string Body) Compose(HvacSystem system, Alert a, string kind, string who, string appUrl)
    {
        if (who == "contractor")
        {
            var tag = $"{system.Name} (site {system.SiteId})";
            List<string> lines;
            string subject;
            if (kind == "raised")
            {
                subject = $"[Fullscope] {tag}: {a.Text}";
                lines = [$"{LevelWord.GetValueOrDefault(a.Level, "Alert")}: {a.Text}.",
                         $"First seen {Local(a.StartedAt)}, still present at {Local(a.RaisedAt)}."];
            }
            else
            {
                subject = $"[Fullscope] {tag}: cleared - {a.Text}";
                lines = [$"Cleared: {a.Text}.", $"Started {Local(a.StartedAt)}, cleared {Local(a.ClearedAt)}."];
            }
            lines.Add($"Technician view: {appUrl}#/monitor/{system.Id}");
            return (subject, string.Join("\n\n", lines));
        }
        var (title, why, todo) = Plain.TryGetValue(a.Code, out var p) ? p : (a.Text, "", "");
        string subj;
        List<string> body;
        if (kind == "raised")
        {
            subj = $"[Fullscope] {system.Name}: {title}";
            body = [$"{title}.", why, todo.Length > 0 ? $"What you can do: {todo}" : "",
                    $"For your contractor: {a.Text} (since {Local(a.StartedAt)})."];
        }
        else
        {
            subj = $"[Fullscope] {system.Name}: back to normal - {title}";
            body = [$"Back to normal: {title.ToLowerInvariant()}.", $"Started {Local(a.StartedAt)}, cleared {Local(a.ClearedAt)}."];
        }
        var lines2 = body.Where(x => x.Length > 0).ToList();
        lines2.Add($"Your system: {appUrl}#/home/{system.Id}");
        return (subj, string.Join("\n\n", lines2));
    }

    /// <summary>Email jobs for these events. Nothing for muted codes; raises are skipped when the same
    /// code was emailed within the cooldown; clears only follow an emailed raise nobody acknowledged.</summary>
    public static List<EmailJob> EmailsFor(Db s, HvacSystem system, List<(string Kind, Alert Alert)> events, double now,
                                           double cooldownS, string appUrl)
    {
        if (events.Count == 0) return [];
        var to = Recipients(s, system);
        if (to.Count == 0) return [];
        var jobs = new List<EmailJob>();
        foreach (var (kind, a) in events)
        {
            if (Muted(s, system.Id, a.Code, now)) continue;
            if (kind == "raised")
            {
                var since = Db.Ts(now - cooldownS);
                bool recent = s.Alerts.Any(x => x.SystemId == system.Id && x.Code == a.Code && x.Id != a.Id
                                                && x.EmailedAt != null && x.EmailedAt >= since);
                if (recent) continue;
            }
            else if (a.EmailedAt is null || a.AckedAt is not null)
            {
                continue;
            }
            foreach (var (addr, who) in to)
            {
                var (subject, body) = Compose(system, a, kind, who, appUrl);
                jobs.Add(new EmailJob(kind == "raised" ? a.Id : null, addr, subject, body));
            }
        }
        return jobs;
    }

    /// <summary>The zone email times are written in: the server's own, as in the Python version.</summary>
    public static TimeZoneInfo LocalZone { get; set; } = TimeZoneInfo.Local;

    /// <summary>"Mon Oct 5, 7:30 AM" in the server's local time (alerts.local).</summary>
    public static string Local(DateTime? t)
    {
        if (t is null) return "None";
        var local = TimeZoneInfo.ConvertTimeFromUtc(DateTime.SpecifyKind(t.Value, DateTimeKind.Utc), LocalZone);
        return local.ToString("ddd MMM dd, hh:mm tt", CultureInfo.InvariantCulture).Replace(" 0", " ");
    }
}
