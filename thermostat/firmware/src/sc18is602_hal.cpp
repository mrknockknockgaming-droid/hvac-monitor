#include "sc18is602_hal.h"

namespace {
// SC18IS602B function IDs (NXP datasheet, table 5)
constexpr uint8_t FN_SPI_SS0 = 0x01, FN_CONFIG_SPI = 0xF0, FN_GPIO_WRITE = 0xF4, FN_GPIO_READ = 0xF5,
                  FN_GPIO_ENABLE = 0xF6, FN_GPIO_CONFIG = 0xF7;
constexpr uint8_t SPI_MODE0_1843KHZ = 0x00;     // MSB first, CPOL 0 / CPHA 0 (SX1262), fastest clock
constexpr uint8_t CFG_PUSH_PULL = 0b01, CFG_INPUT = 0b10;
constexpr size_t MAX_XFER = 120;                // Wire's 128-byte buffer (the bridge holds 200)

bool is_bridge(uint32_t pin) { return pin != RADIOLIB_NC && pin >= BRIDGE_PIN && pin < BRIDGE_PIN + 4; }
}  // namespace

bool Sc18is602Hal::command(uint8_t fn, const uint8_t* data, size_t n) {
    wire_.beginTransmission(addr_);
    wire_.write(fn);
    if (n) wire_.write(data, n);
    return wire_.endTransmission() == 0;
}

void Sc18is602Hal::spiBegin() {
    const uint8_t cfg = SPI_MODE0_1843KHZ;
    ok_ = command(FN_CONFIG_SPI, &cfg, 1);
}

void Sc18is602Hal::pinMode(uint32_t pin, uint32_t mode) {
    if (!is_bridge(pin)) return ArduinoHal::pinMode(pin, mode);
    const uint8_t bit = pin - BRIDGE_PIN;
    if (bit == 0) return;                       // SS0 stays the radio's NSS
    gpio_en_ |= 1 << bit;
    gpio_cfg_ = (gpio_cfg_ & ~(0b11 << (2 * bit))) |
                ((mode == GpioModeInput ? CFG_INPUT : CFG_PUSH_PULL) << (2 * bit));
    command(FN_GPIO_CONFIG, &gpio_cfg_, 1);
    command(FN_GPIO_ENABLE, &gpio_en_, 1);
}

void Sc18is602Hal::digitalWrite(uint32_t pin, uint32_t value) {
    if (!is_bridge(pin)) return ArduinoHal::digitalWrite(pin, value);
    const uint8_t bit = pin - BRIDGE_PIN;
    if (bit == 0) return;                       // NSS: the bridge drives it around each transfer
    if (value == GpioLevelHigh) gpio_out_ |= 1 << bit;
    else gpio_out_ &= ~(1 << bit);
    command(FN_GPIO_WRITE, &gpio_out_, 1);
}

uint32_t Sc18is602Hal::digitalRead(uint32_t pin) {
    if (!is_bridge(pin)) return ArduinoHal::digitalRead(pin);
    // GPIO Read puts the pin states in the buffer; a plain read returns them
    if (!command(FN_GPIO_READ, nullptr, 0) || wire_.requestFrom(addr_, static_cast<uint8_t>(1)) != 1)
        return GpioLevelHigh;                   // no answer: report BUSY so RadioLib times out
    return (wire_.read() >> (pin - BRIDGE_PIN)) & 1 ? GpioLevelHigh : GpioLevelLow;
}

void Sc18is602Hal::attachInterrupt(uint32_t num, void (*cb)(void), uint32_t mode) {
    if (!is_bridge(num)) ArduinoHal::attachInterrupt(num, cb, mode);
}

void Sc18is602Hal::detachInterrupt(uint32_t num) {
    if (!is_bridge(num)) ArduinoHal::detachInterrupt(num);
}

uint32_t Sc18is602Hal::pinToInterrupt(uint32_t pin) {
    return is_bridge(pin) ? pin : ArduinoHal::pinToInterrupt(pin);
}

// One SPI transaction with NSS low throughout: write the bytes, the bridge clocks them out on
// SS0 and keeps what came back on MISO in its buffer, then read that back.
void Sc18is602Hal::spiTransfer(uint8_t* out, size_t len, uint8_t* in) {
    if (len > MAX_XFER) len = MAX_XFER;         // SX1262 commands here are < 64 bytes
    wire_.beginTransmission(addr_);
    wire_.write(FN_SPI_SS0);
    wire_.write(out, len);
    if (wire_.endTransmission() != 0) {
        memset(in, 0xFF, len);
        return;
    }
    delayMicroseconds(20 + 5 * len);            // 1.84 MHz: about 4.3 us a byte
    const size_t got = wire_.requestFrom(addr_, static_cast<uint8_t>(len));
    for (size_t i = 0; i < len; i++) in[i] = i < got ? wire_.read() : 0xFF;
}
