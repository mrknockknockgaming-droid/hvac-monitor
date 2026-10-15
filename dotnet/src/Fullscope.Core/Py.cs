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
        long bits = BitConverter.DoubleToInt64Bits(x);
        bool neg = bits < 0;
        int exp = (int)((bits >> 52) & 0x7FF);
        long frac = bits & 0xFFFFFFFFFFFFFL;
        BigInteger m = exp == 0 ? frac : frac | (1L << 52);
        int e = (exp == 0 ? 1 : exp) - 1075;           // |x| = m * 2^e
        if (e >= 0) return x;                           // an integer already
        BigInteger num = m * BigInteger.Pow(10, digits), den = BigInteger.One << -e;
        var q = BigInteger.DivRem(num, den, out var r);
        int c = (r * 2).CompareTo(den);
        if (c > 0 || (c == 0 && !q.IsEven)) q += 1;
        double result = double.Parse(q.ToString(CultureInfo.InvariantCulture) + "e-" + digits, CultureInfo.InvariantCulture);
        return neg ? -result : result;
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

    /// <summary>Python json.dumps(obj) with its default separators and ensure_ascii.</summary>
    public static string Dumps(JsonNode? n, bool sortKeys = false)
    {
        var sb = new StringBuilder();
        Write(sb, n, sortKeys);
        return sb.ToString();
    }

    static void Write(StringBuilder sb, JsonNode? n, bool sortKeys)
    {
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
                    if (!first) sb.Append(", ");
                    first = false;
                    WriteString(sb, k);
                    sb.Append(": ");
                    Write(sb, o[k], sortKeys);
                }
                sb.Append('}');
                break;
            }
            case JsonArray a:
                sb.Append('[');
                for (int i = 0; i < a.Count; i++)
                {
                    if (i > 0) sb.Append(", ");
                    Write(sb, a[i], sortKeys);
                }
                sb.Append(']');
                break;
            case JsonValue v:
                switch (Classify(v))
                {
                    case Kind.Bool: sb.Append(v.GetValue<bool>() ? "true" : "false"); break;
                    case Kind.String: WriteString(sb, v.GetValue<string>()); break;
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

    static void WriteString(StringBuilder sb, string s)
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
                    if (c < ' ' || c > '~') sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    else sb.Append(c);
                    break;
            }
        }
        sb.Append('"');
    }
}
