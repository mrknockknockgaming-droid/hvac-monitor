using System.Text.Json.Nodes;

namespace Fullscope.Core;

/// <summary>Dict-style access to JSON, the way the Python code reads it (d.get(k), d.get(k) or {}).</summary>
public static class J
{
    /// <summary>o.get(key): null when o isn't an object or has no such key.</summary>
    public static JsonNode? Get(JsonNode? o, string key) =>
        o is JsonObject obj && obj.TryGetPropertyValue(key, out var v) ? v : null;

    public static bool Has(JsonNode? o, string key) => o is JsonObject obj && obj.ContainsKey(key);

    /// <summary>(o.get(key) or {}) as an object (anything else reads as empty).</summary>
    public static JsonObject Obj(JsonNode? o, string key) => Get(o, key) as JsonObject ?? [];

    /// <summary>calc.num(o.get(key)): a finite number that isn't a bool, else null.</summary>
    public static Num? Num(JsonNode? o, string key) => Num(Get(o, key));

    public static Num? Num(JsonNode? n) => Py.NumberOf(n) is Core.Num x && double.IsFinite(x.V) ? x : null;

    public static string? Str(JsonNode? o, string key) =>
        Get(o, key) is JsonValue v && Py.Classify(v) == Py.Kind.String ? v.GetValue<string>() : null;

    public static bool Truthy(JsonNode? o, string key) => Py.Truthy(Get(o, key));

    /// <summary>A deep copy (a node can only have one parent).</summary>
    public static JsonNode? Copy(JsonNode? n) => n?.DeepClone();

    public static JsonNode? Node(Num? v) => v?.ToNode();

    public static JsonNode? Node(double? v) => v is double d ? JsonValue.Create(d) : null;

    /// <summary>snap.get("flags", []) as a list to read.</summary>
    public static JsonArray Flags(JsonObject snap) => Get(snap, "flags") as JsonArray ?? [];

    /// <summary>The snapshot's flag list to append to (snap["flags"] = snap.get("flags", []) + extra).</summary>
    public static JsonArray FlagList(JsonObject snap)
    {
        if (Get(snap, "flags") is JsonArray a) return a;
        var created = new JsonArray();
        snap["flags"] = created;
        return created;
    }

    public static JsonObject Flag(string level, string code, string text, string? node = null)
    {
        var f = new JsonObject { ["level"] = level, ["code"] = code };
        if (node is not null) f["node"] = node;
        f["text"] = text;
        return f;
    }

    /// <summary>', '.join(err) of a node's error list.</summary>
    public static string JoinErrors(JsonNode? err) => err switch
    {
        JsonArray a => string.Join(", ", a.Select(Py.Str)),
        JsonValue v when Py.Classify(v) == Py.Kind.String => string.Join(", ", v.GetValue<string>().ToCharArray()),
        _ => Py.Str(err),
    };
}
