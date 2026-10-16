using Fullscope.Cloud.Ingest;
using Microsoft.Extensions.Logging;

namespace Fullscope.Cloud;

/// <summary>
/// fullscope ingest    MQTT ingest worker (python -m hvaccloud.ingest)
/// fullscope api       web app and REST API (uvicorn hvaccloud.api:app), --host, --port, --proxy-headers
/// fullscope manage    accounts, systems, users and upkeep (manage.py)
/// </summary>
public static class Program
{
    public static int Main(string[] args)
    {
        var settings = Settings.Load();
        using var logs = LoggerFactory.Create(b => b.AddSimpleConsole(o =>
        {
            o.SingleLine = true;
            o.TimestampFormat = "yyyy-MM-dd HH:mm:ss ";
        }).SetMinimumLevel(LogLevel.Information));
        using var stop = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Cancel(); };
        AppDomain.CurrentDomain.ProcessExit += (_, _) => stop.Cancel();
        switch (args.FirstOrDefault())
        {
            case "ingest":
                return IngestWorker.Run(settings, logs, stop.Token);
            case "api":
                return Api.ApiHost.Run(settings, args[1..], logs, stop.Token);
            default:
                Console.Error.WriteLine("usage: fullscope ingest | api | manage <command>");
                return 2;
        }
    }
}
