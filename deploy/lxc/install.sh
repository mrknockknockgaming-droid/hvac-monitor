#!/bin/bash
# Installs or updates the Fullscope cloud inside a Debian 12 LXC: Mosquitto on 1883, the ingest
# worker and the web app on 8000 as systemd services, SQLite data in /var/lib/fullscope.
# Run as root from an unpacked bundle (pve-deploy.sh does this for you):
#   bash deploy/lxc/install.sh [--import-db /path/to/dev.db]
# Safe to re-run: code and Python packages are updated, data and /etc/fullscope/fullscope.env are kept.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/../.." && pwd)
APP=/opt/fullscope
DATA=/var/lib/fullscope
ETC=/etc/fullscope
ENV_FILE=$ETC/fullscope.env
IMPORT_DB=""

while [ $# -gt 0 ]; do
  case "$1" in
    --import-db) IMPORT_DB=$2; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -d "$SRC/hvac-cloud/hvaccloud" ] || { echo "hvac-cloud not found under $SRC" >&2; exit 1; }

echo "== Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv mosquitto mosquitto-clients sqlite3 rsync ca-certificates >/dev/null

id fullscope >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin fullscope
mkdir -p "$APP" "$DATA/backups" "$ETC"

echo "== Copying the app to $APP"
rsync -a --delete --exclude .venv --exclude __pycache__ --exclude tests \
  --exclude .env --exclude 'dev.db*' --exclude backups "$SRC/hvac-cloud/" "$APP/"
[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
  echo "== Writing $ENV_FILE (edit it for email, then: systemctl restart fullscope-ingest fullscope-api)"
  ip=$(hostname -I | awk '{print $1}')
  cat > "$ENV_FILE" <<EOF
# Fullscope settings (same names as hvac-cloud/.env.example). Restart both services after a change:
#   systemctl restart fullscope-ingest fullscope-api
DATABASE_URL=sqlite:///$DATA/dev.db
BACKUP_DIR=$DATA/backups
BACKUP_KEEP=7
FULL_DETAIL_DAYS=30
KEEP_DAYS=365
MQTT_HOST=localhost
MQTT_PORT=1883

# Broker passwords. Empty MQTT_NODES_PASS = no passwords (as on the PC; fine on a home network).
# To require them: fill in all four, re-run install.sh, and put the nodes account in each
# node's config.h (MQTT_USER / MQTT_PASS).
MQTT_USER=
MQTT_PASS=
MQTT_NODES_USER=nodes
MQTT_NODES_PASS=

# Where links in alert emails point
APP_URL=http://$ip:8000/app/

# Alert email (optional). Gmail: smtp.gmail.com, 587, your address, an App Password.
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
SMTP_FROM=
EOF
fi
chown root:fullscope "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "== Configuring Mosquitto"
env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | tr -d '\r' | sed "s/^[\"']//; s/[\"']\$//"; }
nodes_pass=$(env_get MQTT_NODES_PASS)
if [ -n "$nodes_pass" ]; then
  backend_user=$(env_get MQTT_USER); backend_pass=$(env_get MQTT_PASS); nodes_user=$(env_get MQTT_NODES_USER)
  [ -n "$backend_user" ] && [ -n "$backend_pass" ] && [ -n "$nodes_user" ] || {
    echo "MQTT_NODES_PASS is set, so MQTT_USER, MQTT_PASS and MQTT_NODES_USER must be too ($ENV_FILE)" >&2; exit 1; }
  pw=/etc/mosquitto/fullscope.passwd
  rm -f "$pw"
  mosquitto_passwd -c -b "$pw" "$backend_user" "$backend_pass"
  mosquitto_passwd -b "$pw" "$nodes_user" "$nodes_pass"
  chown root:mosquitto "$pw"
  chmod 640 "$pw"
  auth="allow_anonymous false
password_file $pw"
else
  auth="allow_anonymous true"
fi
cat > /etc/mosquitto/conf.d/fullscope.conf <<EOF
# Written by deploy/lxc/install.sh: the nodes connect here on port 1883
listener 1883 0.0.0.0
$auth
EOF

if [ -n "$IMPORT_DB" ]; then
  echo "== Importing $IMPORT_DB"
  systemctl stop fullscope-ingest fullscope-api 2>/dev/null || true
  if [ -f "$DATA/dev.db" ]; then
    keep="$DATA/backups/before-import-$(date +%Y%m%d-%H%M).db"
    mv "$DATA/dev.db" "$keep"
    echo "   the old database was moved to $keep"
  fi
  rm -f "$DATA/dev.db-wal" "$DATA/dev.db-shm"
  cp "$IMPORT_DB" "$DATA/dev.db"
fi
chown -R fullscope:fullscope "$DATA"

echo "== Starting services"
install -m 644 "$SRC/deploy/lxc/fullscope-ingest.service" "$SRC/deploy/lxc/fullscope-api.service" /etc/systemd/system/
install -m 755 "$SRC/deploy/lxc/fullscope-manage" /usr/local/bin/fullscope-manage
systemctl daemon-reload
systemctl enable -q mosquitto fullscope-ingest fullscope-api
systemctl restart mosquitto
systemctl restart fullscope-ingest fullscope-api

ok=0
for _ in $(seq 1 30); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/app/', timeout=2)" 2>/dev/null; then ok=1; break; fi
  sleep 1
done
systemctl --no-pager --lines=0 status mosquitto fullscope-ingest fullscope-api | grep -E '^\S|Active:' || true
if [ "$ok" = 1 ]; then
  echo "== Fullscope is running: http://$(hostname -I | awk '{print $1}'):8000/  (broker on port 1883)"
else
  echo "!! The web app did not answer on port 8000. Logs: journalctl -u fullscope-api -n 50" >&2
  exit 1
fi
if ! runuser -u fullscope -- sqlite3 "$DATA/dev.db" "select 1 from accounts limit 1" 2>/dev/null | grep -q 1; then
  echo "   No account yet. Create one with: fullscope-manage create-account \"Your name\" you@example.com"
  echo "   (then create-system / create-user; see hvac-cloud/README.md)"
fi
