"""Generate tests/Fullscope.Cloud.Tests/vectors/bench_replay.json.gz: about 80 minutes of MQTT traffic
for the PC bench dashboard (hvac-monitor-app/server.py Hub), with the answers it gives along the way.

The clock is frozen and moved per message. Each step is either an MQTT message (topic + payload, as
the broker delivers it) or a call: state, history, export_csv, set_config or send_cmd. The file
records every answer, a hash of the updates pushed to browsers (sorted-key JSON, one per line) and, at the end, the hvac_data.db rows and
config.json. The C# test (BenchReplayTests) feeds the same steps to BenchHub and must match.

Run from the dotnet folder (needs freezegun):

    ..\\hvac-cloud\\.venv\\Scripts\\python.exe tools\\make_bench_replay.py
"""
import gzip
import hashlib
import json
import os
import queue
import random
import sys
import tempfile
import time

os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "..", "hvac-monitor-app")
sys.path.insert(0, APP)

from freezegun import freeze_time  # noqa: E402

import server  # noqa: E402

OUT = os.path.join(HERE, "..", "tests", "Fullscope.Cloud.Tests", "vectors", "bench_replay.json.gz")
T0 = 1784138400.0          # 2026-07-15 18:00 UTC
SITE = "home"


def no_nan(text):
    """C# reads NaN in a payload as null: Python kept NaN, which a browser's JSON.parse rejects."""
    return json.loads(text, parse_constant=lambda _: None)


def script(rng):
    """(t, kind, value) in time order; kind is a topic or a call name."""
    steps = []
    t = T0
    config = {"atm_psia": 14, "keep_days": 0.03}             # ~43 min: the second prune drops the start
    steps.append((t, "set_config", {"atm_psia": 14, "bogus": 1}))
    phase_ends = [(5, "idle"), (38, "cooling"), (44, "idle"), (50, "fan"), (82, "cooling")]
    minute, outage = 0.0, (20.0, 21.5)                        # outdoor silent for 90 s mid-cycle
    while minute < 82:
        mode = next(m for end, m in phase_ends if minute < end)
        on = mode == "cooling"
        warm = min(1.0, (minute % 40) / 4) if on else 0.0
        oat = 95 + 3 * (minute / 80)
        if not (outage[0] <= minute < outage[1]):
            od = {"node": "outdoor", "fw": "0.2.0", "rssi": -61, "uptime": int(minute * 60),
                  "mode": {"Y": on, "OB": on},
                  "p": {"liq": round(345 + 15 * warm + rng.uniform(-2, 2), 1) if on else 300,
                        "vap": round(118 + rng.uniform(-1, 1), 1) if on else 300, "tsuc": None},
                  "t": {"suc": round(52 + rng.uniform(-0.5, 0.5), 1) if on else round(oat - 5, 1),
                        "liq": round(oat + 6 + rng.uniform(-0.5, 0.5), 1) if on else 93,
                        "tsuc": None, "dis": None},
                  "air": {"t": round(oat, 1), "rh": 12}, "v5": 5.02, "err": []}
            if 30 <= minute < 31:
                od["err"] = ["t_liq open"]
                od["t"]["liq"] = None
            if 12 <= minute < 12.2:
                od["p"]["vap"] = float("nan")
            steps.append((t, f"hvac/{SITE}/outdoor/telemetry", json.dumps(od)))
        ind = {"node": "indoor", "fw": "0.2.0", "rssi": -55, "uptime": int(minute * 60),
               "mode": {"Y": on, "W": False, "G": on or mode == "fan", "OB": on},
               "air": {"supply": round(76 - 19 * warm + rng.uniform(-0.3, 0.3), 1) if on else 76,
                       "return": round(76 + rng.uniform(-0.3, 0.3), 1), "dt": None}, "err": []}
        if 60 <= minute < 60.1:
            ind["ts"] = t - 300                                # a backlog reading from the outage buffer
        if 61 <= minute < 61.1:
            ind["ts"] = t - 2 * 86400                          # implausible: stored at arrival time
        steps.append((t + 0.4, f"hvac/{SITE}/indoor/telemetry", json.dumps(ind)))
        if abs(minute - 3) < 1e-9:
            steps.append((t + 1, f"hvac/{SITE}/outdoor/status", json.dumps({"fw": "0.2.0", "cal": {"p_liq": [0.5, 4.5]}})))
            steps.append((t + 1.2, f"hvac/{SITE}/electrical/telemetry", json.dumps({"amps": 12})))
            steps.append((t + 1.3, f"hvac/{SITE}/outdoor/telemetry", "not json"))
            steps.append((t + 1.4, f"hvac/{SITE}/outdoor/x/y", "{}"))
            steps.append((t + 1.5, "send_cmd", ["outdoor", {"cmd": "status"}]))
            steps.append((t + 1.6, f"hvac/{SITE}/outdoor/reply", json.dumps({"ok": True, "cmd": "status"})))
        if minute in (2.0, 25.0, 40.0, 70.0):
            steps.append((t + 2, "state", None))
        if minute in (25.0, 81.0):
            for m in (0.5, 15, 60, 120):
                steps.append((t + 2.1, "history", m))
            steps.append((t + 2.2, "export_csv", 30))
        if minute == 47.0:
            steps.append((t + 2, "set_config", {"refrigerant": "R-32", "heat_pump": False, "ob_energized": "heat"}))
        if minute == 55.0:
            steps.append((t + 2, "set_config", {"refrigerant": "R-410A", "heat_pump": True, "ob_energized": "cool", "atm_psia": 13.95}))
        t += 3
        minute += 0.05
        minute = round(minute, 2)
    return config, steps


def main():
    rng = random.Random(7)
    config, steps = script(rng)
    work = tempfile.mkdtemp()
    server.CONFIG_PATH = os.path.join(work, "config.json")
    server.DB_PATH = os.path.join(work, "hvac_data.db")
    cfg = dict(server.DEFAULTS)
    cfg.update(config)
    with open(server.CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
    start_config = open(server.CONFIG_PATH).read()

    sent = []

    class FakeMqtt:
        def publish(self, topic, payload, qos=0):
            sent.append({"topic": topic, "payload": payload})

    out = []
    with freeze_time(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(steps[0][0]))) as clock:
        hub = server.Hub(server.load_config())
        hub.mqtt = FakeMqtt()
        q = queue.Queue()
        hub.clients.append(q)

        def on_message(topic, payload):                       # start_mqtt's on_message
            parts = topic.split("/")
            if len(parts) != 4:
                return
            node, kind = parts[2], parts[3]
            if node not in ("outdoor", "indoor"):
                return
            try:
                data = json.loads(payload)
            except Exception:
                return
            if kind == "telemetry":
                hub.on_telemetry(node, data)
            elif kind == "status":
                hub.on_status(node, data)
            elif kind == "reply":
                hub.on_reply(node, data)

        for t, kind, value in steps:
            clock.move_to(time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)) + ".%06d" % round((t % 1) * 1e6))
            assert abs(time.time() - t) < 1e-6
            step = {"t": t, "kind": kind}
            result = None
            n0 = len(sent)
            if "/" in kind:
                step["payload"] = value
                on_message(kind, value.encode())
            elif kind == "state":
                with hub.lock:
                    result = hub.state()
            elif kind == "history":
                step["arg"] = value
                result = hub.history(value)
            elif kind == "export_csv":
                step["arg"] = value
                result = hub.export_csv(value)
            elif kind == "set_config":
                step["arg"] = value
                hub.set_config(value)
            elif kind == "send_cmd":
                step["arg"] = value
                result = list(hub.send_cmd(*value))
            step["result"] = result if isinstance(result, str) else json.dumps(result, sort_keys=True)
            pushed = []
            while not q.empty():
                pushed.append(json.dumps(no_nan(q.get()), sort_keys=True))
            step["pushed"] = hashlib.sha256("\n".join(pushed).encode()).hexdigest()[:16] if pushed else ""
            step["pushed_n"] = len(pushed)
            step["sent"] = sent[n0:]
            out.append(step)
        hub.db.commit()
        tel = hub.db.execute("SELECT ts, node, data FROM telemetry ORDER BY rowid").fetchall()
        snaps = hub.db.execute("SELECT ts, data FROM snapshots ORDER BY rowid").fetchall()
        hub.db.close()

    with open(OUT, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(json.dumps({"start_config": start_config, "steps": out,
                   "final": {"config": open(server.CONFIG_PATH).read(),
                             "telemetry": [[ts, n, json.dumps(no_nan(d))] for ts, n, d in tel],
                             "snapshots": [[ts, d] for ts, d in snaps]}}, indent=0).encode())
    print(f"{len(out)} steps, {len(tel)} telemetry and {len(snaps)} snapshot rows kept -> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
