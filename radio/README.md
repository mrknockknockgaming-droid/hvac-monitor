# 900 MHz radio (LoRa) for the nodes: design and decision work

Roadmap: *Decision point: 900 MHz radio architecture (before Phase 9)*. Sheet-metal cabinets
and homeowner WiFi are the weak points for nodes at the condenser and in the attic. The idea,
like Honeywell's RedLINK: each node carries a LoRa radio and talks to a gateway (a plug-in unit,
or built into the display thermostat) that forwards to the cloud.

**Status (2026-10-03):** regulatory and module research done; radio settings chosen; frame
format, encryption and gateway logic written as a tested Python reference (`lorafmt.py`, 8 tests,
`vectors.json` for the firmware); range-test firmware for two Heltec V3 boards built (not run).
Waiting on: buying the prototype boards, the range test, contractor interviews.

## 1. US rules (FCC Part 15.247, 902–928 MHz)

There are three ways to be legal, and they decide the radio settings:

| Approach | Requirement | Power |
|---|---|---|
| Frequency hopping (FHSS) | ≥ 50 channels, pseudo-random hops, **≤ 400 ms on any channel per 20 s** | 1 W (30 dBm) |
| Digital modulation (DTS) | **6 dB bandwidth ≥ 500 kHz**, ≤ 8 dBm per 3 kHz | 1 W |
| Hybrid | hops (no 500 kHz minimum), 400 ms dwell, DTS power density | 1 W |

**A fixed 125 kHz or 250 kHz LoRa channel meets none of them**: it is too narrow for DTS and
doesn't hop. LoRaWAN avoids this by hopping over 64 channels, which needs an 8-channel
concentrator gateway (SX1302, ~$100+).

**Choice: one fixed channel at 500 kHz bandwidth (DTS).** It's legal without hopping, works with
a single-chip SX1262 gateway, and a 45-byte frame takes 77 ms at SF9. It costs 6 dB of
sensitivity against 125 kHz, but the link budget below has plenty to spare.

## 2. Radio settings and link budget

915-ish MHz, 500 kHz bandwidth, coding rate 4/5, preamble 8, explicit header, CRC on, private
sync word. Spreading factor 9 by default (11 as the fallback for a hard install).

| SF (500 kHz) | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|
| Outdoor frame (45 B) airtime, ms | 23 | 41 | 77 | 144 | 267 | 494 |
| SX1262 sensitivity, dBm (≈) | −117 | −120 | −123 | −126 | −128 | −131 |

At 20 dBm transmit power and SF9 the link budget is about **143 dB**. A path from a condenser
or attic through a house typically loses 90–120 dB at 915 MHz, so even with 10–20 dB for metal
cabinets and antenna placement there should be 15+ dB left. The range test (section 7) checks
that on a real house. Airtime is tiny: one 77 ms frame every 5 s is 1.5 % of the time.
(`python lorafmt.py` prints the full airtime table.)

## 3. Frames (`lorafmt.py`, format version 1)

```
| ver|type (1) | device id (4) | counter (4) | encrypted payload | tag (8) |
  └──── authenticated, not encrypted ─────┘
```

- **Readings:** outdoor 28-byte payload → 45-byte frame; indoor 13 → 30. Values are int16 tenths
  (psig, °F), humidity in tenths of a percent, the 5 V rail in 10 mV steps, mode inputs and error
  codes as bit fields, and `age` (seconds since measured, for readings kept during an outage).
  The gateway turns them back into **exactly the JSON the WiFi firmware sends**, plus `ts` and
  the radio's RSSI/SNR, and publishes it on the same MQTT topics: **the cloud and the PC
  dashboard need no changes.**
- **Status:** firmware version, interval, backlog, dropped (10 bytes).
- **Commands / replies:** every command on the Sensors & calibration page fits in 7 bytes
  (command, channel, value, flag); replies carry ok, an error code and the new offset/scale.
- **Not over LoRa:** the raw sensor volts/ohms and the full calibration table. Commissioning
  (zero, span, ice bath) can still be done over LoRa; the raw numbers are only visible when the
  node is set up on WiFi. A "send raw values" command can be added if technicians miss them.

## 4. Security

- **AES-128-CCM** per frame (encrypt + 8-byte authentication tag; mbedTLS on the ESP32 has it).
  The header is authenticated too, so the device id and counter can't be changed.
- **Per-device key**, loaded at manufacturing and registered with the cloud (Phase 9: QR claim
  codes). A copied frame from a neighbour's system fails authentication.
- **Replays refused:** the counter must go up. The node saves it to flash every 100 frames and
  jumps ahead by 100 after a reboot, so a counter is never reused; the gateway keeps the highest
  one it accepted per device.
- The nonce (device id + counter + type) never repeats for a key, which CCM requires.

## 5. Gateway

- ESP32-S3 + SX1262 (prototype: a third Heltec V3). Listens continuously on the site's channel.
- For each frame: look up the device key, authenticate, check the counter, decode, publish to
  `hvac/<site>/<node>/telemetry|status|reply` over MQTT/TLS (firmware 0.2's TLS and outage
  buffer code carries over), and add `radio: {rssi, snr}` so the technician view can show link
  quality.
- **Downlink:** the nodes run from 24 VAC, so they can listen whenever they're not sending: the
  gateway forwards a command as soon as it arrives on `.../cmd` and the node replies at once.
- Its own status (`hvac/<site>/gateway/status`): uptime, frames per node, last RSSI/SNR, errors.
- **WiFi-direct mode stays:** a node with WiFi credentials and no gateway works exactly as today.

## 6. Channel plan

Eight 500 kHz channels, 903.0 + 1.6·n MHz (n = 0–7, the LoRaWAN US915 500 kHz uplink grid). A
site uses one, chosen from its site id (FNV-1a hash mod 8, `channel_mhz` in both lorafmt.py and
the C++ library), so neighbouring systems usually don't share. The range test uses 915.0 MHz.

## 7. Range test (two Heltec V3 boards, `rangetest/`)

1. Flash one board as `sender`, one as `receiver` (PlatformIO, see `rangetest/platformio.ini`).
2. Put the receiver where a gateway would plug in (near the router or thermostat), on USB to a
   laptop logging the serial CSV.
3. Put the sender, on a USB power bank, at the outdoor unit (inside the service panel area,
   then outside the cabinet), in the attic next to the air handler, and in the garage.
4. At each spot, run SF9 for 5 minutes, then SF7 and SF11 (press PRG on both boards).

**Pass at SF9:** < 1 % packets lost, and RSSI ≥ −108 dBm (15 dB above sensitivity) at the
worst spot. If it only passes at SF11, LoRa still works but with less margin; if it fails at
SF11, rethink the antenna placement before the whole approach.

## 8. Hardware

**Prototype (order now if going ahead):**

| Item | Qty | Approx. |
|---|---|---|
| Heltec WiFi LoRa 32 (V3), **915 MHz** version (ESP32-S3 + SX1262, OLED) | 3 (2 range test + gateway) | $20–25 each |
| 915 MHz antenna, 3 dBi, SMA, with u.FL-to-SMA pigtail | 3 | $8 each |
| USB power bank (for the sender during the walk-around) | 1 | (any) |

**Production candidates (FCC modular approval, so the product doesn't need its own radio
certification beyond the usual unintentional-emitter testing):**

| Module | What it is | FCC ID |
|---|---|---|
| Heltec **HT-CT62** | ESP32-C3 + SX1262 in one 18 mm module, ~$7 | 2A2GJ-HT-CT62 |
| Seeed **Wio-SX1262** | SX1262 radio module, pairs with any ESP32 | Z4T-WIO-SX1262 |
| RAKwireless **RAK3172** | STM32WL (MCU + LoRa radio) | 2AF6B-RAK3172 |

Before choosing, confirm with each module's FCC grant (or a test lab) that it covers 500 kHz DTS
operation at the power we'd use, and which antennas it was approved with.

## 9. The decision

| | LoRa + gateway | WiFi only (today) | Cellular (LTE-M) |
|---|---|---|---|
| Reaches attic / condenser | Yes, with margin (range test to confirm) | Often weak through metal | Usually yes |
| Extra hardware per install | Gateway (~$15–25 BOM) | None | Modem per node (~$15–25) |
| Monthly cost | None | None | ~$1–3 per node |
| Install effort | Plug in gateway, pair (QR) | Enter WiFi at each node | SIM activation |
| Depends on homeowner WiFi | Gateway only | Every node | No |

Decide after the range test and the contractor interviews (who installs, how much setup they'll
tolerate, what a monthly fee does to the price).

## Firmware (written, tested on the PC, not yet run on radios)

- **`lora/lib/lorafmt`**: the C++ port of `lorafmt.py`. It covers AES-128-CCM (portable code;
  the S-box is computed rather than typed in; checked against FIPS-197), the frames, the channel
  plan and the gateway logic. `tools/make_cpp_vectors.py` produces exact cases from the Python
  reference: 300 random readings, plus statuses, commands, replies, the channel plan, and a
  gateway session with replays, a wrong key, an unknown device and a tampered frame.
  `pio test -e native` in `lora/` matches every byte (7 tests).
- **`lora/lib/lora_radio`**: the SX1262 via RadioLib on the site's channel (500 kHz, SF9,
  20 dBm). It always listens between transmissions.
- **Plug-in gateway**: `lora/src/main.cpp`, for a Heltec V3 (`pio run -e gateway_heltec`). It
  republishes frames on the usual topics and sends commands from `hvac/<site>/<node>/cmd` to the
  node. An OLED shows frames and RSSI per node, and it reports its own status (retained) on
  `hvac/<site>/gateway/status`.
- **Nodes**: `hvac-firmware`, with `LORA_ENABLED 1` in `config.h`. Readings, status (every
  5 min and after changes) and command replies go over LoRa instead of WiFi, and commands are
  handled exactly as over MQTT.
- **Thermostat as the gateway**: `thermostat/firmware`, with `LORA_GATEWAY 1`. It does the same
  job, and takes the outdoor air temperature straight from the outdoor node's frames, so the
  heat-pump lockouts work with the internet down. It needs free pins on the display board for
  the SX1262; check Waveshare's schematic.
- **Keys**: `tools/new_device.py outdoor` prints a node's device id and key, and the matching
  line for the gateway. There is one key per node.
- **Counters**:
  - Each node saves its counter every 100 frames and starts 100 ahead after a reboot, so a nonce
    is never reused.
  - The gateway saves each node's highest counter every 20 frames, so after a reboot at most 20
    old frames could be replayed once.
  - The gateway's own command counter jumps 100 ahead on boot.
- **Over LoRa there is:**
  - no "ts", because nodes on LoRa have no clock (the gateway stamps arrival time);
  - no over-the-air updates;
  - no WiFi status fields.

## Files

- `lorafmt.py`: reference frame format, encryption, gateway logic, airtime calculator.
- `test_lorafmt.py`: 8 tests; `python test_lorafmt.py` rewrites `vectors.json`.
- `vectors.json`: exact frames (key, ids, counters, bytes) the C++ firmware must reproduce.
- `rangetest/`: sender/receiver firmware for the range test (builds; not yet run on boards).
- `lora/`: C++ frame library, radio wrapper, plug-in gateway firmware and their tests (see above).
- `tools/make_cpp_vectors.py`, `tools/new_device.py`.

Run the tests: `python -m venv .venv`, `.venv\Scripts\pip install cryptography pytest`,
`.venv\Scripts\python -m pytest -q`.

Sources: FCC 47 CFR 15.247; nodakmesh.org, "FCC Part 15.247: The 500 kHz Minimum and LoRa
Mesh"; Semtech SX1261/2 datasheet (time on air, sensitivity); FCC grants listed above.
