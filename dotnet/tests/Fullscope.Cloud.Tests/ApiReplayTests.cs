using System.IO.Compression;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Fullscope.Cloud.Api;
using Fullscope.Cloud.Data;
using Fullscope.Cloud.Ingest;
using Fullscope.Core;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;

namespace Fullscope.Cloud.Tests;

/// <summary>
/// About 230 requests to the Python web API, recorded by dotnet/tools/make_api_replay.py on a database
/// the Python ingest filled, sent to the C# API on a copy of that database with the clock set to
/// each request's time. Status, body, cookies, emails and MQTT messages must match.
/// </summary>
[Collection("clock")]
public partial class ApiReplayTests
{
    sealed class Recorder : IMailer
    {
        public readonly List<JsonObject> Emails = [], Published = [];

        public bool Enabled => true;

        public bool Send(string to, string subject, string body)
        {
            Emails.Add(new JsonObject { ["to"] = to, ["subject"] = subject, ["body"] = body });
            return true;
        }

        public bool Publish(string topic, JsonNode payload, bool retain)
        {
            Published.Add(new JsonObject { ["topic"] = topic, ["payload"] = Py.Dumps(payload, sortKeys: true), ["retain"] = retain });
            return true;
        }
    }

    static string Vector(string name) => Path.Combine(AppContext.BaseDirectory, "vectors", name);

    [GeneratedRegex(@"#/(invite|reset)/([A-Za-z0-9_-]+)")]
    private static partial Regex Token();

    [GeneratedRegex(@"\{(tok\d+)\}")]
    private static partial Regex Placeholder();

    /// <summary>A copy of the seed database with the per-minute rollup filled in, as `fullscope ingest` does at start.</summary>
    internal static DbFactory Seed(out string path)
    {
        path = Path.Combine(Path.GetTempPath(), $"fullscope-api-{Guid.NewGuid():N}.db");
        using (var gz = new GZipStream(File.OpenRead(Vector("api_seed.db.gz")), CompressionMode.Decompress))
        using (var f = File.Create(path))
            gz.CopyTo(f);
        var sessions = DbFactory.FromUrl("sqlite:///" + path).Init();
        using var s = sessions.Open();
        foreach (var id in s.Systems.Select(x => x.Id).ToList()) Rollup.Backfill(sessions, id);
        return sessions;
    }

    [Fact]
    public async Task MatchesPython()
    {
        var replay = JsonNode.Parse(File.ReadAllText(Vector("api_replay.json")))!;
        var sessions = Seed(out var dbPath);
        var oldClock = Time.Clock;
        var oldZone = Time.LocalZone;
        Time.LocalZone = TimeZoneInfo.Utc;
        var rec = new Recorder();
        var settings = new Settings { AppUrl = (string)replay["app_url"]!, OrderEmail = (string?)replay["order_email"] ?? "", SessionDays = 30, WebDir = "" };
        var api = new ApiServer(sessions, settings, rec.Publish, rec, stale: 30, sendAsync: false, cache: false);
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.UseTestServer();
        await using var app = builder.Build();
        api.Map(app);
        await app.StartAsync();
        var client = app.GetTestClient();
        var cookies = new Dictionary<string, string>();
        var tokens = new Dictionary<string, string>();          // actual token -> tokN
        var failures = new List<string>();
        string Norm(string text) => Token().Replace(text, m =>
        {
            var t = m.Groups[2].Value;
            if (!tokens.TryGetValue(t, out var name)) tokens[t] = name = $"tok{tokens.Count + 1}";
            return $"#/{m.Groups[1].Value}/<{name}>";
        });
        string Fill(string text) => Placeholder().Replace(text, m => tokens.First(kv => kv.Value == m.Groups[1].Value).Key);
        try
        {
            int i = 0;
            foreach (var step in replay["steps"]!.AsArray())
            {
                var at = Time.FromEpoch((double)step!["t"]!);
                Time.Clock = () => at;
                string actor = (string)step["actor"]!, method = (string)step["method"]!, path = Fill((string)step["path"]!);
                var req = new HttpRequestMessage(new HttpMethod(method), path);
                if (step["headers"] is JsonObject hs)
                    foreach (var (k, v) in hs) req.Headers.TryAddWithoutValidation(k, (string)v!);
                else if (actor != "anon")
                    req.Headers.Add("X-Requested-With", "fullscope");
                if (actor is "key1" or "key2") req.Headers.Add("X-API-Key", (string)replay["keys"]![actor]!);
                if (cookies.TryGetValue(actor, out var cookie)) req.Headers.Add("Cookie", $"{Auth.Cookie}={cookie}");
                if (step["raw"] is JsonValue raw)
                    req.Content = new ByteArrayContent(Encoding.UTF8.GetBytes(Fill((string)raw!)));
                else if (step["body"] is JsonValue body)
                {
                    req.Content = new ByteArrayContent(Encoding.UTF8.GetBytes(Fill((string)body!)));
                    req.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
                }
                int e0 = rec.Emails.Count, p0 = rec.Published.Count;
                var res = await client.SendAsync(req);
                var text = await res.Content.ReadAsStringAsync();
                string where = $"step {i} {actor} {method} {step["path"]}";
                i++;

                int status = (int)res.StatusCode, wantStatus = (int)step["status"]!;
                if (status != wantStatus)
                {
                    failures.Add($"{where}: status {status}, expected {wantStatus}: {text}");
                    continue;
                }
                var type = res.Content.Headers.ContentType?.MediaType ?? "";
                if (type != (string)step["type"]!) failures.Add($"{where}: content type {type}, expected {step["type"]}");
                string answer = type == "application/json" ? Norm(Py.Dumps(PyJson.Loads(text), sortKeys: true)) : Norm(text);
                string want = (string)step["answer"]!;
                var compare = (string)step["compare"]!;
                bool listDetail = type == "application/json" && JsonNode.Parse(want) is JsonObject wo && wo["detail"] is JsonArray;
                if (listDetail)
                {
                    if (PyJson.Loads(text) is not JsonObject go || go["detail"] is not JsonArray)
                        failures.Add($"{where}: expected a list of validation errors, got {text}");
                }
                else if (compare == "exact" && answer != want)
                    failures.Add($"{where}:\n  got      {answer[..Math.Min(answer.Length, 4000)]}\n  expected {want[..Math.Min(want.Length, 4000)]}");

                string? cookieAction = null;
                if (res.Headers.TryGetValues("Set-Cookie", out var setCookies))
                {
                    foreach (var sc in setCookies.Where(c => c.StartsWith(Auth.Cookie + "=", StringComparison.Ordinal)))
                    {
                        var value = sc[(Auth.Cookie.Length + 1)..].Split(';')[0];
                        bool cleared = value.Length == 0 || sc.Contains("max-age=0", StringComparison.OrdinalIgnoreCase)
                                       || sc.Contains("expires=Thu, 01 Jan 1970", StringComparison.OrdinalIgnoreCase);
                        cookieAction = cleared ? "cleared" : "set";
                        if (cleared) cookies.Remove(actor);
                        else cookies[actor] = value;
                    }
                }
                if (cookieAction != (string?)step["cookie"]) failures.Add($"{where}: cookie {cookieAction ?? "none"}, expected {step["cookie"] ?? "none"}");

                var emails = new JsonArray(rec.Emails.Skip(e0).Select(e => PyJson.Loads(Norm(Py.Dumps(e)))).ToArray());
                if (Py.Dumps(emails, sortKeys: true) != Py.Dumps(step["emails"], sortKeys: true))
                    failures.Add($"{where}: emails\n  got      {Py.Dumps(emails, sortKeys: true)}\n  expected {Py.Dumps(step["emails"], sortKeys: true)}");
                var published = new JsonArray(rec.Published.Skip(p0).Select(p => (JsonNode?)p.DeepClone()).ToArray());
                if (Py.Dumps(published, sortKeys: true) != Py.Dumps(step["published"], sortKeys: true))
                    failures.Add($"{where}: published\n  got      {Py.Dumps(published, sortKeys: true)}\n  expected {Py.Dumps(step["published"], sortKeys: true)}");
            }
        }
        finally
        {
            Time.Clock = oldClock;
            Time.LocalZone = oldZone;
            Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
            File.Delete(dbPath);
        }
        Assert.True(failures.Count == 0, $"{failures.Count} differences:\n" + string.Join("\n", failures.Take(25)));
    }
}
