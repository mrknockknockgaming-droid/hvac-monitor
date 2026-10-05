#pragma once
#include <ArduinoJson.h>

// LoRa instead of WiFi (radio/README.md), when config.h sets LORA_ENABLED 1. Readings, status and
// command replies go to the site's gateway as encrypted frames (radio/lora/lib/lorafmt); commands
// come back the same way and are handled exactly like MQTT ones. The gateway republishes
// everything on the usual topics, so the cloud can't tell the difference.
#include "config.h"
#ifndef LORA_ENABLED
#define LORA_ENABLED 0
#endif

typedef void (*LoraCmdHandler)(JsonDocument& cmd, JsonDocument& reply);

bool loraBegin(LoraCmdHandler onCmd);     // false if the radio didn't answer
void loraLoop();                          // receives commands; call every loop
bool loraSendReading(JsonDocument& doc);  // the telemetry JSON the WiFi firmware would publish
void loraSendStatus(uint32_t intervalMs);
bool loraOk();
