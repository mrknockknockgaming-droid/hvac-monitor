# Going live: deployment checklist

Runs the cloud on a rented Linux server with `docker-compose.yml` + `docker-compose.prod.yml`:
HTTPS through Caddy (certificate from Let's Encrypt, renewed automatically), MQTT over TLS on
port 8883 for the nodes, TimescaleDB, and a daily database backup. Nothing else is reachable
from the internet.

Tested on the dev PC on 2026-10-03 with `DOMAIN=localhost`: HTTP redirects to HTTPS, security
headers, `Secure` + `HttpOnly` session cookie, invite links on the domain, the API unreachable
except through Caddy; MQTT over TLS with the private CA (wrong password, no account, an
untrusted certificate, plain MQTT and the old port all refused); a backup restored into an
empty TimescaleDB with 0 errors and matching row counts.

## 1. What to buy

- **Server:** 1 vCPU, 2 GB RAM, 25 GB+ disk, Ubuntu 24.04 LTS (Hetzner, DigitalOcean, Vultr,
  Linode: about $6–12/month). Enough for hundreds of systems at 5-second readings.
- **Domain:** any registrar (about $10–15/year). Point an **A record** (and AAAA if the
  server has IPv6) for the name you'll use, e.g. `app.example.com`, at the server's address.

## 2. Prepare the server (as root, once)

```bash
adduser hvac && usermod -aG sudo hvac            # then log in as hvac with an SSH key
sudo apt update && sudo apt -y upgrade && sudo apt -y install unattended-upgrades git
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker hvac   # log out and in
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443 && sudo ufw allow 8883/tcp
sudo ufw enable
```

## 3. Get the code and settings

```bash
git clone git@github.com:mrknockknockgaming-droid/hvac-monitor.git   # read-only deploy key on the repo
cd hvac-monitor/hvac-cloud
cp .env.example .env && chmod 600 .env
openssl rand -base64 24        # run three times: POSTGRES_PASSWORD, MQTT_BACKEND_PASS, MQTT_NODES_PASS
nano .env                      # DOMAIN, the three passwords, SMTP_* for email
```

## 4. Broker certificate for the nodes

```bash
docker run --rm -v "$PWD/tls:/tls" alpine:3 sh /tls/make-certs.sh "$(grep ^DOMAIN= .env | cut -d= -f2)"
```

This makes a private certificate authority (`tls/ca.crt`, `tls/ca.key`) and the broker's
certificate (`tls/server.crt`, valid 5 years; run again to renew). `ca.crt` goes into the node
firmware. **Copy `tls/ca.key` somewhere safe off the server**: without it a renewed certificate
can't be signed and every node would need new firmware.

## 5. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps      # all six running
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs caddy | grep -i certificate
```

Open `https://<DOMAIN>/app/`. Data lives in the Docker volumes `dbdata`, `mqttdata` and
`caddydata`; `docker compose down` keeps them (never add `-v` on the server).

## 6. First accounts

```bash
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'
dc exec api python manage.py create-account "Your company" you@example.com
dc exec api python manage.py create-system 1 "Home" home --atm-psia 14.0
dc exec -it api python manage.py create-user you@example.com --name "Your name" --account 1   # asks for a password
```

Then invite homeowners and colleagues from the web app; links now use the real domain.

## 7. Point the nodes at it

Needs firmware with MQTT over TLS (roadmap, firmware v0.2): `MQTT_HOST` = the domain,
`MQTT_PORT 8883`, `MQTT_USER` = `MQTT_NODES_USER`, `MQTT_PASS` = `MQTT_NODES_PASS`, and
`tls/ca.crt` as the trusted CA. Each node's `SITE_ID` must match its system's site id.

## 8. Backups

The `backup` service writes `backups-pg/hvac-<date>.sql.gz` every 24 hours and keeps
`BACKUP_KEEP_DAYS` (14). Copy that folder and `tls/ca.key` off the server regularly (for
example a nightly `rclone` or `scp` to another machine).

Restore (tested) into an empty database:

```bash
dc stop ingest api
dc exec db psql -U hvac -d hvac -c "SELECT timescaledb_pre_restore();"
gunzip -c backups-pg/hvac-YYYYMMDD-HHMM.sql.gz | dc exec -T db psql -U hvac -d hvac
dc exec db psql -U hvac -d hvac -c "SELECT timescaledb_post_restore();"
dc start ingest api
```

(The database must be empty first: on a fresh server that is the case; otherwise recreate
the `dbdata` volume.)

## 9. Updating

```bash
git pull && dc up -d --build
```

Database tables and new columns are added automatically at start-up.

## Not covered yet

- Moving data from the PC's `dev.db` (SQLite) to the server: start fresh on the server, or
  ask for a one-off migration script.
- Monitoring that the server itself is up (an uptime service pinging `https://<DOMAIN>/health`).
