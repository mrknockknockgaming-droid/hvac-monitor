#!/bin/bash
# Installs or updates the Fullscope cloud inside a Debian 12 LXC: Mosquitto on 1883, the ingest
# worker and the web app on 8000 as systemd services, SQLite data in /var/lib/fullscope.
# Run as root from an unpacked bundle (pve-deploy.sh does this for you):
#   bash deploy/lxc/install.sh --bin fullscope-bin.tar.gz [--import-db /path/to/dev.db]   (the C# build)
#   bash deploy/lxc/install.sh --python [--import-db /path/to/dev.db]                     (the Python version)
# Without --bin or --python the version installed now is kept. Both versions use the same
# database and settings, so switching either way keeps the data.
# Safe to re-run: code is updated, data and /etc/fullscope/fullscope.env are kept.
# Public access (README "Going live"): PUBLIC_HOST + a Cloudflare Tunnel token make the web app
# reachable at https://PUBLIC_HOST; MQTT_PUBLIC_HOST adds MQTT over TLS on 8883 for outside boards.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/../.." && pwd)
APP=/opt/fullscope
DATA=/var/lib/fullscope
ETC=/etc/fullscope
ENV_FILE=$ETC/fullscope.env
IMPORT_DB="" BIN="" RUNTIME=""

while [ $# -gt 0 ]; do
  case "$1" in
    --import-db) IMPORT_DB=$2; shift 2 ;;
    --bin) BIN=$2; RUNTIME=dotnet; shift 2 ;;
    --python) RUNTIME=python; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ -d "$SRC/hvac-cloud/hvaccloud" ] || { echo "hvac-cloud not found under $SRC" >&2; exit 1; }
[ -z "$BIN" ] || [ -f "$BIN" ] || { echo "C# build not found: $BIN" >&2; exit 1; }
[ -n "$RUNTIME" ] || RUNTIME=$(cat "$ETC/runtime" 2>/dev/null || echo dotnet)   # re-run: keep the current version
[ "$RUNTIME" = python ] || [ -n "$BIN" ] || [ -x "$APP/bin/fullscope" ] || {
  echo "no C# build: give --bin fullscope-bin.tar.gz (deploy.ps1 makes it) or --python" >&2; exit 2; }

echo "== Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv mosquitto mosquitto-clients sqlite3 rsync ca-certificates libicu72 openssl curl gnupg>/dev/null

id fullscope >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin fullscope
mkdir -p "$APP" "$DATA/backups" "$ETC"

echo "== Copying the app to $APP"
# hvac-cloud/ carries the web app (web/) for both versions and the Python code for --python
rsync -a --delete --exclude .venv --exclude bin --exclude __pycache__ --exclude tests \
  --exclude .env --exclude 'dev.db*' --exclude backups "$SRC/hvac-cloud/" "$APP/"
if [ -n "$BIN" ]; then
  echo "== Installing the C# build in $APP/bin"
  rm -rf "$APP/bin.new"
  mkdir -p "$APP/bin.new"
  tar -xzf "$BIN" -C "$APP/bin.new"
  chmod 755 "$APP/bin.new/fullscope"
  rm -rf "$APP/bin.old"
  [ ! -d "$APP/bin" ] || mv "$APP/bin" "$APP/bin.old"
  mv "$APP/bin.new" "$APP/bin"
  rm -rf "$APP/bin.old"
fi
if [ "$RUNTIME" = python ]; then
  [ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
  "$APP/.venv/bin/pip" install -q --upgrade pip
  "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
fi

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
env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | tr -d '\r' | sed "s/^[\"']//; s/[\"']\$//"; }
env_set() {   # replace KEY=... or add it
  if grep -q "^$1=" "$ENV_FILE"; then sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi
}
# Settings added after the first install: appended once, so older settings files get them too
if ! grep -q "^PUBLIC_HOST=" "$ENV_FILE"; then
  cat >> "$ENV_FILE" <<'EOF'

# Going live (deploy/lxc/README.md). PUBLIC_HOST = the web app's name, e.g. app.example.com, served
# through the Cloudflare Tunnel (token: run fullscope-tunnel-token). Setting it switches APP_URL to
# https://PUBLIC_HOST/app/ and COOKIE_SECURE on, so sign in at that address, not http://<ip>:8000.
PUBLIC_HOST=
COOKIE_SECURE=false
# Boards outside this network: MQTT over TLS on port 8883 at this name (e.g. mqtt.example.com, a
# DNS-only record to your public IP, router port 8883 forwarded here). Needs the broker passwords.
MQTT_PUBLIC_HOST=
EOF
fi
public_host=$(env_get PUBLIC_HOST)
mqtt_public=$(env_get MQTT_PUBLIC_HOST)
if [ -n "$public_host" ]; then
  env_set APP_URL "https://$public_host/app/"
  env_set COOKIE_SECURE true
fi
chown root:fullscope "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "== Configuring Mosquitto"
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
tls_listener=""
if [ -n "$mqtt_public" ]; then
  [ -n "$nodes_pass" ] || {
    echo "MQTT_PUBLIC_HOST is set: the broker needs passwords first (MQTT_NODES_PASS etc. in $ENV_FILE)" >&2; exit 1; }
  tls=$ETC/tls
  mkdir -p "$tls"
  chmod 700 "$tls"
  # The CA is made once and kept (it is in the node firmware); the broker certificate is remade
  # only when the name changes. Renew it (5 years) with: rm $tls/server.name, then re-run install.sh
  if [ "$(cat "$tls/server.name" 2>/dev/null)" != "$mqtt_public" ] || [ ! -f "$tls/server.crt" ]; then
    install -m 755 "$SRC/hvac-cloud/tls/make-certs.sh" "$tls/make-certs.sh"
    sh "$tls/make-certs.sh" "$mqtt_public"
    rm -f "$tls/make-certs.sh"
    echo "$mqtt_public" > "$tls/server.name"
    echo "   $tls/ca.crt goes into the node firmware (MQTT_CA_CERT). Keep a copy of $tls/ca.key off this box."
  fi
  mkdir -p /etc/mosquitto/certs
  install -m 644 "$tls/ca.crt" "$tls/server.crt" /etc/mosquitto/certs/
  install -m 640 -g mosquitto "$tls/server.key" /etc/mosquitto/certs/
  tls_listener="
# Boards outside this network, over the internet (the router forwards 8883 here)
listener 8883 0.0.0.0
cafile /etc/mosquitto/certs/ca.crt
certfile /etc/mosquitto/certs/server.crt
keyfile /etc/mosquitto/certs/server.key
tls_version tlsv1.2"
fi
cat > /etc/mosquitto/conf.d/fullscope.conf <<EOF
# Written by deploy/lxc/install.sh: the nodes on this network connect on port 1883 (never forward it)
per_listener_settings false
$auth
listener 1883 0.0.0.0
$tls_listener
EOF

tunnel_env=$ETC/cloudflared.env
if [ -n "$public_host" ] && ! command -v cloudflared >/dev/null; then
  echo "== Installing cloudflared (Cloudflare Tunnel)"
  mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared bookworm main" \
    > /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq
  apt-get install -y -qq cloudflared >/dev/null
fi
install -m 755 "$SRC/deploy/lxc/fullscope-tunnel-token" /usr/bin/fullscope-tunnel-token

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

echo "== Starting services ($RUNTIME)"
units="$SRC/deploy/lxc"
[ "$RUNTIME" = dotnet ] || units="$SRC/deploy/lxc/python"
install -m 644 "$units/fullscope-ingest.service" "$units/fullscope-api.service" \
  "$SRC/deploy/lxc/fullscope-tunnel.service" /etc/systemd/system/
echo "$RUNTIME" > "$ETC/runtime"
# /usr/bin, not /usr/local/bin: `pct enter` shells have PATH=/sbin:/bin:/usr/sbin:/usr/bin
install -m 755 "$SRC/deploy/lxc/fullscope-manage" /usr/bin/fullscope-manage
rm -f /usr/local/bin/fullscope-manage
systemctl daemon-reload
systemctl enable -q mosquitto fullscope-ingest fullscope-api
systemctl restart mosquitto
systemctl restart fullscope-ingest fullscope-api
tunnel=""
if [ -n "$public_host" ] && [ -s "$tunnel_env" ]; then
  systemctl enable -q fullscope-tunnel
  systemctl restart fullscope-tunnel
  tunnel=fullscope-tunnel
else
  systemctl disable -q --now fullscope-tunnel 2>/dev/null || true
fi

ok=0
for _ in $(seq 1 30); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/app/', timeout=2)" 2>/dev/null; then ok=1; break; fi
  sleep 1
done
systemctl --no-pager --lines=0 status mosquitto fullscope-ingest fullscope-api $tunnel | grep -E '^\S|Active:' || true
if [ "$ok" = 1 ]; then
  echo "== Fullscope is running: http://$(hostname -I | awk '{print $1}'):8000/  (broker on port 1883)"
  if [ -n "$tunnel" ]; then
    echo "   public address: https://$public_host/app/ (sign in there: the sign-in cookie is HTTPS-only now)"
  elif [ -n "$public_host" ]; then
    echo "   PUBLIC_HOST is set but there is no tunnel token yet: run fullscope-tunnel-token"
  fi
  [ -z "$mqtt_public" ] || echo "   outside boards: $mqtt_public port 8883, TLS"
else
  echo "!! The web app did not answer on port 8000. Logs: journalctl -u fullscope-api -n 50" >&2
  exit 1
fi
if ! runuser -u fullscope -- sqlite3 "$DATA/dev.db" "select 1 from accounts limit 1" 2>/dev/null | grep -q 1; then
  echo "   No account yet. Create one with: fullscope-manage create-account \"Your name\" you@example.com"
  echo "   (then create-system / create-user; see hvac-cloud/README.md)"
fi
