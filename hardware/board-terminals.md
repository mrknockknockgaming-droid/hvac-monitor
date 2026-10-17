# Rev A carrier boards: what plugs in where

Checked 2026-10-11 against the routed `.kicad_pcb` pad nets, `hardware/scripts/design.py`
and the firmware (`node_outdoor.cpp`, `node_indoor.cpp`, `common.h`). Board and firmware agree.

Pin 1 is the end of each connector carrying the first silkscreen label listed.
Phoenix = MC 1,5 5.08 mm pluggable screw terminal. JST = JST-XH 2.50 mm.

## Outdoor board

| Ref | Type | Pins (silkscreen) | Sensor / wire | Firmware |
|---|---|---|---|---|
| J1 | Phoenix 2 | R, C | 24 VAC from the condenser (R hot, C common = board GND) | power |
| J2 | Phoenix 2 | Y, O/B | 24 VAC Y call and O/B reversing valve wire (both referenced to C on J1) | `Y` GPIO34, `OB` GPIO35 |
| J3 | JST 3 | +5, SIG, GND | XDB307 transducer, liquid line | `p_liq`, ADS 0x48 A0, on |
| J4 | JST 3 | +5, SIG, GND | XDB307 transducer, vapor (suction service) port | `p_vap`, ADS 0x48 A1, on |
| J5 | JST 3 | +5, SIG, GND | XDB307 true suction (0-35 bar), not fitted yet | `p_tsuc`, ADS 0x48 A2, off |
| J6 | JST 2 | TH, TH | 10k NTC thermistor, suction line (no polarity) | `t_suc`, ADS 0x49 A0, on |
| J7 | JST 2 | TH, TH | 10k NTC thermistor, liquid line (no polarity) | `t_liq`, ADS 0x49 A1, on |
| J9 | JST 2 | TH, TH | 10k NTC thermistor, true suction (extra, beyond rev A drawing) | `t_tsuc`, ADS 0x49 A2, off |
| J8 | Phoenix 4 | 3V3, GND, SDA, SCL | FS400-SHT30 outdoor air temp/RH probe | SHT30 @ 0x44 |

5 V rail monitor (R7/R8) is on ADS 0x48 A3. ADS 0x49 A3 (`t_dis`, discharge) has a net but no connector on rev A.

## Indoor board

| Ref | Type | Pins (silkscreen) | Sensor / wire | Firmware |
|---|---|---|---|---|
| J1 | Phoenix 2 | R, C | 24 VAC from the air handler / furnace (R hot, C common) | power |
| J2 | Phoenix 4 | Y, W, G, O/B | Thermostat calls Y, W, G, O/B (24 VAC, referenced to C on J1) | GPIO34, 35, 36 (VP), 39 (VN) |
| J3 | JST 3 | 3V3, DQ, GND | DS18B20 supply air probe | 1-Wire GPIO4 |
| J4 | JST 3 | 3V3, DQ, GND | DS18B20 return air probe | 1-Wire GPIO4 |
| J5 | Phoenix 4 | 3V3, GND, SDA, SCL | I2C expansion (DNP on rev A): solder a terminal here for the TCA9548A mux; SHT45 return probe on mux channel 0, supply on channel 1; SDP810-500Pa static sensors on channel 2 (return plenum), 3 (supply plenum, after the coil) and 4 (across the filter) | I2C 21/22, mux 0x70, SHT45 0x44, SDP810 0x25 (all found on their own) |

## Things to know

- **Indoor J3 and J4 are the same 1-Wire bus.** The firmware does not know which jack a probe is in;
  it calls the first probe it discovers "supply". If supply and return read backwards, send
  `{"cmd":"ds_swap"}` (saved to flash).
- Wire colors are not recorded in the repo. Typical DS18B20 probes: red = 3V3, yellow = DQ,
  black = GND. Check the XDB307 label or datasheet for its colors (usually red +5 V, black GND,
  third wire signal) before crimping the JST plugs.
- Pressure dividers: the drawing says 20k bottom resistors (R2/R4/R6) and 15k for R8; your build
  uses 10k, and `config.h` already sets `P_DIV_BOTTOM_K` / `RAIL_DIV_BOTTOM_K` to 10.0 to match.
- Never connect USB and 24 VAC to a board at the same time.
