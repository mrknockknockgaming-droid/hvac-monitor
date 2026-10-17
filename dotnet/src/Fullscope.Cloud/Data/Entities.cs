using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Core;

namespace Fullscope.Cloud.Data;

// Tables and columns are the ones hvaccloud/db.py creates, so the C# and Python versions share a
// database. Times are UTC; JSON columns hold the text Python's json.dumps writes.

public sealed class Account
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public string Email { get; set; } = "";
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
}

/// <summary>Only a SHA-256 of the key is stored; the key itself is shown once when created.</summary>
public sealed class ApiKey
{
    public int Id { get; set; }
    public int AccountId { get; set; }
    public string Prefix { get; set; } = "";
    public string KeyHash { get; set; } = "";
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
    public DateTime? LastUsed { get; set; }

    public static (ApiKey Row, string Key) New(int accountId)
    {
        var key = "hvk_" + Db.TokenUrlSafe(32);
        return (new ApiKey { AccountId = accountId, Prefix = key[..12], KeyHash = Db.HashKey(key) }, key);
    }
}

/// <summary>A person who signs in: a contractor (account_id, sees the account's systems) or a
/// homeowner (system_members, sees those systems).</summary>
public sealed class User
{
    public int Id { get; set; }
    public string Email { get; set; } = "";
    public string? Name { get; set; }
    public string Role { get; set; } = "";              // "contractor" | "homeowner"
    public int? AccountId { get; set; }
    public string? PasswordHash { get; set; }           // null until a password is set
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
    public DateTime? LastLogin { get; set; }
}

/// <summary>A signed-in browser. Only a SHA-256 of the cookie token is stored.</summary>
public sealed class UserSession
{
    public string TokenHash { get; set; } = "";
    public int UserId { get; set; }
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
    public DateTime ExpiresAt { get; set; }
    public DateTime LastSeen { get; set; } = Db.UtcNow();
}

public sealed class SystemMember
{
    public int SystemId { get; set; }
    public int UserId { get; set; }
}

public sealed class Invite
{
    public int Id { get; set; }
    public string TokenHash { get; set; } = "";
    public string Email { get; set; } = "";
    public string Role { get; set; } = "";              // "homeowner" | "contractor"
    public int? AccountId { get; set; }
    public int? SystemId { get; set; }
    public int? InvitedBy { get; set; }
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
    public DateTime ExpiresAt { get; set; }
    public DateTime? AcceptedAt { get; set; }
}

public sealed class PasswordReset
{
    public string TokenHash { get; set; } = "";
    public int UserId { get; set; }
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
    public DateTime ExpiresAt { get; set; }
    public DateTime? UsedAt { get; set; }
}

/// <summary>One installation. site_id is the &lt;site&gt; in the nodes' MQTT topics (SITE_ID in config.h).</summary>
public sealed class HvacSystem
{
    public int Id { get; set; }
    public int AccountId { get; set; }
    public string Name { get; set; } = "";
    public string SiteId { get; set; } = "";
    public string Refrigerant { get; set; } = "R-410A";
    public bool HeatPump { get; set; } = true;
    public string ObEnergized { get; set; } = "cool";   // "cool" = O, "heat" = B
    public double AtmPsia { get; set; } = 14.7;
    public DateTime? RunStartedAt { get; set; }
    public DateTime CreatedAt { get; set; } = Db.UtcNow();

    public CalcConfig CalcConfig() => new(Refrigerant, HeatPump, ObEnergized, AtmPsia);

    public JsonObject CalcConfigJson() => new()
    {
        ["refrigerant"] = Refrigerant, ["heat_pump"] = HeatPump, ["ob_energized"] = ObEnergized, ["atm_psia"] = AtmPsia,
    };
}

/// <summary>A node (outdoor, indoor, electrical, thermostat) of a system; created on its first message.</summary>
public sealed class Device
{
    public int Id { get; set; }
    public int SystemId { get; set; }
    public string Node { get; set; } = "";
    public string? Fw { get; set; }
    public string? Ip { get; set; }
    public string? Mac { get; set; }
    public int? Rssi { get; set; }
    public bool Connected { get; set; }
    public DateTime? LastSeen { get; set; }
    public string? LastStatus { get; set; }             // JSON
}

/// <summary>Raw telemetry JSON as each node sent it.</summary>
public sealed class Telemetry
{
    public int DeviceId { get; set; }
    public DateTime Time { get; set; }
    public string? Data { get; set; }                   // JSON
}

/// <summary>Derived system state after each telemetry message.</summary>
public sealed class Snapshot
{
    public int SystemId { get; set; }
    public DateTime Time { get; set; }
    public string Mode { get; set; } = "";
    public string? Data { get; set; }                   // JSON
}

/// <summary>A command sent to a node, and the node's reply once it arrives.</summary>
public sealed class Command
{
    public int Id { get; set; }
    public int DeviceId { get; set; }
    public string? Payload { get; set; }                // JSON; null = a reply nobody here asked for
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
    public bool Sent { get; set; }
    public string? Reply { get; set; }                  // JSON
    public DateTime? RepliedAt { get; set; }
}

/// <summary>A diagnostic flag seen on a system: pending until it has held ALERT_HOLD_SECONDS
/// (raised_at), cleared once it has been gone that long.</summary>
public sealed class Alert
{
    public int Id { get; set; }
    public int SystemId { get; set; }
    public string Code { get; set; } = "";
    public string? Node { get; set; }
    public string Level { get; set; } = "";
    public string Text { get; set; } = "";
    public DateTime StartedAt { get; set; }
    public DateTime LastSeen { get; set; }
    public DateTime? RaisedAt { get; set; }
    public DateTime? ClearedAt { get; set; }
    public DateTime? EmailedAt { get; set; }
    public DateTime? AckedAt { get; set; }               // someone is handling it: no "cleared" email
}

/// <summary>What is installed, entered by the technician. All optional.</summary>
public sealed class EquipmentRow
{
    public int SystemId { get; set; }
    public string? SystemType { get; set; }
    public string? Metering { get; set; }
    public double? Tonnage { get; set; }
    public double? ScTarget { get; set; }
    public double? ScTolerance { get; set; }
    public double? RatedBtuh { get; set; }
    public double? RatedCfm { get; set; }
    public double? MaxEsp { get; set; }
    public double? ElevationFt { get; set; }
    public double? CompRla { get; set; }
    public double? CompLra { get; set; }
    public double? FanFla { get; set; }
    public double? CapHermUf { get; set; }
    public double? CapFanUf { get; set; }
    public double? VoltMin { get; set; }
    public double? VoltMax { get; set; }

    public EquipmentInfo Info() => new()
    {
        SystemType = SystemType, Metering = Metering, Tonnage = Tonnage, ScTarget = ScTarget, ScTolerance = ScTolerance,
        RatedBtuh = RatedBtuh, RatedCfm = RatedCfm, MaxEsp = MaxEsp, ElevationFt = ElevationFt, CompRla = CompRla,
        CompLra = CompLra, FanFla = FanFla, CapHermUf = CapHermUf, CapFanUf = CapFanUf, VoltMin = VoltMin, VoltMax = VoltMax,
    };
}

/// <summary>The display thermostat's settings (homeowner) and safety settings (technician).
/// version goes up on every change; the thermostat reports the version it is running.</summary>
public sealed class ThermostatConfig
{
    public int SystemId { get; set; }
    public string Settings { get; set; } = "{}";        // JSON
    public string Tech { get; set; } = "{}";            // JSON
    public int Version { get; set; }
    public bool Sent { get; set; }
    public DateTime? UpdatedAt { get; set; }
}

public sealed class ServiceVisit
{
    public int Id { get; set; }
    public int SystemId { get; set; }
    public DateOnly Date { get; set; }
    public string Kind { get; set; } = "";              // tuneup, repair, install, inspection, other
    public string? Technician { get; set; }
    public string Work { get; set; } = "";
    public bool FilterChanged { get; set; }
    public string? Readings { get; set; }               // JSON
    public int? CreatedBy { get; set; }
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
}

/// <summary>The technician's gauge readings next to the system's own at the same moment (Api/Gauge.cs).</summary>
public sealed class GaugeCheck
{
    public int Id { get; set; }
    public int SystemId { get; set; }
    public DateTime Time { get; set; }                  // when the gauges were read
    public string? Technician { get; set; }
    public string? Note { get; set; }
    public string Refrigerant { get; set; } = "";
    public double AtmPsia { get; set; }
    public string? GaugeData { get; set; }              // JSON: entered + derived
    public string? SystemData { get; set; }             // JSON: nearest snapshot's values
    public int? CreatedBy { get; set; }
    public DateTime CreatedAt { get; set; } = Db.UtcNow();
}

/// <summary>Who a system's alerts are emailed to. Missing row = both.</summary>
public sealed class AlertPrefs
{
    public int SystemId { get; set; }
    public bool EmailOwner { get; set; } = true;
    public bool EmailContractor { get; set; }
}

/// <summary>No emails for this alert code on this system until `until` (alerts are still recorded).</summary>
public sealed class AlertMute
{
    public int SystemId { get; set; }
    public string Code { get; set; } = "";
    public DateTime Until { get; set; }
}

/// <summary>Seconds the blower (any mode but idle) and the compressor ran on a UTC day.</summary>
public sealed class RuntimeDay
{
    public int SystemId { get; set; }
    public DateOnly Day { get; set; }
    public double BlowerS { get; set; }
    public double CompressorS { get; set; }
}

/// <summary>The system's service contractor, shown to the homeowner.</summary>
public sealed class ServiceInfo
{
    public int SystemId { get; set; }
    public string? Name { get; set; }
    public string? Phone { get; set; }
    public string? Email { get; set; }
}

/// <summary>A recurring task ("filter", "tuneup"): due after interval_days, or interval_run_hours of
/// blower time, since last_done, whichever comes first.</summary>
public sealed class MaintenanceItem
{
    public int Id { get; set; }
    public int SystemId { get; set; }
    public string Kind { get; set; } = "";
    public int? IntervalDays { get; set; }
    public double? IntervalRunHours { get; set; }
    public DateOnly? LastDone { get; set; }
}
