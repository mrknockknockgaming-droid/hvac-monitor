#include "tstat_core.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

namespace tstat {

static const char* MODE_NAMES[] = {"off", "heat", "cool", "auto", "emergency_heat"};
static const char* FAN_NAMES[] = {"auto", "on", "circulate"};

const char* name(Mode m) { return MODE_NAMES[static_cast<int>(m)]; }
const char* name(Fan f) { return FAN_NAMES[static_cast<int>(f)]; }
const char* name(Call c) { return c == Call::Heat ? "heat" : c == Call::Cool ? "cool" : nullptr; }
const char* name(Wait w) {
    switch (w) {
        case Wait::MinOn: return "min_on";
        case Wait::MinOff: return "min_off";
        case Wait::MaxStarts: return "max_starts";
        case Wait::Sensor: return "sensor";
        default: return nullptr;
    }
}
const char* name(Source s) { return s == Source::Hold ? "hold" : s == Source::Schedule ? "schedule" : "manual"; }

bool parse(const char* s, Mode& out) {
    for (int i = 0; i < 5; i++)
        if (s && !strcmp(s, MODE_NAMES[i])) { out = static_cast<Mode>(i); return true; }
    return false;
}
bool parse(const char* s, Fan& out) {
    for (int i = 0; i < 3; i++)
        if (s && !strcmp(s, FAN_NAMES[i])) { out = static_cast<Fan>(i); return true; }
    return false;
}

int compare(const LocalTime& a, const LocalTime& b) {
    const int x[] = {a.year, a.mon, a.day, a.hour, a.min, a.sec}, y[] = {b.year, b.mon, b.day, b.hour, b.min, b.sec};
    for (int i = 0; i < 6; i++)
        if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1;
    return 0;
}

bool parse_iso(const char* s, LocalTime& out) {
    LocalTime t;
    int n = s ? sscanf(s, "%d-%d-%dT%d:%d:%d", &t.year, &t.mon, &t.day, &t.hour, &t.min, &t.sec) : 0;
    if (n < 5) return false;
    if (n == 5) t.sec = 0;
    out = t;
    return true;
}

template <typename T>
static void clamp_to(T& v, double lo, double hi) {
    if (!(v >= lo)) v = static_cast<T>(lo);     // also catches NaN
    if (v > hi) v = static_cast<T>(hi);
}

void clamp(Tech& t) {   // TECH_LIMITS in thermostat.py
    clamp_to(t.differential, 0.5, 4.0);
    clamp_to(t.min_on_s, 60, 1800);
    clamp_to(t.min_off_s, 120, 1800);
    clamp_to(t.max_starts_h, 2, 8);
    clamp_to(t.aux_lockout_f, -20.0, 60.0);
    clamp_to(t.comp_lockout_f, -30.0, 50.0);
    clamp_to(t.aux_droop_f, 1.0, 10.0);
    clamp_to(t.aux_delay_s, 0, 3600);
    clamp_to(t.fan_purge_s, 0, 300);
    clamp_to(t.circulate_min_h, 0, 60);
}

// ------------------------------------------------------------------ schedule
Setpoints schedule_now(const Settings& s, const LocalTime& when) {
    Setpoints r{s.heat_sp, s.cool_sp, Source::Manual};
    if (s.hold.active && (!s.hold.has_until || compare(s.hold.until, when) > 0)) {
        r.heat = s.hold.heat;
        r.cool = s.hold.cool;
        r.source = Source::Hold;
        r.has_next = s.hold.has_until;
        r.hold_until = s.hold.until;
        return r;
    }
    struct Entry { int at; const Period* p; };
    std::vector<Entry> periods;
    for (const Period& p : s.schedule)
        for (uint8_t d : p.days) periods.push_back({d * 1440 + p.minute, &p});
    if (periods.empty()) return r;
    std::stable_sort(periods.begin(), periods.end(), [](const Entry& a, const Entry& b) { return a.at < b.at; });
    const int minute = when.wday * 1440 + when.hour * 60 + when.min;
    // Python: max(x <= minute) -> the first of the largest, default periods[-1];
    //         min(x > minute)  -> the first of the smallest, default periods[0]
    const Entry* cur = nullptr;
    for (const Entry& e : periods)
        if (e.at <= minute && (!cur || e.at > cur->at)) cur = &e;
    if (!cur) cur = &periods.back();
    const Entry* nxt = nullptr;
    for (const Entry& e : periods)
        if (e.at > minute && (!nxt || e.at < nxt->at)) nxt = &e;
    if (!nxt) nxt = &periods.front();
    int ahead = ((nxt->at - minute) % (7 * 1440) + 7 * 1440) % (7 * 1440);
    if (ahead == 0) ahead = 7 * 1440;
    r.heat = cur->p->heat;
    r.cool = cur->p->cool;
    r.source = Source::Schedule;
    r.has_next = true;
    r.next_in_min = ahead;
    return r;
}

// ------------------------------------------------------------------ control loop
Call Controller::demand(double room, double heat_sp, double cool_sp) const {
    const Mode mode = settings_.mode;
    const double half = tech_.differential / 2;
    if (mode == Mode::Off) return Call::None;
    const bool heating_ok = mode == Mode::Heat || mode == Mode::Auto || mode == Mode::EmergencyHeat;
    const bool cooling_ok = mode == Mode::Cool || mode == Mode::Auto;
    if (call_ == Call::Cool && cooling_ok && room > cool_sp - half) return Call::Cool;
    if (call_ == Call::Heat && heating_ok && room < heat_sp + half) return Call::Heat;
    if (cooling_ok && room >= cool_sp + half) return Call::Cool;
    if (heating_ok && room <= heat_sp - half) return Call::Heat;
    return Call::None;
}

void Controller::all_off(double now) {
    if (out_.Y) {
        y_off_since_ = now;
        y_on_since_ = NAN;
    }
    out_.Y = out_.W = out_.G = false;
    call_ = Call::None;
    call_since_ = NAN;
    below_since_ = NAN;
}

Report Controller::report(double room, const Setpoints& sp, double now) const {
    Report r;
    r.mode = settings_.mode;
    r.fan = settings_.fan;
    r.heat_sp = sp.heat;
    r.cool_sp = sp.cool;
    r.source = sp.source;
    r.out = out_;
    r.call = call_;
    r.call_min = std::isnan(call_since_) ? NAN : std::round((now - call_since_) / 60 * 10) / 10;
    r.wait = wait_;
    r.room = room;
    return r;
}

Report Controller::step(double room, double outdoor, double now, const LocalTime& local) {
    const Tech& t = tech_;
    Wait wait = Wait::None;
    const Setpoints sp = schedule_now(settings_, local);
    starts_.erase(std::remove_if(starts_.begin(), starts_.end(), [now](double x) { return !(now - x < 3600); }), starts_.end());
    if (std::isnan(room) || !(room >= ROOM_MIN && room <= ROOM_MAX)) {
        all_off(now);                                   // broken sensor: fail safe
        wait_ = Wait::Sensor;
        return report(room, sp, now);
    }

    const Call dem = demand(room, sp.heat, sp.cool);
    if (dem != call_) {
        call_ = dem;
        call_since_ = dem != Call::None ? now : NAN;
    }
    const bool emergency = settings_.mode == Mode::EmergencyHeat;
    const bool have_outdoor = !std::isnan(outdoor);

    // --- compressor (Y)
    const bool want_y = dem == Call::Cool ||
                        (dem == Call::Heat && t.heat_pump && !emergency && (!have_outdoor || outdoor >= t.comp_lockout_f));
    bool y = out_.Y;
    if (y && !want_y) {
        if (settings_.mode != Mode::Off && now - y_on_since_ < t.min_on_s) {
            wait = Wait::MinOn;                         // finish the minimum run
        } else {
            y = false;
            y_off_since_ = now;
            y_on_since_ = NAN;
        }
    } else if (!y && want_y) {
        if (now - y_off_since_ < t.min_off_s) {
            wait = Wait::MinOff;
        } else if (static_cast<int>(starts_.size()) >= t.max_starts_h) {
            wait = Wait::MaxStarts;
        } else {
            // the reversing valve is set before the compressor starts, never while it runs
            if (t.heat_pump) out_.OB = (dem == Call::Cool) == t.ob_cool;
            y = true;
            y_on_since_ = now;
            starts_.push_back(now);
        }
    }

    // --- auxiliary / primary heat (W)
    bool w = false;
    if (dem == Call::Heat) {
        if (!t.heat_pump) {
            w = true;                                   // furnace or electric heat is the heat
        } else if (t.has_aux) {
            const bool outdoor_ok = !have_outdoor || outdoor <= t.aux_lockout_f;
            const bool comp_locked = have_outdoor && outdoor < t.comp_lockout_f;
            if (room <= sp.heat - t.aux_droop_f) {
                if (std::isnan(below_since_)) below_since_ = now;
            } else {
                below_since_ = NAN;
            }
            const bool drooping = !std::isnan(below_since_) && now - below_since_ >= t.aux_delay_s;
            w = emergency || comp_locked || (drooping && outdoor_ok);
        }
    } else {
        below_since_ = NAN;
    }

    // --- blower (G)
    const bool calling = y || w;
    if ((out_.Y && !y) || (out_.W && !w)) purge_until_ = now + t.fan_purge_s;
    const Fan fan = settings_.fan;
    const bool circulate = fan == Fan::Circulate && local.min < t.circulate_min_h;
    const bool g = calling || fan == Fan::On || circulate || now < purge_until_;

    out_.Y = y;
    out_.W = w;
    out_.G = g;
    wait_ = wait;
    return report(room, sp, now);
}

}  // namespace tstat
