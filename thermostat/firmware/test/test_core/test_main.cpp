// Native tests: pio test -e native
// The C++ controller must make the same decision as the Python reference at every step.
#include <unity.h>

#include <cmath>
#include <cstdio>
#include <cstring>

#include "tstat_core.h"
#include "tstat_json.h"
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
    return UNITY_END();
}
