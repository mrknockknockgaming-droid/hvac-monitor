using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Api;
using Fullscope.Cloud.Ingest;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;

namespace Fullscope.Cloud.Tests;

/// <summary>The public landing page at / and its order requests (test_orders.py).</summary>
public sealed class OrderTests : IAsyncDisposable
{
    sealed class FakeMail : IMailer
    {
        public List<(string To, string Subject, string Body)> Sent { get; } = [];
        public bool Enabled => true;
        public bool Send(string to, string subject, string body) { Sent.Add((to, subject, body)); return true; }
    }

    readonly Seeded _db = new();
    readonly FakeMail _mail = new();
    readonly string _web = Directory.CreateTempSubdirectory("fullscope-web").FullName;
    readonly WebApplication _app;
    readonly HttpClient _http;

    static readonly Dictionary<string, object> Good = new()
    {
        ["company"] = "Desert Air Co.", ["name"] = "Sam", ["email"] = " Sam@DesertAir.example ", ["phone"] = "480-555-0100",
        ["systems"] = 5, ["notes"] = "Pilot on maintenance-agreement customers",
    };

    public OrderTests()
    {
        File.WriteAllText(Path.Combine(_web, "welcome.html"), "<a href=\"/app/\">Sign in</a>");
        var api = new ApiServer(_db.Sessions, new Settings { AppUrl = "https://app.example.com/app/", WebDir = _web, OrderEmail = "owner@example.com" },
                                (_, _, _) => true, _mail, sendAsync: false, cache: false);
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.UseTestServer();
        _app = builder.Build();
        api.Map(_app);
        _app.StartAsync().GetAwaiter().GetResult();
        _http = _app.GetTestClient();
    }

    async Task<(HttpStatusCode Status, JsonNode? Body)> Post(Dictionary<string, object> body)
    {
        var res = await _http.PostAsync("/api/orders", new StringContent(System.Text.Json.JsonSerializer.Serialize(body), Encoding.UTF8, "application/json"));
        var text = await res.Content.ReadAsStringAsync();
        return (res.StatusCode, text.Length > 0 ? JsonNode.Parse(text) : null);
    }

    static Dictionary<string, object> With(string key, object value) => new(Good) { [key] = value };

    [Fact]
    public async Task Landing_page_is_served_at_the_root()
    {
        var res = await _http.GetAsync("/");
        Assert.Equal(HttpStatusCode.OK, res.StatusCode);
        Assert.Contains("href=\"/app/\"", await res.Content.ReadAsStringAsync());
        Assert.Equal("no-cache", res.Headers.CacheControl?.ToString());
    }

    [Fact]
    public async Task Order_request_is_saved_and_emailed_to_the_owner()
    {
        var (status, body) = await Post(Good);
        Assert.Equal(HttpStatusCode.OK, status);
        Assert.True(body!["ok"]!.GetValue<bool>());
        using (var s = _db.Sessions.Open())
        {
            var o = s.Orders.Single();
            Assert.Equal(("Desert Air Co.", "sam@desertair.example", 5, "480-555-0100"), (o.Company, o.Email, o.Systems, o.Phone));
        }
        var (to, subject, text) = Assert.Single(_mail.Sent);
        Assert.Equal("owner@example.com", to);
        Assert.Contains("Desert Air Co.", subject);
        Assert.Contains("Systems: 5", text);
        Assert.Contains("No payment was taken", text);
    }

    [Fact]
    public async Task Order_request_checks_its_fields()
    {
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await Post(With("email", "not-an-email"))).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await Post(With("systems", 0))).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await Post(With("company", "  "))).Status);
        Assert.Empty(_mail.Sent);
    }

    [Fact]
    public async Task Bots_and_floods_are_not_saved()
    {
        Assert.Equal(HttpStatusCode.OK, (await Post(With("website", "http://spam.example"))).Status);
        using (var s = _db.Sessions.Open()) Assert.Empty(s.Orders);
        for (int i = 0; i < 5; i++) Assert.Equal(HttpStatusCode.OK, (await Post(Good)).Status);
        Assert.Equal((HttpStatusCode)429, (await Post(Good)).Status);
        Assert.Equal(5, _mail.Sent.Count);
    }

    public async ValueTask DisposeAsync()
    {
        await _app.DisposeAsync();
        Directory.Delete(_web, true);
    }
}
