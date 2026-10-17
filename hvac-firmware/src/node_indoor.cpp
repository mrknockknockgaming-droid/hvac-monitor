// Indoor node: supply/return air temperatures and thermostat calls (Y, W, G, O/B).
// Matches schematic sheet 2. Optional (phase 6): supply and return humidity from two SHT45s behind a
// TCA9548A I2C mux on J5 (both SHT45s answer at 0x44): return on mux channel 0, supply on channel 1.
#ifdef NODE_INDOOR

#include "common.h"
#include "settings.h"
#include "mode_inputs.h"
#include "sht45.h"
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Wire.h>

static const uint8_t PIN_ONEWIRE = 4;                       // ONEWIRE net, R1 pull-up
static const ModeInput MODES[] = {
  {"Y", 34}, {"W", 35}, {"G", 36}, {"OB", 39}                // MODE_Y, MODE_W, MODE_G (VP), MODE_OB (VN)
};

static OneWire oneWire(PIN_ONEWIRE);
static DallasTemperature ds(&oneWire);
static DeviceAddress addr[2];
static uint8_t found = 0;
static bool swapProbes = false;       // flip which probe is supply vs return

static Cal calSup, calRet;
static float supF = NAN, retF = NAN;
static float supUncal = NAN, retUncal = NAN;

// Humidity: found on its own (no setting to turn on). Without a mux the telemetry has no rh_* keys.
static const uint8_t MUX_ADDR = 0x70, SHT45_ADDR = 0x44;
static const uint8_t RH_CH_RET = 0, RH_CH_SUP = 1;           // mux channels
static bool muxOk = false, muxSeen = false, rhSwap = false;
static bool rhOk[2] = {false, false};                         // [0] return, [1] supply
static float rhPct[2] = {NAN, NAN}, rhUncal[2] = {NAN, NAN}, rhTempF[2] = {NAN, NAN};
static Cal calRh[2];

static uint32_t convStart = 0;
static bool converting = false;
static const uint32_t CONV_MS = 800;       // 12-bit conversion takes up to 750 ms
static const uint32_t READ_EVERY_MS = 2000;
static uint32_t lastRequest = 0;

static String addrHex(const DeviceAddress a) {
  char s[17];
  for (int i = 0; i < 8; i++) sprintf(s + i * 2, "%02X", a[i]);
  return String(s);
}

static void scanProbes() {
  ds.begin();
  found = 0;
  for (uint8_t i = 0; i < ds.getDeviceCount() && found < 2; i++) {
    if (ds.getAddress(addr[found], i)) {
      ds.setResolution(addr[found], 12);
      found++;
    }
  }
  ds.setWaitForConversion(false);
  Serial.printf("[hw] DS18B20 probes found: %u\n", found);
  for (uint8_t i = 0; i < found; i++) Serial.printf("      #%u %s\n", i, addrHex(addr[i]).c_str());
}

static float readF(uint8_t idx) {
  if (idx >= found) return NAN;
  float c = ds.getTempC(addr[idx]);
  if (c == DEVICE_DISCONNECTED_C || c < -50 || c > 120) return NAN;
  return c * 9.0f / 5.0f + 32.0f;
}

static bool muxSelect(uint8_t ch) {
  Wire.beginTransmission(MUX_ADDR);
  Wire.write(ch < 8 ? (uint8_t)(1 << ch) : (uint8_t)0);
  return Wire.endTransmission() == 0;
}

// One high-precision SHT45 measurement on a mux channel (about 9 ms).
static bool sht45Read(uint8_t ch, float& tempC, float& rh) {
  if (!muxSelect(ch)) return false;
  Wire.beginTransmission(SHT45_ADDR);
  Wire.write(SHT45_MEASURE_HIGH);
  if (Wire.endTransmission() != 0) return false;
  delay(10);
  uint8_t b[6];
  if (Wire.requestFrom(SHT45_ADDR, (uint8_t)6) != 6) return false;
  for (uint8_t i = 0; i < 6; i++) b[i] = Wire.read();
  return sht45Decode(b, tempC, rh);
}

static void readHumidity() {
  if (!muxOk) {                                       // look again each cycle, so probes can be added live
    muxOk = muxSelect(0xFF);
    if (muxOk && !muxSeen) Serial.println("[hw] TCA9548A found: humidity probes on");
    muxSeen = muxSeen || muxOk;
    if (!muxOk) { rhOk[0] = rhOk[1] = false; return; }
  }
  for (uint8_t side = 0; side < 2; side++) {          // side 0 = return, 1 = supply
    uint8_t ch = (side == 0) != rhSwap ? RH_CH_RET : RH_CH_SUP;
    float c, rh;
    rhOk[side] = sht45Read(ch, c, rh);
    if (!rhOk[side]) { rhPct[side] = rhUncal[side] = rhTempF[side] = NAN; continue; }
    rhUncal[side] = rh;
    rhPct[side] = constrain(calRh[side].apply(rh), 0.0f, 100.0f);
    rhTempF[side] = c * 9.0f / 5.0f + 32.0f;
  }
  if (!muxSelect(0xFF)) { muxOk = false; rhOk[0] = rhOk[1] = false; }   // all channels off; failing = mux gone
}

static void scanHumidity() {
  muxOk = muxSelect(0xFF);
  muxSeen = muxSeen || muxOk;
  Serial.printf("[hw] TCA9548A %s\n", muxOk ? "ok" : "not fitted");
}

void nodeSetup() {
  calSup.name = "t_sup"; calSup.load();
  calRet.name = "t_ret"; calRet.load();
  swapProbes = setGetB("ds_swap", false);
  calRh[0].name = "rh_ret"; calRh[0].load();
  calRh[1].name = "rh_sup"; calRh[1].load();
  rhSwap = setGetB("rh_swap", false);
  scanProbes();
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(100000);
  scanHumidity();
  modeInputsBegin(MODES, sizeof(MODES) / sizeof(MODES[0]));
}

void nodeLoop() {
  uint32_t now = millis();
  if (!converting && now - lastRequest >= READ_EVERY_MS) {
    if (found < 2) scanProbes();               // keep looking if a probe is missing
    ds.requestTemperatures();
    convStart = now; lastRequest = now; converting = true;
    readHumidity();                                   // while the DS18B20s convert
  }
  if (converting && now - convStart >= CONV_MS) {
    converting = false;
    uint8_t iSup = swapProbes ? 1 : 0, iRet = swapProbes ? 0 : 1;
    supUncal = readF(iSup);
    retUncal = readF(iRet);
    supF = isnan(supUncal) ? NAN : calSup.apply(supUncal);
    retF = isnan(retUncal) ? NAN : calRet.apply(retUncal);
  }
}

void nodeFillTelemetry(JsonDocument& doc) {
  modeFillJson(doc["mode"].to<JsonObject>());
  JsonObject air = doc["air"].to<JsonObject>();     // deg F
  if (!isnan(supF)) air["supply"] = round1(supF); else air["supply"] = nullptr;
  if (!isnan(retF)) air["return"] = round1(retF); else air["return"] = nullptr;
  if (!isnan(supF) && !isnan(retF)) air["dt"] = round1(retF - supF); else air["dt"] = nullptr;
  if (muxSeen) {                                    // % RH, and each SHT45's own temperature (deg F)
    const char* side[2] = {"ret", "sup"};
    for (uint8_t i = 0; i < 2; i++) {
      String k = String(side[i]);
      if (rhOk[i]) { air["rh_" + k] = round1(rhPct[i]); air["t_rh_" + k] = round1(rhTempF[i]); }
      else { air["rh_" + k] = nullptr; air["t_rh_" + k] = nullptr; }
    }
  }

  JsonArray err = doc["err"].to<JsonArray>();
  if (found < 2) err.add("ds18b20_missing");
  if (found >= 1 && isnan(supF)) err.add("t_sup");
  if (found >= 2 && isnan(retF)) err.add("t_ret");
  if (muxSeen && !muxOk) err.add("tca9548a");
  if (muxOk && !rhOk[0]) err.add("sht45_ret");
  if (muxOk && !rhOk[1]) err.add("sht45_sup");
}

void nodeFillStatus(JsonDocument& doc) {
  JsonArray probes = doc["probes"].to<JsonArray>();
  for (uint8_t i = 0; i < found; i++) probes.add(addrHex(addr[i]));
  doc["ds_swap"] = swapProbes;
  JsonObject cal = doc["cal"].to<JsonObject>();
  cal["t_sup"]["o"] = calSup.offset;
  cal["t_ret"]["o"] = calRet.offset;
  JsonObject rh = doc["rh"].to<JsonObject>();
  rh["mux"] = muxOk;
  rh["swap"] = rhSwap;
  cal["rh_ret"]["o"] = calRh[0].offset;
  cal["rh_sup"]["o"] = calRh[1].offset;
}

// Commands (send JSON to hvac/<site>/indoor/cmd):
//  {"cmd":"ds_swap"}                              supply and return are reversed -> swap them
//  {"cmd":"cal_ref","ch":"t_sup","ref":32.0}      probe at a known temperature
//  {"cmd":"cal_set","ch":"t_ret","offset":-0.4}
//  {"cmd":"cal_reset","ch":"t_sup"}
//  {"cmd":"rescan"}                               look for probes (and the humidity mux) again
//  {"cmd":"rh_swap"}                              humidity supply and return are reversed -> swap them
//  {"cmd":"cal_ref","ch":"rh_ret","ref":75.0}     humidity probe at a known % RH (cal_set / cal_reset too)
bool nodeHandleCmd(JsonDocument& cmd, JsonDocument& reply) {
  const char* c  = cmd["cmd"] | "";
  const char* ch = cmd["ch"]  | "";
  Cal* cal = !strcmp(ch, "t_sup") ? &calSup : !strcmp(ch, "t_ret") ? &calRet
           : !strcmp(ch, "rh_ret") ? &calRh[0] : !strcmp(ch, "rh_sup") ? &calRh[1] : nullptr;
  float uncal = cal == &calSup ? supUncal : cal == &calRet ? retUncal
              : cal == &calRh[0] ? rhUncal[0] : rhUncal[1];

  if (!strcmp(c, "ds_swap")) {
    swapProbes = !swapProbes; setPutB("ds_swap", swapProbes);
    reply["ok"] = true; reply["ds_swap"] = swapProbes; return true;
  }
  if (!strcmp(c, "rh_swap")) {
    rhSwap = !rhSwap; setPutB("rh_swap", rhSwap);
    reply["ok"] = true; reply["rh_swap"] = rhSwap; return true;
  }
  if (!strcmp(c, "rescan")) {
    scanProbes(); scanHumidity();
    reply["ok"] = true; reply["found"] = found; reply["mux"] = muxOk; return true;
  }
  if (!strcmp(c, "cal_ref") || !strcmp(c, "cal_set") || !strcmp(c, "cal_reset")) {
    if (!cal) { reply["ok"] = false; reply["error"] = "ch must be t_sup, t_ret, rh_ret or rh_sup"; return true; }
    if (!strcmp(c, "cal_ref")) {
      float ref = cmd["ref"] | NAN;
      if (isnan(ref) || isnan(uncal)) { reply["ok"] = false; reply["error"] = "need ref and a valid reading"; return true; }
      cal->offset = ref - uncal;
      cal->save();
    } else if (!strcmp(c, "cal_set")) {
      cal->offset = cmd["offset"] | cal->offset;
      cal->save();
    } else {
      cal->reset();
    }
    reply["ok"] = true; reply["ch"] = ch; reply["offset"] = cal->offset; return true;
  }
  return false;
}

#endif
