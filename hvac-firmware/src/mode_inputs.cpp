#include "mode_inputs.h"
#include "esp_timer.h"
#include "driver/gpio.h"

#define MAX_INPUTS     6
#define WINDOW_SAMPLES 50      // 50 ms at 1 kHz = 3 full 60 Hz cycles
#define ON_THRESHOLD   15      // >= 30% of samples LOW means 24 VAC present

static const ModeInput* gInputs = nullptr;
static uint8_t gCount = 0;
static volatile uint16_t lowCount[MAX_INPUTS];
static volatile uint16_t sampleCount = 0;
static volatile bool     candidate[MAX_INPUTS];
static volatile bool     stateOn[MAX_INPUTS];
static esp_timer_handle_t timer;

static void sampleTick(void*) {
  for (uint8_t i = 0; i < gCount; i++) {
    if (gpio_get_level((gpio_num_t)gInputs[i].pin) == 0) lowCount[i]++;
  }
  if (++sampleCount >= WINDOW_SAMPLES) {
    for (uint8_t i = 0; i < gCount; i++) {
      bool on = lowCount[i] >= ON_THRESHOLD;
      if (on == candidate[i]) stateOn[i] = on;   // two matching windows -> accept
      candidate[i] = on;
      lowCount[i] = 0;
    }
    sampleCount = 0;
  }
}

void modeInputsBegin(const ModeInput* inputs, uint8_t count) {
  gInputs = inputs;
  gCount = min<uint8_t>(count, MAX_INPUTS);
  for (uint8_t i = 0; i < gCount; i++) {
    pinMode(gInputs[i].pin, INPUT);   // GPIO34-39 are input-only; external 10k pull-ups
    lowCount[i] = 0; candidate[i] = false; stateOn[i] = false;
  }
  esp_timer_create_args_t args = {};
  args.callback = &sampleTick;
  args.name = "modein";
  esp_timer_create(&args, &timer);
  esp_timer_start_periodic(timer, 1000);   // every 1 ms
}

bool modeGet(uint8_t i) { return i < gCount ? stateOn[i] : false; }

void modeFillJson(JsonObject obj) {
  for (uint8_t i = 0; i < gCount; i++) obj[gInputs[i].name] = (bool)stateOn[i];
}
