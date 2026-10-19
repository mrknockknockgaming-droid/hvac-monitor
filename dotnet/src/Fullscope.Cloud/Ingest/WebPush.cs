using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

namespace Fullscope.Cloud.Ingest;

/// <summary>
/// Phone and browser notifications (port of hvaccloud/webpush.py). A browser that turns notifications on
/// gives us a push-service endpoint and two keys; each message is encrypted for it (RFC 8291, aes128gcm)
/// and signed with this server's VAPID key (RFC 8292). The VAPID pair lives in server_keys, shared with
/// the Python version.
/// </summary>
public static class WebPush
{
    public const int TtlS = 24 * 3600;
    public const int JwtHours = 12;
    const int RecordSize = 4096;
    static readonly string[] PushHosts = ["fcm.googleapis.com", ".googleapis.com", ".mozilla.com", ".push.apple.com", ".notify.windows.com"];

    public static string B64u(byte[] b) => Convert.ToBase64String(b).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    public static byte[] UnB64u(string s)
    {
        var t = s.Replace('-', '+').Replace('_', '/');
        return Convert.FromBase64String(t + new string('=', (4 - t.Length % 4) % 4));
    }

    /// <summary>Only the push services of the browsers we support (the server POSTs to the endpoint).</summary>
    public static bool EndpointOk(string url)
    {
        if (!System.Uri.TryCreate(url, UriKind.Absolute, out var u) || u.Scheme != "https") return false;
        var host = u.Host.ToLowerInvariant();
        return PushHosts.Any(h => host == h || (h.StartsWith('.') && host.EndsWith(h, StringComparison.Ordinal)));
    }

    static readonly ECCurve P256 = ECCurve.NamedCurves.nistP256;

    static byte[] Point(ECParameters p) => [0x04, .. p.Q.X!, .. p.Q.Y!];

    static ECPoint Q(byte[] point) => new() { X = point[1..33], Y = point[33..65] };

    /// <summary>(private d, public uncompressed point), both base64url.</summary>
    public static (string Private, string Public) NewKeyPair()
    {
        using var k = ECDsa.Create(P256);
        var p = k.ExportParameters(true);
        return (B64u(p.D!), B64u(Point(p)));
    }

    /// <summary>(private, public), made on first use when `create`; null if there are none yet.</summary>
    public static (string Private, string Public)? VapidKeys(Db s, bool create = false)
    {
        var rows = s.ServerKeys.AsNoTracking().Where(k => k.Name == "vapid_private" || k.Name == "vapid_public").ToDictionary(k => k.Name, k => k.Value);
        if (rows.TryGetValue("vapid_private", out var priv) && rows.TryGetValue("vapid_public", out var pub)) return (priv, pub);
        if (!create) return null;
        var (p, q) = NewKeyPair();
        s.ServerKeys.Add(new ServerKey { Name = "vapid_private", Value = p });
        s.ServerKeys.Add(new ServerKey { Name = "vapid_public", Value = q });
        try
        {
            s.SaveChanges();
        }
        catch (DbUpdateException)                 // the other process made them at the same moment
        {
            s.ChangeTracker.Clear();
            return VapidKeys(s);
        }
        return (p, q);
    }

    static byte[] Hmac(byte[] key, byte[] data) => HMACSHA256.HashData(key, data);

    /// <summary>The request body for one browser (RFC 8291); salt and asPrivate are random unless given (tests).</summary>
    public static byte[] Encrypt(byte[] payload, byte[] uaPublic, byte[] authSecret, byte[]? salt = null, byte[]? asPrivate = null)
    {
        salt ??= RandomNumberGenerator.GetBytes(16);
        using var asKey = asPrivate is null ? ECDiffieHellman.Create(P256) : ImportEcdh(asPrivate);
        var asPublic = Point(asKey.ExportParameters(false));
        using var ua = ECDiffieHellman.Create(new ECParameters { Curve = P256, Q = Q(uaPublic) });
        var shared = asKey.DeriveRawSecretAgreement(ua.PublicKey);
        var ikm = Hmac(Hmac(authSecret, shared), [.. "WebPush: info\0"u8, .. uaPublic, .. asPublic, 1]);
        var prk = Hmac(salt, ikm);
        var cek = Hmac(prk, [.. "Content-Encoding: aes128gcm\0"u8, 1])[..16];
        var nonce = Hmac(prk, [.. "Content-Encoding: nonce\0"u8, 1])[..12];
        var plain = new byte[payload.Length + 1];
        payload.CopyTo(plain, 0);
        plain[^1] = 2;                                      // last (only) record
        var cipher = new byte[plain.Length];
        var tag = new byte[16];
        using (var gcm = new AesGcm(cek, 16)) gcm.Encrypt(nonce, plain, cipher, tag);
        var head = new byte[21];
        salt.CopyTo(head, 0);
        BinaryPrimitives.WriteUInt32BigEndian(head.AsSpan(16), RecordSize);
        head[20] = (byte)asPublic.Length;
        return [.. head, .. asPublic, .. cipher, .. tag];
    }

    static ECDiffieHellman ImportEcdh(byte[] d)
    {
        using var tmp = ECDsa.Create(new ECParameters { Curve = P256, D = d });   // derives the public point
        return ECDiffieHellman.Create(tmp.ExportParameters(true));
    }

    public static string VapidSubject(Settings settings)
    {
        var sender = settings.SmtpFrom.Length > 0 ? settings.SmtpFrom : settings.SmtpUser;
        if (sender.Contains('@')) return "mailto:" + sender.Split('<')[^1].Trim(' ', '>');
        var u = new System.Uri(settings.AppUrl);
        return $"{u.Scheme}://{u.Authority}";
    }

    /// <summary>Authorization header value: a JWT for the push service's origin, signed with ES256.</summary>
    public static string VapidHeader(string endpoint, string priv, string pub, string subject, double? now = null)
    {
        var u = new System.Uri(endpoint);
        long t = (long)Math.Floor(now ?? Time.Now());
        string Enc(JsonObject o) => B64u(Encoding.UTF8.GetBytes(o.ToJsonString()));
        var signing = Enc(new JsonObject { ["typ"] = "JWT", ["alg"] = "ES256" }) + "." +
                      Enc(new JsonObject { ["aud"] = $"{u.Scheme}://{u.Authority}", ["exp"] = t + JwtHours * 3600, ["sub"] = subject });
        using var key = ECDsa.Create(new ECParameters { Curve = P256, D = UnB64u(priv), Q = Q(UnB64u(pub)) });
        var sig = key.SignData(Encoding.ASCII.GetBytes(signing), HashAlgorithmName.SHA256);   // r || s, 64 bytes
        return $"vapid t={signing}.{B64u(sig)}, k={pub}";
    }

    static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(10) };

    /// <summary>POST to the push service; the HTTP status, 0 when it couldn't be reached.</summary>
    public static int Post(string endpoint, byte[] body, Dictionary<string, string> headers)
    {
        var req = new HttpRequestMessage(HttpMethod.Post, endpoint) { Content = new ByteArrayContent(body) };
        foreach (var (k, v) in headers)
            if (!req.Headers.TryAddWithoutValidation(k, v)) req.Content.Headers.TryAddWithoutValidation(k, v);
        try
        {
            using var res = Http.Send(req);
            return (int)res.StatusCode;
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or IOException)
        {
            return 0;
        }
    }
}

/// <summary>Sends a message to every browser a user turned notifications on in.</summary>
public sealed class Pusher(DbFactory sessions, Settings settings, ILogger? log = null,
                           Func<string, byte[], Dictionary<string, string>, int>? post = null)
{
    readonly ILogger _log = log ?? NullLogger.Instance;
    readonly Func<string, byte[], Dictionary<string, string>, int> _post = post ?? WebPush.Post;

    /// <summary>How many of the user's browsers took it. Browsers that unsubscribed are forgotten.</summary>
    public int SendToUser(int userId, JsonObject message)
    {
        using var s = sessions.Open();
        var keys = WebPush.VapidKeys(s);
        var subs = s.PushSubscriptions.Where(p => p.UserId == userId).OrderBy(p => p.Id).ToList();
        if (keys is not (string priv, string pub) || subs.Count == 0) return 0;
        var payload = Encoding.UTF8.GetBytes(Py.Dumps(message, ensureAscii: false));
        var subject = WebPush.VapidSubject(settings);
        int ok = 0;
        foreach (var sub in subs)
        {
            byte[] body;
            try
            {
                body = WebPush.Encrypt(payload, WebPush.UnB64u(sub.P256dh), WebPush.UnB64u(sub.Auth));
            }
            catch (Exception e) when (e is FormatException or CryptographicException or ArgumentException or IndexOutOfRangeException)
            {
                _log.LogWarning("push subscription {Id} has bad keys: {Error}", sub.Id, e.Message);
                s.PushSubscriptions.Remove(sub);
                continue;
            }
            int status = _post(sub.Endpoint, body, new()
            {
                ["Authorization"] = WebPush.VapidHeader(sub.Endpoint, priv, pub, subject), ["Content-Encoding"] = "aes128gcm",
                ["Content-Type"] = "application/octet-stream", ["TTL"] = WebPush.TtlS.ToString(System.Globalization.CultureInfo.InvariantCulture),
                ["Urgency"] = "high",
            });
            if (status is >= 200 and < 300)
            {
                ok++;
                sub.LastOk = Db.UtcNow();
            }
            else if (status is 404 or 410) s.PushSubscriptions.Remove(sub);    // unsubscribed or expired
            else _log.LogWarning("push to {Host}: HTTP {Status}", new System.Uri(sub.Endpoint).Host, status);
        }
        s.SaveChanges();
        return ok;
    }
}
