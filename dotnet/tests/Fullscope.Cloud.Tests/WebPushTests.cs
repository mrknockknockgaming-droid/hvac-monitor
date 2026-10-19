using System.Net;
using System.Security.Cryptography;
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

/// <summary>test_webpush.py</summary>
[Collection("clock")]
public class WebPushTests
{
    // RFC 8291 appendix A
    const string UaPublic = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4";
    const string UaPrivate = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94";
    const string AuthSecret = "BTBZMqHH6r4Tts7J_aSIgg";
    const string RfcBody = "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_" +
                           "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN";
    const string Endpoint = "https://fcm.googleapis.com/fcm/send/abc123";
    static readonly ECCurve P256 = ECCurve.NamedCurves.nistP256;

    [Fact]
    public void EncryptionMatchesRfc8291()
    {
        var body = WebPush.Encrypt("When I grow up, I want to be a watermelon"u8.ToArray(), WebPush.UnB64u(UaPublic), WebPush.UnB64u(AuthSecret),
                                   salt: WebPush.UnB64u("DGv6ra1nlYgDCS1FRnbzlw"), asPrivate: WebPush.UnB64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"));
        Assert.Equal(RfcBody, WebPush.B64u(body));
    }

    /// <summary>What the browser does, to check our messages.</summary>
    internal static JsonNode Decrypt(byte[] body)
    {
        var salt = body[..16];
        int idlen = body[20];
        var asPublic = body[21..(21 + idlen)];
        var uaPub = WebPush.UnB64u(UaPublic);
        using var ua = ECDiffieHellman.Create(new ECParameters { Curve = P256, D = WebPush.UnB64u(UaPrivate), Q = new ECPoint { X = uaPub[1..33], Y = uaPub[33..] } });
        using var other = ECDiffieHellman.Create(new ECParameters { Curve = P256, Q = new ECPoint { X = asPublic[1..33], Y = asPublic[33..] } });
        var shared = ua.DeriveRawSecretAgreement(other.PublicKey);
        byte[] H(byte[] k, byte[] d) => HMACSHA256.HashData(k, d);
        var ikm = H(H(WebPush.UnB64u(AuthSecret), shared), [.. "WebPush: info\0"u8, .. uaPub, .. asPublic, 1]);
        var prk = H(salt, ikm);
        var rest = body[(21 + idlen)..];
        var plain = new byte[rest.Length - 16];
        using (var gcm = new AesGcm(H(prk, [.. "Content-Encoding: aes128gcm\0"u8, 1])[..16], 16))
            gcm.Decrypt(H(prk, [.. "Content-Encoding: nonce\0"u8, 1])[..12], rest[..^16], rest[^16..], plain);
        Assert.Equal(2, plain[^1]);
        return JsonNode.Parse(Encoding.UTF8.GetString(plain[..^1]))!;
    }

    [Fact]
    public void VapidHeaderIsASignedJwt()
    {
        var (priv, pub) = WebPush.NewKeyPair();
        var h = WebPush.VapidHeader(Endpoint, priv, pub, "mailto:a@b.com", now: 1000);
        var parts = h["vapid t=".Length..].Split(", k=");
        Assert.Equal(pub, parts[1]);
        var jwt = parts[0].Split('.');
        var claims = JsonNode.Parse(WebPush.UnB64u(jwt[1]))!;
        Assert.Equal("https://fcm.googleapis.com", (string)claims["aud"]!);
        Assert.Equal(1000 + 12 * 3600, (long)claims["exp"]!);
        var q = WebPush.UnB64u(pub);
        using var key = ECDsa.Create(new ECParameters { Curve = P256, Q = new ECPoint { X = q[1..33], Y = q[33..] } });
        Assert.True(key.VerifyData(Encoding.ASCII.GetBytes(jwt[0] + "." + jwt[1]), WebPush.UnB64u(jwt[2]), HashAlgorithmName.SHA256));
    }

    [Fact]
    public void OnlyKnownPushServices()
    {
        Assert.True(WebPush.EndpointOk(Endpoint));
        Assert.True(WebPush.EndpointOk("https://web.push.apple.com/QGx"));
        Assert.True(WebPush.EndpointOk("https://updates.push.services.mozilla.com/wpush/v2/x"));
        Assert.False(WebPush.EndpointOk("http://fcm.googleapis.com/x"));
        Assert.False(WebPush.EndpointOk("https://192.168.1.1/x"));
        Assert.False(WebPush.EndpointOk("https://evilgoogleapis.com/x"));
    }

    sealed class FakePushService
    {
        public int Status = 201;
        public readonly List<(string Endpoint, JsonNode Message, Dictionary<string, string> Headers)> Sent = [];

        public int Post(string endpoint, byte[] body, Dictionary<string, string> headers)
        {
            Sent.Add((endpoint, Decrypt(body), headers));
            return Status;
        }
    }

    [Fact]
    public async Task SubscribeAndTestNotification()
    {
        var fake = new FakePushService();
        var db = new Seeded();
        using (var s = db.Sessions.Open())
        {
            s.Users.Add(new User { Email = "owner@example.com", Role = "homeowner", PasswordHash = Passwords.Hash(TestApi.Pw) });
            s.SaveChanges();
        }
        var api = new ApiServer(db.Sessions, new Settings { AppUrl = "https://app.example.com/app/", SessionDays = 30, WebDir = "" },
                                (_, _, _) => true, new NoMailer(), sendAsync: false, cache: false, pushPost: fake.Post);
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.UseTestServer();
        await using var app = builder.Build();
        api.Map(app);
        await app.StartAsync();
        var c = new Browser(app.GetTestClient());
        await c.Login("owner@example.com");
        var key = (string)(await c.Send("GET", "/api/push/key")).Body!["public_key"]!;
        Assert.Equal(65, WebPush.UnB64u(key).Length);
        Assert.Equal(key, (string)(await c.Send("GET", "/api/push/key")).Body!["public_key"]!);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await c.Send("POST", "/api/push/subscribe", new { endpoint = "https://10.0.0.1/hook", p256dh = UaPublic, auth = AuthSecret })).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await c.Send("POST", "/api/push/subscribe", new { endpoint = Endpoint, p256dh = "x", auth = AuthSecret })).Status);
        var sub = new { endpoint = Endpoint, p256dh = UaPublic, auth = AuthSecret };
        Assert.Equal("""{"ok":true,"devices":1}""", (await c.Send("POST", "/api/push/subscribe", sub)).Body!.ToJsonString());
        Assert.Equal(1, (int)(await c.Send("POST", "/api/push/subscribe", sub)).Body!["devices"]!);
        Assert.Equal("""{"sent":1,"devices":1}""", (await c.Send("POST", "/api/push/test")).Body!.ToJsonString());
        Assert.Equal("Fullscope", (string)fake.Sent[0].Message["title"]!);
        Assert.Equal("aes128gcm", fake.Sent[0].Headers["Content-Encoding"]);
        Assert.EndsWith("k=" + key, fake.Sent[0].Headers["Authorization"]);

        fake.Status = 410;
        Assert.Equal(0, (int)(await c.Send("POST", "/api/push/test")).Body!["sent"]!);
        using (var s = db.Sessions.Open()) Assert.Empty(s.PushSubscriptions);
        await c.Send("POST", "/api/push/subscribe", sub);
        Assert.Equal("""{"ok":true}""", (await c.Send("POST", "/api/push/unsubscribe", new { endpoint = Endpoint })).Body!.ToJsonString());
        using (var s = db.Sessions.Open()) Assert.Empty(s.PushSubscriptions);
    }

    sealed class NoMailer : IMailer
    {
        public bool Enabled => false;
        public bool Send(string to, string subject, string body) => false;
    }

    static readonly JsonObject Flag = new() { ["level"] = "warn", ["code"] = "dt_low", ["text"] = "Air delta-T 9.0°F is low" };
    const double T0 = 1_790_000_000.0;

    [Fact]
    public void AlertsGoToSubscribedOwnersAndContractors()
    {
        var db = new Seeded();
        int owner, tech, sid;
        using (var s = db.Sessions.Open())
        {
            var sy = s.Systems.Single(x => x.SiteId == "home");
            sid = sy.Id;
            var o = new User { Email = "pat@example.com", Role = "homeowner" };
            var t = new User { Email = "tech@example.com", Role = "contractor", AccountId = sy.AccountId };
            s.Users.AddRange(o, t);
            s.SaveChanges();
            s.SystemMembers.Add(new SystemMember { SystemId = sy.Id, UserId = o.Id });
            s.SaveChanges();
            (owner, tech) = (o.Id, t.Id);
            Assert.Empty(Alerts.PushesFor(s, sy, Alerts.Sync(s, sy, [Flag], T0, 0), T0, 6 * 3600, "https://x/app/"));
            s.SaveChanges();
            s.PushSubscriptions.Add(new PushSubscription { UserId = owner, Endpoint = Endpoint, P256dh = UaPublic, Auth = AuthSecret });
            s.PushSubscriptions.Add(new PushSubscription { UserId = tech, Endpoint = Endpoint + "x", P256dh = UaPublic, Auth = AuthSecret });
            s.SaveChanges();
        }
        using (var s = db.Sessions.Open())
        {
            var sy = s.Systems.Find(sid)!;
            var ev = Alerts.Sync(s, sy, [], T0 + 60, 0);
            s.SaveChanges();
            ev = Alerts.Sync(s, sy, [Flag], T0 + 120, 0);
            s.SaveChanges();
            var jobs = Alerts.PushesFor(s, sy, ev.Where(e => e.Kind == "raised").ToList(), T0 + 120, 6 * 3600, "https://x/app/");
            var by = jobs.ToDictionary(j => j.UserId);
            Assert.Equal([owner, tech], by.Keys.Order());
            Assert.Equal("Home: Air from your vents isn't as cool as it should be", (string)by[owner].Message["title"]!);
            Assert.EndsWith($"#/home/{sid}", (string)by[owner].Message["url"]!);
            Assert.Equal("Home: Warning", (string)by[tech].Message["title"]!);
            Assert.Equal($"fullscope-{sid}-dt_low", (string)by[tech].Message["tag"]!);
            s.AlertPrefs.Add(new AlertPrefs { SystemId = sid, EmailOwner = false, EmailContractor = true });
            s.SaveChanges();
            Assert.Equal([tech], Alerts.PushUsers(s, sy).Select(x => x.UserId));
        }
    }

    [Fact]
    public void IngestSendsAndMarksPushed()
    {
        var db = new Seeded();
        int sid;
        using (var s = db.Sessions.Open())
        {
            var sy = s.Systems.Single(x => x.SiteId == "home");
            sid = sy.Id;
            var o = new User { Email = "pat@example.com", Role = "homeowner" };
            s.Users.Add(o);
            s.SaveChanges();
            s.SystemMembers.Add(new SystemMember { SystemId = sid, UserId = o.Id });
            s.PushSubscriptions.Add(new PushSubscription { UserId = o.Id, Endpoint = Endpoint, P256dh = UaPublic, Auth = AuthSecret });
            s.Snapshots.Add(new Snapshot { SystemId = sid, Time = Db.Ts(T0 - 3600), Mode = "idle", Data = """{"flags": []}""" });
            s.SaveChanges();
            WebPush.VapidKeys(s, create: true);
        }
        var fake = new FakePushService();
        var settings = new Settings { AppUrl = "https://x/app/" };
        var ing = new Fullscope.Cloud.Ingest.Ingest(db.Sessions, Seeded.Tables, 60, 0, 600, 6 * 3600, settings.AppUrl)
        {
            Pusher = new Pusher(db.Sessions, settings, post: fake.Post), Spawn = work => work(),
        };
        ing.Sweep(T0);
        using (var s = db.Sessions.Open()) Assert.NotNull(s.Alerts.Single().PushedAt);
        Assert.Single(fake.Sent);
        Assert.EndsWith($"#/home/{sid}", (string)fake.Sent[0].Message["url"]!);
        ing.Sweep(T0 + 60);
        Assert.Single(fake.Sent);
    }
}
