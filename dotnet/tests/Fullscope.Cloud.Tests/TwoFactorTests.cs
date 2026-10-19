using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Api;
using Fullscope.Cloud.Data;
using Fullscope.Cloud.Ingest;
using Fullscope.Core;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;

namespace Fullscope.Cloud.Tests;

/// <summary>The web API on a test server over a seeded database, with one cookie jar per Browser.</summary>
public sealed class TestApi : IAsyncDisposable
{
    public const string Pw = "correct horse battery";
    public Seeded Db { get; } = new();
    public WebApplication App { get; }

    sealed class NoMail : IMailer
    {
        public bool Enabled => false;
        public bool Send(string to, string subject, string body) => false;
    }

    public TestApi(Func<string, Func<string, JsonNode, bool, bool>, ApiServer>? make = null)
    {
        using (var s = Db.Sessions.Open())
        {
            var home = s.Systems.Single(x => x.SiteId == "home");
            var tech = new User { Email = "tech@example.com", Role = "contractor", AccountId = home.AccountId, PasswordHash = Passwords.Hash(Pw) };
            var owner = new User { Email = "owner@example.com", Role = "homeowner", PasswordHash = Passwords.Hash(Pw) };
            s.Users.AddRange(tech, owner);
            s.SaveChanges();
            s.SystemMembers.Add(new SystemMember { SystemId = home.Id, UserId = owner.Id });
            s.SaveChanges();
        }
        var api = new ApiServer(Db.Sessions, new Settings { AppUrl = "https://app.example.com/app/", SessionDays = 30, WebDir = "" },
                                (_, _, _) => true, new NoMail(), sendAsync: false, cache: false);
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.UseTestServer();
        App = builder.Build();
        api.Map(App);
        App.StartAsync().GetAwaiter().GetResult();
    }

    public Browser NewBrowser() => new(App.GetTestClient());

    public async ValueTask DisposeAsync() => await App.DisposeAsync();
}

public sealed class Browser(HttpClient http)
{
    string? _cookie;

    public async Task<(HttpStatusCode Status, JsonNode? Body, bool SetCookie)> Send(string method, string path, object? body = null,
                                                                                     Dictionary<string, string>? headers = null)
    {
        var req = new HttpRequestMessage(new HttpMethod(method), path);
        req.Headers.Add("X-Requested-With", "fullscope");
        if (_cookie is not null) req.Headers.Add("Cookie", _cookie);
        foreach (var (k, v) in headers ?? []) req.Headers.Add(k, v);
        if (body is not null)
            req.Content = new StringContent(body as string ?? System.Text.Json.JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        var res = await http.SendAsync(req);
        bool set = false;
        if (res.Headers.TryGetValues("Set-Cookie", out var cookies))
            foreach (var c in cookies)
                if (c.StartsWith(Auth.Cookie + "=", StringComparison.Ordinal))
                {
                    var v = c.Split(';')[0];
                    _cookie = v.EndsWith('=') ? null : v;
                    set = _cookie is not null;
                }
        var text = await res.Content.ReadAsStringAsync();
        return (res.StatusCode, text.Length > 0 ? JsonNode.Parse(text) : null, set);
    }

    public Task<(HttpStatusCode Status, JsonNode? Body, bool SetCookie)> Login(string email, string pw = TestApi.Pw, string? code = null,
                                                                              Dictionary<string, string>? headers = null) =>
        Send("POST", "/api/auth/login", code is null ? new { email, password = pw } : new { email, password = pw, code }, headers);
}

/// <summary>test_two_factor.py</summary>
[Collection("clock")]
public class TwoFactorTests
{
    [Fact]
    public void TotpMatchesRfc6238()
    {
        const string secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
        foreach (var (t, want) in new[] { (59L, "287082"), (1111111109L, "081804"), (1234567890L, "005924"), (2000000000L, "279037") })
            Assert.Equal(want, Totp.Code(secret, t / 30));
        Assert.Equal(1, Totp.Check(secret, "287 082", now: 59));
        Assert.Equal(1, Totp.Check(secret, "287082", now: 59 + 30));
        Assert.Null(Totp.Check(secret, "287082", now: 59 + 90));
        Assert.Null(Totp.Check(secret, "287082", lastStep: 1, now: 59));
        Assert.Null(Totp.Check(secret, "28708", now: 59));
        Assert.StartsWith("otpauth://totp/Fullscope:a%40b.com?secret=" + secret, Totp.Uri(secret, "a@b.com"));
        Assert.Equal(32, Totp.NewSecret().Length);
    }

    static async Task<(string Secret, List<string> Codes)> SetUp(Browser c)
    {
        Assert.Equal(HttpStatusCode.OK, (await c.Login("tech@example.com")).Status);
        var r = await c.Send("POST", "/api/auth/2fa/setup", new { password = TestApi.Pw });
        Assert.Equal(HttpStatusCode.OK, r.Status);
        var secret = (string)r.Body!["secret"]!;
        Assert.Contains("secret=" + secret, (string)r.Body!["uri"]!);
        var wrong = Totp.Check(secret, "000000") is null ? "000000" : "111111";
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await c.Send("POST", "/api/auth/2fa/enable", new { code = wrong })).Status);
        r = await c.Send("POST", "/api/auth/2fa/enable", new { code = Totp.Code(secret, (long)(Time.Now() / 30)) });
        Assert.Equal(HttpStatusCode.OK, r.Status);
        var codes = r.Body!["recovery_codes"]!.AsArray().Select(x => (string)x!).ToList();
        Assert.Equal(8, codes.Distinct().Count());
        return (secret, codes);
    }

    [Fact]
    public async Task SignInAsksForTheCode()
    {
        await using var api = new TestApi();
        var c = api.NewBrowser();
        Assert.Equal(HttpStatusCode.Unauthorized, (await c.Send("POST", "/api/auth/2fa/setup", new { password = "x" })).Status);
        await c.Login("tech@example.com");
        Assert.Equal(HttpStatusCode.Unauthorized, (await c.Send("POST", "/api/auth/2fa/setup", new { password = "wrong pass" })).Status);
        var (secret, codes) = await SetUp(c);
        var state = (await c.Send("GET", "/api/auth/2fa")).Body!;
        Assert.Equal("""{"enabled":true,"recovery_left":8}""", state.ToJsonString());
        Assert.Equal(HttpStatusCode.Conflict, (await c.Send("POST", "/api/auth/2fa/setup", new { password = TestApi.Pw })).Status);

        var d = api.NewBrowser();
        var r = await d.Login("tech@example.com");
        Assert.Equal(HttpStatusCode.OK, r.Status);
        Assert.Equal("""{"totp_required":true}""", r.Body!.ToJsonString());
        Assert.False(r.SetCookie);
        Assert.Equal(HttpStatusCode.Unauthorized, (await d.Send("GET", "/api/auth/me")).Status);
        Assert.Equal(HttpStatusCode.Unauthorized, (await d.Login("tech@example.com", code: "12345")).Status);

        int last;
        using (var s = api.Db.Sessions.Open()) last = s.Users.Single(u => u.Email == "tech@example.com").TotpLastStep!.Value;
        Assert.Equal(HttpStatusCode.Unauthorized, (await d.Login("tech@example.com", code: Totp.Code(secret, last))).Status);
        var old = Time.Clock;
        try
        {
            Time.Clock = () => Time.FromEpoch((last + 1) * 30.0 + 1);
            var next = Totp.Code(secret, last + 1);
            r = await d.Login("tech@example.com", code: next);
            Assert.Equal(HttpStatusCode.OK, r.Status);
            Assert.Equal("contractor", (string)r.Body!["role"]!);
            Assert.Equal(HttpStatusCode.Unauthorized, (await api.NewBrowser().Login("tech@example.com", code: next)).Status);   // replayed
        }
        finally
        {
            Time.Clock = old;
        }

        var e = api.NewBrowser();
        Assert.Equal(HttpStatusCode.OK, (await e.Login("tech@example.com", code: codes[0].ToUpperInvariant())).Status);
        Assert.Equal(HttpStatusCode.Unauthorized, (await e.Login("tech@example.com", code: codes[0])).Status);
        Assert.Equal(7, (int)(await e.Send("GET", "/api/auth/2fa")).Body!["recovery_left"]!);
        Assert.Equal(HttpStatusCode.Unauthorized, (await api.NewBrowser().Login("tech@example.com", "nope nope nope")).Status);
    }

    [Fact]
    public async Task TurningItOffNeedsThePassword()
    {
        await using var api = new TestApi();
        var c = api.NewBrowser();
        await SetUp(c);
        Assert.Equal(HttpStatusCode.Unauthorized, (await c.Send("POST", "/api/auth/2fa/disable", new { password = "wrong pass" })).Status);
        Assert.Equal("""{"enabled":false}""", (await c.Send("POST", "/api/auth/2fa/disable", new { password = TestApi.Pw })).Body!.ToJsonString());
        Assert.Equal("contractor", (string)(await api.NewBrowser().Login("tech@example.com")).Body!["role"]!);
    }

    [Fact]
    public async Task TurningItOnSignsOutOtherBrowsers()
    {
        await using var api = new TestApi();
        var other = api.NewBrowser();
        await other.Login("tech@example.com");
        var c = api.NewBrowser();
        await SetUp(c);
        Assert.Equal(HttpStatusCode.Unauthorized, (await other.Send("GET", "/api/auth/me")).Status);
        Assert.Equal(HttpStatusCode.OK, (await c.Send("GET", "/api/auth/me")).Status);
    }

    [Fact]
    public async Task ResetLinkDoesNotSkipTheCode()
    {
        await using var api = new TestApi();
        var c = api.NewBrowser();
        await SetUp(c);
        using (var s = api.Db.Sessions.Open())
        {
            var user = s.Users.Single(u => u.Email == "tech@example.com");
            s.PasswordResets.Add(new PasswordReset { TokenHash = Data.Db.HashKey("tok"), UserId = user.Id, ExpiresAt = new DateTime(2100, 1, 1, 0, 0, 0, DateTimeKind.Utc) });
            s.SaveChanges();
        }
        var d = api.NewBrowser();
        var r = await d.Send("POST", "/api/auth/reset", new { token = "tok", password = "a brand new password" });
        Assert.Equal("""{"totp_required":true,"email":"tech@example.com"}""", r.Body!.ToJsonString());
        Assert.False(r.SetCookie);
        Assert.Equal(HttpStatusCode.Unauthorized, (await c.Send("GET", "/api/auth/me")).Status);
        Assert.Equal("""{"totp_required":true}""", (await d.Login("tech@example.com", "a brand new password")).Body!.ToJsonString());
    }

    [Fact]
    public async Task ApiKeysHaveNoSecondFactor()
    {
        await using var api = new TestApi();
        var r = await api.NewBrowser().Send("GET", "/api/auth/2fa", headers: new() { ["X-API-Key"] = api.Db.Key1 });
        Assert.Equal(HttpStatusCode.BadRequest, r.Status);
    }

    [Fact]
    public async Task FailedSignInsFromOneAddressAreLimited()
    {
        await using var api = new TestApi();
        var c = api.NewBrowser();
        Dictionary<string, string> Tunnel(string ip) => new() { ["CF-Connecting-IP"] = ip };
        for (int i = 0; i < 30; i++) await c.Login($"guess{i}@example.com", "nope", headers: Tunnel("198.51.100.7"));
        Assert.Equal(HttpStatusCode.TooManyRequests, (await c.Login("tech@example.com", headers: Tunnel("198.51.100.7"))).Status);
        Assert.Equal(HttpStatusCode.OK, (await c.Login("tech@example.com", headers: Tunnel("203.0.113.9"))).Status);
    }
}
