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

// LoRa instead of WiFi (radio/README.md): 1 sends everything to the site's LoRa gateway. Make the
// id and key with radio/tools/new_device.py; the same pair goes in the gateway's config.h.
// With LORA_ENABLED 1 the WiFi settings above aren't used.
#define LORA_ENABLED   0
#define LORA_DEVICE_ID 0x00000000
#define LORA_KEY       "00000000000000000000000000000000"
// SX1262 module wiring (defaults: VSPI SCK 18 / MISO 19 / MOSI 23, NSS 5, DIO1 26, RST 27, BUSY 25;
// TCXO 1.8 V as on the Wio-SX1262). Check against the carrier board before the first power-up.
// #define LORA_PIN_NSS 5
// #define LORA_PIN_DIO1 26
// #define LORA_PIN_RST 27
// #define LORA_PIN_BUSY 25
// #define LORA_TCXO_V 1.8

// Password for over-the-air firmware updates
#define OTA_PASS    "change-me"
