#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

// Reads H11AA1 opto outputs. The output is pulled LOW while 24 VAC is present,
// but pops HIGH briefly at every zero crossing (120 times a second). Each input
// is sampled at 1 kHz and counted "on" when it is LOW for a good share of each
// 50 ms window; two windows in a row must agree before the state changes.

struct ModeInput {
  const char* name;
  uint8_t pin;
};

void modeInputsBegin(const ModeInput* inputs, uint8_t count);
bool modeGet(uint8_t index);
void modeFillJson(JsonObject obj);
