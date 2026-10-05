// What a gateway does with LoRa frames (port of lorafmt.Gateway): look up the device's key,
// authenticate, refuse replays, and turn each frame into the topic + JSON the WiFi firmware would
// have published, so the cloud can't tell the difference. And the other way: commands from MQTT
// become frames for the node. No radio or network code here; the firmware supplies those.
#pragma once
#include <ArduinoJson.h>

#include <string>
#include <vector>

#include "lorafmt.h"

namespace lorafmt {

struct Device {
    uint32_t id;
    uint8_t key[16];
    std::string site, node;
    bool seen = false;
    uint32_t last_counter = 0;      // highest counter accepted from it (keep in flash)
    uint32_t tx_counter = 0;        // our counter for commands to it (keep in flash)
};

class Gateway {
public:
    void add(uint32_t id, const uint8_t key[16], const char* site, const char* node);
    std::vector<Device>& devices() { return devices_; }
    Device* find(uint32_t id);
    Device* find(const char* site, const char* node);

    // A received frame -> (topic, JSON). false + why for unknown devices, failed authentication,
    // replays, and frame types nodes don't send.
    bool receive(const uint8_t* frame, size_t n, double received_at, bool has_radio, int rssi, float snr,
                 std::string& topic, JsonDocument& doc, const char** why);

    // A command for site/node (from hvac/<site>/<node>/cmd) -> a frame to transmit; 0 + why if the
    // node isn't a LoRa node here or the command can't go over the radio.
    size_t command(const char* site, const char* node, JsonVariantConst cmd, uint8_t* frame, const char** why);

private:
    std::vector<Device> devices_;
};


}  // namespace lorafmt
