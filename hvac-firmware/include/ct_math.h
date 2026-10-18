// Current-transformer maths for the outdoor board's CT inputs (rev B, electrical module version 1).
// No Arduino code here, so it builds and is tested on the host (test/ct_math_host.cpp).
#pragma once
#include <math.h>
#include <stdint.h>

// RMS of AC samples around their own mean. The jack sleeves sit on CT_BIAS (~1.34 V), which moves
// with the 3.3 V rail, so the offset is measured from the samples rather than assumed.
struct CtAcc {
  double sum = 0, sq = 0;
  uint32_t n = 0;
  void reset() { sum = sq = 0; n = 0; }
  void add(float mv) { sum += mv; sq += (double)mv * mv; n++; }
  float meanMv() const { return n ? (float)(sum / n) : NAN; }
  float rmsMv() const {
    if (n < 2) return NAN;
    double m = sum / n, v = sq / n - m * m;
    return v > 0 ? (float)sqrt(v) : 0.0f;
  }
};

// A compressor start from per-cycle RMS amps captured from the moment Y energized.
struct CtStart {
  bool valid = false;   // false: the compressor never drew current during the capture
  float peakA = 0;      // highest one-cycle RMS (the inrush)
  int ms = 0;           // from the first cycle drawing current until it settles near running amps
};

// cycleA: one-cycle RMS values in order; settledA: running current (mean of the last few cycles).
// The start ends at the first cycle after the peak at or below 1.25 x running current; if that
// never happens, ms covers the whole capture (so a hard start still reads as slow).
inline CtStart ctStart(const float* cycleA, int n, float cycleMs, float floorA = 1.0f) {
  CtStart r;
  if (n < 6) return r;
  float settled = 0;
  for (int i = n - 5; i < n; i++) settled += cycleA[i];
  settled /= 5;
  if (settled < floorA) return r;
  float on = fmaxf(floorA, 0.3f * settled);
  int first = -1, peakAt = -1;
  for (int i = 0; i < n; i++) {
    if (first < 0 && cycleA[i] >= on) first = i;
    if (first >= 0 && (peakAt < 0 || cycleA[i] > r.peakA)) { r.peakA = cycleA[i]; peakAt = i; }
  }
  if (first < 0) return r;
  int end = n;
  for (int i = peakAt; i < n; i++) {
    if (cycleA[i] <= 1.25f * settled) { end = i; break; }
  }
  r.valid = true;
  r.ms = (int)lroundf((end - first) * cycleMs);
  return r;
}
