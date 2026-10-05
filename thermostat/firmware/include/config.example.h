// Copy this file to include/config.h and fill in your values.
// config.h is ignored by git so your WiFi password never gets committed.
#pragma once

#define WIFI_SSID   "YourNetwork"
#define WIFI_PASS   "YourPassword"

// The MQTT broker: the same one the monitors use (see hvac-firmware/include/config.example.h).
#define MQTT_HOST   "192.168.1.50"
#define MQTT_PORT   1883
#define MQTT_USER   ""
#define MQTT_PASS   ""
#define MQTT_TLS    0
// With MQTT_TLS 1, paste the server's tls/ca.crt here:
// static const char MQTT_CA_CERT[] = R"PEM(
// -----BEGIN CERTIFICATE-----
// ...
// -----END CERTIFICATE-----
// )PEM";

// The system's site id (the same as the monitors')
#define SITE_ID     "home"

// The thermostat as the site's LoRa gateway (radio/README.md). 1 also needs an SX1262 on free
// pins of the display board (check Waveshare's schematic: most GPIOs drive the LCD) and the
// nodes' ids and keys from radio/tools/new_device.py.
#define LORA_GATEWAY 0
// #define LORA_PIN_SCK 0
// #define LORA_PIN_MISO 0
// #define LORA_PIN_MOSI 0
// #define LORA_PIN_NSS 0
// #define LORA_PIN_DIO1 0
// #define LORA_PIN_RST 0
// #define LORA_PIN_BUSY 0
// #define LORA_DEVICES {0x00000000, "00000000000000000000000000000000", "outdoor"}, //                      {0x00000000, "00000000000000000000000000000000", "indoor"},
