// Copy this file to include/config.h and fill in your values.
// config.h is ignored by git so your WiFi password never gets committed.
#pragma once

#define WIFI_SSID   "YourNetwork"
#define WIFI_PASS   "YourPassword"

// The MQTT broker.
// Bench / PC broker: its IP, port 1883, MQTT_TLS 0.
#define MQTT_HOST   "192.168.1.50"
#define MQTT_PORT   1883
#define MQTT_USER   ""          // leave empty if the broker allows anonymous
#define MQTT_PASS   ""
#define MQTT_TLS    0

// Cloud server (hvac-cloud/DEPLOY.md): MQTT_HOST = the domain (the name in its certificate),
// MQTT_PORT 8883, MQTT_TLS 1, MQTT_USER / MQTT_PASS = MQTT_NODES_USER / MQTT_NODES_PASS from the
// server's .env, and the server's tls/ca.crt pasted below (all of it, including the BEGIN/END
// lines). It has to be a string constant like this, not a #define: a #define ends at the line end.
// static const char MQTT_CA_CERT[] = R"PEM(
// -----BEGIN CERTIFICATE-----
// ...
// -----END CERTIFICATE-----
// )PEM";

// Identifies this installation; lets one server handle many systems later
#define SITE_ID     "home"

// Password for over-the-air firmware updates
#define OTA_PASS    "change-me"
