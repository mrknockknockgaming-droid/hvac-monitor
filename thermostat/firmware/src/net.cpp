#include "net.h"

#include <Preferences.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <time.h>

#include "board.h"
#include "config.h"
#include "tstat_json.h"

namespace {
#if MQTT_TLS
WiFiClientSecure tcp;
#else
WiFiClient tcp;
#endif
PubSubClient mqtt(tcp);
Preferences prefs;
NetState state;
Feed feed;
uint32_t feed_ver = 0;
ConfigHandler on_config = nullptr;
int current_ver = 0;
double outdoor_at = -1e9;
uint32_t last_attempt = 0;
JsonDocument pending;           // unsent change from the screen
uint32_t request_id = 0;
uint32_t changed_at = 0;        // a burst of taps (- - -) is sent as one request, 1.5 s after the last

String topic(const char* node, const char* kind) { return String("hvac/") + SITE_ID + "/" + node + "/" + kind; }

// IANA names the cloud uses -> POSIX TZ strings for the C library (US zones; others fall back to UTC)
const char* posix_tz(const std::string& iana) {
    static const char* const map[][2] = {
        {"America/Phoenix", "MST7"},
        {"America/Denver", "MST7MDT,M3.2.0,M11.1.0"},
        {"America/Boise", "MST7MDT,M3.2.0,M11.1.0"},
        {"America/Chicago", "CST6CDT,M3.2.0,M11.1.0"},
        {"America/New_York", "EST5EDT,M3.2.0,M11.1.0"},
        {"America/Detroit", "EST5EDT,M3.2.0,M11.1.0"},
        {"America/Indiana/Indianapolis", "EST5EDT,M3.2.0,M11.1.0"},
        {"America/Los_Angeles", "PST8PDT,M3.2.0,M11.1.0"},
        {"America/Anchorage", "AKST9AKDT,M3.2.0,M11.1.0"},
        {"Pacific/Honolulu", "HST10"},
        {"UTC", "UTC0"},
    };
    for (auto& m : map)
        if (iana == m[0]) return m[1];
    Serial.printf("net: unknown time zone %s, using UTC\n", iana.c_str());
    return "UTC0";
}

void on_message(char* t, byte* payload, unsigned int len) {
    const String tp(t);
    if (tp.endsWith("/thermostat/config")) {
        int ver = 0;
        tstat::Settings s;
        tstat::Tech x;
        if (len == 0 || !tstat::parse_config(reinterpret_cast<const char*>(payload), len, ver, s, x)) return;
        if (ver <= current_ver) return;          // ours is as new or newer (e.g. an edit not yet sent)
        net_save(ver, s, x);
        if (on_config) on_config(ver, s, x);
    } else if (tp.endsWith("/thermostat/display")) {
        if (len && feed_parse(reinterpret_cast<const char*>(payload), len, feed)) feed_ver++;
    } else if (tp.endsWith("/outdoor/telemetry")) {
        JsonDocument doc;
        if (deserializeJson(doc, payload, len)) return;
        JsonVariantConst v = doc["air"]["t"];
        if (v.is<double>() || v.is<long>()) {
            state.outdoor = v.as<double>();
            outdoor_at = millis() / 1000.0;
        }
    }
}

void connect() {
    if (millis() - last_attempt < 5000 && last_attempt) return;
    last_attempt = millis();
    const String status = topic("thermostat", "status");
    const String id = String("tstat-") + SITE_ID + "-" + String(static_cast<uint32_t>(ESP.getEfuseMac()), HEX);
    const char* user = strlen(MQTT_USER) ? MQTT_USER : nullptr;
    const char* pass = strlen(MQTT_PASS) ? MQTT_PASS : nullptr;
    if (!mqtt.connect(id.c_str(), user, pass, status.c_str(), 1, true, "{\"online\":false}")) return;
    JsonDocument doc;
    doc["online"] = true;
    doc["node"] = "thermostat";
    doc["fw"] = FW_VERSION;
    doc["ip"] = WiFi.localIP().toString();
    doc["rssi"] = WiFi.RSSI();
    doc["cfg_ver"] = current_ver;
    String out;
    serializeJson(doc, out);
    mqtt.publish(status.c_str(), out.c_str(), true);
    mqtt.subscribe(topic("thermostat", "config").c_str(), 1);
    mqtt.subscribe(topic("thermostat", "display").c_str(), 1);
    mqtt.subscribe(topic("outdoor", "telemetry").c_str(), 0);
    Serial.println("net: MQTT connected");
}
}  // namespace

void net_begin(ConfigHandler handler) {
    on_config = handler;
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
#if MQTT_TLS
    tcp.setCACert(MQTT_CA_CERT);
#endif
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setBufferSize(16384);                   // the display feed is ~6 KB
    mqtt.setKeepAlive(30);
    mqtt.setCallback(on_message);
}

void net_loop() {
    state.wifi = WiFi.status() == WL_CONNECTED;
    if (state.wifi && !mqtt.connected()) connect();
    state.mqtt = mqtt.connected();
    if (state.mqtt) {
        mqtt.loop();
        if (pending.size() && millis() - changed_at >= 1500) {
            pending["id"] = ++request_id;
            pending["ver"] = current_ver;
            String out;
            serializeJson(pending, out);
            if (mqtt.publish(topic("thermostat", "request").c_str(), out.c_str())) pending.clear();
        }
    }
    state.clock_ok = time(nullptr) > 1700000000;
    if (millis() / 1000.0 - outdoor_at > 600) state.outdoor = NAN;
}

const NetState& net_state() { return state; }
const Feed& net_feed() { return feed; }
uint32_t net_feed_version() { return feed_ver; }

bool net_load(int& ver, tstat::Settings& s, tstat::Tech& t) {
    prefs.begin("tstat", true);
    const String json = prefs.getString("cfg", "");
    prefs.end();
    if (!json.length() || !tstat::parse_config(json.c_str(), json.length(), ver, s, t)) return false;
    current_ver = ver;
    return true;
}

void net_save(int ver, const tstat::Settings& s, const tstat::Tech& t) {
    JsonDocument doc;
    tstat::config_to_json(doc, ver, s, t);
    String json;
    serializeJson(doc, json);
    prefs.begin("tstat", false);
    prefs.putString("cfg", json);
    prefs.end();
    current_ver = ver;
}

void net_set_tz(const std::string& iana) {
    configTzTime(posix_tz(iana), "pool.ntp.org", "time.nist.gov");
}

bool net_local_time(tstat::LocalTime& out) {
    if (!state.clock_ok) return false;
    const time_t now = time(nullptr);
    struct tm tm;
    localtime_r(&now, &tm);
    out.year = tm.tm_year + 1900;
    out.mon = tm.tm_mon + 1;
    out.day = tm.tm_mday;
    out.hour = tm.tm_hour;
    out.min = tm.tm_min;
    out.sec = tm.tm_sec;
    out.wday = (tm.tm_wday + 6) % 7;             // Monday = 0, as the cloud writes schedules
    return true;
}

double net_epoch() { return state.clock_ok ? static_cast<double>(time(nullptr)) : millis() / 1000.0; }

void net_publish_report(JsonDocument& doc) {
    if (!state.mqtt) return;
    String out;
    serializeJson(doc, out);
    mqtt.publish(topic("thermostat", "telemetry").c_str(), out.c_str());
}

bool net_pending() { return pending.size() > 0; }

void net_request(const JsonDocument& change) {
    changed_at = millis();
    for (JsonPairConst kv : change.as<JsonObjectConst>()) {
        if (!strcmp(kv.key().c_str(), "resume")) pending.remove("hold");
        if (!strcmp(kv.key().c_str(), "hold")) pending.remove("resume");
        pending[kv.key()] = kv.value();
    }
}
