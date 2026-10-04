// The cloud's config message (hvac/<site>/thermostat/config, retained) and the telemetry the
// thermostat reports (hvac/<site>/thermostat/telemetry). Formats: hvaccloud/thermostat.py.
#pragma once
#include <ArduinoJson.h>

#include "tstat_core.h"

namespace tstat {

// {"ver": 4, "settings": {...}, "tech": {...}}. Missing fields keep their defaults (as merged()
// does in Python); tech values are clamped to the safe limits. Returns false (and leaves the
// outputs untouched) if the message isn't a config at all.
bool parse_config(const char* json, size_t len, int& ver, Settings& settings, Tech& tech);

// The telemetry document for one control step.
void build_report(JsonDocument& doc, const Report& r, int cfg_ver, double rh, uint32_t uptime_s,
                  double ts, const char* fw);

}  // namespace tstat
