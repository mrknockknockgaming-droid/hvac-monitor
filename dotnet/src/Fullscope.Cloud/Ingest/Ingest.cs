using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

namespace Fullscope.Cloud.Ingest;

/// <summary>
/// MQTT ingest: stores node messages and derived snapshots for every known system (port of
/// hvaccloud/ingest.py). Listens on hvac/&lt;site&gt;/&lt;node&gt;/{telemetry,status,reply} and
/// hvac/&lt;site&gt;/thermostat/request. &lt;site&gt; must match a system's site_id; messages from unknown
/// sites are ignored. Every message also updates the system's alerts, and once a minute systems
/// whose nodes have gone quiet get a "no data" alert (Sweep).
/// </summary>
public sealed class Ingest
{
    public static readonly string[] AllNodes = [.. Calc.Nodes, Electrical.Node, Thermostat.Node];
    public const double RunGapS = 60;               // longer gaps between snapshots don't count as run time
    public const double TsMaxAgeS = 24 * 3600;      // a node's own reading time is trusted up to this old
    public const double TsMaxAheadS = 60;           // ... and this far in the future (clock drift)
    public const double DisplayEveryS = 60;         // the thermostat's display feed: at most this often

    readonly DbFactory _sessions;
    readonly RefrigerantTables _tables;
    readonly ILogger _log;
    readonly double _hold, _silence, _cooldownS;
    double _stale;

    /// <summary>Seconds without a message before a node counts as offline (the demo backfill raises it).</summary>
    public double Stale
    {
        get => _stale;
        set => _stale = value;
    }
    readonly string _appUrl;
    readonly Dictionary<int, double> _displayAt = [];
    readonly HashSet<string> _unknownSites = [];
    DateTime _snapshotAt;

    /// <summary>(system_id, node) -> (epoch, data): each node's newest reading.</summary>
    public Dictionary<(int, string), (double At, JsonNode? Data)> Latest { get; } = [];

    /// <summary>Email; null = none (tests).</summary>
    public IMailer? Mailer { get; set; }

    /// <summary>Phone notifications; null = none (tests).</summary>
    public Pusher? Pusher { get; set; }

    /// <summary>(topic, payload) -> publish retained; null = no display feed or thermostat config.</summary>
    public Action<string, JsonNode>? Publish { get; set; }

    /// <summary>Runs the email sending; tests replace it to run inline.</summary>
    public Action<Action> Spawn { get; set; } = work => Task.Run(work);

    /// <summary>MQTT thread (Handle) vs. the once-a-minute sweep and the nightly prune.</summary>
    public object Lock { get; } = new();

    public Ingest(DbFactory sessions, RefrigerantTables tables, Settings settings, ILogger? log = null)
        : this(sessions, tables, settings.StaleSeconds, settings.AlertHoldSeconds, settings.AlertNoDataSeconds,
               settings.AlertEmailCooldownHours * 3600, settings.AppUrl, log)
    {
    }

    public Ingest(DbFactory sessions, RefrigerantTables tables, double stale = 30, double hold = 300, double silence = 600,
                  double cooldownS = 6 * 3600, string appUrl = "http://localhost:8000/app/", ILogger? log = null)
    {
        _sessions = sessions;
        _tables = tables;
        _stale = stale;
        _hold = hold;
        _silence = silence;
        _cooldownS = cooldownS;
        _appUrl = appUrl;
        _log = log ?? NullLogger.Instance;
    }

    // ---------- routing ----------

    /// <summary>Process one MQTT message. Returns what was stored, for logging and tests.</summary>
    public JsonNode? Handle(string topic, byte[] payload, double? now = null)
    {
        lock (Lock) return HandleLocked(topic, payload, now ?? Time.Now());
    }

    public JsonNode? Handle(string topic, string payload, double? now = null) => Handle(topic, Encoding.UTF8.GetBytes(payload), now);

    JsonNode? HandleLocked(string topic, byte[] payload, double now)
    {
        var parts = topic.Split('/');
        if (parts.Length != 4 || parts[0] != "hvac" || !AllNodes.Contains(parts[2])) return null;
        string site = parts[1], node = parts[2], kind = parts[3];
        JsonObject? data;
        try
        {
            data = PyJson.Loads(payload) as JsonObject;
        }
        catch (Exception e) when (e is JsonException or DecoderFallbackException or ArgumentException)
        {
            _log.LogWarning("bad JSON on {Topic}", topic);
            return null;
        }
        if (data is null) return null;

        JsonObject? snap = null, config = null;
        List<EmailJob> jobs = [];
        List<PushJob> pushes = [];
        (int, string)? show = null;
        string configTopic = "";
        using (var s = _sessions.Open())
        using (var tx = s.Database.BeginTransaction())
        {
            var system = s.Systems.FirstOrDefault(x => x.SiteId == site);
            if (system is null)
            {
                if (_unknownSites.Add(site)) _log.LogInformation("ignoring site {Site}: no system registered for it", site);
                return null;
            }
            var device = DeviceFor(s, system, node);
            JsonNode? result = null;
            bool done = true;
            if (kind == "telemetry" && Repeat(s, device, data, now))
                return null;
            if (kind == "telemetry" && node == Thermostat.Node)
                result = OnThermostat(s, device, data, ReadingTime(data, now));
            else if (kind == "telemetry" && node == Electrical.Node)
                result = OnElectrical(s, system, device, data, ReadingTime(data, now));
            else if (kind == "telemetry")
            {
                var at = ReadingTime(data, now);
                snap = OnTelemetry(s, system, device, data, at);
                var events = Alerts.Sync(s, system, J.Flags(snap), at, _hold, Alerts.Unjudged(snap));
                jobs = Alerts.EmailsFor(s, system, events, at, _cooldownS, _appUrl);
                pushes = Alerts.PushesFor(s, system, events, at, _cooldownS, _appUrl);
                show = DisplayDue(s, system, events.Count > 0, now);
                done = false;
            }
            else if (kind == "status")
                result = OnStatus(device, data, now);
            else if (kind == "reply")
                result = OnReply(s, device, data, now);
            else if (kind == "request" && node == Thermostat.Node)
            {
                config = OnThermostatRequest(s, system, data);
                configTopic = $"hvac/{system.SiteId}/{Thermostat.Node}/config";
                done = false;
            }
            s.SaveChanges();
            if (snap is not null)
            {
                Rollup.Refresh(s, system.Id, _snapshotAt);        // the minute's history/summary row
                s.SaveChanges();
            }
            tx.Commit();
            if (done) return result;
        }
        if (kind == "request")       // after commit: the stored version must exist before the thermostat sees it
        {
            if (config is not null) Publish?.Invoke(configTopic, config);
            return config;
        }
        Email(jobs);                 // after commit, so the alerts exist when emailed_at is written
        Push(pushes);
        if (show is (int id, string siteId)) PublishDisplay(id, siteId, now);
        return snap;
    }

    // ---------- the display thermostat's feed ----------

    (int, string)? DisplayDue(Db s, HvacSystem system, bool events, double now)
    {
        if (Publish is null || ThermostatState(s, system, now) is null) return null;
        if (events || now - _displayAt.GetValueOrDefault(system.Id, 0) >= DisplayEveryS) return (system.Id, system.SiteId);
        return null;
    }

    void PublishDisplay(int systemId, string site, double now)
    {
        _displayAt[systemId] = now;
        try
        {
            JsonObject feed;
            using (var s = _sessions.Open())
                feed = Display.Build(s, s.Systems.Find(systemId)!, now, _stale);
            Publish?.Invoke($"hvac/{site}/{Thermostat.Node}/display", feed);
        }
        catch (Exception e)
        {
            _log.LogError(e, "display feed for {Site} failed", site);
        }
    }

    /// <summary>When a reading was taken: the node's own "ts" (firmware 0.2+, UTC seconds) if it is
    /// plausible, so readings kept during an outage land where they belong; else arrival time.</summary>
    /// <summary>A reading we already have: the node sends again what the broker never echoed back after a
    /// connection died quietly (firmware 0.3.2+), and some of it may have arrived after all.</summary>
    public static bool Repeat(Db s, Device device, JsonObject data, double now)
    {
        double at = ReadingTime(data, now);      // stored a few ms later if that moment was taken (OnTelemetry)
        DateTime from = Db.Ts(at), to = Db.Ts(at + 0.01);
        return s.Telemetry.Where(t => t.DeviceId == device.Id && t.Time >= from && t.Time < to).Select(t => t.Data)
                .AsEnumerable().Any(d => JsonNode.DeepEquals(Db.Parse(d), data));
    }

    public static double ReadingTime(JsonObject data, double now)
    {
        if (J.Num(J.Get(data, "ts")) is Num v && now - TsMaxAgeS <= v.V && v.V <= now + TsMaxAheadS)
            return Math.Min(v.V, now);
        return now;
    }

    Device DeviceFor(Db s, HvacSystem system, string node)
    {
        var d = s.Devices.FirstOrDefault(x => x.SystemId == system.Id && x.Node == node);
        if (d is null)
        {
            d = new Device { SystemId = system.Id, Node = node };
            s.Devices.Add(d);
            s.SaveChanges();
            _log.LogInformation("new device: {Site}/{Node}", system.SiteId, node);
        }
        return d;
    }

    // ---------- alerts ----------

    /// <summary>Raise (or keep open) "no data" alerts for systems whose nodes have gone quiet.</summary>
    public void Sweep(double? now = null)
    {
        double t = now ?? Time.Now();
        var jobs = new List<EmailJob>();
        var pushes = new List<PushJob>();
        var shows = new List<(int, string)>();
        lock (Lock)
        {
            using (var s = _sessions.Open())
            using (var tx = s.Database.BeginTransaction())
            {
                foreach (var system in s.Systems.OrderBy(x => x.Id).ToList())
                {
                    var flags = Alerts.NoDataFlags(s, system, t, _silence);
                    if (flags is null) continue;
                    var events = Alerts.Sync(s, system, flags, t, _hold);
                    jobs.AddRange(Alerts.EmailsFor(s, system, events, t, _cooldownS, _appUrl));
                    pushes.AddRange(Alerts.PushesFor(s, system, events, t, _cooldownS, _appUrl));
                    if (events.Count > 0 && Publish is not null && ThermostatState(s, system, t) is not null)
                        shows.Add((system.Id, system.SiteId));
                }
                s.SaveChanges();
                tx.Commit();
            }
            foreach (var (id, site) in shows) PublishDisplay(id, site, t);
        }
        Email(jobs);
        Push(pushes);
    }

    void Push(List<PushJob> jobs)
    {
        if (jobs.Count > 0 && Pusher is not null) Spawn(() => SendPushes(jobs));
    }

    void SendPushes(List<PushJob> jobs)
    {
        foreach (var job in jobs)
        {
            int sent;
            try
            {
                sent = Pusher!.SendToUser(job.UserId, job.Message);
            }
            catch (Exception e)
            {
                _log.LogError(e, "push to user {User} failed", job.UserId);
                continue;
            }
            if (sent > 0 && job.AlertId is int id)
            {
                using var s = _sessions.Open();
                if (s.Alerts.Find(id) is Alert a)
                {
                    a.PushedAt = Db.UtcNow();
                    s.SaveChanges();
                }
            }
        }
    }

    void Email(List<EmailJob> jobs)
    {
        if (jobs.Count > 0 && Mailer is not null) Spawn(() => Send(jobs));
    }

    void Send(List<EmailJob> jobs)
    {
        foreach (var job in jobs)
        {
            if (Mailer!.Send(job.To, job.Subject, job.Body) && job.AlertId is int id)
            {
                using var s = _sessions.Open();
                if (s.Alerts.Find(id) is Alert a)
                {
                    a.EmailedAt = Db.UtcNow();
                    s.SaveChanges();
                }
            }
        }
    }

    // ---------- message kinds ----------

    JsonObject OnTelemetry(Db s, HvacSystem system, Device device, JsonObject data, double now)
    {
        // two readings on the same millisecond (both nodes catching up) would collide on the key
        while (s.Snapshots.Find(system.Id, Db.Ts(now)) is not null || s.Telemetry.Find(device.Id, Db.Ts(now)) is not null)
            now += 0.001;
        if (device.LastSeen is null || Db.Ts(now) > device.LastSeen) device.LastSeen = Db.Ts(now);
        device.Connected = true;
        CopyDeviceFields(device, data, "fw", "rssi");
        s.Telemetry.Add(new Telemetry { DeviceId = device.Id, Time = Db.Ts(now), Data = Db.Json(data) });
        Latest[(system.Id, device.Node)] = (now, data.DeepClone());

        var nodes = new JsonObject();
        foreach (var n in Calc.Nodes) nodes[n] = NodeState(s, system, n, now);
        var runStart = system.RunStartedAt;
        var prev = s.Snapshots.Where(x => x.SystemId == system.Id).OrderByDescending(x => x.Time)
                    .Select(x => new { x.Time, x.Mode }).FirstOrDefault();
        double? last = prev is null ? null : Db.Epoch(prev.Time);
        if (prev is not null) AddRuntime(s, system.Id, prev.Mode, now - last!.Value, now);
        if (last is null || now - last.Value > _stale) runStart = null;   // after an outage the call can't be assumed to have continued
        var (snap, newStart) = Calc.Compute(system.CalcConfig(), nodes, runStart is DateTime rs ? Db.Epoch(rs) : null, now, _tables);
        snap = Psychro.Apply(snap, nodes["indoor"], system.AtmPsia);         // indoor humidity (cloud only)
        var eq = s.Equipment.Find(system.Id)?.Info();
        snap = Equipment.Apply(snap, eq);                                     // nameplate targets (cloud only)
        snap = Airflow.Apply(snap, nodes["indoor"], eq);                      // static pressure (cloud only)
        snap = Electrical.Apply(snap, ElectricalState(s, system, now), eq);
        snap = Thermostat.Apply(snap, ThermostatState(s, system, now));
        system.RunStartedAt = newStart is double ns && ns != 0 ? Db.Ts(ns) : null;
        s.Snapshots.Add(new Snapshot { SystemId = system.Id, Time = Db.Ts(now), Mode = J.Str(snap, "mode") ?? "", Data = Db.Json(snap) });
        _snapshotAt = Db.Ts(now);
        return snap;
    }

    static void CopyDeviceFields(Device device, JsonObject data, params string[] keys)
    {
        foreach (var k in keys)
        {
            if (!data.ContainsKey(k)) continue;
            var v = data[k];
            switch (k)
            {
                case "fw": device.Fw = v is null ? null : Py.Str(v); break;
                case "ip": device.Ip = v is null ? null : Py.Str(v); break;
                case "mac": device.Mac = v is null ? null : Py.Str(v); break;
                case "rssi": device.Rssi = J.Num(v) is Num n ? (int)n.V : null; break;
            }
        }
    }

    JsonObject OnThermostat(Db s, Device device, JsonObject data, double now)
    {
        // no snapshot of its own: the next indoor or outdoor reading picks it up, with its flags
        while (s.Telemetry.Find(device.Id, Db.Ts(now)) is not null) now += 0.001;
        if (device.LastSeen is null || Db.Ts(now) > device.LastSeen) device.LastSeen = Db.Ts(now);
        device.Connected = true;
        CopyDeviceFields(device, data, "fw");
        s.Telemetry.Add(new Telemetry { DeviceId = device.Id, Time = Db.Ts(now), Data = Db.Json(data) });
        Latest[(device.SystemId, device.Node)] = (now, data.DeepClone());
        return data;
    }

    /// <summary>Null when the system has no thermostat (most don't).</summary>
    JsonObject? ThermostatState(Db s, HvacSystem system, double now)
    {
        var state = NodeState(s, system, Thermostat.Node, now);
        return state["data"] is null ? null : state;
    }

    /// <summary>Credit the time since the previous snapshot to its mode; a gap over RunGapS is an outage.</summary>
    static void AddRuntime(Db s, int systemId, string prevMode, double gap, double now)
    {
        if (prevMode == "idle" || !(0 < gap && gap <= RunGapS)) return;
        var day = DateOnly.FromDateTime(Db.Ts(now));
        var row = s.RuntimeDays.Find(systemId, day);
        if (row is null)
        {
            row = new RuntimeDay { SystemId = systemId, Day = day, BlowerS = 0.0, CompressorS = 0.0 };
            s.RuntimeDays.Add(row);
        }
        row.BlowerS += gap;
        if (prevMode is "cooling" or "heating") row.CompressorS += gap;
    }

    JsonObject OnElectrical(Db s, HvacSystem system, Device device, JsonObject data, double now)
    {
        if (s.Telemetry.Find(device.Id, Db.Ts(now)) is null)
            s.Telemetry.Add(new Telemetry { DeviceId = device.Id, Time = Db.Ts(now), Data = Db.Json(data) });
        if (device.LastSeen is null || Db.Ts(now) > device.LastSeen) device.LastSeen = Db.Ts(now);
        device.Connected = true;
        CopyDeviceFields(device, data, "fw", "rssi");
        Latest[(system.Id, Electrical.Node)] = (now, data.DeepClone());
        return data;
    }

    /// <summary>Null when this system has no electrical module, else its {"online", "data"}.</summary>
    JsonObject? ElectricalState(Db s, HvacSystem system, double now)
    {
        if (!Latest.ContainsKey((system.Id, Electrical.Node)) &&
            !s.Devices.Any(d => d.SystemId == system.Id && d.Node == Electrical.Node))
            return null;
        return NodeState(s, system, Electrical.Node, now);
    }

    JsonObject NodeState(Db s, HvacSystem system, string node, double now)
    {
        if (!Latest.TryGetValue((system.Id, node), out var e))
        {
            // after a restart, fall back to the stored reading
            var row = s.Telemetry.Join(s.Devices.Where(d => d.SystemId == system.Id && d.Node == node),
                                       t => t.DeviceId, d => d.Id, (t, d) => new { t.Time, t.Data })
                       .OrderByDescending(x => x.Time).FirstOrDefault();
            if (row is not null)
            {
                e = (Db.Epoch(row.Time), Db.Parse(row.Data));
                Latest[(system.Id, node)] = e;
            }
            else
            {
                return new JsonObject { ["online"] = false, ["data"] = null };
            }
        }
        return new JsonObject { ["online"] = now - e.At <= _stale, ["data"] = J.Copy(e.Data) };
    }

    static JsonObject OnStatus(Device device, JsonObject data, double now)
    {
        device.Connected = J.Truthy(data, "online");
        device.LastStatus = Db.Json(data);
        if (device.Connected)
        {
            device.LastSeen = Db.Ts(now);
            CopyDeviceFields(device, data, "fw", "ip", "mac", "rssi");
        }
        return data;
    }

    /// <summary>A change made on the thermostat's screen: store it and return the new config to send
    /// back (the thermostat accepts it because its version is newer). Null if it isn't valid.</summary>
    JsonObject? OnThermostatRequest(Db s, HvacSystem system, JsonObject data)
    {
        var row = s.ThermostatConfigs.Find(system.Id);
        if (row is null)
        {
            row = new ThermostatConfig { SystemId = system.Id, Settings = "{}", Tech = "{}", Version = 0, Sent = false };
            s.ThermostatConfigs.Add(row);
        }
        try
        {
            row.Settings = Db.Json(Thermostat.ApplyRequest(Db.Parse(row.Settings), data))!;
        }
        catch (ThermostatSettingsException e)
        {
            _log.LogWarning("thermostat request from {Site} ignored: {Error}", system.SiteId, e.Message);
            return null;
        }
        row.Version += 1;
        row.UpdatedAt = Db.UtcNow();
        row.Sent = true;
        return Thermostat.ConfigPayload(row.Version, Db.Parse(row.Settings), Db.Parse(row.Tech), system.HeatPump, system.ObEnergized);
    }

    /// <summary>Attach the reply to the newest sent, unanswered command with the same verb.
    /// Nodes answer within a second, so an older unanswered one was lost, not queued.</summary>
    static JsonObject OnReply(Db s, Device device, JsonObject data, double now)
    {
        var verb = J.Get(data, "cmd");
        var candidates = s.Commands.Where(c => c.DeviceId == device.Id && c.RepliedAt == null && c.Payload != null && c.Sent)
                          .OrderByDescending(c => c.Id).ToList();
        var cmd = candidates.FirstOrDefault(c => JsonEquals(J.Get(Db.Parse(c.Payload), "cmd"), verb));
        if (cmd is null)
        {
            cmd = new Command { DeviceId = device.Id, Payload = null, Sent = false };
            s.Commands.Add(cmd);
        }
        cmd.Reply = Db.Json(data);
        cmd.RepliedAt = Db.Ts(now);
        return data;
    }

    static bool JsonEquals(JsonNode? a, JsonNode? b) => JsonNode.DeepEquals(a, b);
}
