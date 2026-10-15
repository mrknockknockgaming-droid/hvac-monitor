using System.Globalization;
using System.Numerics;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Fullscope.Core;

/// <summary>
/// A JSON number that remembers whether it was an int or a float, because the Python code it
/// replaces does: 76 - 57 is 19 (shown as "19°F") but 76.0 - 57 is 19.0 (shown as "19.0°F").
/// </summary>
public readonly record struct Num(double V, bool IsInt)
{
    public static Num Float(double v) => new(v, false);
    public static Num operator -(Num a, Num b) => new(a.V - b.V, a.IsInt && b.IsInt);
    public override string ToString() => IsInt ? ((long)V).ToString(CultureInfo.InvariantCulture) : Py.Repr(V);
    public JsonNode ToNode() => IsInt ? JsonValue.Create((long)V) : JsonValue.Create(V);
}

/// <summary>Python behaviours the ported code depends on: truthiness, round(), repr() and json.dumps().</summary>
public static class Py
{
    /// <summary>Python's bool(x) for a JSON value.</summary>
    public static bool Truthy(JsonNode? n) => n switch
    {
        null => false,
        JsonObject o => o.Count > 0,
        JsonArray a => a.Count > 0,
        JsonValue v => Classify(v) switch
        {
            Kind.Bool => v.GetValue<bool>(),
            Kind.String => v.GetValue<string>().Length > 0,
            Kind.Int or Kind.Float => NumberOf(v)!.Value.V != 0,
            _ => false,
        },
        _ => false,
    };

    public enum Kind { Null, Bool, Int, Float, String, Other }

    public static Kind Classify(JsonValue v)
    {
        if (v.TryGetValue<JsonElement>(out var e))
        {
            return e.ValueKind switch
            {
                JsonValueKind.True or JsonValueKind.False => Kind.Bool,
                JsonValueKind.String => Kind.String,
                JsonValueKind.Null => Kind.Null,
                JsonValueKind.Number => e.GetRawText().IndexOfAny(['.', 'e', 'E']) >= 0 ? Kind.Float : Kind.Int,
                _ => Kind.Other,
            };
        }
        if (v.TryGetValue<bool>(out _)) return Kind.Bool;
        if (v.TryGetValue<string>(out _)) return Kind.String;
        if (v.TryGetValue<long>(out _) || v.TryGetValue<int>(out _)) return Kind.Int;
        if (v.TryGetValue<double>(out _) || v.TryGetValue<float>(out _)) return Kind.Float;
        return Kind.Other;
    }

    /// <summary>The number in a JSON value (bools are not numbers), or null.</summary>
    public static Num? NumberOf(JsonNode? n)
    {
        if (n is not JsonValue v) return null;
        switch (Classify(v))
        {
            case Kind.Int:
                return new Num(v.TryGetValue<JsonElement>(out var e) ? e.GetDouble()
                               : v.TryGetValue<long>(out var l) ? l : v.GetValue<int>(), true);
            case Kind.Float:
                return new Num(v.TryGetValue<JsonElement>(out var f) ? f.GetDouble()
                               : v.TryGetValue<double>(out var d) ? d : v.GetValue<float>(), false);
            default:
                return null;
        }
    }

    /// <summary>Python round(x, digits) for a float: round half to even on the exact binary value.</summary>
    public static double Round(double x, int digits)
    {
        if (!double.IsFinite(x) || x == 0) return x;
        var q = Scaled(x, digits);
        double result = double.Parse(q.ToString(CultureInfo.InvariantCulture) + "e" + (-digits).ToString(CultureInfo.InvariantCulture),
                                     CultureInfo.InvariantCulture);
        return double.IsNegative(x) ? -result : result;
    }

    /// <summary>Python round(x) with no digits: the nearest integer, halves to even.</summary>
    public static long RoundInt(double x) => (long)Math.Round(x, MidpointRounding.ToEven);

    /// <summary>|x| exactly as digits * 10^exp10.</summary>
    static (BigInteger Digits, int Exp10) Exact(double x)
    {
        long bits = BitConverter.DoubleToInt64Bits(x);
        int exp = (int)((bits >> 52) & 0x7FF);
        long frac = bits & 0xFFFFFFFFFFFFFL;
        BigInteger m = exp == 0 ? frac : frac | (1L << 52);
        int e = (exp == 0 ? 1 : exp) - 1075;           // |x| = m * 2^e
        return e >= 0 ? (m << e, 0) : (m * BigInteger.Pow(5, -e), e);
    }

    /// <summary>round(|x| * 10^digits) half to even, on the exact binary value.</summary>
    static BigInteger Scaled(double x, int digits)
    {
        var (d, e) = Exact(x);
        int shift = e + digits;                         // |x| * 10^digits = d * 10^shift
        if (shift >= 0) return d * BigInteger.Pow(10, shift);
        return DivRoundEven(d, BigInteger.Pow(10, -shift));
    }

    static BigInteger DivRoundEven(BigInteger n, BigInteger den)
    {
        var q = BigInteger.DivRem(n, den, out var r);
        int c = (r * 2).CompareTo(den);
        if (c > 0 || (c == 0 && !q.IsEven)) q += 1;
        return q;
    }

    static int DigitCount(BigInteger n) => n.IsZero ? 1 : n.ToString(CultureInfo.InvariantCulture).Length;

    /// <summary>Python f"{x:.{digits}f}".</summary>
    public static string FormatFixed(double x, int digits)
    {
        if (double.IsNaN(x)) return "nan";
        if (double.IsInfinity(x)) return x > 0 ? "inf" : "-inf";
        string sign = double.IsNegative(x) ? "-" : "";
        string q = (x == 0 ? BigInteger.Zero : Scaled(x, digits)).ToString(CultureInfo.InvariantCulture);
        if (digits == 0) return sign + q;
        q = q.PadLeft(digits + 1, '0');
        return sign + q[..^digits] + "." + q[^digits..];
    }

    /// <summary>Python f"{x:g}" (6 significant digits, trailing zeros removed).</summary>
    public static string FormatG(double x, int precision = 6)
    {
        if (double.IsNaN(x)) return "nan";
        if (double.IsInfinity(x)) return x > 0 ? "inf" : "-inf";
        string sign = double.IsNegative(x) ? "-" : "";
        if (x == 0) return sign + "0";
        var (d, e) = Exact(x);
        int shift = DigitCount(d) - precision;
        var q = shift > 0 ? DivRoundEven(d, BigInteger.Pow(10, shift)) : d;
        int exp = shift > 0 ? e + shift : e;
        int x10 = DigitCount(q) - 1 + exp;              // decimal exponent after rounding
        if (x10 >= -4 && x10 < precision)
        {
            string f = FormatFixed(Math.Abs(x), precision - 1 - x10);
            if (f.Contains('.')) f = f.TrimEnd('0').TrimEnd('.');
            return sign + f;
        }
        string s = q.ToString(CultureInfo.InvariantCulture).TrimEnd('0');
        string mant = s.Length > 1 ? s[0] + "." + s[1..] : s;
        return sign + mant + "e" + (x10 < 0 ? "-" : "+") + Math.Abs(x10).ToString("00", CultureInfo.InvariantCulture);
    }

    /// <summary>Python str(x) of a JSON value (str of a str is itself; containers are repr).</summary>
    public static string Str(JsonNode? n) =>
        n is JsonValue v && Classify(v) == Kind.String ? v.GetValue<string>() : ReprNode(n);

    /// <summary>Python repr(x) of a JSON value as Python would hold it (dicts, lists, True, None...).</summary>
    public static string ReprNode(JsonNode? n)
    {
        switch (n)
        {
            case null:
                return "None";
            case JsonObject o:
                return "{" + string.Join(", ", o.Select(kv => ReprString(kv.Key) + ": " + ReprNode(kv.Value))) + "}";
            case JsonArray a:
                return "[" + string.Join(", ", a.Select(ReprNode)) + "]";
            case JsonValue v:
                return Classify(v) switch
                {
                    Kind.Bool => v.GetValue<bool>() ? "True" : "False",
                    Kind.String => ReprString(v.GetValue<string>()),
                    Kind.Int or Kind.Float => NumberOf(v)!.Value.ToString(),
                    _ => "None",
                };
            default:
                return "None";
        }
    }

    /// <summary>Python repr(str).</summary>
    public static string ReprString(string s)
    {
        char q = s.Contains('\'') && !s.Contains('"') ? '"' : '\'';
        var sb = new StringBuilder().Append(q);
        for (int i = 0; i < s.Length; i++)
        {
            char c = s[i];
            if (c == q || c == '\\') sb.Append('\\').Append(c);
            else if (c == '\n') sb.Append("\\n");
            else if (c == '\r') sb.Append("\\r");
            else if (c == '\t') sb.Append("\\t");
            else if (c < ' ' || c == 0x7f || (c >= 0x80 && c < 0xa0)) sb.Append("\\x").Append(((int)c).ToString("x2", CultureInfo.InvariantCulture));
            else sb.Append(c);
        }
        return sb.Append(q).ToString();
    }

    /// <summary>Python int(s) for a string: surrounding whitespace, a sign, digits (underscores between them).</summary>
    public static bool TryParseInt(string s, out long value)
    {
        value = 0;
        s = s.Trim();
        if (s.Length == 0) return false;
        int i = 0;
        bool neg = false;
        if (s[0] is '+' or '-') { neg = s[0] == '-'; i = 1; }
        if (i >= s.Length) return false;
        bool lastDigit = false;
        long v = 0;
        for (; i < s.Length; i++)
        {
            char c = s[i];
            if (c >= '0' && c <= '9') { v = checked(v * 10 + (c - '0')); lastDigit = true; }
            else if (c == '_' && lastDigit && i + 1 < s.Length && char.IsAsciiDigit(s[i + 1])) lastDigit = false;
            else return false;
        }
        value = neg ? -v : v;
        return true;
    }

    /// <summary>Python int(x) for a JSON value; false where Python would raise.</summary>
    public static bool TryInt(JsonNode? n, out long value)
    {
        value = 0;
        if (n is not JsonValue v) return false;
        switch (Classify(v))
        {
            case Kind.Bool: value = v.GetValue<bool>() ? 1 : 0; return true;
            case Kind.Int: value = (long)NumberOf(v)!.Value.V; return true;
            case Kind.Float:
                double d = NumberOf(v)!.Value.V;
                if (!double.IsFinite(d)) return false;
                value = (long)Math.Truncate(d);
                return true;
            case Kind.String: return TryParseInt(v.GetValue<string>(), out value);
            default: return false;
        }
    }

    /// <summary>Python repr(float).</summary>
    public static string Repr(double x)
    {
        if (double.IsNaN(x)) return "nan";
        if (double.IsInfinity(x)) return x > 0 ? "inf" : "-inf";
        string sign = (x < 0 || (x == 0 && double.IsNegative(x))) ? "-" : "";
        string s = Math.Abs(x).ToString("R", CultureInfo.InvariantCulture);
        int exp10 = 0;
        int ei = s.IndexOfAny(['E', 'e']);
        if (ei >= 0) { exp10 = int.Parse(s[(ei + 1)..], CultureInfo.InvariantCulture); s = s[..ei]; }
        int dot = s.IndexOf('.');
        string intPart = dot >= 0 ? s[..dot] : s, fracPart = dot >= 0 ? s[(dot + 1)..] : "";
        string d = intPart + fracPart;
        int decpt = intPart.Length + exp10;
        int lead = 0;
        while (lead < d.Length && d[lead] == '0') lead++;
        d = d[lead..].TrimEnd('0');
        decpt -= lead;
        if (d.Length == 0) return sign + "0.0";
        if (decpt <= -4 || decpt > 16)
        {
            int e = decpt - 1;
            string mant = d.Length == 1 ? d : d[0] + "." + d[1..];
            return sign + mant + "e" + (e < 0 ? "-" : "+") + Math.Abs(e).ToString("00", CultureInfo.InvariantCulture);
        }
        if (decpt <= 0) return sign + "0." + new string('0', -decpt) + d;
        if (decpt >= d.Length) return sign + d + new string('0', decpt - d.Length) + ".0";
        return sign + d[..decpt] + "." + d[decpt..];
    }

    /// <summary>Python json.dumps(obj, sort_keys, separators=(",", ":") if compact, ensure_ascii).</summary>
    public static string Dumps(JsonNode? n, bool sortKeys = false, bool compact = false, bool ensureAscii = true)
    {
        var sb = new StringBuilder();
        Write(sb, n, sortKeys, compact, ensureAscii);
        return sb.ToString();
    }

    /// <summary>Python a // b for floats.</summary>
    public static double FloorDiv(double a, double b)
    {
        double mod = a % b;                                        // C fmod
        double div = (a - mod) / b;
        if (mod != 0)
        {
            if ((b < 0) != (mod < 0)) div -= 1.0;
        }
        if (div != 0)
        {
            double fl = Math.Floor(div);
            if (div - fl > 0.5) fl += 1.0;
            return fl;
        }
        return Math.CopySign(0.0, a / b);
    }

    static void Write(StringBuilder sb, JsonNode? n, bool sortKeys, bool compact, bool ensureAscii)
    {
        string itemSep = compact ? "," : ", ", keySep = compact ? ":" : ": ";
        switch (n)
        {
            case null:
                sb.Append("null");
                break;
            case JsonObject o:
            {
                sb.Append('{');
                var keys = o.Select(kv => kv.Key);
                if (sortKeys) keys = keys.OrderBy(k => k, StringComparer.Ordinal);
                bool first = true;
                foreach (var k in keys)
                {
                    if (!first) sb.Append(itemSep);
                    first = false;
                    WriteString(sb, k, ensureAscii);
                    sb.Append(keySep);
                    Write(sb, o[k], sortKeys, compact, ensureAscii);
                }
                sb.Append('}');
                break;
            }
            case JsonArray a:
                sb.Append('[');
                for (int i = 0; i < a.Count; i++)
                {
                    if (i > 0) sb.Append(itemSep);
                    Write(sb, a[i], sortKeys, compact, ensureAscii);
                }
                sb.Append(']');
                break;
            case JsonValue v:
                switch (Classify(v))
                {
                    case Kind.Bool: sb.Append(v.GetValue<bool>() ? "true" : "false"); break;
                    case Kind.String: WriteString(sb, v.GetValue<string>(), ensureAscii); break;
                    case Kind.Int or Kind.Float:
                        var num = NumberOf(v)!.Value;
                        sb.Append(num.IsInt ? num.ToString() : JsonFloat(num.V));
                        break;
                    default: sb.Append("null"); break;
                }
                break;
        }
    }

    static string JsonFloat(double x) =>
        double.IsNaN(x) ? "NaN" : double.IsInfinity(x) ? (x > 0 ? "Infinity" : "-Infinity") : Repr(x);

    static void WriteString(StringBuilder sb, string s, bool ensureAscii = true)
    {
        sb.Append('"');
        foreach (char c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                default:
                    if (c < ' ' || (ensureAscii && c > '~')) sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    else sb.Append(c);
                    break;
            }
        }
        sb.Append('"');
    }
}
