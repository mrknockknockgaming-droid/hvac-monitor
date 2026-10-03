# HVAC Monitor cloud

Stores node data for many systems and serves it over a REST API. It is the multi-customer
version of `hvac-monitor-app` (the PC dashboard), using the same MQTT topics and the same
superheat / subcooling / fault-flag math.

| Part | What it does |
|---|---|
| `hvaccloud/ingest.py` | Subscribes to `hvac/<site>/<node>/{telemetry,status,reply}`, stores raw telemetry, derived snapshots and node status, pairs command replies |
| `hvaccloud/alerts.py` | Turns fault flags that last 5 min into alerts, adds "no data" alerts, emails the account |
| `hvaccloud/api.py` | FastAPI: systems, live state, history, CSV export, settings, commands to nodes |
| `hvaccloud/calc.py` | Port of the dashboard's `Hub.compute` / `Hub.flags`; `tests/test_calc.py` checks they match |
| `hvaccloud/db.py` | SQLAlchemy models; SQLite in development, TimescaleDB hypertables in production |
| `web/` | Fullscope web app (homeowner + technician views), served by the API at `/app/` |
| `hvaccloud/auth.py` | Passwords (scrypt), sign-in sessions, who is asking (contractor / homeowner / API key) |
| `hvaccloud/service.py` | Maintenance reminders (air filter, tune-up) and the service contractor |
| `hvaccloud/maintenance.py` | Nightly backup of `dev.db` and thinning of old data |
| `manage.py` | Create accounts, API keys and systems; send a test alert email; backup / prune by hand |
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

## Sign-in and roles

- **Contractor** users belong to an account and see all of its systems, including the
  technician pages. **Homeowner** users see only the systems they are members of: the
  homeowner page, read-only except "I changed it" for the air filter.
- Users sign in with email and password (scrypt hashes). The session lives in an HttpOnly
  cookie for `SESSION_DAYS` (30, renewed while in use); set `COOKIE_SECURE=true` once the site
  runs on HTTPS. Requests that change something with the cookie must carry `X-Requested-With`
  (the web app adds it), which stops other sites from forging them. Eight wrong passwords for
  one email lock it for 15 minutes. Users change their password on the Account page.
- API keys still work (header `X-API-Key`) and act as a contractor of their account.

```powershell
.venv\Scripts\python.exe manage.py create-user you@example.com --name "Tyler" --account 1
.venv\Scripts\python.exe manage.py create-user owner@example.com --role homeowner --system 1
.venv\Scripts\python.exe manage.py set-password you@example.com
```
Both commands ask for the password at a hidden prompt (typed twice, at least 10 characters).

## Web app

Plain HTML/CSS/JS in `web/`, no build step; styles are copied from the Fullscope mockups in
`design/`. It polls the API every 5 s.

- **Home** (`#/home/<id>`): plain-language status, inside/outside/vent temperatures, what we
  noticed (each fault flag explained for a homeowner), maintenance, recent alerts, system
  health, last 24 hours.
  - **Maintenance:** air filter (due after 90 days or 500 hours of blower run time, whichever
    comes first; the homeowner presses "I changed it") and tune-up (every 182 days), with the
    service contractor and a **Request service** button. It opens the homeowner's email with
    the current issues filled in, or calls if only a phone number is set. Run time is added up
    per day by the ingest worker (`runtime_days`).
- **Monitor** (`#/monitor/<id>`): superheat, subcooling, delta-T, condensing over ambient,
  compression ratio; live trend (15 min to 7 days); operating state; sensor health from the
  nodes' own error codes; refrigerant and air-side tables; current diagnostics; alert log;
  CSV export.
- **Sensors & calibration** (`#/setup/<id>`): for commissioning. Per channel: on/off, live
  reading, raw sensor volts or ohms, and the calibration stored on the node. Buttons send the
  firmware's commands (zero, span, ice bath / reference temperature, reset, range, B-value,
  measured 3.3 V rail, swap indoor probes, rescan, reporting interval, reboot). The command log
  shows each node's reply; after a change the page asks the node for its settings again. Buttons
  are disabled while a node is offline, and zero / reset / reboot ask for confirmation.
  Also the service contractor (name, phone, email) and the maintenance schedule (intervals and
  last-done dates).

Both views list the last 7 days of alerts (homeowner: "Recent alerts"; technician: "Alert log").

## Alerts and email

- A fault flag opens an alert once it has lasted `ALERT_HOLD_SECONDS` (5 min), and the alert
  clears once the flag has been gone that long, so a value hovering at a limit gives one alert.
- If a system that has reported goes silent for `ALERT_NO_DATA_SECONDS` (10 min), the ingest
  worker's once-a-minute sweep opens a "no data" alert; it clears on the next reading.
- Raised alerts are emailed at most once per fault type per `ALERT_EMAIL_COOLDOWN_HOURS` (6 h);
  a "back to normal" email follows an emailed alert unless someone acknowledged it.
- **Recipients** (Sensors & calibration page, "Alert email"): the account owner (default) and/or
  the service contractor's email. The owner gets plain language with what to do; the
  contractor gets the technical reading, the site id and a link to the technician view.
- **Acknowledge** (Alert log) marks an open alert as being handled; the homeowner sees "Being
  handled". **Mute** stops emails for one kind of alert on a system for 1, 7 or 30 days; alerts
  are still recorded and shown.
- Email is off until SMTP is set up. Copy `.env.example` to `.env` (never committed) and fill
  in the `SMTP_*` lines. For Gmail, use `smtp.gmail.com`, port 587 and an App Password
  (Google account > Security > 2-Step Verification > App passwords). Then check it with
  `.venv\Scripts\python.exe manage.py test-email 1` and restart the ingest worker. Set where emails
  go with `manage.py set-email 1 <address>`.

Values the hardware does not measure yet (indoor humidity, static pressure, capacity) are
shown as "Not installed", never estimated. The API key is kept in the browser's local
storage; real per-user sign-in comes with going live (roadmap phase 8).

## Backups and data cleanup (SQLite)

Each node reports every 5 s, so `dev.db` grows by tens of MB a day. The ingest worker looks
after it once a night (3 AM, or on its next start if the PC was off then):

1. **Backup:** a consistent copy to `backups/dev-YYYYMMDD-HHMM.db` (safe while running),
   keeping the newest `BACKUP_KEEP` (7). `backups/` is not committed.
2. **Prune:** snapshots older than `FULL_DETAIL_DAYS` (30) are averaged into one per minute,
   raw telemetry older than that is deleted (each node's newest message is kept), and anything
   older than `KEEP_DAYS` (365) is deleted. Charts, history, summaries and CSV export work the
   same on thinned data, at 1-minute resolution.

Run either by hand with `manage.py backup` and `manage.py prune`. SQLite reuses the freed
space instead of shrinking the file. To restore: close the two cloud windows, delete
`dev.db-wal` and `dev.db-shm`, copy a backup over `dev.db`, then run `start-cloud.bat`.
On PostgreSQL, TimescaleDB's retention policy expires old data; back up with `pg_dump`.

## API

All endpoints except `/health` and `/api/auth/login` need a signed-in session cookie or the
header `X-API-Key: <key from manage.py create-key>`. Endpoints marked *tech* answer 403 to homeowners.

| Method | Path | |
|---|---|---|
| POST | `/api/auth/login` | `{"email","password"}`; sets the session cookie |
| POST | `/api/auth/logout` | Ends the session |
| GET | `/api/auth/me` | Role, user and account of whoever is asking |
| POST | `/api/auth/password` | `{"current","new"}` |
| GET | `/api/systems` | Systems this user can see |
| GET | `/api/systems/{id}` | Settings + nodes (online, age, fw, ip, rssi) |
| PATCH | `/api/systems/{id}` | `refrigerant`, `heat_pump`, `ob_energized` (`cool`/`heat`), `atm_psia` |
| GET | `/api/systems/{id}/latest` | Newest derived snapshot (mode, pressures, sat temps, SH, SC, delta-T, flags) + each node's raw telemetry |
| GET | `/api/systems/{id}/summary?hours=24` | Compressor runtime, cycles, average on-time, outside high, inside average |
| GET | `/api/systems/{id}/history?minutes=60` | Averaged series, at most ~600 points |
| GET | `/api/systems/{id}/export.csv?minutes=1440` | Snapshots as CSV (UTC times) |
| GET | `/api/systems/{id}/alerts?days=7` | Raised alerts open during the last `days`, newest first |
| POST | `/api/systems/{id}/alerts/{alert_id}/ack` | `{"ack": true}` (false undoes) |
| GET / PUT | `/api/systems/{id}/alert-settings` | `email_owner`, `email_contractor`; GET also lists active mutes |
| PUT | `/api/systems/{id}/alert-mutes/{code}` | `{"hours": 24}`; 0 unmutes |
| GET | `/api/systems/{id}/service` | Contractor + maintenance items (status, days and run hours since, next due) |
| PUT | `/api/systems/{id}/service/contractor` | `{"name","phone","email"}` |
| PATCH | `/api/systems/{id}/service/items/{filter\|tuneup}` | `interval_days`, `interval_run_hours` (null = days only), `last_done` |
| POST | `/api/systems/{id}/service/items/{kind}/done` | `{"date":"2026-10-03"}` (defaults to today) |
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

- Web app: invites (a contractor adds a homeowner by email) and the contractor fleet page,
  service requests sent through the cloud (now the homeowner's email),
  sending the homeowner and contractor emails from separate per-user accounts.
- Per-device MQTT accounts and topic ACLs (every node shares one account for now).
- HTTPS / MQTT TLS (Phase 8, going live).
