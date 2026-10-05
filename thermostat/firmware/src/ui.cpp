#include "ui.h"

#include <lvgl.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <ctime>

using tstat::Call;
using tstat::Mode;
using tstat::Source;
using tstat::Wait;

namespace {
// ---- colours (the preview's)
const lv_color_t BG = lv_color_hex(0x0d1013), BAR = lv_color_hex(0x111417), LINE = lv_color_hex(0x262d34),
                 INK = lv_color_hex(0xe4e8eb), MUTED = lv_color_hex(0x9ea8b1), FAINT = lv_color_hex(0x848f99),
                 BTN = lv_color_hex(0x1b2026), BTN_LINE = lv_color_hex(0x3a434c), BRAND = lv_color_hex(0x7dff08),
                 CARD = lv_color_hex(0x151a1f);
lv_color_t level_color(Level l) {
    switch (l) {
        case L_OK: return lv_color_hex(0x5cb87a);
        case L_ADVISORY: return lv_color_hex(0x73aee2);
        case L_CAUTION: return lv_color_hex(0xe2a33b);
        case L_FAULT: return lv_color_hex(0xf06a60);
        default: return FAINT;
    }
}
const char* level_word(Level l) {
    static const char* w[] = {"System OK", "Good to know", "Check soon", "Service needed", "No data"};
    return w[l];
}

UiActions act;
UiModel model;
const Feed* feed = nullptr;
uint32_t shown_feed = UINT32_MAX;
enum Tab { HOME, ALERTS, SERVICE } tab = HOME;
bool unlocked = false;
std::string pin;
int range_h = 6;

// ---- widgets
lv_obj_t *top_clock, *top_out, *top_status, *body[3], *tab_btn[3], *tab_badge;
lv_obj_t *room_lbl, *doing_lbl, *source_lbl, *resume_btn, *step_box[2], *step_val[2], *mode_btn[4];
lv_obj_t *al_head, *al_sub, *al_list, *hl_list, *detail;
lv_obj_t *pin_box, *pin_dots, *svc_box, *chips[8], *chart_p, *chart_t, *chart_on, *marker_layer, *diag_row, *range_btn[2];
lv_chart_series_t *s_low, *s_high, *s_ret, *s_sup, *s_oat, *s_on;

lv_obj_t* label(lv_obj_t* parent, const lv_font_t* font, lv_color_t color, const char* text = "") {
    lv_obj_t* l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, color, 0);
    lv_label_set_text(l, text);
    return l;
}

lv_obj_t* box(lv_obj_t* parent, int x, int y, int w, int h) {
    lv_obj_t* o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    return o;
}

lv_obj_t* button(lv_obj_t* parent, const char* text, const lv_font_t* font, lv_event_cb_t cb, void* data) {
    lv_obj_t* b = lv_btn_create(parent);
    lv_obj_set_style_bg_color(b, BTN, 0);
    lv_obj_set_style_bg_color(b, lv_color_hex(0x29313a), LV_STATE_PRESSED);
    lv_obj_set_style_border_color(b, BTN_LINE, 0);
    lv_obj_set_style_border_width(b, 1, 0);
    lv_obj_set_style_radius(b, 10, 0);
    lv_obj_set_style_shadow_width(b, 0, 0);
    lv_obj_t* l = label(b, font, INK, text);
    lv_obj_center(l);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, data);
    return b;
}

void fmt_num(char* out, size_t n, double v, int digits, const char* unit) {
    if (std::isnan(v)) snprintf(out, n, "-");
    else snprintf(out, n, "%.*f%s", digits, v, unit);
}

void clock_text(char* out, size_t n, const tstat::LocalTime& t) {
    const int h12 = t.hour % 12 == 0 ? 12 : t.hour % 12;
    snprintf(out, n, "%d:%02d %s", h12, t.min, t.hour < 12 ? "AM" : "PM");
}

void show_tab(Tab t);

// ---------------------------------------------------------------- events
void on_tab(lv_event_t* e) { show_tab(static_cast<Tab>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)))); }

void on_step(lv_event_t* e) {
    const intptr_t d = reinterpret_cast<intptr_t>(lv_event_get_user_data(e));   // +-1 heat, +-2 cool
    const bool heat = d == 1 || d == -1;
    const double cur = heat ? model.sp.heat : model.sp.cool;
    const double v = std::round(cur) + (d > 0 ? 1 : -1);
    if (v >= 50 && v <= 90 && act.setpoint) act.setpoint(heat, v);
}

void on_mode(lv_event_t* e) {
    if (act.mode) act.mode(static_cast<Mode>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e))));
}

void on_resume(lv_event_t*) {
    if (act.resume) act.resume();
}

void on_alert(lv_event_t* e) {
    const int i = static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
    if (!feed || i >= static_cast<int>(feed->alerts.size())) return;
    const FeedAlert& a = feed->alerts[i];
    lv_obj_clean(detail);
    lv_obj_t* w = label(detail, &lv_font_montserrat_16, level_color(a.level), a.level == L_FAULT ? "Service needed" : a.level == L_CAUTION ? "Check soon" : "Good to know");
    lv_obj_set_pos(w, 24, 16);
    lv_obj_t* t = label(detail, &lv_font_montserrat_28, INK, a.title.c_str());
    lv_obj_set_width(t, 750);
    lv_obj_set_pos(t, 24, 44);
    const char* heads[] = {"Why it matters", "What you can do"};
    const std::string* texts[] = {&a.why, &a.todo};
    int y = 100;
    for (int k = 0; k < 2; k++) {
        lv_obj_t* h = label(detail, &lv_font_montserrat_16, MUTED, heads[k]);
        lv_obj_set_pos(h, 24, y);
        lv_obj_t* p = label(detail, &lv_font_montserrat_20, INK, texts[k]->c_str());
        lv_label_set_long_mode(p, LV_LABEL_LONG_WRAP);
        lv_obj_set_width(p, 750);
        lv_obj_set_pos(p, 24, y + 24);
        y += 100;
    }
    lv_obj_t* tech = label(detail, &lv_font_montserrat_14, FAINT, ("For your contractor: " + a.tech).c_str());
    lv_label_set_long_mode(tech, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(tech, 750);
    lv_obj_set_pos(tech, 24, 266);
    lv_obj_t* back = button(detail, "Back", &lv_font_montserrat_20, [](lv_event_t*) { lv_obj_add_flag(detail, LV_OBJ_FLAG_HIDDEN); }, nullptr);
    lv_obj_set_size(back, 120, 48);
    lv_obj_set_pos(back, 24, 318);
    lv_obj_clear_flag(detail, LV_OBJ_FLAG_HIDDEN);
}

void on_pin(lv_event_t* e) {
    lv_obj_t* m = lv_event_get_target(e);
    const char* k = lv_btnmatrix_get_btn_text(m, lv_btnmatrix_get_selected_btn(m));
    if (!k) return;
    if (!strcmp(k, LV_SYMBOL_BACKSPACE)) {
        if (!pin.empty()) pin.pop_back();
    } else if (!strcmp(k, "OK")) {
        unlocked = pin == model.tech.service_pin;
        pin.clear();
        if (unlocked) {
            shown_feed = UINT32_MAX;                // draw the charts now
            show_tab(SERVICE);
            return;
        }
    } else if (pin.size() < 6) {
        pin += k;
    }
    std::string dots;
    for (size_t i = 0; i < std::max<size_t>(4, pin.size()); i++) dots += i < pin.size() ? "* " : "_ ";
    lv_label_set_text(pin_dots, dots.c_str());
}

void on_range(lv_event_t* e) {
    range_h = static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
    shown_feed = UINT32_MAX;
}

void on_lock(lv_event_t*) {
    unlocked = false;
    show_tab(HOME);
}

// ---------------------------------------------------------------- building
void build_home(lv_obj_t* p) {
    lv_obj_t* l = label(p, &lv_font_montserrat_16, MUTED, "Inside");
    lv_obj_set_pos(l, 30, 22);
    room_lbl = label(p, &lv_font_montserrat_48, INK, "-");
    lv_obj_set_style_transform_zoom(room_lbl, 512, 0);   // 2x: about 96 px tall
    lv_obj_set_style_transform_pivot_x(room_lbl, 0, 0);
    lv_obj_set_style_transform_pivot_y(room_lbl, 0, 0);
    lv_obj_set_pos(room_lbl, 30, 48);
    doing_lbl = label(p, &lv_font_montserrat_20, INK, "");
    lv_obj_set_width(doing_lbl, 440);
    lv_label_set_long_mode(doing_lbl, LV_LABEL_LONG_WRAP);
    lv_obj_set_pos(doing_lbl, 30, 170);
    source_lbl = label(p, &lv_font_montserrat_16, MUTED, "");
    lv_obj_set_pos(source_lbl, 30, 330);
    resume_btn = button(p, "Resume schedule", &lv_font_montserrat_16, on_resume, nullptr);
    lv_obj_set_size(resume_btn, 180, 40);
    lv_obj_set_pos(resume_btn, 290, 318);

    lv_obj_t* side = box(p, 500, 0, 300, 376);
    lv_obj_set_style_border_side(side, LV_BORDER_SIDE_LEFT, 0);
    lv_obj_set_style_border_color(side, LINE, 0);
    lv_obj_set_style_border_width(side, 1, 0);
    const char* titles[] = {"Heat to", "Cool to"};
    for (int k = 0; k < 2; k++) {
        step_box[k] = box(side, 20, 14 + k * 112, 260, 104);
        lv_obj_t* t = label(step_box[k], &lv_font_montserrat_16, MUTED, titles[k]);
        lv_obj_set_pos(t, 0, 0);
        lv_obj_t* minus = button(step_box[k], "-", &lv_font_montserrat_28, on_step, reinterpret_cast<void*>(static_cast<intptr_t>(k ? -2 : -1)));
        lv_obj_set_size(minus, 64, 64);
        lv_obj_set_pos(minus, 0, 30);
        lv_obj_t* plus = button(step_box[k], "+", &lv_font_montserrat_28, on_step, reinterpret_cast<void*>(static_cast<intptr_t>(k ? 2 : 1)));
        lv_obj_set_size(plus, 64, 64);
        lv_obj_set_pos(plus, 196, 30);
        step_val[k] = label(step_box[k], &lv_font_montserrat_48, INK, "-");
        lv_obj_align(step_val[k], LV_ALIGN_TOP_MID, 0, 36);
    }
    const Mode modes[] = {Mode::Cool, Mode::Heat, Mode::Auto, Mode::Off};
    const char* names[] = {"Cool", "Heat", "Auto", "Off"};
    for (int i = 0; i < 4; i++) {
        mode_btn[i] = button(side, names[i], &lv_font_montserrat_16, on_mode, reinterpret_cast<void*>(static_cast<intptr_t>(modes[i])));
        lv_obj_set_size(mode_btn[i], 125, 46);
        lv_obj_set_pos(mode_btn[i], 20 + (i % 2) * 135, 258 + (i / 2) * 54);
    }
}

void build_alerts(lv_obj_t* p) {
    lv_obj_t* left = box(p, 0, 0, 400, 376);
    al_head = label(left, &lv_font_montserrat_20, INK, "");
    lv_obj_set_width(al_head, 360);
    lv_label_set_long_mode(al_head, LV_LABEL_LONG_WRAP);
    lv_obj_set_pos(al_head, 20, 16);
    al_sub = label(left, &lv_font_montserrat_14, MUTED, "");
    lv_obj_set_pos(al_sub, 20, 70);
    al_list = lv_obj_create(left);
    lv_obj_remove_style_all(al_list);
    lv_obj_set_pos(al_list, 20, 96);
    lv_obj_set_size(al_list, 364, 270);
    lv_obj_set_flex_flow(al_list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(al_list, 8, 0);

    lv_obj_t* right = box(p, 400, 0, 400, 376);
    lv_obj_set_style_border_side(right, LV_BORDER_SIDE_LEFT, 0);
    lv_obj_set_style_border_color(right, LINE, 0);
    lv_obj_set_style_border_width(right, 1, 0);
    lv_obj_t* h = label(right, &lv_font_montserrat_20, INK, "System health");
    lv_obj_set_pos(h, 20, 16);
    hl_list = lv_obj_create(right);
    lv_obj_remove_style_all(hl_list);
    lv_obj_set_pos(hl_list, 20, 50);
    lv_obj_set_size(hl_list, 364, 320);
    lv_obj_set_flex_flow(hl_list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(hl_list, 6, 0);
    lv_obj_add_flag(hl_list, LV_OBJ_FLAG_SCROLLABLE);

    detail = box(p, 0, 0, 800, 376);
    lv_obj_set_style_bg_color(detail, BG, 0);
    lv_obj_set_style_bg_opa(detail, LV_OPA_COVER, 0);
    lv_obj_add_flag(detail, LV_OBJ_FLAG_HIDDEN);
}

lv_obj_t* make_chart(lv_obj_t* p, int y, int h) {
    lv_obj_t* c = lv_chart_create(p);
    lv_obj_set_pos(c, 40, y);
    lv_obj_set_size(c, 744, h);
    lv_obj_set_style_bg_opa(c, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(c, 0, 0);
    lv_obj_set_style_pad_all(c, 0, 0);
    lv_obj_set_style_line_color(c, LINE, LV_PART_MAIN);
    lv_obj_set_style_size(c, 0, LV_PART_INDICATOR);        // no dots on the lines
    lv_obj_set_style_line_width(c, 2, LV_PART_ITEMS);
    lv_chart_set_div_line_count(c, 3, 0);
    lv_chart_set_type(c, LV_CHART_TYPE_LINE);
    lv_chart_set_update_mode(c, LV_CHART_UPDATE_MODE_SHIFT);
    return c;
}

void build_service(lv_obj_t* p) {
    pin_box = box(p, 0, 0, 800, 376);
    lv_obj_t* t = label(pin_box, &lv_font_montserrat_20, INK, "Service PIN");
    lv_obj_align(t, LV_ALIGN_TOP_MID, 0, 14);
    pin_dots = label(pin_box, &lv_font_montserrat_28, INK, "_ _ _ _ ");
    lv_obj_align(pin_dots, LV_ALIGN_TOP_MID, 0, 44);
    static const char* keys[] = {"1", "2", "3", "\n", "4", "5", "6", "\n", "7", "8", "9", "\n", LV_SYMBOL_BACKSPACE, "0", "OK", ""};
    lv_obj_t* m = lv_btnmatrix_create(pin_box);
    lv_btnmatrix_set_map(m, keys);
    lv_obj_set_size(m, 300, 280);
    lv_obj_align(m, LV_ALIGN_TOP_MID, 0, 88);
    lv_obj_set_style_bg_opa(m, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(m, 0, 0);
    lv_obj_set_style_bg_color(m, BTN, LV_PART_ITEMS);
    lv_obj_set_style_text_color(m, INK, LV_PART_ITEMS);
    lv_obj_set_style_text_font(m, &lv_font_montserrat_20, LV_PART_ITEMS);
    lv_obj_add_event_cb(m, on_pin, LV_EVENT_VALUE_CHANGED, nullptr);

    svc_box = box(p, 0, 0, 800, 376);
    for (int i = 0; i < 8; i++) {
        chips[i] = box(svc_box, 10 + i * 98, 6, 92, 44);
        lv_obj_set_style_bg_color(chips[i], CARD, 0);
        lv_obj_set_style_bg_opa(chips[i], LV_OPA_COVER, 0);
        lv_obj_set_style_border_color(chips[i], LINE, 0);
        lv_obj_set_style_border_width(chips[i], 1, 0);
        lv_obj_set_style_radius(chips[i], 6, 0);
        lv_obj_t* a = label(chips[i], &lv_font_montserrat_12, FAINT, "");
        lv_obj_set_pos(a, 6, 3);
        lv_obj_t* b = label(chips[i], &lv_font_montserrat_16, INK, "-");
        lv_obj_set_pos(b, 6, 20);
    }
    for (int k = 0; k < 2; k++) {
        range_btn[k] = button(svc_box, k ? "6 h" : "1 h", &lv_font_montserrat_14, on_range, reinterpret_cast<void*>(static_cast<intptr_t>(k ? 6 : 1)));
        lv_obj_set_size(range_btn[k], 56, 28);
        lv_obj_set_pos(range_btn[k], 600 + k * 62, 56);
    }
    lv_obj_t* lock = button(svc_box, "Lock", &lv_font_montserrat_14, on_lock, nullptr);
    lv_obj_set_size(lock, 64, 28);
    lv_obj_set_pos(lock, 726, 56);
    lv_obj_t* key = label(svc_box, &lv_font_montserrat_12, MUTED, "Suction / liquid psig   |   Return / supply / outdoor F   |   shaded: electrical issue");
    lv_obj_set_pos(key, 10, 62);
    chart_p = make_chart(svc_box, 92, 100);
    s_low = lv_chart_add_series(chart_p, lv_color_hex(0x8fb6d6), LV_CHART_AXIS_PRIMARY_Y);
    s_high = lv_chart_add_series(chart_p, lv_color_hex(0xd6a583), LV_CHART_AXIS_PRIMARY_Y);
    chart_t = make_chart(svc_box, 200, 100);
    s_ret = lv_chart_add_series(chart_t, lv_color_hex(0xd6bf6e), LV_CHART_AXIS_PRIMARY_Y);
    s_sup = lv_chart_add_series(chart_t, lv_color_hex(0x62c3b5), LV_CHART_AXIS_PRIMARY_Y);
    s_oat = lv_chart_add_series(chart_t, lv_color_hex(0xad9fd8), LV_CHART_AXIS_PRIMARY_Y);
    chart_on = make_chart(svc_box, 304, 8);
    lv_chart_set_type(chart_on, LV_CHART_TYPE_BAR);
    lv_chart_set_div_line_count(chart_on, 0, 0);
    lv_chart_set_range(chart_on, LV_CHART_AXIS_PRIMARY_Y, 0, 1);
    lv_obj_set_style_pad_column(chart_on, 0, 0);
    s_on = lv_chart_add_series(chart_on, lv_color_hex(0x5cb87a), LV_CHART_AXIS_PRIMARY_Y);
    marker_layer = box(svc_box, 40, 78, 744, 234);
    lv_obj_clear_flag(marker_layer, LV_OBJ_FLAG_CLICKABLE);
    diag_row = box(svc_box, 10, 318, 780, 54);
    lv_obj_set_flex_flow(diag_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(diag_row, 6, 0);
}

// ---------------------------------------------------------------- updating
void set_chip(int i, const char* name, const char* value, bool bad) {
    lv_label_set_text(lv_obj_get_child(chips[i], 0), name);
    lv_label_set_text(lv_obj_get_child(chips[i], 1), value);
    lv_obj_set_style_border_color(chips[i], bad ? level_color(L_CAUTION) : LINE, 0);
}

bool has_alert(std::initializer_list<const char*> codes) {
    if (!feed) return false;
    for (const FeedAlert& a : feed->alerts)
        for (const char* c : codes)
            if (a.code == c) return true;
    return false;
}

void fill_series(lv_obj_t* chart, lv_chart_series_t* s, const std::vector<float>& v, size_t first) {
    lv_coord_t* ys = lv_chart_get_y_array(chart, s);
    const size_t n = v.size() > first ? v.size() - first : 0;
    for (size_t i = 0; i < n; i++) ys[i] = std::isnan(v[first + i]) ? LV_CHART_POINT_NONE : static_cast<lv_coord_t>(std::lround(v[first + i]));
}

void range_of(std::initializer_list<const std::vector<float>*> vs, size_t first, lv_obj_t* chart) {
    float lo = INFINITY, hi = -INFINITY;
    for (auto* v : vs)
        for (size_t i = first; i < v->size(); i++)
            if (!std::isnan((*v)[i])) { lo = std::min(lo, (*v)[i]); hi = std::max(hi, (*v)[i]); }
    if (lo > hi) { lo = 0; hi = 1; }
    const float pad = std::max((hi - lo) * 0.12f, 2.0f);
    lv_chart_set_range(chart, LV_CHART_AXIS_PRIMARY_Y, static_cast<lv_coord_t>(std::floor(lo - pad)), static_cast<lv_coord_t>(std::ceil(hi + pad)));
}

void draw_service_feed() {
    const Feed& f = *feed;
    char a[24], b[24];
    snprintf(a, sizeof a, "%s", f.mode.empty() ? "-" : f.mode.c_str());
    set_chip(0, "Mode", a, false);
    fmt_num(b, sizeof b, f.sh, 1, " F"); set_chip(1, "Superheat", b, has_alert({"sh_low", "sh_high"}));
    fmt_num(b, sizeof b, f.sc, 1, " F"); set_chip(2, "Subcooling", b, has_alert({"sc_low", "sc_high"}));
    fmt_num(b, sizeof b, f.dt, 1, " F"); set_chip(3, "Delta-T", b, has_alert({"dt_low"}));
    fmt_num(b, sizeof b, f.p_low, 0, " psig"); set_chip(4, "Suction", b, false);
    fmt_num(b, sizeof b, f.p_high, 0, " psig"); set_chip(5, "Liquid", b, false);
    fmt_num(b, sizeof b, f.line_v, 0, " V"); set_chip(6, "Line", f.has_elec ? b : "-", has_alert({"voltage"}));
    fmt_num(b, sizeof b, f.comp_a, 1, " A"); set_chip(7, "Compressor", f.has_elec ? b : "-", has_alert({"comp_amps_high", "comp_not_running"}));

    const size_t n = f.on.size();
    const size_t want = static_cast<size_t>(range_h * 3600 / (f.step > 0 ? f.step : 120)) + 1;
    const size_t first = n > want ? n - want : 0;
    const uint16_t pts = static_cast<uint16_t>(n - first);
    for (lv_obj_t* c : {chart_p, chart_t, chart_on}) lv_chart_set_point_count(c, pts ? pts : 1);
    fill_series(chart_p, s_low, f.p_low_s, first);
    fill_series(chart_p, s_high, f.p_high_s, first);
    fill_series(chart_t, s_ret, f.t_ret_s, first);
    fill_series(chart_t, s_sup, f.t_sup_s, first);
    fill_series(chart_t, s_oat, f.oat_s, first);
    lv_coord_t* on = lv_chart_get_y_array(chart_on, s_on);
    for (size_t i = 0; i < pts; i++) on[i] = f.on[first + i] ? 1 : 0;
    range_of({&f.p_low_s, &f.p_high_s}, first, chart_p);
    range_of({&f.t_ret_s, &f.t_sup_s, &f.oat_s}, first, chart_t);
    for (lv_obj_t* c : {chart_p, chart_t, chart_on}) lv_chart_refresh(c);
    for (int k = 0; k < 2; k++) lv_obj_set_style_border_color(range_btn[k], (k ? 6 : 1) == range_h ? BRAND : BTN_LINE, 0);

    // markers: shaded spans with a numbered tag, where the electrical module found a problem
    lv_obj_clean(marker_layer);
    const double x0 = f.t0 + first * f.step, x1 = f.t0 + (n ? n - 1 : 0) * f.step, span = x1 > x0 ? x1 - x0 : 1;
    int num = 0;
    for (const FeedMarker& m : f.markers) {
        num++;
        const double end = std::isnan(m.end) ? x1 + f.step : m.end;
        if (end < x0 || m.start > x1) continue;
        const int a_px = static_cast<int>(std::max(0.0, (m.start - x0) / span) * 744);
        const int b_px = static_cast<int>(std::min(1.0, (end - x0) / span) * 744);
        lv_obj_t* r = box(marker_layer, a_px, 14, std::max(b_px - a_px, 3), 220);
        lv_obj_set_style_bg_color(r, level_color(m.level), 0);
        lv_obj_set_style_bg_opa(r, LV_OPA_20, 0);
        lv_obj_set_style_border_side(r, LV_BORDER_SIDE_LEFT, 0);
        lv_obj_set_style_border_color(r, level_color(m.level), 0);
        lv_obj_set_style_border_width(r, 2, 0);
        char tag[64];
        snprintf(tag, sizeof tag, "%d %s", num, m.label.c_str());
        lv_obj_t* t = label(marker_layer, &lv_font_montserrat_12, BG, tag);
        lv_obj_set_style_bg_color(t, level_color(m.level), 0);
        lv_obj_set_style_bg_opa(t, LV_OPA_COVER, 0);
        lv_obj_set_style_pad_hor(t, 4, 0);
        lv_obj_set_pos(t, std::min(a_px, 744 - 170), 0);
    }

    lv_obj_clean(diag_row);
    int shown = 0;
    for (const FeedAlert& al : f.alerts) {
        if (shown++ == 4) break;
        lv_obj_t* d = box(diag_row, 0, 0, 190, 50);
        lv_obj_set_style_bg_color(d, CARD, 0);
        lv_obj_set_style_bg_opa(d, LV_OPA_COVER, 0);
        lv_obj_set_style_border_side(d, LV_BORDER_SIDE_LEFT, 0);
        lv_obj_set_style_border_width(d, 4, 0);
        lv_obj_set_style_border_color(d, level_color(al.level), 0);
        lv_obj_set_style_radius(d, 6, 0);
        lv_obj_t* h = label(d, &lv_font_montserrat_12, INK, (al.tech_word + " - " + al.code).c_str());
        lv_obj_set_pos(h, 8, 3);
        lv_obj_t* x = label(d, &lv_font_montserrat_12, MUTED, al.tech.c_str());
        lv_label_set_long_mode(x, LV_LABEL_LONG_DOT);
        lv_obj_set_width(x, 176);
        lv_obj_set_pos(x, 8, 22);
    }
    if (!shown) {
        lv_obj_t* x = label(diag_row, &lv_font_montserrat_14, MUTED, "No open diagnostics: all checks normal");
        (void)x;
    }
}

void draw_alerts_feed() {
    const Feed& f = *feed;
    lv_obj_clean(al_list);
    int i = 0;
    for (const FeedAlert& a : f.alerts) {
        lv_obj_t* c = lv_btn_create(al_list);
        lv_obj_set_width(c, 360);
        lv_obj_set_height(c, LV_SIZE_CONTENT);
        lv_obj_set_style_bg_color(c, BTN, 0);
        lv_obj_set_style_border_side(c, LV_BORDER_SIDE_LEFT, 0);
        lv_obj_set_style_border_width(c, 6, 0);
        lv_obj_set_style_border_color(c, level_color(a.level), 0);
        lv_obj_set_style_radius(c, 8, 0);
        lv_obj_set_style_shadow_width(c, 0, 0);
        lv_obj_set_style_pad_all(c, 10, 0);
        lv_obj_set_flex_flow(c, LV_FLEX_FLOW_COLUMN);
        lv_obj_t* t = label(c, &lv_font_montserrat_16, INK, a.title.c_str());
        lv_label_set_long_mode(t, LV_LABEL_LONG_WRAP);
        lv_obj_set_width(t, 330);
        label(c, &lv_font_montserrat_14, MUTED, "Tap for what to do");
        lv_obj_add_event_cb(c, on_alert, LV_EVENT_CLICKED, reinterpret_cast<void*>(static_cast<intptr_t>(i++)));
    }
    if (f.alerts.empty()) {
        lv_obj_t* e = label(al_list, &lv_font_montserrat_16, MUTED, "No alerts. Fullscope checks your system continuously while it runs.");
        lv_label_set_long_mode(e, LV_LABEL_LONG_WRAP);
        lv_obj_set_width(e, 360);
    }
    lv_obj_clean(hl_list);
    for (const FeedHealth& h : f.health) {
        lv_obj_t* row = box(hl_list, 0, 0, 360, 58);
        lv_obj_t* n = label(row, &lv_font_montserrat_16, INK, h.name.c_str());
        lv_obj_set_pos(n, 0, 0);
        lv_obj_t* w = label(row, &lv_font_montserrat_14, level_color(h.level), h.word.c_str());
        lv_obj_align(w, LV_ALIGN_TOP_RIGHT, 0, 2);
        lv_obj_t* x = label(row, &lv_font_montserrat_12, MUTED, h.text.c_str());
        lv_label_set_long_mode(x, LV_LABEL_LONG_WRAP);
        lv_obj_set_width(x, 360);
        lv_obj_set_pos(x, 0, 22);
    }
}

void show_tab(Tab t) {
    tab = t;
    for (int i = 0; i < 3; i++) {
        if (i == t) lv_obj_clear_flag(body[i], LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(body[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_border_color(tab_btn[i], i == t ? BRAND : BAR, 0);
        lv_obj_set_style_text_color(lv_obj_get_child(tab_btn[i], 0), i == t ? INK : MUTED, 0);
    }
    if (t == SERVICE) {
        if (unlocked) { lv_obj_add_flag(pin_box, LV_OBJ_FLAG_HIDDEN); lv_obj_clear_flag(svc_box, LV_OBJ_FLAG_HIDDEN); }
        else { lv_obj_clear_flag(pin_box, LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(svc_box, LV_OBJ_FLAG_HIDDEN); }
    }
    if (t != ALERTS) lv_obj_add_flag(detail, LV_OBJ_FLAG_HIDDEN);
}
}  // namespace

void ui_begin(const UiActions& actions) {
    act = actions;
    lv_obj_t* scr = lv_scr_act();
    lv_obj_set_style_bg_color(scr, BG, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t* top = box(scr, 0, 0, 800, 44);
    lv_obj_set_style_border_side(top, LV_BORDER_SIDE_BOTTOM, 0);
    lv_obj_set_style_border_color(top, LINE, 0);
    lv_obj_set_style_border_width(top, 1, 0);
    top_clock = label(top, &lv_font_montserrat_20, INK, "--:--");
    lv_obj_set_pos(top_clock, 18, 10);
    top_out = label(top, &lv_font_montserrat_16, MUTED, "");
    lv_obj_set_pos(top_out, 140, 13);
    top_status = label(top, &lv_font_montserrat_16, FAINT, "");
    lv_obj_align(top_status, LV_ALIGN_TOP_RIGHT, -18, 13);
    lv_obj_add_flag(top_status, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(top_status, on_tab, LV_EVENT_CLICKED, reinterpret_cast<void*>(static_cast<intptr_t>(ALERTS)));

    for (int i = 0; i < 3; i++) body[i] = box(scr, 0, 44, 800, 376);
    build_home(body[HOME]);
    build_alerts(body[ALERTS]);
    build_service(body[SERVICE]);

    lv_obj_t* bar = box(scr, 0, 420, 800, 60);
    lv_obj_set_style_bg_color(bar, BAR, 0);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
    const char* names[] = {"Home", "Alerts", "Service"};
    for (int i = 0; i < 3; i++) {
        tab_btn[i] = box(bar, i * 267, 0, 266, 60);
        lv_obj_add_flag(tab_btn[i], LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_style_border_side(tab_btn[i], LV_BORDER_SIDE_TOP, 0);
        lv_obj_set_style_border_width(tab_btn[i], 3, 0);
        lv_obj_t* l = label(tab_btn[i], &lv_font_montserrat_16, MUTED, names[i]);
        lv_obj_center(l);
        lv_obj_add_event_cb(tab_btn[i], on_tab, LV_EVENT_CLICKED, reinterpret_cast<void*>(static_cast<intptr_t>(i)));
    }
    tab_badge = label(tab_btn[ALERTS], &lv_font_montserrat_14, BG, "");
    lv_obj_set_style_bg_opa(tab_badge, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(tab_badge, 11, 0);
    lv_obj_set_style_pad_hor(tab_badge, 7, 0);
    lv_obj_align(tab_badge, LV_ALIGN_CENTER, 50, 0);
    show_tab(HOME);
}

void ui_update(const UiModel& m, const Feed& f, uint32_t feed_version) {
    model = m;
    feed = &f;
    char buf[96];

    // auto-lock the service page after 5 minutes without a touch
    if (unlocked && lv_disp_get_inactive_time(nullptr) > 5 * 60 * 1000) {
        unlocked = false;
        if (tab == SERVICE) show_tab(HOME);
    }

    // top bar
    if (m.clock_ok) clock_text(buf, sizeof buf, m.now); else snprintf(buf, sizeof buf, "--:--");
    lv_label_set_text(top_clock, buf);
    std::string out = "Outside ";
    char t[24];
    fmt_num(t, sizeof t, m.outdoor, 0, "\xC2\xB0");
    out += t;
    if (!std::isnan(m.rh)) { snprintf(t, sizeof t, "   Humidity %.0f %%", m.rh); out += t; }
    if (!m.online) out += "   Offline: running on its own";
    lv_label_set_text(top_out, out.c_str());
    const Level st = f.valid ? f.status : L_OFFLINE;
    lv_label_set_text(top_status, level_word(st));
    lv_obj_set_style_text_color(top_status, level_color(st), 0);
    lv_obj_align(top_status, LV_ALIGN_TOP_RIGHT, -18, 13);
    if (f.valid && !f.alerts.empty()) {
        snprintf(buf, sizeof buf, "%u", static_cast<unsigned>(f.alerts.size()));
        lv_label_set_text(tab_badge, buf);
        lv_obj_set_style_bg_color(tab_badge, level_color(f.alerts[0].level), 0);
        lv_obj_clear_flag(tab_badge, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(tab_badge, LV_OBJ_FLAG_HIDDEN);
    }

    // home
    const tstat::Report& r = m.rep;
    fmt_num(buf, sizeof buf, std::isnan(r.room) ? NAN : std::round(r.room), 0, "\xC2\xB0");
    lv_label_set_text(room_lbl, m.have_report ? buf : "-");
    const char* doing;
    if (!m.have_report) doing = "Starting...";
    else if (r.wait == Wait::Sensor) doing = "Room sensor problem: heating and cooling are off";
    else if (r.wait == Wait::MinOff) doing = "Protecting the compressor: starting in a few minutes";
    else if (r.wait == Wait::MinOn) doing = "Finishing a minimum run";
    else if (r.wait == Wait::MaxStarts) doing = "Resting the compressor";
    else if (r.call == Call::Cool) { snprintf(buf, sizeof buf, "Cooling to %.0f\xC2\xB0", m.sp.cool); doing = buf; }
    else if (r.call == Call::Heat) {
        snprintf(buf, sizeof buf, "Heating to %.0f\xC2\xB0%s", m.sp.heat, r.out.W ? " - backup heat" : "");
        doing = buf;
    }
    else if (m.settings.mode == Mode::Off) doing = "System off";
    else doing = "Holding temperature";
    lv_label_set_text(doing_lbl, doing);
    if (m.sp.source == Source::Hold) {
        if (m.sp.has_next) {
            char c[16];
            clock_text(c, sizeof c, m.sp.hold_until);
            snprintf(buf, sizeof buf, "Held until %s", c);
        } else {
            snprintf(buf, sizeof buf, "Held");
        }
        lv_obj_clear_flag(resume_btn, LV_OBJ_FLAG_HIDDEN);
    } else {
        snprintf(buf, sizeof buf, "%s", m.sp.source == Source::Schedule ? "Following your schedule" : "");
        lv_obj_add_flag(resume_btn, LV_OBJ_FLAG_HIDDEN);
    }
    if (m.pending) strncat(buf, " (sending...)", sizeof buf - strlen(buf) - 1);
    lv_label_set_text(source_lbl, buf);
    const Mode mode = m.settings.mode;
    const bool show[2] = {mode == Mode::Heat || mode == Mode::Auto || mode == Mode::EmergencyHeat, mode == Mode::Cool || mode == Mode::Auto};
    const double vals[2] = {m.sp.heat, m.sp.cool};
    int y = 14;
    for (int k = 0; k < 2; k++) {
        if (!show[k]) { lv_obj_add_flag(step_box[k], LV_OBJ_FLAG_HIDDEN); continue; }
        lv_obj_clear_flag(step_box[k], LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_y(step_box[k], y);
        y += 112;
        snprintf(buf, sizeof buf, "%.0f\xC2\xB0", vals[k]);
        lv_label_set_text(step_val[k], buf);
    }
    const Mode modes[] = {Mode::Cool, Mode::Heat, Mode::Auto, Mode::Off};
    for (int i = 0; i < 4; i++) lv_obj_set_style_border_color(mode_btn[i], modes[i] == mode ? BRAND : BTN_LINE, 0);

    // alerts + service: rebuilt when a new feed arrives
    if (f.valid && feed_version != shown_feed) {
        shown_feed = feed_version;
        if (!f.fresh || f.alerts.empty()) lv_label_set_text(al_head, f.headline.c_str());
        else if (f.alerts.size() == 1) lv_label_set_text(al_head, "One thing needs attention");
        else { snprintf(buf, sizeof buf, "%u things need attention", static_cast<unsigned>(f.alerts.size())); lv_label_set_text(al_head, buf); }
        lv_label_set_text(al_sub, level_word(f.status));
        lv_obj_set_style_text_color(al_sub, level_color(f.status), 0);
        draw_alerts_feed();
        if (unlocked) draw_service_feed();
    }
}
