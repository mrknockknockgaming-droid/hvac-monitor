// Display thermostat: settings, schedule and the control loop.
//
// A line-by-line port of hvac-cloud/hvaccloud/thermostat.py (the reference). It has no Arduino
// dependencies, so it is unit-tested on the PC (pio test -e native) against vectors generated
// from the Python Controller: both must make the same decision at every step.
#pragma once
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>

namespace tstat {

enum class Mode : uint8_t { Off, Heat, Cool, Auto, EmergencyHeat };
enum class Fan : uint8_t { Auto, On, Circulate };
enum class Call : uint8_t { None, Heat, Cool };
enum class Wait : uint8_t { None, MinOn, MinOff, MaxStarts, Sensor };
enum class Source : uint8_t { Manual, Schedule, Hold };

const char* name(Mode m);
const char* name(Fan f);
const char* name(Call c);     // "heat" | "cool" | nullptr
const char* name(Wait w);     // "min_on" | ... | nullptr
const char* name(Source s);
bool parse(const char* s, Mode& out);
bool parse(const char* s, Fan& out);

// The house's local time (the schedule is written in it). wday: Monday = 0, as in Python.
struct LocalTime {
    int year = 2000, mon = 1, day = 1, hour = 0, min = 0, sec = 0, wday = 0;
};
int compare(const LocalTime& a, const LocalTime& b);          // <0, 0, >0 (date and time, not wday)
bool parse_iso(const char* s, LocalTime& out);                // "2026-10-05T17:00:00" (seconds optional)

struct Period {                 // one schedule line; days kept in the order given (matters for ties)
    std::vector<uint8_t> days;  // 0..6, Monday = 0
    int minute = 0;             // minutes after midnight
    double heat = 68, cool = 78;
};

struct Hold {
    bool active = false;
    double heat = 68, cool = 76;
    bool has_until = false;     // false = until resumed
    LocalTime until;
};

struct Settings {               // the homeowner's (DEFAULT_SETTINGS)
    Mode mode = Mode::Cool;
    Fan fan = Fan::Auto;
    double heat_sp = 68, cool_sp = 76;
    std::vector<Period> schedule;
    Hold hold;
};

struct Tech {                   // technician-only (DEFAULT_TECH)
    bool heat_pump = true;
    bool ob_cool = true;        // ob_energized == "cool" (O); false = B
    bool has_aux = true;
    double differential = 1.0;
    double min_on_s = 300, min_off_s = 300;
    int max_starts_h = 4;
    double aux_lockout_f = 35, comp_lockout_f = 5;
    double aux_droop_f = 2, aux_delay_s = 600;
    double fan_purge_s = 90;
    int circulate_min_h = 15;
    std::string tz = "America/Phoenix";
    std::string service_pin = "0000";
};
void clamp(Tech& t);            // to TECH_LIMITS: a bad config can never make the safety rules unsafe

struct Setpoints {
    double heat, cool;
    Source source;
    bool has_next = false;      // schedule: minutes until the next change; hold: until (if any)
    int next_in_min = 0;
    LocalTime hold_until;
};
// schedule_now(): a hold wins (until it expires), then the schedule line that started most
// recently (wrapping round the week), else the base setpoints.
Setpoints schedule_now(const Settings& s, const LocalTime& when);

struct Outputs {
    bool Y = false, W = false, G = false, OB = false;
};

struct Report {
    Mode mode;
    Fan fan;
    double heat_sp, cool_sp;
    Source source;
    Outputs out;
    Call call;
    double call_min;            // NaN when not calling
    Wait wait;
    double room;                // NaN when unknown
};

constexpr double ROOM_MIN = 32.0, ROOM_MAX = 120.0;   // outside this the sensor is broken: all off
constexpr double NONE = NAN;                          // "no reading" for room / outdoor

class Controller {
public:
    Controller() = default;
    Controller(const Settings& s, const Tech& t) : settings_(s), tech_(t) {}
    void configure(const Settings& s) { settings_ = s; }
    void configure(const Tech& t) { tech_ = t; }
    const Settings& settings() const { return settings_; }
    const Tech& tech() const { return tech_; }
    const Outputs& outputs() const { return out_; }

    // Call every few seconds. room / outdoor in F (NONE if unknown), now in epoch seconds.
    Report step(double room, double outdoor, double now, const LocalTime& local);

private:
    Call demand(double room, double heat_sp, double cool_sp) const;
    void all_off(double now);
    Report report(double room, const Setpoints& sp, double now) const;

    Settings settings_;
    Tech tech_;
    Outputs out_;
    Call call_ = Call::None;
    double call_since_ = NAN;
    double y_on_since_ = NAN;
    double y_off_since_ = -1e12;            // long ago: free to start
    std::vector<double> starts_;            // compressor starts in the last hour
    double purge_until_ = 0;
    double below_since_ = NAN;              // heating: when the room first sat aux_droop below setpoint
    Wait wait_ = Wait::None;
};

}  // namespace tstat
