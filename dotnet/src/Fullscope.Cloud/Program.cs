using Fullscope.Cloud.Ingest;
using Microsoft.Extensions.Logging;

namespace Fullscope.Cloud;

/// <summary>
/// fullscope ingest    MQTT ingest worker (python -m hvaccloud.ingest)
/// fullscope api       web app and REST API (uvicorn hvaccloud.api:app), --host, --port, --proxy-headers
/// fullscope bench     PC bench dashboard on :8080 (hvac-monitor-app/server.py), --demo, --no-browser
/// fullscope manage    accounts, systems, users and upkeep (manage.py)
/// fullscope demo      contractor demo account: setup | run | reset (demo.py)
/// fullscope simulate  simulated nodes over MQTT (demo_publisher.py)
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
        var stop = new CancellationTokenSource();          // lives as long as the process (ProcessExit uses it)
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Cancel(); };
        AppDomain.CurrentDomain.ProcessExit += (_, _) => stop.Cancel();
        switch (args.FirstOrDefault())
        {
            case "ingest":
                return IngestWorker.Run(settings, logs, stop.Token);
            case "api":
                return Api.ApiHost.Run(settings, args[1..], logs, stop.Token);
            case "bench":
                return Bench.BenchHost.Run(settings, args[1..], logs, stop.Token);
            case "demo":
                return Tools.Demo.Command(settings, args[1..], logs, stop.Token);
            case "simulate":
                return Tools.Simulator.Run(settings, args[1..], logs, stop.Token);
            case "manage":
                return Manage.Run(settings, args[1..], logs);
            default:
                Console.Error.WriteLine("usage: fullscope ingest | api | bench | manage <command> | demo setup|run|reset | simulate");
                return 2;
        }
    }
}
