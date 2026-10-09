// Outdoor node: refrigerant pressures, line temperatures, outdoor air, Y and O/B.
// Matches schematic sheet 1 (net names in comments).
#ifdef NODE_OUTDOOR

#include "common.h"
#include "config.h"
#include "settings.h"
#include "mode_inputs.h"
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <Adafruit_SHT31.h>

// ---------- Hardware constants ----------
static const float PSI_PER_BAR    = 14.5038f;
// Voltage dividers in kilohms (top resistor to the signal, bottom to ground). Defaults are the rev A
// board as designed; set them in config.h if the board was built with other values (e.g. all 10k).
#ifndef P_DIV_TOP_K
#define P_DIV_TOP_K 10.0         // R1, R3, R5
#endif
#ifndef P_DIV_BOTTOM_K
#define P_DIV_BOTTOM_K 20.0      // R2, R4, R6
#endif
#ifndef RAIL_DIV_TOP_K
#define RAIL_DIV_TOP_K 10.0      // R7
#endif
#ifndef RAIL_DIV_BOTTOM_K
#define RAIL_DIV_BOTTOM_K 15.0   // R8
#endif
// The ADS1115 runs from 3.3 V: readings must stay below that, and nothing may pass its 3.6 V absolute
// maximum. Sensors top out at 4.5 V (5 V if a wire faults high); the 5 V rail can reach 5.5 V.
// Refuse to build dividers that would overdrive it.
static_assert(4.5 * P_DIV_BOTTOM_K / (P_DIV_TOP_K + P_DIV_BOTTOM_K) <= 3.3,
              "pressure divider: a 4.5 V reading would exceed the ADS1115's 3.3 V supply (bottom resistor too large)");
static_assert(5.0 * P_DIV_BOTTOM_K / (P_DIV_TOP_K + P_DIV_BOTTOM_K) <= 3.6,
              "pressure divider: a faulted 5 V signal would exceed the ADS1115's 3.6 V absolute maximum");
static_assert(5.5 * RAIL_DIV_BOTTOM_K / (RAIL_DIV_TOP_K + RAIL_DIV_BOTTOM_K) <= 3.3,
              "rail divider: a 5.5 V rail would exceed the ADS1115's 3.3 V supply (bottom resistor too large)");
static const float P_DIV_GAIN     = (P_DIV_TOP_K + P_DIV_BOTTOM_K) / P_DIV_BOTTOM_K;           // ADC volts -> sensor volts
static const float RAIL_DIV_GAIN  = (RAIL_DIV_TOP_K + RAIL_DIV_BOTTOM_K) / RAIL_DIV_BOTTOM_K;  // V5_MON -> 5 V rail
static const float NTC_RREF       = 10000.0f;                  // R9 / R10
static const float NTC_R0         = 10000.0f;                  // 10k at 25 C
static const int   ADC_AVG        = 4;                         // samples averaged per reading

static const ModeInput MODES[] = { {"Y", 34}, {"OB", 35} };    // MODE_Y, MODE_OB

static Adafruit_ADS1115 ads1;   // U3 @ 0x48: pressures + rail
static Adafruit_ADS1115 ads2;   // U4 @ 0x49: thermistors
static Adafruit_SHT31   sht;    // J8 @ 0x44
static bool ads1Ok = false, ads2Ok = false, shtOk = false;

// ---------- Channels ----------
struct PressureCh {
  const char* key;      // "p_liq" etc.
  uint8_t adc;          // ADS #1 input
  float   defFsBar;     // default full-scale range
  bool    defFitted;
  float   fsBar;
  bool    fitted;
  Cal     cal;
  bool    ok = false;
  float   uncal = NAN;  // psig before calibration
  float   psig = NAN;
  float   volts = NAN;  // sensor-side volts
};
static PressureCh P[] = {
  {"p_liq",  0, 50.0f, true },   // J3 liquid line
  {"p_vap",  1, 50.0f, true },   // J4 vapor line port
  {"p_tsuc", 2, 35.0f, false},   // J5 true suction (not fitted yet)
};
static const int NP = sizeof(P) / sizeof(P[0]);

struct TempCh {
  const char* key;
  uint8_t adc;          // ADS #2 input
  bool    defFitted;
  bool    fitted;
  Cal     cal;
  bool    ok = false;
  float   uncal = NAN;  // deg F before calibration
  float   degF = NAN;
  float   ohms = NAN;
};
static TempCh T[] = {
  {"t_suc",  0, true },   // TH1 suction line
  {"t_liq",  1, true },   // TH2 liquid line
  {"t_tsuc", 2, false},   // future
  {"t_dis",  3, false},   // future (needs high-temp thermistor)
};
static const int NT = sizeof(T) / sizeof(T[0]);

static float railV = NAN;
static bool  railOk = false;
static float ntcBeta = 3950.0f;
static float v33 = 3.30f;              // supply feeding the thermistor dividers
static float airF = NAN, airRh = NAN;
static uint32_t lastRead = 0;
static const uint32_t READ_MS = 1000;

// ---------- Helpers ----------
static float readVolts(Adafruit_ADS1115& a, uint8_t ch) {
  float sum = 0;
  for (int i = 0; i < ADC_AVG; i++) sum += a.computeVolts(a.readADC_SingleEnded(ch));
  return sum / ADC_AVG;
}

static PressureCh* findP(const char* k) { for (auto& p : P) if (!strcmp(p.key, k)) return &p; return nullptr; }
static TempCh*     findT(const char* k) { for (auto& t : T) if (!strcmp(t.key, k)) return &t; return nullptr; }

static void loadChannelSettings() {
  for (auto& p : P) {
    p.cal.name = p.key; p.cal.load();
    p.fsBar  = setGetF((String(p.key) + ".fs").c_str(), p.defFsBar);
    p.fitted = setGetB((String(p.key) + ".fit").c_str(), p.defFitted);
  }
  for (auto& t : T) {
    t.cal.name = t.key; t.cal.load();
    t.fitted = setGetB((String(t.key) + ".fit").c_str(), t.defFitted);
  }
  ntcBeta = setGetF("ntc_b", 3950.0f);
  v33     = setGetF("v33", 3.30f);
}

// ---------- Reading ----------
static void readPressures() {
  if (!ads1Ok) { railOk = false; for (auto& p : P) p.ok = false; return; }

  // Ratiometric: sensor output is 10%..90% of its supply, so divide by the measured 5 V rail.
  railV = readVolts(ads1, 3) * RAIL_DIV_GAIN;           // V5_MON
  railOk = railV > 4.5f && railV < 5.5f;
  float supply = railOk ? railV : 5.0f;

  for (auto& p : P) {
    if (!p.fitted) { p.ok = false; continue; }
    p.volts = readVolts(ads1, p.adc) * P_DIV_GAIN;
    float ratio = p.volts / supply;
    if (ratio < 0.05f || ratio > 0.97f) { p.ok = false; continue; }   // open wire or short
    float bar = (ratio - 0.10f) / 0.80f * p.fsBar;
    p.uncal = bar * PSI_PER_BAR;
    p.psig  = p.cal.apply(p.uncal);
    p.ok = true;
  }
}

static void readTemps() {
  if (!ads2Ok) { for (auto& t : T) t.ok = false; return; }
  for (auto& t : T) {
    if (!t.fitted) { t.ok = false; continue; }
    float v = readVolts(ads2, t.adc);
    if (v < 0.02f || v > v33 - 0.02f) { t.ok = false; continue; }    // open or shorted probe
    t.ohms = NTC_RREF * v / (v33 - v);
    float kelvin = 1.0f / (1.0f / 298.15f + logf(t.ohms / NTC_R0) / ntcBeta);
    t.uncal = (kelvin - 273.15f) * 9.0f / 5.0f + 32.0f;
    t.degF  = t.cal.apply(t.uncal);
    t.ok = t.degF > -40.0f && t.degF < 300.0f;
  }
}

static void readAir() {
  if (!shtOk) { shtOk = sht.begin(0x44); if (!shtOk) return; }
  float c = sht.readTemperature();
  float h = sht.readHumidity();
  if (isnan(c) || isnan(h)) { airF = airRh = NAN; shtOk = false; return; }
  airF = c * 9.0f / 5.0f + 32.0f;
  airRh = h;
}

// ---------- Node interface ----------
void nodeSetup() {
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(100000);
  loadChannelSettings();

  ads1Ok = ads1.begin(0x48, &Wire);
  ads2Ok = ads2.begin(0x49, &Wire);
  for (auto* a : {&ads1, &ads2}) {
    a->setGain(GAIN_ONE);                   // +/-4.096 V range (inputs stay under 3.3 V)
    a->setDataRate(RATE_ADS1115_250SPS);
  }
  shtOk = sht.begin(0x44);
  Serial.printf("[hw] ADS1115 #1 %s, ADS1115 #2 %s, SHT30 %s\n",
                ads1Ok ? "ok" : "MISSING", ads2Ok ? "ok" : "MISSING", shtOk ? "ok" : "MISSING");

  modeInputsBegin(MODES, sizeof(MODES) / sizeof(MODES[0]));
}

void nodeLoop() {
  uint32_t now = millis();
  if (now - lastRead < READ_MS) return;
  lastRead = now;
  if (!ads1Ok) ads1Ok = ads1.begin(0x48, &Wire);   // retry if a module was unplugged
  if (!ads2Ok) ads2Ok = ads2.begin(0x49, &Wire);
  readPressures();
  readTemps();
  readAir();
}

void nodeFillTelemetry(JsonDocument& doc) {
  modeFillJson(doc["mode"].to<JsonObject>());

  JsonObject p = doc["p"].to<JsonObject>();          // psig
  for (auto& ch : P) { const char* k = ch.key + 2; if (ch.ok) p[k] = round1(ch.psig); else p[k] = nullptr; }

  JsonObject t = doc["t"].to<JsonObject>();          // deg F
  for (auto& ch : T) { const char* k = ch.key + 2; if (ch.ok) t[k] = round1(ch.degF); else t[k] = nullptr; }

  JsonObject air = doc["air"].to<JsonObject>();
  if (!isnan(airF)) { air["t"] = round1(airF); air["rh"] = round1(airRh); }
  else { air["t"] = nullptr; air["rh"] = nullptr; }

  if (railOk) doc["v5"] = round2(railV); else doc["v5"] = nullptr;

  // Raw values for calibration and troubleshooting
  JsonObject raw = doc["raw"].to<JsonObject>();
  for (auto& ch : P) if (ch.fitted && !isnan(ch.volts)) raw[ch.key] = round2(ch.volts);
  for (auto& ch : T) if (ch.fitted && !isnan(ch.ohms)) raw[ch.key] = (int)ch.ohms;

  JsonArray err = doc["err"].to<JsonArray>();
  if (!ads1Ok) err.add("ads1");
  if (!ads2Ok) err.add("ads2");
  if (!shtOk)  err.add("sht30");
  if (ads1Ok && !railOk) err.add("rail5v");
  for (auto& ch : P) if (ch.fitted && !ch.ok && ads1Ok) err.add(ch.key);
  for (auto& ch : T) if (ch.fitted && !ch.ok && ads2Ok) err.add(ch.key);
}

void nodeFillStatus(JsonDocument& doc) {
  JsonObject cal = doc["cal"].to<JsonObject>();
  for (auto& ch : P) {
    JsonObject c = cal[ch.key].to<JsonObject>();
    c["o"] = ch.cal.offset; c["s"] = ch.cal.scale; c["fs_bar"] = ch.fsBar; c["fit"] = ch.fitted;
  }
  for (auto& ch : T) {
    JsonObject c = cal[ch.key].to<JsonObject>();
    c["o"] = ch.cal.offset; c["fit"] = ch.fitted;
  }
  doc["ntc_b"] = ntcBeta;
  doc["v33"] = v33;
  doc["p_div"] = P_DIV_GAIN;        // so the build's divider settings show in the status
  doc["rail_div"] = RAIL_DIV_GAIN;
}

// Commands (send JSON to hvac/<site>/outdoor/cmd):
//  {"cmd":"cal_zero","ch":"p_liq"}              sensor open to atmosphere -> reads 0 psig
//  {"cmd":"cal_span","ch":"p_liq","ref":300}    at a known pressure from your reference gauge
//  {"cmd":"cal_ref","ch":"t_suc","ref":32.0}    temperature probe at a known temp (ice bath = 32 F)
//  {"cmd":"cal_set","ch":"p_liq","offset":0,"scale":1}
//  {"cmd":"cal_reset","ch":"p_liq"}
//  {"cmd":"range","ch":"p_tsuc","bar":35}       set transducer full-scale range
//  {"cmd":"fitted","ch":"p_tsuc","on":true}     enable a channel once the sensor is installed
//  {"cmd":"ntc_b","value":3950}                 thermistor B-value
//  {"cmd":"v33","value":3.30}                   measured 3.3 V rail (improves thermistor accuracy)
bool nodeHandleCmd(JsonDocument& cmd, JsonDocument& reply) {
  const char* c  = cmd["cmd"] | "";
  const char* ch = cmd["ch"]  | "";
  PressureCh* p = findP(ch);
  TempCh*     t = findT(ch);
  Cal* cal = p ? &p->cal : (t ? &t->cal : nullptr);
  float uncal = p ? p->uncal : (t ? t->uncal : NAN);
  bool chOk   = p ? p->ok : (t ? t->ok : false);

  auto fail = [&](const char* why) { reply["ok"] = false; reply["error"] = why; return true; };
  auto done = [&]() { reply["ok"] = true; reply["ch"] = ch;
                      if (cal) { reply["offset"] = cal->offset; reply["scale"] = cal->scale; } return true; };

  if (!strcmp(c, "cal_zero")) {
    if (!p) return fail("cal_zero needs a pressure channel");
    if (!chOk) return fail("channel has no valid reading");
    p->cal.offset = -p->uncal * p->cal.scale;
    p->cal.save(); return done();
  }
  if (!strcmp(c, "cal_span")) {
    if (!p) return fail("cal_span needs a pressure channel");
    float ref = cmd["ref"] | NAN;
    if (isnan(ref) || !chOk) return fail("need ref and a valid reading");
    if (p->uncal < 50.0f) return fail("apply at least ~50 psi before spanning");
    p->cal.scale = (ref - p->cal.offset) / p->uncal;
    p->cal.save(); return done();
  }
  if (!strcmp(c, "cal_ref")) {
    if (!cal) return fail("unknown channel");
    float ref = cmd["ref"] | NAN;
    if (isnan(ref) || !chOk) return fail("need ref and a valid reading");
    cal->offset = ref - uncal * cal->scale;
    cal->save(); return done();
  }
  if (!strcmp(c, "cal_set")) {
    if (!cal) return fail("unknown channel");
    cal->offset = cmd["offset"] | cal->offset;
    cal->scale  = cmd["scale"]  | cal->scale;
    cal->save(); return done();
  }
  if (!strcmp(c, "cal_reset")) {
    if (!cal) return fail("unknown channel");
    cal->reset(); return done();
  }
  if (!strcmp(c, "range")) {
    float bar = cmd["bar"] | 0.0f;
    if (!p || bar < 5 || bar > 100) return fail("need pressure channel and bar 5-100");
    p->fsBar = bar; setPutF((String(p->key) + ".fs").c_str(), bar);
    reply["fs_bar"] = bar; return done();
  }
  if (!strcmp(c, "fitted")) {
    if (!p && !t) return fail("unknown channel");
    bool on = cmd["on"] | false;
    if (p) p->fitted = on; else t->fitted = on;
    setPutB((String(ch) + ".fit").c_str(), on);
    reply["fit"] = on; return done();
  }
  if (!strcmp(c, "ntc_b")) {
    float b = cmd["value"] | 0.0f;
    if (b < 2000 || b > 5000) return fail("value must be 2000-5000");
    ntcBeta = b; setPutF("ntc_b", b); reply["ok"] = true; reply["ntc_b"] = b; return true;
  }
  if (!strcmp(c, "v33")) {
    float v = cmd["value"] | 0.0f;
    if (v < 3.0f || v > 3.6f) return fail("value must be 3.0-3.6");
    v33 = v; setPutF("v33", v); reply["ok"] = true; reply["v33"] = v; return true;
  }
  return false;
}

#endif
