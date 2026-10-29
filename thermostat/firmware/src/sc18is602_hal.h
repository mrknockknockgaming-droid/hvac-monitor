// RadioLib hardware layer for an SX1262 behind an SC18IS602B I2C-to-SPI bridge (the thermostat
// carrier, hardware/scripts/design.py): the Waveshare display board has no free SPI pins, so the
// radio's SPI goes over the I2C bus the outputs already use.
//
// Pins at or above BRIDGE_PIN are the bridge's SS0-SS3 (NSS on SS0, then BUSY, NRST and RF_SW as
// GPIOs); any other pin number is a real ESP32 pin (DIO1 on GPIO6), handled by ArduinoHal.
#pragma once
#include <RadioLib.h>
#include <Wire.h>

constexpr uint32_t BRIDGE_PIN = 0x100;

class Sc18is602Hal : public ArduinoHal {
public:
    explicit Sc18is602Hal(uint8_t addr, TwoWire& wire = Wire) : addr_(addr), wire_(wire) {}

    void pinMode(uint32_t pin, uint32_t mode) override;
    void digitalWrite(uint32_t pin, uint32_t value) override;
    uint32_t digitalRead(uint32_t pin) override;
    void attachInterrupt(uint32_t num, void (*cb)(void), uint32_t mode) override;
    void detachInterrupt(uint32_t num) override;
    uint32_t pinToInterrupt(uint32_t pin) override;

    void spiBegin() override;
    void spiBeginTransaction() override {}
    void spiTransfer(uint8_t* out, size_t len, uint8_t* in) override;
    void spiEndTransaction() override {}
    void spiEnd() override {}

    bool ok() const { return ok_; }

private:
    bool command(uint8_t fn, const uint8_t* data, size_t n);
    uint8_t addr_;
    TwoWire& wire_;
    uint8_t gpio_out_ = 0x0F;       // SS0-SS3 latch (all high = deselected / released)
    uint8_t gpio_en_ = 0;           // 1 = GPIO, 0 = slave select
    uint8_t gpio_cfg_ = 0;          // 2 bits per pin
    bool ok_ = false;
};
