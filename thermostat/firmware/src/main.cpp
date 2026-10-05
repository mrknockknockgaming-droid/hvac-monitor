// Fullscope display thermostat firmware (thermostat/README.md).
//
// The control and safety logic (lib/tstat_core) runs here, on the thermostat: heating and cooling
// keep working with no WiFi, no internet and no cloud. The cloud only sends settings and the
// display feed. Loop: every 2 s read the room, run the controller, set the outputs; every 5 s
// report; feed the hardware watchdog all the time.
#include <Arduino.h>
#include <esp_timer.h>

#include "board.h"
#include "display.h"
#include "io.h"
#include "net.h"
#include "tstat_json.h"
#include "ui.h"

using namespace tstat;

namespace {
Controller ctrl;
Settings settings;              // as the cloud (or this screen) last set them
Tech tech;
int cfg_ver = 0;
bool clock_was_ok = false;
Report last{};
bool have_report = false;
double room = NAN, rh = NAN;
uint32_t t_step = 0, t_report = 0, t_ui = 0, t_wdi = 0;

// The controller runs on the chip's monotonic clock: the wall clock jumps when NTP first sets
// it, and a jump must never make a minimum-off time look already served.
double mono_s() { return esp_timer_get_time() / 1e6; }

// Without a real clock the schedule (and a hold's end time) can't be known: run the base
// setpoints, or a hold with no end time, until NTP sets the clock.
void apply_settings() {
    Settings eff = settings;
    if (!net_state().clock_ok) {
        eff.schedule.clear();
        if (eff.hold.active && eff.hold.has_until) eff.hold.active = false;
    }
    ctrl.configure(eff);
    ctrl.configure(tech);
}

void on_cloud_config(int ver, const Settings& s, const Tech& t) {
    const bool tz_changed = t.tz != tech.tz;
    cfg_ver = ver;
    settings = s;
    tech = t;
    apply_settings();
    if (tz_changed) net_set_tz(tech.tz);
    Serial.printf("config: version %d (%s)\n", ver, name(settings.mode));
}

// ---- changes made on the screen: applied at once, kept in flash, sent to the cloud
LocalTime local_now() {
    LocalTime t;
    net_local_time(t);
    return t;
}

void changed_here(const JsonDocument& change) {
    apply_settings();
    net_save(cfg_ver, settings, tech);          // same version: a stale retained config can't undo it
    net_request(change);
}

void ui_setpoint(bool heat, double v) {
    settings.hold = hold_until_next(settings, local_now(), heat ? v : NAN, heat ? NAN : v);
    if (!net_state().clock_ok) settings.hold.has_until = false;
    JsonDocument change;
    JsonObject h = change["hold"].to<JsonObject>();
    h["heat"] = settings.hold.heat;
    h["cool"] = settings.hold.cool;
    if (settings.hold.has_until) {
        char iso[24];
        format_iso(settings.hold.until, iso, sizeof iso);
        h["until"] = iso;
    } else {
        h["until"] = nullptr;
    }
    changed_here(change);
}

void ui_mode(Mode m) {
    settings.mode = m;
    JsonDocument change;
    change["mode"] = name(m);
    changed_here(change);
}

void ui_resume() {
    settings.hold = Hold();
    JsonDocument change;
    change["resume"] = true;
    changed_here(change);
}

void control_step() {
    io_room(room, rh);
    const NetState& ns = net_state();
    if (ns.clock_ok != clock_was_ok) {
        clock_was_ok = ns.clock_ok;
        apply_settings();
    }
    last = ctrl.step(room, ns.outdoor, mono_s(), local_now());
    have_report = true;
    if (!io_outputs(last.out)) {
        // the expander didn't take it: try once more; if it's gone its outputs are already off
        io_outputs(last.out);
    }
}

void report() {
    JsonDocument doc;
    build_report(doc, last, cfg_ver, rh, millis() / 1000, net_state().clock_ok ? net_epoch() : 0, FW_VERSION);
    const Sensed s = io_sense();
    if (s.ok) {
        JsonObject seen = doc["seen"].to<JsonObject>();   // what the equipment terminals actually get
        seen["Y"] = s.Y;
        seen["W"] = s.W;
        seen["G"] = s.G;
        seen["OB"] = s.OB;
    }
    net_publish_report(doc);
}
}  // namespace

void setup() {
    Serial.begin(115200);
    Serial.printf("\nFullscope thermostat %s\n", FW_VERSION);
    io_begin();                                  // outputs off first, before anything slow
    if (net_load(cfg_ver, settings, tech)) Serial.printf("config: version %d from flash\n", cfg_ver);
    else Serial.println("config: none saved, using defaults");
    apply_settings();
    net_set_tz(tech.tz);
    net_begin(on_cloud_config);
    display_begin();
    ui_begin(UiActions{ui_setpoint, ui_mode, ui_resume});
}

void loop() {
    const uint32_t now = millis();
    if (now - t_wdi >= 200) {                    // TPS3823 times out after ~1.6 s
        t_wdi = now;
        io_watchdog();
    }
    net_loop();
    if (now - t_step >= 2000) {
        t_step = now;
        control_step();
    }
    if (now - t_report >= 5000) {
        t_report = now;
        report();
    }
    if (now - t_ui >= 500) {
        t_ui = now;
        UiModel m;
        m.have_report = have_report;
        m.rep = last;
        m.settings = settings;
        m.tech = tech;
        m.rh = rh;
        m.outdoor = net_state().outdoor;
        m.online = net_state().mqtt;
        m.clock_ok = net_local_time(m.now);
        m.sp = schedule_now(settings, m.now);
        m.pending = net_pending();
        ui_update(m, net_feed(), net_feed_version());
    }
    display_loop();
    delay(5);
}
