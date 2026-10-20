using System.Data;
using System.Data.Common;
using System.Text.RegularExpressions;
using Microsoft.EntityFrameworkCore;

namespace Fullscope.Cloud.Data;

/// <summary>
/// Creates missing tables, and adds columns that were added to a model later (they must be
/// nullable), so an existing database keeps working (db.init_db / add_missing_columns). On
/// PostgreSQL it also turns telemetry and snapshots into TimescaleDB hypertables with retention.
/// </summary>
public static class Schema
{
    public static void Ensure(Db db, int keepDays)
    {
        var conn = db.Database.GetDbConnection();
        db.Database.OpenConnection();
        try
        {
            if (db.IsSqlite && new Microsoft.Data.Sqlite.SqliteConnectionStringBuilder(conn.ConnectionString).DataSource
                    is { Length: > 0 } file && file != ":memory:" && !file.StartsWith("file::memory:", StringComparison.Ordinal))
                Exec(conn, "PRAGMA journal_mode=WAL");      // ingest and API share the file
            var tables = ExistingTables(db, conn);
            var script = db.Database.GenerateCreateScript();
            foreach (var stmt in Regex.Split(script, @";\s*(?:\r?\n|$)").Select(s => s.Trim()).Where(s => s.Length > 0))
            {
                var create = Regex.Match(stmt, "^CREATE TABLE \"?(\\w+)\"?", RegexOptions.IgnoreCase);
                if (create.Success)
                {
                    if (!tables.Contains(create.Groups[1].Value)) Exec(conn, stmt);
                    continue;
                }
                if (Regex.IsMatch(stmt, "^CREATE (UNIQUE )?INDEX", RegexOptions.IgnoreCase))
                    Exec(conn, Regex.Replace(stmt, "^CREATE (UNIQUE )?INDEX ", "CREATE $1INDEX IF NOT EXISTS ", RegexOptions.IgnoreCase));
            }
            AddMissingColumns(db, conn);
            EnsureAdmins(db);
            EnsureStaff(db);
            EnsureOrderTickets(db);
            if (!db.IsSqlite)
            {
                Exec(conn, "CREATE EXTENSION IF NOT EXISTS timescaledb");
                foreach (var t in new[] { "telemetry", "snapshots" })
                {
                    Exec(conn, $"SELECT create_hypertable('{t}', 'time', if_not_exists => TRUE, migrate_data => TRUE)");
                    Exec(conn, $"SELECT add_retention_policy('{t}', INTERVAL '{keepDays} days', if_not_exists => TRUE)");
                }
            }
        }
        finally
        {
            db.Database.CloseConnection();
        }
    }

    /// <summary>Every account keeps at least one admin: when it has none (a database from before admins
    /// existed, or the last admin was removed), its first contractor becomes one (db.ensure_admins).</summary>
    public static void EnsureAdmins(Db s)
    {
        foreach (var accountId in s.Users.Where(u => u.Role == "contractor").Select(u => u.AccountId).Distinct().ToList())
        {
            var team = s.Users.Where(u => u.Role == "contractor" && u.AccountId == accountId).OrderBy(u => u.Id).ToList();
            if (team.Count > 0 && !team.Any(u => u.Admin == true)) team[0].Admin = true;
        }
        s.SaveChanges();
    }

    /// <summary>Someone can always open the Office pages: when nobody is staff, the first contractor admin
    /// (the owner's own sign-in on a new server) becomes staff (db.ensure_staff).</summary>
    public static void EnsureStaff(Db s)
    {
        if (!s.Users.Any(u => u.Staff == true)
            && s.Users.Where(u => u.Role == "contractor" && u.Admin == true).OrderBy(u => u.Id).FirstOrDefault() is User first)
            first.Staff = true;
        s.SaveChanges();
    }

    /// <summary>Order requests saved before the Office inbox existed get their inbox ticket (db.ensure_order_tickets).</summary>
    public static void EnsureOrderTickets(Db s)
    {
        var have = s.Tickets.Where(t => t.OrderId != null).Select(t => t.OrderId!.Value).ToHashSet();
        foreach (var order in s.Orders.OrderBy(o => o.Id).ToList())
            if (!have.Contains(order.Id)) s.Tickets.Add(Ticket.ForOrder(order));
        s.SaveChanges();
    }

    static HashSet<string> ExistingTables(Db db, DbConnection conn) => Query(conn,
        db.IsSqlite ? "SELECT name FROM sqlite_master WHERE type='table'"
                    : "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()");

    static HashSet<string> Columns(Db db, DbConnection conn, string table) => Query(conn,
        db.IsSqlite ? $"SELECT name FROM pragma_table_info('{table}')"
                    : $"SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = '{table}'");

    static void AddMissingColumns(Db db, DbConnection conn)
    {
        foreach (var entity in db.Model.GetEntityTypes())
        {
            var table = entity.GetTableName()!;
            var have = Columns(db, conn, table);
            foreach (var p in entity.GetProperties())
            {
                var name = p.GetColumnName();
                if (have.Contains(name)) continue;
                if (!p.IsNullable)
                    throw new InvalidOperationException($"can't add NOT NULL column {table}.{name} automatically");
                Exec(conn, $"ALTER TABLE {table} ADD COLUMN {name} {p.GetColumnType()}");
            }
        }
    }

    static HashSet<string> Query(DbConnection conn, string sql)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        using var r = cmd.ExecuteReader();
        var set = new HashSet<string>(StringComparer.Ordinal);
        while (r.Read()) set.Add(r.GetString(0));
        return set;
    }

    static void Exec(DbConnection conn, string sql)
    {
        using var cmd = conn.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }
}
