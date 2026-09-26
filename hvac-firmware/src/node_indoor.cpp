// Indoor node: supply/return air temperatures and thermostat calls (Y, W, G, O/B).
// Matches schematic sheet 2.
#ifdef NODE_INDOOR

#include "common.h"
#include "settings.h"
#include "mode_inputs.h"
#include <OneWire.h>
#include <DallasTemperature.h>

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

void nodeSetup() {
  calSup.name = "t_sup"; calSup.load();
  calRet.name = "t_ret"; calRet.load();
  swapProbes = setGetB("ds_swap", false);
  scanProbes();
  modeInputsBegin(MODES, sizeof(MODES) / sizeof(MODES[0]));
}

void nodeLoop() {
  uint32_t now = millis();
  if (!converting && now - lastRequest >= READ_EVERY_MS) {
    if (found < 2) scanProbes();               // keep looking if a probe is missing
    ds.requestTemperatures();
    convStart = now; lastRequest = now; converting = true;
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

  JsonArray err = doc["err"].to<JsonArray>();
  if (found < 2) err.add("ds18b20_missing");
  if (found >= 1 && isnan(supF)) err.add("t_sup");
  if (found >= 2 && isnan(retF)) err.add("t_ret");
}

void nodeFillStatus(JsonDocument& doc) {
  JsonArray probes = doc["probes"].to<JsonArray>();
  for (uint8_t i = 0; i < found; i++) probes.add(addrHex(addr[i]));
  doc["ds_swap"] = swapProbes;
  JsonObject cal = doc["cal"].to<JsonObject>();
  cal["t_sup"]["o"] = calSup.offset;
  cal["t_ret"]["o"] = calRet.offset;
}

// Commands (send JSON to hvac/<site>/indoor/cmd):
//  {"cmd":"ds_swap"}                              supply and return are reversed -> swap them
//  {"cmd":"cal_ref","ch":"t_sup","ref":32.0}      probe at a known temperature
//  {"cmd":"cal_set","ch":"t_ret","offset":-0.4}
//  {"cmd":"cal_reset","ch":"t_sup"}
//  {"cmd":"rescan"}                               look for probes again
bool nodeHandleCmd(JsonDocument& cmd, JsonDocument& reply) {
  const char* c  = cmd["cmd"] | "";
  const char* ch = cmd["ch"]  | "";
  Cal* cal = !strcmp(ch, "t_sup") ? &calSup : (!strcmp(ch, "t_ret") ? &calRet : nullptr);
  float uncal = cal == &calSup ? supUncal : retUncal;

  if (!strcmp(c, "ds_swap")) {
    swapProbes = !swapProbes; setPutB("ds_swap", swapProbes);
    reply["ok"] = true; reply["ds_swap"] = swapProbes; return true;
  }
  if (!strcmp(c, "rescan")) {
    scanProbes(); reply["ok"] = true; reply["found"] = found; return true;
  }
  if (!strcmp(c, "cal_ref") || !strcmp(c, "cal_set") || !strcmp(c, "cal_reset")) {
    if (!cal) { reply["ok"] = false; reply["error"] = "ch must be t_sup or t_ret"; return true; }
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
