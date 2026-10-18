#include "setup_ble.h"
#include "common.h"
#include "settings.h"
#include <WiFi.h>
#include <SimpleBLE.h>    // only so the library finder adds it for WiFiProv
#include <WiFiProv.h>

static const uint32_t HOLD_MS    = 3000;                 // button hold that starts setup
static const uint32_t SETUP_MS   = 10UL * 60UL * 1000UL; // give up (no changes) after 10 min
static const char*    K_REQ      = "setup.req";          // set before the restart into setup mode
static const char*    K_POP      = "setup.pop";
static const char*    K_SSID     = "wifi.ssid";
static const char*    K_PASS     = "wifi.pass";

static uint32_t pressedSince = 0;
static volatile bool credOk = false;
static char rxSsid[33], rxPass[65];

// The setup code: random, made once and kept in flash (survives firmware updates; a full flash
// erase makes a new one). Not derived from the MAC, which the Bluetooth advertisement gives away.
static String setupCode() {
  String pop = setGetS(K_POP, "");
  if (pop.length() == 8) return pop;
  static const char abc[] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";   // no 0/O, 1/I
  pop = "";
  for (int i = 0; i < 8; i++) pop += abc[esp_random() % 32];
  setPutS(K_POP, pop);
  return pop;
}

static String serviceName() {
  uint64_t mac = ESP.getEfuseMac();                       // bytes in transmit order, low byte first
  char buf[32];
  snprintf(buf, sizeof(buf), "PROV_hvac-%s-%02X%02X", NODE_NAME,
           (uint8_t)(mac >> 32), (uint8_t)(mac >> 40));
  return String(buf);
}

static void onProvEvent(arduino_event_t* e) {
  switch (e->event_id) {
    case ARDUINO_EVENT_PROV_CRED_RECV:                    // kept only once the node has joined with them
      memcpy(rxSsid, e->event_info.prov_cred_recv.ssid, 32);     rxSsid[32] = 0;
      memcpy(rxPass, e->event_info.prov_cred_recv.password, 64); rxPass[64] = 0;
      Serial.printf("[setup] trying network \"%s\"\n", rxSsid);
      break;
    case ARDUINO_EVENT_PROV_CRED_FAIL:
      Serial.println("[setup] could not join (wrong password or network not found); try again from the app");
      break;
    case ARDUINO_EVENT_PROV_CRED_SUCCESS:
      credOk = true;
      break;
    default: break;
  }
}

static bool buttonHeld(uint32_t ms) {
  uint32_t t0 = millis();
  while (digitalRead(PIN_SETUP) == LOW) {
    digitalWrite(PIN_LED, (millis() / 100) % 2);
    if (millis() - t0 >= ms) return true;
    delay(10);
  }
  digitalWrite(PIN_LED, LOW);
  return false;
}

static void runSetup() {
  String name = serviceName(), pop = setupCode();
  Serial.printf("[setup] Bluetooth WiFi setup: open \"ESP BLE Provisioning\", pick %s, code %s\n",
                name.c_str(), pop.c_str());
  WiFi.onEvent(onProvEvent);
  WiFiProv.beginProvision(WIFI_PROV_SCHEME_BLE, WIFI_PROV_SCHEME_HANDLER_FREE_BTDM, WIFI_PROV_SECURITY_1,
                          pop.c_str(), name.c_str(), NULL, NULL, true);
  WiFiProv.printQR(name.c_str(), pop.c_str(), "ble");

  uint32_t t0 = millis();
  while (!credOk) {
    uint32_t t = millis() % 1000;                         // two short blinks a second = setup mode
    digitalWrite(PIN_LED, t < 80 || (t >= 200 && t < 280));
    if (millis() - t0 > SETUP_MS) {
      Serial.println("[setup] timed out, nothing changed; restarting");
      delay(100);
      ESP.restart();
    }
    delay(10);
  }
  setPutS(K_SSID, String(rxSsid));
  setPutS(K_PASS, String(rxPass));
  Serial.printf("[setup] joined \"%s\"; saved, restarting\n", rxSsid);
  digitalWrite(PIN_LED, HIGH);
  delay(2000);                                            // lets the app read the result
  ESP.restart();
}

void setupBleBegin() {
  Serial.printf("[setup] hold SETUP 3 s for Bluetooth WiFi setup (code %s)\n", setupCode().c_str());
  bool asked = setGetB(K_REQ, false);
  if (asked) setClear(K_REQ);
  if (asked || buttonHeld(HOLD_MS)) runSetup();
}

void setupBleLoop() {
  if (digitalRead(PIN_SETUP) != LOW) { pressedSince = 0; return; }
  if (!pressedSince) { pressedSince = millis() | 1; return; }
  if (millis() - pressedSince < HOLD_MS) return;
  Serial.println("[setup] SETUP held: restarting into Bluetooth WiFi setup");
  setPutB(K_REQ, true);                                   // setup runs from a clean start
  delay(100);
  ESP.restart();
}

bool setupBleWifi(String& ssid, String& pass) {
  ssid = setGetS(K_SSID, "");
  if (!ssid.length()) return false;
  pass = setGetS(K_PASS, "");
  return true;
}
