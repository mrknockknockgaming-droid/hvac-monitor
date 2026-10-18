// CT inputs on the outdoor carrier rev B (J11-J13): SCT-013 clamps on 3.5 mm jacks, tip through
// 1k + 10 nF to an ADC1 pin, sleeve on CT_BIAS (~1.34 V). Read with the ESP32's own ADC (the
// ADS1115s are far too slow for 60 Hz RMS) at 11 dB, where it is linear from ~0.15 to ~2.45 V.
#ifdef NODE_OUTDOOR

#include "ct.h"
#include "ct_math.h"
#include "settings.h"
#include "common.h"
#include <Arduino.h>

namespace {

struct CtCh {
  const char* key;      // "ct_comp" etc.
  const char* field;    // key in the electrical telemetry
  uint8_t pin;
  float defAPerV;       // amps per volt RMS at the ADC pin for the CT the BOM names
  bool fitted;
  float aPerV;
  float vrms = NAN;     // last RMS at the pin, volts
  float amps = NAN;
};

// CT1 compressor common: SCT-013-000 (100 A : 50 mA) into 18 R = 0.9 V at 100 A.
// CT2 condenser fan: SCT-013-005 (5 A : 1 V).  CT3 HERM lead: SCT-013-010 (10 A : 1 V).
CtCh CH[] = {
  {"ct_comp", "comp", 36, 100.0f / 0.9f, false, 0},
  {"ct_fan",  "fan",  39, 5.0f,          false, 0},
  {"ct_herm", "herm", 32, 10.0f,         false, 0},
};
const int NCH = sizeof(CH) / sizeof(CH[0]);

const uint32_t SAMPLE_US = 333;       // 3 kHz per channel, interleaved
const uint32_t WINDOW_MS = 200;       // 12 cycles at 60 Hz
const uint32_t READ_MS = 2000;
const float NOISE_A = 0.15f;          // below this reads as 0 (ADC noise on an idle clamp)
const int START_CYCLES = 90;          // 1.5 s of compressor start
const float CYCLE_MS = 1000.0f / 60.0f;

uint32_t lastRead = 0;
bool lastY = false;
bool haveStart = false;
CtStart lastStart;

CtCh* find(const char* k) { for (auto& c : CH) if (!strcmp(c.key, k)) return &c; return nullptr; }

float toAmps(const CtCh& c, float vrms) {
  float a = vrms * c.aPerV;
  return a < NOISE_A ? 0.0f : a;
}

void readRms() {
  CtAcc acc[NCH];
  uint32_t t0 = millis(), next = micros();
  while (millis() - t0 < WINDOW_MS) {
    for (int i = 0; i < NCH; i++) if (CH[i].fitted) acc[i].add(analogReadMilliVolts(CH[i].pin));
    next += SAMPLE_US;
    while ((int32_t)(micros() - next) < 0) {}
  }
  for (int i = 0; i < NCH; i++) {
    if (!CH[i].fitted) continue;
    CH[i].vrms = acc[i].rmsMv() / 1000.0f;
    CH[i].amps = toAmps(CH[i], CH[i].vrms);
  }
}

// Blocks for 1.5 s once per compressor start (the mode inputs keep sampling on their timer).
void captureStart() {
  CtCh& c = CH[0];
  float cyc[START_CYCLES];
  uint32_t next = micros();
  for (int k = 0; k < START_CYCLES; k++) {
    CtAcc acc;
    uint32_t t0 = micros();
    while (micros() - t0 < (uint32_t)(CYCLE_MS * 1000)) {
      acc.add(analogReadMilliVolts(c.pin));
      next += 250;
      while ((int32_t)(micros() - next) < 0) {}
    }
    cyc[k] = toAmps(c, acc.rmsMv() / 1000.0f);
  }
  lastStart = ctStart(cyc, START_CYCLES, CYCLE_MS);
  haveStart = lastStart.valid;
  Serial.printf("[ct] start: %s peak %.1f A, %d ms\n", lastStart.valid ? "ok" : "no current",
                lastStart.peakA, lastStart.ms);
}

}  // namespace

void ctBegin() {
  analogReadResolution(12);
  for (auto& c : CH) {
    c.fitted = setGetB((String(c.key) + ".fit").c_str(), false);
    c.aPerV = setGetF((String(c.key) + ".apv").c_str(), c.defAPerV);
    analogSetPinAttenuation(c.pin, ADC_11db);
  }
}

bool ctAnyFitted() { for (auto& c : CH) if (c.fitted) return true; return false; }

void ctLoop(bool y) {
  if (!ctAnyFitted()) { lastY = y; return; }
  if (y && !lastY && CH[0].fitted) captureStart();
  lastY = y;
  if (millis() - lastRead < READ_MS) return;
  lastRead = millis();
  readRms();
}

void ctFillTelemetry(JsonDocument& doc, bool y) {
  doc["contactor"] = y;                        // version 1: the contactor coil is the Y call
  JsonObject v = doc["v"].to<JsonObject>();
  v["line"] = nullptr; v["contactor"] = nullptr;
  JsonObject i = doc["i"].to<JsonObject>();
  for (int k = 0; k < 2; k++) {
    if (CH[k].fitted && !isnan(CH[k].amps)) i[CH[k].field] = round2(CH[k].amps); else i[CH[k].field] = nullptr;
  }
  JsonObject cap = doc["cap"].to<JsonObject>();
  if (CH[2].fitted && !isnan(CH[2].amps)) {
    JsonObject h = cap["herm"].to<JsonObject>();
    h["v"] = nullptr; h["i"] = round2(CH[2].amps);
  } else cap["herm"] = nullptr;
  cap["fan"] = nullptr;
  if (haveStart) {
    JsonObject s = doc["start"].to<JsonObject>();
    s["peak_a"] = round1(lastStart.peakA); s["ms"] = lastStart.ms;
  } else doc["start"] = nullptr;
  JsonObject raw = doc["raw"].to<JsonObject>();
  for (auto& c : CH) if (c.fitted && !isnan(c.vrms)) raw[c.key] = round3(c.vrms);
  JsonArray err = doc["err"].to<JsonArray>();
  // a clamp sitting at the rail means the jack is shorted or CT_BIAS is missing
  for (auto& c : CH) if (c.fitted && !isnan(c.vrms) && c.vrms > 1.0f) err.add(c.key);
}

void ctFillStatus(JsonObject cal) {
  for (auto& c : CH) {
    JsonObject o = cal[c.key].to<JsonObject>();
    o["fit"] = c.fitted; o["a_per_v"] = c.aPerV;
  }
}

// {"cmd":"fitted","ch":"ct_comp","on":true}        a clamp is installed on that jack
// {"cmd":"cal_ref","ch":"ct_comp","ref":14.2}      amps from a clamp meter on the same wire, running
// {"cmd":"ct_scale","ch":"ct_fan","a_per_v":5}     a different CT model (amps per volt RMS)
// {"cmd":"cal_reset","ch":"ct_comp"}               back to the BOM CT's scale
bool ctHandleCmd(JsonDocument& cmd, JsonDocument& reply) {
  const char* c = cmd["cmd"] | "";
  const char* k = cmd["ch"] | "";
  CtCh* ch = find(k);
  if (!ch) return false;
  auto fail = [&](const char* why) { reply["ok"] = false; reply["error"] = why; return true; };
  auto done = [&]() { reply["ok"] = true; reply["ch"] = k; reply["a_per_v"] = ch->aPerV; reply["fit"] = ch->fitted; return true; };
  if (!strcmp(c, "fitted")) {
    ch->fitted = cmd["on"] | false;
    setPutB((String(k) + ".fit").c_str(), ch->fitted);
    return done();
  }
  if (!strcmp(c, "cal_ref")) {
    float ref = cmd["ref"] | NAN;
    if (isnan(ref) || ref < 1.0f) return fail("need ref amps (at least 1 A, with the load running)");
    if (!ch->fitted || isnan(ch->vrms) || ch->vrms < 0.005f) return fail("no current on this clamp");
    ch->aPerV = ref / ch->vrms;
  } else if (!strcmp(c, "ct_scale")) {
    float apv = cmd["a_per_v"] | 0.0f;
    if (apv <= 0 || apv > 1000) return fail("a_per_v must be 0-1000");
    ch->aPerV = apv;
  } else if (!strcmp(c, "cal_reset")) {
    ch->aPerV = ch->defAPerV;
  } else {
    return fail("CT channels take fitted, cal_ref, ct_scale and cal_reset");
  }
  setPutF((String(k) + ".apv").c_str(), ch->aPerV);
  return done();
}

#endif
