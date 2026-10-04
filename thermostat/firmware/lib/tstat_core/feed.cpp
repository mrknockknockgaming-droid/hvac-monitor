#include "feed.h"

#include <ArduinoJson.h>

#include <cmath>
#include <cstring>

namespace {
Level level(const char* s) {
    if (!s) return L_OFFLINE;
    if (!strcmp(s, "ok")) return L_OK;
    if (!strcmp(s, "advisory")) return L_ADVISORY;
    if (!strcmp(s, "caution")) return L_CAUTION;
    if (!strcmp(s, "fault")) return L_FAULT;
    return L_OFFLINE;
}

double num(JsonVariantConst v) { return v.is<double>() || v.is<long>() ? v.as<double>() : NAN; }

void series(JsonArrayConst a, std::vector<float>& out) {
    out.clear();
    out.reserve(a.size());
    for (JsonVariantConst v : a) out.push_back(v.isNull() ? NAN : v.as<float>());
}
}  // namespace

bool feed_parse(const char* json, size_t len, Feed& out) {
    JsonDocument doc;
    if (deserializeJson(doc, json, len) || doc["ver"].as<int>() != 1) return false;
    Feed f;
    f.valid = true;
    f.fresh = doc["fresh"].as<bool>();
    f.status = level(doc["status"]);
    f.word = doc["word"] | "";
    f.headline = doc["headline"] | "";
    f.updated = num(doc["updated"]);
    for (JsonObjectConst a : doc["alerts"].as<JsonArrayConst>())
        f.alerts.push_back({a["code"] | "", a["title"] | "", a["why"] | "", a["todo"] | "", a["tech"] | "",
                            a["tech_word"] | "", level(a["level"])});
    for (JsonObjectConst h : doc["health"].as<JsonArrayConst>())
        f.health.push_back({h["name"] | "", h["word"] | "", h["text"] | "", level(h["level"])});
    JsonObjectConst tech = doc["tech"];
    JsonObjectConst n = tech["now"];
    f.mode = n["mode"] | "";
    f.run_min = num(n["run_min"]);
    f.sh = num(n["sh"]);
    f.sc = num(n["sc"]);
    f.dt = num(n["dt"]);
    f.p_low = num(n["p_low"]);
    f.p_high = num(n["p_high"]);
    f.oat = num(n["oat"]);
    f.line_v = num(n["line_v"]);
    f.comp_a = num(n["comp_a"]);
    f.has_elec = tech["has_elec"].as<bool>();
    JsonObjectConst tr = tech["trend"];
    f.t0 = num(tr["t0"]);
    f.step = num(tr["step"]);
    JsonObjectConst s = tr["series"];
    series(s["p_low"], f.p_low_s);
    series(s["p_high"], f.p_high_s);
    series(s["t_ret"], f.t_ret_s);
    series(s["t_sup"], f.t_sup_s);
    series(s["oat"], f.oat_s);
    for (JsonVariantConst v : tr["on"].as<JsonArrayConst>()) f.on.push_back(v.as<int>() ? 1 : 0);
    for (JsonObjectConst m : tech["markers"].as<JsonArrayConst>())
        f.markers.push_back({m["label"] | "", level(m["level"]), num(m["start"]), num(m["end"])});
    out = std::move(f);
    return true;
}
