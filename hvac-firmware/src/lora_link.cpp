#include "lora_link.h"

#if LORA_ENABLED
#include "common.h"
#include "lora_radio.h"
#include "lorafmt.h"
#include "settings.h"

#ifndef LORA_PIN_NSS            // the node's SX1262 (e.g. Wio-SX1262) on VSPI; see config.example.h
#define LORA_PIN_NSS 5
#define LORA_PIN_DIO1 26
#define LORA_PIN_RST 27
#define LORA_PIN_BUSY 25
#endif
#ifndef LORA_TCXO_V
#define LORA_TCXO_V 1.8
#endif

static LoraRadio radio;
static LoraCmdHandler cmdHandler = nullptr;
static uint8_t key[16];
static uint32_t counter = 0, savedCounter = 0, lastCmdCounter = 0;
static bool haveCmdCounter = false;

// The frame counter must never repeat for our key: kept in flash every 100 frames, and after a
// reboot we carry on 100 past the last saved value.
static uint32_t nextCounter() {
  counter++;
  if (counter - savedCounter >= 100) {
    setPutU("lora_ctr", counter);
    savedCounter = counter;
  }
  return counter;
}

static bool sendPayload(uint8_t type, const uint8_t* payload, size_t n) {
  uint8_t frame[lorafmt::MAX_FRAME];
  const size_t fn = lorafmt::seal(key, LORA_DEVICE_ID, nextCounter(), type, payload, n, frame);
  return radio.send(frame, fn);
}

bool loraBegin(LoraCmdHandler onCmd) {
  cmdHandler = onCmd;
  if (!lorafmt::parse_key(LORA_KEY, key) || LORA_DEVICE_ID == 0) {
    Serial.println("[lora] LORA_DEVICE_ID / LORA_KEY not set: run radio/tools/new_device.py");
    return false;
  }
  counter = savedCounter = setGetU("lora_ctr", 0) + 100;
  setPutU("lora_ctr", counter);
  lastCmdCounter = setGetU("lora_cmd", 0);
  haveCmdCounter = lastCmdCounter != 0;
  LoraPins pins = {LORA_PIN_NSS, LORA_PIN_DIO1, LORA_PIN_RST, LORA_PIN_BUSY};
  pins.tcxo_v = LORA_TCXO_V;
  const double mhz = lorafmt::channel_mhz(SITE_ID);
  if (!radio.begin(pins, mhz)) return false;
  Serial.printf("[lora] device %08x on %.1f MHz, counter from %u\n", (unsigned)LORA_DEVICE_ID, mhz, (unsigned)counter);
  return true;
}

void loraLoop() {
  uint8_t frame[lorafmt::MAX_FRAME], payload[lorafmt::MAX_FRAME], type;
  size_t n, pn;
  int rssi;
  float snr;
  uint32_t dev, ctr;
  if (!radio.receive(frame, n, sizeof frame, rssi, snr)) return;
  if (n < lorafmt::HEADER_LEN || lorafmt::peek_device(frame) != LORA_DEVICE_ID) return;   // another node's
  if (!lorafmt::open_frame(key, frame, n, type, dev, ctr, payload, pn) || type != lorafmt::COMMAND) return;
  if (haveCmdCounter && ctr <= lastCmdCounter) {
    Serial.printf("[lora] refused a replayed command (%u)\n", (unsigned)ctr);
    return;
  }
  lastCmdCounter = ctr;
  haveCmdCounter = true;
  setPutU("lora_cmd", ctr);
  JsonDocument cmd, reply;
  if (!lorafmt::unpack_command(payload, pn, cmd)) {
    reply["ok"] = false;
    reply["error"] = "bad frame";
  } else {
    reply["cmd"] = cmd["cmd"];
    if (cmdHandler) cmdHandler(cmd, reply);
  }
  String out;
  serializeJson(reply, out);
  Serial.printf("[lora cmd] %s (%d dBm)\n", out.c_str(), rssi);
  uint8_t p[lorafmt::MAX_PAYLOAD];
  sendPayload(lorafmt::REPLY, p, lorafmt::pack_reply(reply.as<JsonVariantConst>(), p));
}

bool loraSendReading(JsonDocument& doc) {
  uint8_t p[lorafmt::MAX_PAYLOAD], type;
  const size_t n = lorafmt::pack_reading(doc.as<JsonVariantConst>(), 0, type, p);
  return n && sendPayload(type, p, n);
}

void loraSendStatus(uint32_t intervalMs) {
  uint8_t p[lorafmt::MAX_PAYLOAD];
  sendPayload(lorafmt::STATUS, p, lorafmt::pack_status(FW_VERSION, intervalMs, 0, 0, p));
}

bool loraOk() { return radio.ok(); }

#else   // LoRa off: nothing to do

bool loraBegin(LoraCmdHandler) { return false; }
void loraLoop() {}
bool loraSendReading(JsonDocument&) { return false; }
void loraSendStatus(uint32_t) {}
bool loraOk() { return false; }

#endif
