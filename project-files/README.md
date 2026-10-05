# Project files

Everything about the HVAC Monitor / Fullscope project that isn't code: plans, the parts list,
screenshots of each stage of the web app and the thermostat screen, and branding. The design
documents live next to the code they describe; the table at the end points to each one.

Collected Oct 4, 2026. The roadmap and the interview kit are copies: the live versions are the
Claude Docs linked at the top of each file, and those are the ones to edit.

## 1-plans

| File | What it is |
|---|---|
| [roadmap.md](1-plans/roadmap.md) | Phases 0–12, the radio decision point, customer discovery and IP tracks, with what's done ([live doc](https://claude.ai/code/artifact/e6a28e8e-9f92-404c-92b2-67be47d77f7e)) |
| [contractor-interview-kit.md](1-plans/contractor-interview-kit.md) | Who to talk to, demo setup, the 10-minute demo script, 21 questions, what each answer decides, notes sheet ([live doc](https://claude.ai/code/artifact/73343a90-f40b-4778-9df8-f79033e4813c)) |

## 2-parts

[HVAC_Monitor_Parts_List.xlsx](2-parts/HVAC_Monitor_Parts_List.xlsx): a copy of the parts list from the Desktop (Oct 4), with these sheets:
- Summary, Power Kit, Outdoor Node, Indoor Node, Infrastructure
- Display Node, with the thermostat module
- LoRa Radio

Costs are split by phase: V1, Optional, Phase 2, Future (thermostat) and LoRa. The working copy stays on the Desktop.

## 3-screenshots

Taken in the browser while each feature was built and checked. They show test and demo data only.

| Folder | Shows |
|---|---|
| 1-first-web-app-sep29 | The first web app: homeowner page, technician monitor, live trends, refrigerant/air tables, sensor health |
| 2-fullscope-redesign-oct2 | The Fullscope design system: logo header, dark and light themes |
| 3-technician-tools | Alert log; Sensors & calibration (zero / span from the browser) |
| 4-homeowner | Maintenance card: filter and tune-up reminders, contractor, recent service |
| 5-accounts-and-fleet | Email and password sign-in; the contractor's fleet page, worst first |
| 6-contractor-demo | The interview demo: fleet, Brooks home (floodback), Rivera's thermostat screen and capacitor marker |
| 7-thermostat-web | Thermostat card, holds and schedule, technician rows, safety settings, phone width |
| 8-thermostat-screen | The thermostat's own 800 × 480 screen: Home, Alerts (plain language, System health, what to do), Service (trend with electrical markers) |
| 9-electrical-module | Technician electrical rows: line volts, contactor, amps, run capacitor |

## 4-branding

`fullscope-logo-original.png`, a copy of the original logo file (the `fullscope logo` file at the
top of the repo). The web app's logos and design system are in [../design](../design).

## Where everything else is

| Topic | Where |
|---|---|
| Project overview and current state | [../CLAUDE.md](../CLAUDE.md) |
| Cloud: setup, demo, alerts, API | [../hvac-cloud/README.md](../hvac-cloud/README.md) |
| Going live on a server | [../hvac-cloud/DEPLOY.md](../hvac-cloud/DEPLOY.md) |
| Node firmware: flashing, MQTT topics, commands, calibration, LoRa mode | [../hvac-firmware/README.md](../hvac-firmware/README.md) |
| Wiring schematics (rev A) | [../docs/hvac-schematics.html](../docs/hvac-schematics.html) |
| Carrier boards (rev A, KiCad, JLCPCB) | [../docs/carrier-boards.html](../docs/carrier-boards.html), [../hardware](../hardware) (schematic PDFs in each carrier folder) |
| Display thermostat: design, safety rules, screen, firmware | [../thermostat/README.md](../thermostat/README.md) |
| Electrical module: design and rules | [../electrical/README.md](../electrical/README.md) |
| 900 MHz LoRa radio: FCC rules, frames, security, gateway, range test, firmware | [../radio/README.md](../radio/README.md) |
| Design system, mockups, logos | [../design](../design) |
| PC dashboard (bench tool) | [../hvac-monitor-app](../hvac-monitor-app) |
