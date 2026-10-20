#include "identity.h"
#include "common.h"
#include "config.h"
#include "settings.h"
#include <WiFi.h>

static const char* K_SITE = "site";
static const char* K_HOST = "mqtt.host";
static const char* K_PORT = "mqtt.port";
static const char* K_USER = "mqtt.user";
static const char* K_PASS = "mqtt.pass";

// Read once at boot: PubSubClient keeps a pointer to the host, and topics are built from the site.
static String site, host, user, pass;
static uint16_t port;
static bool provisioned = false;

void identityBegin() {
  site = setGetS(K_SITE, SITE_ID);
  host = setGetS(K_HOST, MQTT_HOST);
  port = (uint16_t)setGetU(K_PORT, MQTT_PORT);
  user = setGetS(K_USER, MQTT_USER);
  pass = setGetS(K_PASS, MQTT_PASS);
  provisioned = site != SITE_ID || host != MQTT_HOST;
  Serial.printf("[id] site %s, broker %s:%u (%s)\n", site.c_str(), host.c_str(), port, provisioned ? "provisioned" : "config.h");
}

const char* idSite()     { return site.c_str(); }
const char* idMqttHost() { return host.c_str(); }
uint16_t    idMqttPort() { return port; }
const char* idMqttUser() { return user.c_str(); }
const char* idMqttPass() { return pass.c_str(); }
bool        idProvisioned() { return provisioned; }

// A site becomes MQTT topics: lower case letters, digits and dashes only.
static bool siteOk(const char* s) {
  size_t n = strlen(s);
  if (n < 1 || n > 32) return false;
  for (size_t i = 0; i < n; i++)
    if (!(islower(s[i]) || isdigit(s[i]) || s[i] == '-')) return false;
  return true;
}

void idProvision(JsonDocument& cmd, JsonDocument& reply) {
  if (cmd["clear"] | false) {
    for (const char* k : {K_SITE, K_HOST, K_PORT, K_USER, K_PASS}) setClear(k);
    reply["ok"] = true; reply["restart"] = true;
    return;
  }
  const char* s = cmd["site"] | "";
  const char* h = cmd["mqtt_host"] | "";
  uint32_t p = cmd["mqtt_port"] | 0;
  if (*s && !siteOk(s)) { reply["ok"] = false; reply["error"] = "site: 1-32 of a-z, 0-9 and -"; return; }
  if (strlen(h) > 64) { reply["ok"] = false; reply["error"] = "mqtt_host too long"; return; }
  if (!cmd["mqtt_port"].isNull() && (p < 1 || p > 65535)) { reply["ok"] = false; reply["error"] = "mqtt_port must be 1-65535"; return; }
  if (strlen(cmd["mqtt_user"] | "") > 64 || strlen(cmd["mqtt_pass"] | "") > 64) { reply["ok"] = false; reply["error"] = "mqtt_user / mqtt_pass too long"; return; }
  if (*s) setPutS(K_SITE, s);
  if (*h) setPutS(K_HOST, h);
  if (p) setPutU(K_PORT, p);
  if (!cmd["mqtt_user"].isNull()) setPutS(K_USER, cmd["mqtt_user"].as<const char*>() ?: "");
  if (!cmd["mqtt_pass"].isNull()) setPutS(K_PASS, cmd["mqtt_pass"].as<const char*>() ?: "");
  reply["ok"] = true; reply["restart"] = true;
  reply["site"] = *s ? s : site.c_str();
}

void idFill(JsonDocument& reply) {
  reply["ok"] = true;
  reply["node"] = NODE_NAME;
  reply["fw"] = FW_VERSION;
  reply["mac"] = WiFi.macAddress();
  reply["site"] = site;
  reply["mqtt_host"] = host;
  reply["mqtt_port"] = port;
  reply["provisioned"] = provisioned;
}
