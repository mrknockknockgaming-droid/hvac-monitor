#include "lorafmt.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>

#include "aes_ccm.h"

namespace lorafmt {
namespace {

constexpr int16_t NULL16 = -32768;
constexpr uint8_t NULL8 = 255;
constexpr uint16_t NULLU16 = 0xFFFF;
constexpr int OTHER_ERR = 15;
const char* const OUTDOOR_ERRS[] = {"ads1", "ads2", "sht30", "rail5v", "p_liq", "p_vap", "p_tsuc", "t_suc", "t_liq", "t_tsuc", "t_dis"};
const char* const INDOOR_ERRS[] = {"ds18b20_missing", "t_sup", "t_ret"};
const char* const MODE_BITS[] = {"Y", "OB", "W", "G"};
const char* const COMMANDS[] = {"status", "reboot", "interval", "cal_zero", "cal_span", "cal_ref", "cal_set", "cal_reset",
                                "range", "fitted", "ntc_b", "v33", "ds_swap", "rescan"};
constexpr int N_COMMANDS = 14;
const char* const CHANNELS[] = {nullptr, "p_liq", "p_vap", "p_tsuc", "t_suc", "t_liq", "t_tsuc", "t_dis", "t_sup", "t_ret"};
constexpr int N_CHANNELS = 10;
const char* const REPLY_ERRORS[] = {"", "unknown command", "unknown channel", "channel has no valid reading",
                                    "need ref and a valid reading", "apply at least ~50 psi before spanning",
                                    "value out of range", "bad frame"};
constexpr int N_REPLY_ERRORS = 8;

// ---- little-endian writer / reader
struct W {
    uint8_t* p;
    size_t n = 0;
    void u8(uint32_t v) { p[n++] = static_cast<uint8_t>(v); }
    void u16(uint32_t v) { u8(v); u8(v >> 8); }
    void i16(int16_t v) { u16(static_cast<uint16_t>(v)); }
    void u32(uint32_t v) { u16(v); u16(v >> 16); }
    void f32(float f) { uint32_t v; memcpy(&v, &f, 4); u32(v); }
};
struct R {
    const uint8_t* p;
    size_t n, i = 0;
    bool ok() const { return i <= n; }
    uint32_t u8() { return i < n ? p[i++] : (i++, 0); }
    uint32_t u16() { uint32_t a = u8(); return a | (u8() << 8); }
    int16_t i16() { return static_cast<int16_t>(u16()); }
    uint32_t u32() { uint32_t a = u16(); return a | (u16() << 16); }
    float f32() { uint32_t v = u32(); float f; memcpy(&f, &v, 4); return f; }
};

bool is_num(JsonVariantConst v) { return (v.is<double>() || v.is<long>()) && !v.is<bool>(); }

// Python's round(): halves go to the even neighbour (the default FP rounding mode)
long pyround(double x) { return std::lrint(x); }

int16_t i16(JsonVariantConst v, double scale = 10) {
    if (!is_num(v)) return NULL16;
    const long r = pyround(v.as<double>() * scale);
    return static_cast<int16_t>(r < -32767 ? -32767 : r > 32767 ? 32767 : r);
}

void f16(JsonObject o, const char* key, int16_t n, double scale = 10) {
    if (n == NULL16) o[key] = nullptr;
    else o[key] = std::round(n / scale * 10) / 10;
}

int index_of(const char* const* names, int count, const char* s) {
    if (!s) return -1;
    for (int i = 0; i < count; i++)
        if (names[i] && !strcmp(names[i], s)) return i;
    return -1;
}

uint16_t err_bits(const char* const* names, int count, JsonArrayConst errs) {
    uint16_t out = 0;
    for (JsonVariantConst e : errs) {
        const int i = index_of(names, count, e.as<const char*>());
        out |= static_cast<uint16_t>(1u << (i >= 0 ? i : OTHER_ERR));
    }
    return out;
}

void unbits(JsonArray arr, const char* const* names, int count, uint32_t v) {
    for (int i = 0; i < count; i++)
        if (v >> i & 1) arr.add(names[i]);
    if (v >> OTHER_ERR & 1) arr.add("other");
}

uint8_t modes(JsonObjectConst mode) {
    uint8_t out = 0;
    for (int i = 0; i < 4; i++)
        if (mode[MODE_BITS[i]].as<bool>()) out |= static_cast<uint8_t>(1 << i);
    return out;
}

void unmodes(JsonObject mode, uint32_t bits, std::initializer_list<const char*> keys) {
    for (const char* k : keys) mode[k] = static_cast<bool>(bits >> index_of(MODE_BITS, 4, k) & 1);
}

double round_to(double v, int places) {
    const double f = std::pow(10.0, places);
    return std::round(v * f) / f;
}

void nonce(uint8_t out[13], uint8_t byte0, uint32_t dev, uint32_t counter) {
    W w{out};
    w.u32(dev);
    w.u32(counter);
    w.u8(byte0);
    memset(out + 9, 0, 4);
}
}  // namespace

// ------------------------------------------------------------------ readings
size_t pack_reading(JsonVariantConst doc, uint32_t age_s, uint8_t& type, uint8_t* out) {
    const char* node = doc["node"] | "";
    W w{out};
    const uint32_t age = age_s > 65535 ? 65535 : age_s;
    if (!strcmp(node, "outdoor")) {
        type = OUTDOOR;
        JsonVariantConst p = doc["p"], t = doc["t"], air = doc["air"];
        w.u16(age);
        w.u8(modes(doc["mode"]));
        w.u16(err_bits(OUTDOOR_ERRS, 11, doc["err"]));
        w.i16(i16(p["liq"])); w.i16(i16(p["vap"])); w.i16(i16(p["tsuc"]));
        w.i16(i16(t["suc"])); w.i16(i16(t["liq"])); w.i16(i16(t["tsuc"])); w.i16(i16(t["dis"]));
        w.i16(i16(air["t"]));
        JsonVariantConst rh = air["rh"], v5 = doc["v5"];
        if (!is_num(rh)) w.u16(NULLU16);
        else { long r = pyround(rh.as<double>() * 10); w.u16(r < 0 ? 0 : r > 1000 ? 1000 : r); }
        if (!is_num(v5)) w.u8(NULL8);
        else { long r = pyround((v5.as<double>() - 4.0) * 100); w.u8(r < 0 ? 0 : r > 254 ? 254 : r); }
        w.u32(is_num(doc["uptime"]) ? static_cast<uint32_t>(doc["uptime"].as<double>()) : 0);
        return w.n;
    }
    if (!strcmp(node, "indoor")) {
        type = INDOOR;
        JsonVariantConst air = doc["air"];
        w.u16(age);
        w.u8(modes(doc["mode"]));
        w.u16(err_bits(INDOOR_ERRS, 3, doc["err"]));
        w.i16(i16(air["supply"]));
        w.i16(i16(air["return"]));
        w.u32(is_num(doc["uptime"]) ? static_cast<uint32_t>(doc["uptime"].as<double>()) : 0);
        return w.n;
    }
    return 0;
}

bool unpack_reading(uint8_t type, const uint8_t* p, size_t n, double received_at, JsonDocument& doc) {
    R r{p, n};
    doc.clear();
    uint32_t age;
    if (type == OUTDOOR && n == 28) {
        age = r.u16();
        const uint32_t m = r.u8(), errs = r.u16();
        const int16_t pl = r.i16(), pv = r.i16(), pt = r.i16(), ts = r.i16(), tl = r.i16(), tt = r.i16(), td = r.i16(), at = r.i16();
        const uint32_t rh = r.u16(), v5 = r.u8(), up = r.u32();
        doc["node"] = "outdoor";
        doc["uptime"] = up;
        unmodes(doc["mode"].to<JsonObject>(), m, {"Y", "OB"});
        JsonObject po = doc["p"].to<JsonObject>();
        f16(po, "liq", pl); f16(po, "vap", pv); f16(po, "tsuc", pt);
        JsonObject to = doc["t"].to<JsonObject>();
        f16(to, "suc", ts); f16(to, "liq", tl); f16(to, "tsuc", tt); f16(to, "dis", td);
        JsonObject air = doc["air"].to<JsonObject>();
        f16(air, "t", at);
        if (rh == NULLU16) air["rh"] = nullptr; else air["rh"] = rh / 10.0;
        if (v5 == NULL8) doc["v5"] = nullptr; else doc["v5"] = round_to(4.0 + v5 / 100.0, 2);
        unbits(doc["err"].to<JsonArray>(), OUTDOOR_ERRS, 11, errs);
    } else if (type == INDOOR && n == 13) {
        age = r.u16();
        const uint32_t m = r.u8(), errs = r.u16();
        const int16_t sup = r.i16(), ret = r.i16();
        const uint32_t up = r.u32();
        doc["node"] = "indoor";
        doc["uptime"] = up;
        unmodes(doc["mode"].to<JsonObject>(), m, {"Y", "W", "G", "OB"});
        JsonObject air = doc["air"].to<JsonObject>();
        f16(air, "supply", sup);
        f16(air, "return", ret);
        if (sup == NULL16 || ret == NULL16) air["dt"] = nullptr;
        else air["dt"] = round_to(ret / 10.0 - sup / 10.0, 1);
        unbits(doc["err"].to<JsonArray>(), INDOOR_ERRS, 3, errs);
    } else {
        return false;
    }
    if (received_at >= 0) doc["ts"] = round_to(received_at - age, 3);
    return r.ok();
}

// ------------------------------------------------------------------ status
size_t pack_status(const char* fw, uint32_t interval_ms, uint32_t backlog, uint32_t dropped, uint8_t* out) {
    int a = 0, b = 0, c = 0;
    if (sscanf(fw ? fw : "", "%d.%d.%d", &a, &b, &c) != 3) a = b = c = 0;
    W w{out};
    w.u8(a); w.u8(b); w.u8(c);
    w.u16(interval_ms / 1000 > 65535 ? 65535 : interval_ms / 1000);
    w.u8(backlog > 255 ? 255 : backlog);
    w.u16(dropped > 65535 ? 65535 : dropped);
    return w.n;
}

bool unpack_status(const uint8_t* p, size_t n, JsonDocument& doc) {
    if (n != 8) return false;
    R r{p, n};
    const uint32_t a = r.u8(), b = r.u8(), c = r.u8(), iv = r.u16(), backlog = r.u8(), dropped = r.u16();
    char fw[16];
    snprintf(fw, sizeof fw, "%u.%u.%u", static_cast<unsigned>(a), static_cast<unsigned>(b), static_cast<unsigned>(c));
    doc.clear();
    doc["online"] = true;
    doc["fw"] = fw;
    doc["interval_ms"] = iv * 1000;
    doc["backlog"] = backlog;
    doc["dropped"] = dropped;
    doc["link"] = "lora";
    return true;
}

// ------------------------------------------------------------------ commands + replies
size_t pack_command(JsonVariantConst cmd, uint8_t* out, const char** err) {
    const char* name = cmd["cmd"] | "";
    const int c = index_of(COMMANDS, N_COMMANDS, name);
    if (c < 0) { if (err) *err = "can't be sent over LoRa"; return 0; }
    int ch = 0;
    if (!cmd["ch"].isNull()) {
        ch = index_of(CHANNELS, N_CHANNELS, cmd["ch"].as<const char*>());
        if (ch < 1) { if (err) *err = "unknown channel"; return 0; }
    }
    double value = 0;
    uint8_t flag = 0;
    const std::string n(name);
    if (n == "interval") value = cmd["ms"].as<double>() / 1000;
    else if (n == "cal_span" || n == "cal_ref") value = cmd["ref"].as<double>();
    else if (n == "range") value = cmd["bar"].as<double>();
    else if (n == "ntc_b" || n == "v33") value = cmd["value"].as<double>();
    else if (n == "fitted") flag = cmd["on"].as<bool>() ? 1 : 0;
    else if (n == "cal_set") {
        if (!cmd["scale"].isUnbound()) { if (err) *err = "cal_set over LoRa takes offset only"; return 0; }
        value = cmd["offset"].as<double>();
    }
    W w{out};
    w.u8(c);
    w.u8(ch);
    w.f32(static_cast<float>(value));
    w.u8(flag);
    return w.n;
}

bool unpack_command(const uint8_t* p, size_t n, JsonDocument& doc) {
    if (n != 7) return false;
    R r{p, n};
    const uint32_t c = r.u8(), ch = r.u8();
    const double value = round_to(r.f32(), 4);
    const uint32_t flag = r.u8();
    if (c >= N_COMMANDS || ch >= N_CHANNELS) return false;
    doc.clear();
    const std::string name = COMMANDS[c];
    doc["cmd"] = COMMANDS[c];
    if (CHANNELS[ch]) doc["ch"] = CHANNELS[ch];
    if (name == "interval") doc["ms"] = static_cast<long>(std::lround(value * 1000));
    else if (name == "cal_span" || name == "cal_ref") doc["ref"] = value;
    else if (name == "range") doc["bar"] = value;
    else if (name == "ntc_b" || name == "v33") doc["value"] = value;
    else if (name == "fitted") doc["on"] = flag != 0;
    else if (name == "cal_set") doc["offset"] = value;
    return true;
}

size_t pack_reply(JsonVariantConst reply, uint8_t* out) {
    const int c = index_of(COMMANDS, N_COMMANDS, reply["cmd"] | "");
    const char* e = reply["error"] | "";
    int code = index_of(REPLY_ERRORS, N_REPLY_ERRORS, e);
    if (code < 0) code = N_REPLY_ERRORS - 1;
    double first = 0;
    for (const char* k : {"offset", "ms", "ntc_b", "v33", "fs_bar"}) {      // the first one present
        if (!reply[k].isUnbound()) { first = reply[k].as<double>(); break; }
    }
    W w{out};
    w.u8(c >= 0 ? c : 255);
    w.u8(reply["ok"].as<bool>() ? 1 : 0);
    w.u8(code);
    w.f32(static_cast<float>(first));
    w.f32(static_cast<float>(reply["scale"].isUnbound() ? 1.0 : reply["scale"].as<double>()));
    return w.n;
}

bool unpack_reply(const uint8_t* p, size_t n, JsonDocument& doc) {
    if (n != 11) return false;
    R r{p, n};
    const uint32_t c = r.u8(), ok = r.u8(), code = r.u8();
    const double first = r.f32(), scale = r.f32();
    doc.clear();
    if (c < N_COMMANDS) doc["cmd"] = COMMANDS[c]; else doc["cmd"] = nullptr;
    doc["ok"] = ok != 0;
    if (code) doc["error"] = code < N_REPLY_ERRORS ? REPLY_ERRORS[code] : "bad frame";
    if (c < N_COMMANDS && ok) {
        const std::string name = COMMANDS[c];
        if (name.rfind("cal_", 0) == 0) {
            doc["offset"] = round_to(first, 4);
            doc["scale"] = round_to(scale, 6);
        }
    }
    return true;
}

// ------------------------------------------------------------------ framing
size_t seal(const uint8_t key[16], uint32_t dev, uint32_t counter, uint8_t type, const uint8_t* payload, size_t n,
            uint8_t* frame) {
    const uint8_t byte0 = static_cast<uint8_t>(VERSION << 4 | (type & 0x0F));
    W w{frame};
    w.u8(byte0);
    w.u32(dev);
    w.u32(counter);
    uint8_t nc[13];
    nonce(nc, byte0, dev, counter);
    ccm_encrypt(key, nc, frame, HEADER_LEN, payload, n, frame + HEADER_LEN);
    return HEADER_LEN + n + TAG_LEN;
}

bool open_frame(const uint8_t key[16], const uint8_t* frame, size_t n, uint8_t& type, uint32_t& dev, uint32_t& counter,
                uint8_t* payload, size_t& payload_len) {
    if (n < HEADER_LEN + TAG_LEN || n > MAX_FRAME) return false;
    R r{frame, n};
    const uint8_t byte0 = static_cast<uint8_t>(r.u8());
    if (byte0 >> 4 != VERSION) return false;
    const uint32_t d = r.u32(), c = r.u32();
    uint8_t nc[13];
    nonce(nc, byte0, d, c);
    if (!ccm_decrypt(key, nc, frame, HEADER_LEN, frame + HEADER_LEN, n - HEADER_LEN, payload)) return false;
    type = byte0 & 0x0F;
    dev = d;
    counter = c;
    payload_len = n - HEADER_LEN - TAG_LEN;
    return true;
}

uint32_t peek_device(const uint8_t* frame) {
    return frame[1] | (frame[2] << 8) | (frame[3] << 16) | (static_cast<uint32_t>(frame[4]) << 24);
}

double airtime_ms(size_t payload_len, int sf, double bw_khz, int cr, int preamble) {
    const double tsym = std::pow(2.0, sf) / (bw_khz * 1000.0);
    const int ldro = tsym > 0.016 ? 1 : 0;
    const double num = 8.0 * payload_len - 4.0 * sf + 28 + 16;
    const double symbols = 8 + std::max(std::ceil(num / (4.0 * (sf - 2 * ldro))) * (cr + 4), 0.0);
    return ((preamble + 4.25) + symbols) * tsym * 1000.0;
}

}  // namespace lorafmt
