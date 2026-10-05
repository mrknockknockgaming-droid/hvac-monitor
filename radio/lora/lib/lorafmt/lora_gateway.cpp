#include "lora_gateway.h"

#include <cstdio>
#include <cstring>

namespace lorafmt {

void Gateway::add(uint32_t id, const uint8_t key[16], const char* site, const char* node) {
    Device d;
    d.id = id;
    memcpy(d.key, key, 16);
    d.site = site;
    d.node = node;
    devices_.push_back(d);
}

Device* Gateway::find(uint32_t id) {
    for (Device& d : devices_)
        if (d.id == id) return &d;
    return nullptr;
}

Device* Gateway::find(const char* site, const char* node) {
    for (Device& d : devices_)
        if (d.site == site && d.node == node) return &d;
    return nullptr;
}

bool Gateway::receive(const uint8_t* frame, size_t n, double received_at, bool has_radio, int rssi, float snr,
                      std::string& topic, JsonDocument& doc, const char** why) {
    auto fail = [&](const char* w) { if (why) *why = w; return false; };
    if (n < HEADER_LEN + TAG_LEN) return fail("frame too short");
    Device* dev = find(peek_device(frame));
    if (!dev) return fail("unknown device");
    uint8_t payload[MAX_FRAME], type;
    size_t plen;
    uint32_t id, counter;
    if (!open_frame(dev->key, frame, n, type, id, counter, payload, plen)) return fail("authentication failed");
    if (dev->seen && counter <= dev->last_counter) return fail("replayed or repeated frame");
    const std::string base = "hvac/" + dev->site + "/" + dev->node + "/";
    if (type == OUTDOOR || type == INDOOR) {
        if (!unpack_reading(type, payload, plen, received_at, doc)) return fail("bad reading");
        if (has_radio) {
            JsonObject radio = doc["radio"].to<JsonObject>();
            radio["rssi"] = rssi;
            radio["snr"] = snr;
        }
        topic = base + "telemetry";
    } else if (type == STATUS) {
        if (!unpack_status(payload, plen, doc)) return fail("bad status");
        topic = base + "status";
    } else if (type == REPLY) {
        if (!unpack_reply(payload, plen, doc)) return fail("bad reply");
        topic = base + "reply";
    } else {
        return fail("nodes don't send this frame type");
    }
    dev->seen = true;                     // only now: a frame we couldn't use doesn't move the counter
    dev->last_counter = counter;
    return true;
}

size_t Gateway::command(const char* site, const char* node, JsonVariantConst cmd, uint8_t* frame, const char** why) {
    Device* dev = find(site, node);
    if (!dev) { if (why) *why = "not a LoRa node of this gateway"; return 0; }
    uint8_t payload[MAX_PAYLOAD];
    const size_t n = pack_command(cmd, payload, why);
    if (!n) return 0;
    return seal(dev->key, dev->id, ++dev->tx_counter, COMMAND, payload, n, frame);
}

bool parse_key(const char* hex, uint8_t key[16]) {
    if (!hex || strlen(hex) != 32) return false;
    for (int i = 0; i < 16; i++) {
        unsigned v;
        if (sscanf(hex + 2 * i, "%2x", &v) != 1) return false;
        key[i] = static_cast<uint8_t>(v);
    }
    return true;
}

}  // namespace lorafmt
