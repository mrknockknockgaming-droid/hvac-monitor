#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

// WiFi + MQTT + OTA. Topics:
//   hvac/<site>/<node>/telemetry   periodic readings (JSON)
//   hvac/<site>/<node>/status      retained; {"online":false} is the last-will
//   hvac/<site>/<node>/cmd         commands in (JSON)
//   hvac/<site>/<node>/reply       command results (JSON)
// Telemetry carries "ts" (UTC epoch seconds) once NTP has set the clock; readings taken while
// the broker can't be reached are kept (up to 10 min) and sent oldest first on reconnect.
// config.h: MQTT_TLS 1 + MQTT_CA_CERT for the server (port 8883); 0 for the PC broker.

typedef void (*CmdHandler)(JsonDocument& cmd, JsonDocument& reply);
typedef void (*StatusFiller)(JsonDocument& doc);

enum NetState { NET_NO_WIFI, NET_NO_MQTT, NET_OK };

void netBegin(CmdHandler onCmd, StatusFiller onStatus);
void netLoop();
bool netPublishTelemetry(const String& json);
void netPublishStatus();
NetState netState();
bool netTimeValid();          // true once NTP has set the clock
double netEpoch();            // UTC seconds, with milliseconds
size_t netBacklog();          // readings waiting for the broker
