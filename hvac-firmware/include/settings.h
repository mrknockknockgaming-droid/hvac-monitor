#pragma once
#include <Arduino.h>

// Persistent settings in ESP32 flash (survive reboots and firmware updates)
void     settingsBegin();
float    setGetF(const char* key, float def);
void     setPutF(const char* key, float v);
bool     setGetB(const char* key, bool def);
void     setPutB(const char* key, bool v);
uint32_t setGetU(const char* key, uint32_t def);
void     setPutU(const char* key, uint32_t v);
void     setClear(const char* key);

// Linear calibration: value = raw * scale + offset. Keys stored as "<name>.o" / "<name>.s"
struct Cal {
  String name;
  float  offset = 0.0f;
  float  scale  = 1.0f;
  void load();
  void save();
  void reset();
  float apply(float raw) const { return raw * scale + offset; }
};
