using System.Globalization;
using System.Text.Json.Nodes;
using Fullscope.Core;

namespace Fullscope.Cloud.Api;

/// <summary>An error answer: {"detail": ...} with this status (FastAPI's HTTPException).</summary>
public sealed class ApiError(int status, JsonNode? detail) : Exception(detail?.ToJsonString())
{
    public int Status { get; } = status;
    public JsonNode? Detail { get; } = detail;

    public ApiError(int status, string message) : this(status, JsonValue.Create(message)) { }

    /// <summary>422 with a list of problems, the shape FastAPI gives request validation errors.</summary>
    public static ApiError Invalid(params JsonObject[] errors) => new(422, new JsonArray(errors.ToArray<JsonNode?>()));

    public static JsonObject Problem(string type, string msg, JsonNode? input, params object[] loc) => new()
    {
        ["type"] = type,
        ["loc"] = new JsonArray(loc.Select(l => l is int i ? (JsonNode?)i : JsonValue.Create(l.ToString())).ToArray()),
        ["msg"] = msg,
        ["input"] = input?.DeepClone(),
    };
}

public enum FieldType { Str, Float, Int, Bool, Date, Dict, ListOfDict }

/// <summary>One field of a request body or query string, with the checks its pydantic model has.</summary>
public sealed record Field(string Name, FieldType Type)
{
    public bool Required { get; init; }
    public bool Nullable { get; init; } = true;
    public JsonNode? Default { get; init; }
    public double? Ge { get; init; }
    public double? Gt { get; init; }
    public double? Le { get; init; }
    public int? MinLength { get; init; }
    public int? MaxLength { get; init; }
    public string[]? Choices { get; init; }
    public Func<DateOnly, string?>? Check { get; init; }      // returns a problem, or null

    public static Field Str(string name, int? maxLength = null, bool required = false, int? minLength = null) =>
        new(name, FieldType.Str) { MaxLength = maxLength, Required = required, Nullable = !required, MinLength = minLength };

    public static Field Float(string name, double? ge = null, double? gt = null, double? le = null, bool required = false) =>
        new(name, FieldType.Float) { Ge = ge, Gt = gt, Le = le, Required = required, Nullable = !required };

    public static Field Int(string name, double? ge = null, double? gt = null, double? le = null, bool nullable = true) =>
        new(name, FieldType.Int) { Ge = ge, Gt = gt, Le = le, Nullable = nullable };

    public static Field Bool(string name, bool? dflt = null, bool required = false) =>
        new(name, FieldType.Bool) { Default = dflt is bool b ? JsonValue.Create(b) : null, Nullable = dflt is null, Required = required };

    public static Field Choice(string name, string[] choices, bool required = false) =>
        new(name, FieldType.Str) { Choices = choices, Required = required, Nullable = !required };
}

/// <summary>A validated request body: the values given (converted like pydantic does) and which were sent.</summary>
public sealed class Model(Dictionary<string, JsonNode?> values, HashSet<string> set)
{
    public IReadOnlySet<string> FieldsSet => set;

    public bool IsSet(string name) => set.Contains(name);

    public JsonNode? Node(string name) => values.GetValueOrDefault(name);

    public string? Str(string name) => Node(name) is JsonValue v ? v.GetValue<string>() : null;

    public double? Float(string name) => Node(name) is JsonValue v ? v.GetValue<double>() : null;

    public int? Int(string name) => Node(name) is JsonValue v ? (int)v.GetValue<long>() : null;

    public bool? Bool(string name) => Node(name) is JsonValue v ? v.GetValue<bool>() : null;

    public DateOnly? Date(string name) => Node(name) is JsonValue v ? DateOnly.ParseExact(v.GetValue<string>(), "yyyy-MM-dd", CultureInfo.InvariantCulture) : null;

    /// <summary>model_dump(exclude_none=True) for the given names, in order.</summary>
    public JsonObject Dump(params string[] names)
    {
        var o = new JsonObject();
        foreach (var n in names)
            if (Node(n) is JsonNode v) o[n] = v.DeepClone();
        return o;
    }
}

/// <summary>pydantic-style checking and conversion (lax mode, as FastAPI uses it).</summary>
public static class Validate
{
    /// <summary>Check a JSON body against a model's fields; 422 when it doesn't fit.</summary>
    public static Model Body(JsonNode? body, bool present, params Field[] fields)
    {
        if (!present) throw ApiError.Invalid(ApiError.Problem("missing", "Field required", null, "body"));
        if (body is not JsonObject obj)
            throw ApiError.Invalid(ApiError.Problem("model_attributes_type", "Input should be a valid dictionary or object to extract fields from", body, "body"));
        var values = new Dictionary<string, JsonNode?>();
        var set = new HashSet<string>();
        var errors = new List<JsonObject>();
        foreach (var f in fields)
        {
            if (!obj.ContainsKey(f.Name))
            {
                if (f.Required) errors.Add(ApiError.Problem("missing", "Field required", obj, "body", f.Name));
                else values[f.Name] = f.Default?.DeepClone();
                continue;
            }
            set.Add(f.Name);
            if (Convert(f, obj[f.Name], "body", out var v) is JsonObject err) errors.Add(err);
            else values[f.Name] = v;
        }
        if (errors.Count > 0) throw ApiError.Invalid([.. errors]);
        return new Model(values, set);
    }

    /// <summary>A query-string number with its default when it isn't given.</summary>
    public static JsonNode Query(string? raw, Field f, JsonNode dflt)
    {
        if (raw is null) return dflt;
        if (Convert(f, JsonValue.Create(raw), "query", out var v) is JsonObject err) throw ApiError.Invalid(err);
        return v!;
    }

    /// <summary>A path parameter that must be an integer.</summary>
    public static int PathInt(string name, string? raw)
    {
        if (raw is not null && Py.TryParseInt(raw, out long n) && n is >= int.MinValue and <= int.MaxValue) return (int)n;
        throw ApiError.Invalid(ApiError.Problem("int_parsing", "Input should be a valid integer, unable to parse string as an integer", JsonValue.Create(raw), "path", name));
    }

    static JsonObject? Convert(Field f, JsonNode? input, string where, out JsonNode? value)
    {
        value = null;
        JsonObject Err(string type, string msg) => ApiError.Problem(type, msg, input, where, f.Name);
        if (input is null)
            return f.Nullable ? null : Err(TypeName(f) + "_type", $"Input should be a valid {Noun(f)}");
        var kind = input is JsonValue jv ? Py.Classify(jv) : Py.Kind.Other;
        switch (f.Type)
        {
            case FieldType.Str:
            {
                if (kind != Py.Kind.String) return Err("string_type", "Input should be a valid string");
                var s = input.GetValue<string>();
                if (f.Choices is not null && !f.Choices.Contains(s))
                    return Err("literal_error", "Input should be " + string.Join(", ", f.Choices.Select(c => $"'{c}'")));
                int len = s.EnumerateRunes().Count();
                if (f.MinLength is int mn && len < mn) return Err("string_too_short", $"String should have at least {mn} character{(mn == 1 ? "" : "s")}");
                if (f.MaxLength is int mx && len > mx) return Err("string_too_long", $"String should have at most {mx} characters");
                value = JsonValue.Create(s);
                return null;
            }
            case FieldType.Float:
            case FieldType.Int:
            {
                double d;
                if (kind is Py.Kind.Int or Py.Kind.Float) d = Py.NumberOf(input)!.Value.V;
                else if (kind == Py.Kind.String && double.TryParse(input.GetValue<string>().Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var p)) d = p;
                else if (kind == Py.Kind.Bool && f.Type == FieldType.Int) d = input.GetValue<bool>() ? 1 : 0;
                else return f.Type == FieldType.Int ? Err("int_type", "Input should be a valid integer") : Err("float_type", "Input should be a valid number");
                if (f.Type == FieldType.Int && (d != Math.Floor(d) || !double.IsFinite(d)))
                    return Err("int_from_float", "Input should be a valid integer, got a number with a fractional part");
                if (f.Gt is double gt && !(d > gt)) return Err("greater_than", $"Input should be greater than {Py.FormatG(gt)}");
                if (f.Ge is double ge && !(d >= ge)) return Err("greater_than_equal", $"Input should be greater than or equal to {Py.FormatG(ge)}");
                if (f.Le is double le && !(d <= le)) return Err("less_than_equal", $"Input should be less than or equal to {Py.FormatG(le)}");
                value = f.Type == FieldType.Int ? JsonValue.Create((long)d) : JsonValue.Create(d);
                return null;
            }
            case FieldType.Bool:
            {
                bool? b = kind switch
                {
                    Py.Kind.Bool => input.GetValue<bool>(),
                    Py.Kind.Int or Py.Kind.Float => Py.NumberOf(input)!.Value.V switch { 0 => false, 1 => true, _ => null },
                    Py.Kind.String => input.GetValue<string>().Trim().ToLowerInvariant() switch
                    {
                        "0" or "off" or "f" or "false" or "n" or "no" => false,
                        "1" or "on" or "t" or "true" or "y" or "yes" => true,
                        _ => null,
                    },
                    _ => null,
                };
                if (b is null) return Err("bool_parsing", "Input should be a valid boolean");
                value = JsonValue.Create(b.Value);
                return null;
            }
            case FieldType.Date:
            {
                if (kind != Py.Kind.String || !DateOnly.TryParseExact(input.GetValue<string>(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var date))
                    return Err("date_from_datetime_parsing", "Input should be a valid date");
                if (f.Check?.Invoke(date) is string problem) return Err("value_error", "Value error, " + problem);
                value = JsonValue.Create(Service.IsoDate(date));
                return null;
            }
            case FieldType.Dict:
                if (input is not JsonObject) return Err("dict_type", "Input should be a valid dictionary");
                value = input.DeepClone();
                return null;
            case FieldType.ListOfDict:
                if (input is not JsonArray arr) return Err("list_type", "Input should be a valid list");
                if (arr.Any(x => x is not JsonObject)) return Err("dict_type", "Input should be a valid dictionary");
                value = input.DeepClone();
                return null;
        }
        return Err("unknown", "unsupported field");
    }

    static string TypeName(Field f) => f.Type switch
    {
        FieldType.Str => "string", FieldType.Float => "float", FieldType.Int => "int", FieldType.Bool => "bool",
        FieldType.Date => "date", FieldType.Dict => "dict", _ => "list",
    };

    static string Noun(Field f) => f.Type switch
    {
        FieldType.Str => "string", FieldType.Float => "number", FieldType.Int => "integer", FieldType.Bool => "boolean",
        FieldType.Date => "date", FieldType.Dict => "dictionary", _ => "list",
    };
}
