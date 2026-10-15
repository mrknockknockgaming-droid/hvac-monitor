using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Cloud.Ingest;
using Fullscope.Cloud.Mqtt;
using Fullscope.Core;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.Extensions.Logging;

namespace Fullscope.Cloud.Api;

/// <summary>Publishes commands to hvac/&lt;site&gt;/&lt;node&gt;/cmd and the thermostat's retained config;
/// connects to the broker on first use.</summary>
public sealed class ApiPublisher(Settings settings, ILogger log)
{
    MqttConnection? _mqtt;
    readonly Lock _lock = new();

    public bool Publish(string topic, JsonNode payload, bool retain)
    {
        lock (_lock)
        {
            if (_mqtt is null)
            {
                _mqtt = new MqttConnection(settings, "hvac-cloud-api", log);
                _mqtt.Start();
            }
        }
        return _mqtt.Publish(topic, Py.Dumps(payload), retain, TimeSpan.FromSeconds(5));
    }
}

/// <summary>`fullscope api [--host 127.0.0.1] [--port 8000] [--proxy-headers]`: the web app and REST API.</summary>
public static class ApiHost
{
    public static int Run(Settings settings, string[] args, ILoggerFactory logs, CancellationToken stop)
    {
        string host = Arg(args, "--host") ?? "127.0.0.1";
        string port = Arg(args, "--port") ?? "8000";
        var builder = WebApplication.CreateSlimBuilder();
        builder.Logging.ClearProviders();
        builder.Logging.AddSimpleConsole(o =>
        {
            o.SingleLine = true;
            o.TimestampFormat = "yyyy-MM-dd HH:mm:ss ";
        });
        builder.Logging.AddFilter("Microsoft.AspNetCore", LogLevel.Warning);
        builder.Logging.AddFilter("Microsoft.EntityFrameworkCore", LogLevel.Warning);
        builder.WebHost.UseUrls($"http://{(host == "0.0.0.0" ? "*" : host)}:{port}");
        var app = builder.Build();
        if (args.Contains("--proxy-headers"))
        {
            var fwd = new ForwardedHeadersOptions { ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto };
            fwd.KnownIPNetworks.Clear();
            fwd.KnownProxies.Clear();
            app.UseForwardedHeaders(fwd);
        }
        var log = logs.CreateLogger("api");
        var sessions = DbFactory.FromUrl(settings.DatabaseUrl).Init(settings.KeepDays);
        var publisher = new ApiPublisher(settings, logs.CreateLogger("mqtt"));
        var server = new ApiServer(sessions, settings, publisher.Publish, new SmtpMailer(settings, logs.CreateLogger("email")), log: log);
        server.Map(app);
        log.LogInformation("web app on http://{Host}:{Port}/app/", host, port);
        app.RunAsync(stop).GetAwaiter().GetResult();
        return 0;
    }

    static string? Arg(string[] args, string name)
    {
        int i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}
