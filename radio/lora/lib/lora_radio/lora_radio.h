// The SX1262 for every LoRa device here (nodes, plug-in gateway, thermostat): the settings from
// radio/README.md (500 kHz, SF9, 4/5, private sync word, 20 dBm) on the site's channel, always
// listening between transmissions (everything runs from 24 VAC or USB, so power isn't an issue).
#pragma once
#include <Arduino.h>

struct LoraPins {
    int nss, dio1, rst, busy;
    int sck = -1, miso = -1, mosi = -1;     // -1: the board's default SPI pins
    float tcxo_v = 1.8f;                    // 0 for a crystal (no TCXO)
    bool dio2_rf_switch = true;
};

class LoraRadio {
public:
    // false if the radio didn't answer (wrong pins, no module)
    bool begin(const LoraPins& pins, double mhz, uint8_t sf = 9, int8_t dbm = 20);
    // Transmit (blocking, ~80 ms at SF9), then back to listening. false on a radio error.
    bool send(const uint8_t* frame, size_t n);
    // A received frame, if one arrived since the last call. rssi in dBm, snr in dB.
    bool receive(uint8_t* frame, size_t& n, size_t max, int& rssi, float& snr);
    bool ok() const { return ok_; }

private:
    bool ok_ = false;
};
