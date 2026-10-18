#pragma once
#include <Arduino.h>

// SETUP button (GPIO14, to GND) -> WiFi setup from a phone over Bluetooth.
// Hold it 3 s, at power-up or while running: the node restarts into setup mode, advertises
// "PROV_hvac-<node>-XXXX" for 10 minutes and the LED blinks in pairs. Use Espressif's
// "ESP BLE Provisioning" app (Android / iOS) with the setup code printed on serial at every boot
// (security 1: the code is the proof of possession). The network it sends is kept in flash and
// used instead of config.h WIFI_SSID / WIFI_PASS; then the node restarts. With nothing stored,
// config.h is used as before.

void setupBleBegin();                              // in setup(): runs setup mode if asked (never returns then)
void setupBleLoop();                               // in loop(): watches the button
bool setupBleWifi(String& ssid, String& pass);     // true + the stored network if setup saved one
