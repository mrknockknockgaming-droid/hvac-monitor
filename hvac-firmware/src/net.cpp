#include "net.h"
#include "common.h"
#include "config.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoOTA.h>
#include <deque>
#include <sys/time.h>

// Older config.h files don't have these: plain MQTT, as before.
#ifndef MQTT_TLS
#define MQTT_TLS 0
#endif

#if MQTT_TLS
  // config.h must also have MQTT_CA_CERT (the server's tls/ca.crt), see config.example.h
  static WiFiClientSecure netClient;   // checks the broker's certificate and name against our CA
#else
  static WiFiClient netClient;
#endif
static PubSubClient mqtt(netClient);
static CmdHandler   cmdHandler = nullptr;
static StatusFiller statusFiller = nullptr;

static String tTele, tStatus, tCmd, tReply, clientId;
static uint32_t lastWifiTry = 0, lastMqttTry = 0, wifiDownSince = 0, wifiUpSince = 0;
static bool otaReady = false, ntpStarted = false, wifiWasUp = false;

static const uint32_t WIFI_RETRY_MS     = 10000;
static const uint32_t MQTT_RETRY_MS     = 5000;
static const uint32_t WIFI_REBOOT_MS    = 5UL * 60UL * 1000UL;  // reboot after 5 min without WiFi
static const uint32_t TLS_CLOCK_WAIT_MS = 15000;                // TLS needs the date; give NTP this long

// Readings taken while the broker can't be reached, sent oldest first once it can.
static const size_t   BACKLOG_MAX   = 120;                      // 10 min at the default 5 s
static const size_t   BACKLOG_BURST = 10;                       // per loop, so commands still get through
static std::deque<String> backlog;
static uint32_t dropped = 0;

static void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument cmd, reply;
  DeserializationError err = deserializeJson(cmd, payload, len);
  if (err) {
    reply["ok"] = false;
    reply["error"] = String("bad JSON: ") + err.c_str();
  } else {
    reply["cmd"] = cmd["cmd"];
    if (cmdHandler) cmdHandler(cmd, reply);
  }
  String out;
  serializeJson(reply, out);
  mqtt.publish(tReply.c_str(), out.c_str());
  Serial.printf("[cmd] %s\n", out.c_str());
}

static void setupOta() {
  ArduinoOTA.setHostname(("hvac-" NODE_NAME));
  ArduinoOTA.setPassword(OTA_PASS);
  ArduinoOTA.onStart([]() { Serial.println("[ota] update starting"); });
  ArduinoOTA.onEnd([]()   { Serial.println("[ota] update done, rebooting"); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("[ota] error %u\n", e); });
  ArduinoOTA.begin();
  otaReady = true;
}

bool netTimeValid() {
  return time(nullptr) > 1704067200;          // after 2024-01-01: NTP has answered
}

double netEpoch() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return tv.tv_sec + tv.tv_usec / 1e6;
}

static void mqttConnect() {
#if MQTT_TLS
  // Certificate dates can't be checked before the clock is set; wait for NTP a while first.
  if (!netTimeValid() && millis() - wifiUpSince < TLS_CLOCK_WAIT_MS) return;
#endif
  String will = "{\"online\":false}";
  bool ok;
  if (strlen(MQTT_USER) > 0)
    ok = mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS, tStatus.c_str(), 1, true, will.c_str());
  else
    ok = mqtt.connect(clientId.c_str(), nullptr, nullptr, tStatus.c_str(), 1, true, will.c_str());
  if (ok) {
    Serial.printf("[mqtt] connected%s, %u readings to catch up\n", MQTT_TLS ? " (TLS)" : "", (unsigned)backlog.size());
    mqtt.subscribe(tCmd.c_str(), 1);
    netPublishStatus();
  } else {
    Serial.printf("[mqtt] connect failed, state %d\n", mqtt.state());
#if MQTT_TLS
    char buf[100];
    if (netClient.lastError(buf, sizeof(buf))) Serial.printf("[mqtt] TLS: %s\n", buf);
#endif
  }
}

static void flushBacklog() {
  for (size_t n = 0; n < BACKLOG_BURST && !backlog.empty() && mqtt.connected(); n++) {
    if (!mqtt.publish(tTele.c_str(), backlog.front().c_str())) return;   // try again next loop
    backlog.pop_front();
  }
}

void netBegin(CmdHandler onCmd, StatusFiller onStatus) {
  cmdHandler = onCmd;
  statusFiller = onStatus;
  String base = String("hvac/") + SITE_ID + "/" + NODE_NAME + "/";
  tTele = base + "telemetry"; tStatus = base + "status";
  tCmd  = base + "cmd";       tReply  = base + "reply";
  clientId = String("hvac-") + NODE_NAME + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(("hvac-" NODE_NAME));
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  lastWifiTry = millis();
  wifiDownSince = millis();

#if MQTT_TLS
  netClient.setCACert(MQTT_CA_CERT);
  netClient.setHandshakeTimeout(15);
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(1024);
  mqtt.setKeepAlive(30);
  mqtt.setSocketTimeout(10);
  Serial.printf("[net] connecting to %s; broker %s:%d%s\n", WIFI_SSID, MQTT_HOST, MQTT_PORT, MQTT_TLS ? " (TLS)" : "");
}

void netLoop() {
  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    wifiWasUp = false;
    if (now - lastWifiTry > WIFI_RETRY_MS) {
      lastWifiTry = now;
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      Serial.println("[net] WiFi retry");
    }
    if (now - wifiDownSince > WIFI_REBOOT_MS) {
      Serial.println("[net] offline too long, rebooting");
      delay(100);
      ESP.restart();
    }
    return;
  }
  wifiDownSince = now;
  if (!wifiWasUp) { wifiWasUp = true; wifiUpSince = now; }
  if (!ntpStarted) {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");   // UTC; timestamps readings, and TLS needs it
    ntpStarted = true;
  }
  if (!otaReady) {
    Serial.printf("[net] WiFi up, IP %s, RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
    setupOta();
  }
  ArduinoOTA.handle();

  if (!mqtt.connected()) {
    if (now - lastMqttTry > MQTT_RETRY_MS) {
      lastMqttTry = now;
      mqttConnect();
    }
    return;
  }
  mqtt.loop();
  flushBacklog();
}

bool netPublishTelemetry(const String& json) {
  if (mqtt.connected() && backlog.empty() && mqtt.publish(tTele.c_str(), json.c_str())) return true;
  // Keep it for later, but only with a timestamp: without one the cloud can't place it in time.
  if (!netTimeValid()) return false;
  if (backlog.size() >= BACKLOG_MAX) { backlog.pop_front(); dropped++; }
  backlog.push_back(json);
  return false;
}

size_t netBacklog() { return backlog.size(); }

void netPublishStatus() {
  if (!mqtt.connected()) return;
  JsonDocument doc;
  doc["online"] = true;
  doc["node"] = NODE_NAME;
  doc["fw"] = FW_VERSION;
  doc["ip"] = WiFi.localIP().toString();
  doc["mac"] = WiFi.macAddress();
  doc["rssi"] = WiFi.RSSI();
  doc["tls"] = (bool)MQTT_TLS;
  doc["backlog"] = backlog.size();
  doc["dropped"] = dropped;
  if (statusFiller) statusFiller(doc);
  String out;
  serializeJson(doc, out);
  mqtt.publish(tStatus.c_str(), out.c_str(), true);
}

NetState netState() {
  if (WiFi.status() != WL_CONNECTED) return NET_NO_WIFI;
  if (!mqtt.connected()) return NET_NO_MQTT;
  return NET_OK;
}
