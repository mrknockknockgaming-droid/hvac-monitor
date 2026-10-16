using System.Globalization;
using System.IO.Compression;
using System.Text.Json.Nodes;

namespace Fullscope.Core.Tests;

/// <summary>The thermostat firmware's 32,400 reference steps (dotnet/tools/make_controller_vectors.py)
/// through ThermostatController: every report must match Python's.</summary>
public class ControllerTests
{
    [Fact]
    public void MatchesPythonController()
    {
        JsonNode runs;
        using (var gz = new GZipStream(File.OpenRead(Path.Combine(AppContext.BaseDirectory, "vectors", "controller_vectors.json.gz")), CompressionMode.Decompress))
        using (var r = new StreamReader(gz))
            runs = PyJson.Loads(r.ReadToEnd())!;
        int checkedSteps = 0;
        foreach (var run in runs.AsArray())
        {
            var configs = run!["configs"]!.AsArray();
            var ctrl = new ThermostatController(configs[0]!["settings"], configs[0]!["tech"]);
            int i = 0;
            foreach (var step in run["steps"]!.AsArray())
            {
                var s = step!.AsArray();
                int cfg = (int)s[4]!;
                if (cfg > 0) ctrl.Configure(configs[cfg]!["settings"], configs[cfg]!["tech"]);
                var local = DateTime.ParseExact((string)s[3]!, ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm:ss.ffffff"], CultureInfo.InvariantCulture);
                var rep = ctrl.Step(Py.NumberOf(s[1]), Py.NumberOf(s[2])?.V, (double)s[0]!, local);
                var got = Py.Dumps(rep, sortKeys: true, compact: true);
                Assert.True(got == (string)s[5]!, $"{run["name"]} step {i}:\n  got      {got}\n  expected {s[5]}");
                i++;
                checkedSteps++;
            }
        }
        Assert.Equal(32400, checkedSteps);
    }
}
