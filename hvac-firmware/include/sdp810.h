#pragma once
// Sensirion SDP810-500Pa differential pressure sensor: commands, CRC check and conversion
// (datasheet section 5). Kept free of Arduino calls so it can be checked on a PC.
#include "sht45.h"   // same CRC-8 (0x31, init 0xFF) over each 2-byte word

static const uint8_t SDP810_ADDR = 0x25;
static const uint16_t SDP810_START = 0x3603;   // continuous, differential pressure, averaging on
static const float PA_PER_INWC = 249.0889f;

// b: the 9 bytes read in continuous mode (dp msb, lsb, crc, temp msb, lsb, crc, scale msb, lsb, crc).
// Returns false on a bad CRC or a zero scale factor.
inline bool sdp810Decode(const uint8_t* b, float& inwc) {
  if (sht45Crc(b) != b[2] || sht45Crc(b + 3) != b[5] || sht45Crc(b + 6) != b[8]) return false;
  int16_t raw = (int16_t)(b[0] << 8 | b[1]);
  uint16_t scale = (uint16_t)(b[6] << 8 | b[7]);           // 60 per Pa for the 500 Pa part
  if (scale == 0) return false;
  inwc = ((float)raw / scale) / PA_PER_INWC;
  return true;
}
