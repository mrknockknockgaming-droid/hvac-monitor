#!/bin/bash
# Run on the Proxmox host as root. Creates the Fullscope LXC the first time (Debian 12,
# unprivileged, starts at boot), then copies a bundle into it and runs install.sh. Later runs
# with the same --ctid update the code and keep the data.
#
#   bash pve-deploy.sh BUNDLE.tar.gz --bin BIN.tar.gz --ctid 120 --ip 192.168.1.50/24 --gw 192.168.1.1 [--db dev.db]
#   bash pve-deploy.sh BUNDLE.tar.gz --bin BIN.tar.gz --ctid 120    # update an existing container
#
# BUNDLE is `git archive` of hvac-cloud/ and deploy/lxc/, BIN the C# build (deploy.ps1 on the PC
# makes both). --python instead of --bin runs the Python version.
# Options: --storage local-lvm (container disk), --tstorage local (templates), --bridge vmbr0,
#          --hostname fullscope, --disk 8 (GB), --memory 1024 (MB), --cores 1, --db FILE (import a
#          dev.db; the container's current database is kept in its backups folder first).
set -euo pipefail

BUNDLE=${1:-}
[ -n "$BUNDLE" ] && [ -f "$BUNDLE" ] || { sed -n '2,15p' "$0"; exit 2; }
shift
CTID=120 IP="" GW="" DB="" BIN="" PYTHON="" STORAGE=local-lvm TSTORAGE=local BRIDGE=vmbr0 HOST=fullscope DISK=8 MEM=1024 CORES=1
while [ $# -gt 0 ]; do
  [ "$1" = --python ] && { PYTHON=1; shift; continue; }
  case "$1" in
    --bin) BIN=$2 ;;
    --ctid) CTID=$2 ;;     --ip) IP=$2 ;;          --gw) GW=$2 ;;          --db) DB=$2 ;;
    --storage) STORAGE=$2 ;; --tstorage) TSTORAGE=$2 ;; --bridge) BRIDGE=$2 ;; --hostname) HOST=$2 ;;
    --disk) DISK=$2 ;;     --memory) MEM=$2 ;;     --cores) CORES=$2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift 2
done
[ -z "$DB" ] || [ -f "$DB" ] || { echo "database file not found: $DB" >&2; exit 1; }
[ -z "$BIN" ] || [ -f "$BIN" ] || { echo "C# build not found: $BIN" >&2; exit 1; }
command -v pct >/dev/null || { echo "run this on the Proxmox host (pct not found)" >&2; exit 1; }

if pct status "$CTID" >/dev/null 2>&1; then
  echo "== Container $CTID exists: updating it"
  [ "$(pct config "$CTID" | sed -n 's/^hostname: //p')" = "$HOST" ] || {
    echo "container $CTID is not named '$HOST'; refusing to install into it (use --ctid / --hostname)" >&2; exit 1; }
  pct status "$CTID" | grep -q running || pct start "$CTID"
else
  [ -n "$IP" ] || { echo "first install: give --ip (e.g. 192.168.1.50/24) and --gw" >&2; exit 2; }
  if [ "$IP" = dhcp ]; then net="name=eth0,bridge=$BRIDGE,ip=dhcp"
  else [ -n "$GW" ] || { echo "--gw is needed with a fixed --ip" >&2; exit 2; }
       net="name=eth0,bridge=$BRIDGE,ip=$IP,gw=$GW"; fi

  echo "== Finding the Debian 12 template"
  pveam update >/dev/null || true
  tmpl=$(pveam available --section system | awk '{print $2}' | grep '^debian-12-standard_' | sort -V | tail -1)
  [ -n "$tmpl" ] || { echo "no debian-12-standard template offered by pveam" >&2; exit 1; }
  pveam list "$TSTORAGE" | grep -q "$tmpl" || pveam download "$TSTORAGE" "$tmpl"

  echo "== Creating container $CTID ($HOST, $IP)"
  pct create "$CTID" "$TSTORAGE:vztmpl/$tmpl" \
    --hostname "$HOST" --cores "$CORES" --memory "$MEM" --swap 512 \
    --rootfs "$STORAGE:$DISK" --net0 "$net" \
    --unprivileged 1 --features nesting=1 --onboot 1 --timezone host \
    --description "Fullscope cloud: MQTT broker :1883, web app :8000"
  pct start "$CTID"
fi

echo "== Waiting for the container's network"
for i in $(seq 1 60); do
  pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  [ "$i" = 60 ] && { echo "container $CTID can't reach deb.debian.org (check --ip / --gw / DNS)" >&2; exit 1; }
  sleep 2
done

echo "== Copying the bundle in"
pct push "$CTID" "$BUNDLE" /root/fullscope.tar.gz
pct exec "$CTID" -- bash -c 'rm -rf /root/fullscope-src && mkdir -p /root/fullscope-src && tar -xzf /root/fullscope.tar.gz -C /root/fullscope-src'
args=()
if [ -n "$DB" ]; then
  pct push "$CTID" "$DB" /root/fullscope-import.db
  args+=(--import-db /root/fullscope-import.db)
fi
if [ -n "$BIN" ]; then
  pct push "$CTID" "$BIN" /root/fullscope-bin.tar.gz
  args+=(--bin /root/fullscope-bin.tar.gz)
fi
[ -z "$PYTHON" ] || args+=(--python)
pct exec "$CTID" -- bash /root/fullscope-src/deploy/lxc/install.sh "${args[@]}"
pct exec "$CTID" -- rm -f /root/fullscope-import.db /root/fullscope-bin.tar.gz
