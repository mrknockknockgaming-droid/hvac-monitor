// The cloud's display feed (hvac/<site>/thermostat/display, built by hvaccloud/display.py):
// what the Alerts and Service screens show.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

enum Level : uint8_t { L_OK, L_ADVISORY, L_CAUTION, L_FAULT, L_OFFLINE };

struct FeedAlert { std::string code, title, why, todo, tech, tech_word; Level level; };
struct FeedHealth { std::string name, word, text; Level level; };
struct FeedMarker { std::string label; Level level; double start, end; };   // end NAN = still open

struct Feed {
    bool valid = false;
    bool fresh = false;
    Level status = L_OFFLINE;
    std::string word, headline;
    double updated = 0;
    std::vector<FeedAlert> alerts;
    std::vector<FeedHealth> health;
    // technician numbers (NAN when unknown)
    std::string mode;
    double run_min, sh, sc, dt, p_low, p_high, oat, line_v, comp_a;
    bool has_elec = false;
    // trend: t0 + i * step
    double t0 = 0, step = 120;
    std::vector<float> p_low_s, p_high_s, t_ret_s, t_sup_s, oat_s;   // NAN = no reading
    std::vector<uint8_t> on;
    std::vector<FeedMarker> markers;
};

bool feed_parse(const char* json, size_t len, Feed& out);
