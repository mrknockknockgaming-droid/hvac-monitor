#include "lora_gw.h"

#include "config.h"
#ifndef LORA_GATEWAY
#define LORA_GATEWAY 0
#endif

#if LORA_GATEWAY
#include <Arduino.h>
#include <Preferences.h>
#include <time.h>

#include "lora_gateway.h"
#include "lora_radio.h"
#include "net.h"

#if !defined(LORA_PIN_NSS) || !defined(LORA_PIN_SCK)
#error "LORA_GATEWAY needs the SX1262 pins in config.h (LORA_PIN_SCK/MISO/MOSI/NSS/DIO1/RST/BUSY)"
#endif
#ifndef LORA_TCXO_V
#define LORA_TCXO_V 1.8
#endif

namespace {
struct DeviceCfg { uint32_t id; const char* key; const char* node; };
const DeviceCfg DEVICES[] = {LORA_DEVICES};

LoraRadio radio;
lorafmt::Gateway gw;
Preferences prefs;
uint32_t saved[sizeof(DEVICES) / sizeof(DEVICES[0])] = {};

std::string topic(const std::string& node, const char* kind) { return std::string("hvac/") + SITE_ID + "/" + node + "/" + kind; }
}  // namespace

void lora_gw_begin() {
    prefs.begin("lora-gw", false);
    for (const DeviceCfg& c : DEVICES) {
        uint8_t key[16];
        if (c.id == 0 || !lorafmt::parse_key(c.key, key)) continue;
        gw.add(c.id, key, SITE_ID, c.node);
        lorafmt::Device& d = gw.devices().back();
        char k[12];
        snprintf(k, sizeof k, "c%08x", static_cast<unsigned>(c.id));
        if (prefs.isKey(k)) {
            d.last_counter = saved[gw.devices().size() - 1] = prefs.getUInt(k);
            d.seen = true;
        }
        snprintf(k, sizeof k, "t%08x", static_cast<unsigned>(c.id));
        d.tx_counter = prefs.getUInt(k, 0) + 100;
        prefs.putUInt(k, d.tx_counter);
        net_subscribe(topic(c.node, "cmd").c_str());
    }
    LoraPins pins = {LORA_PIN_NSS, LORA_PIN_DIO1, LORA_PIN_RST, LORA_PIN_BUSY, LORA_PIN_SCK, LORA_PIN_MISO, LORA_PIN_MOSI};
    pins.tcxo_v = LORA_TCXO_V;
    radio.begin(pins, lorafmt::channel_mhz(SITE_ID));
}

void lora_gw_loop() {
    uint8_t frame[lorafmt::MAX_FRAME];
    size_t n;
    int rssi;
    float snr;
    if (!radio.receive(frame, n, sizeof frame, rssi, snr)) return;
    const time_t now = time(nullptr);
    std::string tp;
    JsonDocument doc;
    const char* why = nullptr;
    if (!gw.receive(frame, n, now > 1700000000 ? static_cast<double>(now) : -1, true, rssi, snr, tp, doc, &why)) {
        Serial.printf("lora: refused a frame (%s)\n", why ? why : "?");
        return;
    }
    if (doc["node"] == "outdoor") {                       // the heat-pump lockouts, even offline
        JsonVariantConst t = doc["air"]["t"];
        if (t.is<double>()) net_set_outdoor(t.as<double>());
    }
    String out;
    serializeJson(doc, out);
    net_publish(tp.c_str(), out.c_str(), tp.size() > 7 && tp.compare(tp.size() - 7, 7, "/status") == 0);
    for (size_t i = 0; i < gw.devices().size(); i++) {    // replay protection survives a reboot (every 20 frames)
        lorafmt::Device& d = gw.devices()[i];
        if (d.seen && d.last_counter - saved[i] >= 20) {
            char k[12];
            snprintf(k, sizeof k, "c%08x", static_cast<unsigned>(d.id));
            prefs.putUInt(k, d.last_counter);
            saved[i] = d.last_counter;
        }
    }
}

bool lora_gw_on_mqtt(const char* t, const uint8_t* payload, size_t len) {
    const std::string tp(t);
    const std::string prefix = std::string("hvac/") + SITE_ID + "/";
    if (tp.compare(0, prefix.size(), prefix) || tp.size() < 4 || tp.compare(tp.size() - 4, 4, "/cmd")) return false;
    const std::string node = tp.substr(prefix.size(), tp.size() - prefix.size() - 4);
    lorafmt::Device* d = gw.find(SITE_ID, node.c_str());
    if (!d) return false;
    JsonDocument cmd;
    if (deserializeJson(cmd, payload, len)) return true;
    uint8_t frame[lorafmt::MAX_FRAME];
    const char* why = nullptr;
    const size_t n = gw.command(SITE_ID, node.c_str(), cmd.as<JsonVariantConst>(), frame, &why);
    if (!n) {
        JsonDocument reply;
        reply["cmd"] = cmd["cmd"];
        reply["ok"] = false;
        reply["error"] = why ? why : "can't be sent over LoRa";
        String out;
        serializeJson(reply, out);
        net_publish(topic(node, "reply").c_str(), out.c_str(), false);
        return true;
    }
    char k[12];
    snprintf(k, sizeof k, "t%08x", static_cast<unsigned>(d->id));
    prefs.putUInt(k, d->tx_counter);
    radio.send(frame, n);
    return true;
}

#else   // not a gateway

void lora_gw_begin() {}
void lora_gw_loop() {}
bool lora_gw_on_mqtt(const char*, const uint8_t*, size_t) { return false; }

#endif
