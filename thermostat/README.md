# Fullscope display thermostat (roadmap phase 11)

An optional add-on that replaces the customer's existing thermostat. Fullscope already watches
the equipment; owning the thermostat as well lets it compare what was asked for with what
happened, adjust the schedule remotely, and give the contractor one place to see both.

**Status:** design + cloud side + reference control logic (this branch). No hardware or
firmware yet.

## The rule that shapes everything

**All control and safety logic runs on the thermostat.** The cloud only stores settings and
sends them down. With the internet, WiFi or the cloud down, the thermostat keeps heating and
cooling with the last settings it received and its own clock, and it never needs the cloud
to stay safe.

## How the pieces fit

```
 web app ── PUT /thermostat, POST/DELETE /thermostat/hold, PUT /thermostat/tech
    │
 cloud API ── stores ThermostatConfig (settings, tech, version)
    │         publishes the whole config, retained:  hvac/<site>/thermostat/config
    ▼
 thermostat ── runs the control loop every few seconds (Controller)
    │          reports every 5 s:                       hvac/<site>/thermostat/telemetry
    ▼
 ingest ── stores the report; the next indoor/outdoor snapshot carries it ("tstat")
           plus its diagnostics (room too hot/cold, setpoint not reached, call mismatch)
```

- **Config** is versioned: every change bumps `ver`. The thermostat reports the `cfg_ver` it is
  running, so the web app can show "Sending to the thermostat…" until it matches. Because the
  message is retained, a thermostat that was offline gets the latest config as soon as it
  reconnects.
- **Outdoor temperature** for the heat-pump lockouts comes from the outdoor node over the local
  broker (`hvac/<site>/outdoor/telemetry`). If it's missing, the lockouts that depend on it
  are skipped. The safe direction: aux heat is allowed and the compressor isn't locked out.
- **Time** comes from NTP, as on the nodes (firmware 0.2). The schedule is in the house's time
  zone (`tech.tz`). The firmware converts the IANA name to a POSIX TZ string.

## The screen

Three tabs on the 800 × 480 touchscreen. `hvac-cloud/web/thermostat.html` is a browser preview of
the screen, driven by a system's live data; open it from the thermostat card ("See the
thermostat's screen") or from the Equipment page.

- **Home:** room temperature, setpoint −/+, mode, what it's doing ("Cooling to 76°", "Protecting
  the compressor"), schedule or hold with Resume. Top bar: clock, outdoor temperature, humidity
  and the monitoring status ("System OK", "Check soon"), which opens Alerts.
- **Alerts (homeowner):** the monitoring system's open alerts in plain words, the same as the
  emails. Tap one for why it matters and what to do. Beside them are the System health areas,
  as on the homeowner web page, plus Comfort.
- **Service (technician, behind the service PIN):**
  - top: key numbers (mode and run time, superheat, subcooling, delta-T, suction, liquid, line
    volts, compressor amps), outlined when one has an alert
  - middle: a trend of suction and liquid pressure, and return, supply and outdoor air, over
    1 or 6 hours, with the compressor's run times along the bottom and shaded, numbered markers
    where the electrical module found a problem (weak capacitor, pitted contactor, high amps,
    voltage…)
  - bottom: the open diagnostics in technical words

  It locks again after 5 minutes without a touch. The PIN is set on the Equipment page
  (default 0000).

### The display feed

The cloud does the diagnosing, so the thermostat only draws. Ingest publishes a feed, retained,
on `hvac/<site>/thermostat/display`: at most once a minute, and at once when an alert is raised
or cleared. It is built by `hvaccloud/display.py` and is also available at
`GET /api/systems/{id}/thermostat/display`. Contents:

- status and headline
- open alerts: plain title, why, what to do, technical text, since when
- health areas
- key numbers
- the 6-hour trend at 2-minute steps
- electrical markers: code, label, start, end

It is about 6 KB. The firmware needs an MQTT buffer of at least 16 KB.

Alerts on the screen are the raised ones (5-minute hold), so it doesn't flicker. Problems that
can only be checked while the unit runs (refrigerant, delta-T, the electrical checks) stay
shown between cycles. They clear only when a later run shows them gone.

## Settings

Homeowner (`settings`): `mode` (off, heat, cool, auto, emergency_heat), `fan` (auto, on,
circulate), `heat_sp`, `cool_sp`, `schedule` (weekly periods: `{"days": [0-6], "at": "HH:MM",
"heat": 68, "cool": 78}`, Monday = 0) and `hold` (`{"heat", "cool", "until"}`; `until` null =
until resumed).

Technician only (`tech`; Equipment page): `has_aux`, `tz`, `service_pin`, `differential`, `min_on_s`,
`min_off_s` (at least 120 s), `max_starts_h`, `comp_lockout_f`, `aux_lockout_f`, `aux_droop_f`,
`aux_delay_s`, `fan_purge_s`, `circulate_min_h`. Allowed ranges are in `TECH_LIMITS`.
`heat_pump` and `ob_energized` come from the system's Equipment settings, so they are set in
one place.

## Control rules (reference: `hvac-cloud/hvaccloud/thermostat.py`, `Controller`)

The firmware implements the same rules. `tests/test_thermostat.py` checks them, including every
step of simulated random days:

| Rule | Detail |
|---|---|
| Hysteresis | A call starts at setpoint ± half the differential and ends at ∓ half |
| Auto mode | Cooling setpoint at least 3 °F above heating |
| Compressor minimum off | No restart within `min_off_s` (floor 120 s) |
| Compressor minimum run | Once started, runs `min_on_s` unless the mode is switched off |
| Starts per hour | At most `max_starts_h` |
| Reversing valve | Set only while the compressor is off, just before it starts |
| Compressor lockout | Heat pump below `comp_lockout_f` outdoors: aux heat only |
| Aux heat | Joins after the room has been `aux_droop_f` below setpoint for `aux_delay_s`, only at or below `aux_lockout_f` outdoors; emergency heat = aux only |
| Blower | On with any call, for `fan_purge_s` after it, always (fan on), or `circulate_min_h` minutes an hour |
| Sensor fail-safe | Room reading missing or outside 32–120 °F: all outputs off |

## Hardware (proposed)

- **MCU + display:** ESP32-S3 with a 3.5–4.3" capacitive touchscreen (an off-the-shelf
  ESP32-S3 display module to start), WiFi built in. Its FCC-certified radio module keeps the
  Part 15 work to a modular-approval check. Later it could double as the LoRa gateway for the
  monitors (see the radio design).
- **Power:** from R and C (24 VAC), rectified, then a buck converter to 3.3 V. Version 1
  requires a C wire. "Power stealing" without C is a later option, with known problems on some
  equipment.
- **Outputs:** Y, W, G, O/B, each a solid-state switch rated for 24 VAC loads (PhotoMOS relay,
  or opto-triac plus triac). They are open on reset and power loss, with a hardware watchdog
  that drops all outputs if the firmware stops servicing it. A second compressor stage (Y2) and
  aux stage (W2) are left for later.
- **Room sensor:** SHT4x on a tab thermally isolated from the board, with self-heating
  compensation for the display and regulator (calibrated against a reference in the
  enclosure).
- **Terminals:** R, C, Y, W, G, O/B, labelled like a standard thermostat sub-base.

## Diagnostics the cloud adds (on top of the monitors' and the electrical module's)

| Code | When | Level |
|---|---|---|
| `room_hot` | Room ≥ 90 °F | Service needed |
| `room_cold` | Room ≤ 50 °F | Service needed |
| `setpoint_not_reached` | Calling ≥ 90 min and still ≥ 2 °F off target | Check soon |
| `call_mismatch` | Thermostat's Y differs from the Y the indoor node sees (alerts after the usual 5-min hold) | Check soon |
| `node_offline` (node thermostat) | No thermostat report | Good to know |

`call_mismatch` is the one only Fullscope can do: it catches a broken wire or a stuck relay
between the thermostat and the equipment.

## Certification and liability (to confirm with a compliance lab)

- **UL 60730-1 / UL 60730-2-9** (automatic electrical controls, temperature-sensing controls)
  is the usual listing for a line-voltage-free 24 V thermostat sold in the US. It covers the
  software class for the safety functions above, which is why they are kept small and
  separately tested.
- **FCC Part 15:** a pre-certified WiFi module plus unintentional-radiator testing of the
  finished product.
- **Optional later:** ENERGY STAR connected-thermostat certification (needs a data-sharing
  API) and California Title 24 JA5 (occupant-controlled smart thermostat; demand-response
  signals).
- Installation by the contractor, using the existing thermostat wires. No line-voltage work.

## Trying it without hardware

```
python demo_publisher.py --site demo --thermostat
```

This simulates a house whose room temperature drifts toward the outdoor temperature. The
reference controller runs it, following the config the cloud publishes. Changing the setpoint,
mode or schedule in the web app changes what the simulated equipment does, and the
indoor/outdoor readings follow.

## Open questions

- Version 1 display hardware: a module (faster) or a custom board with the carrier design
  (cheaper at volume)?
- Should the homeowner be able to edit the schedule on the thermostat itself? (Yes eventually.
  The thermostat would publish its edits back and the cloud would accept them by version.)
- Demand response and utility programs: which ones the target market's contractors care about.
