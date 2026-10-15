using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Cloud.Ingest;
using Fullscope.Core;
using Microsoft.EntityFrameworkCore;

namespace Fullscope.Cloud.Tests;

/// <summary>
/// Four simulated hours of MQTT traffic for three systems, recorded from the Python ingest worker by
/// dotnet/tools/make_ingest_replay.py, replayed through the C# one on a copy of the same database.
/// </summary>
public class ReplayTests
{
    sealed class Mailer(List<JsonObject> log) : IMailer
    {
        public bool Enabled => true;

        public bool Send(string to, string subject, string body)
        {
            log.Add(new JsonObject { ["to"] = to, ["subject"] = subject, ["body"] = body });
            return true;
        }
    }

    static string Vector(string name) => Path.Combine(AppContext.BaseDirectory, "vectors", name);

    public static string H(JsonNode? n) =>
        Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(Py.Dumps(n, sortKeys: true))))[..16];

    /// <summary>Python's datetime.isoformat(sep=" ") of a naive UTC time.</summary>
    public static string? Iso(DateTime? t) => t is null ? null
        : t.Value.ToString(t.Value.Ticks % TimeSpan.TicksPerSecond == 0 ? "yyyy-MM-dd HH:mm:ss" : "yyyy-MM-dd HH:mm:ss.ffffff", CultureInfo.InvariantCulture);

    [Fact]
    public void MatchesPython()
    {
        var replay = JsonNode.Parse(File.ReadAllText(Vector("replay.json")))!;
        var dbPath = Path.Combine(Path.GetTempPath(), $"fullscope-replay-{Guid.NewGuid():N}.db");
        File.Copy(Vector("replay_seed.db"), dbPath);
        var old = Alerts.LocalZone;
        Alerts.LocalZone = TimeZoneInfo.Utc;
        try
        {
            var sessions = DbFactory.FromUrl("sqlite:///" + dbPath).Init();
            var emails = new List<JsonObject>();
            var published = new List<JsonObject>();
            var ingest = new Ingest.Ingest(sessions, RefrigerantTables.Load(), appUrl: (string)replay["app_url"]!)
            {
                Mailer = new Mailer(emails),
                Spawn = work => work(),
                Publish = (topic, payload) => published.Add(new JsonObject { ["topic"] = topic, ["payload"] = H(payload) }),
            };
            int n = 0;
            foreach (var step in replay["steps"]!.AsArray())
            {
                int e0 = emails.Count, p0 = published.Count;
                double t = (double)step!["t"]!;
                string topic = (string)step["topic"]!;
                JsonNode? result = null;
                if (topic == "sweep") ingest.Sweep(t);
                else result = ingest.Handle(topic, (string)step["payload"]!, t);
                var where = $"step {n} ({topic} at {t})";
                Assert.True(H(result) == (string?)step["result"] || (result is null && step["result"] is null),
                            $"{where}: result {Py.Dumps(result, sortKeys: true)}");
                Assert.Equal(Py.Dumps(step["emails"]), Py.Dumps(new JsonArray(emails.Skip(e0).Select(x => (JsonNode?)x.DeepClone()).ToArray())));
                Assert.Equal(Py.Dumps(step["published"]), Py.Dumps(new JsonArray(published.Skip(p0).Select(x => (JsonNode?)x.DeepClone()).ToArray())));
                n++;
            }
            var final = replay["final"]!;
            var got = Dump(sessions);
            foreach (var table in new[] { "systems", "devices", "telemetry", "snapshots", "alerts", "runtime_days", "commands", "thermostat_configs" })
                Assert.True(Py.Dumps(final[table], sortKeys: true) == Py.Dumps(got[table], sortKeys: true),
                            $"{table} differs:\nexpected {Py.Dumps(final[table], sortKeys: true)[..Math.Min(3000, Py.Dumps(final[table]).Length)]}\n" +
                            $"got      {Py.Dumps(got[table], sortKeys: true)[..Math.Min(3000, Py.Dumps(got[table]).Length)]}");
        }
        finally
        {
            Alerts.LocalZone = old;
            Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
            foreach (var ext in new[] { "", "-wal", "-shm" }) File.Delete(dbPath + ext);
        }
    }

    static JsonObject Dump(DbFactory sessions)
    {
        using var s = sessions.Open();
        JsonNode? J(string? text) => Db.Parse(text);
        return new JsonObject
        {
            ["systems"] = new JsonArray(s.Systems.OrderBy(x => x.Id).AsEnumerable()
                .Select(x => (JsonNode?)new JsonObject { ["id"] = x.Id, ["run_started_at"] = Iso(x.RunStartedAt) }).ToArray()),
            ["devices"] = new JsonArray(s.Devices.OrderBy(x => x.Id).AsEnumerable().Select(d => (JsonNode?)new JsonObject
            {
                ["id"] = d.Id, ["system_id"] = d.SystemId, ["node"] = d.Node, ["fw"] = d.Fw, ["ip"] = d.Ip, ["mac"] = d.Mac,
                ["rssi"] = d.Rssi, ["connected"] = d.Connected, ["last_seen"] = Iso(d.LastSeen), ["last_status"] = J(d.LastStatus),
            }).ToArray()),
            ["telemetry"] = H(new JsonArray(s.Telemetry.OrderBy(x => x.DeviceId).ThenBy(x => x.Time).AsEnumerable()
                .Select(t => (JsonNode?)new JsonArray(t.DeviceId, Iso(t.Time), J(t.Data))).ToArray())),
            ["snapshots"] = new JsonArray(s.Snapshots.OrderBy(x => x.SystemId).ThenBy(x => x.Time).AsEnumerable()
                .Select(x => (JsonNode?)new JsonArray(x.SystemId, Iso(x.Time), x.Mode, H(J(x.Data)))).ToArray()),
            ["alerts"] = new JsonArray(s.Alerts.OrderBy(x => x.Id).AsEnumerable().Select(a => (JsonNode?)new JsonObject
            {
                ["id"] = a.Id, ["system_id"] = a.SystemId, ["code"] = a.Code, ["node"] = a.Node, ["level"] = a.Level, ["text"] = a.Text,
                ["started_at"] = Iso(a.StartedAt), ["last_seen"] = Iso(a.LastSeen), ["raised_at"] = Iso(a.RaisedAt),
                ["cleared_at"] = Iso(a.ClearedAt), ["emailed"] = a.EmailedAt is not null, ["acked_at"] = Iso(a.AckedAt),
            }).ToArray()),
            ["runtime_days"] = new JsonArray(s.RuntimeDays.OrderBy(x => x.SystemId).ThenBy(x => x.Day).AsEnumerable()
                .Select(x => (JsonNode?)new JsonArray(x.SystemId, x.Day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
                                                      Py.Round(x.BlowerS, 6), Py.Round(x.CompressorS, 6))).ToArray()),
            ["commands"] = new JsonArray(s.Commands.OrderBy(x => x.Id).AsEnumerable().Select(c => (JsonNode?)new JsonObject
            {
                ["id"] = c.Id, ["device_id"] = c.DeviceId, ["payload"] = J(c.Payload), ["sent"] = c.Sent, ["reply"] = J(c.Reply),
                ["replied_at"] = Iso(c.RepliedAt),
            }).ToArray()),
            ["thermostat_configs"] = new JsonArray(s.ThermostatConfigs.OrderBy(x => x.SystemId).AsEnumerable().Select(c => (JsonNode?)new JsonObject
            {
                ["system_id"] = c.SystemId, ["settings"] = J(c.Settings), ["tech"] = J(c.Tech), ["version"] = c.Version, ["sent"] = c.Sent,
            }).ToArray()),
        };
    }
}
