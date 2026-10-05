// Screen and touch: LovyanGFX drives the RGB panel, LVGL draws the UI, GT911 touch over Wire.
#pragma once
#include <lvgl.h>

bool display_begin();          // Wire must already be running (io_begin)
void display_backlight(bool on);
void display_loop();           // lv_timer_handler()
