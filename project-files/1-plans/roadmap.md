# HVAC Monitor Roadmap

Oct 17, 2026 · @Tyler

## Overview

Phases 1 to 3 are done and Phase 4 is under way. The rev A carrier boards are built and installed on your own AC, both nodes run firmware 0.2.0, the sensors are calibrated, and live data shows in the Fullscope web app. Enclosures and cabinet mounting plates are designed. The broker and cloud now run in a Proxmox container (192.168.1.50), on the new C# build of the server. Open follow-ups: reserve 192.168.1.50 in the router's DHCP, change the weak OTA password, and land the Y and O/B call wires if they aren't yet.

Phases run in order. Customer discovery and IP protection run alongside the build from now on. Tick items as you finish them.

## Phase 0: Design groundwork (done)

- [x] Sensor selection and parts list
- [x] Rev A wiring schematics, outdoor and indoor nodes
- [x] Firmware 0.1.0 builds cleanly; the cloud uses its existing MQTT topics, so no separate cloud mode is needed
- [x] PC dashboard app with refrigerant tables
- [x] Mosquitto broker running, ESP32 connected over MQTT
- [x] Cloud stack rebuilt (database, ingest, API) with the Fullscope web app: homeowner and technician views
- [x] Rev A carrier boards designed in KiCad and ordered from JLCPCB (release hardware-rev-a)

## Phase 1: Bench bring-up

- [x] Build the three 24 VAC to 5 V power supplies
- [x] Set each LM2596HV to 5.00 V before connecting loads
- [x] Flash both boards and confirm WiFi (2.4 GHz) and MQTT with the PC app
- [x] Rev A carrier boards arrived from JLCPCB and assembled (Oct 10)
- [x] Bench-test one outdoor and one indoor carrier board before building the spares

## Phase 2: Outdoor node on the bench

- [x] Wire the ADS1115s, dividers, XDB307 transducers, thermistors and SHT30
- [x] Firmware takes the divider resistor values from config.h, so the outdoor board can be built with 10k in place of the 20k and 15k (set for your build); it refuses values that would overdrive the ADC
- [x] Build notes: 5% resistors are fine for the opto inputs and pull-ups; use 1% for the dividers and 1% (or 0.1%) 10k for the thermistor references, then set v33, check the thermistors' B value and ice-bath each probe; unmarked H11AA1s: find pin 1 with a meter's diode test (the LED pair reads ~1.1 V both ways)
- [ ] Wire the Y and O/B opto inputs
- [x] Zero the transducers open to atmosphere (use the Sensors & calibration page for this and the steps below)
- [x] Span against the Fieldpiece on nitrogen at about 300 psi, then check 100, 200 and 400 psi
- [x] Measure the 3V3 pin and set v33; calibrate thermistors in an ice bath
- [ ] Test Y and O/B with 24 VAC

## Phase 3: Indoor node on the bench

- [x] Wire the DS18B20 probes
- [x] Calibrate both probes in an ice bath; swap supply/return if reversed
- [x] Confirm the delta-T reading in the dashboard
- [ ] Wire the Y, W, G and O/B inputs (needed for the diagnostics, which only run during a call)

## Phase 4: Install on your own system

- [x] Boards installed on your AC on 24 VAC, live data in the web app (Oct 11)
- [x] Enclosures, mounting ear bars, cabinet mounting plates and an SHT30 louvre shield designed (hardware/enclosures, Hammond 1554R2GY as the buy option); board connector map in hardware/board-terminals.md
- [ ] Print or buy the enclosures and mount the nodes in them, shaded and outside the cabinets
- [ ] Give the PC (now 192.168.1.204, was .191) a DHCP reservation, or move the broker first
- [x] Move the MQTT broker to a Proxmox VM and point both nodes at it (Proxmox container, Oct 14)
- [ ] Change the weak OTA password and reflash
- [ ] Braze in the true-suction access fitting (or defer to Phase 6)
- [x] Set atmospheric pressure to about 14.0 psia in the PC app (config.json has 14.0; the cloud system "Home" has 14.0 too)
- [ ] Run through several weeks of real cooling cycles and log the data

## Phase 5: Validate the diagnostics

- [ ] Compare superheat, subcooling and delta-T against your own gauge readings
- [x] Gauge check panel on the Sensors & calibration page: enter your gauge and probe readings, and it stores them next to the system's reading from the same moment and shows the difference, with the average and largest difference over all checks
- [ ] Tune the alert thresholds from real data
- [x] Threshold report on the Equipment page: for steady cooling over the last 7, 14 or 31 days, the spread of superheat, subcooling, delta-T and condensing over ambient next to the alert limits, how much of that time each limit would flag, and the alerts raised
- [x] Equipment page in the web app, and nameplate subcooling targets for TXV/EEV systems (cloud; the PC dashboard keeps the generic limits)
- [x] Charging-chart target superheat for piston systems: (3 × return wet bulb − 80 − outdoor temp) / 2, ± 5 °F, judged in steady cooling once the indoor humidity probes report (Oct 17)

## Phase 6: Complete the V1 sensors

- [ ] Buy and fit the third XDB307 (0–35 bar) and a thermistor at true suction
- [ ] Enable them with the fitted command; confirm heating-mode superheat
- [x] SDP810 static pressure, software side (Oct 17, PR #54): indoor firmware 0.2.2 reads 3× SDP810-500Pa on mux channels 2 (return), 3 (supply), 4 (across the filter); the web app shows return, supply, total external static and filter drop, alerts on TESP above the Equipment max ESP and a filter drop over 0.30 in. w.c., and has a Static pressure zero panel on Sensors & calibration
- [ ] Buy 3 SDP810-500Pa sensors, tubing and static taps; fit taps in both plenums and on both sides of the filter, wire to mux channels 2-4, flash the indoor node and zero with the blower off
- [x] SHT45 supply and return humidity, software side (Oct 17): indoor firmware 0.2.1 finds a TCA9548A on J5 by itself (return probe on channel 0, supply on 1); the web app shows RH, wet bulb, dew point and enthalpy, with a Humidity probes panel (swap, calibration) on Sensors & calibration
- [ ] Buy 2 SHT45 probes and a TCA9548A, solder a terminal into the indoor board's J5, fit the probes in the return and supply, and flash the indoor node
- [x] Update firmware and schematics to rev B (merged Oct 18): both boards get the Wio-SX1262 LoRa module and a SETUP button; outdoor adds the discharge thermistor and three clamp-on current sensor jacks (firmware 0.3.0 reads them); indoor has the sensor hub on board. Order files released as hardware-rev-b (Oct 18); not ordered yet

## Phase 7: Cloud stack, running locally

- [x] Run the cloud locally without Docker (SQLite): the HVAC Monitor shortcut now starts the ingest worker and the Fullscope web app at localhost:8000
- [x] Web app restyled to the Fullscope design system: logo on a dark header bar, dark-first theme, D-DIN type self-hosted (no font CDN)
- [x] Sensors & calibration page in the web app: zero, span, ice bath, channels on/off and node settings sent to the nodes from the browser, with each node's reply logged
- [x] Nightly backup of the local database (keeps 7) and cleanup of old data: full detail for 30 days, then 1-minute averages, deleted after a year
- [x] Homeowner maintenance card: filter reminder by days or actual blower run time, tune-up reminder, service contractor details and a Request service button
- [x] Service history: log visits with the readings at the time; tune-ups and filter changes reset the reminders, and homeowners see recent visits
- [x] Install WSL and Docker Desktop (a restart, and accepting Docker's license), then start the TimescaleDB stack (tested: hypertables with 365-day retention, broker logins, data through to the API; broker passwords now come from .env)
- [x] Create your account and system with manage.py
- [x] Register both nodes: they appear on their first message once powered and online (no firmware change needed, same MQTT topics)
- [x] Port the cloud server and bench dashboard to C# (.NET 10): one `fullscope` program, tested against recorded Python output, with faster long history ranges; the Proxmox container runs it (deploy with `-Python` to go back)
- [ ] Confirm history, alerts, email and remote calibration all work (all built; needs the nodes online and alert email set up)

## Phase 8: Go live in the cloud

- [x] Production setup built and tested on the PC: HTTPS through Caddy, MQTT over TLS (port 8883) with a private CA, secure cookie, daily database backups with a tested restore, and a deployment checklist (DEPLOY.md)
- [ ] Rent a server and register a domain
- [ ] Deploy with DEPLOY.md (about an hour once the server and domain exist; HTTPS and the secure cookie come with it)
- [x] Firmware 0.2.0 written and built for both nodes: MQTT over TLS, timestamped readings with a 10-minute outage buffer, settings sent back after changes (cloud and dashboard store caught-up readings at their own time)
- [x] Flash firmware 0.2.0 (done at install, Oct 11)
- [ ] Test TLS, the clock sync and the outage buffer on real hardware
- [x] Per-user sign-in with contractor and homeowner roles (email and password, secure session cookie; homeowners see only their own system)
- [x] Invite links for homeowners and contractor colleagues, and a contractor Fleet page listing every system, most in need of attention first
- [x] Review of the cloud code for bugs and security: 8 fixes (alert emails now reach homeowners, password change signs out other sessions, remove colleagues, invite tokens kept out of logs, and smaller hardening)
- [x] Password reset: emailed one-time link, or a link the contractor makes for a homeowner when email is off
- [ ] Move sign-in to a managed provider (Auth0 or Clerk) for password reset, two-factor and phone-friendly login
- [x] Build alerts: a fault lasting 5 min raises an alert, "no data" after 10 min of silence, alert lists in the web app, email with a 6 h cooldown
- [x] Alert recipients (owner and/or contractor; plain-language emails for the owner, technical for the contractor), acknowledge, and mute by alert type
- [x] Alerts for checks that only run while the system runs (refrigerant, delta-T) stay open between cycles instead of clearing every time the compressor stops: one problem, one alert, no "back to normal" email each cycle
- [ ] Turn on alert email (SMTP account and recipient address), and copy the server's daily backups (built) and the CA key off the server regularly

## Decision point: 900 MHz radio architecture (before Phase 9)

Sheet-metal cabinets and homeowner WiFi are the weak points for nodes at the condenser and in the attic. Honeywell's RedLINK solves the same problem with a 900 MHz link to a gateway. Proposed design: the outdoor and indoor nodes each carry a LoRa radio and talk to a gateway that connects to the network. The standard gateway is a plug-in unit; the optional display node, which replaces the existing thermostat, has the gateway built in. Example: a two-story house with the air handler in the attic, where neither equipment node sits near the router.

- [x] Research the US rules: a fixed LoRa channel must be 500 kHz wide (or hop across 50+ channels). Chosen: one 500 kHz channel at SF9, 77 ms per reading, about 143 dB link budget (design in radio/README.md)
- [ ] Buy 3 Heltec WiFi LoRa 32 V3 boards (915 MHz) and prototype the link (range-test firmware is written and builds)
- [ ] Range-test on your own house: attic, condenser and thermostat location, antennas outside the cabinets
- [x] Define a compact telemetry format that fits LoRa airtime limits: 45-byte encrypted frames that decode to the same JSON, so the cloud needs no changes (tested reference in radio/lorafmt.py)
- [x] Gateway buffers readings and forwards them to the cloud over WiFi (written and merged into main Oct 4, not yet run on radios: a plug-in gateway for a Heltec V3, or the thermostat as the gateway, which also feeds the outdoor temperature to the controller offline)
- [x] LoRa firmware for the nodes (LORA\_ENABLED in config.h: readings, status and calibration commands over the radio), with the frame format and encryption ported to C++ and checked byte for byte against the Python reference; each site picks one of 8 channels from its site id; a tool makes each node's id and key
- [x] Humidity, static pressure and current readings over LoRa (merged Oct 18, PR #61): the indoor frame carries an optional humidity and static tail, current readings get their own frame, and the gateway republishes them on the same topics; built and tested on the PC
- [x] Keep a WiFi-direct mode for installs without a display node (kept: WiFi stays the default; LoRa is a config switch)
- [ ] Confirm the production module's FCC grant covers 500 kHz operation (candidates with modular approval: Heltec HT-CT62, Seeed Wio-SX1262, RAK3172)
- [ ] Decide: LoRa + gateway, WiFi only, or cellular (use the contractor interviews)

## Phase 9: Productize the device

- [ ] Custom PCB in KiCad combining power, ADCs, optos and the LoRa radio (JLCPCB)
- [ ] Route each equipment node's antenna outside the cabinet
- [ ] Plug-in gateway (standard with every system): LoRa to both nodes, WiFi to the cloud
- [ ] Decide on ESP32-C5 for the gateway's WiFi (adds 5 GHz support)
- [ ] Enable secure boot and flash encryption
- [ ] Load unique device credentials at manufacturing
- [ ] Bluetooth WiFi setup for the gateway from a phone, with QR claim codes that pair all devices
- [ ] Cloud firmware updates, relayed through the gateway to the nodes
- [ ] Local buffering during outages (nodes keep 10 minutes since firmware 0.2; longer storage and the gateway still to do)
- [ ] Heat-rated production enclosures

## Phase 10: Pilot

- [ ] Install on 5–10 systems with a friendly contractor
- [ ] Fix reliability issues found in the field
- [ ] Refine the contractor portal from their feedback
- [ ] Invite homeowners as viewers (invite links are built)

## Phase 11: Optional add-ons

### Display thermostat (replaces the existing thermostat)

- [x] Software side built (merged into main Oct 4): control and safety logic that runs on the thermostat itself (minimum off and run times, starts per hour, reversing valve switched only at rest, compressor and aux heat lockouts by outdoor temperature, emergency heat, sensor fail-safe), weekly schedule and holds, settings sent to the thermostat as a retained, versioned config, homeowner thermostat card, technician safety settings on the Equipment page, new alerts (room too hot or cold, setpoint not reached, thermostat and equipment disagree), a simulator; design in thermostat/README.md
- [x] Thermostat screen designed (browser preview at the real 800 × 480 size): Home; Alerts for homeowners (the monitoring system's alerts in plain words, System health areas, tap for what to do); Service for technicians behind a PIN (key numbers, pressure and air trend over 1 or 6 hours with markers where electrical problems were found, open diagnostics). The cloud sends the thermostat a ready-to-draw feed. Run-only alerts now stay open between cycles instead of clearing each time the compressor stops
- [x] Thermostat firmware written (builds for the Waveshare ESP32-S3 4.3" board, not yet run on hardware): the control and safety logic ported to C++ and checked against the Python version at every one of 32,400 simulated steps; screens, room sensor, output switches, hardware watchdog, settings kept in flash; changes made on the thermostat work offline and sync back to the cloud
- [ ] ESP32-S3 touchscreen with the gateway built in (LoRa + WiFi), so no separate plug-in gateway is needed
- [ ] Output relays for Y, Y2, W, W2, G and O/B, powered from R and C
- [ ] Scheduling, setpoints and heat pump / aux heat staging
- [ ] Safety logic: compressor minimum off time, short-cycle protection, aux heat lockout by outdoor temp, fail-safe if the firmware locks up
- [ ] Uses the system's own data for smarter control (defrost awareness, lockouts when a fault is detected)
- [ ] Thermostat safety certification (UL 60730) on top of FCC
- [ ] Buy the Waveshare ESP32-S3 4.3" touch board (\~$40), check its pin numbers against the schematic, and run the firmware on it
- [x] Thermostat merged into main (Oct 4), so the demo runs without switching branches; it stays unused until thermostat hardware exists

### Electrical monitoring module

- [ ] Amps, contactor voltage drop and capacitor under load (kept separate so the core product stays low-voltage)
- [x] Software side built (merged into main Oct 4): 10 diagnostic rules (compressor or fan not running, contactor not closing, amps vs RLA/FLA, run capacitor ±6 %, contactor drop, line voltage, slow starts), nameplate fields on the Equipment page, technician and homeowner views, a simulator with each fault; design in electrical/README.md
- [ ] Version 1 hardware, clamp-on current sensors only (no line contact, covers 5 of the 10 rules): built into the outdoor carrier rev B (three jacks, firmware 0.3.0, merged Oct 18); test once rev B boards are built
- [ ] Version 2: isolated voltage board for the capacitor test, contactor drop and line voltage, then a test-lab review for UL/IEC 61010
- [x] Electrical module merged into main with the thermostat (Oct 4); it stays unused until a module is installed

## Phase 12: Launch

- [ ] FCC Part 15 testing; UL/ETL for the electrical module
- [ ] Multi-contractor accounts and subscription billing
- [ ] Push notifications
- [ ] Install guides and support docs
- [ ] Volume sourcing direct from manufacturers
- [ ] Final pricing: hardware plus subscription

## Parallel tracks (start now)

### Customer discovery

Their answers shape Phases 5, 8 and 10.

- [x] Demo and interview kit ready: start-demo.bat (seven scripted homes with common faults) and the [Contractor Interview Kit](https://claude.ai/code/artifact/73343a90-f40b-4778-9df8-f79033e4813c) (21 questions, demo script, notes sheet)
- [x] Thermostat in the demo: a seventh home, Rivera, with a thermostat and electrical module whose run capacitor is weakening, shown on the thermostat's Alerts and Service screens; Garcia's homeowner view has a thermostat too. The interview kit's demo script has a Rivera step, shown after question 16
- [x] Project files organized (Oct 4): a project-files folder in the repo with this roadmap and the interview kit, the parts list, 43 screenshots by topic and the logo; a README on the GitHub repo's front page; HVAC Monitor Start and Fullscope Thermostat shortcuts on the Desktop (the thermostat screen now signs in and comes straight back)
- [ ] List 5–10 HVAC contractors to talk to
- [ ] Ask which alerts would save them truck rolls
- [ ] Ask what they would pay per system per month
- [ ] Ask who should get alerts: owner, dispatcher, technician or homeowner

### IP protection

- [ ] Book a consultation with a patent attorney
- [ ] File a provisional application before showing the system publicly or starting the pilot
- [ ] Keep dated notes and design files as you go
