using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Bench;
using Fullscope.Core;
using Microsoft.Data.Sqlite;

namespace Fullscope.Cloud.Tests;

/// <summary>
/// 80 minutes of MQTT traffic and dashboard calls recorded from the Python bench dashboard
/// (dotnet/tools/make_bench_replay.py), replayed through BenchHub with the clock set per step.
/// </summary>
[Collection("clock")]
public class BenchReplayTests
{
    static string Sorted(JsonNode? n) => Py.Dumps(n, sortKeys: true);

    [Fact]
    public void MatchesPython()
    {
        JsonNode replay;
        using (var gz = new GZipStream(File.OpenRead(Path.Combine(AppContext.BaseDirectory, "vectors", "bench_replay.json.gz")), CompressionMode.Decompress))
        using (var r = new StreamReader(gz))
            replay = PyJson.Loads(r.ReadToEnd())!;
        var dir = Path.Combine(Path.GetTempPath(), $"fullscope-bench-{Guid.NewGuid():N}");
        Directory.CreateDirectory(dir);
        File.WriteAllText(Path.Combine(dir, "config.json"), (string)replay["start_config"]!);
        var oldClock = Time.Clock;
        var oldZone = Time.LocalZone;
        Time.LocalZone = TimeZoneInfo.Utc;
        var failures = new List<string>();
        try
        {
            var steps = replay["steps"]!.AsArray();
            Time.Clock = () => Time.FromEpoch((double)steps[0]!["t"]!);
            using var hub = new BenchHub(dir, RefrigerantTables.Load());
            var sent = new List<JsonObject>();
            hub.Publish = (topic, payload) =>
            {
                sent.Add(new JsonObject { ["topic"] = topic, ["payload"] = payload });
                return true;
            };
            var pushed = Channel(hub);
            int i = 0;
            foreach (var step in steps)
            {
                var at = Time.FromEpoch((double)step!["t"]!);
                Time.Clock = () => at;
                var kind = (string)step["kind"]!;
                int s0 = sent.Count;
                string? result = null;
                if (kind.Contains('/')) hub.OnMessage(kind, Encoding.UTF8.GetBytes((string)step["payload"]!));
                else if (kind == "state") result = Sorted(hub.State());
                else if (kind == "history") result = Sorted(hub.History(Py.NumberOf(step["arg"])!.Value.V));
                else if (kind == "export_csv") result = hub.ExportCsv(Py.NumberOf(step["arg"])!.Value.V);
                else if (kind == "set_config") hub.SetConfig(step["arg"]!.AsObject());
                else if (kind == "send_cmd")
                {
                    var (ok, msg) = hub.SendCmd((string)step["arg"]![0]!, step["arg"]![1]!.AsObject());
                    result = Sorted(new JsonArray(ok, msg));
                }
                result ??= "null";
                var where = $"step {i} {kind} at {step["t"]}";
                i++;
                if (result != (string)step["result"]!)
                    failures.Add($"{where}:\n  got      {result[..Math.Min(3000, result.Length)]}\n  expected {((string)step["result"]!)[..Math.Min(3000, ((string)step["result"]!).Length)]}");
                var got = new List<string>();
                while (pushed.Reader.TryRead(out var p)) got.Add(Sorted(PyJson.Loads(p)));
                var hash = got.Count == 0 ? "" : Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(string.Join("\n", got))))[..16];
                if (got.Count != (int)step["pushed_n"]! || hash != (string)step["pushed"]!)
                    failures.Add($"{where}: pushed {got.Count} updates (hash {hash}), expected {step["pushed_n"]} ({step["pushed"]}); first: {got.FirstOrDefault()?[..Math.Min(1500, got.First().Length)]}");
                var gotSent = Py.Dumps(new JsonArray(sent.Skip(s0).Select(x => (JsonNode?)x.DeepClone()).ToArray()));
                // Python publishes json.dumps(cmd) (", " separators); compare parsed.
                var wantSent = new JsonArray(step["sent"]!.AsArray().Select(x => (JsonNode?)new JsonObject
                {
                    ["topic"] = x!["topic"]!.DeepClone(), ["payload"] = x["payload"]!.DeepClone(),
                }).ToArray());
                if (gotSent != Py.Dumps(wantSent)) failures.Add($"{where}: sent {gotSent}, expected {Py.Dumps(wantSent)}");
                if (failures.Count > 25) break;
            }
            var final = replay["final"]!;
            Assert.True(failures.Count == 0, $"{failures.Count} differences:\n" + string.Join("\n", failures.Take(25)));
            Assert.Equal((string)final["config"]!, File.ReadAllText(Path.Combine(dir, "config.json")));
            hub.Dispose();
            using var db = new SqliteConnection($"Data Source={Path.Combine(dir, "hvac_data.db")}");
            db.Open();
            Assert.Equal(Py.Dumps(final["telemetry"]), Py.Dumps(Rows(db, "SELECT ts, node, data FROM telemetry ORDER BY rowid")));
            Assert.Equal(Py.Dumps(final["snapshots"]), Py.Dumps(Rows(db, "SELECT ts, data FROM snapshots ORDER BY rowid")));
        }
        finally
        {
            Time.Clock = oldClock;
            Time.LocalZone = oldZone;
            SqliteConnection.ClearAllPools();
            Directory.Delete(dir, true);
        }
    }

    static System.Threading.Channels.Channel<string> Channel(BenchHub hub) => hub.Subscribe();

    static JsonArray Rows(SqliteConnection db, string sql)
    {
        using var c = db.CreateCommand();
        c.CommandText = sql;
        using var r = c.ExecuteReader();
        var rows = new JsonArray();
        while (r.Read())
        {
            var row = new JsonArray();
            for (int k = 0; k < r.FieldCount; k++)
                row.Add(r.GetFieldType(k) == typeof(double) ? JsonValue.Create(r.GetDouble(k)) : JsonValue.Create(r.GetString(k)));
            rows.Add(row);
        }
        return rows;
    }
}
