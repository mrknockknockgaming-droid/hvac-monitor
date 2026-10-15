using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace Fullscope.Cloud.Data;

/// <summary>Opens a short-lived Db per unit of work (sessions() in the Python code).</summary>
public sealed class DbFactory
{
    readonly DbContextOptions<Db> _options;
    readonly SqliteConnection? _keepAlive;

    public DbFactory(DbContextOptions<Db> options, SqliteConnection? keepAlive = null)
    {
        _options = options;
        _keepAlive = keepAlive;
    }

    public static DbFactory FromUrl(string url) => new(Db.Options(url));

    /// <summary>A private in-memory SQLite database that lives as long as this factory (tests).</summary>
    public static DbFactory InMemory()
    {
        var conn = new SqliteConnection("Data Source=:memory:;Foreign Keys=True");
        conn.Open();
        var options = new DbContextOptionsBuilder<Db>().UseSqlite(conn).Options;
        return new DbFactory(options, conn);
    }

    public Db Open() => new(_options);

    public bool IsSqlite
    {
        get
        {
            using var db = Open();
            return db.IsSqlite;
        }
    }

    /// <summary>Create missing tables and columns (init_db).</summary>
    public DbFactory Init(int keepDays = 365)
    {
        using var db = Open();
        Schema.Ensure(db, keepDays);
        return this;
    }
}
