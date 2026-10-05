// Plug-in LoRa gateway (Heltec WiFi LoRa 32 V3) for homes that keep their own thermostat.
// Receives the nodes' frames on the site's channel, checks them (lorafmt::Gateway) and publishes
// exactly what the WiFi firmware would have, on the same topics; commands from the cloud
// (hvac/<site>/<node>/cmd) go back to the node over the radio. Its own status, retained, on
// hvac/<site>/gateway/status. See radio/README.md.
#include <Arduino.h>
#include <Preferences.h>
#include <PubSubClient.h>
#include <U8g2lib.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <time.h>

#include "config.h"
#include "lora_gateway.h"
#include "lora_radio.h"

static const char* FW = "0.1.0";
static const LoraPins PINS = {8, 14, 12, 13};                 // Heltec V3: NSS, DIO1, RST, BUSY
static const int PIN_VEXT = 36, PIN_OLED_RST = 21, PIN_OLED_SDA = 17, PIN_OLED_SCL = 18;

struct DeviceCfg { uint32_t id; const char* key; const char* node; };
static const DeviceCfg DEVICES[] = {LORA_DEVICES};

#if MQTT_TLS
WiFiClientSecure tcp;
#else
WiFiClient tcp;
#endif
PubSubClient mqtt(tcp);
Preferences prefs;
U8G2_SSD1306_128X64_NONAME_F_SW_I2C oled(U8G2_R0, PIN_OLED_SCL, PIN_OLED_SDA, PIN_OLED_RST);
LoraRadio radio;
lorafmt::Gateway gw;

struct Stats { uint32_t frames = 0, refused = 0, saved_counter = 0; int rssi = 0; float snr = 0; uint32_t last_ms = 0; };
static Stats stats[sizeof(DEVICES) / sizeof(DEVICES[0])];
static uint32_t refused_unknown = 0, last_status = 0, last_attempt = 0, last_screen = 0;
static const char* last_error = "";

static String topic(const char* node, const char* kind) { return String("hvac/") + SITE_ID + "/" + node + "/" + kind; }

static void save_counters(bool force) {
    // Replay protection keeps the highest counter per node in flash, every 20 frames (a reboot can
    // let at most 20 old frames through once). Our own command counter jumps 100 ahead on boot.
    for (size_t i = 0; i < gw.devices().size(); i++) {
        lorafmt::Device& d = gw.devices()[i];
        if (!d.seen || (!force && d.last_counter - stats[i].saved_counter < 20)) continue;
        char key[12];
        snprintf(key, sizeof key, "c%08x", static_cast<unsigned>(d.id));
        prefs.putUInt(key, d.last_counter);
        stats[i].saved_counter = d.last_counter;
    }
}

static void publish_status() {
    JsonDocument doc;
    doc["online"] = true;
    doc["fw"] = FW;
    doc["ip"] = WiFi.localIP().toString();
    doc["rssi"] = WiFi.RSSI();
    doc["radio"] = radio.ok();
    doc["channel_mhz"] = lorafmt::channel_mhz(SITE_ID);
    doc["refused_unknown"] = refused_unknown;
    JsonObject nodes = doc["nodes"].to<JsonObject>();
    for (size_t i = 0; i < gw.devices().size(); i++) {
        JsonObject n = nodes[gw.devices()[i].node].to<JsonObject>();
        n["frames"] = stats[i].frames;
        n["refused"] = stats[i].refused;
        if (stats[i].frames) {
            n["rssi"] = stats[i].rssi;
            n["snr"] = stats[i].snr;
            n["age_s"] = (millis() - stats[i].last_ms) / 1000;
        }
    }
    String out;
    serializeJson(doc, out);
    mqtt.publish(topic("gateway", "status").c_str(), out.c_str(), true);
}

static void on_mqtt(char* t, byte* payload, unsigned int len) {
    // hvac/<site>/<node>/cmd -> a command frame for that node
    String tp(t);
    const int a = tp.indexOf('/', 5), b = tp.indexOf('/', a + 1);
    if (a < 0 || b < 0) return;
    const String node = tp.substring(a + 1, b);
    JsonDocument cmd;
    if (deserializeJson(cmd, payload, len)) return;
    uint8_t frame[lorafmt::MAX_FRAME];
    const char* why = nullptr;
    const size_t n = gw.command(SITE_ID, node.c_str(), cmd.as<JsonVariantConst>(), frame, &why);
    if (!n) {
        // tell the cloud, in the shape a node's reply has, so the command log shows why
        JsonDocument reply;
        reply["cmd"] = cmd["cmd"];
        reply["ok"] = false;
        reply["error"] = why ? why : "can't be sent over LoRa";
        String out;
        serializeJson(reply, out);
        mqtt.publish(topic(node.c_str(), "reply").c_str(), out.c_str());
        return;
    }
    lorafmt::Device* d = gw.find(SITE_ID, node.c_str());
    char key[12];
    snprintf(key, sizeof key, "t%08x", static_cast<unsigned>(d->id));
    prefs.putUInt(key, d->tx_counter);
    radio.send(frame, n);
}

static void connect_mqtt() {
    if (millis() - last_attempt < 5000 && last_attempt) return;
    last_attempt = millis();
    const String will = topic("gateway", "status");
    const char* user = strlen(MQTT_USER) ? MQTT_USER : nullptr;
    const char* pass = strlen(MQTT_PASS) ? MQTT_PASS : nullptr;
    if (!mqtt.connect((String("lora-gw-") + SITE_ID).c_str(), user, pass, will.c_str(), 1, true, "{\"online\":false}")) return;
    for (const lorafmt::Device& d : gw.devices()) mqtt.subscribe(topic(d.node.c_str(), "cmd").c_str(), 1);
    publish_status();
}

static void screen() {
    oled.clearBuffer();
    oled.setFont(u8g2_font_6x12_tf);
    char line[32];
    snprintf(line, sizeof line, "LoRa gw %s %.1f", SITE_ID, lorafmt::channel_mhz(SITE_ID));
    oled.drawStr(0, 11, line);
    snprintf(line, sizeof line, "%s %s", WiFi.status() == WL_CONNECTED ? "WiFi" : "no WiFi", mqtt.connected() ? "MQTT" : "no MQTT");
    oled.drawStr(0, 23, radio.ok() ? line : "RADIO ERROR");
    int y = 35;
    for (size_t i = 0; i < gw.devices().size() && y <= 59; i++, y += 12) {
        if (stats[i].frames) snprintf(line, sizeof line, "%-7s %lu %ddBm", gw.devices()[i].node.c_str(), static_cast<unsigned long>(stats[i].frames), stats[i].rssi);
        else snprintf(line, sizeof line, "%-7s waiting", gw.devices()[i].node.c_str());
        oled.drawStr(0, y, line);
    }
    oled.sendBuffer();
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_VEXT, OUTPUT);
    digitalWrite(PIN_VEXT, LOW);                 // powers the OLED
    delay(100);
    oled.begin();
    prefs.begin("lora-gw", false);
    for (const DeviceCfg& c : DEVICES) {
        uint8_t key[16];
        if (!lorafmt::parse_key(c.key, key) || c.id == 0) {
            Serial.printf("gateway: device %s has no id/key: run radio/tools/new_device.py\n", c.node);
            continue;
        }
        gw.add(c.id, key, SITE_ID, c.node);
        lorafmt::Device& d = gw.devices().back();
        char k[12];
        snprintf(k, sizeof k, "c%08x", static_cast<unsigned>(c.id));
        if (prefs.isKey(k)) {
            d.last_counter = prefs.getUInt(k);
            d.seen = true;
            stats[gw.devices().size() - 1].saved_counter = d.last_counter;
        }
        snprintf(k, sizeof k, "t%08x", static_cast<unsigned>(c.id));
        d.tx_counter = prefs.getUInt(k, 0) + 100;
        prefs.putUInt(k, d.tx_counter);
    }
    radio.begin(PINS, lorafmt::channel_mhz(SITE_ID));
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
#if MQTT_TLS
    tcp.setCACert(MQTT_CA_CERT);
#endif
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setBufferSize(2048);
    mqtt.setCallback(on_mqtt);
}

void loop() {
    if (WiFi.status() == WL_CONNECTED && !mqtt.connected()) connect_mqtt();
    if (mqtt.connected()) mqtt.loop();

    uint8_t frame[lorafmt::MAX_FRAME];
    size_t n;
    int rssi;
    float snr;
    if (radio.receive(frame, n, sizeof frame, rssi, snr)) {
        const time_t now = time(nullptr);
        const double received_at = now > 1700000000 ? now + (millis() % 1000) / 1000.0 : -1;
        std::string tp;
        JsonDocument doc;
        const char* why = nullptr;
        lorafmt::Device* d = n >= lorafmt::HEADER_LEN ? gw.find(lorafmt::peek_device(frame)) : nullptr;
        const size_t idx = d ? d - gw.devices().data() : 0;
        if (gw.receive(frame, n, received_at, true, rssi, snr, tp, doc, &why)) {
            stats[idx].frames++;
            stats[idx].rssi = rssi;
            stats[idx].snr = snr;
            stats[idx].last_ms = millis();
            String out;
            serializeJson(doc, out);
            const bool retain = tp.size() > 7 && tp.compare(tp.size() - 7, 7, "/status") == 0;
            if (mqtt.connected()) mqtt.publish(tp.c_str(), out.c_str(), retain);
            save_counters(false);
        } else {
            if (d) stats[idx].refused++; else refused_unknown++;
            last_error = why ? why : "?";
            Serial.printf("gateway: refused a frame (%s), %d dBm\n", last_error, rssi);
        }
    }
    if (millis() - last_status > 60000 && mqtt.connected()) {
        last_status = millis();
        save_counters(true);
        publish_status();
    }
    if (millis() - last_screen > 1000) {
        last_screen = millis();
        screen();
    }
    delay(2);
}
