#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

#define FW_VERSION "0.1.0"

#if defined(NODE_OUTDOOR)
  #define NODE_NAME "outdoor"
#elif defined(NODE_INDOOR)
  #define NODE_NAME "indoor"
#else
  #error "Build with -DNODE_OUTDOOR or -DNODE_INDOOR (see platformio.ini)"
#endif

// Pins shared by both nodes (see schematic sheet, block B)
#define PIN_SDA   21
#define PIN_SCL   22
#define PIN_LED    2

// Implemented by node_outdoor.cpp or node_indoor.cpp
void nodeSetup();
void nodeLoop();
void nodeFillTelemetry(JsonDocument& doc);
void nodeFillStatus(JsonDocument& doc);
bool nodeHandleCmd(JsonDocument& cmd, JsonDocument& reply);   // true if handled

inline float round1(float v) { return roundf(v * 10.0f) / 10.0f; }
inline float round2(float v) { return roundf(v * 100.0f) / 100.0f; }
