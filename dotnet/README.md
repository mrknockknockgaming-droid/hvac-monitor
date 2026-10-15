# Fullscope in C#

The .NET 8 port of the cloud server (`hvac-cloud/`) and the bench dashboard (`hvac-monitor-app/`).
The Python versions stay the ones that run and deploy until the port is finished.

| Project | What it is | Ported from |
|---|---|---|
| `src/Fullscope.Core` | Calculation shared by the server and the dashboard: operating mode, saturation temperatures, superheat, subcooling, delta-T, fault flags, refrigerant tables | `hvaccloud/calc.py`, `hvaccloud/refrigerants.py` (and `Hub.compute` in `hvac-monitor-app/server.py`) |
| `tests/Fullscope.Core.Tests` | xUnit tests | `tests/test_calc.py` |

## Build and test

Install the .NET 8 SDK once (`winget install Microsoft.DotNet.SDK.8`), then from this folder:

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

Python details the port keeps on purpose (`src/Fullscope.Core/Py.cs`):
- whole-number readings stay whole (76 - 57 is `19`, 76.0 - 57 is `19.0`), which shows in flag text;
- `round()` rounds half to even on the exact binary value (`round(2.675, 1)` is 2.7);
- floats print like Python `repr` and JSON is written like `json.dumps`.

The refrigerant tables are embedded from `hvac-cloud/hvaccloud/refrigerant_tables.json` (one copy).
