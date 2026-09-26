// Copy this file to include/config.h and fill in your values.
// config.h is ignored by git so your WiFi password never gets committed.
#pragma once

#define WIFI_SSID   "YourNetwork"
#define WIFI_PASS   "YourPassword"

// IP of the PC running the MQTT broker (Mosquitto) and the web app
#define MQTT_HOST   "192.168.1.50"
#define MQTT_PORT   1883
#define MQTT_USER   ""          // leave empty if the broker allows anonymous
#define MQTT_PASS   ""

// Identifies this installation; lets one server handle many systems later
#define SITE_ID     "home"

// Password for over-the-air firmware updates
#define OTA_PASS    "change-me"
