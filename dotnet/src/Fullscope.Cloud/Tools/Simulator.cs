using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using Fullscope.Cloud.Mqtt;
using Fullscope.Core;
using Microsoft.Extensions.Logging;

namespace Fullscope.Cloud.Tools;

/// <summary>
/// fullscope simulate: publish simulated outdoor + indoor node messages over MQTT, like the real
/// firmware (demo_publisher.py). Same cooling cycle as the bench dashboard's --demo mode (14 min on,
/// 6 min off), on its own site id so it never mixes with real nodes.
///
/// With --thermostat the site also gets a display thermostat: a simulated house whose room
/// temperature drifts toward the outdoor temperature, cooled or heated when the thermostat's outputs
/// say so. It runs ThermostatController and follows the config the cloud publishes (retained) on
/// hvac/&lt;site&gt;/thermostat/config, so changing the setpoint or schedule in the web app changes what
/// the equipment does.
///
///   fullscope simulate                         (site "demo", broker from MQTT_HOST/MQTT_PORT)
///   fullscope simulate --site demo2 --interval 5
///   fullscope simulate --thermostat
///   fullscope simulate --electrical --fault weak_cap     (adds the electrical module, with a fault)
///
/// The electrical module is a 3.5-ton unit: 14.1 A RLA compressor, 1.4 A FLA fan, 45/5 uF capacitor.
/// Enter those on the system's Equipment page to see the rating-based checks.
/// </summary>
public static class Simulator
{
    public static readonly string[] Faults = ["none", "weak_cap", "pitted_contactor", "fan_dead", "comp_tripped", "low_voltage", "slow_start"];

    static double U(double a, double b) => a + Random.Shared.NextDouble() * (b - a);
    static JsonNode R(double x, int digits = 1) => JsonValue.Create(Py.Round(x, digits));

    /// <summary>Room temperature: loses toward outdoors over ~5 h; the equipment moves it about 0.35 F/min.</summary>
    sealed class House(double room = 77.0)
    {
        const double LossS = 5 * 3600, CoolFS = 0.35 / 60, HeatFS = 0.30 / 60, AuxFS = 0.25 / 60;

        public double Room = room;

        public double Step(double dt, double oat, JsonNode outputs, double warm)
        {
            Room += dt * (oat - Room) / LossS;
            if (J.Truthy(outputs, "Y"))
            {
                bool cooling = J.Truthy(outputs, "OB");          // O energized in cooling (the default config)
                Room += dt * warm * (cooling ? -CoolFS : HeatFS);
            }
            if (J.Truthy(outputs, "W")) Room += dt * AuxFS;
            return Py.Round(Room + U(-0.05, 0.05), 2);
        }
    }

    public static JsonObject Outdoor(double k, bool on, double warm, double oat)
    {
        double pVap = on ? 120 + U(-1, 1) : 300;
        double pLiq = on ? 345 + 15 * warm + U(-2, 2) : 300;
        return new JsonObject
        {
            ["node"] = "outdoor", ["fw"] = "demo", ["rssi"] = -61, ["uptime"] = (long)(k * 60),
            ["mode"] = new JsonObject { ["Y"] = on, ["OB"] = on },
            ["p"] = new JsonObject { ["liq"] = R(pLiq), ["vap"] = R(pVap), ["tsuc"] = null },
            ["t"] = new JsonObject
            {
                ["suc"] = on ? R(52 + U(-0.5, 0.5)) : R(oat - 5), ["liq"] = on ? R(oat + 6 + U(-0.5, 0.5)) : R(oat - 2),
                ["tsuc"] = null, ["dis"] = null,
            },
            ["air"] = new JsonObject { ["t"] = R(oat), ["rh"] = 12.0 }, ["v5"] = 5.02, ["err"] = new JsonArray(),
        };
    }

    public static JsonObject Indoor(double k, bool on, double warm, double room = 76.0, JsonObject? outputs = null)
    {
        outputs ??= new JsonObject { ["Y"] = on, ["W"] = false, ["G"] = on, ["OB"] = on };
        return new JsonObject
        {
            ["node"] = "indoor", ["fw"] = "demo", ["rssi"] = -55, ["uptime"] = (long)(k * 60),
            ["mode"] = outputs.DeepClone(),
            ["air"] = new JsonObject
            {
                ["supply"] = J.Truthy(outputs, "G") ? R(room - 19 * warm + U(-0.3, 0.3)) : R(room + 1),
                ["return"] = R(room + U(-0.3, 0.3)), ["dt"] = null,
            },
            ["err"] = new JsonArray(),
        };
    }

    /// <summary>The electrical module during the same cycle; `fault` injects one problem.</summary>
    public static JsonObject Electrical(double k, bool on, double warm, string fault)
    {
        double line = (fault == "low_voltage" ? 198.5 : 240.0) + U(-1, 1) - (on ? 3 : 0);
        double comp = 0.0;
        if (on && fault != "comp_tripped") comp = 10.8 + 1.5 * warm + U(-0.2, 0.2);
        double fan = !on || fault == "fan_dead" ? 0.0 : 1.1 + U(-0.03, 0.03);
        double hermUf = fault == "weak_cap" ? 38.0 : 45.0;
        double capV = on ? 370.0 + U(-3, 3) : 0.0;
        return new JsonObject
        {
            ["node"] = "electrical", ["fw"] = "demo", ["uptime"] = (long)(k * 60), ["contactor"] = on,
            ["v"] = new JsonObject
            {
                ["line"] = R(line),
                ["contactor"] = on ? (fault == "pitted_contactor" ? R(1.6 + U(-0.1, 0.1), 2) : R(0.04 + U(0, 0.02), 2)) : null,
            },
            ["i"] = new JsonObject { ["comp"] = R(comp, 2), ["fan"] = R(fan, 2) },
            ["cap"] = new JsonObject
            {
                ["herm"] = new JsonObject { ["v"] = R(capV), ["i"] = comp != 0 ? R(hermUf * capV / 2652.6, 3) : 0.0 },
                ["fan"] = new JsonObject { ["v"] = on ? R(capV - 5) : 0.0, ["i"] = fan != 0 ? R(5.0 * (capV - 5) / 2652.6, 3) : 0.0 },
            },
            ["start"] = new JsonObject { ["peak_a"] = fault == "slow_start" ? 71.0 : 58.0, ["ms"] = fault == "slow_start" ? 1450 : 230 },
            ["err"] = new JsonArray(),
        };
    }

    /// <summary>The display thermostat: the controller plus the cloud's retained config.</summary>
    sealed class SimThermostat
    {
        readonly ThermostatController _ctrl = new();
        readonly Lock _lock = new();
        int _ver;
        string _tz;

        public SimThermostat(string tz) => _tz = tz;

        public void OnConfig(string topic, byte[] payload)
        {
            JsonNode? cfg;
            try { cfg = PyJson.Loads(payload); } catch (Exception) { return; }
            if (cfg is not JsonObject o || Py.NumberOf(o["ver"]) is not { IsInt: true } ver) return;
            lock (_lock)
            {
                _ctrl.Configure(o["settings"], o["tech"]);
                _tz = J.Str(o["tech"], "tz") ?? _tz;
                _ver = (int)ver.V;
                var s = _ctrl.Settings;
                Console.WriteLine($"thermostat: config version {_ver} ({J.Str(s, "mode")}, {G(s["heat_sp"])}/{G(s["cool_sp"])} F)");
            }
        }

        static string G(JsonNode? n) => (Py.NumberOf(n)?.V ?? 0).ToString("G", CultureInfo.InvariantCulture);

        public JsonObject Step(double room, double oat, double now, double uptime)
        {
            lock (_lock)
            {
                var rep = _ctrl.Step(Num.Float(room), oat, now, Thermostat.LocalNow(new JsonObject { ["tz"] = _tz }, now));
                rep["node"] = "thermostat";
                rep["fw"] = "demo";
                rep["uptime"] = (long)uptime;
                rep["cfg_ver"] = _ver;
                rep["err"] = new JsonArray();
                rep["ts"] = Py.Round(now, 1);
                rep["room"]!["rh"] = 45.0;
                return rep;
            }
        }
    }

    public static int Run(Settings settings, string[] args, ILoggerFactory logs, CancellationToken stop)
    {
        string site = Arg(args, "--site") ?? "demo";
        double interval = double.Parse(Arg(args, "--interval") ?? "5", CultureInfo.InvariantCulture);
        bool withTstat = args.Contains("--thermostat"), withElec = args.Contains("--electrical");
        string fault = Arg(args, "--fault") ?? "none";
        if (!Faults.Contains(fault))
        {
            Console.Error.WriteLine($"--fault must be one of: {string.Join(", ", Faults)}");
            return 2;
        }
        var log = logs.CreateLogger("simulate");
        var nodes = new List<string> { "outdoor", "indoor" };
        if (withTstat) nodes.Add("thermostat");
        if (withElec) nodes.Add("electrical");
        var tstat = withTstat ? new SimThermostat(J.Str(Thermostat.DefaultTech(), "tz")!) : null;
        var clients = new Dictionary<string, MqttConnection>();
        foreach (var node in nodes)
        {
            var b = $"hvac/{site}/{node}/";
            var c = node == "thermostat"
                ? new MqttConnection(settings, $"hvac-demo-{node}", log, b + "config") { Will = (b + "status", "{\"online\": false}"), OnMessage = tstat!.OnConfig }
                : new MqttConnection(settings, $"hvac-demo-{node}", log) { Will = (b + "status", "{\"online\": false}") };
            c.Start();
            c.Publish(b + "status", Py.Dumps(new JsonObject { ["online"] = true, ["node"] = node, ["fw"] = "demo", ["ip"] = "0.0.0.0", ["rssi"] = -60 }),
                      retain: true, TimeSpan.FromSeconds(10));
            clients[node] = c;
        }
        Console.WriteLine($"publishing site {Py.ReprString(site)} to {settings.MqttHost}:{settings.MqttPort} every {interval.ToString(CultureInfo.InvariantCulture)}s (Ctrl+C to stop)");

        double t0 = Time.Now(), last = t0;
        var house = new House();
        double? onSince = null;
        try
        {
            while (!stop.IsCancellationRequested)
            {
                double now = Time.Now(), k = (now - t0) / 60;
                double oat = 95 + 3 * Math.Sin(k / 30);
                bool on;
                double warm;
                var msgs = new List<(string, JsonObject)>();
                if (tstat is not null)
                {
                    var rep = tstat.Step(house.Room, oat, now, now - t0);
                    clients["thermostat"].Publish($"hvac/{site}/thermostat/telemetry", Py.Dumps(rep), retain: false);
                    var outputs = rep["out"]!.AsObject();
                    on = J.Truthy(outputs, "Y");
                    onSince = on ? onSince ?? now : null;
                    warm = on ? Math.Min(1, (now - onSince!.Value) / 240) : 0;      // coil takes ~4 min to settle
                    house.Step(now - last, oat, outputs, warm);
                    msgs.Add(("outdoor", Outdoor(k, on, warm, oat)));
                    msgs.Add(("indoor", Indoor(k, on, warm, house.Room, outputs)));
                }
                else
                {
                    on = k % 20 < 14;
                    warm = on ? Math.Min(1, k % 20 / 4) : 0;
                    msgs.Add(("outdoor", Outdoor(k, on, warm, oat)));
                    msgs.Add(("indoor", Indoor(k, on, warm)));
                }
                last = now;
                if (withElec) msgs.Insert(0, ("electrical", Electrical(k, on, warm, fault)));    // before outdoor: same snapshot
                foreach (var (node, payload) in msgs)
                    clients[node].Publish($"hvac/{site}/{node}/telemetry", Py.Dumps(payload), retain: false);
                try { Task.Delay(TimeSpan.FromSeconds(interval), stop).Wait(stop); } catch (OperationCanceledException) { break; }
            }
        }
        finally
        {
            foreach (var (node, c) in clients)
            {
                c.Publish($"hvac/{site}/{node}/status", "{\"online\": false}", retain: true, TimeSpan.FromSeconds(2));
                c.DisposeAsync().AsTask().GetAwaiter().GetResult();
            }
        }
        return 0;
    }

    static string? Arg(string[] args, string name)
    {
        int i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}
