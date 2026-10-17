#pragma once
// Sensirion SHT45 humidity sensor: command, CRC check and conversion (datasheet section 4).
// Kept free of Arduino calls so it can be checked on a PC.
#include <stdint.h>

static const uint8_t SHT45_MEASURE_HIGH = 0xFD;   // high repeatability, about 8.3 ms

// CRC-8, polynomial 0x31, init 0xFF, over each 2-byte word.
inline uint8_t sht45Crc(const uint8_t* d) {
  uint8_t crc = 0xFF;
  for (uint8_t i = 0; i < 2; i++) {
    crc ^= d[i];
    for (uint8_t b = 0; b < 8; b++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
  }
  return crc;
}

// b: the 6 bytes read after a measurement (T msb, lsb, crc, RH msb, lsb, crc). False on a bad CRC.
inline bool sht45Decode(const uint8_t* b, float& tempC, float& rh) {
  if (sht45Crc(b) != b[2] || sht45Crc(b + 3) != b[5]) return false;
  uint16_t st = (uint16_t)(b[0] << 8 | b[1]), srh = (uint16_t)(b[3] << 8 | b[4]);
  tempC = -45.0f + 175.0f * st / 65535.0f;
  rh = -6.0f + 125.0f * srh / 65535.0f;
  if (rh < 0) rh = 0;
  if (rh > 100) rh = 100;
  return true;
}
