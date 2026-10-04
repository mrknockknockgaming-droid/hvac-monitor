#include "display.h"

#include <Wire.h>

#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#include <lgfx/v1/platforms/esp32s3/Bus_RGB.hpp>
#include <lgfx/v1/platforms/esp32s3/Panel_RGB.hpp>

#include "board.h"

namespace {

class Panel : public lgfx::LGFX_Device {
public:
    Panel() {
        {
            auto cfg = panel_.config();
            cfg.memory_width = cfg.panel_width = LCD_W;
            cfg.memory_height = cfg.panel_height = LCD_H;
            cfg.offset_x = cfg.offset_y = 0;
            panel_.config(cfg);
        }
        {
            auto cfg = panel_.config_detail();
            cfg.use_psram = 1;
            panel_.config_detail(cfg);
        }
        {
            auto cfg = bus_.config();
            cfg.panel = &panel_;
            const int* order[] = {PIN_LCD_B, PIN_LCD_G, PIN_LCD_R};
            const int counts[] = {5, 6, 5};
            int8_t* pins[16] = {&cfg.pin_d0, &cfg.pin_d1, &cfg.pin_d2, &cfg.pin_d3, &cfg.pin_d4, &cfg.pin_d5,
                                &cfg.pin_d6, &cfg.pin_d7, &cfg.pin_d8, &cfg.pin_d9, &cfg.pin_d10, &cfg.pin_d11,
                                &cfg.pin_d12, &cfg.pin_d13, &cfg.pin_d14, &cfg.pin_d15};
            int k = 0;
            for (int c = 0; c < 3; c++)
                for (int i = 0; i < counts[c]; i++) *pins[k++] = order[c][i];
            cfg.pin_henable = PIN_LCD_DE;
            cfg.pin_vsync = PIN_LCD_VSYNC;
            cfg.pin_hsync = PIN_LCD_HSYNC;
            cfg.pin_pclk = PIN_LCD_PCLK;
            cfg.freq_write = LCD_PCLK_HZ;
            cfg.hsync_polarity = 0;
            cfg.hsync_front_porch = 8;
            cfg.hsync_pulse_width = 4;
            cfg.hsync_back_porch = 8;
            cfg.vsync_polarity = 0;
            cfg.vsync_front_porch = 8;
            cfg.vsync_pulse_width = 4;
            cfg.vsync_back_porch = 8;
            cfg.pclk_idle_high = 1;
            bus_.config(cfg);
        }
        panel_.setBus(&bus_);
        setPanel(&panel_);
    }

private:
    lgfx::Bus_RGB bus_;
    lgfx::Panel_RGB panel_;
};

Panel lcd;
uint8_t exio = EXIO_TP_RST | EXIO_LCD_RST | EXIO_SD_CS;   // backlight off until the first frame
uint8_t touch_addr = GT911_ADDR;
lv_disp_draw_buf_t draw_buf;
lv_disp_drv_t disp_drv;
lv_indev_drv_t indev_drv;

void exio_write() {
    Wire.beginTransmission(CH422G_MODE);
    Wire.write(0x01);
    Wire.endTransmission();
    Wire.beginTransmission(CH422G_OUT);
    Wire.write(exio);
    Wire.endTransmission();
}

bool gt911_write(uint16_t reg, uint8_t v) {
    Wire.beginTransmission(touch_addr);
    Wire.write(reg >> 8);
    Wire.write(reg & 0xFF);
    Wire.write(v);
    return Wire.endTransmission() == 0;
}

bool gt911_read(uint16_t reg, uint8_t* buf, size_t n) {
    Wire.beginTransmission(touch_addr);
    Wire.write(reg >> 8);
    Wire.write(reg & 0xFF);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(touch_addr, static_cast<uint8_t>(n)) != n) return false;
    for (size_t i = 0; i < n; i++) buf[i] = Wire.read();
    return true;
}

void touch_reset() {
    // INT held low through reset selects address 0x5D
    pinMode(PIN_TOUCH_INT, OUTPUT);
    digitalWrite(PIN_TOUCH_INT, LOW);
    exio &= ~EXIO_TP_RST;
    exio_write();
    delay(10);
    exio |= EXIO_TP_RST;
    exio_write();
    delay(60);
    pinMode(PIN_TOUCH_INT, INPUT);
    uint8_t id[4];
    if (!gt911_read(0x8140, id, 4)) {
        touch_addr = GT911_ADDR_ALT;
        if (!gt911_read(0x8140, id, 4)) Serial.println("touch: GT911 not found");
    }
}

void flush_cb(lv_disp_drv_t* drv, const lv_area_t* a, lv_color_t* px) {
    const int w = a->x2 - a->x1 + 1, h = a->y2 - a->y1 + 1;
    lcd.pushImage(a->x1, a->y1, w, h, reinterpret_cast<lgfx::rgb565_t*>(&px->full));
    lv_disp_flush_ready(drv);
}

void touch_cb(lv_indev_drv_t*, lv_indev_data_t* data) {
    static int16_t last_x = 0, last_y = 0;
    uint8_t status = 0;
    data->state = LV_INDEV_STATE_REL;
    if (gt911_read(0x814E, &status, 1) && (status & 0x80)) {
        if ((status & 0x0F) > 0) {
            uint8_t p[5];
            if (gt911_read(0x814F, p, 5)) {
                last_x = p[1] | (p[2] << 8);
                last_y = p[3] | (p[4] << 8);
                data->state = LV_INDEV_STATE_PR;
            }
        }
        gt911_write(0x814E, 0);
    }
    data->point.x = last_x;
    data->point.y = last_y;
}

}  // namespace

bool display_begin() {
    exio_write();
    exio &= ~EXIO_LCD_RST;
    exio_write();
    delay(10);
    exio |= EXIO_LCD_RST;
    exio_write();
    touch_reset();
    if (!lcd.init()) {
        Serial.println("display: panel init failed");
        return false;
    }
    lcd.fillScreen(TFT_BLACK);
    lv_init();
    const size_t buf_px = LCD_W * 60;
    auto* b1 = static_cast<lv_color_t*>(heap_caps_malloc(buf_px * sizeof(lv_color_t), MALLOC_CAP_SPIRAM));
    auto* b2 = static_cast<lv_color_t*>(heap_caps_malloc(buf_px * sizeof(lv_color_t), MALLOC_CAP_SPIRAM));
    lv_disp_draw_buf_init(&draw_buf, b1, b2, buf_px);
    lv_disp_drv_init(&disp_drv);
    disp_drv.hor_res = LCD_W;
    disp_drv.ver_res = LCD_H;
    disp_drv.flush_cb = flush_cb;
    disp_drv.draw_buf = &draw_buf;
    lv_disp_drv_register(&disp_drv);
    lv_indev_drv_init(&indev_drv);
    indev_drv.type = LV_INDEV_TYPE_POINTER;
    indev_drv.read_cb = touch_cb;
    lv_indev_drv_register(&indev_drv);
    display_backlight(true);
    return true;
}

void display_backlight(bool on) {
    if (on) exio |= EXIO_LCD_BL; else exio &= ~EXIO_LCD_BL;
    exio_write();
}

void display_loop() { lv_timer_handler(); }
