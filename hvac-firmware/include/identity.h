#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

// Which kit this board belongs to and which broker it reports to (firmware 0.4.0). Fullscope Office's
// flasher saves them in flash over USB with the "provision" command, so one firmware build serves every
// kit; until a board is provisioned, config.h's SITE_ID and MQTT_HOST / PORT / USER / PASS apply.
// TLS (MQTT_TLS + MQTT_CA_CERT) stays a build setting. Changes take effect after a restart.
void identityBegin();                 // after settingsBegin()
const char* idSite();
const char* idMqttHost();
uint16_t    idMqttPort();
const char* idMqttUser();
const char* idMqttPass();
bool        idProvisioned();          // site and broker come from flash, not config.h

// {"cmd":"provision","site":"fs-7k3qx","mqtt_host":"…","mqtt_port":1883,"mqtt_user":"…","mqtt_pass":"…"}
// saves what is given ({"cmd":"provision","clear":true} goes back to config.h). USB serial only.
void idProvision(JsonDocument& cmd, JsonDocument& reply);
// {"cmd":"identity"}: node, firmware, MAC (the board's serial in Fullscope Office), site and broker.
void idFill(JsonDocument& reply);
