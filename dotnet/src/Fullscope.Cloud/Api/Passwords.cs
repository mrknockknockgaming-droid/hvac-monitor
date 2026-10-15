using System.Buffers.Binary;
using System.Globalization;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;

namespace Fullscope.Cloud.Api;

/// <summary>
/// scrypt password hashes in the format hvaccloud/auth.py writes
/// ("scrypt$N$r$p$salt$digest", base64), so either version can check the other's passwords.
/// </summary>
public static class Passwords
{
    public const int N = 1 << 14, R = 8, P = 1;
    public const int MinLength = 10;

    public static string Hash(string password)
    {
        var salt = RandomNumberGenerator.GetBytes(16);
        var digest = Scrypt(Encoding.UTF8.GetBytes(password), salt, N, R, P, 32);
        return $"scrypt${N}${R}${P}${Convert.ToBase64String(salt)}${Convert.ToBase64String(digest)}";
    }

    /// <summary>Checked when an email has no sign-in, so the answer takes as long as a wrong password.</summary>
    public static readonly string Dummy = Hash(Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(16)));

    /// <summary>True when the password matches the stored hash.</summary>
    public static bool Check(string password, string? stored)
    {
        var parts = (stored ?? "").Split('$');
        if (parts.Length != 6 || parts[0] != "scrypt") return false;
        try
        {
            int n = int.Parse(parts[1], CultureInfo.InvariantCulture), r = int.Parse(parts[2], CultureInfo.InvariantCulture),
                p = int.Parse(parts[3], CultureInfo.InvariantCulture);
            var salt = Convert.FromBase64String(parts[4]);
            var want = Convert.FromBase64String(parts[5]);
            if (n < 2 || (n & (n - 1)) != 0 || n > 1 << 20 || r < 1 || r > 32 || p < 1 || p > 16 || want.Length == 0 || want.Length > 64)
                return false;
            var got = Scrypt(Encoding.UTF8.GetBytes(password), salt, n, r, p, want.Length);
            return CryptographicOperations.FixedTimeEquals(got, want);
        }
        catch (FormatException)
        {
            return false;
        }
        catch (OverflowException)
        {
            return false;
        }
    }

    /// <summary>A reason the password is too weak, or null.</summary>
    public static string? Problem(string password)
    {
        if (password.EnumerateRunes().Count() < MinLength) return $"Use at least {MinLength} characters.";
        if (PyStrip(password) != password) return "The password can't start or end with a space.";
        return null;
    }

    // Python's len() counts code points and str.strip() removes Unicode whitespace.
    static string PyStrip(string s)
    {
        var runes = s.EnumerateRunes().ToList();
        int a = 0, b = runes.Count;
        while (a < b && IsPySpace(runes[a])) a++;
        while (b > a && IsPySpace(runes[b - 1])) b--;
        return string.Concat(runes.Skip(a).Take(b - a).Select(x => x.ToString()));
    }

    static bool IsPySpace(Rune r) => Rune.IsWhiteSpace(r) || r.Value is 0x1C or 0x1D or 0x1E or 0x1F;

    // ---------- scrypt (RFC 7914) ----------

    public static byte[] Scrypt(byte[] password, byte[] salt, int n, int r, int p, int dkLen)
    {
        int blockWords = 32 * r;
        var b = Rfc2898DeriveBytes.Pbkdf2(password, salt, 1, HashAlgorithmName.SHA256, p * 128 * r);
        var x = new uint[blockWords];
        var v = new uint[blockWords * n];
        var t = new uint[blockWords];
        for (int i = 0; i < p; i++)
        {
            var chunk = b.AsSpan(i * 128 * r, 128 * r);
            for (int k = 0; k < blockWords; k++) x[k] = BinaryPrimitives.ReadUInt32LittleEndian(chunk[(k * 4)..]);
            for (int k = 0; k < n; k++)
            {
                Array.Copy(x, 0, v, k * blockWords, blockWords);
                BlockMix(x, t, r);
            }
            for (int k = 0; k < n; k++)
            {
                int j = (int)(x[(2 * r - 1) * 16] & (uint)(n - 1));
                for (int w = 0; w < blockWords; w++) x[w] ^= v[j * blockWords + w];
                BlockMix(x, t, r);
            }
            for (int k = 0; k < blockWords; k++) BinaryPrimitives.WriteUInt32LittleEndian(chunk[(k * 4)..], x[k]);
        }
        return Rfc2898DeriveBytes.Pbkdf2(password, b, 1, HashAlgorithmName.SHA256, dkLen);
    }

    static void BlockMix(uint[] b, uint[] y, int r)
    {
        Span<uint> x = stackalloc uint[16];
        b.AsSpan((2 * r - 1) * 16, 16).CopyTo(x);
        for (int i = 0; i < 2 * r; i++)
        {
            for (int w = 0; w < 16; w++) x[w] ^= b[i * 16 + w];
            Salsa8(x);
            int dest = (i % 2 == 0 ? i / 2 : r + i / 2) * 16;
            x.CopyTo(y.AsSpan(dest, 16));
        }
        Array.Copy(y, b, 32 * r);
    }

    static void Salsa8(Span<uint> b)
    {
        uint x0 = b[0], x1 = b[1], x2 = b[2], x3 = b[3], x4 = b[4], x5 = b[5], x6 = b[6], x7 = b[7],
             x8 = b[8], x9 = b[9], x10 = b[10], x11 = b[11], x12 = b[12], x13 = b[13], x14 = b[14], x15 = b[15];
        for (int i = 0; i < 8; i += 2)
        {
            x4 ^= BitOperations.RotateLeft(x0 + x12, 7); x8 ^= BitOperations.RotateLeft(x4 + x0, 9);
            x12 ^= BitOperations.RotateLeft(x8 + x4, 13); x0 ^= BitOperations.RotateLeft(x12 + x8, 18);
            x9 ^= BitOperations.RotateLeft(x5 + x1, 7); x13 ^= BitOperations.RotateLeft(x9 + x5, 9);
            x1 ^= BitOperations.RotateLeft(x13 + x9, 13); x5 ^= BitOperations.RotateLeft(x1 + x13, 18);
            x14 ^= BitOperations.RotateLeft(x10 + x6, 7); x2 ^= BitOperations.RotateLeft(x14 + x10, 9);
            x6 ^= BitOperations.RotateLeft(x2 + x14, 13); x10 ^= BitOperations.RotateLeft(x6 + x2, 18);
            x3 ^= BitOperations.RotateLeft(x15 + x11, 7); x7 ^= BitOperations.RotateLeft(x3 + x15, 9);
            x11 ^= BitOperations.RotateLeft(x7 + x3, 13); x15 ^= BitOperations.RotateLeft(x11 + x7, 18);
            x1 ^= BitOperations.RotateLeft(x0 + x3, 7); x2 ^= BitOperations.RotateLeft(x1 + x0, 9);
            x3 ^= BitOperations.RotateLeft(x2 + x1, 13); x0 ^= BitOperations.RotateLeft(x3 + x2, 18);
            x6 ^= BitOperations.RotateLeft(x5 + x4, 7); x7 ^= BitOperations.RotateLeft(x6 + x5, 9);
            x4 ^= BitOperations.RotateLeft(x7 + x6, 13); x5 ^= BitOperations.RotateLeft(x4 + x7, 18);
            x11 ^= BitOperations.RotateLeft(x10 + x9, 7); x8 ^= BitOperations.RotateLeft(x11 + x10, 9);
            x9 ^= BitOperations.RotateLeft(x8 + x11, 13); x10 ^= BitOperations.RotateLeft(x9 + x8, 18);
            x12 ^= BitOperations.RotateLeft(x15 + x14, 7); x13 ^= BitOperations.RotateLeft(x12 + x15, 9);
            x14 ^= BitOperations.RotateLeft(x13 + x12, 13); x15 ^= BitOperations.RotateLeft(x14 + x13, 18);
        }
        b[0] += x0; b[1] += x1; b[2] += x2; b[3] += x3; b[4] += x4; b[5] += x5; b[6] += x6; b[7] += x7;
        b[8] += x8; b[9] += x9; b[10] += x10; b[11] += x11; b[12] += x12; b[13] += x13; b[14] += x14; b[15] += x15;
    }
}
