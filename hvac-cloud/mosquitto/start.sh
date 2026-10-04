#!/bin/sh
# Broker entrypoint for docker-compose.yml: builds the password file from the MQTT_* accounts in
# .env on every start (so a changed password only needs a restart), then runs Mosquitto.
set -e
: "${MQTT_BACKEND_USER:?set MQTT_BACKEND_USER in .env}"
: "${MQTT_BACKEND_PASS:?set MQTT_BACKEND_PASS in .env}"
: "${MQTT_NODES_USER:?set MQTT_NODES_USER in .env}"
: "${MQTT_NODES_PASS:?set MQTT_NODES_PASS in .env}"

pw=/mosquitto/data/passwd
rm -f "$pw"
mosquitto_passwd -c -b "$pw" "$MQTT_BACKEND_USER" "$MQTT_BACKEND_PASS"
mosquitto_passwd -b "$pw" "$MQTT_NODES_USER" "$MQTT_NODES_PASS"

# docker-compose.prod.yml mounts tls/ here: copy so the mosquitto user owns readable copies
if [ -f /mosquitto/certs/server.crt ]; then
  mkdir -p /mosquitto/data/certs
  cp /mosquitto/certs/ca.crt /mosquitto/certs/server.crt /mosquitto/certs/server.key /mosquitto/data/certs/
  chmod 0600 /mosquitto/data/certs/server.key
fi
chown -R mosquitto:mosquitto /mosquitto/data
chmod 0700 "$pw"
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
