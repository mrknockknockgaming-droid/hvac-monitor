using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Cloud.Ingest;
using Fullscope.Core;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.StaticFiles;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

namespace Fullscope.Cloud.Api;

/// <summary>One request: its database session, body and who is asking (worked out once).</summary>
public sealed class Req(HttpContext http, Db s, ApiServer api, JsonNode? body, bool bodyPresent)
{
    public HttpContext Http { get; } = http;
    public Db S { get; } = s;
    public JsonNode? Body { get; } = body;
    public bool BodyPresent { get; } = bodyPresent;
    Principal? _who;

    public Principal Who => _who ??= api.Authenticate(this);

    public string? Route(string name) => Http.Request.RouteValues.TryGetValue(name, out var v) ? v as string : null;

    public string? Query(string name) => Http.Request.Query.TryGetValue(name, out var v) && v.Count > 0 ? v[^1] : null;

    public string? Cookie => Http.Request.Cookies[Auth.Cookie];
}

/// <summary>
/// REST API for accounts' systems and the web app (port of hvaccloud/api.py; same paths, bodies and
/// answers, so web/app.js is unchanged). Requests are signed in with the session cookie
/// (POST /api/auth/login) or an account API key in X-API-Key. Contractors (and API keys) see all
/// their account's systems; homeowners see the systems they are members of, read-only apart from
/// marking the air filter changed.
/// </summary>
public sealed partial class ApiServer
{
    public const int InviteDays = 7;
    public const int ResetHours = 1;               // a forgotten-password link works this long, once
    static readonly System.Text.RegularExpressions.Regex EmailRe = new(@"^[^@\s]+@[^@\s]+\.[^@\s]+$");
    static readonly Dictionary<string, int> LevelRank = new() { ["fault"] = 4, ["offline"] = 3, ["caution"] = 2, ["advisory"] = 1, ["ok"] = 0 };
    internal static readonly string[] CsvCols = ["mode", "p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc",
                                        "oat", "orh", "t_ret", "t_sup", "dt", "ctoa", "approach", "Y", "W", "G", "OB"];
    static readonly string[] SystemTypes = ["split_hp", "split_ac", "packaged_hp", "packaged_ac"];
    static readonly string[] EquipmentFields = ["system_type", "metering", "tonnage", "sc_target", "sc_tolerance", "rated_btuh", "rated_cfm",
                                                "max_esp", "elevation_ft", "comp_rla", "comp_lra", "fan_fla", "cap_herm_uf", "cap_fan_uf", "volt_min", "volt_max"];

    readonly DbFactory _sessions;
    readonly Settings _settings;
    readonly Func<string, JsonNode, bool, bool> _publish;
    readonly IMailer _mailer;
    RefrigerantTables? _tables;                    // saturation tables, loaded on first gauge check
    RefrigerantTables Tables => _tables ??= RefrigerantTables.Load();
    readonly double _stale;
    readonly bool _sendAsync;
    readonly ILogger _log;
    readonly Throttle _throttle = new();
    readonly Throttle _ipThrottle = new(limit: 30, window: 15 * 60);       // failed sign-ins per address, any email
    readonly Throttle _resetThrottle = new(limit: 3, window: 60 * 60);    // reset emails per address per hour
    readonly Throttle _orderThrottle = new(limit: 5, window: 60 * 60);    // landing-page order requests per address per hour
    readonly HistoryCache? _cache;
    readonly Pusher _pusher;

    /// <param name="publish">(topic, payload, retain) -> sent; MQTT commands and the thermostat's config.</param>
    public ApiServer(DbFactory sessions, Settings settings, Func<string, JsonNode, bool, bool> publish, IMailer mailer,
                     double? stale = null, bool sendAsync = true, ILogger? log = null, bool cache = true,
                     RefrigerantTables? tables = null, Func<string, byte[], Dictionary<string, string>, int>? pushPost = null)
    {
        _pusher = new Pusher(sessions, settings, log, pushPost);
        _tables = tables;
        _sessions = sessions;
        _settings = settings;
        _publish = publish;
        _mailer = mailer;
        _stale = stale ?? settings.StaleSeconds;
        _sendAsync = sendAsync;
        _log = log ?? NullLogger.Instance;
        _cache = cache ? new HistoryCache() : null;
    }

    T Cached<T>(Db s, int systemId, string kind, double param, Func<T> compute) where T : class =>
        _cache is null ? compute() : _cache.Get(s, systemId, kind, param, compute);

    // ---------- plumbing ----------

    public static string? Iso(DateTime? t)
    {
        if (t is not DateTime d) return null;
        var s = d.ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture);
        long us = d.Ticks % 10_000_000 / 10;
        if (us != 0) s += "." + us.ToString("D6", CultureInfo.InvariantCulture);
        return s + "+00:00";
    }

    static double Seconds(TimeSpan span) => span.Ticks / 1e7;

    static JsonNode? Load(string? json) => json is null ? null : PyJson.Loads(json);

    /// <summary>The routes, the web app under /app/, and FastAPI-style error answers.</summary>
    public void Map(WebApplication app)
    {
        app.UseStatusCodePages(async ctx =>
        {
            var r = ctx.HttpContext.Response;
            if (r.HasStarted || r.ContentLength > 0) return;
            string detail = r.StatusCode switch { 404 => "Not Found", 405 => "Method Not Allowed", _ => ReasonPhrases(r.StatusCode) };
            r.ContentType = "application/json";
            if (ctx.HttpContext.Request.Path.StartsWithSegments("/app")) r.Headers.CacheControl = "no-cache";
            await r.WriteAsync(Py.Dumps(new JsonObject { ["detail"] = detail }, compact: true, ensureAscii: false));
        });
        if (Directory.Exists(_settings.WebDir))
        {
            var files = new PhysicalFileProvider(Path.GetFullPath(_settings.WebDir));
            app.Use((ctx, next) =>
            {
                if (ctx.Request.Path.Value != "/app") return next(ctx);
                ctx.Response.StatusCode = 307;
                ctx.Response.Headers.Location = "/app/" + ctx.Request.QueryString;
                return Task.CompletedTask;
            });
            var types = new FileExtensionContentTypeProvider();
            types.Mappings[".woff"] = "font/woff";
            types.Mappings[".webp"] = "image/webp";
            app.UseDefaultFiles(new DefaultFilesOptions { FileProvider = files, RequestPath = "/app", DefaultFileNames = ["index.html"] });
            app.UseStaticFiles(new StaticFileOptions
            {
                FileProvider = files, RequestPath = "/app", ContentTypeProvider = types,
                // browsers must check for a newer web app on every load instead of reusing old copies
                OnPrepareResponse = c => c.Context.Response.Headers.CacheControl = "no-cache",
            });
        }
        // public landing page (web/welcome.html); straight to the web app if it isn't there
        app.MapGet("/", (HttpContext ctx) =>
        {
            string page = Path.GetFullPath(Path.Combine(_settings.WebDir, "welcome.html"));
            if (!File.Exists(page)) return Results.Redirect("/app/", permanent: false, preserveMethod: true);
            ctx.Response.Headers.CacheControl = "no-cache";
            return Results.File(page, "text/html; charset=utf-8");
        });
        Routes(app);
    }

    static string ReasonPhrases(int status) => Microsoft.AspNetCore.WebUtilities.ReasonPhrases.GetReasonPhrase(status);

    delegate object? Handler(Req r);

    void Route(WebApplication app, string method, string pattern, Handler h, bool body = false) =>
        app.MapMethods(pattern, [method], (HttpContext ctx) => Run(ctx, h, body));

    async Task Run(HttpContext ctx, Handler h, bool hasBody)
    {
        JsonNode? body = null;
        bool present = false;
        if (hasBody)
        {
            using var ms = new MemoryStream();
            await ctx.Request.Body.CopyToAsync(ms);
            if (ms.Length > 0)
            {
                present = true;
                var ct = ctx.Request.ContentType;
                var bytes = ms.ToArray();
                if (ct is null || IsJson(ct))
                {
                    try
                    {
                        body = PyJson.Loads(bytes);
                    }
                    catch (Exception e) when (e is System.Text.Json.JsonException or DecoderFallbackException or ArgumentException)
                    {
                        await Write(ctx, 422, Detail(ApiError.Invalid(ApiError.Problem("json_invalid", "JSON decode error", null, "body", 0)).Detail));
                        return;
                    }
                }
                else
                {
                    body = JsonValue.Create(Encoding.UTF8.GetString(bytes));   // not JSON: fails the model check
                }
            }
        }
        object? result;
        try
        {
            using var db = _sessions.Open();
            result = h(new Req(ctx, db, this, body, present));
        }
        catch (ApiError e)
        {
            await Write(ctx, e.Status, Detail(e.Detail));
            return;
        }
        if (result is IResult res) await res.ExecuteAsync(ctx);
        else await Write(ctx, 200, result as JsonNode);
    }

    static bool IsJson(string contentType)
    {
        var mt = contentType.Split(';')[0].Trim().ToLowerInvariant();
        return mt == "application/json" || mt.StartsWith("application/", StringComparison.Ordinal) && mt.EndsWith("+json", StringComparison.Ordinal);
    }

    static JsonObject Detail(JsonNode? d) => new() { ["detail"] = d };

    static Task Write(HttpContext ctx, int status, JsonNode? json)
    {
        ctx.Response.StatusCode = status;
        ctx.Response.ContentType = "application/json";
        return ctx.Response.WriteAsync(Py.Dumps(json, compact: true, ensureAscii: false));
    }

    // ---------- who is asking ----------

    internal Principal Authenticate(Req r)
    {
        var s = r.S;
        var key = r.Http.Request.Headers["X-API-Key"].ToString();
        if (key.Length > 0)
        {
            var h = Db.HashKey(key);
            var row = s.ApiKeys.FirstOrDefault(k => k.KeyHash == h) ?? throw new ApiError(401, "invalid API key");
            row.LastUsed = Db.UtcNow();
            s.SaveChanges();
            return new Principal("contractor", row.AccountId, Via: "key", Admin: true);
        }
        var userId = Auth.SessionUserId(s, r.Cookie, _settings.SessionDays);
        var user = userId is int id ? s.Users.Find(id) : null;
        if (user is null) throw new ApiError(401, "sign in required");
        if (r.Http.Request.Method is not ("GET" or "HEAD") && r.Http.Request.Headers[Auth.CsrfHeader].ToString().Length == 0)
            throw new ApiError(403, $"missing {Auth.CsrfHeader} header");
        return Principal.ForUser(user);
    }

    static IQueryable<HvacSystem> Visible(Db s, Principal who) => who.IsTech
        ? s.Systems.Where(x => x.AccountId == who.AccountId)
        : s.Systems.Where(x => s.SystemMembers.Any(m => m.SystemId == x.Id && m.UserId == who.UserId));

    static HvacSystem OwnSystem(Req r)
    {
        var who = r.Who;
        int id = Validate.PathInt("system_id", r.Route("system_id"));
        return Visible(r.S, who).FirstOrDefault(x => x.Id == id) ?? throw new ApiError(404, "no such system");
    }

    /// <summary>Technician-only endpoints: settings, commands, calibration, alert handling.</summary>
    static HvacSystem TechSystem(Req r)
    {
        var system = OwnSystem(r);
        if (!r.Who.IsTech) throw new ApiError(403, "for your contractor only");
        return system;
    }

    static void TechOnly(Principal who)
    {
        if (!who.IsTech) throw new ApiError(403, "for your contractor only");
    }

    /// <summary>The account's own pages: fleet overview and teammates.</summary>
    static void AdminOnly(Principal who)
    {
        if (!who.IsAdmin) throw new ApiError(403, "for your account's admins only");
    }

    static JsonObject MeView(Db s, Principal who)
    {
        var user = who.UserId is int uid ? s.Users.Find(uid) : null;
        var acct = who.AccountId is int aid ? s.Accounts.Find(aid) : null;
        return new JsonObject
        {
            ["role"] = who.Role, ["level"] = who.Level, ["via"] = who.Via, ["staff"] = who.Staff,
            ["user"] = user is null ? null : new JsonObject { ["id"] = user.Id, ["email"] = user.Email, ["name"] = user.Name },
            ["account"] = acct is null ? null : new JsonObject { ["id"] = acct.Id, ["name"] = acct.Name },
        };
    }

    void SetCookie(Req r, string token) =>
        r.Http.Response.Cookies.Append(Auth.Cookie, token, new CookieOptions
        {
            MaxAge = TimeSpan.FromSeconds((int)(_settings.SessionDays * 86400)), HttpOnly = true,
            SameSite = SameSiteMode.Lax, Secure = _settings.CookieSecure, Path = "/",
        });

    JsonObject DeviceView(Device d, DateTime now)
    {
        double? age = d.LastSeen is DateTime seen ? Seconds(now - seen) : null;
        return new JsonObject
        {
            ["id"] = d.Id, ["node"] = d.Node, ["online"] = age is double a && a <= _stale && d.Connected,
            ["age"] = age is double x ? Py.Round(x, 1) : null, ["fw"] = d.Fw, ["ip"] = d.Ip, ["rssi"] = d.Rssi,
            ["status"] = Load(d.LastStatus),
        };
    }

    static JsonObject SystemView(HvacSystem system)
    {
        var o = new JsonObject { ["id"] = system.Id, ["name"] = system.Name, ["site_id"] = system.SiteId };
        foreach (var (k, v) in system.CalcConfigJson()) o[k] = v?.DeepClone();
        return o;
    }

    static JsonObject AlertView(Alert a) => new()
    {
        ["id"] = a.Id, ["code"] = a.Code, ["node"] = a.Node, ["level"] = a.Level, ["text"] = a.Text,
        ["open"] = a.ClearedAt is null, ["started_at"] = Iso(a.StartedAt), ["raised_at"] = Iso(a.RaisedAt),
        ["cleared_at"] = Iso(a.ClearedAt), ["emailed_at"] = Iso(a.EmailedAt), ["acked_at"] = Iso(a.AckedAt),
    };

    static JsonObject CommandView(Command c, string node) => new()
    {
        ["id"] = c.Id, ["node"] = node, ["cmd"] = Load(c.Payload), ["sent"] = c.Sent,
        ["created_at"] = Iso(c.CreatedAt), ["reply"] = Load(c.Reply), ["replied_at"] = Iso(c.RepliedAt),
    };

    static string FluidList() => "[" + string.Join(", ", RefrigerantTables.Fluids.Select(f => $"'{f}'")) + "]";

    /// <summary>timedelta(minutes=m): whole microseconds.</summary>
    static TimeSpan Minutes(double m) => TimeSpan.FromTicks((long)Math.Round(m * 60e6, MidpointRounding.ToEven) * 10);

    /// <summary>A done date: not in the future (a day of slack for time zones) and not absurdly old.</summary>
    static string? PastDate(DateOnly d) =>
        d >= new DateOnly(2000, 1, 1) && d <= Time.Today().AddDays(1) ? null : "the date must be between 2000 and today";

    static Model Body(Req r, params Field[] fields) => Validate.Body(r.Body, r.BodyPresent, fields);

    static Model? OptionalBody(Req r, params Field[] fields) => r.BodyPresent && r.Body is not null ? Body(r, fields) : null;

    // ---------- routes ----------

    void Routes(WebApplication app)
    {
        Route(app, "GET", "/health", _ => new JsonObject { ["ok"] = true });

        // sign-in
        Route(app, "POST", "/api/auth/login", Login, body: true);
        Route(app, "POST", "/api/auth/logout", Logout);
        Route(app, "GET", "/api/auth/me", r => MeView(r.S, r.Who));
        Route(app, "POST", "/api/auth/password", ChangePassword, body: true);
        Route(app, "GET", "/api/auth/2fa", TwoFactorState);
        Route(app, "POST", "/api/auth/2fa/setup", TwoFactorSetup, body: true);
        Route(app, "POST", "/api/auth/2fa/enable", TwoFactorEnable, body: true);
        Route(app, "POST", "/api/auth/2fa/disable", TwoFactorDisable, body: true);
        Route(app, "GET", "/api/push/key", PushKey);
        Route(app, "POST", "/api/push/subscribe", PushSubscribe, body: true);
        Route(app, "POST", "/api/push/unsubscribe", PushUnsubscribe, body: true);
        Route(app, "POST", "/api/push/test", PushTest);
        Route(app, "POST", "/api/orders", PlaceOrder, body: true);
        Route(app, "POST", "/api/auth/forgot", ForgotPassword, body: true);
        Route(app, "POST", "/api/systems/{system_id}/members/{user_id}/reset-link", MemberResetLink);
        Route(app, "POST", "/api/auth/reset", ResetPassword, body: true);

        // equipment and the display thermostat
        Route(app, "GET", "/api/systems/{system_id}/equipment", r =>
        {
            var system = TechSystem(r);
            return EquipmentView(r.S.Equipment.Find(system.Id), system);
        });
        Route(app, "PUT", "/api/systems/{system_id}/equipment", SetEquipment, body: true);
        Route(app, "GET", "/api/systems/{system_id}/thermostat", r =>
        {
            var system = OwnSystem(r);
            return TstatView(r.S, system, r.S.ThermostatConfigs.Find(system.Id));
        });
        Route(app, "GET", "/api/systems/{system_id}/thermostat/display", r => Display.Build(r.S, OwnSystem(r), Db.Epoch(Db.UtcNow()), _stale));
        Route(app, "PUT", "/api/systems/{system_id}/thermostat", SetThermostat, body: true);
        Route(app, "POST", "/api/systems/{system_id}/thermostat/hold", HoldThermostat, body: true);
        Route(app, "DELETE", "/api/systems/{system_id}/thermostat/hold", r =>
        {
            var system = OwnSystem(r);
            var row = TstatRow(r.S, system);
            var st = Thermostat.Merged(Load(row.Settings));
            st["hold"] = null;
            return TstatSave(r.S, system, row, st, null);
        });
        Route(app, "PUT", "/api/systems/{system_id}/thermostat/tech", SetThermostatTech, body: true);

        // invites and people
        Route(app, "POST", "/api/systems/{system_id}/invites", InviteHomeowner, body: true);
        Route(app, "GET", "/api/systems/{system_id}/people", SystemPeople);
        Route(app, "DELETE", "/api/systems/{system_id}/members/{user_id}", RemoveMember);
        Route(app, "POST", "/api/account/invites", InviteTeammate, body: true);
        Route(app, "GET", "/api/account/people", AccountPeople);
        Route(app, "DELETE", "/api/account/users/{user_id}", RemoveTeammate);
        Route(app, "PATCH", "/api/account/users/{user_id}", SetAdmin, body: true);
        Route(app, "DELETE", "/api/invites/{invite_id}", RevokeInvite);
        Route(app, "POST", "/api/invites/lookup", InviteInfo, body: true);
        Route(app, "POST", "/api/invites/accept", AcceptInvite, body: true);

        // systems
        OfficeRoutes(app);
        Route(app, "GET", "/api/fleet", Fleet);
        Route(app, "GET", "/api/refrigerants", _ => new JsonArray(RefrigerantTables.Fluids.Select(f => (JsonNode?)f).ToArray()));
        Route(app, "GET", "/api/systems", r =>
            new JsonArray(Visible(r.S, r.Who).OrderBy(x => x.Id).AsEnumerable().Select(x => (JsonNode?)SystemView(x)).ToArray()));
        Route(app, "GET", "/api/systems/{system_id}", GetSystem);
        Route(app, "PATCH", "/api/systems/{system_id}", UpdateSystem, body: true);
        Route(app, "GET", "/api/systems/{system_id}/latest", Latest);
        Route(app, "GET", "/api/systems/{system_id}/summary", Summary);
        Route(app, "GET", "/api/systems/{system_id}/history", History);
        Route(app, "GET", "/api/systems/{system_id}/export.csv", ExportCsv);

        // alerts
        Route(app, "GET", "/api/systems/{system_id}/alerts", ListAlerts);
        Route(app, "POST", "/api/systems/{system_id}/alerts/{alert_id}/ack", AckAlert, body: true);
        Route(app, "GET", "/api/systems/{system_id}/alert-settings", r => AlertSettingsView(r.S, TechSystem(r)));
        Route(app, "PUT", "/api/systems/{system_id}/alert-settings", SetAlertSettings, body: true);
        Route(app, "PUT", "/api/systems/{system_id}/alert-mutes/{code}", MuteAlerts, body: true);

        // service
        Route(app, "GET", "/api/systems/{system_id}/service", r => Service.ServiceView(r.S, OwnSystem(r).Id));
        Route(app, "PUT", "/api/systems/{system_id}/service/contractor", SetContractor, body: true);
        Route(app, "PATCH", "/api/systems/{system_id}/service/items/{kind}", UpdateItem, body: true);
        Route(app, "POST", "/api/systems/{system_id}/service/items/{kind}/done", ItemDone, body: true);
        Route(app, "GET", "/api/systems/{system_id}/visits", ListVisits);
        Route(app, "POST", "/api/systems/{system_id}/visits", AddVisit, body: true);
        Route(app, "DELETE", "/api/systems/{system_id}/visits/{visit_id}", DeleteVisit);
        Route(app, "GET", "/api/systems/{system_id}/threshold-report", ThresholdReport);
        Route(app, "GET", "/api/systems/{system_id}/gauge-checks", ListGaugeChecks);
        Route(app, "POST", "/api/systems/{system_id}/gauge-checks", AddGaugeCheck, body: true);
        Route(app, "DELETE", "/api/systems/{system_id}/gauge-checks/{check_id}", DeleteGaugeCheck);

        // node commands
        Route(app, "POST", "/api/systems/{system_id}/commands", SendCommand, body: true);
        Route(app, "GET", "/api/systems/{system_id}/commands", ListCommands);
    }

    // ---------- sign-in ----------

    object? Login(Req r)
    {
        var b = Body(r, Field.Str("email", 320, required: true), Field.Str("password", 200, required: true), Field.Str("code", 20));
        var email = b.Str("email")!.Trim().ToLowerInvariant();
        var ip = ClientIp(r.Http);
        if (_throttle.Blocked(email) || _ipThrottle.Blocked(ip)) throw new ApiError(429, "Too many attempts. Try again in 15 minutes.");
        var user = r.S.Users.FirstOrDefault(u => u.Email == email);
        var stored = user is not null && !string.IsNullOrEmpty(user.PasswordHash) ? user.PasswordHash : Passwords.Dummy;
        if (!Passwords.Check(b.Str("password")!, stored) || user is null)     // same work either way
        {
            _throttle.Fail(email);
            _ipThrottle.Fail(ip);
            throw new ApiError(401, "Email or password is not right.");
        }
        if (user.TotpOn is not null)
        {
            var code = b.Str("code");
            if (string.IsNullOrEmpty(code)) return new JsonObject { ["totp_required"] = true };   // the browser asks for the code and sends all three again
            if (!Totp.SecondFactor(user, code))
            {
                _throttle.Fail(email);
                _ipThrottle.Fail(ip);
                throw new ApiError(401, "That code is not right. Use the newest code from your authenticator app, or a recovery code.");
            }
        }
        _throttle.Clear(email);
        user.LastLogin = Db.UtcNow();
        var token = Auth.NewSession(r.S, user.Id, _settings.SessionDays);
        r.S.SaveChanges();
        SetCookie(r, token);
        return MeView(r.S, Principal.ForUser(user));
    }

    object? Logout(Req r)
    {
        Auth.EndSession(r.S, r.Cookie);
        r.Http.Response.Cookies.Delete(Auth.Cookie, new CookieOptions { Path = "/", SameSite = SameSiteMode.Lax });
        return new JsonObject { ["ok"] = true };
    }

    object? ChangePassword(Req r)
    {
        var who = r.Who;
        var b = Body(r, Field.Str("current", 200, required: true), Field.Str("new", 200, required: true));
        var user = who.UserId is int uid ? r.S.Users.Find(uid) : null;
        if (user is null) throw new ApiError(400, "API keys have no password");
        if (!Passwords.Check(b.Str("current")!, user.PasswordHash)) throw new ApiError(401, "The current password is not right.");
        if (Passwords.Problem(b.Str("new")!) is string problem) throw new ApiError(422, problem);
        user.PasswordHash = Passwords.Hash(b.Str("new")!);
        Auth.EndOtherSessions(r.S, user.Id, r.Cookie);              // anyone else signed in as this user is out
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true };
    }

    /// <summary>The browser's address. Behind the Cloudflare Tunnel every request arrives from this machine,
    /// and cloudflared names the real one in CF-Connecting-IP.</summary>
    internal static string ClientIp(HttpContext http)
    {
        var addr = http.Connection.RemoteIpAddress;
        var cf = http.Request.Headers["CF-Connecting-IP"].ToString().Trim();
        if ((addr is null || System.Net.IPAddress.IsLoopback(addr)) && cf.Length > 0) return cf;
        return addr?.ToString() ?? "";
    }

    // ---------- two-step sign-in (authenticator app) ----------

    static User PasswordUser(Req r) =>
        (r.Who.UserId is int uid ? r.S.Users.Find(uid) : null) ?? throw new ApiError(400, "API keys have no password");

    object? TwoFactorState(Req r)
    {
        var user = PasswordUser(r);
        bool on = user.TotpOn is not null;
        return new JsonObject { ["enabled"] = on, ["recovery_left"] = on ? Totp.RecoveryLeft(user).Count : 0 };
    }

    /// <summary>A new authenticator secret. It only counts once a code from it is confirmed (enable).</summary>
    object? TwoFactorSetup(Req r)
    {
        var user = PasswordUser(r);
        var b = Body(r, Field.Str("password", 200, required: true));
        if (!Passwords.Check(b.Str("password")!, user.PasswordHash)) throw new ApiError(401, "The password is not right.");
        if (user.TotpOn is not null) throw new ApiError(409, "Two-step sign-in is already on.");
        user.TotpSecret = Totp.NewSecret();
        r.S.SaveChanges();
        return new JsonObject { ["secret"] = user.TotpSecret, ["uri"] = Totp.Uri(user.TotpSecret, user.Email) };
    }

    object? TwoFactorEnable(Req r)
    {
        var user = PasswordUser(r);
        var b = Body(r, Field.Str("code", 20, required: true));
        if (user.TotpOn is not null) throw new ApiError(409, "Two-step sign-in is already on.");
        if (string.IsNullOrEmpty(user.TotpSecret)) throw new ApiError(409, "Start the setup first.");
        if (Totp.Check(user.TotpSecret, b.Str("code")) is not int step)
            throw new ApiError(422, "That code is not right. Check that the phone's clock is set automatically and try the newest code.");
        var (codes, hashes) = Totp.NewRecoveryCodes();
        user.TotpOn = Db.UtcNow();
        user.TotpLastStep = step;
        user.RecoveryCodes = Py.Dumps(new JsonArray([.. hashes.Select(h => (JsonNode)JsonValue.Create(h)!)]));
        Auth.EndOtherSessions(r.S, user.Id, r.Cookie);              // other browsers sign in again, with a code
        r.S.SaveChanges();
        return new JsonObject { ["enabled"] = true, ["recovery_codes"] = new JsonArray([.. codes.Select(c => (JsonNode)JsonValue.Create(c)!)]) };
    }

    object? TwoFactorDisable(Req r)
    {
        var user = PasswordUser(r);
        var b = Body(r, Field.Str("password", 200, required: true));
        if (!Passwords.Check(b.Str("password")!, user.PasswordHash)) throw new ApiError(401, "The password is not right.");
        Totp.TurnOff(user);
        r.S.SaveChanges();
        return new JsonObject { ["enabled"] = false };
    }

    // ---------- phone notifications (WebPush) ----------

    /// <summary>This server's VAPID public key, which the browser needs to subscribe.</summary>
    object? PushKey(Req r)
    {
        PasswordUser(r);
        return new JsonObject { ["public_key"] = WebPush.VapidKeys(r.S, create: true)!.Value.Public };
    }

    static Field[] PushFields() =>
        [Field.Str("endpoint", 1000, required: true), Field.Str("p256dh", 200), Field.Str("auth", 100)];

    object? PushSubscribe(Req r)
    {
        var user = PasswordUser(r);
        var b = Body(r, PushFields());
        var endpoint = b.Str("endpoint")!;
        if (!WebPush.EndpointOk(endpoint)) throw new ApiError(422, "That browser's push service isn't supported.");
        string p256dh = b.Str("p256dh") ?? "", auth = b.Str("auth") ?? "";
        try
        {
            if (WebPush.UnB64u(p256dh).Length != 65 || WebPush.UnB64u(auth).Length != 16) throw new FormatException();
        }
        catch (FormatException)
        {
            throw new ApiError(422, "The browser sent keys that don't look right.");
        }
        var sub = r.S.PushSubscriptions.FirstOrDefault(p => p.Endpoint == endpoint);
        if (sub is null)
        {
            sub = new PushSubscription { Endpoint = endpoint };
            r.S.PushSubscriptions.Add(sub);
        }
        sub.UserId = user.Id;
        sub.P256dh = p256dh;
        sub.Auth = auth;
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true, ["devices"] = r.S.PushSubscriptions.Count(p => p.UserId == user.Id) };
    }

    object? PushUnsubscribe(Req r)
    {
        var user = PasswordUser(r);
        var endpoint = Body(r, PushFields()).Str("endpoint")!;
        if (r.S.PushSubscriptions.FirstOrDefault(p => p.Endpoint == endpoint && p.UserId == user.Id) is PushSubscription sub)
        {
            r.S.PushSubscriptions.Remove(sub);
            r.S.SaveChanges();
        }
        return new JsonObject { ["ok"] = true };
    }

    /// <summary>Send a test notification to every browser this user turned notifications on in.</summary>
    object? PushTest(Req r)
    {
        var user = PasswordUser(r);
        int devices = r.S.PushSubscriptions.Count(p => p.UserId == user.Id);
        int sent = _pusher.SendToUser(user.Id, new JsonObject
        {
            ["title"] = "Fullscope", ["body"] = "Alert notifications are on for this device.",
            ["url"] = _settings.AppUrl + "#/account", ["tag"] = "fullscope-test",
        });
        return new JsonObject { ["sent"] = sent, ["devices"] = devices };
    }

    /// <summary>Revoke the user's older reset links and return a new one.</summary>
    string MakeReset(Db s, User user)
    {
        foreach (var old in s.PasswordResets.Where(p => p.UserId == user.Id && p.UsedAt == null).ToList()) s.PasswordResets.Remove(old);
        var token = Db.TokenUrlSafe(24);
        s.PasswordResets.Add(new PasswordReset { TokenHash = Db.HashKey(token), UserId = user.Id, ExpiresAt = Db.UtcNow().AddHours(ResetHours) });
        s.SaveChanges();
        return $"{_settings.AppUrl}#/reset/{token}";
    }

    /// <summary>Email a reset link. The answer never says whether the email has a sign-in.</summary>
    /// <summary>Order request from the landing page: saved, and emailed to ORDER_EMAIL. No payment is taken.</summary>
    object? PlaceOrder(Req r)
    {
        var b = Body(r, Field.Str("company", 200, required: true, minLength: 1), Field.Str("name", 200, required: true, minLength: 1),
                     Field.Str("email", 320, required: true), Field.Str("phone", 40), Field.Int("systems", ge: 1, le: 1000, nullable: false),
                     Field.Str("notes", 2000), Field.Str("website", 200));
        if (!string.IsNullOrEmpty(b.Str("website"))) return new JsonObject { ["ok"] = true };   // a bot filled in the hidden field
        var ip = ClientIp(r.Http);
        if (_orderThrottle.Blocked(ip)) throw new ApiError(429, "Too many orders from this address. Try again in an hour.");
        var email = CleanEmail(b.Str("email")!);
        string company = b.Str("company")!.Trim(), name = b.Str("name")!.Trim();
        if (company.Length == 0 || name.Length == 0) throw new ApiError(422, "Company and name are required.");
        int systems = b.Int("systems") ?? throw new ApiError(422, "How many systems?");
        _orderThrottle.Fail(ip);
        var order = new Order
        {
            Company = company, Name = name, Email = email, Systems = systems,
            Phone = b.Str("phone")?.Trim() is { Length: > 0 } phone ? phone : null,
            Notes = b.Str("notes")?.Trim() is { Length: > 0 } notes ? notes : null,
        };
        r.S.Orders.Add(order);
        r.S.SaveChanges();
        r.S.Tickets.Add(Ticket.ForOrder(order));                 // shows in the Office inbox
        r.S.SaveChanges();
        if (_settings.OrderEmail.Length > 0)
            SendInvite(_settings.OrderEmail, $"[Fullscope] Order request from {company}",
                       $"New order request #{order.Id} from the Fullscope website.\n\n" +
                       $"Company: {company}\nName: {name}\nEmail: {email}\nPhone: {order.Phone ?? "-"}\n" +
                       $"Systems: {order.Systems}\n\nNotes:\n{order.Notes ?? "-"}\n\n" +
                       $"No payment was taken. Reply to {email} to confirm the order.");
        return new JsonObject { ["ok"] = true, ["id"] = order.Id };
    }

    object? ForgotPassword(Req r)
    {
        var b = Body(r, Field.Str("email", 320, required: true));
        var email = b.Str("email")!.Trim().ToLowerInvariant();
        if (!_mailer.Enabled) return new JsonObject { ["ok"] = true, ["email_enabled"] = false };
        var user = r.S.Users.FirstOrDefault(u => u.Email == email);
        if (user is not null && !_resetThrottle.Blocked(email))
        {
            _resetThrottle.Fail(email);
            var url = MakeReset(r.S, user);
            SendInvite(email, "Reset your Fullscope password",
                       $"Someone asked to reset the password for {email} on Fullscope.\n\n" +
                       $"Choose a new password here (the link works once, for {ResetHours} hour):\n{url}\n\n" +
                       "If it wasn't you, ignore this email; your password stays the same.");
        }
        return new JsonObject { ["ok"] = true, ["email_enabled"] = true };
    }

    /// <summary>A reset link for one of this system's homeowners, to pass on yourself (works without email).</summary>
    object? MemberResetLink(Req r)
    {
        var system = TechSystem(r);
        int userId = Validate.PathInt("user_id", r.Route("user_id"));
        if (r.S.SystemMembers.Find(system.Id, userId) is null) throw new ApiError(404, "not a member");
        var user = r.S.Users.Find(userId)!;
        return new JsonObject { ["url"] = MakeReset(r.S, user), ["email"] = user.Email, ["hours"] = ResetHours };
    }

    object? ResetPassword(Req r)
    {
        var b = Body(r, Field.Str("token", 100, required: true), Field.Str("password", 200, required: true));
        var row = r.S.PasswordResets.Find(Db.HashKey(b.Str("token")!));
        if (row is null || row.UsedAt is not null || row.ExpiresAt <= Db.UtcNow())
            throw new ApiError(410, "This reset link has expired or was already used. Ask for a new one.");
        if (Passwords.Problem(b.Str("password")!) is string problem) throw new ApiError(422, problem);
        var user = r.S.Users.Find(row.UserId)!;
        user.PasswordHash = Passwords.Hash(b.Str("password")!);
        row.UsedAt = Db.UtcNow();
        Auth.EndOtherSessions(r.S, user.Id, null);                  // whoever had the old password is out
        if (user.TotpOn is not null)                                // the link alone doesn't sign in: the code is still needed
        {
            r.S.SaveChanges();
            _throttle.Clear(user.Email);
            return new JsonObject { ["totp_required"] = true, ["email"] = user.Email };
        }
        user.LastLogin = Db.UtcNow();
        var token = Auth.NewSession(r.S, user.Id, _settings.SessionDays);
        r.S.SaveChanges();
        _throttle.Clear(user.Email);
        SetCookie(r, token);
        return MeView(r.S, Principal.ForUser(user));
    }

    // ---------- equipment ----------

    /// <summary>Equipment details, the system's settings and the targets the diagnostics use.</summary>
    static JsonObject EquipmentView(EquipmentRow? eq, HvacSystem system)
    {
        var info = eq?.Info();
        var (lo, hi, source) = Core.Equipment.ScBand(info, "cooling");
        var o = new JsonObject();
        double?[] values = eq is null ? new double?[EquipmentFields.Length] :
            [null, null, eq.Tonnage, eq.ScTarget, eq.ScTolerance, eq.RatedBtuh, eq.RatedCfm, eq.MaxEsp, eq.ElevationFt,
             eq.CompRla, eq.CompLra, eq.FanFla, eq.CapHermUf, eq.CapFanUf, eq.VoltMin, eq.VoltMax];
        for (int i = 0; i < EquipmentFields.Length; i++) o[EquipmentFields[i]] = values[i];
        o["system_type"] = eq?.SystemType;
        o["metering"] = eq?.Metering;
        o["sc_tolerance"] ??= Core.Equipment.DefaultScTol;
        o["refrigerant"] = system.Refrigerant;
        o["heat_pump"] = system.HeatPump;
        o["ob_energized"] = system.ObEnergized;
        o["atm_psia"] = system.AtmPsia;
        o["targets"] = new JsonObject
        {
            ["sc"] = new JsonObject { ["lo"] = lo, ["hi"] = hi, ["source"] = source },
            ["sh"] = new JsonObject
            {
                ["lo"] = Core.Equipment.GenericShLo, ["hi"] = Core.Equipment.GenericShHi, ["note"] = Core.Equipment.SuperheatNote(info),
            },
        };
        return o;
    }

    object? SetEquipment(Req r)
    {
        var system = TechSystem(r);
        var b = Body(r,
            Field.Choice("system_type", SystemTypes), Field.Choice("metering", ["txv", "eev", "piston"]),
            Field.Float("tonnage", ge: 0.5, le: 30), Field.Float("sc_target", ge: 0, le: 30), Field.Float("sc_tolerance", ge: 0.5, le: 10),
            Field.Float("rated_btuh", ge: 1000, le: 500000), Field.Float("rated_cfm", ge: 100, le: 20000), Field.Float("max_esp", gt: 0, le: 2),
            Field.Float("elevation_ft", ge: -1500, le: 12000), Field.Float("comp_rla", gt: 0, le: 200), Field.Float("comp_lra", gt: 0, le: 600),
            Field.Float("fan_fla", gt: 0, le: 20), Field.Float("cap_herm_uf", gt: 0, le: 200), Field.Float("cap_fan_uf", gt: 0, le: 30),
            Field.Float("volt_min", ge: 100, le: 600), Field.Float("volt_max", ge: 100, le: 600),
            Field.Str("refrigerant", required: true), Field.Bool("heat_pump", required: true) with { Nullable = false },
            Field.Choice("ob_energized", ["cool", "heat"], required: true), Field.Float("atm_psia", ge: 10, le: 15.5));
        var refrigerant = b.Str("refrigerant")!;
        if (!RefrigerantTables.Fluids.Contains(refrigerant)) throw new ApiError(422, $"refrigerant must be one of {FluidList()}");
        var eq = r.S.Equipment.Find(system.Id);
        if (eq is null)
        {
            eq = new EquipmentRow { SystemId = system.Id, ScTolerance = Core.Equipment.DefaultScTol };
            r.S.Equipment.Add(eq);
        }
        double? vmin = b.Float("volt_min"), vmax = b.Float("volt_max");
        if (vmin is double lo && lo != 0 && vmax is double hi && hi != 0 && lo >= hi)
            throw new ApiError(422, "the minimum voltage must be below the maximum");
        eq.SystemType = b.Str("system_type");
        eq.Metering = b.Str("metering");
        eq.Tonnage = b.Float("tonnage");
        eq.ScTarget = b.Float("sc_target");
        eq.RatedBtuh = b.Float("rated_btuh");
        eq.RatedCfm = b.Float("rated_cfm");
        eq.MaxEsp = b.Float("max_esp");
        eq.ElevationFt = b.Float("elevation_ft");
        eq.CompRla = b.Float("comp_rla");
        eq.CompLra = b.Float("comp_lra");
        eq.FanFla = b.Float("fan_fla");
        eq.CapHermUf = b.Float("cap_herm_uf");
        eq.CapFanUf = b.Float("cap_fan_uf");
        eq.VoltMin = vmin;
        eq.VoltMax = vmax;
        eq.ScTolerance = b.Float("sc_tolerance") ?? Core.Equipment.DefaultScTol;
        system.Refrigerant = refrigerant;
        system.HeatPump = b.Bool("heat_pump")!.Value;
        system.ObEnergized = b.Str("ob_energized")!;
        if (b.Float("elevation_ft") is double ft) system.AtmPsia = Core.Equipment.AtmFromElevation(ft);
        else if (b.Float("atm_psia") is double atm) system.AtmPsia = atm;
        if (b.Str("system_type") is "split_ac" or "packaged_ac") system.HeatPump = false;
        r.S.SaveChanges();
        return EquipmentView(eq, system);
    }

    // ---------- display thermostat ----------

    static ThermostatConfig TstatRow(Db s, HvacSystem system)
    {
        var row = s.ThermostatConfigs.Find(system.Id);
        if (row is null)
        {
            row = new ThermostatConfig { SystemId = system.Id, Settings = "{}", Tech = "{}", Version = 0, Sent = false };
            s.ThermostatConfigs.Add(row);
        }
        return row;
    }

    static JsonObject TechLimitsJson()
    {
        var o = new JsonObject();
        foreach (var (k, lo, hi) in Thermostat.TechLimits)
        {
            bool isFloat = k == "differential" || k.EndsWith("_f", StringComparison.Ordinal);   // as written in thermostat.TECH_LIMITS
            o[k] = isFloat ? new JsonArray(lo, hi) : new JsonArray((long)lo, (long)hi);
        }
        return o;
    }

    JsonObject TstatView(Db s, HvacSystem system, ThermostatConfig? row)
    {
        var d = s.Devices.FirstOrDefault(x => x.SystemId == system.Id && x.Node == Thermostat.Node);
        var raw = d is null ? null : Load(s.Telemetry.Where(t => t.DeviceId == d.Id).OrderByDescending(t => t.Time).Select(t => t.Data).FirstOrDefault());
        var st = Thermostat.Merged(row is null ? null : Load(row.Settings));
        var tech = (JsonObject)Thermostat.ConfigPayload(0, null, row is null ? null : Load(row.Tech), system.HeatPump, system.ObEnergized)["tech"]!;
        var (heat, cool, source, next) = Thermostat.ScheduleNow(st, Thermostat.LocalNow(tech));
        // raw.get("cfg_ver") == row.version, with Python's equality (True == 1, 3.0 == 3)
        bool applied = row is not null && Py.Truthy(raw) && raw is JsonObject rep && rep["cfg_ver"] is JsonValue ver
                       && (Py.Classify(ver) == Py.Kind.Bool ? (ver.GetValue<bool>() ? 1 : 0) == row.Version
                           : Py.NumberOf(ver) is Num v && v.V == row.Version);
        return new JsonObject
        {
            ["present"] = d is not null, ["device"] = d is null ? null : DeviceView(d, Db.UtcNow()), ["report"] = raw,
            ["settings"] = st, ["tech"] = tech.DeepClone(), ["limits"] = TechLimitsJson(),
            ["version"] = row?.Version ?? 0, ["sent"] = row is not null && row.Sent, ["applied"] = applied,
            ["updated_at"] = row is null ? null : Iso(row.UpdatedAt),
            ["now"] = new JsonObject { ["heat"] = heat?.DeepClone(), ["cool"] = cool?.DeepClone(), ["source"] = source, ["next_change"] = next },
        };
    }

    /// <summary>Store a change, bump the version and send the whole config (retained, so a thermostat
    /// that was offline gets it when it reconnects).</summary>
    JsonObject TstatSave(Db s, HvacSystem system, ThermostatConfig row, JsonObject? settings, JsonObject? tech)
    {
        if (settings is not null)
        {
            var errs = Thermostat.Validate(settings);
            if (errs.Count > 0) throw new ApiError(422, string.Join("; ", errs));
            row.Settings = Db.Json(settings)!;
        }
        if (tech is not null)
        {
            var errs = Thermostat.ValidateTech(tech);
            if (errs.Count > 0) throw new ApiError(422, string.Join("; ", errs));
            row.Tech = Db.Json(tech)!;
        }
        row.Version += 1;
        row.UpdatedAt = Db.UtcNow();
        row.Sent = _publish($"hvac/{system.SiteId}/{Thermostat.Node}/config",
                            Thermostat.ConfigPayload(row.Version, Load(row.Settings), Load(row.Tech), system.HeatPump, system.ObEnergized), true);
        s.SaveChanges();
        return TstatView(s, system, row);
    }

    /// <summary>Mode, fan, setpoints and schedule: the homeowner may change these.</summary>
    object? SetThermostat(Req r)
    {
        var system = OwnSystem(r);
        var b = Body(r, Field.Choice("mode", Thermostat.Modes), Field.Choice("fan", Thermostat.Fans), Field.Float("heat_sp"),
                     Field.Float("cool_sp"), new Field("schedule", FieldType.ListOfDict));
        var row = TstatRow(r.S, system);
        var st = Thermostat.Merged(Load(row.Settings));
        foreach (var (k, v) in b.Dump("mode", "fan", "heat_sp", "cool_sp", "schedule")) st[k] = v?.DeepClone();
        return TstatSave(r.S, system, row, st, null);
    }

    static JsonNode? PyMax(JsonNode? a, double b) => Py.NumberOf(a) is Num x && !(b > x.V) ? a : JsonValue.Create(b);

    static JsonNode? PyMin(JsonNode? a, double b) => Py.NumberOf(a) is Num x && !(b < x.V) ? a : JsonValue.Create(b);

    /// <summary>New setpoints until the next scheduled change (or until resumed, if permanent).</summary>
    object? HoldThermostat(Req r)
    {
        var system = OwnSystem(r);
        var b = Body(r, Field.Float("heat"), Field.Float("cool"), Field.Bool("permanent", false));
        var row = TstatRow(r.S, system);
        var st = Thermostat.Merged(Load(row.Settings));
        double? heat = b.Float("heat"), cool = b.Float("cool");
        var hold = Thermostat.HoldUntilNext(st, Thermostat.LocalNow(Load(row.Tech)), heat is double h ? JsonValue.Create(h) : null,
                                            cool is double c ? JsonValue.Create(c) : null);
        if (b.Bool("permanent") == true) hold["until"] = null;
        if (heat is double hh && cool is null) hold["cool"] = PyMax(hold["cool"]?.DeepClone(), hh + Thermostat.AutoDeadband);   // pushing one setpoint moves the other along
        if (cool is double cc && heat is null) hold["heat"] = PyMin(hold["heat"]?.DeepClone(), cc - Thermostat.AutoDeadband);
        var errs = Thermostat.ValidateHold(hold);
        if (errs.Count > 0) throw new ApiError(422, string.Join("; ", errs));
        st["hold"] = hold;
        return TstatSave(r.S, system, row, st, null);
    }

    /// <summary>Safety settings (compressor protection, aux heat lockouts, time zone): technician only.</summary>
    object? SetThermostatTech(Req r)
    {
        var system = TechSystem(r);
        if (!r.BodyPresent) throw ApiError.Invalid(ApiError.Problem("missing", "Field required", null, "body"));
        if (r.Body is not JsonObject body) throw ApiError.Invalid(ApiError.Problem("dict_type", "Input should be a valid dictionary", r.Body, "body"));
        var row = TstatRow(r.S, system);
        var known = Thermostat.DefaultTech();
        var unknown = body.Select(kv => kv.Key).Where(k => !known.ContainsKey(k)).OrderBy(k => k, StringComparer.Ordinal).ToList();
        if (unknown.Count > 0) throw new ApiError(422, $"unknown settings: {string.Join(", ", unknown)}");
        var tech = Load(row.Tech) as JsonObject ?? [];
        foreach (var (k, v) in body)
            if (k is not ("heat_pump" or "ob_energized")) tech[k] = v?.DeepClone();          // the Equipment page owns these
        return TstatSave(r.S, system, row, null, tech);
    }

    // ---------- invites ----------

    static string CleanEmail(string raw)
    {
        var email = raw.Trim().ToLowerInvariant();
        if (!EmailRe.IsMatch(email)) throw new ApiError(422, "That doesn't look like an email address.");
        return email;
    }

    bool SendInvite(string email, string subject, string body)
    {
        if (!_mailer.Enabled) return false;
        if (_sendAsync)
        {
            Task.Run(() => _mailer.Send(email, subject, body));
            return true;
        }
        return _mailer.Send(email, subject, body);
    }

    /// <summary>New invite (older pending ones for the same person and place are revoked). Returns the link.</summary>
    (Invite Invite, string Url) MakeInvite(Db s, Principal who, string email, string role, int? accountId = null, int? systemId = null, bool admin = false)
    {
        var q = s.Invites.Where(i => i.Email == email && i.AcceptedAt == null && i.Role == role);
        q = systemId is not null ? q.Where(i => i.SystemId == systemId) : q.Where(i => i.AccountId == accountId);
        var token = Db.TokenUrlSafe(24);
        var olds = q.ToList();
        var inv = new Invite
        {
            TokenHash = Db.HashKey(token), Email = email, Role = role, AccountId = accountId, SystemId = systemId,
            InvitedBy = who.UserId, ExpiresAt = Db.UtcNow().AddDays(InviteDays), Admin = admin ? true : null,
        };
        s.Invites.Add(inv);
        s.SaveChanges();                        // insert, then delete, as SQLAlchemy flushes (the new id comes after the old one)
        foreach (var old in olds) s.Invites.Remove(old);
        s.SaveChanges();
        return (inv, $"{_settings.AppUrl}#/invite/{token}");
    }

    static JsonObject InviteView(Invite inv) => new()
    {
        ["id"] = inv.Id, ["email"] = inv.Email, ["role"] = inv.Role, ["admin"] = inv.Admin == true, ["expires_at"] = Iso(inv.ExpiresAt),
        ["expired"] = inv.ExpiresAt <= Db.UtcNow(),
    };

    static JsonObject PersonView(User u) => new()
    {
        ["user_id"] = u.Id, ["email"] = u.Email, ["name"] = u.Name, ["last_login"] = Iso(u.LastLogin),
    };

    static JsonObject TeammateView(User u) => new()
    {
        ["user_id"] = u.Id, ["email"] = u.Email, ["name"] = u.Name, ["admin"] = u.Admin == true, ["last_login"] = Iso(u.LastLogin),
    };

    /// <summary>Give a homeowner access: an existing homeowner sign-in is added at once, anyone else gets a link.</summary>
    object? InviteHomeowner(Req r)
    {
        var system = TechSystem(r);
        var who = r.Who;
        var b = Body(r, Field.Str("email", 320, required: true));
        var email = CleanEmail(b.Str("email")!);
        var user = r.S.Users.FirstOrDefault(u => u.Email == email);
        if (user is not null)
        {
            if (user.Role != "homeowner") throw new ApiError(409, "That email already has a contractor sign-in.");
            if (r.S.SystemMembers.Find(system.Id, user.Id) is null)
            {
                r.S.SystemMembers.Add(new SystemMember { SystemId = system.Id, UserId = user.Id });
                r.S.SaveChanges();
            }
            return new JsonObject { ["status"] = "added", ["email"] = email };
        }
        var (inv, url) = MakeInvite(r.S, who, email, "homeowner", systemId: system.Id);
        var acct = r.S.Accounts.Find(system.AccountId);
        bool emailed = SendInvite(email, $"Your Fullscope access for {system.Name}",
            $"{acct?.Name ?? "Your contractor"} has given you access to {system.Name} on Fullscope, " +
            "which watches how your heating and cooling are running.\n\n" +
            $"Choose a password here (the link works for {InviteDays} days):\n{url}");
        return new JsonObject { ["status"] = "invited", ["email"] = email, ["url"] = url, ["emailed"] = emailed, ["expires_at"] = Iso(inv.ExpiresAt) };
    }

    /// <summary>Homeowners with access to this system, and pending invites.</summary>
    object? SystemPeople(Req r)
    {
        var system = TechSystem(r);
        var members = r.S.Users.Where(u => r.S.SystemMembers.Any(m => m.SystemId == system.Id && m.UserId == u.Id)).OrderBy(u => u.Email).ToList();
        var invites = r.S.Invites.Where(i => i.SystemId == system.Id && i.AcceptedAt == null).ToList().OrderByDescending(i => i.CreatedAt).ToList();
        return new JsonObject
        {
            ["members"] = new JsonArray(members.Select(u => (JsonNode?)PersonView(u)).ToArray()),
            ["invites"] = new JsonArray(invites.Select(i => (JsonNode?)InviteView(i)).ToArray()),
        };
    }

    object? RemoveMember(Req r)
    {
        var system = TechSystem(r);
        int userId = Validate.PathInt("user_id", r.Route("user_id"));
        var m = r.S.SystemMembers.Find(system.Id, userId) ?? throw new ApiError(404, "not a member");
        r.S.SystemMembers.Remove(m);
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true };
    }

    /// <summary>Invite a contractor colleague to this account, as a technician or an admin.</summary>
    object? InviteTeammate(Req r)
    {
        var who = r.Who;
        AdminOnly(who);
        var b = Body(r, Field.Str("email", 320, required: true), Field.Bool("admin", false));
        var email = CleanEmail(b.Str("email")!);
        if (r.S.Users.Any(u => u.Email == email)) throw new ApiError(409, "That email already has a sign-in.");
        var (inv, url) = MakeInvite(r.S, who, email, "contractor", accountId: who.AccountId, admin: b.Bool("admin") == true);
        var acct = who.AccountId is int aid ? r.S.Accounts.Find(aid) : null;
        bool emailed = SendInvite(email, $"Join {acct?.Name ?? "your team"} on Fullscope",
            $"You've been invited to {acct?.Name ?? "a contractor account"} on Fullscope.\n\n" +
            $"Choose a password here (the link works for {InviteDays} days):\n{url}");
        return new JsonObject { ["status"] = "invited", ["email"] = email, ["url"] = url, ["emailed"] = emailed, ["expires_at"] = Iso(inv.ExpiresAt) };
    }

    object? AccountPeople(Req r)
    {
        var who = r.Who;
        AdminOnly(who);
        var users = r.S.Users.Where(u => u.AccountId == who.AccountId).OrderBy(u => u.Email).ToList();
        var invites = r.S.Invites.Where(i => i.AccountId == who.AccountId && i.Role == "contractor" && i.AcceptedAt == null)
                       .ToList().OrderByDescending(i => i.CreatedAt).ToList();
        return new JsonObject
        {
            ["users"] = new JsonArray(users.Select(u => (JsonNode?)TeammateView(u)).ToArray()),
            ["invites"] = new JsonArray(invites.Select(i => (JsonNode?)InviteView(i)).ToArray()),
        };
    }

    /// <summary>Take a colleague off the account: their sign-in is deleted and they are signed out.</summary>
    object? RemoveTeammate(Req r)
    {
        var who = r.Who;
        AdminOnly(who);
        int userId = Validate.PathInt("user_id", r.Route("user_id"));
        var user = r.S.Users.Find(userId);
        if (user is null || user.Role != "contractor" || user.AccountId != who.AccountId)
            throw new ApiError(404, "no such teammate");
        if (user.Id == who.UserId) throw new ApiError(409, "You can't remove yourself.");
        Auth.EndOtherSessions(r.S, user.Id, null);
        foreach (var inv in r.S.Invites.Where(i => i.InvitedBy == user.Id).ToList()) inv.InvitedBy = null;
        foreach (var p in r.S.PasswordResets.Where(p => p.UserId == user.Id).ToList()) r.S.PasswordResets.Remove(p);
        foreach (var p in r.S.PushSubscriptions.Where(p => p.UserId == user.Id).ToList()) r.S.PushSubscriptions.Remove(p);
        foreach (var v in r.S.ServiceVisits.Where(v => v.CreatedBy == user.Id).ToList()) v.CreatedBy = null;
        foreach (var c in r.S.GaugeChecks.Where(c => c.CreatedBy == user.Id).ToList()) c.CreatedBy = null;
        r.S.SaveChanges();                         // the references go first, then the user (no cascades)
        r.S.Users.Remove(user);
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true };
    }

    /// <summary>Make a colleague an admin, or a technician again. The account always keeps one admin.</summary>
    object? SetAdmin(Req r)
    {
        var who = r.Who;
        AdminOnly(who);
        int userId = Validate.PathInt("user_id", r.Route("user_id"));
        var b = Body(r, Field.Bool("admin", required: true) with { Nullable = false });
        var user = r.S.Users.Find(userId);
        if (user is null || user.Role != "contractor" || user.AccountId != who.AccountId)
            throw new ApiError(404, "no such teammate");
        bool admin = b.Bool("admin")!.Value;
        if (!admin && user.Admin == true
            && r.S.Users.Count(u => u.Role == "contractor" && u.AccountId == who.AccountId && u.Admin == true) <= 1)
            throw new ApiError(409, "The account needs at least one admin. Make someone else an admin first.");
        user.Admin = admin ? true : null;
        r.S.SaveChanges();
        return new JsonObject { ["user_id"] = user.Id, ["admin"] = user.Admin == true };
    }

    object? RevokeInvite(Req r)
    {
        var who = r.Who;
        int inviteId = Validate.PathInt("invite_id", r.Route("invite_id"));
        var inv = r.S.Invites.Find(inviteId);
        int? owner = null;
        if (inv is not null) owner = inv.SystemId is int sid ? r.S.Systems.Find(sid)?.AccountId : inv.AccountId;
        // homeowner links belong to the technician pages; teammate links to the admins
        bool allowed = inv is not null && inv.SystemId is null ? who.IsAdmin : who.IsTech;
        if (inv is null || !allowed || owner != who.AccountId) throw new ApiError(404, "no such invite");
        r.S.Invites.Remove(inv);
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true };
    }

    static Invite OpenInvite(Db s, string token)
    {
        var h = Db.HashKey(token);
        var inv = s.Invites.FirstOrDefault(i => i.TokenHash == h);
        if (inv is null || inv.AcceptedAt is not null) throw new ApiError(404, "This invite link isn't valid any more. Ask for a new one.");
        if (inv.ExpiresAt <= Db.UtcNow()) throw new ApiError(410, "This invite link has expired. Ask for a new one.");
        return inv;
    }

    /// <summary>What an invite link is for (no sign-in needed: the link itself is the secret, so it is
    /// sent in the body, where access logs don't record it).</summary>
    object? InviteInfo(Req r)
    {
        var b = Body(r, Field.Str("token", 100, required: true));
        var inv = OpenInvite(r.S, b.Str("token")!);
        var system = inv.SystemId is int sid ? r.S.Systems.Find(sid) : null;
        var acctId = system?.AccountId ?? inv.AccountId;
        var acct = acctId is int aid ? r.S.Accounts.Find(aid) : null;
        return new JsonObject
        {
            ["email"] = inv.Email, ["role"] = inv.Role, ["system"] = system?.Name, ["account"] = acct?.Name,
            ["expires_at"] = Iso(inv.ExpiresAt), ["has_user"] = r.S.Users.Any(u => u.Email == inv.Email),
        };
    }

    object? AcceptInvite(Req r)
    {
        var b = Body(r, Field.Str("token", 100, required: true), Field.Str("name", 200), Field.Str("password", 200, required: true));
        var inv = OpenInvite(r.S, b.Str("token")!);
        if (r.S.Users.Any(u => u.Email == inv.Email)) throw new ApiError(409, "This email already has a sign-in. Sign in instead.");
        if (Passwords.Problem(b.Str("password")!) is string problem) throw new ApiError(422, problem);
        var name = (b.Str("name") ?? "").Trim();
        var user = new User
        {
            Email = inv.Email, Name = name.Length > 0 ? name : null, Role = inv.Role,
            AccountId = inv.Role == "contractor" ? inv.AccountId : null,
            PasswordHash = Passwords.Hash(b.Str("password")!), LastLogin = Db.UtcNow(),
            Admin = inv.Role == "contractor" && inv.Admin == true ? true : null,
        };
        using var tx = r.S.Database.BeginTransaction();
        r.S.Users.Add(user);
        r.S.SaveChanges();
        if (inv.SystemId is int sid) r.S.SystemMembers.Add(new SystemMember { SystemId = sid, UserId = user.Id });
        inv.AcceptedAt = Db.UtcNow();
        var token = Auth.NewSession(r.S, user.Id, _settings.SessionDays);
        r.S.SaveChanges();
        tx.Commit();
        SetCookie(r, token);
        return MeView(r.S, Principal.ForUser(user));
    }

    // ---------- systems ----------

    /// <summary>Every system the contractor services, most in need of attention first (admins' page).</summary>
    object? Fleet(Req r)
    {
        var who = r.Who;
        AdminOnly(who);
        var now = Db.UtcNow();
        var today = Time.Today();
        var rows = new List<(JsonObject View, int Rank, bool Due, string Name)>();
        foreach (var system in Visible(r.S, who).OrderBy(x => x.Name).ToList())
        {
            var row = r.S.Snapshots.Where(x => x.SystemId == system.Id).OrderByDescending(x => x.Time)
                       .Select(x => new { x.Time, x.Data }).FirstOrDefault();
            var data = row is null ? null : Load(row.Data) as JsonObject;
            data ??= [];
            double? age = row is null ? null : Seconds(now - row.Time);
            bool fresh = age is double a && a <= _stale;
            var devices = r.S.Devices.Where(d => d.SystemId == system.Id).ToList();
            int online = devices.Count(d => d.Connected && d.LastSeen is DateTime ls && Seconds(now - ls) <= _stale);
            var openAlerts = r.S.Alerts.Where(x => x.SystemId == system.Id && x.RaisedAt != null && x.ClearedAt == null)
                              .OrderBy(x => x.StartedAt).ToList();
            var flags = fresh && Py.Truthy(data["flags"]) ? (data["flags"] as JsonArray)?.OfType<JsonObject>().ToList() ?? [] : [];
            string level = "ok";
            foreach (var code in flags.Select(f => J.Str(f, "code")).Concat(openAlerts.Where(x => x.AckedAt is null).Select(x => (string?)x.Code)))
            {
                var lv = code is not null && Alerts.Level.TryGetValue(code, out var l) ? l : "caution";
                if (LevelRank[lv] > LevelRank[level]) level = lv;
            }
            if (!fresh && LevelRank[level] < LevelRank["offline"]) level = "offline";
            var maint = (JsonArray)Service.ServiceView(r.S, system.Id, today)["items"]!;
            int members = r.S.SystemMembers.Count(m => m.SystemId == system.Id);
            var maintObj = new JsonObject();
            foreach (var m in maint)
                maintObj[(string)m!["kind"]!] = new JsonObject { ["status"] = m["status"]!.DeepClone(), ["next_due"] = m["next_due"]?.DeepClone() };
            var view = new JsonObject
            {
                ["id"] = system.Id, ["name"] = system.Name, ["site_id"] = system.SiteId, ["level"] = level,
                ["mode"] = fresh ? data["mode"]?.DeepClone() : null, ["last_seen"] = row is null ? null : Iso(row.Time),
                ["nodes"] = new JsonObject { ["seen"] = devices.Count, ["online"] = online },
                ["issues"] = new JsonArray(flags.Select(f => f["text"]?.DeepClone()).ToArray()),
                ["open_alerts"] = new JsonArray(openAlerts.Select(x => (JsonNode?)new JsonObject
                {
                    ["id"] = x.Id, ["code"] = x.Code, ["text"] = x.Text, ["since"] = Iso(x.StartedAt), ["acked"] = x.AckedAt is not null,
                }).ToArray()),
                ["maintenance"] = maintObj, ["homeowners"] = members,
            };
            bool due = maint.Any(m => (string?)m!["status"] == "due");
            rows.Add((view, LevelRank[level], due, system.Name));
        }
        return new JsonArray(rows.OrderBy(x => -x.Rank).ThenBy(x => !x.Due).ThenBy(x => PyLower(x.Name), StringComparer.Ordinal)
                                 .Select(x => (JsonNode?)x.View).ToArray());
    }

    static string PyLower(string s) => s.ToLowerInvariant();

    object? GetSystem(Req r)
    {
        var system = OwnSystem(r);
        var now = Db.UtcNow();
        var devices = r.S.Devices.Where(d => d.SystemId == system.Id).OrderBy(d => d.Node).ToList();
        var o = SystemView(system);
        o["devices"] = new JsonArray(devices.Select(d => (JsonNode?)DeviceView(d, now)).ToArray());
        return o;
    }

    object? UpdateSystem(Req r)
    {
        var system = TechSystem(r);
        var b = Body(r, Field.Str("refrigerant"), Field.Bool("heat_pump"), Field.Choice("ob_energized", ["cool", "heat"]),
                     Field.Float("atm_psia", ge: 10, le: 15.5));
        if (b.Str("refrigerant") is string rf && !RefrigerantTables.Fluids.Contains(rf))
            throw new ApiError(422, $"refrigerant must be one of {FluidList()}");
        if (b.Str("refrigerant") is string f) system.Refrigerant = f;
        if (b.Bool("heat_pump") is bool hp) system.HeatPump = hp;
        if (b.Str("ob_energized") is string ob) system.ObEnergized = ob;
        if (b.Float("atm_psia") is double atm) system.AtmPsia = atm;
        r.S.SaveChanges();
        return SystemView(system);
    }

    /// <summary>Newest derived snapshot, plus each node's status and its newest raw telemetry.</summary>
    object? Latest(Req r)
    {
        var system = OwnSystem(r);
        var now = Db.UtcNow();
        var devices = r.S.Devices.Where(d => d.SystemId == system.Id).ToList().GroupBy(d => d.Node).ToDictionary(g => g.Key, g => g.Last());
        var row = r.S.Snapshots.Where(x => x.SystemId == system.Id).OrderByDescending(x => x.Time)
                   .Select(x => new { x.Time, x.Data }).FirstOrDefault();
        var nodes = new JsonObject();
        foreach (var n in Ingest.Ingest.AllNodes)
        {
            if (!devices.TryGetValue(n, out var d))
            {
                nodes[n] = null;
                continue;
            }
            var raw = r.S.Telemetry.Where(t => t.DeviceId == d.Id).OrderByDescending(t => t.Time).Select(t => t.Data).FirstOrDefault();
            var v = DeviceView(d, now);
            v["data"] = Load(raw);
            nodes[n] = v;
        }
        return new JsonObject
        {
            ["time"] = row is null ? null : Iso(row.Time), ["run_started_at"] = Iso(system.RunStartedAt),
            ["derived"] = row is null ? null : Load(row.Data), ["nodes"] = nodes,
        };
    }

    /// <summary>Compressor runtime and cycles, outside high and inside average over the last `hours`.</summary>
    object? Summary(Req r)
    {
        var system = OwnSystem(r);
        var hoursNode = Validate.Query(r.Query("hours"), Field.Float("hours", gt: 0, le: 24 * 31), JsonValue.Create(24.0));
        double hours = Py.NumberOf(hoursNode)!.Value.V;
        var since = Db.UtcNow() - Minutes(hours * 60);
        var res = Cached(r.S, system.Id, "summary", hours, () => Rollup.Summary(r.S, system.Id, since));
        return new JsonObject
        {
            ["hours"] = hoursNode.DeepClone(), ["runtime_hours"] = Py.Round(res.OnS / 3600, 2), ["cycles"] = res.Cycles,
            ["avg_on_minutes"] = res.Cycles != 0 ? Py.Round(res.OnS / 60 / res.Cycles, 1) : null,
            ["outside_high"] = res.OatHigh is Num hi ? (hi.IsInt ? JsonValue.Create((long)hi.V) : JsonValue.Create(Py.Round(hi.V, 1))) : null,
            ["inside_avg"] = res.RetN > 0 ? Py.Round(res.RetSum / res.RetN, 1) : null,
            ["cycle_started_at"] = Iso(system.RunStartedAt),
        };
    }

    /// <summary>Averaged into at most ~600 points per series, like the PC dashboard.</summary>
    object? History(Req r)
    {
        var system = OwnSystem(r);
        double minutes = Py.NumberOf(Validate.Query(r.Query("minutes"), Field.Float("minutes", gt: 0, le: 60 * 24 * 31), JsonValue.Create(60.0)))!.Value.V;
        var since = Db.UtcNow() - Minutes(minutes);
        return Cached(r.S, system.Id, "history", minutes, () => Rollup.History(r.S, system.Id, minutes, since)).DeepClone();
    }

    object? ExportCsv(Req r)
    {
        var system = OwnSystem(r);
        double minutes = Py.NumberOf(Validate.Query(r.Query("minutes"), Field.Float("minutes", gt: 0, le: 60 * 24 * 365), JsonValue.Create(1440.0)))!.Value.V;
        var since = Db.UtcNow() - Minutes(minutes);
        var sb = new StringBuilder();
        CsvRow(sb, ["time_utc", .. CsvCols]);
        foreach (var row in r.S.Snapshots.AsNoTracking().Where(x => x.SystemId == system.Id && x.Time >= since).OrderBy(x => x.Time)
                                .Select(x => new { x.Time, x.Data }))
        {
            var d = Load(row.Data) as JsonObject;
            CsvRow(sb, [row.Time.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture), .. CsvCols.Select(c => CsvCell(d?[c]))]);
        }
        return new CsvResult(Results.Text(sb.ToString(), "text/csv", Encoding.UTF8), $"attachment; filename={system.SiteId}-export.csv");
    }

    sealed class CsvResult(IResult inner, string disposition) : IResult
    {
        public Task ExecuteAsync(HttpContext ctx)
        {
            ctx.Response.Headers.ContentDisposition = disposition;
            return inner.ExecuteAsync(ctx);
        }
    }

    /// <summary>str() of a JSON value as Python's csv module writes it (None is empty).</summary>
    internal static string CsvCell(JsonNode? v) => v switch
    {
        null => "",
        JsonValue jv when Py.Classify(jv) == Py.Kind.Bool => jv.GetValue<bool>() ? "True" : "False",
        JsonValue jv when Py.Classify(jv) == Py.Kind.String => jv.GetValue<string>(),
        JsonValue jv when Py.NumberOf(jv) is Num n => n.ToString(),
        _ => Py.ReprNode(v),
    };

    /// <summary>csv.writer, QUOTE_MINIMAL, \r\n line ends.</summary>
    internal static void CsvRow(StringBuilder sb, IEnumerable<string> cells)
    {
        bool first = true;
        foreach (var c in cells)
        {
            if (!first) sb.Append(',');
            first = false;
            if (c.IndexOfAny([',', '"', '\r', '\n']) >= 0) sb.Append('"').Append(c.Replace("\"", "\"\"")).Append('"');
            else sb.Append(c);
        }
        sb.Append("\r\n");
    }

    // ---------- alerts ----------

    /// <summary>Raised alerts that were open at any time in the last `days`, newest first (pending ones are left out).</summary>
    object? ListAlerts(Req r)
    {
        var system = OwnSystem(r);
        double days = Py.NumberOf(Validate.Query(r.Query("days"), Field.Float("days", gt: 0, le: 365), JsonValue.Create(7.0)))!.Value.V;
        var since = Db.UtcNow() - Minutes(days * 24 * 60);
        var rows = r.S.Alerts.Where(a => a.SystemId == system.Id && a.RaisedAt != null && (a.ClearedAt == null || a.ClearedAt >= since))
                    .OrderByDescending(a => a.StartedAt).Take(200).ToList();
        return new JsonArray(rows.Select(a => (JsonNode?)AlertView(a)).ToArray());
    }

    /// <summary>Mark an alert as being handled (or undo with {"ack": false}).</summary>
    object? AckAlert(Req r)
    {
        var system = TechSystem(r);
        int alertId = Validate.PathInt("alert_id", r.Route("alert_id"));
        var b = OptionalBody(r, Field.Bool("ack", true));
        var a = r.S.Alerts.Find(alertId);
        if (a is null || a.SystemId != system.Id) throw new ApiError(404, "no such alert");
        a.AckedAt = b is null || b.Bool("ack") == true ? Db.UtcNow() : null;
        r.S.SaveChanges();
        return AlertView(a);
    }

    JsonObject AlertSettingsView(Db s, HvacSystem system)
    {
        var (ownersOn, contractorOn) = Alerts.Wants(s, system.Id);
        var now = Db.UtcNow();
        var mutes = s.AlertMutes.Where(m => m.SystemId == system.Id).OrderBy(m => m.Code).ToList().Where(m => m.Until > now)
                     .Select(m => (JsonNode?)new JsonObject { ["code"] = m.Code, ["until"] = Iso(m.Until) }).ToArray();
        return new JsonObject
        {
            ["email_owner"] = ownersOn, ["email_contractor"] = contractorOn,
            ["homeowner_emails"] = new JsonArray(Alerts.HomeownerEmails(s, system.Id).Select(e => (JsonNode?)e).ToArray()),
            ["contractor_email"] = Alerts.ContractorEmail(s, system),
            ["email_enabled"] = _settings.SmtpHost.Length > 0, ["mutes"] = new JsonArray(mutes),
        };
    }

    object? SetAlertSettings(Req r)
    {
        var system = TechSystem(r);
        var b = Body(r, Field.Bool("email_owner"), Field.Bool("email_contractor"));
        var prefs = r.S.AlertPrefs.Find(system.Id);
        if (prefs is null)
        {
            prefs = new AlertPrefs { SystemId = system.Id, EmailOwner = true, EmailContractor = true };
            r.S.AlertPrefs.Add(prefs);
        }
        if (b.Bool("email_owner") is bool o) prefs.EmailOwner = o;
        if (b.Bool("email_contractor") is bool c) prefs.EmailContractor = c;
        r.S.SaveChanges();
        return AlertSettingsView(r.S, system);
    }

    /// <summary>No emails for this alert code for `hours` (0 unmutes). Alerts are still recorded and shown.</summary>
    object? MuteAlerts(Req r)
    {
        var system = TechSystem(r);
        var code = r.Route("code") ?? "";
        var b = Body(r, Field.Float("hours", ge: 0, le: 24 * 90, required: true));
        var stripped = code.Replace("_", "");
        if (stripped.Length == 0 || !stripped.EnumerateRunes().All(Rune.IsLetterOrDigit) || code.EnumerateRunes().Count() > 32)
            throw new ApiError(422, "bad alert code");
        var m = r.S.AlertMutes.Find(system.Id, code);
        double hours = b.Float("hours")!.Value;
        if (hours == 0)
        {
            if (m is not null) r.S.AlertMutes.Remove(m);
        }
        else
        {
            if (m is null)
            {
                m = new AlertMute { SystemId = system.Id, Code = code, Until = Db.UtcNow() };
                r.S.AlertMutes.Add(m);
            }
            m.Until = Db.UtcNow() + Minutes(hours * 60);
        }
        r.S.SaveChanges();
        return AlertSettingsView(r.S, system);
    }

    // ---------- service ----------

    object? SetContractor(Req r)
    {
        var system = TechSystem(r);
        var b = Body(r, Field.Str("name", 200), Field.Str("phone", 40), Field.Str("email", 320));
        var info = r.S.ServiceInfo.Find(system.Id);
        if (info is null)
        {
            info = new ServiceInfo { SystemId = system.Id };
            r.S.ServiceInfo.Add(info);
        }
        static string? Clean(string? v) => (v ?? "").Trim() is { Length: > 0 } t ? t : null;
        (info.Name, info.Phone, info.Email) = (Clean(b.Str("name")), Clean(b.Str("phone")), Clean(b.Str("email")));
        r.S.SaveChanges();
        return Service.ServiceView(r.S, system.Id);
    }

    static void CheckKind(string kind)
    {
        if (Service.KindOf(kind) is null)
            throw new ApiError(404, "kind must be one of [" + string.Join(", ", Service.Kinds.Select(k => $"'{k.Key}'")) + "]");
    }

    object? UpdateItem(Req r)
    {
        var system = TechSystem(r);
        var kind = r.Route("kind") ?? "";
        var b = Body(r, Field.Int("interval_days", ge: 1, le: 3650), Field.Float("interval_run_hours", gt: 0, le: 20000),
                     new Field("last_done", FieldType.Date) { Check = PastDate });
        CheckKind(kind);
        var row = Service.GetItem(r.S, system.Id, kind);
        foreach (var k in b.FieldsSet)
        {
            switch (k)
            {
                case "interval_days":
                    row.IntervalDays = b.Int(k) ?? throw new ApiError(422, "interval_days can't be empty");
                    break;
                case "interval_run_hours": row.IntervalRunHours = b.Float(k); break;
                case "last_done": row.LastDone = b.Date(k); break;
            }
        }
        r.S.SaveChanges();
        return Service.ServiceView(r.S, system.Id);
    }

    object? ItemDone(Req r)
    {
        var system = OwnSystem(r);
        var who = r.Who;
        var kind = r.Route("kind") ?? "";
        var b = OptionalBody(r, new Field("date", FieldType.Date) { Check = PastDate });
        CheckKind(kind);
        if (kind != "filter" && !who.IsTech) throw new ApiError(403, "for your contractor only");
        var row = Service.GetItem(r.S, system.Id, kind);
        row.LastDone = b?.Date("date") ?? Time.Today();
        r.S.SaveChanges();
        return Service.ServiceView(r.S, system.Id);
    }

    /// <summary>Logged service visits, newest first (homeowners see them too).</summary>
    object? ListVisits(Req r)
    {
        var system = OwnSystem(r);
        int limit = (int)Py.NumberOf(Validate.Query(r.Query("limit"), Field.Int("limit", gt: 0, le: 500), JsonValue.Create(100L)))!.Value.V;
        var rows = r.S.ServiceVisits.Where(v => v.SystemId == system.Id).OrderByDescending(v => v.Date).ThenByDescending(v => v.Id).Take(limit).ToList();
        return new JsonArray(rows.Select(v => (JsonNode?)Service.VisitView(v)).ToArray());
    }

    /// <summary>Log a visit. A tune-up resets the tune-up reminder, a changed filter the filter reminder;
    /// attach_readings stores the newest snapshot's main values if it is under 15 minutes old.</summary>
    object? AddVisit(Req r)
    {
        var system = TechSystem(r);
        var who = r.Who;
        var b = Body(r, new Field("date", FieldType.Date) { Required = true, Nullable = false, Check = PastDate },
                     Field.Choice("kind", Service.VisitKinds.Select(k => k.Key).ToArray(), required: true),
                     Field.Str("technician", 200), Field.Str("work", 2000, required: true, minLength: 1),
                     Field.Bool("filter_changed", false), Field.Bool("attach_readings", false));
        JsonObject? readings = null;
        if (b.Bool("attach_readings") == true)
        {
            var row = r.S.Snapshots.Where(x => x.SystemId == system.Id).OrderByDescending(x => x.Time).Select(x => new { x.Time, x.Data }).FirstOrDefault();
            if (row is null || Seconds(Db.UtcNow() - row.Time) > Service.ReadingsMaxAgeS)
                throw new ApiError(409, "No readings from the last 15 minutes to attach.");
            var data = Load(row.Data) as JsonObject ?? [];
            readings = [];
            foreach (var k in Service.ReadingKeys) readings[k] = data[k]?.DeepClone();
            readings["time"] = Iso(row.Time);
        }
        var date = b.Date("date")!.Value;
        var kind = b.Str("kind")!;
        var technician = (b.Str("technician") ?? "").Trim();
        var v = new ServiceVisit
        {
            SystemId = system.Id, Date = date, Kind = kind, Technician = technician.Length > 0 ? technician : null,
            Work = b.Str("work")!.Trim(), FilterChanged = b.Bool("filter_changed") == true, Readings = Db.Json(readings),
            CreatedBy = who.UserId,
        };
        r.S.ServiceVisits.Add(v);
        if (kind == "tuneup") Service.MarkDone(r.S, system.Id, "tuneup", date);
        if (v.FilterChanged) Service.MarkDone(r.S, system.Id, "filter", date);
        r.S.SaveChanges();
        return Service.VisitView(v);
    }

    /// <summary>Remove a visit logged by mistake (reminder dates it moved are left as they are).</summary>
    object? DeleteVisit(Req r)
    {
        var system = TechSystem(r);
        int visitId = Validate.PathInt("visit_id", r.Route("visit_id"));
        var v = r.S.ServiceVisits.Find(visitId);
        if (v is null || v.SystemId != system.Id) throw new ApiError(404, "no such visit");
        r.S.ServiceVisits.Remove(v);
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true };
    }

    /// <summary>Steady-cooling spread of superheat, subcooling, delta-T, condensing over ambient and approach
    /// over the last `days`, next to the alert limits (Report.cs), and alerts raised by code.</summary>
    object? ThresholdReport(Req r)
    {
        var system = TechSystem(r);
        var daysNode = Validate.Query(r.Query("days"), Field.Float("days", gt: 0, le: 31), JsonValue.Create(14.0));
        var since = Db.UtcNow() - Minutes(Py.NumberOf(daysNode)!.Value.V * 24 * 60);
        var rows = r.S.Snapshots.Where(x => x.SystemId == system.Id && x.Time >= since && x.Mode == "cooling").OrderBy(x => x.Time)
                    .Select(x => new { x.Time, x.Data }).AsEnumerable()
                    .Select(x => ((x.Time - DateTime.UnixEpoch).Ticks / 1e7, Load(x.Data)));
        var counts = r.S.Alerts.Where(a => a.SystemId == system.Id && a.RaisedAt >= since).GroupBy(a => a.Code)
                      .Select(g => new { g.Key, N = g.Count() }).ToDictionary(g => g.Key, g => g.N);
        return Report.Build(rows, r.S.Equipment.Find(system.Id)?.Info(), counts, daysNode);
    }

    // ---------- gauge checks (phase 5: diagnostics vs the technician's gauges) ----------

    /// <summary>Gauge checks, newest first, with the average and largest system-minus-gauge difference per value.</summary>
    object? ListGaugeChecks(Req r)
    {
        var system = TechSystem(r);
        int limit = (int)Py.NumberOf(Validate.Query(r.Query("limit"), Field.Int("limit", gt: 0, le: 500), JsonValue.Create(50L)))!.Value.V;
        var rows = r.S.GaugeChecks.Where(c => c.SystemId == system.Id).OrderByDescending(c => c.Time).ThenByDescending(c => c.Id).Take(limit).ToList();
        var views = rows.Select(Gauge.CheckView).ToList();
        return new JsonObject { ["checks"] = new JsonArray(views.Select(v => (JsonNode?)v).ToArray()), ["summary"] = Gauge.Summary(views) };
    }

    /// <summary>Store the gauge readings with the snapshot nearest the time they were read
    /// (minutes_ago before now, within 3 minutes).</summary>
    object? AddGaugeCheck(Req r)
    {
        var system = TechSystem(r);
        var who = r.Who;
        var b = Body(r, Field.Float("p_low", ge: -15, le: 800), Field.Float("p_high", ge: -15, le: 800),
                     Field.Float("t_suc", ge: -60, le: 250), Field.Float("t_liq", ge: -60, le: 250),
                     Field.Float("t_ret", ge: -60, le: 250), Field.Float("t_sup", ge: -60, le: 250),
                     Field.Float("minutes_ago", ge: 0, le: 60) with { Default = JsonValue.Create(0.0), Nullable = false },
                     Field.Str("technician", 200), Field.Str("note", 500));
        var entered = Gauge.Entered.ToDictionary(k => k, k => b.Float(k));
        if (entered.Values.All(v => v is null)) throw new ApiError(422, "Enter at least one gauge reading.");
        var at = Db.UtcNow() - TimeSpan.FromTicks((long)Math.Round(b.Float("minutes_ago")!.Value * 60e6, MidpointRounding.ToEven) * 10);   // timedelta: whole µs
        var before = r.S.Snapshots.Where(x => x.SystemId == system.Id && x.Time <= at).OrderByDescending(x => x.Time)
                      .Select(x => new { x.Time, x.Data }).FirstOrDefault();
        var after = r.S.Snapshots.Where(x => x.SystemId == system.Id && x.Time > at).OrderBy(x => x.Time)
                     .Select(x => new { x.Time, x.Data }).FirstOrDefault();
        var near = new[] { before, after }.Where(x => x is not null && Math.Abs(Seconds(x.Time - at)) <= Gauge.NearestS).ToList();
        if (near.Count == 0) throw new ApiError(409, "No system readings within 3 minutes of that time to compare with.");
        var row = near.MinBy(x => Math.Abs(Seconds(x!.Time - at)))!;
        var sysSide = Gauge.SystemSide(Load(row.Data));
        sysSide["time"] = Iso(row.Time);
        string? Clean(string? v) => (v ?? "").Trim() is { Length: > 0 } t ? t : null;
        var c = new GaugeCheck
        {
            SystemId = system.Id, Time = at, Technician = Clean(b.Str("technician")), Note = Clean(b.Str("note")),
            Refrigerant = system.Refrigerant, AtmPsia = system.AtmPsia,
            GaugeData = Db.Json(Gauge.Derive(entered, system.Refrigerant, system.AtmPsia, Tables)),
            SystemData = Db.Json(sysSide), CreatedBy = who.UserId,
        };
        r.S.GaugeChecks.Add(c);
        r.S.SaveChanges();
        return Gauge.CheckView(c);
    }

    object? DeleteGaugeCheck(Req r)
    {
        var system = TechSystem(r);
        int checkId = Validate.PathInt("check_id", r.Route("check_id"));
        var c = r.S.GaugeChecks.Find(checkId);
        if (c is null || c.SystemId != system.Id) throw new ApiError(404, "no such gauge check");
        r.S.GaugeChecks.Remove(c);
        r.S.SaveChanges();
        return new JsonObject { ["ok"] = true };
    }

    // ---------- node commands ----------

    object? SendCommand(Req r)
    {
        var system = TechSystem(r);
        var b = Body(r, Field.Choice("node", ["outdoor", "indoor"], required: true), new Field("cmd", FieldType.Dict) { Required = true, Nullable = false });
        var cmd = (JsonObject)b.Node("cmd")!;
        if (cmd["cmd"] is not JsonValue verb || Py.Classify(verb) != Py.Kind.String)
            throw new ApiError(422, "cmd needs a \"cmd\" verb, e.g. {\"cmd\":\"cal_zero\",\"ch\":\"p_liq\"}");
        var node = b.Str("node")!;
        var device = r.S.Devices.FirstOrDefault(d => d.SystemId == system.Id && d.Node == node)
                     ?? throw new ApiError(404, $"{node} node has not reported yet");
        var row = new Command { DeviceId = device.Id, Payload = Db.Json(cmd) };
        r.S.Commands.Add(row);
        r.S.SaveChanges();
        row.Sent = _publish($"hvac/{system.SiteId}/{node}/cmd", cmd, false);
        r.S.SaveChanges();
        return CommandView(row, node);
    }

    object? ListCommands(Req r)
    {
        var system = TechSystem(r);
        int limit = (int)Py.NumberOf(Validate.Query(r.Query("limit"), Field.Int("limit", gt: 0, le: 200), JsonValue.Create(20L)))!.Value.V;
        var rows = r.S.Commands.Join(r.S.Devices.Where(d => d.SystemId == system.Id), c => c.DeviceId, d => d.Id, (c, d) => new { c, d.Node })
                    .OrderByDescending(x => x.c.Id).Take(limit).ToList();
        return new JsonArray(rows.Select(x => (JsonNode?)CommandView(x.c, x.Node)).ToArray());
    }
}
