// Host test for include/ct_math.h:  g++ -std=c++17 -I../../include ct_math_test.cpp && ./a.out
#include <assert.h>
#include <stdio.h>
#include "ct_math.h"

static bool near(float a, float b, float tol) { return fabsf(a - b) <= tol; }

int main() {
  // 60 Hz sine, 100 mV RMS on a 1340 mV bias, sampled at 2 kHz for 200 ms
  CtAcc acc;
  for (int i = 0; i < 400; i++) acc.add(1340.0f + 141.42f * sinf(2 * (float)M_PI * 60 * i / 2000.0f));
  assert(near(acc.meanMv(), 1340.0f, 1.0f));
  assert(near(acc.rmsMv(), 100.0f, 0.5f));
  acc.reset();
  for (int i = 0; i < 10; i++) acc.add(1340.0f);
  assert(acc.rmsMv() == 0.0f);

  // healthy start: 2 cycles before the contactor closes, 60 A inrush decaying to 15 A
  float c[90];
  for (int i = 0; i < 90; i++) c[i] = i < 2 ? 0.1f : (i < 6 ? 60.0f - 5 * (i - 2) : (i < 12 ? 40.0f - 4 * (i - 6) : 15.0f));
  CtStart s = ctStart(c, 90, 16.667f);
  assert(s.valid && near(s.peakA, 60.0f, 0.01f));
  assert(s.ms > 100 && s.ms < 250);      // first draw at cycle 2, settled by cycle 12 or so

  // hard start: stays at locked-rotor current the whole capture
  for (int i = 0; i < 90; i++) c[i] = i < 85 ? 70.0f : 18.0f;
  s = ctStart(c, 90, 16.667f);
  assert(s.valid && s.ms > 1300);

  // never started (overload open): no current at all
  for (int i = 0; i < 90; i++) c[i] = 0.05f;
  assert(!ctStart(c, 90, 16.667f).valid);
  puts("ct_math ok");
  return 0;
}
