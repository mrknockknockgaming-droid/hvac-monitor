using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Fullscope.Core;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Metadata;
using Microsoft.EntityFrameworkCore.Metadata.Builders;
using Microsoft.EntityFrameworkCore.Storage;
using Microsoft.EntityFrameworkCore.Storage.ValueConversion;

namespace Fullscope.Cloud.Data;

/// <summary>
/// The database: SQLite (dev.db, the LXC) or TimescaleDB/PostgreSQL (docker-compose). Same tables,
/// columns and stored formats as hvaccloud/db.py, so either version can run on the other's database.
/// </summary>
public sealed class Db(DbContextOptions<Db> options) : DbContext(options)
{
    public DbSet<Account> Accounts => Set<Account>();
    public DbSet<ApiKey> ApiKeys => Set<ApiKey>();
    public DbSet<User> Users => Set<User>();
    public DbSet<UserSession> UserSessions => Set<UserSession>();
    public DbSet<SystemMember> SystemMembers => Set<SystemMember>();
    public DbSet<Invite> Invites => Set<Invite>();
    public DbSet<Order> Orders => Set<Order>();
    public DbSet<Customer> Customers => Set<Customer>();
    public DbSet<Ticket> Tickets => Set<Ticket>();
    public DbSet<TicketMessage> TicketMessages => Set<TicketMessage>();
    public DbSet<Kit> Kits => Set<Kit>();
    public DbSet<Board> Boards => Set<Board>();
    public DbSet<WarrantyClaim> WarrantyClaims => Set<WarrantyClaim>();
    public DbSet<PasswordReset> PasswordResets => Set<PasswordReset>();
    public DbSet<PushSubscription> PushSubscriptions => Set<PushSubscription>();
    public DbSet<ServerKey> ServerKeys => Set<ServerKey>();
    public DbSet<HvacSystem> Systems => Set<HvacSystem>();
    public DbSet<Device> Devices => Set<Device>();
    public DbSet<Telemetry> Telemetry => Set<Telemetry>();
    public DbSet<Snapshot> Snapshots => Set<Snapshot>();
    public DbSet<Command> Commands => Set<Command>();
    public DbSet<Alert> Alerts => Set<Alert>();
    public DbSet<EquipmentRow> Equipment => Set<EquipmentRow>();
    public DbSet<ThermostatConfig> ThermostatConfigs => Set<ThermostatConfig>();
    public DbSet<ServiceVisit> ServiceVisits => Set<ServiceVisit>();
    public DbSet<GaugeCheck> GaugeChecks => Set<GaugeCheck>();
    public DbSet<AlertPrefs> AlertPrefs => Set<AlertPrefs>();
    public DbSet<AlertMute> AlertMutes => Set<AlertMute>();
    public DbSet<RuntimeDay> RuntimeDays => Set<RuntimeDay>();
    public DbSet<ServiceInfo> ServiceInfo => Set<ServiceInfo>();
    public DbSet<MaintenanceItem> MaintenanceItems => Set<MaintenanceItem>();
    public DbSet<SnapshotMinute> SnapshotMinutes => Set<SnapshotMinute>();

    public bool IsSqlite => Database.ProviderName == "Microsoft.EntityFrameworkCore.Sqlite";

    // ---------- helpers shared with the Python code's conventions ----------

    /// <summary>Now, in UTC, to the microsecond (db.utcnow).</summary>
    public static DateTime UtcNow() => Time.Micro(Time.UtcNow());

    public static DateTime Ts(double epoch) => Time.FromEpoch(epoch);

    public static double Epoch(DateTime t) => Time.ToEpoch(t);

    /// <summary>SHA-256 hex of a key or token (db.hash_key).</summary>
    public static string HashKey(string key) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(key)));

    /// <summary>secrets.token_urlsafe(n).</summary>
    public static string TokenUrlSafe(int bytes) =>
        Convert.ToBase64String(RandomNumberGenerator.GetBytes(bytes)).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    /// <summary>JSON column text as Python writes it (json.dumps).</summary>
    public static string? Json(JsonNode? n) => n is null ? null : Py.Dumps(n);

    /// <summary>A JSON column's value (SQL NULL and JSON null both read as null).</summary>
    public static JsonNode? Parse(string? text) => string.IsNullOrEmpty(text) ? null : JsonNode.Parse(text);

    public static JsonObject ParseObject(string? text) => Parse(text) as JsonObject ?? [];

    // ---------- mapping ----------

    const string SqliteTimeFormat = "yyyy-MM-dd HH:mm:ss.ffffff";
    static readonly string[] SqliteTimeFormats =
        ["yyyy-MM-dd HH:mm:ss.FFFFFFF", "yyyy-MM-dd'T'HH:mm:ss.FFFFFFF", "yyyy-MM-dd HH:mm:ss.FFFFFFFzzz", "yyyy-MM-dd'T'HH:mm:ss.FFFFFFFzzz", "yyyy-MM-dd"];

    /// <summary>SQLAlchemy's SQLite DATETIME text: UTC, always six fractional digits, so text order is time order.</summary>
    public static string SqliteTime(DateTime t) => t.ToString(SqliteTimeFormat, CultureInfo.InvariantCulture);

    static DateTime ParseSqliteTime(string s) =>
        DateTime.ParseExact(s, SqliteTimeFormats, CultureInfo.InvariantCulture,
                            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal);

    static readonly ValueConverter<DateTime, string> SqliteTimeConverter =
        new(v => SqliteTime(v), v => ParseSqliteTime(v));

    static readonly ValueConverter<DateTime, DateTime> UtcConverter =
        new(v => v.Kind == DateTimeKind.Utc ? v : DateTime.SpecifyKind(v, DateTimeKind.Utc),
            v => DateTime.SpecifyKind(v, DateTimeKind.Utc));

    static string Snake(string name) => Regex.Replace(name, "(?<=[a-z0-9])([A-Z])|(?<=[A-Z])([A-Z])(?=[a-z])", "_$1$2").ToLowerInvariant();

    protected override void OnModelCreating(ModelBuilder b)
    {
        bool sqlite = IsSqlite;
        b.Entity<Account>(e => { e.ToTable("accounts"); e.HasIndex(x => x.Email).IsUnique(); Len(e.Property(x => x.Name), 200); Len(e.Property(x => x.Email), 320); });
        b.Entity<ApiKey>(e =>
        {
            e.ToTable("api_keys");
            e.HasIndex(x => x.KeyHash).IsUnique();
            Len(e.Property(x => x.Prefix), 12);
            Len(e.Property(x => x.KeyHash), 64);
            e.HasOne<Account>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<User>(e =>
        {
            e.ToTable("users");
            e.HasIndex(x => x.Email).IsUnique();
            Len(e.Property(x => x.Email), 320);
            Len(e.Property(x => x.Name), 200);
            Len(e.Property(x => x.Role), 16);
            Len(e.Property(x => x.PasswordHash), 200);
            Len(e.Property(x => x.TotpSecret), 64);
            e.HasOne<Account>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<UserSession>(e =>
        {
            e.ToTable("user_sessions");
            e.HasKey(x => x.TokenHash);
            Len(e.Property(x => x.TokenHash), 64);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.UserId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<SystemMember>(e =>
        {
            e.ToTable("system_members");
            e.HasKey(x => new { x.SystemId, x.UserId });
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.UserId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Order>(e =>
        {
            e.ToTable("orders");
            Len(e.Property(x => x.Company), 200);
            Len(e.Property(x => x.Name), 200);
            Len(e.Property(x => x.Email), 320);
            Len(e.Property(x => x.Phone), 40);
            Len(e.Property(x => x.Notes), 2000);
        });
        b.Entity<Customer>(e =>
        {
            e.ToTable("customers");
            Len(e.Property(x => x.Company), 200);
            Len(e.Property(x => x.Contact), 200);
            Len(e.Property(x => x.Email), 320);
            Len(e.Property(x => x.Phone), 40);
            Len(e.Property(x => x.Address), 500);
            Len(e.Property(x => x.Status), 16);
            Len(e.Property(x => x.Notes), 4000);
            e.HasOne<Account>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Ticket>(e =>
        {
            e.ToTable("tickets");
            Len(e.Property(x => x.Kind), 16);
            Len(e.Property(x => x.Status), 16);
            Len(e.Property(x => x.Subject), 200);
            Len(e.Property(x => x.Name), 200);
            Len(e.Property(x => x.Email), 320);
            Len(e.Property(x => x.Phone), 40);
            Len(e.Property(x => x.Company), 200);
            Len(e.Property(x => x.Body), 4000);
            e.HasOne<Order>().WithMany().HasForeignKey(x => x.OrderId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<Customer>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<Account>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.UserId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<TicketMessage>(e =>
        {
            e.ToTable("ticket_messages");
            Len(e.Property(x => x.Kind), 8);
            Len(e.Property(x => x.Body), 4000);
            e.HasOne<Ticket>().WithMany().HasForeignKey(x => x.TicketId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.AuthorId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Kit>(e =>
        {
            e.ToTable("kits");
            e.HasIndex(x => x.Code).IsUnique();
            Len(e.Property(x => x.Code), 16);
            Len(e.Property(x => x.ClaimCode), 16);
            Len(e.Property(x => x.HwRev), 16);
            Len(e.Property(x => x.Notes), 1000);
            e.HasOne<Customer>().WithMany().HasForeignKey(x => x.CustomerId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.ClaimedBy).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<Account>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Board>(e =>
        {
            e.ToTable("boards");
            e.HasIndex(x => x.Serial).IsUnique();
            Len(e.Property(x => x.Serial), 17);
            Len(e.Property(x => x.Kind), 16);
            Len(e.Property(x => x.HwRev), 16);
            Len(e.Property(x => x.Fw), 32);
            Len(e.Property(x => x.Status), 16);
            Len(e.Property(x => x.Notes), 1000);
            e.HasOne<Kit>().WithMany().HasForeignKey(x => x.KitId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<WarrantyClaim>(e =>
        {
            e.ToTable("warranty_claims");
            Len(e.Property(x => x.Problem), 2000);
            Len(e.Property(x => x.Status), 16);
            Len(e.Property(x => x.Resolution), 2000);
            e.HasOne<Board>().WithMany().HasForeignKey(x => x.BoardId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<Board>().WithMany().HasForeignKey(x => x.ReplacementId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Invite>(e =>
        {
            e.ToTable("invites");
            e.HasIndex(x => x.TokenHash).IsUnique();
            Len(e.Property(x => x.TokenHash), 64);
            Len(e.Property(x => x.Email), 320);
            Len(e.Property(x => x.Role), 16);
            e.HasOne<Account>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.InvitedBy).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<PushSubscription>(e =>
        {
            e.ToTable("push_subscriptions");
            e.HasIndex(x => x.Endpoint).IsUnique();
            Len(e.Property(x => x.Endpoint), 1000);
            Len(e.Property(x => x.P256dh), 200);
            Len(e.Property(x => x.Auth), 100);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.UserId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<ServerKey>(e =>
        {
            e.ToTable("server_keys");
            e.HasKey(x => x.Name);
            Len(e.Property(x => x.Name), 64);
            Len(e.Property(x => x.Value), 200);
        });
        b.Entity<PasswordReset>(e =>
        {
            e.ToTable("password_resets");
            e.HasKey(x => x.TokenHash);
            Len(e.Property(x => x.TokenHash), 64);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.UserId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<HvacSystem>(e =>
        {
            e.ToTable("systems");
            e.HasIndex(x => x.SiteId).IsUnique();
            Len(e.Property(x => x.Name), 200);
            Len(e.Property(x => x.SiteId), 64);
            Len(e.Property(x => x.Refrigerant), 20);
            Len(e.Property(x => x.ObEnergized), 4);
            Len(e.Property(x => x.Address), 500);
            if (sqlite) e.Property(x => x.AtmPsia).HasColumnType("FLOAT");
            e.HasOne<Account>().WithMany().HasForeignKey(x => x.AccountId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Device>(e =>
        {
            e.ToTable("devices");
            e.HasIndex(x => new { x.SystemId, x.Node }).IsUnique();
            Len(e.Property(x => x.Node), 16);
            Len(e.Property(x => x.Fw), 32);
            Len(e.Property(x => x.Ip), 45);
            Len(e.Property(x => x.Mac), 17);
            Json(e.Property(x => x.LastStatus), sqlite);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Telemetry>(e =>
        {
            e.ToTable("telemetry");
            e.HasKey(x => new { x.DeviceId, x.Time });
            Json(e.Property(x => x.Data), sqlite);
            e.HasOne<Device>().WithMany().HasForeignKey(x => x.DeviceId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Snapshot>(e =>
        {
            e.ToTable("snapshots");
            e.HasKey(x => new { x.SystemId, x.Time });
            Len(e.Property(x => x.Mode), 12);
            Json(e.Property(x => x.Data), sqlite);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Command>(e =>
        {
            e.ToTable("commands");
            Json(e.Property(x => x.Payload), sqlite);
            Json(e.Property(x => x.Reply), sqlite);
            e.HasOne<Device>().WithMany().HasForeignKey(x => x.DeviceId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<Alert>(e =>
        {
            e.ToTable("alerts");
            Len(e.Property(x => x.Code), 32);
            Len(e.Property(x => x.Node), 16);
            Len(e.Property(x => x.Level), 8);
            Len(e.Property(x => x.Text), 300);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<EquipmentRow>(e =>
        {
            e.ToTable("equipment");
            e.HasKey(x => x.SystemId);
            e.Property(x => x.SystemId).ValueGeneratedNever();
            Len(e.Property(x => x.SystemType), 16);
            Len(e.Property(x => x.Metering), 8);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<ThermostatConfig>(e =>
        {
            e.ToTable("thermostat_configs");
            e.HasKey(x => x.SystemId);
            e.Property(x => x.SystemId).ValueGeneratedNever();
            Json(e.Property(x => x.Settings), sqlite);
            Json(e.Property(x => x.Tech), sqlite);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<ServiceVisit>(e =>
        {
            e.ToTable("service_visits");
            Len(e.Property(x => x.Kind), 16);
            Len(e.Property(x => x.Technician), 200);
            Len(e.Property(x => x.Work), 2000);
            Json(e.Property(x => x.Readings), sqlite);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.CreatedBy).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<GaugeCheck>(e =>
        {
            e.ToTable("gauge_checks");
            Len(e.Property(x => x.Technician), 200);
            Len(e.Property(x => x.Note), 500);
            Len(e.Property(x => x.Refrigerant), 20);
            e.Property(x => x.GaugeData).HasColumnName("gauge");
            e.Property(x => x.SystemData).HasColumnName("system");
            Json(e.Property(x => x.GaugeData), sqlite);
            Json(e.Property(x => x.SystemData), sqlite);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
            e.HasOne<User>().WithMany().HasForeignKey(x => x.CreatedBy).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<AlertPrefs>(e =>
        {
            e.ToTable("alert_prefs");
            e.HasKey(x => x.SystemId);
            e.Property(x => x.SystemId).ValueGeneratedNever();
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<AlertMute>(e =>
        {
            e.ToTable("alert_mutes");
            e.HasKey(x => new { x.SystemId, x.Code });
            Len(e.Property(x => x.Code), 32);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<RuntimeDay>(e =>
        {
            e.ToTable("runtime_days");
            e.HasKey(x => new { x.SystemId, x.Day });
            if (sqlite)
            {
                e.Property(x => x.BlowerS).HasColumnType("FLOAT");
                e.Property(x => x.CompressorS).HasColumnType("FLOAT");
            }
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<ServiceInfo>(e =>
        {
            e.ToTable("service_info");
            e.HasKey(x => x.SystemId);
            e.Property(x => x.SystemId).ValueGeneratedNever();
            Len(e.Property(x => x.Name), 200);
            Len(e.Property(x => x.Phone), 40);
            Len(e.Property(x => x.Email), 320);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<MaintenanceItem>(e =>
        {
            e.ToTable("maintenance_items");
            e.HasIndex(x => new { x.SystemId, x.Kind }).IsUnique();
            Len(e.Property(x => x.Kind), 16);
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });
        b.Entity<SnapshotMinute>(e =>
        {
            e.ToTable("snapshot_minutes");
            e.HasKey(x => new { x.SystemId, x.Minute });
            e.HasOne<HvacSystem>().WithMany().HasForeignKey(x => x.SystemId).OnDelete(DeleteBehavior.NoAction);
        });

        // snake_case columns; SQLite column types as SQLAlchemy writes them; UTC times
        foreach (var entity in b.Model.GetEntityTypes())
        {
            foreach (var p in entity.GetProperties())
            {
                if (p.GetColumnName() == p.Name) p.SetColumnName(Snake(p.Name));     // unless named with HasColumnName
                var t = Nullable.GetUnderlyingType(p.ClrType) ?? p.ClrType;
                if (t == typeof(DateTime))
                {
                    p.SetValueConverter(sqlite ? SqliteTimeConverter : UtcConverter);
                    if (sqlite) p.SetColumnType("DATETIME");
                }
                else if (sqlite && p.GetColumnType() is null)
                {
                    if (t == typeof(bool)) p.SetColumnType("BOOLEAN");
                    else if (t == typeof(DateOnly)) p.SetColumnType("DATE");
                    else if (t == typeof(double)) p.SetColumnType("DOUBLE");
                    else if (t == typeof(int)) p.SetColumnType("INTEGER");
                    else if (t == typeof(string) && p.GetMaxLength() is int n) p.SetColumnType($"VARCHAR({n})");
                }
            }
        }
    }

    static void Len(PropertyBuilder p, int n) => p.HasMaxLength(n);

    static void Json(PropertyBuilder p, bool sqlite) => p.HasColumnType(sqlite ? "JSON" : "jsonb");

    // ---------- connecting ----------

    /// <summary>Options for a SQLAlchemy-style URL: sqlite:///path or postgresql[+driver]://user:pass@host:port/db.</summary>
    public static DbContextOptions<Db> Options(string url)
    {
        var o = new DbContextOptionsBuilder<Db>();
        if (url.StartsWith("sqlite", StringComparison.Ordinal))
        {
            var path = SqlitePath(url);
            var cs = new SqliteConnectionStringBuilder { DataSource = path ?? ":memory:", ForeignKeys = true, DefaultTimeout = 30 };
            o.UseSqlite(cs.ToString());
        }
        else
        {
            o.UseNpgsql(PostgresConnectionString(url));
        }
        return o.Options;
    }

    /// <summary>File path of a sqlite:/// URL, or null for other databases (and in-memory SQLite).</summary>
    public static string? SqlitePath(string url)
    {
        if (!url.StartsWith("sqlite:///", StringComparison.Ordinal)) return null;
        var path = url["sqlite:///".Length..];
        return path.Length == 0 ? null : path;
    }

    static string PostgresConnectionString(string url)
    {
        var u = new Uri(Regex.Replace(url, @"^postgres(ql)?(\+\w+)?://", "postgresql://"));
        var userInfo = u.UserInfo.Split(':', 2);
        var cs = new Npgsql.NpgsqlConnectionStringBuilder
        {
            Host = u.Host,
            Port = u.Port > 0 ? u.Port : 5432,
            Database = u.AbsolutePath.TrimStart('/'),
            Username = Uri.UnescapeDataString(userInfo[0]),
            Password = userInfo.Length > 1 ? Uri.UnescapeDataString(userInfo[1]) : null,
        };
        return cs.ToString();
    }
}
