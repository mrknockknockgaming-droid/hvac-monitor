// Native tests: pio test -e native
// The C++ controller must make the same decision as the Python reference at every step.
#include <unity.h>

#include <cmath>
#include <cstdio>
#include <cstring>

#include "tstat_core.h"
#include "tstat_json.h"
#include "feed.h"
#include "feed_sample.h"
#include "vectors.h"

using namespace tstat;

void setUp() {}
void tearDown() {}

static bool same(double a, double b, double tol) {
    if (std::isnan(a) || std::isnan(b)) return std::isnan(a) && std::isnan(b);
    return std::fabs(a - b) <= tol;
}

static void run_scenario(const VScenario& sc) {
    Controller c;
    int ver = 0;
    for (int i = sc.first; i < sc.first + sc.count; i++) {
        const VStep& v = V_STEPS[i];
        if (v.cfg >= 0) {
            Settings s;
            Tech t;
            const char* cfg = V_CONFIGS[v.cfg];
            TEST_ASSERT_TRUE_MESSAGE(parse_config(cfg, strlen(cfg), ver, s, t), "config didn't parse");
            c.configure(s);
            c.configure(t);
        }
        LocalTime lt;
        lt.year = v.year; lt.mon = v.mon; lt.day = v.day; lt.hour = v.hour; lt.min = v.min; lt.sec = v.sec; lt.wday = v.wday;
        const Report r = c.step(v.room, v.outdoor, v.now, lt);
        const bool ok = r.out.Y == v.Y && r.out.W == v.W && r.out.G == v.G && r.out.OB == v.OB &&
                        static_cast<int>(r.call) == v.call && static_cast<int>(r.wait) == v.wait &&
                        same(r.heat_sp, v.heat, 1e-9) && same(r.cool_sp, v.cool, 1e-9) &&
                        static_cast<int>(r.source) == v.source && same(r.call_min, v.call_min, 0.051);
        if (!ok) {
            char msg[400];
            snprintf(msg, sizeof msg,
                     "%s step %d (%02d:%02d): got Y%d W%d G%d OB%d call %d wait %d sp %.1f/%.1f src %d; "
                     "Python Y%d W%d G%d OB%d call %d wait %d sp %.1f/%.1f src %d",
                     sc.name, i - sc.first, v.hour, v.min, r.out.Y, r.out.W, r.out.G, r.out.OB, static_cast<int>(r.call),
                     static_cast<int>(r.wait), r.heat_sp, r.cool_sp, static_cast<int>(r.source), v.Y, v.W, v.G, v.OB, v.call,
                     v.wait, v.heat, v.cool, v.source);
            TEST_FAIL_MESSAGE(msg);
        }
    }
}

static void test_scenario_0() { run_scenario(V_SCENARIOS[0]); }
static void test_scenario_1() { run_scenario(V_SCENARIOS[1]); }
static void test_scenario_2() { run_scenario(V_SCENARIOS[2]); }
static void test_scenario_3() { run_scenario(V_SCENARIOS[3]); }

static void test_bad_tech_values_are_clamped_to_safe_limits() {
    const char* cfg = R"({"ver":7,"settings":{"mode":"cool"},"tech":{"min_off_s":10,"max_starts_h":40,"differential":0,"aux_lockout_f":"x"}})";
    Settings s;
    Tech t;
    int ver = 0;
    TEST_ASSERT_TRUE(parse_config(cfg, strlen(cfg), ver, s, t));
    TEST_ASSERT_EQUAL(7, ver);
    TEST_ASSERT_EQUAL_DOUBLE(120, t.min_off_s);        // never below 2 minutes
    TEST_ASSERT_EQUAL(8, t.max_starts_h);
    TEST_ASSERT_EQUAL_DOUBLE(0.5, t.differential);
    TEST_ASSERT_EQUAL_DOUBLE(35, t.aux_lockout_f);     // not a number: default kept
}

static void test_garbage_config_changes_nothing() {
    const char* bad[] = {"not json", R"({"settings":{}})", R"({"ver":2,"settings":{"mode":"party"}})"};
    for (const char* cfg : bad) {
        Settings s;
        s.cool_sp = 71;
        Tech t;
        int ver = 5;
        TEST_ASSERT_FALSE(parse_config(cfg, strlen(cfg), ver, s, t));
        TEST_ASSERT_EQUAL(5, ver);
        TEST_ASSERT_EQUAL_DOUBLE(71, s.cool_sp);
    }
}

static void test_hold_until_expires() {
    Settings s;
    s.hold.active = true;
    s.hold.cool = 72;
    s.hold.has_until = parse_iso("2026-10-05T17:00:00", s.hold.until);
    LocalTime before{2026, 10, 5, 16, 59, 59, 0}, at{2026, 10, 5, 17, 0, 0, 0};
    TEST_ASSERT_EQUAL(static_cast<int>(Source::Hold), static_cast<int>(schedule_now(s, before).source));
    TEST_ASSERT_EQUAL(static_cast<int>(Source::Manual), static_cast<int>(schedule_now(s, at).source));
}

static void test_report_json() {
    Controller c;
    LocalTime lt{2026, 10, 5, 12, 0, 0, 0};
    Report r = c.step(80.0, 95.0, 1.8e9, lt);
    JsonDocument doc;
    build_report(doc, r, 3, 41.0, 120, 1.8e9, "0.1.0");
    TEST_ASSERT_EQUAL_STRING("thermostat", doc["node"]);
    TEST_ASSERT_EQUAL(3, doc["cfg_ver"].as<int>());
    TEST_ASSERT_TRUE(doc["out"]["Y"].as<bool>());
    TEST_ASSERT_EQUAL_STRING("cool", doc["call"]);
    TEST_ASSERT_TRUE(doc["wait"].isNull());
    r = c.step(NONE, 95.0, 1.8e9 + 10, lt);
    build_report(doc, r, 3, NONE, 130, 0, "0.1.0");
    TEST_ASSERT_TRUE(doc["room"]["t"].isNull());
    TEST_ASSERT_EQUAL_STRING("sensor", doc["wait"]);
    TEST_ASSERT_FALSE(doc["out"]["Y"].as<bool>());
}

static void test_config_round_trips_through_flash_format() {
    for (int i = 0; i < static_cast<int>(sizeof V_CONFIGS / sizeof V_CONFIGS[0]); i++) {
        Settings s, s2;
        Tech t, t2;
        int ver = 0, ver2 = 0;
        TEST_ASSERT_TRUE(parse_config(V_CONFIGS[i], strlen(V_CONFIGS[i]), ver, s, t));
        JsonDocument doc;
        config_to_json(doc, ver, s, t);
        std::string json;
        serializeJson(doc, json);
        TEST_ASSERT_TRUE(parse_config(json.c_str(), json.size(), ver2, s2, t2));
        TEST_ASSERT_EQUAL(ver, ver2);
        TEST_ASSERT_EQUAL(static_cast<int>(s.mode), static_cast<int>(s2.mode));
        TEST_ASSERT_EQUAL(s.schedule.size(), s2.schedule.size());
        TEST_ASSERT_EQUAL(s.hold.active, s2.hold.active);
        TEST_ASSERT_EQUAL(0, compare(s.hold.until, s2.hold.until));
        TEST_ASSERT_EQUAL_DOUBLE(t.min_off_s, t2.min_off_s);
        TEST_ASSERT_EQUAL(t.ob_cool, t2.ob_cool);
        LocalTime lt{2026, 10, 6, 9, 30, 0, 1};
        Setpoints a = schedule_now(s, lt), b = schedule_now(s2, lt);
        TEST_ASSERT_EQUAL_DOUBLE(a.cool, b.cool);
        TEST_ASSERT_EQUAL(static_cast<int>(a.source), static_cast<int>(b.source));
    }
}

static void test_holds_made_on_the_screen_match_python() {
    Settings s;
    Tech t;
    int ver;
    TEST_ASSERT_TRUE(parse_config(H_SETTINGS, strlen(H_SETTINGS), ver, s, t));
    for (const HVec& v : H_VECS) {
        LocalTime w{v.year, v.mon, v.day, v.hour, v.min, v.sec, v.wday};
        Hold h = hold_until_next(s, w, NAN, 74);
        char msg[96];
        snprintf(msg, sizeof msg, "at %04d-%02d-%02d %02d:%02d", v.year, v.mon, v.day, v.hour, v.min);
        TEST_ASSERT_EQUAL_MESSAGE(v.has_until, h.has_until, msg);
        TEST_ASSERT_EQUAL_DOUBLE_MESSAGE(v.cool, h.cool, msg);
        TEST_ASSERT_EQUAL_DOUBLE_MESSAGE(v.heat, h.heat, msg);
        if (v.has_until) {
            LocalTime u{v.uy, v.um, v.ud, v.uh, v.umin, 0, 0};
            TEST_ASSERT_EQUAL_MESSAGE(0, compare(u, h.until), msg);
        }
    }
    LocalTime ny = add_minutes(LocalTime{2026, 12, 31, 23, 50, 0, 3}, 20);
    TEST_ASSERT_EQUAL(2027, ny.year);
    TEST_ASSERT_EQUAL(1, ny.mon);
    TEST_ASSERT_EQUAL(4, ny.wday);                 // Fri 1 Jan 2027
}

static void test_display_feed_from_the_cloud_parses() {
    Feed f;
    TEST_ASSERT_TRUE(feed_parse(FEED_SAMPLE, strlen(FEED_SAMPLE), f));
    TEST_ASSERT_TRUE(f.fresh);
    TEST_ASSERT_EQUAL(L_CAUTION, f.status);
    TEST_ASSERT_EQUAL(1, f.alerts.size());
    TEST_ASSERT_EQUAL_STRING("cap_herm", f.alerts[0].code.c_str());
    TEST_ASSERT_TRUE(f.alerts[0].title.size() > 10 && f.alerts[0].todo.size() > 10);
    TEST_ASSERT_EQUAL(5, f.health.size());
    TEST_ASSERT_EQUAL(FEED_SAMPLE_POINTS, f.on.size());
    TEST_ASSERT_EQUAL(f.on.size(), f.p_low_s.size());
    TEST_ASSERT_TRUE(std::isnan(f.p_low_s[0]) && !std::isnan(f.p_high_s[f.p_high_s.size() - 2]));
    TEST_ASSERT_EQUAL(1, f.markers.size());
    TEST_ASSERT_EQUAL_STRING("Compressor capacitor weak", f.markers[0].label.c_str());
    TEST_ASSERT_TRUE(std::isnan(f.markers[0].end));
    TEST_ASSERT_TRUE(f.has_elec);
    TEST_ASSERT_EQUAL_DOUBLE(240.0, f.line_v);
    Feed g;
    TEST_ASSERT_FALSE(feed_parse("{\"ver\":2}", 9, g));
    TEST_ASSERT_FALSE(g.valid);
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_scenario_0);
    RUN_TEST(test_scenario_1);
    RUN_TEST(test_scenario_2);
    RUN_TEST(test_scenario_3);
    RUN_TEST(test_bad_tech_values_are_clamped_to_safe_limits);
    RUN_TEST(test_garbage_config_changes_nothing);
    RUN_TEST(test_hold_until_expires);
    RUN_TEST(test_report_json);
    RUN_TEST(test_config_round_trips_through_flash_format);
    RUN_TEST(test_holds_made_on_the_screen_match_python);
    RUN_TEST(test_display_feed_from_the_cloud_parses);
    return UNITY_END();
}
