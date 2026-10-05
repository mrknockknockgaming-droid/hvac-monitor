// LoRa frames, format version 1: a C++ port of radio/lorafmt.py (the reference; see its docstring
// and radio/README.md). Tested against frames made by the Python code: pio test -e native.
//
// frame = header (9 bytes, authenticated) | encrypted payload | 8-byte tag
// header = version << 4 | type, device id (uint32 LE), counter (uint32 LE)
// nonce  = device id | counter | header byte 0 | 4 zero bytes
#pragma once
#include <ArduinoJson.h>

#include <cstddef>
#include <cstdint>

namespace lorafmt {

constexpr uint8_t VERSION = 1;
constexpr size_t HEADER_LEN = 9, TAG_LEN = 8, MAX_PAYLOAD = 32, MAX_FRAME = HEADER_LEN + MAX_PAYLOAD + TAG_LEN;
enum Type : uint8_t { OUTDOOR = 1, INDOOR = 2, STATUS = 3, COMMAND = 4, REPLY = 5 };

// ---- payloads <-> the WiFi firmware's JSON
// Telemetry JSON (node "outdoor" or "indoor") -> payload; returns its length (0 if not a reading).
size_t pack_reading(JsonVariantConst doc, uint32_t age_s, uint8_t& type, uint8_t* out);
// payload -> telemetry JSON as the WiFi firmware publishes it (no fw / rssi). received_at < 0: no "ts".
bool unpack_reading(uint8_t type, const uint8_t* p, size_t n, double received_at, JsonDocument& doc);

size_t pack_status(const char* fw, uint32_t interval_ms, uint32_t backlog, uint32_t dropped, uint8_t* out);
bool unpack_status(const uint8_t* p, size_t n, JsonDocument& doc);

// Command JSON -> payload; 0 (and *err) for commands the radio can't carry.
size_t pack_command(JsonVariantConst cmd, uint8_t* out, const char** err = nullptr);
bool unpack_command(const uint8_t* p, size_t n, JsonDocument& doc);

size_t pack_reply(JsonVariantConst reply, uint8_t* out);
bool unpack_reply(const uint8_t* p, size_t n, JsonDocument& doc);

// ---- framing + encryption
size_t seal(const uint8_t key[16], uint32_t device_id, uint32_t counter, uint8_t type, const uint8_t* payload,
            size_t n, uint8_t* frame);
// false for a wrong version, a frame too short, or one that doesn't authenticate
bool open_frame(const uint8_t key[16], const uint8_t* frame, size_t n, uint8_t& type, uint32_t& device_id,
                uint32_t& counter, uint8_t* payload, size_t& payload_len);
uint32_t peek_device(const uint8_t* frame);     // to look up the key before opening
bool parse_key(const char* hex, uint8_t key[16]);   // 32 hex digits

// The site's channel (README "Channel plan"): 903.0 + 1.6 n MHz, n from a FNV-1a hash of the site id.
double channel_mhz(const char* site);

double airtime_ms(size_t payload_len, int sf, double bw_khz, int cr = 1, int preamble = 8);

}  // namespace lorafmt
