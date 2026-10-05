#include "lora_radio.h"

#include <RadioLib.h>
#include <SPI.h>

namespace {
SX1262* radio = nullptr;
volatile bool got_packet = false;
volatile bool transmitting = false;

ICACHE_RAM_ATTR void on_dio1() {
    if (!transmitting) got_packet = true;
}
}  // namespace

bool LoraRadio::begin(const LoraPins& p, double mhz, uint8_t sf, int8_t dbm) {
    if (p.sck >= 0) SPI.begin(p.sck, p.miso, p.mosi, p.nss);
    radio = new SX1262(new Module(p.nss, p.dio1, p.rst, p.busy));
    const int st = radio->begin(static_cast<float>(mhz), 500.0f, sf, 5, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, dbm, 8, p.tcxo_v);
    if (st != RADIOLIB_ERR_NONE) {
        Serial.printf("lora: radio.begin failed (%d)\n", st);
        return ok_ = false;
    }
    if (p.dio2_rf_switch) radio->setDio2AsRfSwitch(true);
    radio->setCRC(true);
    radio->setPacketReceivedAction(on_dio1);
    radio->startReceive();
    Serial.printf("lora: %.1f MHz, 500 kHz, SF%u, %d dBm\n", mhz, sf, dbm);
    return ok_ = true;
}

bool LoraRadio::send(const uint8_t* frame, size_t n) {
    if (!ok_) return false;
    transmitting = true;
    const int st = radio->transmit(const_cast<uint8_t*>(frame), n);
    transmitting = false;
    got_packet = false;
    radio->startReceive();
    if (st != RADIOLIB_ERR_NONE) Serial.printf("lora: transmit failed (%d)\n", st);
    return st == RADIOLIB_ERR_NONE;
}

bool LoraRadio::receive(uint8_t* frame, size_t& n, size_t max, int& rssi, float& snr) {
    if (!ok_ || !got_packet) return false;
    got_packet = false;
    const size_t len = radio->getPacketLength();
    if (len == 0 || len > max) {
        radio->startReceive();
        return false;
    }
    const int st = radio->readData(frame, len);
    rssi = static_cast<int>(radio->getRSSI());
    snr = radio->getSNR();
    radio->startReceive();
    if (st != RADIOLIB_ERR_NONE) return false;      // CRC error etc.
    n = len;
    return true;
}
