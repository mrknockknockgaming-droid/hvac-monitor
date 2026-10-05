// HVAC Monitor node firmware
// One codebase, two builds: -DNODE_OUTDOOR (refrigerant side) and -DNODE_INDOOR (air side).

#include "common.h"
#include "config.h"
#include "lora_link.h"
#include "net.h"
#include "settings.h"
#include <WiFi.h>

static uint32_t lastPublish = 0;
static uint32_t intervalMs  = 5000;
static uint32_t lastLoraStatus = 0;
static const uint32_t LORA_STATUS_MS = 5UL * 60UL * 1000UL;   // over LoRa the status isn't retained: resend

// The link: WiFi + MQTT as before, or LoRa to the site's gateway when config.h says so.
static void publishStatus();

static void handleCommandInner(JsonDocument& cmd, JsonDocument& reply);

// Every successful change re-publishes the retained status, so the cloud shows the new settings.
static void handleCommand(JsonDocument& cmd, JsonDocument& reply) {
  handleCommandInner(cmd, reply);
  const char* c = cmd["cmd"] | "";
  if ((reply["ok"] | false) && strcmp(c, "status") && strcmp(c, "reboot")) publishStatus();
}

static void publishStatus() {
  if (LORA_ENABLED) loraSendStatus(intervalMs);
  else netPublishStatus();
}

static void fillStatus(JsonDocument& doc) {
  doc["interval_ms"] = intervalMs;
  nodeFillStatus(doc);
}

// Commands every node understands; anything else goes to the node file.
static void handleCommandInner(JsonDocument& cmd, JsonDocument& reply) {
  const char* c = cmd["cmd"] | "";
  if (!strcmp(c, "reboot")) {
    reply["ok"] = true;
    publishStatus();
    delay(300);
    ESP.restart();
  } else if (!strcmp(c, "interval")) {
    uint32_t ms = cmd["ms"] | 0;
    if (ms < 1000 || ms > 600000) { reply["ok"] = false; reply["error"] = "ms must be 1000-600000"; return; }
    intervalMs = ms;
    setPutU("interval", ms);
    reply["ok"] = true; reply["ms"] = ms;
  } else if (!strcmp(c, "status")) {
    publishStatus();
    reply["ok"] = true;
  } else if (!nodeHandleCmd(cmd, reply)) {
    reply["ok"] = false;
    reply["error"] = "unknown command";
  }
}

// Status LED: fast blink = no WiFi, slow blink = no MQTT, short flash every 3 s = all good
static void ledUpdate() {
  uint32_t t = millis();
  bool on;
  if (LORA_ENABLED) {                // LoRa: fast blink = radio problem, short flash = sending
    digitalWrite(PIN_LED, loraOk() ? (t % 3000) < 40 : (t / 100) % 2);
    return;
  }
  switch (netState()) {
    case NET_NO_WIFI: on = (t / 100) % 2; break;
    case NET_NO_MQTT: on = (t / 500) % 2; break;
    default:          on = (t % 3000) < 40; break;
  }
  digitalWrite(PIN_LED, on);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.printf("\n=== HVAC monitor | %s node | fw %s ===\n", NODE_NAME, FW_VERSION);
  pinMode(PIN_LED, OUTPUT);
  settingsBegin();
  intervalMs = setGetU("interval", 5000);
  nodeSetup();
  if (LORA_ENABLED) {
    loraBegin(handleCommand);
    loraSendStatus(intervalMs);
    lastLoraStatus = millis();
  } else {
    netBegin(handleCommand, fillStatus);
  }
}

void loop() {
  if (LORA_ENABLED) loraLoop(); else netLoop();
  if (LORA_ENABLED && millis() - lastLoraStatus >= LORA_STATUS_MS) {
    lastLoraStatus = millis();
    loraSendStatus(intervalMs);
  }
  nodeLoop();
  ledUpdate();

  uint32_t now = millis();
  if (now - lastPublish >= intervalMs) {
    lastPublish = now;
    JsonDocument doc;
    doc["node"]   = NODE_NAME;
    doc["fw"]     = FW_VERSION;
    doc["uptime"] = now / 1000;
    doc["rssi"]   = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0;
    if (netTimeValid()) doc["ts"] = serialized(String(netEpoch(), 3));   // when it was measured (UTC)
    nodeFillTelemetry(doc);
    String out;
    serializeJson(doc, out);
    Serial.println(out);             // bench testing works with just a USB cable
    if (LORA_ENABLED) loraSendReading(doc); else netPublishTelemetry(out);
  }
}
