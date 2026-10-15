using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using Fullscope.Cloud.Api;
using Fullscope.Core;
using Microsoft.Data.Sqlite;

namespace Fullscope.Cloud.Bench;

/// <summary>
/// The PC bench dashboard's live state (Hub in hvac-monitor-app/server.py): the newest message per
/// node, the calculated snapshot, history in hvac_data.db and updates pushed to open browsers.
/// Uses the same config.json and database as the Python version.
/// </summary>
public sealed class BenchHub : IDisposable
{
    public static readonly string[] Nodes = Calc.Nodes;
    static readonly string[] Editable = ["refrigerant", "heat_pump", "ob_energized", "atm_psia"];
    static readonly string[] HistoryKeys = ["p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc", "oat", "t_sup", "t_ret", "dt"];

    public static JsonObject Defaults() => new()
    {
        ["mqtt_host"] = "localhost", ["mqtt_port"] = 1883, ["mqtt_user"] = "", ["mqtt_pass"] = "",
        ["site"] = "home", ["web_port"] = 8080,
        ["refrigerant"] = "R-410A",
        ["heat_pump"] = true,
        ["ob_energized"] = "cool",     // "cool" = O terminal (most brands), "heat" = B terminal
        ["atm_psia"] = 14.7,           // local atmospheric pressure; ~14.0 at 1,200 ft elevation
        ["stale_seconds"] = 30,
        ["keep_days"] = 90,
    };

    readonly Lock _lock = new();
    readonly string _configPath;
    readonly SqliteConnection _db;
    readonly RefrigerantTables _tables;
    readonly Dictionary<string, (double Ts, JsonObject Data)> _latest = [];
    readonly Dictionary<string, JsonNode?> _status = [];
    readonly List<JsonObject> _replies = [];
    readonly List<Channel<string>> _clients = [];
    JsonObject _derived = [];
    double? _runStart;
    double _lastPrune;

    public JsonObject Config { get; }

    /// <summary>(topic, payload) -> sent; null in demo mode.</summary>
    public Func<string, string, bool>? Publish { get; set; }

    public BenchHub(string dir, RefrigerantTables tables)
    {
        _configPath = Path.Combine(dir, "config.json");
        _tables = tables;
        Config = LoadConfig(_configPath);
        _db = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = Path.Combine(dir, "hvac_data.db") }.ToString());
        _db.Open();
        Exec("CREATE TABLE IF NOT EXISTS telemetry (ts REAL, node TEXT, data TEXT)");
        Exec("CREATE TABLE IF NOT EXISTS snapshots (ts REAL, data TEXT)");
        Exec("CREATE INDEX IF NOT EXISTS ix_snap_ts ON snapshots(ts)");
        Exec("CREATE INDEX IF NOT EXISTS ix_tel_ts ON telemetry(ts)");
    }

    public void Dispose() => _db.Dispose();

    void Exec(string sql, params (string, object)[] args)
    {
        using var c = _db.CreateCommand();
        c.CommandText = sql;
        foreach (var (k, v) in args) c.Parameters.AddWithValue(k, v);
        c.ExecuteNonQuery();
    }

    // ---------- config.json ----------

    static JsonObject LoadConfig(string path)
    {
        var cfg = Defaults();
        if (File.Exists(path))
        {
            if (PyJson.Loads(File.ReadAllText(path)) is JsonObject file)
                foreach (var (k, v) in file) cfg[k] = v?.DeepClone();
        }
        else SaveConfig(path, cfg);
        return cfg;
    }

    /// <summary>json.dump(cfg, f, indent=2).</summary>
    static void SaveConfig(string path, JsonObject cfg)
    {
        var sb = new StringBuilder("{");
        bool first = true;
        foreach (var (k, v) in cfg)
        {
            sb.Append(first ? "\n  " : ",\n  ").Append(Py.Dumps(JsonValue.Create(k))).Append(": ").Append(Py.Dumps(v));
            first = false;
        }
        sb.Append(first ? "}" : "\n}");
        File.WriteAllText(path, sb.ToString());
    }

    double Stale => Py.NumberOf(Config["stale_seconds"])?.V ?? 30;
    double KeepDays => Py.NumberOf(Config["keep_days"])?.V ?? 90;
    public string Site => J.Str(Config, "site") ?? "home";

    CalcConfig CalcConfig() => new(
        J.Str(Config, "refrigerant") ?? "R-410A", Py.Truthy(Config["heat_pump"]),
        J.Str(Config, "ob_energized") ?? "cool", Py.NumberOf(Config["atm_psia"])?.V ?? 14.7);

    // ---------- incoming data ----------

    public void OnTelemetry(string node, JsonObject data)
    {
        double now = Time.Now();
        // Firmware 0.2+ stamps readings ("ts") and sends the ones kept during an outage on reconnect:
        // store those at their own time (if plausible) so history has no burst; "online" uses arrival.
        double at = Py.NumberOf(data["ts"]) is Num ts && now - 86400 <= ts.V && ts.V <= now + 60 ? Math.Min(ts.V, now) : now;
        lock (_lock)
        {
            _latest[node] = (now, data);
            using var tx = _db.BeginTransaction();
            Exec("INSERT INTO telemetry VALUES ($t, $n, $d)", ("$t", at), ("$n", node), ("$d", Py.Dumps(data)));
            var snap = Compute(now);
            _derived = snap;
            Exec("INSERT INTO snapshots VALUES ($t, $d)", ("$t", at), ("$d", Py.Dumps(snap)));
            Prune(now);
            tx.Commit();
        }
        PushState();
    }

    public void OnStatus(string node, JsonNode? data)
    {
        lock (_lock) _status[node] = data;
        PushState();
    }

    public void OnReply(string node, JsonNode? data)
    {
        var entry = new JsonObject { ["ts"] = Time.Now(), ["node"] = node, ["reply"] = data };
        lock (_lock)
        {
            _replies.Add(entry);
            if (_replies.Count > 30) _replies.RemoveRange(0, _replies.Count - 30);
        }
        Push(new JsonObject { ["type"] = "reply", ["entry"] = entry.DeepClone() });
    }

    /// <summary>An MQTT message on hvac/&lt;site&gt;/&lt;node&gt;/&lt;kind&gt;.</summary>
    public void OnMessage(string topic, byte[] payload)
    {
        var parts = topic.Split('/');
        if (parts.Length != 4) return;
        string node = parts[2], kind = parts[3];
        if (!Nodes.Contains(node)) return;              // e.g. the cloud-only electrical module
        JsonNode? data;
        try
        {
            data = PyJson.Loads(payload);
        }
        catch (Exception)
        {
            return;
        }
        if (kind == "telemetry" && data is JsonObject obj) OnTelemetry(node, obj);
        else if (kind == "status") OnStatus(node, data);
        else if (kind == "reply") OnReply(node, data);
    }

    void Prune(double now)
    {
        if (now - _lastPrune < 3600) return;
        _lastPrune = now;
        double cutoff = now - KeepDays * 86400;
        Exec("DELETE FROM telemetry WHERE ts < $c", ("$c", cutoff));
        Exec("DELETE FROM snapshots WHERE ts < $c", ("$c", cutoff));
    }

    // ---------- calculations ----------

    JsonObject NodesView(double now)
    {
        var nodes = new JsonObject();
        foreach (var n in Nodes)
        {
            bool has = _latest.TryGetValue(n, out var e);
            nodes[n] = new JsonObject { ["online"] = has && now - e.Ts <= Stale, ["data"] = has ? e.Data.DeepClone() : null };
        }
        return nodes;
    }

    JsonObject Compute(double now)
    {
        var (snap, runStart) = Calc.Compute(CalcConfig(), NodesView(now), _runStart, now, _tables);
        _runStart = runStart;
        return snap;
    }

    // ---------- outputs ----------

    public JsonObject State()
    {
        lock (_lock)
        {
            double now = Time.Now();
            var nodes = new JsonObject();
            foreach (var n in Nodes)
            {
                bool has = _latest.TryGetValue(n, out var e);
                nodes[n] = new JsonObject
                {
                    ["online"] = has && now - e.Ts <= Stale,
                    ["age"] = has ? Py.Round(now - e.Ts, 1) : null,
                    ["data"] = has ? e.Data.DeepClone() : null,
                    ["status"] = _status.GetValueOrDefault(n)?.DeepClone(),
                };
            }
            var config = new JsonObject();
            foreach (var k in Editable) config[k] = Config[k]?.DeepClone();
            return new JsonObject
            {
                ["ts"] = now, ["nodes"] = nodes, ["derived"] = _derived.DeepClone(), ["config"] = config,
                ["refrigerants"] = new JsonArray(_tables.Available().Select(x => (JsonNode?)x).ToArray()), ["pt_error"] = null,
                ["replies"] = new JsonArray(_replies.TakeLast(10).Select(x => (JsonNode?)x.DeepClone()).ToArray()),
                ["demo"] = Publish is null,
            };
        }
    }

    List<(double Ts, string Data)> Snapshots(double minutes)
    {
        double since = Time.Now() - minutes * 60;
        var rows = new List<(double, string)>();
        lock (_lock)
        {
            using var c = _db.CreateCommand();
            c.CommandText = "SELECT ts, data FROM snapshots WHERE ts >= $s ORDER BY ts";
            c.Parameters.AddWithValue("$s", since);
            using var r = c.ExecuteReader();
            while (r.Read()) rows.Add((r.GetDouble(0), r.GetString(1)));
        }
        return rows;
    }

    /// <summary>Averaged into at most ~600 points per series.</summary>
    public JsonArray History(double minutes)
    {
        long bucket = Math.Max(1, (long)(minutes * 60 / 600));
        var outRows = new JsonArray();
        var acc = new List<(string Key, PySum Sum, int N)>();     // in the order keys first appear, like a dict
        long? cur = null;
        void Flush(long b)
        {
            var row = new JsonObject { ["ts"] = b * bucket };
            foreach (var (k, sum, n) in acc) row[k] = Py.Round(sum.Value / n, 1);
            outRows.Add(row);
            acc.Clear();
        }
        void Add(string key, Num v)
        {
            int i = acc.FindIndex(x => x.Key == key);
            if (i < 0)
            {
                acc.Add((key, new PySum(), 0));
                i = acc.Count - 1;
            }
            var (k, sum, n) = acc[i];
            sum.Add(v);
            acc[i] = (k, sum, n + 1);
        }
        foreach (var (ts, text) in Snapshots(minutes))
        {
            var d = PyJson.Loads(text) as JsonObject ?? [];
            long b = (long)Py.FloorDiv(ts, bucket);
            if (cur is long c && b != c) Flush(c);
            cur = b;
            foreach (var k in HistoryKeys)
                if (J.Num(d[k]) is Num v) Add(k, v);
            Add("_on", new Num(J.Str(d, "mode") is "cooling" or "heating" ? 1 : 0, true));
        }
        if (acc.Count > 0) Flush(cur!.Value);
        return outRows;
    }

    public string ExportCsv(double minutes)
    {
        var sb = new StringBuilder();
        ApiServer.CsvRow(sb, ["time", .. ApiServer.CsvCols]);
        foreach (var (ts, text) in Snapshots(minutes))
        {
            var d = PyJson.Loads(text) as JsonObject ?? [];
            var local = TimeZoneInfo.ConvertTimeFromUtc(Time.FromEpoch(Math.Floor(ts)), Time.LocalZone);
            ApiServer.CsvRow(sb, [local.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture), .. ApiServer.CsvCols.Select(c => ApiServer.CsvCell(d[c]))]);
        }
        return sb.ToString();
    }

    public (bool Ok, string Message) SendCmd(string node, JsonObject cmd)
    {
        if (Publish is null) return (false, "Demo mode: commands are not sent");
        var topic = $"hvac/{Site}/{node}/cmd";
        if (!Publish(topic, Py.Dumps(cmd))) return (false, "Not sent: the MQTT broker is not connected");
        return (true, "sent");
    }

    public void SetConfig(JsonObject changes)
    {
        lock (_lock)
        {
            foreach (var (k, v) in changes)
            {
                if (!Editable.Contains(k)) continue;
                bool ok = k switch
                {
                    "refrigerant" or "ob_energized" => v is JsonValue s && Py.Classify(s) == Py.Kind.String,
                    "atm_psia" => J.Num(v) is not null,
                    _ => true,
                };
                if (ok) Config[k] = v?.DeepClone();       // a wrong type is ignored (Python stored it and then failed)
            }
            SaveConfig(_configPath, Config);
            _derived = Compute(Time.Now());
        }
        PushState();
    }

    // ---------- browsers (server-sent events) ----------

    public Channel<string> Subscribe()
    {
        var ch = Channel.CreateBounded<string>(new BoundedChannelOptions(100) { FullMode = BoundedChannelFullMode.DropWrite });
        lock (_clients) _clients.Add(ch);
        return ch;
    }

    public void Unsubscribe(Channel<string> ch)
    {
        lock (_clients) _clients.Remove(ch);
    }

    void PushState() => Push(new JsonObject { ["type"] = "state", ["state"] = State() });

    void Push(JsonObject msg)
    {
        var data = Py.Dumps(msg);
        List<Channel<string>> clients;
        lock (_clients) clients = [.. _clients];
        foreach (var c in clients) c.Writer.TryWrite(data);
    }
}
