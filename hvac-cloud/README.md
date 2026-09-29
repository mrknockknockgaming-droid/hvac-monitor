# HVAC Monitor cloud

Stores node data for many systems and serves it over a REST API. It is the multi-customer
version of `hvac-monitor-app` (the PC dashboard), using the same MQTT topics and the same
superheat / subcooling / fault-flag math.

| Part | What it does |
|---|---|
| `hvaccloud/ingest.py` | Subscribes to `hvac/<site>/<node>/{telemetry,status,reply}`, stores raw telemetry, derived snapshots and node status, pairs command replies |
| `hvaccloud/api.py` | FastAPI: systems, live state, history, CSV export, settings, commands to nodes |
| `hvaccloud/calc.py` | Port of the dashboard's `Hub.compute` / `Hub.flags`; `tests/test_calc.py` checks they match |
| `hvaccloud/db.py` | SQLAlchemy models; SQLite in development, TimescaleDB hypertables in production |
| `web/` | Fullscope web app (homeowner + technician views), served by the API at `/app/` |
| `manage.py` | Create accounts, API keys and systems |
| `demo_publisher.py` | Simulated outdoor + indoor nodes over MQTT (site `demo`) |

A system's `site_id` is the node's `SITE_ID` in `hvac-firmware/include/config.h`, so the
firmware needs no changes. Messages from sites with no system are ignored.

## Development (no Docker)

```powershell
cd C:\Users\mrkno\hvac-monitor\hvac-cloud
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

With a Mosquitto broker on localhost:1883 (`start-hvac.bat` starts one), in separate windows:

```powershell
.venv\Scripts\python.exe manage.py create-account "Tyler" you@example.com
.venv\Scripts\python.exe manage.py create-key 1
.venv\Scripts\python.exe manage.py create-system 1 "Home" home --atm-psia 14.0
.venv\Scripts\python.exe -m hvaccloud.ingest
.venv\Scripts\python.exe demo_publisher.py          # optional, needs: manage.py create-system 1 "Demo" demo
.venv\Scripts\python.exe -m uvicorn hvaccloud.api:app --port 8000
```

Data goes to `dev.db` (SQLite). Web app: http://localhost:8000/ (sign in with the API key).
Interactive API docs: http://localhost:8000/docs (click Authorize and paste the API key).

## Web app

Plain HTML/CSS/JS in `web/`, no build step; styles are copied from the Fullscope mockups in
`design/`. It polls the API every 5 s.

- **Home** (`#/home/<id>`): plain-language status, inside/outside/vent temperatures, what we
  noticed (each fault flag explained for a homeowner), system health, last 24 hours.
- **Monitor** (`#/monitor/<id>`): superheat, subcooling, delta-T, condensing over ambient,
  compression ratio; live trend (15 min to 7 days); operating state; sensor health from the
  nodes' own error codes; refrigerant and air-side tables; current diagnostics; CSV export.

Values the hardware does not measure yet (indoor humidity, static pressure, capacity) are
shown as "Not installed", never estimated. The API key is kept in the browser's local
storage; real per-user sign-in comes with going live (roadmap phase 8).

## API

All endpoints except `/health` need the header `X-API-Key: <key from manage.py create-key>`.

| Method | Path | |
|---|---|---|
| GET | `/api/systems` | Systems on this account |
| GET | `/api/systems/{id}` | Settings + nodes (online, age, fw, ip, rssi) |
| PATCH | `/api/systems/{id}` | `refrigerant`, `heat_pump`, `ob_energized` (`cool`/`heat`), `atm_psia` |
| GET | `/api/systems/{id}/latest` | Newest derived snapshot (mode, pressures, sat temps, SH, SC, delta-T, flags) + each node's raw telemetry |
| GET | `/api/systems/{id}/summary?hours=24` | Compressor runtime, cycles, average on-time, outside high, inside average |
| GET | `/api/systems/{id}/history?minutes=60` | Averaged series, at most ~600 points |
| GET | `/api/systems/{id}/export.csv?minutes=1440` | Snapshots as CSV (UTC times) |
| POST | `/api/systems/{id}/commands` | `{"node":"outdoor","cmd":{"cmd":"cal_zero","ch":"p_liq"}}` |
| GET | `/api/systems/{id}/commands` | Recent commands with the node's reply |

Commands are the firmware's (see the top of `hvac-firmware/src/node_outdoor.cpp`), e.g.
`{"cmd":"fitted","ch":"t_tsuc","on":true}` to enable the J9 thermistor channel.

## Docker (not yet run: Docker Desktop is not installed on the dev PC)

1. Copy `.env.example` to `.env` and set the passwords.
2. Create the broker accounts (one for the backend, one the nodes use):
   ```powershell
   docker compose run --rm mqtt mosquitto_passwd -c -b /mosquitto/config/passwd backend <MQTT_BACKEND_PASS>
   docker compose run --rm mqtt mosquitto_passwd -b /mosquitto/config/passwd nodes <node password>
   ```
   (`mosquitto/passwd` must exist first: create an empty file.)
3. `docker compose up -d --build`, then
   `docker compose run --rm api python manage.py create-account ...` etc. as above.
4. Point a node at it in `config.h`: `MQTT_HOST` = this machine, `MQTT_PORT 1884`,
   `MQTT_USER "nodes"`, `MQTT_PASS` = the node password. Reflash.

## Not done yet

- Web app: per-user sign-in, maintenance reminders, contractor details, service requests,
  a diagnostics history (only current flags are shown), calibration from the browser.
- Alerts and email when a flag appears.
- Per-device MQTT accounts and topic ACLs (every node shares one account for now).
- HTTPS / MQTT TLS (Phase 8, going live).
