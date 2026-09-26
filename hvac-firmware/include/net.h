#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

// WiFi + MQTT + OTA. Topics:
//   hvac/<site>/<node>/telemetry   periodic readings (JSON)
//   hvac/<site>/<node>/status      retained; {"online":false} is the last-will
//   hvac/<site>/<node>/cmd         commands in (JSON)
//   hvac/<site>/<node>/reply       command results (JSON)

typedef void (*CmdHandler)(JsonDocument& cmd, JsonDocument& reply);
typedef void (*StatusFiller)(JsonDocument& doc);

enum NetState { NET_NO_WIFI, NET_NO_MQTT, NET_OK };

void netBegin(CmdHandler onCmd, StatusFiller onStatus);
void netLoop();
bool netPublishTelemetry(const String& json);
void netPublishStatus();
NetState netState();
