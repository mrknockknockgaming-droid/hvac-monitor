# Fullscope cloud in a Proxmox LXC

This is the main way to run the Fullscope cloud from 2026-10-13. One small Debian 12
container on the Proxmox server runs everything the PC used to run, and keeps running when the
PC sleeps:

| Part | In the container |
|---|---|
| MQTT broker | Mosquitto (Debian package), port **1883**, the port the nodes already use |
| Ingest worker | `fullscope-ingest.service` (`fullscope ingest`, the C# build) |
| Web app + API | `fullscope-api.service` (`fullscope api` on port **8000**) |
| Database | SQLite at `/var/lib/fullscope/dev.db`; nightly backups in `/var/lib/fullscope/backups/` (keeps 7) |
| Settings | `/etc/fullscope/fullscope.env` (same names as `hvac-cloud/.env.example`) |
| Code | `/opt/fullscope/bin/fullscope` (the C# server from `dotnet/`, self-contained) and `/opt/fullscope` (a copy of `hvac-cloud/`: the web app, plus the Python version for `-Python`) |

All three services start at boot and restart if they stop, and the container itself is set to
start when Proxmox boots. Docker (`hvac-cloud/docker-compose*.yml`) is still there for the
public server later (roadmap step 8), but it isn't needed here.

## Files

| File | Runs on | What it does |
|---|---|---|
| `deploy.ps1` | your PC | Packs the committed `hvac-cloud/` + `deploy/lxc/` with `git archive`, builds the committed `dotnet/` server for Linux, copies both (and, with `-WithData`, a copy of your `dev.db`) to the Proxmox host, runs `pve-deploy.sh` there |
| `pve-deploy.sh` | Proxmox host | Creates the container the first time (Debian 12 template, unprivileged, nesting, start at boot, host time zone), copies the bundle in, runs `install.sh` |
| `install.sh` | in the container | Installs packages and the app, writes the settings file once, sets up Mosquitto and the services. Safe to re-run; data and settings are kept |
| `fullscope-*.service` | in the container | The two systemd services (`python/` has the Python versions, used with `-Python`) |
| `fullscope-manage` | in the container | `fullscope manage` with the container's settings, e.g. `fullscope-manage list` |

## The C# build

`deploy.ps1` builds the server from `dotnet/` on the PC, so the PC needs the .NET 10 SDK once:
`winget install Microsoft.DotNet.SDK.10`, then open a new PowerShell. The container needs no
.NET install (the build is self-contained). The C# and Python versions read and write the same
database and settings, so you can switch either way at any time and keep all data:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.170 -Python   # back to Python
powershell -ExecutionPolicy Bypass -File .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.170           # C# again
```

The first start of the C# ingest worker fills a per-minute summary table from the existing
history, alongside live data (about 25 seconds per month of readings on a desktop PC, longer on
the container's one core). Chart ranges of 10 hours or more read that table, so their older part
is blank until it finishes.

## First install

You need: the Proxmox host's address, a free address for the container on your network (outside
the router's DHCP range, or reserved), and the router's address. The examples use Proxmox at
192.168.1.10, the container at 192.168.1.50 and the router at 192.168.1.1, so change those.

1. Close the "HVAC cloud ingest" window on the PC so the database copy is final.
2. In PowerShell, from the repo folder (after `git pull` on `main`):

   ```powershell
   cd C:\Users\mrkno\hvac-monitor
   powershell -ExecutionPolicy Bypass -File .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.10 -Ip 192.168.1.50/24 -Gateway 192.168.1.1 -WithData
   ```

   It asks for the Proxmox root password twice (once to copy, once to install). It finishes with
   `Fullscope is running: http://192.168.1.50:8000/`. Sign in the same way as on the PC: your
   accounts, systems, history and API key came across with the database.

   Container ID 120 and disk storage `local-lvm` are the defaults; add `-CtId 130` or
   `-Storage local-zfs` if yours differ.
3. Point the nodes at the container: in `hvac-firmware/include/config.h` set
   `MQTT_HOST "192.168.1.50"` (port stays 1883), then reflash both nodes (USB or the `*_ota`
   environments). Until they are reflashed they keep sending to the PC.
4. Point the PC dashboard at it too, if you still use it: in `hvac-monitor-app/config.json`
   set `"mqtt_host": "192.168.1.50"`.
5. Stop using `start-hvac.bat` for the broker and cloud. The PC is no longer needed for data.

## Updating after code changes

Commit (or pull) the change on the PC, then:

```powershell
cd C:\Users\mrkno\hvac-monitor
powershell -ExecutionPolicy Bypass -File .\deploy\lxc\deploy.ps1 -Proxmox 192.168.1.10
```

Only committed code is deployed; the script warns about uncommitted changes. The database and
`/etc/fullscope/fullscope.env` are kept. Adding `-WithData` again replaces the container's
database with the PC's (the old one is kept in `backups/before-import-*.db`), so leave it off.

## Everyday commands (Proxmox shell: `pct enter 120`)

```bash
systemctl status fullscope-ingest fullscope-api mosquitto
journalctl -u fullscope-ingest -f          # live ingest log
mosquitto_sub -v -t 'hvac/#'               # watch the nodes' messages
fullscope-manage list                      # accounts and systems
fullscope-manage create-user ...           # see hvac-cloud/README.md
fullscope-manage test-email 1
nano /etc/fullscope/fullscope.env          # then: systemctl restart fullscope-ingest fullscope-api
```

Email: fill in the `SMTP_*` lines in `/etc/fullscope/fullscope.env` and restart both services.

Broker passwords: off by default, like the PC broker. To turn them on, fill in `MQTT_USER`,
`MQTT_PASS`, `MQTT_NODES_USER` and `MQTT_NODES_PASS` in the settings file, run
`bash /root/fullscope-src/deploy/lxc/install.sh`, and put the nodes account in each node's
`config.h` (`MQTT_USER` / `MQTT_PASS`) before reflashing.

Restore a backup: `systemctl stop fullscope-ingest fullscope-api`, copy the backup over
`/var/lib/fullscope/dev.db`, delete `dev.db-wal` and `dev.db-shm` next to it,
`chown fullscope:fullscope /var/lib/fullscope/dev.db`, start both services. Proxmox backups
(`vzdump`) of container 120 also capture everything.
