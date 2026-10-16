using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Mqtt;
using Fullscope.Core;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;

namespace Fullscope.Cloud.Bench;

/// <summary>
/// fullscope bench [--demo] [--no-browser] [--dir hvac-monitor-app]: the PC dashboard on
/// http://localhost:8080 (python server.py). Settings come from config.json in that folder.
/// </summary>
public static class BenchHost
{
    public static int Run(Settings settings, string[] args, ILoggerFactory logs, CancellationToken stop)
    {
        var dir = Arg(args, "--dir") ?? FindDir(settings.Home);
        if (dir is null || !File.Exists(Path.Combine(dir, "web", "index.html")))
        {
            Console.Error.WriteLine("Can't find the dashboard folder (hvac-monitor-app with web/index.html); pass --dir <folder>.");
            return 2;
        }
        var log = logs.CreateLogger("bench");
        using var hub = new BenchHub(dir, RefrigerantTables.Load());
        var cfg = hub.Config;
        MqttConnection? mqtt = null;
        if (args.Contains("--demo"))
        {
            log.LogInformation("[demo] generating simulated data (no MQTT)");
            _ = Task.Run(() => Demo(hub, stop));
        }
        else
        {
            var mqttSettings = settings with
            {
                MqttHost = J.Str(cfg, "mqtt_host") ?? "localhost", MqttPort = (int)(J.Num(cfg, "mqtt_port")?.V ?? 1883),
                MqttUser = J.Str(cfg, "mqtt_user") ?? "", MqttPass = J.Str(cfg, "mqtt_pass") ?? "",
            };
            mqtt = new MqttConnection(mqttSettings, "hvac-server", logs.CreateLogger("mqtt"), $"hvac/{hub.Site}/+/#")
            {
                OnMessage = hub.OnMessage,
            };
            hub.Publish = (topic, payload) =>
            {
                bool ok = mqtt.Publish(topic, payload, retain: false, TimeSpan.FromSeconds(2));
                log.LogInformation("[cmd] {Topic} {Payload}", topic, payload);
                return ok;
            };
            mqtt.Start();
        }

        int port = (int)(J.Num(cfg, "web_port")?.V ?? 8080);
        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.UseUrls($"http://*:{port}");
        var app = builder.Build();
        Map(app, hub, Path.Combine(dir, "web", "index.html"));
        try
        {
            app.StartAsync(stop).GetAwaiter().GetResult();
        }
        catch (IOException)
        {
            Console.Error.WriteLine($"Port {port} is already in use. Close the other HVAC Monitor window or change web_port in config.json.");
            return 1;
        }
        var url = $"http://localhost:{port}";
        log.LogInformation("[web] dashboard at {Url}  (Ctrl+C to stop)", url);
        if (!args.Contains("--no-browser")) OpenBrowser(url);
        app.WaitForShutdownAsync(stop).GetAwaiter().GetResult();
        log.LogInformation("stopping");
        mqtt?.DisposeAsync().AsTask().GetAwaiter().GetResult();
        return 0;
    }

    public static void Map(WebApplication app, BenchHub hub, string indexPath)
    {
        static void NoStore(HttpContext c) => c.Response.Headers.CacheControl = "no-store";
        static IResult Json(HttpContext c, JsonNode? body, int status = 200)
        {
            NoStore(c);
            return Results.Text(Py.Dumps(body), "application/json", Encoding.UTF8, status);
        }

        app.MapGet("/", (HttpContext c) => Index(c));
        app.MapGet("/index.html", (HttpContext c) => Index(c));
        IResult Index(HttpContext c)
        {
            NoStore(c);
            return Results.Bytes(File.ReadAllBytes(indexPath), "text/html; charset=utf-8");
        }
        app.MapGet("/api/state", (HttpContext c) => Json(c, hub.State()));
        app.MapGet("/api/history", (HttpContext c) =>
            Minutes(c, 60) is double m ? Json(c, hub.History(m)) : Json(c, new JsonObject { ["ok"] = false, ["error"] = "bad minutes" }, 400));
        app.MapGet("/api/export.csv", (HttpContext c) =>
        {
            if (Minutes(c, 1440) is not double m) return Json(c, new JsonObject { ["ok"] = false, ["error"] = "bad minutes" }, 400);
            NoStore(c);
            c.Response.Headers.ContentDisposition = "attachment; filename=hvac-export.csv";
            return Results.Text(hub.ExportCsv(m), "text/csv", Encoding.UTF8);
        });
        app.MapGet("/api/stream", async (HttpContext c) =>
        {
            c.Response.ContentType = "text/event-stream";
            NoStore(c);
            var ch = hub.Subscribe();
            var abort = c.RequestAborted;
            try
            {
                await c.Response.Body.FlushAsync(abort);
                while (!abort.IsCancellationRequested)
                {
                    using var wait = CancellationTokenSource.CreateLinkedTokenSource(abort);
                    wait.CancelAfter(TimeSpan.FromSeconds(15));
                    string text;
                    try
                    {
                        var data = await ch.Reader.ReadAsync(wait.Token);
                        text = $"data: {data}\n\n";
                    }
                    catch (OperationCanceledException) when (!abort.IsCancellationRequested)
                    {
                        text = ": keepalive\n\n";
                    }
                    await c.Response.WriteAsync(text, abort);
                    await c.Response.Body.FlushAsync(abort);
                }
            }
            catch (OperationCanceledException) { }
            catch (IOException) { }
            finally
            {
                hub.Unsubscribe(ch);
            }
        });
        app.MapPost("/api/cmd", async (HttpContext c) =>
        {
            if (await Body(c) is not JsonObject body) return Json(c, new JsonObject { ["ok"] = false, ["error"] = "bad JSON" }, 400);
            var node = J.Str(body, "node");
            if (node is null || !BenchHub.Nodes.Contains(node) || body["cmd"] is not JsonObject cmd)
                return Json(c, new JsonObject { ["ok"] = false, ["error"] = "need node and cmd" }, 400);
            var (ok, msg) = await Task.Run(() => hub.SendCmd(node, cmd));
            return Json(c, new JsonObject { ["ok"] = ok, ["message"] = msg });
        });
        app.MapPost("/api/config", async (HttpContext c) =>
        {
            if (await Body(c) is not JsonObject body) return Json(c, new JsonObject { ["ok"] = false, ["error"] = "bad JSON" }, 400);
            hub.SetConfig(body);
            return Json(c, new JsonObject { ["ok"] = true });
        });
        app.MapFallback((HttpContext c) =>
        {
            NoStore(c);
            return Results.Text("not found", "text/plain", Encoding.UTF8, 404);
        });
    }

    static double? Minutes(HttpContext c, double dflt)
    {
        var raw = c.Request.Query["minutes"].FirstOrDefault();
        if (raw is null) return dflt;
        return double.TryParse(raw.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var m) ? m : null;
    }

    /// <summary>The request's JSON ({} when empty), or null when it isn't JSON.</summary>
    static async Task<JsonNode?> Body(HttpContext c)
    {
        using var ms = new MemoryStream();
        await c.Request.Body.CopyToAsync(ms);
        if (ms.Length == 0) return new JsonObject();
        try
        {
            return PyJson.Loads(ms.ToArray());
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>Simulated nodes: 14 min on, 6 min off.</summary>
    static async Task Demo(BenchHub hub, CancellationToken stop)
    {
        double t0 = Time.Now();
        var rnd = Random.Shared;
        double U(double a, double b) => a + rnd.NextDouble() * (b - a);
        static JsonNode R(double x) => JsonValue.Create(Py.Round(x, 1));
        while (!stop.IsCancellationRequested)
        {
            double k = (Time.Now() - t0) / 60;
            bool on = k % 20 < 14;
            double warm = on ? Math.Min(1, k % 20 / 4) : 0;
            double oat = 95 + 3 * Math.Sin(k / 30);
            double pVap = on ? 120 + U(-1, 1) : 300;
            double pLiq = on ? 345 + 15 * warm + U(-2, 2) : 300;
            hub.OnTelemetry("outdoor", new JsonObject
            {
                ["node"] = "outdoor", ["fw"] = "demo", ["rssi"] = -61, ["uptime"] = (int)(k * 60),
                ["mode"] = new JsonObject { ["Y"] = on, ["OB"] = on },   // O energized = cooling
                ["p"] = new JsonObject { ["liq"] = R(pLiq), ["vap"] = R(pVap), ["tsuc"] = null },
                ["t"] = new JsonObject
                {
                    ["suc"] = on ? R(52 + U(-0.5, 0.5)) : R(oat - 5), ["liq"] = on ? R(oat + 6 + U(-0.5, 0.5)) : R(oat - 2),
                    ["tsuc"] = null, ["dis"] = null,
                },
                ["air"] = new JsonObject { ["t"] = R(oat), ["rh"] = 12.0 }, ["v5"] = 5.02, ["err"] = new JsonArray(),
            });
            hub.OnTelemetry("indoor", new JsonObject
            {
                ["node"] = "indoor", ["fw"] = "demo", ["rssi"] = -55, ["uptime"] = (int)(k * 60),
                ["mode"] = new JsonObject { ["Y"] = on, ["W"] = false, ["G"] = on, ["OB"] = on },
                ["air"] = new JsonObject { ["supply"] = R(76 - 19 * warm + U(-0.3, 0.3)), ["return"] = R(76 + U(-0.3, 0.3)), ["dt"] = null },
                ["err"] = new JsonArray(),
            });
            try { await Task.Delay(2000, stop); } catch (OperationCanceledException) { break; }
        }
    }

    static void OpenBrowser(string url)
    {
        try
        {
            if (OperatingSystem.IsWindows()) Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            else if (OperatingSystem.IsMacOS()) Process.Start("open", url);
            else Process.Start("xdg-open", url);
        }
        catch (Exception)
        {
            // no browser here; the URL is in the log
        }
    }

    static string? FindDir(string home)
    {
        foreach (var d in new[] { Path.Combine(home, "..", "hvac-monitor-app"), Path.Combine(Directory.GetCurrentDirectory(), "hvac-monitor-app"), Directory.GetCurrentDirectory() })
            if (File.Exists(Path.Combine(d, "web", "index.html"))) return Path.GetFullPath(d);
        return null;
    }

    static string? Arg(string[] args, string name)
    {
        int i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}
