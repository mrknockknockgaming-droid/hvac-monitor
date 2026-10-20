using Fullscope.Cloud.Data;
using Fullscope.Core;
using Microsoft.EntityFrameworkCore;

namespace Fullscope.Cloud.Api;

/// <summary>Who is making a request: a signed-in user, or an account API key (acts as an admin contractor).</summary>
public sealed record Principal(string Role, int? AccountId, int? UserId = null, string Via = "session", bool Admin = false, bool Staff = false)
{
    public bool IsTech => Role == "contractor";

    /// <summary>A contractor who runs the account: fleet page and team.</summary>
    public bool IsAdmin => IsTech && Admin;

    /// <summary>Which pages this person gets: "admin" (fleet, team and the technician pages),
    /// "technician" (the technician pages only) or "homeowner" (their home page only).</summary>
    public string Level => IsAdmin ? "admin" : IsTech ? "technician" : "homeowner";

    public static Principal ForUser(User user) => new(user.Role, user.AccountId, user.Id, Admin: user.Admin == true, Staff: user.Staff == true);
}

/// <summary>
/// Sign-in sessions (port of hvaccloud/auth.py). A sign-in creates a random token that goes to the
/// browser in an HttpOnly cookie; only its SHA-256 is stored. Browsers send cookies with every
/// request, so a request that changes something and is signed in by the cookie must also carry
/// X-Requested-With, which other sites can't add without asking this server first (CORS, never allowed).
/// </summary>
public static class Auth
{
    public const string Cookie = "fs_session";
    public const string CsrfHeader = "X-Requested-With";
    public static readonly TimeSpan TouchEvery = TimeSpan.FromMinutes(5);   // how often last_seen is written

    /// <summary>Create a session; returns the token for the cookie.</summary>
    public static string NewSession(Db s, int userId, double sessionDays)
    {
        var now = Db.UtcNow();
        var token = Db.TokenUrlSafe(32);
        s.UserSessions.Add(new UserSession
        {
            TokenHash = Db.HashKey(token), UserId = userId, CreatedAt = now, LastSeen = now,
            ExpiresAt = now + TimeSpan.FromDays(sessionDays),
        });
        return token;
    }

    /// <summary>The user id of a valid session token (sliding expiry), or null.</summary>
    public static int? SessionUserId(Db s, string? token, double sessionDays)
    {
        if (string.IsNullOrEmpty(token)) return null;
        var now = Db.UtcNow();
        var row = s.UserSessions.Find(Db.HashKey(token));
        if (row is null) return null;
        if (row.ExpiresAt <= now)
        {
            s.UserSessions.Remove(row);
            s.SaveChanges();
            return null;
        }
        if (now - row.LastSeen >= TouchEvery)
        {
            row.LastSeen = now;
            row.ExpiresAt = now + TimeSpan.FromDays(sessionDays);
            s.SaveChanges();
        }
        return row.UserId;
    }

    /// <summary>Sign the user out everywhere except the session `keep` (a cookie token, or null for all).</summary>
    public static void EndOtherSessions(Db s, int userId, string? keep)
    {
        var q = s.UserSessions.Where(x => x.UserId == userId);
        if (!string.IsNullOrEmpty(keep))
        {
            var h = Db.HashKey(keep);
            q = q.Where(x => x.TokenHash != h);
        }
        foreach (var row in q.ToList()) s.UserSessions.Remove(row);
    }

    public static void EndSession(Db s, string? token)
    {
        var row = string.IsNullOrEmpty(token) ? null : s.UserSessions.Find(Db.HashKey(token));
        if (row is null) return;
        s.UserSessions.Remove(row);
        s.SaveChanges();
    }
}

/// <summary>Slows down password guessing: after `limit` failures for one email within `window`
/// seconds, sign-in for that email is refused until the window passes.</summary>
public sealed class Throttle(int limit = 8, double window = 15 * 60)
{
    readonly Dictionary<string, List<double>> _fails = [];
    readonly Lock _lock = new();

    List<double> Recent(string key, double now) =>
        _fails.TryGetValue(key, out var l) ? l.Where(t => now - t < window).ToList() : [];

    public bool Blocked(string key, double? now = null)
    {
        double t = now ?? Time.Now();
        lock (_lock) return Recent(key, t).Count >= limit;
    }

    public void Fail(string key, double? now = null)
    {
        double t = now ?? Time.Now();
        lock (_lock)
        {
            if (_fails.Count > 1000)                       // forget emails whose failures have all aged out
            {
                foreach (var k in _fails.Keys.ToList())
                    if (Recent(k, t).Count == 0) _fails.Remove(k);
            }
            var recent = Recent(key, t);
            recent.Add(t);
            _fails[key] = recent;
        }
    }

    public void Clear(string key)
    {
        lock (_lock) _fails.Remove(key);
    }
}
