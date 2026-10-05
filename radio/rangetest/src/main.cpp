// LoRa range test: a sender and a receiver on Heltec WiFi LoRa 32 (V3).
//
// Radio settings follow ../README.md: 915.0 MHz, 500 kHz bandwidth (FCC 15.247 digital
// modulation: no hopping needed), coding rate 4/5, 45-byte packets (the size of a real outdoor
// reading frame). The PRG button cycles the spreading factor 7..12; set both boards the same.
//
// Receiver screen: last RSSI / SNR, packets received and lost (from the sender's counter).
// Receiver serial: one CSV line per packet, for logging a walk-around:
//   ms,counter,sf,rssi_dbm,snr_db,lost_total

#include <Arduino.h>
#include <RadioLib.h>
#include <U8g2lib.h>

#if !defined(ROLE_SENDER) && !defined(ROLE_RECEIVER)
#error "build with -DROLE_SENDER or -DROLE_RECEIVER (see platformio.ini)"
#endif

// Heltec V3 wiring
static const int PIN_NSS = 8, PIN_DIO1 = 14, PIN_RST = 12, PIN_BUSY = 13;
static const int PIN_VEXT = 36, PIN_OLED_RST = 21, PIN_OLED_SDA = 17, PIN_OLED_SCL = 18, PIN_BUTTON = 0;

static const float  FREQ_MHZ = 915.0;
static const float  BW_KHZ = 500.0;
static const int8_t TX_DBM = 20;                // SX1262 can do 22; 20 leaves margin
static const size_t PACKET_LEN = 45;
static const uint32_t SEND_EVERY_MS = 2000;

SX1262 radio = new Module(PIN_NSS, PIN_DIO1, PIN_RST, PIN_BUSY);
U8G2_SSD1306_128X64_NONAME_F_SW_I2C oled(U8G2_R0, PIN_OLED_SCL, PIN_OLED_SDA, PIN_OLED_RST);

static uint8_t sf = 9;
static volatile bool radioDone = false;
static uint32_t counter = 0, received = 0, lost = 0, lastCounter = 0;
static float lastRssi = 0, lastSnr = 0;
static bool haveLast = false;

ICACHE_RAM_ATTR void onRadio() { radioDone = true; }

static void show(const char* l1, const char* l2, const char* l3, const char* l4) {
  oled.clearBuffer();
  oled.setFont(u8g2_font_6x12_tf);
  oled.drawStr(0, 12, l1); oled.drawStr(0, 28, l2); oled.drawStr(0, 44, l3); oled.drawStr(0, 60, l4);
  oled.sendBuffer();
}

static void applySf() {
  radio.standby();
  radio.setSpreadingFactor(sf);
  received = lost = 0; haveLast = false;
  Serial.printf("# spreading factor %u\n", sf);
#ifdef ROLE_RECEIVER
  radio.startReceive();
#endif
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_VEXT, OUTPUT); digitalWrite(PIN_VEXT, LOW);   // powers the OLED
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  delay(100);
  oled.begin();
  // freq, bw, sf, cr (4/5), private sync word, power, preamble, TCXO 1.8 V
  int st = radio.begin(FREQ_MHZ, BW_KHZ, sf, 5, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, TX_DBM, 8, 1.8);
  if (st != RADIOLIB_ERR_NONE) {
    char m[24]; snprintf(m, sizeof(m), "radio error %d", st);
    show("LoRa range test", m, "check the board", "");
    Serial.printf("# radio.begin failed: %d\n", st);
    while (true) delay(1000);
  }
  radio.setDio2AsRfSwitch(true);
  radio.setCRC(true);
  radio.setPacketReceivedAction(onRadio);   // also fires when a transmission finishes
#ifdef ROLE_RECEIVER
  radio.startReceive();
  Serial.println("ms,counter,sf,rssi_dbm,snr_db,lost_total");
#endif
}

static bool buttonPressed() {
  static uint32_t last = 0;
  if (digitalRead(PIN_BUTTON) == LOW && millis() - last > 400) { last = millis(); return true; }
  return false;
}

void loop() {
  if (buttonPressed()) { sf = sf >= 12 ? 7 : sf + 1; applySf(); }
  char l1[24], l2[24], l3[24], l4[24];

#ifdef ROLE_SENDER
  static uint32_t lastSend = 0;
  if (millis() - lastSend >= SEND_EVERY_MS) {
    lastSend = millis();
    uint8_t pkt[PACKET_LEN] = {'H', 'V', 'R', sf};
    memcpy(pkt + 4, &counter, 4);
    int st = radio.transmit(pkt, PACKET_LEN);
    Serial.printf("%lu,%lu,%u,sent,%d\n", (unsigned long)millis(), (unsigned long)counter, sf, st);
    counter++;
    snprintf(l1, sizeof(l1), "SENDER  SF%u 500k", sf);
    snprintf(l2, sizeof(l2), "%.1f MHz %d dBm", FREQ_MHZ, TX_DBM);
    snprintf(l3, sizeof(l3), "sent %lu", (unsigned long)counter);
    snprintf(l4, sizeof(l4), st == RADIOLIB_ERR_NONE ? "ok" : "tx error %d", st);
    show(l1, l2, l3, l4);
  }
#else
  if (radioDone) {
    radioDone = false;
    uint8_t pkt[64];
    size_t len = radio.getPacketLength();
    int st = radio.readData(pkt, min(len, sizeof(pkt)));
    if (st == RADIOLIB_ERR_NONE && len == PACKET_LEN && pkt[0] == 'H' && pkt[1] == 'V' && pkt[2] == 'R') {
      uint32_t c; memcpy(&c, pkt + 4, 4);
      if (haveLast && c > lastCounter + 1) lost += c - lastCounter - 1;
      lastCounter = c; haveLast = true; received++;
      lastRssi = radio.getRSSI(); lastSnr = radio.getSNR();
      Serial.printf("%lu,%lu,%u,%.1f,%.1f,%lu\n", (unsigned long)millis(), (unsigned long)c, sf, lastRssi, lastSnr, (unsigned long)lost);
    } else if (st == RADIOLIB_ERR_CRC_MISMATCH) {
      Serial.printf("# CRC error at SF%u\n", sf);
    }
    radio.startReceive();
  }
  static uint32_t lastDraw = 0;
  if (millis() - lastDraw > 500) {
    lastDraw = millis();
    float loss = received + lost ? 100.0f * lost / (received + lost) : 0;
    snprintf(l1, sizeof(l1), "RECEIVER SF%u 500k", sf);
    snprintf(l2, sizeof(l2), haveLast ? "RSSI %.0f dBm" : "waiting...", lastRssi);
    snprintf(l3, sizeof(l3), "SNR %.1f dB", lastSnr);
    snprintf(l4, sizeof(l4), "got %lu lost %lu %.0f%%", (unsigned long)received, (unsigned long)lost, loss);
    show(l1, l2, l3, l4);
  }
#endif
}
