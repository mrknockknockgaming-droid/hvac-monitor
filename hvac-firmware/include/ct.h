// Outdoor board rev B: three CT inputs (electrical module version 1), reported as the
// "electrical" node. See src/ct.cpp and electrical/README.md.
#pragma once
#include <ArduinoJson.h>

void ctBegin();
// Call from nodeLoop. y = the Y call input; a rising edge starts the compressor-start capture.
void ctLoop(bool y);
bool ctAnyFitted();
// The electrical node's telemetry (electrical/README.md section 5); version 1 has no voltages.
void ctFillTelemetry(JsonDocument& doc, bool y);
void ctFillStatus(JsonObject cal);
bool ctHandleCmd(JsonDocument& cmd, JsonDocument& reply);   // true if it was a CT command
