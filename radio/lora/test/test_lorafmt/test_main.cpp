// Native tests: pio test -e native (in radio/lora). Every frame, payload and decoded JSON must
// match what radio/lorafmt.py (the reference) produced (tools/make_cpp_vectors.py).
#include <unity.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>

#include "aes_ccm.h"
#include "lora_gateway.h"
#include "lorafmt.h"
#include "vectors.h"

using namespace lorafmt;

void setUp() {}
void tearDown() {}

static size_t unhex(const char* s, uint8_t* out) {
    size_t n = strlen(s) / 2;
    for (size_t i = 0; i < n; i++) {
        unsigned v;
        sscanf(s + 2 * i, "%2x", &v);
        out[i] = static_cast<uint8_t>(v);
    }
    return n;
}

static std::string hex(const uint8_t* p, size_t n) {
    std::string s;
    char b[3];
    for (size_t i = 0; i < n; i++) { snprintf(b, sizeof b, "%02x", p[i]); s += b; }
    return s;
}

// Same JSON? Numbers within a small tolerance (Python and C++ round decimals slightly differently).
static bool same(JsonVariantConst a, JsonVariantConst b, std::string& where, const std::string& path = "") {
    if (a.isNull() || b.isNull()) { if (a.isNull() != b.isNull()) { where = path; return false; } return true; }
    if (a.is<bool>() || b.is<bool>()) { if (a.as<bool>() != b.as<bool>() || a.is<bool>() != b.is<bool>()) { where = path; return false; } return true; }
    if (a.is<double>() && b.is<double>()) {
        if (std::fabs(a.as<double>() - b.as<double>()) > 1e-6 * std::max(1.0, std::fabs(b.as<double>()))) { where = path; return false; }
        return true;
    }
    if (a.is<const char*>() && b.is<const char*>()) { if (strcmp(a.as<const char*>(), b.as<const char*>())) { where = path; return false; } return true; }
    if (a.is<JsonObjectConst>() && b.is<JsonObjectConst>()) {
        JsonObjectConst x = a, y = b;
        if (x.size() != y.size()) { where = path + " (keys)"; return false; }
        for (JsonPairConst kv : y)
            if (!same(x[kv.key()], kv.value(), where, path + "." + kv.key().c_str())) return false;
        return true;
    }
    if (a.is<JsonArrayConst>() && b.is<JsonArrayConst>()) {
        JsonArrayConst x = a, y = b;
        if (x.size() != y.size()) { where = path + " (length)"; return false; }
        for (size_t i = 0; i < x.size(); i++)
            if (!same(x[i], y[i], where, path + "[" + std::to_string(i) + "]")) return false;
        return true;
    }
    where = path + " (type)";
    return false;
}

static void expect_json(JsonDocument& got, const char* expected, const char* what) {
    JsonDocument want;
    TEST_ASSERT_FALSE(deserializeJson(want, expected));
    std::string where;
    if (!same(got.as<JsonVariantConst>(), want.as<JsonVariantConst>(), where)) {
        std::string g;
        serializeJson(got, g);
        std::string msg = std::string(what) + ": differs at " + where + "\n got  " + g + "\n want " + expected;
        TEST_FAIL_MESSAGE(msg.c_str());
    }
}

static void test_aes_fips197() {
    uint8_t key[16], pt[16], ct[16];
    unhex("000102030405060708090a0b0c0d0e0f", key);
    unhex("00112233445566778899aabbccddeeff", pt);
    Aes128(key).encrypt(pt, ct);
    TEST_ASSERT_EQUAL_STRING("69c4e0d86a7b0430d8cdb78070b4c55a", hex(ct, 16).c_str());
}

static void test_readings_match_python() {
    uint8_t key[16];
    unhex(V_KEY, key);
    for (const VReading& v : V_READINGS) {
        JsonDocument doc;
        TEST_ASSERT_FALSE(deserializeJson(doc, v.doc));
        uint8_t payload[MAX_PAYLOAD], frame[MAX_FRAME], type;
        const size_t n = pack_reading(doc.as<JsonVariantConst>(), v.age, type, payload);
        TEST_ASSERT_EQUAL_STRING_MESSAGE(v.payload, hex(payload, n).c_str(), v.doc);
        const size_t fn = seal(key, v.dev, v.counter, type, payload, n, frame);
        TEST_ASSERT_EQUAL_STRING_MESSAGE(v.frame, hex(frame, fn).c_str(), v.doc);
        uint8_t back[MAX_FRAME], t2;
        size_t bn;
        uint32_t dev, counter;
        TEST_ASSERT_TRUE(open_frame(key, frame, fn, t2, dev, counter, back, bn));
        TEST_ASSERT_EQUAL(v.dev, dev);
        TEST_ASSERT_EQUAL(v.counter, counter);
        JsonDocument out;
        TEST_ASSERT_TRUE(unpack_reading(t2, back, bn, V_RECEIVED_AT, out));
        expect_json(out, v.json, v.doc);
    }
}

static void test_status_commands_replies_match_python() {
    for (const VStatus& v : V_STATUS) {
        uint8_t p[MAX_PAYLOAD];
        const size_t n = pack_status(v.fw, v.interval_ms, v.backlog, v.dropped, p);
        TEST_ASSERT_EQUAL_STRING(v.payload, hex(p, n).c_str());
        JsonDocument out;
        TEST_ASSERT_TRUE(unpack_status(p, n, out));
        expect_json(out, v.json, v.fw);
    }
    for (const VCommand& v : V_COMMANDS) {
        JsonDocument cmd;
        deserializeJson(cmd, v.cmd);
        uint8_t p[MAX_PAYLOAD];
        const char* err = nullptr;
        const size_t n = pack_command(cmd.as<JsonVariantConst>(), p, &err);
        if (!*v.payload) {
            TEST_ASSERT_EQUAL_MESSAGE(0, n, v.cmd);
            TEST_ASSERT_NOT_NULL(err);
            continue;
        }
        TEST_ASSERT_EQUAL_STRING_MESSAGE(v.payload, hex(p, n).c_str(), v.cmd);
        JsonDocument out;
        TEST_ASSERT_TRUE(unpack_command(p, n, out));
        expect_json(out, v.json, v.cmd);
    }
    for (const VReply& v : V_REPLIES) {
        JsonDocument r;
        deserializeJson(r, v.reply);
        uint8_t p[MAX_PAYLOAD];
        const size_t n = pack_reply(r.as<JsonVariantConst>(), p);
        TEST_ASSERT_EQUAL_STRING_MESSAGE(v.payload, hex(p, n).c_str(), v.reply);
        JsonDocument out;
        TEST_ASSERT_TRUE(unpack_reply(p, n, out));
        expect_json(out, v.json, v.reply);
    }
}

static void test_gateway_session_matches_python() {
    uint8_t k1[16], k2[16];
    TEST_ASSERT_TRUE(parse_key(V_KEY, k1));
    TEST_ASSERT_TRUE(parse_key(V_KEY2, k2));
    Gateway gw;
    gw.add(0x0A0B0C0D, k1, "home", "outdoor");
    gw.add(0x01020304, k2, "home", "indoor");
    int k = 0;
    for (const VGateway& v : V_GATEWAY) {
        uint8_t frame[MAX_FRAME];
        const size_t n = unhex(v.frame, frame);
        std::string topic;
        JsonDocument doc;
        const char* why = nullptr;
        const bool ok = gw.receive(frame, n, V_RECEIVED_AT + k, true, -97, 7.5f, topic, doc, &why);
        char msg[64];
        snprintf(msg, sizeof msg, "gateway frame %d", k);
        if (!*v.topic) {
            TEST_ASSERT_FALSE_MESSAGE(ok, msg);
        } else {
            TEST_ASSERT_TRUE_MESSAGE(ok, why ? why : msg);
            TEST_ASSERT_EQUAL_STRING_MESSAGE(v.topic, topic.c_str(), msg);
            expect_json(doc, v.json, msg);
        }
        k++;
    }
}

static void test_commands_reach_the_node() {
    uint8_t key[16];
    parse_key(V_KEY, key);
    Gateway gw;
    gw.add(0x0A0B0C0D, key, "home", "outdoor");
    JsonDocument cmd;
    deserializeJson(cmd, R"({"cmd":"cal_span","ch":"p_vap","ref":300})");
    uint8_t frame[MAX_FRAME];
    const char* why = nullptr;
    size_t n = gw.command("home", "outdoor", cmd.as<JsonVariantConst>(), frame, &why);
    TEST_ASSERT_TRUE(n > 0);
    uint8_t p[MAX_FRAME], type;
    size_t pn;
    uint32_t dev, counter;
    TEST_ASSERT_TRUE(open_frame(key, frame, n, type, dev, counter, p, pn));   // what the node does
    TEST_ASSERT_EQUAL(COMMAND, type);
    TEST_ASSERT_EQUAL(1, counter);
    JsonDocument out;
    TEST_ASSERT_TRUE(unpack_command(p, pn, out));
    TEST_ASSERT_EQUAL_STRING("cal_span", out["cmd"]);
    TEST_ASSERT_EQUAL_DOUBLE(300.0, out["ref"].as<double>());
    TEST_ASSERT_EQUAL(0, gw.command("home", "indoor", cmd.as<JsonVariantConst>(), frame, &why));     // not ours
    n = gw.command("home", "outdoor", cmd.as<JsonVariantConst>(), frame, &why);
    TEST_ASSERT_TRUE(open_frame(key, frame, n, type, dev, counter, p, pn));
    TEST_ASSERT_EQUAL(2, counter);                                                           // never reused
}

static void test_airtime_matches_python() {
    TEST_ASSERT_DOUBLE_WITHIN(0.1, 144.4, airtime_ms(12, 9, 125));
    TEST_ASSERT_DOUBLE_WITHIN(0.1, 36.1, airtime_ms(12, 9, 500));
    TEST_ASSERT_DOUBLE_WITHIN(0.5, 1155.1, airtime_ms(12, 12, 125));
    TEST_ASSERT_TRUE(airtime_ms(45, 9, 500) < 100);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_aes_fips197);
    RUN_TEST(test_readings_match_python);
    RUN_TEST(test_status_commands_replies_match_python);
    RUN_TEST(test_gateway_session_matches_python);
    RUN_TEST(test_commands_reach_the_node);
    RUN_TEST(test_airtime_matches_python);
    return UNITY_END();
}
