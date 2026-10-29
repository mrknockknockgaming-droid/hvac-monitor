// Waveshare ESP32-S3-Touch-LCD-4.3 plus the thermostat's output board (thermostat/README.md).
// Pin numbers for the display come from Waveshare's documentation for this board:
// CHECK THEM against the board's schematic before the first power-up.
#pragma once
#include <Arduino.h>

constexpr const char* FW_VERSION = "0.1.0";

// ---- shared I2C bus (touch, CH422G, and our parts on the header)
constexpr int PIN_I2C_SDA = 8;
constexpr int PIN_I2C_SCL = 9;
constexpr int PIN_TOUCH_INT = 4;

// ---- RGB panel (800 x 480, RGB565)
constexpr int LCD_W = 800, LCD_H = 480;
constexpr int PIN_LCD_DE = 5, PIN_LCD_VSYNC = 3, PIN_LCD_HSYNC = 46, PIN_LCD_PCLK = 7;
constexpr int PIN_LCD_B[5] = {14, 38, 18, 17, 10};      // B3..B7
constexpr int PIN_LCD_G[6] = {39, 0, 45, 48, 47, 21};   // G2..G7
constexpr int PIN_LCD_R[5] = {1, 2, 42, 41, 40};        // R3..R7
constexpr uint32_t LCD_PCLK_HZ = 14000000;

// ---- CH422G I/O expander on the board (backlight, panel and touch resets)
constexpr uint8_t CH422G_MODE = 0x24;   // write 0x01: IO0-7 are outputs
constexpr uint8_t CH422G_OUT = 0x38;    // output latch
constexpr uint8_t EXIO_TP_RST = 1 << 1, EXIO_LCD_BL = 1 << 2, EXIO_LCD_RST = 1 << 3, EXIO_SD_CS = 1 << 4;

// ---- GT911 touch controller (address chosen at reset by the INT pin: low -> 0x5D)
constexpr uint8_t GT911_ADDR = 0x5D, GT911_ADDR_ALT = 0x14;

// ---- our carrier board (hardware/thermostat-carrier, hardware/scripts/design.py), on the same
// I2C bus through the display's I2C header; the sensor header's GPIO6 carries the radio's DIO1.
// MCP23017 (0x20): GPA0 Y1, GPA1 Y2, GPA2 W1, GPA3 W2, GPA4 G, GPA5 O/B -> PhotoMOS LEDs (high = on),
//                  GPA6 -> TPS3823 WDI (toggled every loop), GPA7 spare (output-only pin);
//                  GPB0-5 <- H11AA1 read-back (low while 24 VAC is on the terminal): Y1, Y2, W1, G,
//                  O/B, W2. GPB6 spare, GPB7 output-only.
// The TPS3823's RESET drives the MCP23017's RESET (the display board's EN isn't brought out): if
// the firmware stops toggling WDI, the expander resets to all inputs and every output turns off.
constexpr uint8_t MCP_ADDR = 0x20;
constexpr uint8_t OUT_Y = 0, OUT_Y2 = 1, OUT_W = 2, OUT_W2 = 3, OUT_G = 4, OUT_OB = 5, OUT_WDI = 6;
constexpr uint8_t SENSE_Y = 0, SENSE_Y2 = 1, SENSE_W = 2, SENSE_G = 3, SENSE_OB = 4, SENSE_W2 = 5;
constexpr uint8_t SENSE_MASK = 0x3F;

// ---- LoRa (Wio-SX1262) behind the SC18IS602B I2C-to-SPI bridge (0x28, A0-A2 low): SS0 = NSS,
// GPIO1 = BUSY, GPIO2 = NRST, GPIO3 = RF_SW (sc18is602_hal.h numbers them from BRIDGE_PIN).
constexpr uint8_t LORA_BRIDGE_ADDR = 0x28;
constexpr int LORA_BRIDGE_NSS = 0x100, LORA_BRIDGE_BUSY = 0x101, LORA_BRIDGE_NRST = 0x102,
              LORA_BRIDGE_RFSW = 0x103;
constexpr int LORA_DIO1_GPIO = 6;       // display board sensor header (check against Waveshare's schematic)

// ---- room sensor: SHT45 (0x44) on its thermally isolated tab
constexpr uint8_t SHT45_ADDR = 0x44;
constexpr float ROOM_OFFSET_F = 0.0f;   // calibrate against a reference: the display warms the case
