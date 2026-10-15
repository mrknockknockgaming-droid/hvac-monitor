using Fullscope.Cloud.Data;
using Fullscope.Cloud.Mqtt;
using Fullscope.Core;
using Microsoft.Extensions.Logging;

namespace Fullscope.Cloud.Ingest;

/// <summary>`fullscope ingest`: the MQTT loop, the once-a-minute sweep and the nightly backup and prune.</summary>
public static class IngestWorker
{
    public static int Run(Settings settings, ILoggerFactory logs, CancellationToken stop)
    {
        var log = logs.CreateLogger("ingest");
        var sessions = DbFactory.FromUrl(settings.DatabaseUrl).Init(settings.KeepDays);
        var mailer = new SmtpMailer(settings, logs.CreateLogger("alerts"));
        var ingest = new Ingest(sessions, RefrigerantTables.Load(), settings, log) { Mailer = mailer };
        log.LogInformation("alert email {State}", mailer.Enabled ? $"on via {mailer.Host}" : "off (set SMTP_HOST in .env)");

        var mqtt = new MqttConnection(settings, "hvac-cloud-ingest", logs.CreateLogger("mqtt"),
                                      "hvac/+/+/telemetry", "hvac/+/+/status", "hvac/+/+/reply", $"hvac/+/{Thermostat.Node}/request");
        mqtt.OnMessage = (topic, payload) => ingest.Handle(topic, payload);
        ingest.Publish = (topic, payload) => mqtt.Publish(topic, Py.Dumps(payload, compact: true), retain: true);
        mqtt.Start();

        var maintenanceLog = logs.CreateLogger("maintenance");
        while (!stop.WaitHandle.WaitOne(TimeSpan.FromSeconds(60)))
        {
            try
            {
                ingest.Sweep();
            }
            catch (Exception e)
            {
                log.LogError(e, "alert sweep failed");
            }
            if (Db.SqlitePath(settings.DatabaseUrl) is not null && Maintenance.NightlyDue(settings.BackupDir))
            {
                try
                {
                    Maintenance.Backup(settings.DatabaseUrl, settings.BackupDir, settings.BackupKeep, maintenanceLog);
                    Maintenance.Prune(sessions, settings.FullDetailDays, settings.KeepDays,
                                      step: work => { lock (ingest.Lock) return work(); }, log: maintenanceLog);
                }
                catch (Exception e)
                {
                    log.LogError(e, "nightly backup / prune failed");
                }
            }
        }
        mqtt.DisposeAsync().AsTask().Wait(TimeSpan.FromSeconds(5));
        return 0;
    }
}
