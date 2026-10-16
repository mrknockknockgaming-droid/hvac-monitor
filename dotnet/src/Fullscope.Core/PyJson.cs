using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace Fullscope.Core;

/// <summary>json.loads as Python does it: a repeated key keeps the last value, and the bare
/// NaN / Infinity / -Infinity tokens are accepted (read as null here: they are never valid readings).</summary>
public static partial class PyJson
{
    public static JsonNode? Loads(byte[] utf8) => Loads(Encoding.UTF8.GetString(utf8.AsSpan().StartsWith((ReadOnlySpan<byte>)[0xEF, 0xBB, 0xBF]) ? utf8[3..] : utf8));

    public static JsonNode? Loads(string text)
    {
        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(text);
        }
        catch (JsonException) when (NonFinite().IsMatch(text))
        {
            doc = JsonDocument.Parse(NonFinite().Replace(text, m => m.Groups[1].Success ? m.Value : "null"));
        }
        using (doc) return Convert(doc.RootElement);
    }

    // a string literal (kept as it is) or a bare NaN / Infinity / -Infinity token
    [GeneratedRegex("(\"(?:[^\"\\\\]|\\\\.)*\")|-?Infinity|NaN")]
    private static partial Regex NonFinite();

    static JsonNode? Convert(JsonElement e)
    {
        switch (e.ValueKind)
        {
            case JsonValueKind.Object:
                var o = new JsonObject();
                foreach (var p in e.EnumerateObject())
                {
                    o[p.Name] = Convert(p.Value);      // a repeated key: first position, last value
                }
                return o;
            case JsonValueKind.Array:
                var a = new JsonArray();
                foreach (var x in e.EnumerateArray()) a.Add(Convert(x));
                return a;
            case JsonValueKind.Null:
            case JsonValueKind.Undefined:
                return null;
            default:
                return JsonValue.Create(e.Clone());
        }
    }
}
