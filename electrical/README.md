# Electrical monitoring module (roadmap phase 11): design

Measures the outdoor unit's electrical side: compressor and condenser-fan current, line voltage,
the voltage drop across the contactor, and the run capacitor under load. Sold and installed as
a separate module so the core product stays low-voltage.

**Status (2026-10-04):** the cloud side is built and tested with simulated data (the
`electrical` node, ten diagnostic rules, technician and homeowner views, nameplate fields; see
`hvac-cloud/hvaccloud/electrical.py`). No hardware or firmware yet.

> **Line voltage.** Anything that connects to the 208/240 V side must be built and installed by
> a qualified person, with the circuit de-energized and locked out, and the measuring side
> isolated from everything a person or the low-voltage wiring can touch (section 4). Version 1
> below avoids line contact entirely.

## 1. What it catches

| Rule (code) | Needs | Measured how | Level |
|---|---|---|---|
| Compressor not running with the contactor closed (`comp_not_running`) | amps | CT on the compressor common (C) wire | fault |
| Condenser fan not running (`fan_not_running`) | amps | CT on a fan motor lead | fault |
| Cooling call but contactor not closed (`contactor_open`) | contactor state | coil voltage (24 VAC, low-voltage side) or current | fault |
| Compressor amps above RLA (`comp_amps_high`) | amps + nameplate RLA | CT | caution |
| Fan amps above FLA (`fan_amps_high`) | amps + nameplate FLA | CT | caution |
| Slow / hard start (`slow_start`) | inrush peak and duration | CT, sampled fast at start-up | caution |
| Run capacitor outside ±6 % (`cap_herm`, `cap_fan`) | current through the cap and voltage across it | CT on HERM / FAN wire + isolated voltage | caution |
| Contactor drop over 1 V (`contactor_drop`; replace over 2 V) | voltage across the closed contacts | isolated voltage, line vs load side of one pole | caution |
| Line voltage outside the nameplate range (`voltage`) | line voltage | isolated voltage | caution |

Capacitance under load: µF = amps × 2652 ÷ volts (60 Hz), compared with the rating (±6 %).
Contactor: under 1 V across closed contacts is normal (good ones read tens of millivolts); over
2 V means pitted contacts. All rules wait for a minute of compressor call before judging amps.

## 2. Two versions

**Version 1: current only (no line contact).** Clamp-on split-core current transformers (CTs)
on the insulated compressor common and fan leads, plus the contactor coil (24 VAC) for state.
Covers five of the rules (not running, fan not running, contactor not closing, high amps, slow
start). Low-voltage only, so it can be added to the outdoor carrier board's next revision or a
small add-on board, powered with the outdoor node.

**Version 2: adds isolated voltage.** Line voltage, voltage across each run-capacitor section,
and across one contactor pole: the capacitor test, contactor drop and voltage rules. Needs a
separate, isolated measuring board (section 4) and a certification path (section 6).

## 3. Sensors and parts (prototype)

| Part | Use | Notes |
|---|---|---|
| Split-core CT, 100 A (e.g. YHDC SCT-013-000, 50 mA output + burden resistor) | compressor common | 100 A so the start inrush (LRA ~70–110 A) doesn't saturate |
| Split-core CT, 5–10 A | fan lead; HERM lead for the capacitor current | small, accurate at low current |
| Energy-metering IC (e.g. ADE7953 or ATM90E26: RMS current/voltage, waveform sampling) | version 2 voltage + current | does the RMS maths; 24-bit inputs resolve millivolts for the contactor drop |
| Digital isolator + isolated DC-DC (reinforced) | version 2 | separates the line-referenced IC from the ESP32 |
| ESP32 (or the outdoor node's) ADC with a burden resistor | version 1 CT inputs | sample ~2–4 kHz for RMS; catch the inrush peak and duration at contactor close |

Wiring at the condenser: compressor C wire and fan lead through the CTs; the HERM lead through a
small CT for the capacitor current. Version 2 taps: L1/L2 (line side of the contactor), the load
side of one pole, and the capacitor C/HERM/FAN terminals, each through fused, high-value
divider resistors on the isolated board.

## 4. Isolation (version 2)

- The measuring board's ground is a line conductor. Everything on it is treated as live.
- Reinforced isolation between it and the ESP32/low-voltage side: a digital isolator for the
  IC's SPI/UART and an isolated DC-DC (or a transformer-isolated supply) rated for reinforced
  isolation at 300 V working voltage.
- Creepage/clearance on the PCB per the standard (several millimetres for 240 V), slots under
  the isolation barrier, fused inputs, divider resistors rated for the voltage.
- Its own enclosure in the unit's electrical compartment, with strain relief; the CTs clamp on
  insulated wire and don't touch conductors.

## 5. Data (already accepted by the cloud)

The module publishes `hvac/<site>/electrical/telemetry`, every few seconds:

```json
{"node": "electrical", "uptime": 312, "contactor": true,
 "v":   {"line": 241.0, "contactor": 0.05},
 "i":   {"comp": 11.8, "fan": 1.1},
 "cap": {"herm": {"v": 372.0, "i": 6.3}, "fan": {"v": 368.0, "i": 0.69}},
 "start": {"peak_a": 58.0, "ms": 240},
 "err": []}
```

Version 1 sends `null` for the voltages and capacitor values; those rules then don't run. It can
be its own ESP32 or extra fields published by the outdoor node on the `electrical` topic. Over
LoRa it would need its own compact frame type (see `radio/`, branch `radio/lora-design`).

Nameplate values for the rules go on the Equipment page: compressor RLA / LRA, fan FLA, run
capacitor µF (HERM and fan), voltage range (197–253 V assumed for 208/230 V units when empty).

## 6. Certification

Line-voltage measuring equipment needs safety certification before sale: typically UL/IEC 61010-1
(measurement equipment) with 61010-2-030, or UL 60730 if it ends up switching anything. Version 1
(current transformers on insulated wire, low-voltage electronics) is a much smaller step than
version 2. A test lab review of the version 2 isolation design early saves a re-spin.

## 7. Trying it now (no hardware)

```powershell
.venv\Scripts\python.exe demo_publisher.py --site demo --electrical --fault weak_cap
```

Faults: `weak_cap`, `pitted_contactor`, `fan_dead`, `comp_tripped`, `low_voltage`, `slow_start`
(or `none`). Enter RLA 14.1, LRA 73, fan FLA 1.4, capacitor 45/5 µF on the demo system's
Equipment page. Each fault raises its one flag in the technician view, the alert log and the
homeowner page.

## Next

1. Decide whether version 1 goes on the outdoor carrier rev B or a separate add-on board.
2. Prototype version 1: two CTs + burden resistors into the outdoor ESP32, firmware to sample
   RMS current and catch starts, publish on the `electrical` topic.
3. Version 2 isolated board design, then a test-lab review.
