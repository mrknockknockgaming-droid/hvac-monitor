using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Data;
using Fullscope.Core;

namespace Fullscope.Cloud.Api;

/// <summary>
/// Two-step sign-in (port of the TOTP part of hvaccloud/auth.py): RFC 6238 codes as authenticator
/// apps make them, plus single-use recovery codes stored as SHA-256s.
/// </summary>
public static class Totp
{
    public const int Step = 30;                 // seconds per code
    public const int Digits = 6;
    public const int Skew = 1;                  // also accept the code just before and after
    public const int RecoveryCount = 8;
    public const string Issuer = "Fullscope";
    const string B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

    public static string NewSecret() => Base32(RandomNumberGenerator.GetBytes(20));

    public static string Base32(byte[] data)
    {
        var sb = new StringBuilder();
        int buffer = 0, bits = 0;
        foreach (var b in data)
        {
            buffer = (buffer << 8) | b;
            bits += 8;
            while (bits >= 5)
            {
                sb.Append(B32[(buffer >> (bits - 5)) & 31]);
                bits -= 5;
            }
        }
        if (bits > 0) sb.Append(B32[(buffer << (5 - bits)) & 31]);
        return sb.ToString();
    }

    static byte[] FromBase32(string s)
    {
        var bytes = new List<byte>();
        int buffer = 0, bits = 0;
        foreach (var c in s.ToUpperInvariant().TrimEnd('='))
        {
            int v = B32.IndexOf(c);
            if (v < 0) throw new FormatException("not base32");
            buffer = (buffer << 5) | v;
            bits += 5;
            if (bits >= 8)
            {
                bytes.Add((byte)(buffer >> (bits - 8)));
                bits -= 8;
            }
        }
        return [.. bytes];
    }

    public static string Code(string secret, long step)
    {
        var msg = new byte[8];
        BinaryPrimitives.WriteInt64BigEndian(msg, step);
        var mac = HMACSHA1.HashData(FromBase32(secret), msg);
        int off = mac[^1] & 0x0F;
        int n = BinaryPrimitives.ReadInt32BigEndian(mac.AsSpan(off, 4)) & 0x7FFFFFFF;
        return (n % 1_000_000).ToString("D6", System.Globalization.CultureInfo.InvariantCulture);
    }

    /// <summary>The time step `code` belongs to when it is right and newer than `lastStep`, else null.</summary>
    public static int? Check(string? secret, string? code, int? lastStep = null, double? now = null)
    {
        code = string.Concat((code ?? "").Where(c => !char.IsWhiteSpace(c)));
        if (string.IsNullOrEmpty(secret) || code.Length != Digits || !code.All(char.IsAsciiDigit)) return null;
        long step = (long)Math.Floor((now ?? Time.Now()) / Step);
        for (long st = step - Skew; st <= step + Skew; st++)
        {
            if (lastStep is int last && st <= last) continue;
            if (CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(Code(secret, st)), Encoding.ASCII.GetBytes(code)))
                return (int)st;
        }
        return null;
    }

    public static string Uri(string secret, string email)
    {
        string q(string x) => System.Uri.EscapeDataString(x);
        return $"otpauth://totp/{q(Issuer)}:{q(email)}?secret={secret}&issuer={q(Issuer)}&digits={Digits}&period={Step}";
    }

    /// <summary>(codes to show once, their hashes to store); each is 10 letters and digits as xxxxx-xxxxx.</summary>
    public static (List<string> Codes, List<string> Hashes) NewRecoveryCodes()
    {
        const string alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
        var codes = new List<string>();
        for (int i = 0; i < RecoveryCount; i++)
        {
            var raw = new string(Enumerable.Range(0, 10).Select(_ => alphabet[RandomNumberGenerator.GetInt32(alphabet.Length)]).ToArray());
            codes.Add(raw[..5] + "-" + raw[5..]);
        }
        return (codes, codes.Select(c => Db.HashKey(Norm(c))).ToList());
    }

    static string Norm(string? code) => string.Concat((code ?? "").ToLowerInvariant().Where(char.IsAsciiLetterOrDigit));

    public static List<string> RecoveryLeft(User user) =>
        (Db.Parse(user.RecoveryCodes) as JsonArray)?.Select(x => (string)x!).ToList() ?? [];

    /// <summary>Check a sign-in code for a user with two-step on: an authenticator code (remembered so it
    /// can't be used twice) or one of their recovery codes (crossed off). The caller saves.</summary>
    public static bool SecondFactor(User user, string? code, double? now = null)
    {
        if (Check(user.TotpSecret, code, user.TotpLastStep, now) is int step)
        {
            user.TotpLastStep = step;
            return true;
        }
        var norm = Norm(code);
        if (norm.Length != 10) return false;
        var left = RecoveryLeft(user);
        if (!left.Remove(Db.HashKey(norm))) return false;
        user.RecoveryCodes = Py.Dumps(new JsonArray([.. left.Select(h => (JsonNode)JsonValue.Create(h)!)]));
        return true;
    }

    public static void TurnOff(User user)
    {
        user.TotpSecret = null;
        user.TotpOn = null;
        user.TotpLastStep = null;
        user.RecoveryCodes = null;
    }
}
