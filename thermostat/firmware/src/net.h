// WiFi, MQTT, the clock, and keeping the settings in flash.
//
// Topics (hvac/<site>/thermostat/...):
//   config    <- the cloud's settings (retained, versioned); applied only if newer than ours
//   display   <- the display feed (retained): alerts, health, technician trend
//   telemetry -> our report every 5 s;  status -> online/offline (retained, with a last will)
//   request   -> a change made on our own screen; the cloud answers with a newer config
// plus hvac/<site>/outdoor/telemetry <- the outdoor monitor's air temperature (heat-pump lockouts).
#pragma once
#include <ArduinoJson.h>

#include "feed.h"
#include "tstat_core.h"

struct NetState {
    bool wifi = false, mqtt = false, clock_ok = false;
    double outdoor = NAN;           // F, NAN if the outdoor monitor hasn't reported for 10 min
};

// Called when the cloud sends a newer config (already saved to flash).
using ConfigHandler = void (*)(int ver, const tstat::Settings&, const tstat::Tech&);

void net_begin(ConfigHandler on_config);
void net_loop();
const NetState& net_state();
const Feed& net_feed();
uint32_t net_feed_version();        // bumps each time a new feed arrives

// Settings kept in flash, so heating and cooling carry on after a power cut with no internet.
bool net_load(int& ver, tstat::Settings& s, tstat::Tech& t);
void net_save(int ver, const tstat::Settings& s, const tstat::Tech& t);

bool net_local_time(tstat::LocalTime& out);     // false until NTP has set the clock
void net_set_tz(const std::string& iana);
double net_epoch();                             // seconds; uptime-based before NTP

void net_publish_report(JsonDocument& doc);
// A change made on the screen: queued (merged with any earlier unsent one) and sent once MQTT
// is up and the screen has been left alone for 1.5 s.
void net_request(const JsonDocument& change);
bool net_pending();                             // a change from the screen is still unsent
