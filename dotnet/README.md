# Fullscope in C#

The .NET 10 port of the cloud server (`hvac-cloud/`) and the bench dashboard (`hvac-monitor-app/`).
The Python versions stay the ones that run and deploy until the port is finished.

| Project | What it is | Ported from |
|---|---|---|
| `src/Fullscope.Core` | Calculation shared by the server and the dashboard: operating mode, saturation temperatures, superheat, subcooling, delta-T, fault flags, refrigerant tables | `hvaccloud/calc.py`, `hvaccloud/refrigerants.py` (and `Hub.compute` in `hvac-monitor-app/server.py`) |
| `src/Fullscope.Core` (also) | Equipment notes, electrical module, thermostat settings/schedule/diagnostics, Python-compatible JSON | `equipment.py`, `electrical.py`, `thermostat.py` (not the controller) |
| `src/Fullscope.Cloud` | The `fullscope` program. So far: `fullscope ingest` (MQTT ingest, alerts and emails, thermostat display, nightly backup and prune) and the database layer, which reads and writes the same SQLite/TimescaleDB tables as Python | `ingest.py`, `alerts.py`, `display.py`, `maintenance.py`, `models.py`, `db.py`, `settings.py` |
| `tests/Fullscope.Core.Tests` | xUnit tests | `tests/test_calc.py`, plus snapshot and thermostat vectors |
| `tests/Fullscope.Cloud.Tests` | xUnit tests, including a replay of 4 simulated hours of MQTT traffic | `test_ingest.py`, `test_maintenance.py`, `test_reading_time.py` |

## Build and test

Install the .NET 10 SDK once (`winget install Microsoft.DotNet.SDK.10`), then from this folder:

```powershell
dotnet test
```

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

Python details the port keeps on purpose (`src/Fullscope.Core/Py.cs`):
- whole-number readings stay whole (76 - 57 is `19`, 76.0 - 57 is `19.0`), which shows in flag text;
- `round()` rounds half to even on the exact binary value (`round(2.675, 1)` is 2.7);
- floats print like Python `repr` and JSON is written like `json.dumps`.

The refrigerant tables are embedded from `hvac-cloud/hvaccloud/refrigerant_tables.json` (one copy).
