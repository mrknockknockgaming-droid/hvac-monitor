#include "net.h"
#include "common.h"
#include "config.h"
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoOTA.h>

static WiFiClient   wifiClient;
static PubSubClient mqtt(wifiClient);
static CmdHandler   cmdHandler = nullptr;
static StatusFiller statusFiller = nullptr;

static String tTele, tStatus, tCmd, tReply, clientId;
static uint32_t lastWifiTry = 0, lastMqttTry = 0, wifiDownSince = 0;
static bool otaReady = false;

static const uint32_t WIFI_RETRY_MS   = 10000;
static const uint32_t MQTT_RETRY_MS   = 5000;
static const uint32_t WIFI_REBOOT_MS  = 5UL * 60UL * 1000UL;   // reboot after 5 min offline

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

static void mqttConnect() {
  String will = "{\"online\":false}";
  bool ok;
  if (strlen(MQTT_USER) > 0)
    ok = mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASS, tStatus.c_str(), 1, true, will.c_str());
  else
    ok = mqtt.connect(clientId.c_str(), nullptr, nullptr, tStatus.c_str(), 1, true, will.c_str());
  if (ok) {
    Serial.println("[mqtt] connected");
    mqtt.subscribe(tCmd.c_str(), 1);
    netPublishStatus();
  } else {
    Serial.printf("[mqtt] connect failed, state %d\n", mqtt.state());
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

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(1024);
  mqtt.setKeepAlive(30);
  Serial.printf("[net] connecting to %s\n", WIFI_SSID);
}

void netLoop() {
  uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED) {
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
}

bool netPublishTelemetry(const String& json) {
  if (!mqtt.connected()) return false;
  return mqtt.publish(tTele.c_str(), json.c_str());
}

void netPublishStatus() {
  if (!mqtt.connected()) return;
  JsonDocument doc;
  doc["online"] = true;
  doc["node"] = NODE_NAME;
  doc["fw"] = FW_VERSION;
  doc["ip"] = WiFi.localIP().toString();
  doc["mac"] = WiFi.macAddress();
  doc["rssi"] = WiFi.RSSI();
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
