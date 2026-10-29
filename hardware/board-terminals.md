# Rev B carrier boards: what plugs in where

Checked 2026-10-18 against the routed rev B `.kicad_pcb` pad nets (`checkpoint1-summary.txt`),
`hardware/scripts/design.py` and firmware 0.3.0 (`node_outdoor.cpp`, `ct.cpp`, `node_indoor.cpp`,
`lora_link.cpp`, `common.h`). Board and firmware agree.

Sources: for rev B, `hardware/scripts/design.py` and the generated
`*-carrier/*-schematic.pdf` are the wiring source. `docs/hvac-schematics.html` and
`docs/carrier-boards.html` show rev A only.

Pin 1 is the end of each connector carrying the first silkscreen label listed.
Phoenix = MC 1,5 5.08 mm pluggable screw terminal. JST = JST-XH 2.50 mm (vertical, top entry).
Jack = 3.5 mm TRS socket (CUI SJ1-3523N): tip = signal, sleeve = CT_BIAS, ring not connected.

## Outdoor board (100 x 100 mm)

| Ref | Edge | Type | Pins (silkscreen) | Sensor / wire | Firmware |
|---|---|---|---|---|---|
| J1 | top | Phoenix 2 | R, C | 24 VAC from the condenser (R hot, C common = board GND) | power |
| J2 | right | Phoenix 2 | Y, O/B | 24 VAC Y call and O/B reversing valve wire (both referenced to C on J1) | `Y` GPIO34, `OB` GPIO35 |
| J8 | right | Phoenix 4 | 3V3, GND, SDA, SCL | FS400-SHT30 outdoor air temp/RH probe | SHT30 @ 0x44 |
| J3 | bottom | JST 3 | +5, SIG, GND | XDB307 transducer, liquid line | `p_liq`, ADS 0x48 A0, on |
| J4 | bottom | JST 3 | +5, SIG, GND | XDB307 transducer, vapor (suction service) port | `p_vap`, ADS 0x48 A1, on |
| J5 | bottom | JST 3 | +5, SIG, GND | XDB307 true suction (0-35 bar) | `p_tsuc`, ADS 0x48 A2, off |
| J6 | bottom | JST 2 | TH, TH | 10k NTC thermistor, suction line (no polarity) | `t_suc`, ADS 0x49 A0, on |
| J7 | bottom | JST 2 | TH, TH | 10k NTC thermistor, liquid line | `t_liq`, ADS 0x49 A1, on |
| J9 | bottom | JST 2 | TH, TH | 10k NTC thermistor, true suction | `t_tsuc`, ADS 0x49 A2, off |
| J10 | bottom | JST 2 | TH, TH | 10k NTC thermistor, discharge line (new in rev B; needs a probe rated past 250 °F) | `t_dis`, ADS 0x49 A3, off |
| J11 | left | Jack | CT1 comp | SCT-013-000 (100 A : 50 mA) on the compressor common; burden R19 = 18 R fitted | `ct_comp`, GPIO36 (VP), off |
| J12 | left | Jack | CT2 fan | SCT-013-005 (5 A : 1 V, voltage output); burden R21 not fitted | `ct_fan`, GPIO39 (VN), off |
| J13 | left | Jack | CT3 HERM | SCT-013-010 (10 A : 1 V) on the compressor HERM lead (run capacitor current); burden R23 not fitted | `ct_herm`, GPIO32, off |

- The 5 V rail monitor (R7/R8) is on ADS 0x48 A3. All four dividers (R2/R4/R6/R8) are 10k on
  rev B, the same as Tyler's rev A build, so `P_DIV_BOTTOM_K` / `RAIL_DIV_BOTTOM_K` default to 10.
- CT inputs: each tip goes through 1k (R20/R22/R24) to the ADC pin with 10 nF (C12-C14) to GND;
  the sleeves share CT_BIAS = 3.3 V x 6.8k / 16.8k = 1.34 V (R17/R18, C11 10 µF). Fit the
  burden resistor only for a current-output CT (SCT-013-000); voltage-output models
  (-005, -010, ...) have their own. Turn a channel on with `{"cmd":"fitted","ch":"ct_comp","on":true}`.
  Calibrate against a clamp meter with `{"cmd":"cal_ref","ch":"ct_comp","ref":<amps>}` while running.
- The jacks sit on the left board edge; in the printed box the plugs go in through the left wall.

## Indoor board (100 x 88 mm)

| Ref | Edge | Type | Pins (silkscreen) | Sensor / wire | Firmware |
|---|---|---|---|---|---|
| J1 | top | Phoenix 2 | R, C | 24 VAC from the air handler / furnace (R hot, C common) | power |
| J2 | bottom | Phoenix 4 | Y, W, G, O/B | Thermostat calls Y, W, G, O/B (24 VAC, referenced to C on J1) | GPIO34, 35, 36 (VP), 39 (VN) |
| J3 | left | JST 3 | 3V3, DQ, GND | DS18B20 supply air probe | 1-Wire GPIO4 |
| J4 | left | JST 3 | 3V3, DQ, GND | DS18B20 return air probe | 1-Wire GPIO4 |
| J5 | right | Phoenix 4 | 3V3, GND, SDA, SCL | Main I2C bus terminal (fitted on rev B), for anything else on the bus | I2C 21/22 |
| J6 | bottom | JST 4 | 3V3, GND, SDA, SCL | SHT45 return air (mux channel 0) | SHT45 0x44 via TCA9548A 0x70 |
| J7 | bottom | JST 4 | 3V3, GND, SDA, SCL | SHT45 supply air (mux channel 1) | same |
| J8 | bottom | JST 4 | 3V3, GND, SDA, SCL | SDP810-500Pa return plenum static (channel 2) | SDP810 0x25 |
| J9 | bottom | JST 4 | 3V3, GND, SDA, SCL | SDP810-500Pa supply plenum static, after the coil (channel 3) | SDP810 0x25 |
| J10 | bottom | JST 4 | 3V3, GND, SDA, SCL | SDP810-500Pa across the filter (channel 4) | SDP810 0x25 |

- The TCA9548A (U7, TSSOP-24, address 0x70: A0-A2 tied low, RESET pulled up by R10) is on the
  board now. Each channel has its own 10k SDA/SCL pull-ups (R11-R20). The firmware finds it by
  itself; nothing to configure. Don't add a second 0x70 mux on J5.

## Both boards (rev B additions)

| Ref | What | Pins | Firmware |
|---|---|---|---|
| U7 outdoor / U8 indoor | Seeed Wio-SX1262 LoRa module (u.FL), at the top edge with the u.FL toward the top wall | NSS GPIO5, SCK 18, MISO 19, MOSI 23, DIO1 26, NRST 27, BUSY 25, RF_SW 13 | `lora_link.cpp` defaults (`LORA_PIN_*`), only with `LORA_ENABLED 1` |
| U8 outdoor / U9 indoor | AMS1117-3.3 for the radio only (+3V3_RF), fed from +5V | | |
| SW1 | SETUP push button, GPIO14 to GND | | `PIN_SETUP` 14, INPUT_PULLUP; hold 3 s for Bluetooth WiFi setup (fw 0.3.1) |

- Antenna: a u.FL to SMA-bulkhead pigtail (about 10-15 cm) to the SMA hole in the box's top
  wall, then a 900 MHz whip outside the cabinet. Never transmit without the antenna connected.
- SETUP is pressed through the lid by the printed plunger (see `enclosures/README.md`).

## Thermostat carrier (rev A, 100 x 100 mm, 2026-10-29)

Sits behind a Waveshare ESP32-S3-Touch-LCD-4.3, which runs everything. That board's screen uses
almost every GPIO, so the carrier talks to it over I2C only, plus one wire for the radio's
interrupt. Source: `hardware/scripts/design.py` (`thermostat()`), firmware `thermostat/firmware/include/board.h`.

| Ref | Edge | Type | Pins (silkscreen) | Wire / cable | Firmware |
|---|---|---|---|---|---|
| J1 | bottom | Phoenix 3 | Rh, Rc, C | Heat and cool transformer R, common. Board power is Rc-C (C = board GND), so a C wire is required | power |
| JP1 | bottom left | 2-pin header + shunt | Rc=Rh | Fit the shunt for one transformer (most systems); remove it for separate heating and cooling transformers | |
| J2 | bottom | Phoenix 6 | G, Y1, Y2, W1, W2, O/B | Equipment call wires. Y1 Y2 G O/B switch from Rc, W1 W2 from Rh | outputs MCP23017 GPA4, GPA0, GPA1, GPA2, GPA3, GPA5; read-back GPB3, GPB0, GPB1, GPB2, GPB5, GPB4 |
| J3 | top | USB-C (power only) | 5 V in | Display-only node: a 5 V USB charger instead of 24 VAC | power |
| J4 | top | USB-A | to display USB | Short USB-A to USB-C cable to the display's USB port (power only) | |
| J5 | right | 4-pin 2.54 header | 3V3, GND, SDA, SCL | The display's I2C cable. Its 3V3 wire goes on the 3V3 pin, which is not connected on the carrier | I2C GPIO8/9 |
| J6 | right | 3-pin 2.54 header | 3V3, GND, IO6 | The display's sensor-header cable (3V3 not connected) | LoRa DIO1 on GPIO6 |
| J7 | right | Qwiic (JST SH 4) | GND, 3V3, SDA, SCL | SHT45 room sensor breakout on a Qwiic cable, mounted on the isolated tab | SHT45 @ 0x44 |
| U6 | top right | Wio-SX1262 | u.FL toward the top edge | u.FL pigtail to the antenna | via SC18IS602B @ 0x28 |

- Outputs K1-K6: AQV252G PhotoMOS (60 V, 2.5 A) in DIP-6 sockets, a SMBJ40CA across each. F2/F3
  are 1.25 A PTCs in the Rc and Rh feeds, so a shorted call wire trips them instead of a switch.
- Read-backs U7-U12: H11AA1 from each call terminal to C, as on the nodes. With separate heating
  and cooling transformers, W1/W2 read-backs only work if the two commons are tied.
- Watchdog: TPS3823-33 resets the MCP23017 (every output off) if the firmware stops toggling
  GPA6 for 1.6 s. The display board's own EN is not reachable.
- Set U1 to **5.30 V** before fitting anything: the display's 5 V comes through a Schottky (D3).
- Never plug a PC into the display's USB ports while 24 VAC is on the carrier (C is GND).

## Things to know

- **Indoor J3 and J4 are the same 1-Wire bus.** The firmware does not know which jack a probe is in;
  it calls the first probe it discovers "supply". If supply and return read backwards, send
  `{"cmd":"ds_swap"}` (saved to flash).
- Wire colors are not recorded in the repo. Typical DS18B20 probes: red = 3V3, yellow = DQ,
  black = GND. Check the XDB307 label or datasheet for its colors (usually red +5 V, black GND,
  third wire signal) before crimping the JST plugs. SHT45 / SDP810 breakouts: check each board's
  pin order against 3V3 / GND / SDA / SCL before crimping.
- Never connect USB and 24 VAC to a board at the same time.
