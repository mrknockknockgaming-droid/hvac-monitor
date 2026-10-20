# Fullscope in C#

The .NET 10 port of the cloud server (`hvac-cloud/`) and the bench dashboard (`hvac-monitor-app/`).
It is the version the Proxmox LXC runs (`deploy/lxc/deploy.ps1`; `-Python` goes back to the Python
one, same data) and the one `hvac-monitor-app/start.bat` starts when the .NET SDK is installed.

One program, `fullscope`:

| Command | What it does | Python original |
|---|---|---|
| `fullscope ingest` | MQTT ingest worker: snapshots, alerts and email, thermostat display, nightly backup and prune | `python -m hvaccloud.ingest` |
| `fullscope api [--host H] [--port 8000] [--proxy-headers]` | Web API and the web app (`hvac-cloud/web/`, unchanged) | `uvicorn hvaccloud.api:app` |
| `fullscope manage <command>` | `init-db`, `create-account`, `create-key`, `create-system`, `list`, `set-email`, `backup`, `prune`, `create-user`, `set-password`, `test-email` | `manage.py` |
| `fullscope bench [--demo] [--no-browser] [--dir hvac-monitor-app]` | PC bench dashboard on :8080, same `config.json` and `hvac_data.db` | `hvac-monitor-app/server.py` |
| `fullscope demo setup\|run\|reset` | Contractor demo account with seven homes | `demo.py` |
| `fullscope simulate [--site demo] [--thermostat] [--electrical --fault weak_cap]` | Simulated nodes over MQTT | `demo_publisher.py` |

Settings come from the same environment variables and `.env` as the Python cloud.
From this folder: `dotnet run --project src/Fullscope.Cloud -- manage list`.

| Project | What it is | Ported from |
|---|---|---|
| `src/Fullscope.Core` | Calculation shared by the server and the dashboard: operating mode, saturation temperatures, superheat, subcooling, delta-T, fault flags, refrigerant tables | `hvaccloud/calc.py`, `hvaccloud/refrigerants.py` (and `Hub.compute` in `hvac-monitor-app/server.py`) |
| `src/Fullscope.Core` (also) | Equipment notes, electrical module, thermostat settings/schedule/diagnostics and its control loop, Python-compatible JSON | `equipment.py`, `electrical.py`, `thermostat.py` |
| `src/Fullscope.Cloud` | The `fullscope` program (above) and the database layer, which reads and writes the same SQLite/TimescaleDB tables as Python | `hvaccloud/*.py`, `manage.py`, `demo.py`, `demo_publisher.py`, `hvac-monitor-app/server.py` |
| `tests/Fullscope.Core.Tests` | xUnit tests | `tests/test_calc.py`, plus snapshot, thermostat and controller vectors |
| `tests/Fullscope.Cloud.Tests` | xUnit tests, including replays of 4 simulated hours of MQTT traffic, 230 web API requests and 80 minutes of bench dashboard traffic | `test_ingest.py`, `test_maintenance.py`, `test_reading_time.py`, `test_api.py`, `test_auth.py` |

## Build and test

Install the .NET 10 SDK once (`winget install Microsoft.DotNet.SDK.10`), then from this folder:

```powershell
dotnet test
```

## Other differences from Python, on purpose

- The bench dashboard reads `NaN` in a node's message as null (Python passed it on to the browser, whose
  JSON parser rejects it), says so when a command can't reach the broker, and ignores a setting of the
  wrong type instead of storing it.
- `fullscope demo reset` also removes the demo homes' `snapshot_minutes` rows.

## Matching the Python results

`tools/make_calc_vectors.py` runs the Python `calc.compute` over 1,500 random readings and writes
`tests/Fullscope.Core.Tests/vectors/calc_vectors.json`. The C# tests replay every case and compare the
snapshot as text (Python `json.dumps` with sorted keys), so the numbers, rounding, flags and their
wording must be identical. Re-run the script after changing either side:

```powershell
..\hvac-cloud\.venv\Scripts\python.exe tools\make_calc_vectors.py
```

The other generators work the same way:
- `tools/make_snapshot_vectors.py` covers the full snapshot pipeline (equipment, electrical, thermostat) and thermostat settings.
- `tools/make_ingest_replay.py` feeds 10,449 MQTT messages for three systems through the Python ingest and records every
  result, email and publish plus the final database. The C# replay test must match all of it.
- `tools/make_api_replay.py` sends 420 requests (every endpoint, as contractor, homeowner, API key and signed out,
  including errors) to the Python API on the database that replay left, with the clock frozen per request, and
  records status, body, cookie, emails and MQTT publishes (`api_replay.json`, seed in `api_seed.db.gz`). Needs
  `fastapi`, `httpx` and `freezegun` in the venv.
- `tools/make_bench_replay.py` does the same for the bench dashboard's `Hub` (MQTT messages, state, history,
  CSV, settings, commands, the updates pushed to browsers and the final `hvac_data.db`).
- `tools/make_controller_vectors.py` turns the thermostat firmware's 32,400 reference steps
  (`thermostat/firmware/tools/make_vectors.py`) into JSON for the C# control loop.

The API serves the unchanged web app from `hvac-cloud/web/` (`FULLSCOPE_WEB_DIR` to change it) and answers with
the same JSON as `uvicorn hvaccloud.api:app`.

## Faster history (`snapshot_minutes`)

The C# ingest also keeps one row per system and minute (snapshot count, run seconds, cycles, outside high, and a
sum and count per chart series). `fullscope ingest` fills it in for older data in the background when it starts, and the nightly
prune drops rows with the raw data. Differences from Python, on purpose:
- `/history` for ranges of 10 hours or more averages whole minutes, so its bucket is rounded up to a multiple
  of 60 s (a day is 180 s buckets instead of 144 s, 480 points instead of 600). Shorter ranges are read exactly
  as in Python.
- `/summary` adds whole minutes from the rollup; the result is the same up to float rounding.
- Both answers are kept for 10 seconds per system and range, until a newer snapshot arrives.

Python details the port keeps on purpose (`src/Fullscope.Core/Py.cs`):
- whole-number readings stay whole (76 - 57 is `19`, 76.0 - 57 is `19.0`), which shows in flag text;
- `round()` rounds half to even on the exact binary value (`round(2.675, 1)` is 2.7);
- floats print like Python `repr` and JSON is written like `json.dumps`;
- `sum()` of readings follows Python 3.12+ (compensated float sums). The LXC runs Python 3.11, which can
  differ from both in the last digit before rounding.

The refrigerant tables are embedded from `hvac-cloud/hvaccloud/refrigerant_tables.json` (one copy).
