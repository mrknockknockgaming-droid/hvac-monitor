// Copy to include/config.h and fill in. config.h is ignored by git (it holds the WiFi password
// and the radio keys).
#pragma once

#define WIFI_SSID   "YourNetwork"
#define WIFI_PASS   "YourPassword"

// The MQTT broker: the same one the monitors use (see hvac-firmware/include/config.example.h)
#define MQTT_HOST   "192.168.1.50"
#define MQTT_PORT   1883
#define MQTT_USER   ""
#define MQTT_PASS   ""
#define MQTT_TLS    0
// static const char MQTT_CA_CERT[] = R"PEM( ... )PEM";      // with MQTT_TLS 1

#define SITE_ID     "home"

// The LoRa nodes this gateway serves: device id, 16-byte key (32 hex digits), node name.
// Make each pair with radio/tools/new_device.py; the same id and key go in that node's config.h.
#define LORA_DEVICES \
    {0x00000000, "00000000000000000000000000000000", "outdoor"}, \
    {0x00000000, "00000000000000000000000000000000", "indoor"},
