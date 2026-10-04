#include "tstat_json.h"

#include <cmath>
#include <cstdio>
#include <cstring>

namespace tstat {

static void read_num(JsonVariantConst v, double& out) {
    if (v.is<double>() || v.is<long>()) out = v.as<double>();
}

static bool read_settings(JsonObjectConst o, Settings& s) {
    Settings r;                                       // defaults, like merged()
    if (o["mode"].is<const char*>() && !parse(o["mode"].as<const char*>(), r.mode)) return false;
    if (o["fan"].is<const char*>() && !parse(o["fan"].as<const char*>(), r.fan)) return false;
    read_num(o["heat_sp"], r.heat_sp);
    read_num(o["cool_sp"], r.cool_sp);
    for (JsonObjectConst p : o["schedule"].as<JsonArrayConst>()) {
        Period per;
        int h = 0, m = 0;
        const char* at = p["at"] | "";
        if (sscanf(at, "%d:%d", &h, &m) != 2 || h < 0 || h > 23 || m < 0 || m > 59) continue;
        per.minute = h * 60 + m;
        for (JsonVariantConst d : p["days"].as<JsonArrayConst>()) {
            int day = d.as<int>();
            if (day >= 0 && day <= 6) per.days.push_back(static_cast<uint8_t>(day));
        }
        read_num(p["heat"], per.heat);
        read_num(p["cool"], per.cool);
        if (!per.days.empty()) r.schedule.push_back(per);
    }
    JsonObjectConst hold = o["hold"].as<JsonObjectConst>();
    if (!hold.isNull() && hold.size()) {
        r.hold.active = true;
        r.hold.heat = r.heat_sp;
        r.hold.cool = r.cool_sp;
        read_num(hold["heat"], r.hold.heat);
        read_num(hold["cool"], r.hold.cool);
        r.hold.has_until = hold["until"].is<const char*>() && parse_iso(hold["until"].as<const char*>(), r.hold.until);
    }
    s = r;
    return true;
}

static void read_tech(JsonObjectConst o, Tech& t) {
    Tech r;
    if (o["heat_pump"].is<bool>()) r.heat_pump = o["heat_pump"].as<bool>();
    if (o["ob_energized"].is<const char*>()) r.ob_cool = strcmp(o["ob_energized"].as<const char*>(), "heat") != 0;
    if (o["has_aux"].is<bool>()) r.has_aux = o["has_aux"].as<bool>();
    read_num(o["differential"], r.differential);
    read_num(o["min_on_s"], r.min_on_s);
    read_num(o["min_off_s"], r.min_off_s);
    if (o["max_starts_h"].is<int>()) r.max_starts_h = o["max_starts_h"].as<int>();
    read_num(o["aux_lockout_f"], r.aux_lockout_f);
    read_num(o["comp_lockout_f"], r.comp_lockout_f);
    read_num(o["aux_droop_f"], r.aux_droop_f);
    read_num(o["aux_delay_s"], r.aux_delay_s);
    read_num(o["fan_purge_s"], r.fan_purge_s);
    if (o["circulate_min_h"].is<int>()) r.circulate_min_h = o["circulate_min_h"].as<int>();
    if (o["tz"].is<const char*>()) r.tz = o["tz"].as<const char*>();
    if (o["service_pin"].is<const char*>()) r.service_pin = o["service_pin"].as<const char*>();
    clamp(r);
    t = r;
}

bool parse_config(const char* json, size_t len, int& ver, Settings& settings, Tech& tech) {
    JsonDocument doc;
    if (deserializeJson(doc, json, len) || !doc["ver"].is<int>()) return false;
    Settings s;
    if (!read_settings(doc["settings"].as<JsonObjectConst>(), s)) return false;
    Tech t;
    read_tech(doc["tech"].as<JsonObjectConst>(), t);
    ver = doc["ver"].as<int>();
    settings = s;
    tech = t;
    return true;
}

void format_iso(const LocalTime& t, char* out, size_t n) {
    snprintf(out, n, "%04d-%02d-%02dT%02d:%02d:%02d", t.year, t.mon, t.day, t.hour, t.min, t.sec);
}

void config_to_json(JsonDocument& doc, int ver, const Settings& s, const Tech& t) {
    doc.clear();
    doc["ver"] = ver;
    JsonObject o = doc["settings"].to<JsonObject>();
    o["mode"] = name(s.mode);
    o["fan"] = name(s.fan);
    o["heat_sp"] = s.heat_sp;
    o["cool_sp"] = s.cool_sp;
    JsonArray sched = o["schedule"].to<JsonArray>();
    for (const Period& p : s.schedule) {
        JsonObject e = sched.add<JsonObject>();
        JsonArray days = e["days"].to<JsonArray>();
        for (uint8_t d : p.days) days.add(d);
        char at[6];
        snprintf(at, sizeof at, "%02d:%02d", p.minute / 60, p.minute % 60);
        e["at"] = at;
        e["heat"] = p.heat;
        e["cool"] = p.cool;
    }
    if (s.hold.active) {
        JsonObject h = o["hold"].to<JsonObject>();
        h["heat"] = s.hold.heat;
        h["cool"] = s.hold.cool;
        if (s.hold.has_until) {
            char iso[24];
            format_iso(s.hold.until, iso, sizeof iso);
            h["until"] = iso;
        } else {
            h["until"] = nullptr;
        }
    } else {
        o["hold"] = nullptr;
    }
    JsonObject x = doc["tech"].to<JsonObject>();
    x["heat_pump"] = t.heat_pump;
    x["ob_energized"] = t.ob_cool ? "cool" : "heat";
    x["has_aux"] = t.has_aux;
    x["differential"] = t.differential;
    x["min_on_s"] = t.min_on_s;
    x["min_off_s"] = t.min_off_s;
    x["max_starts_h"] = t.max_starts_h;
    x["aux_lockout_f"] = t.aux_lockout_f;
    x["comp_lockout_f"] = t.comp_lockout_f;
    x["aux_droop_f"] = t.aux_droop_f;
    x["aux_delay_s"] = t.aux_delay_s;
    x["fan_purge_s"] = t.fan_purge_s;
    x["circulate_min_h"] = t.circulate_min_h;
    x["tz"] = t.tz;
    x["service_pin"] = t.service_pin;
}

static double r1(double v) { return std::round(v * 10) / 10; }

void build_report(JsonDocument& doc, const Report& r, int cfg_ver, double rh, uint32_t uptime_s, double ts, const char* fw) {
    doc.clear();
    doc["node"] = "thermostat";
    doc["fw"] = fw;
    doc["uptime"] = uptime_s;
    doc["cfg_ver"] = cfg_ver;
    if (ts > 0) doc["ts"] = static_cast<long long>(ts);
    JsonObject room = doc["room"].to<JsonObject>();
    if (std::isnan(r.room)) room["t"] = nullptr; else room["t"] = r1(r.room);
    if (std::isnan(rh)) room["rh"] = nullptr; else room["rh"] = r1(rh);
    doc["mode"] = name(r.mode);
    doc["fan"] = name(r.fan);
    JsonObject sp = doc["sp"].to<JsonObject>();
    sp["heat"] = r.heat_sp;
    sp["cool"] = r.cool_sp;
    doc["source"] = name(r.source);
    JsonObject out = doc["out"].to<JsonObject>();
    out["Y"] = r.out.Y;
    out["W"] = r.out.W;
    out["G"] = r.out.G;
    out["OB"] = r.out.OB;
    if (r.call == Call::None) doc["call"] = nullptr; else doc["call"] = name(r.call);
    if (std::isnan(r.call_min)) doc["call_min"] = nullptr; else doc["call_min"] = r.call_min;
    if (r.wait == Wait::None) doc["wait"] = nullptr; else doc["wait"] = name(r.wait);
    JsonArray err = doc["err"].to<JsonArray>();
    if (r.wait == Wait::Sensor) err.add("room_sensor");
}

}  // namespace tstat
