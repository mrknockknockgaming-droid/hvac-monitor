using System.Text.Json.Nodes;

namespace Fullscope.Core.Tests;

/// <summary>Cases recorded from the Python reference by the scripts in dotnet/tools.</summary>
static class Vectors
{
    static readonly Dictionary<string, JsonArray> Cache = [];

    public static JsonArray Load(string name)
    {
        lock (Cache)
        {
            if (!Cache.TryGetValue(name, out var a))
                Cache[name] = a = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "vectors", name)))!.AsArray();
            return a;
        }
    }

    public static IEnumerable<object[]> Indexes(string name) => Enumerable.Range(0, Load(name).Count).Select(i => new object[] { i });
}
