#include "io.h"

#include <Arduino.h>
#include <Wire.h>

#include "board.h"

namespace {
constexpr uint8_t IODIRA = 0x00, IODIRB = 0x01, GPPUB = 0x0D, GPIOB = 0x13, OLATA = 0x14;
uint8_t olat = 0;
int room_failures = 3;          // NAN until the first good read
double last_t = NAN, last_rh = NAN;

bool mcp_write(uint8_t reg, uint8_t v) {
    Wire.beginTransmission(MCP_ADDR);
    Wire.write(reg);
    Wire.write(v);
    return Wire.endTransmission() == 0;
}

bool mcp_read(uint8_t reg, uint8_t& v) {
    Wire.beginTransmission(MCP_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0 || Wire.requestFrom(MCP_ADDR, static_cast<uint8_t>(1)) != 1) return false;
    v = Wire.read();
    return true;
}

uint8_t crc8(const uint8_t* d, int n) {         // Sensirion: poly 0x31, init 0xFF
    uint8_t crc = 0xFF;
    for (int i = 0; i < n; i++) {
        crc ^= d[i];
        for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (crc << 1) ^ 0x31 : crc << 1;
    }
    return crc;
}
}  // namespace

bool io_begin() {
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
    olat = 0;
    bool ok = mcp_write(OLATA, 0)                 // latch everything off before making them outputs
              && mcp_write(IODIRA, 0x80)          // GPA0-6 outputs, GPA7 spare input
              && mcp_write(IODIRB, 0xFF)          // GPB: state-sense inputs
              && mcp_write(GPPUB, 0x1F);
    if (!ok) Serial.println("io: MCP23017 not found: outputs can't switch");
    return ok;
}

bool io_outputs(const tstat::Outputs& o) {
    uint8_t v = olat & (1 << OUT_WDI);
    if (o.Y) v |= 1 << OUT_Y;
    if (o.W) v |= 1 << OUT_W;
    if (o.G) v |= 1 << OUT_G;
    if (o.OB) v |= 1 << OUT_OB;                   // Y2 / W2: second stages, not used yet
    if (!mcp_write(OLATA, v)) return false;
    olat = v;
    return true;
}

void io_watchdog() {
    olat ^= 1 << OUT_WDI;
    mcp_write(OLATA, olat);
}

Sensed io_sense() {
    // H11AA1 outputs go high briefly near each zero crossing: "on" if low in any of 5 samples 2 ms apart
    Sensed s;
    uint8_t low = 0, v;
    for (int i = 0; i < 5; i++) {
        if (!mcp_read(GPIOB, v)) return s;
        low |= ~v & 0x1F;
        delay(2);
    }
    s.ok = true;
    s.Y = low & (1 << SENSE_Y);
    s.Y2 = low & (1 << SENSE_Y2);
    s.W = low & (1 << SENSE_W);
    s.G = low & (1 << SENSE_G);
    s.OB = low & (1 << SENSE_OB);
    return s;
}

void io_room(double& temp_f, double& rh) {
    uint8_t d[6];
    bool ok = false;
    Wire.beginTransmission(SHT45_ADDR);
    Wire.write(0xFD);                             // measure, high precision
    if (Wire.endTransmission() == 0) {
        delay(10);
        if (Wire.requestFrom(SHT45_ADDR, static_cast<uint8_t>(6)) == 6) {
            for (auto& b : d) b = Wire.read();
            ok = crc8(d, 2) == d[2] && crc8(d + 3, 2) == d[5];
        }
    }
    if (ok) {
        const double c = -45 + 175.0 * ((d[0] << 8) | d[1]) / 65535.0;
        last_t = c * 9 / 5 + 32 + ROOM_OFFSET_F;
        last_rh = constrain(-6 + 125.0 * ((d[3] << 8) | d[4]) / 65535.0, 0.0, 100.0);
        room_failures = 0;
    } else if (room_failures < 3) {
        room_failures++;
    }
    temp_f = room_failures >= 3 ? NAN : last_t;
    rh = room_failures >= 3 ? NAN : last_rh;
}
