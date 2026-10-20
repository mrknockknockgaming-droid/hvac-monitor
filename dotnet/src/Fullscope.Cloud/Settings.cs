using System.Globalization;

namespace Fullscope.Cloud;

/// <summary>
/// Configuration from environment variables, or KEY=value lines in hvac-cloud/.env (see .env.example).
/// FULLSCOPE_ENV_FILE names a different file (the LXC uses /etc/fullscope/fullscope.env). Real
/// environment variables win over the file. Same names and defaults as hvaccloud/settings.py.
/// </summary>
public sealed record Settings
{
    /// <summary>hvac-cloud/: the folder with .env, dev.db, backups/ and web/.</summary>
    public string Home { get; init; } = "";
    public string DatabaseUrl { get; init; } = "";
    public string MqttHost { get; init; } = "localhost";
    public int MqttPort { get; init; } = 1883;
    public string MqttUser { get; init; } = "";
    public string MqttPass { get; init; } = "";
    public double StaleSeconds { get; init; } = 30;           // node counts as offline after this
    public int KeepDays { get; init; } = 365;                 // older data is deleted
    public int FullDetailDays { get; init; } = 30;            // then thinned to 1-minute averages (SQLite)
    public string BackupDir { get; init; } = "";
    public int BackupKeep { get; init; } = 7;
    public double AlertHoldSeconds { get; init; } = 300;      // a flag must hold this long to raise an alert, and be gone this long to clear it
    public double AlertNoDataSeconds { get; init; } = 600;    // silence before "no data"
    public double AlertEmailCooldownHours { get; init; } = 6; // per system + code
    public string AppUrl { get; init; } = "http://localhost:8000/app/";
    public double SessionDays { get; init; } = 30;
    public bool CookieSecure { get; init; }
    public string SmtpHost { get; init; } = "";
    public int SmtpPort { get; init; } = 587;                 // 587 = STARTTLS, 465 = SSL
    public string SmtpUser { get; init; } = "";
    public string SmtpPass { get; init; } = "";
    public string SmtpFrom { get; init; } = "";
    public string OrderEmail { get; init; } = "";             // where landing-page order requests go (default SmtpFrom)
    public string WebDir { get; init; } = "";
    public int WarrantyMonths { get; init; } = 12;            // Fullscope boards, from the day a kit is claimed
    public int InvoiceDays { get; init; } = 30;               // invoices are due this many days after sending
    public string InvoiceNote { get; init; } = "";            // how to pay, printed on every invoice
    public int QuoteDays { get; init; } = 30;                 // a sent quote can be accepted for this many days

    /// <summary>Settings for this process: the environment, then the .env file.</summary>
    public static Settings Load(string? home = null)
    {
        home ??= Environment.GetEnvironmentVariable("FULLSCOPE_HOME") ?? FindHome();
        var file = ReadEnvFile(Environment.GetEnvironmentVariable("FULLSCOPE_ENV_FILE") is { Length: > 0 } f
                               ? f : Path.Combine(home, ".env"));
        string Get(string key, string dflt) => Environment.GetEnvironmentVariable(key) ?? file.GetValueOrDefault(key) ?? dflt;
        double D(string key, string dflt) => double.Parse(Get(key, dflt), CultureInfo.InvariantCulture);
        int I(string key, string dflt) => int.Parse(Get(key, dflt), CultureInfo.InvariantCulture);
        var user = Get("SMTP_USER", "");
        var settings = new Settings
        {
            Home = home,
            DatabaseUrl = Get("DATABASE_URL", "sqlite:///" + Path.Combine(home, "dev.db").Replace('\\', '/')),
            MqttHost = Get("MQTT_HOST", "localhost"),
            MqttPort = I("MQTT_PORT", "1883"),
            MqttUser = Get("MQTT_USER", ""),
            MqttPass = Get("MQTT_PASS", ""),
            StaleSeconds = D("STALE_SECONDS", "30"),
            KeepDays = I("KEEP_DAYS", "365"),
            FullDetailDays = I("FULL_DETAIL_DAYS", "30"),
            BackupDir = Get("BACKUP_DIR", Path.Combine(home, "backups")),
            BackupKeep = I("BACKUP_KEEP", "7"),
            AlertHoldSeconds = D("ALERT_HOLD_SECONDS", "300"),
            AlertNoDataSeconds = D("ALERT_NO_DATA_SECONDS", "600"),
            AlertEmailCooldownHours = D("ALERT_EMAIL_COOLDOWN_HOURS", "6"),
            AppUrl = Get("APP_URL", "http://localhost:8000/app/"),
            SessionDays = D("SESSION_DAYS", "30"),
            CookieSecure = Get("COOKIE_SECURE", "false").ToLowerInvariant() is "1" or "true" or "yes",
            SmtpHost = Get("SMTP_HOST", ""),
            SmtpPort = I("SMTP_PORT", "587"),
            SmtpUser = user,
            SmtpPass = Get("SMTP_PASS", ""),
            SmtpFrom = Get("SMTP_FROM", "") is { Length: > 0 } from ? from : user,
            WebDir = Get("FULLSCOPE_WEB_DIR", Path.Combine(home, "web")),
            WarrantyMonths = I("WARRANTY_MONTHS", "12"),
            InvoiceDays = I("INVOICE_DAYS", "30"),
            InvoiceNote = Get("INVOICE_NOTE", ""),
            QuoteDays = I("QUOTE_DAYS", "30"),
        };
        return settings with { OrderEmail = Get("ORDER_EMAIL", "") is { Length: > 0 } to ? to : settings.SmtpFrom };
    }

    /// <summary>KEY=value lines; # comments; quotes around values are dropped.</summary>
    public static Dictionary<string, string> ReadEnvFile(string path)
    {
        var d = new Dictionary<string, string>();
        if (!File.Exists(path)) return d;
        foreach (var raw in File.ReadAllLines(path))
        {
            var line = raw.Trim();
            int eq = line.IndexOf('=');
            if (line.Length == 0 || line.StartsWith('#') || eq < 0) continue;
            var key = line[..eq].Trim();
            d.TryAdd(key, line[(eq + 1)..].Trim().Trim('"', '\''));
        }
        return d;
    }

    /// <summary>The hvac-cloud folder: next to the program in a deployment, else found from the
    /// working directory (dotnet run in the repo).</summary>
    static string FindHome()
    {
        foreach (var start in new[] { AppContext.BaseDirectory, Directory.GetCurrentDirectory() })
        {
            for (var dir = new DirectoryInfo(start); dir is not null; dir = dir.Parent)
            {
                if (File.Exists(Path.Combine(dir.FullName, "hvaccloud", "calc.py")) || Directory.Exists(Path.Combine(dir.FullName, "web")) && File.Exists(Path.Combine(dir.FullName, "web", "app.js")))
                    return dir.FullName;
                var cloud = Path.Combine(dir.FullName, "hvac-cloud");
                if (Directory.Exists(cloud)) return cloud;
            }
        }
        return Directory.GetCurrentDirectory();
    }
}
