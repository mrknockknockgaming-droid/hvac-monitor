// The touchscreen (LVGL): Home, Alerts (homeowner), Service (technician, behind the PIN).
// Layout follows the browser preview, hvac-cloud/web/thermostat.html.
#pragma once
#include "feed.h"
#include "tstat_core.h"

struct UiModel {
    bool have_report = false;
    tstat::Report rep{};
    tstat::Setpoints sp{};
    tstat::Settings settings;
    tstat::Tech tech;
    double rh = NAN, outdoor = NAN;
    bool clock_ok = false, online = false;
    tstat::LocalTime now;
    bool pending = false;           // a change made here hasn't reached the cloud yet
};

struct UiActions {
    void (*setpoint)(bool heat, double value);    // the -/+ buttons: a hold until the next change
    void (*mode)(tstat::Mode mode);
    void (*resume)();
};

void ui_begin(const UiActions& actions);
// Call about once a second; the feed's lists and charts are rebuilt only when feed_version changes.
void ui_update(const UiModel& m, const Feed& feed, uint32_t feed_version);
