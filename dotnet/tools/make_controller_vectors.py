"""Generate tests/Fullscope.Core.Tests/vectors/controller_vectors.json.gz: the thermostat firmware's
scripted runs (thermostat/firmware/tools/make_vectors.py, 32,400 steps of hvaccloud/thermostat.py's
Controller) as JSON, so the C# ThermostatController can be checked against the same reference.

Each step has the inputs (time, room, outdoor, local time, a new config if one was sent) and the
Python report as sorted-key JSON. Re-run after changing thermostat.py:

    ..\\hvac-cloud\\.venv\\Scripts\\python.exe tools\\make_controller_vectors.py
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "thermostat", "firmware", "tools"))
import make_vectors  # noqa: E402

OUT = os.path.join(HERE, "..", "tests", "Fullscope.Core.Tests", "vectors", "controller_vectors.json.gz")


def main():
    runs = []
    for name, configs, steps in make_vectors.scenarios():
        runs.append({"name": name, "configs": [json.loads(c) for c in configs], "steps": [
            [now, room, oat, when.isoformat(), cfg, json.dumps(rep, sort_keys=True, separators=(",", ":"))]
            for now, room, oat, when, cfg, rep in steps]})
    with open(OUT, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(json.dumps(runs, separators=(",", ":")).encode())
    print(f"{sum(len(r['steps']) for r in runs)} steps -> {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
