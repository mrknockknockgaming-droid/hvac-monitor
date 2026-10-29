"""Single source of truth for both carrier boards. Rev A was transcribed from
docs/hvac-schematics.html (2026-09-24); rev B (2026-10-18) adds the product features below.

Each part: ref, lib_id, value, footprint, schematic position (mm), and a
pin-number -> net map. 'NC' marks a no-connect. Nets named GND, +5V, +3V3 are
drawn as power symbols; every other net is a local label, so labels with the
same name are connected, exactly as on the rev A drawings.

Deviations from rev A (agreed with the user):
  * Outdoor: J9 + R15 + C9 add a third thermistor channel on T_TSUC
    (same circuit as T_SUC). TH3 is the off-board sensor, marked DNP.
Rev B (2026-10-18, "everything the final product has", still a hand-built carrier
with plug-in modules):
  * Both: Wio-SX1262 LoRa radio (U7 outdoor / U8 indoor) on the ESP32's VSPI with its own
    AMS1117-3.3 (the DevKit's regulator can't also feed 125 mA TX bursts), u.FL lead to an
    SMA bulkhead antenna outside the cabinet; SETUP button on D14 for phone (BLE) setup.
    Pins: NSS D5, SCK D18, MISO D19, MOSI D23, DIO1 D26, NRST D27, BUSY D25, RF_SW D13.
  * Outdoor: R2/R4/R6/R8 are 10k (as built on rev A; one resistor value on the board).
    J10 + R16 + C10: discharge-line thermistor on T_DIS (ADS 0x49 A3, firmware t_dis).
    Three CT inputs (electrical module version 1) on 3.5 mm jacks for SCT-013 clamps:
    CT1 compressor common (VP), CT2 condenser fan (VN), CT3 HERM lead (D32), each with a
    burden footprint, 1k + 10 nF to the ADC pin, sleeves on a shared CT_BIAS (~1.34 V).
  * Indoor: TCA9548A mux (U7, 0x70) on the board with JST-XH 4-pin jacks for the SHT45
    return/supply (ch 0/1) and SDP810 return/supply/filter (ch 2/3/4), 10k pull-ups per
    channel. J5 stays as the main-bus I2C terminal, now fitted.
  * Layout (gen_pcb.py): outdoor 100 x 100, indoor 100 x 88 (same parts as the first rev B
    layout, packed tighter); CT passives and the mux parts are 0805 hand-solder SMD.
Additions needed to make a board (not on rev A):
  * H1-H4 M3 mounting holes.
  * Names for nets rev A leaves unnamed: VAC_IN, VAC_F, VRAW (named on rev A
    as a note), P_LIQ_IN / P_VAP_IN / P_TSUC_IN, and <call>_OPTO.
"""

POWER_NETS = ('GND', '+5V', '+3V3')

FP_R_QW = 'Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal'
FP_R_HW = 'Resistor_THT:R_Axial_DIN0309_L9.0mm_D3.2mm_P12.70mm_Horizontal'
FP_C_DISC = 'Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm'
FP_C1 = 'Capacitor_THT:CP_Radial_D10.0mm_P5.00mm'
FP_D2 = 'Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal'
FP_D1 = 'Diode_SMD:D_SMB_Handsoldering'
FP_FUSE = 'Fuse:Fuseholder_Clip-5x20mm_Littelfuse_111_Inline_P20.00x5.00mm_D1.05mm_Horizontal'
FP_DIP6 = 'Package_DIP:DIP-6_W7.62mm_Socket'
FP_MH = 'MountingHole:MountingHole_3.2mm_M3_DIN965'
FP_ESP32 = 'hvac_carrier:ESP32_DevKit_30pin_W25.4mm'
FP_ADS = 'hvac_carrier:ADS1115_Module'
FP_BUCK = 'hvac_carrier:LM2596HVS_Module'
FP_LORA = 'hvac_carrier:Wio_SX1262'          # castellated 11.6 x 11 mm module (datasheet fig. 9)
FP_LDO = 'Package_TO_SOT_SMD:SOT-223-3_TabPin2'
FP_C_EL = 'Capacitor_THT:CP_Radial_D5.0mm_P2.00mm'
FP_R_SMD = 'Resistor_SMD:R_0805_2012Metric_Pad1.20x1.40mm_HandSolder'
FP_C_SMD = 'Capacitor_SMD:C_0805_2012Metric_Pad1.18x1.45mm_HandSolder'
FP_TSSOP24 = 'Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm'
FP_JACK35 = 'hvac_carrier:Jack_3.5mm_CUI_SJ1-3523N_Horizontal_EdgeTrim'  # KiCad's, edge silk trimmed
FP_SW = 'Button_Switch_THT:SW_PUSH_6mm'


def fp_term(n):
    # Phoenix MC 1,5/n-G-5.08 horizontal pluggable header (mates with MC 1,5/n-ST-5,08).
    # MSTBA 2,5 was too wide to fit the outdoor board's eight terminals on its edges.
    return (f'Connector_Phoenix_MC_HighVoltage:PhoenixContact_MC_1,5_{n}-G-5.08_'
            f'1x0{n}_P5.08mm_Horizontal')


def fp_jst(n):
    # JST XH top-entry header, 2.50 mm pitch (sensor cables)
    return f'Connector_JST:JST_XH_B{n}B-XH-A_1x0{n}_P2.50mm_Vertical'


def part(ref, lib, value, fp, x, y, nets, **kw):
    d = dict(ref=ref, lib=lib, value=value, fp=fp, x=x, y=y, nets=nets)
    d.update(kw)
    return d


# ESP32 DevKit 30-pin: pins 1-15 are the EN..VIN column, 16-30 the D23..3V3
# column (rev A zone B, top to bottom).
ESP32_LEFT = ['EN', 'VP', 'VN', 'D34', 'D35', 'D32', 'D33', 'D25', 'D26', 'D27',
              'D14', 'D12', 'D13', 'GND', 'VIN']
ESP32_RIGHT = ['D23', 'D22', 'TX0', 'RX0', 'D21', 'D19', 'D18', 'D5', 'TX2', 'RX2',
               'D4', 'D2', 'D15', 'GND', '3V3']


def esp32_nets(**by_name):
    """Map pin names to nets; unlisted pins are NC (the × pins on rev A)."""
    nets = {}
    for i, n in enumerate(ESP32_LEFT + ESP32_RIGHT, start=1):
        nets[str(i)] = by_name.get(n, 'NC')
    return nets


def power_section(y0):
    """Rev A zone A, identical on both boards."""
    return [
        part('J1', 'Connector_Generic:Conn_01x02', '24 VAC', fp_term(2), 30.48, y0,
             {'1': 'VAC_IN', '2': 'GND'}, pinlabels=['R', 'C']),
        part('F1', 'Device:Fuse', '0.5 A fast', FP_FUSE, 50.8, y0,
             {'1': 'VAC_IN', '2': 'VAC_F'}),
        part('D1', 'Device:D_TVS', 'SMBJ48CA', FP_D1, 71.12, y0,
             {'1': 'VAC_F', '2': 'GND'}),
        part('D2', 'Device:D', '1N4007', FP_D2, 101.6, y0,
             {'1': 'VRAW', '2': 'VAC_F'}),  # pin 1 = K, pin 2 = A
        part('C1', 'Device:C_Polarized', '470µF 63V', FP_C1, 124.46, y0,
             {'1': 'VRAW', '2': 'GND'}),
        part('C2', 'Device:C', '0.1µF', FP_C_DISC, 137.16, y0,
             {'1': 'VRAW', '2': 'GND'}),
        part('U2', 'hvac_carrier:LM2596HV_Module', 'LM2596HV', FP_BUCK, 175.26, y0,
             {'1': 'VRAW', '2': 'GND', '3': '+5V', '4': 'GND'}),
    ]


def divider(jref, rtop, rbot, cref, raw, net, title, x, y):
    return [
        part(jref, 'Connector_Generic:Conn_01x03', title, fp_jst(3), x, y,
             {'1': '+5V', '2': raw, '3': 'GND'}, pinlabels=['+5', 'SIG', 'GND']),
        part(rtop, 'Device:R', '10k 1%', FP_R_QW, x + 20.32, y, {'1': raw, '2': net}),
        part(rbot, 'Device:R', '10k 1%', FP_R_QW, x + 33.02, y, {'1': net, '2': 'GND'}),
        part(cref, 'Device:C', '0.1µF', FP_C_DISC, x + 45.72, y, {'1': net, '2': 'GND'}),
    ]


def thermistor(rref, thref, cref, jref, net, title, x, y, th_dnp=False):
    return [
        part(rref, 'Device:R', '10k 0.1%', FP_R_QW, x, y, {'1': '+3V3', '2': net}),
        part(thref, 'Device:Thermistor_NTC', '10k NTC B3950', '', x + 12.7, y,
             {'1': net, '2': 'GND'}, on_board=False, dnp=th_dnp),
        part(cref, 'Device:C', '0.1µF', FP_C_DISC, x + 25.4, y, {'1': net, '2': 'GND'}),
        part(jref, 'Connector_Generic:Conn_01x02', title, fp_jst(2), x + 45.72, y,
             {'1': net, '2': 'GND'}, pinlabels=['TH', 'TH']),
    ]


def opto(rs, u, rp, call, x, y):
    """Series 4.7k 1/2 W, H11AA1, 10k pull-up to +3V3 (rev A mode inputs)."""
    return [
        part(rs, 'Device:R', '4.7k ½W', FP_R_HW, x, y,
             {'1': f'{call}_24V', '2': f'{call}_OPTO'}),
        part(u, 'Isolator:H11AA1', 'H11AA1', FP_DIP6, x + 25.4, y,
             {'1': f'{call}_OPTO', '2': 'GND', '3': 'NC', '4': 'GND',
              '5': f'MODE_{call}', '6': 'NC'}),
        part(rp, 'Device:R', '10k', FP_R_QW, x + 50.8, y,
             {'1': '+3V3', '2': f'MODE_{call}'}),
    ]


def mounting_holes(x, y):
    return [part(f'H{i}', 'Mechanical:MountingHole', 'M3', FP_MH, x + 12.7 * (i - 1), y, {},
                 in_bom=False) for i in range(1, 5)]


LORA_PINS = dict(D5='LORA_NSS', D18='LORA_SCK', D19='LORA_MISO', D23='LORA_MOSI',
                 D26='LORA_DIO1', D27='LORA_NRST', D25='LORA_BUSY', D13='LORA_RFSW', D14='SETUP')


def lora_section(uref, ldo, cin, cout, cdec, sw, x, y):
    """Wio-SX1262 on its own 3.3 V regulator (+3V3_RF), and the SETUP button."""
    return [
        part(ldo, 'Regulator_Linear:AMS1117-3.3', 'AMS1117-3.3', FP_LDO, x, y,
             {'1': 'GND', '2': '+3V3_RF', '3': '+5V'}),
        part(cin, 'Device:C_Polarized', '10µF 16V', FP_C_EL, x + 20.32, y, {'1': '+5V', '2': 'GND'}),
        part(cout, 'Device:C_Polarized', '22µF 10V', FP_C_EL, x + 33.02, y,
             {'1': '+3V3_RF', '2': 'GND'}),
        part(cdec, 'Device:C', '0.1µF', FP_C_DISC, x + 45.72, y, {'1': '+3V3_RF', '2': 'GND'}),
        part(uref, 'hvac_carrier:Wio_SX1262', 'Wio-SX1262', FP_LORA, x + 76.2, y,
             {'1': 'LORA_RFSW', '2': 'LORA_MISO', '3': 'LORA_MOSI', '4': 'LORA_SCK',
              '5': 'LORA_NRST', '6': 'LORA_NSS', '7': 'GND', '8': '+3V3_RF', '9': 'NC',
              '10': 'GND', '11': 'LORA_BUSY', '12': 'LORA_DIO1'}),
        part(sw, 'Switch:SW_Push', 'SETUP', FP_SW, x + 119.38, y, {'1': 'SETUP', '2': 'GND'}),
    ]


def ct_input(jref, rb, rs, cf, n, net, adc, burden, x, y):
    """3.5 mm jack for an SCT-013 clamp: tip = signal, sleeve = CT_BIAS. The burden sits across
    the jack (fit it for current-output CTs, leave it off for the voltage-output ones).
    0805 hand-solder parts (like the indoor mux) so three channels fit beside the jacks."""
    return [
        part(jref, 'Connector_Audio:AudioJack3', f'CT{n} {net}', FP_JACK35, x, y,
             {'S': 'CT_BIAS', 'T': f'CT{n}_IN', 'R': 'NC'}),
        part(rb, 'Device:R', burden, FP_R_SMD, x + 20.32, y, {'1': f'CT{n}_IN', '2': 'CT_BIAS'},
             dnp=burden == 'DNP'),
        part(rs, 'Device:R', '1k', FP_R_SMD, x + 33.02, y, {'1': f'CT{n}_IN', '2': adc}),
        part(cf, 'Device:C', '10nF', FP_C_SMD, x + 45.72, y, {'1': adc, '2': 'GND'}),
    ]


def outdoor():
    p = power_section(38.1)
    p.append(part('U1', 'hvac_carrier:ESP32_DevKit_30', 'ESP32 DevKit', FP_ESP32, 330.2, 68.58,
                  esp32_nets(D34='MODE_Y', D35='MODE_OB', GND='GND', VIN='+5V',
                             D22='SCL', D21='SDA', VP='CT1_ADC', VN='CT2_ADC', D32='CT3_ADC',
                             **LORA_PINS, **{'3V3': '+3V3'})))
    p += [
        part('U3', 'hvac_carrier:ADS1115_Module', 'ADS1115 0x48', FP_ADS, 50.8, 101.6,
             {'1': '+3V3', '2': 'GND', '3': 'SCL', '4': 'SDA', '5': 'GND', '6': 'NC',
              '7': 'P_LIQ', '8': 'P_VAP', '9': 'P_TSUC', '10': 'V5_MON'}),
        part('U4', 'hvac_carrier:ADS1115_Module', 'ADS1115 0x49', FP_ADS, 132.08, 101.6,
             {'1': '+3V3', '2': 'GND', '3': 'SCL', '4': 'SDA', '5': '+3V3', '6': 'NC',
              '7': 'T_SUC', '8': 'T_LIQ', '9': 'T_TSUC', '10': 'T_DIS'}),
        part('J8', 'Connector_Generic:Conn_01x04', 'SHT30 FS400', fp_term(4), 205.74, 101.6,
             {'1': '+3V3', '2': 'GND', '3': 'SDA', '4': 'SCL'},
             pinlabels=['3V3', 'GND', 'SDA', 'SCL']),
    ]
    p += divider('J3', 'R1', 'R2', 'C3', 'P_LIQ_IN', 'P_LIQ', 'XDB307 liquid', 22.86, 147.32)
    p += divider('J4', 'R3', 'R4', 'C4', 'P_VAP_IN', 'P_VAP', 'XDB307 vapor', 96.52, 147.32)
    p += divider('J5', 'R5', 'R6', 'C5', 'P_TSUC_IN', 'P_TSUC', 'XDB307 t.suction',
                 170.18, 147.32)
    p += [
        part('R7', 'Device:R', '10k 1%', FP_R_QW, 256.54, 147.32, {'1': '+5V', '2': 'V5_MON'}),
        part('R8', 'Device:R', '10k 1%', FP_R_QW, 269.24, 147.32, {'1': 'V5_MON', '2': 'GND'}),
        part('C6', 'Device:C', '0.1µF', FP_C_DISC, 281.94, 147.32, {'1': 'V5_MON', '2': 'GND'}),
    ]
    p += thermistor('R9', 'TH1', 'C7', 'J6', 'T_SUC', 'TH1 suction', 30.48, 190.5)
    p += thermistor('R10', 'TH2', 'C8', 'J7', 'T_LIQ', 'TH2 liquid', 111.76, 190.5)
    p += thermistor('R15', 'TH3', 'C9', 'J9', 'T_TSUC', 'TH3 t.suction', 193.04, 190.5,
                    th_dnp=True)
    p += thermistor('R16', 'TH4', 'C10', 'J10', 'T_DIS', 'TH4 discharge', 274.32, 190.5,
                    th_dnp=True)
    # CT inputs (electrical module version 1): bias at 3.3 x 6.8 / 16.8 = 1.34 V, the middle of
    # the ESP32 ADC's linear range at 11 dB. SCT-013-000 (100 A : 50 mA) with 18 R puts a
    # 141 A start peak at 1.27 V; SCT-013-005 / -010 have their own burden (1 V at 5 / 10 A).
    p += [
        part('R17', 'Device:R', '10k 1%', FP_R_SMD, 345.44, 236.22, {'1': '+3V3', '2': 'CT_BIAS'}),
        part('R18', 'Device:R', '6.8k 1%', FP_R_SMD, 358.14, 236.22, {'1': 'CT_BIAS', '2': 'GND'}),
        part('C11', 'Device:C_Polarized', '10µF 16V', FP_C_EL, 370.84, 236.22,
             {'1': 'CT_BIAS', '2': 'GND'}),
    ]
    p += ct_input('J11', 'R19', 'R20', 'C12', 1, 'compressor', 'CT1_ADC', '18 1%', 22.86, 281.94)
    p += ct_input('J12', 'R21', 'R22', 'C13', 2, 'fan', 'CT2_ADC', 'DNP', 96.52, 281.94)
    p += ct_input('J13', 'R23', 'R24', 'C14', 3, 'HERM', 'CT3_ADC', 'DNP', 170.18, 281.94)
    p += lora_section('U7', 'U8', 'C15', 'C16', 'C17', 'SW1', 22.86, 320.04)
    p.append(part('J2', 'Connector_Generic:Conn_01x02', 'Mode in 24 VAC', fp_term(2),
                  30.48, 236.22, {'1': 'Y_24V', '2': 'OB_24V'}, pinlabels=['Y', 'O/B']))
    p += opto('R11', 'U5', 'R12', 'Y', 66.04, 236.22)
    p += opto('R13', 'U6', 'R14', 'OB', 147.32, 236.22)
    p += mounting_holes(274.32, 243.84)
    return p


MUX_CH = [('SHT45 return', 0), ('SHT45 supply', 1), ('SDP810 return', 2),
          ('SDP810 supply', 3), ('SDP810 filter', 4)]
# TCA9548A TSSOP-24 pins: SDn / SCn per channel
MUX_PINS = {0: ('4', '5'), 1: ('6', '7'), 2: ('8', '9'), 3: ('10', '11'), 4: ('13', '14'),
            5: ('15', '16'), 6: ('17', '18'), 7: ('19', '20')}


def mux_section(x, y):
    """TCA9548A at 0x70 (A0-A2 low) and a JST-XH 4-pin jack with 10k pull-ups per channel."""
    nets = {'1': 'GND', '2': 'GND', '21': 'GND', '3': 'MUX_RST', '12': 'GND', '22': 'SCL',
            '23': 'SDA', '24': '+3V3'}
    for ch, (sd, sc) in MUX_PINS.items():
        used = ch < len(MUX_CH)
        nets[sd] = f'SD{ch}' if used else 'NC'
        nets[sc] = f'SC{ch}' if used else 'NC'
    p = [part('U7', 'Interface_Expansion:TCA9548APWR', 'TCA9548A', FP_TSSOP24, x, y, nets),
         part('R10', 'Device:R', '10k', FP_R_SMD, x + 25.4, y, {'1': '+3V3', '2': 'MUX_RST'}),
         part('C3', 'Device:C', '0.1µF', FP_C_SMD, x + 33.02, y, {'1': '+3V3', '2': 'GND'})]
    for i, (title, ch) in enumerate(MUX_CH):
        jx = x + 50.8 + 30.48 * i
        p += [
            part(f'J{6 + i}', 'Connector_Generic:Conn_01x04', title, fp_jst(4), jx, y + 15.24,
                 {'1': '+3V3', '2': 'GND', '3': f'SD{ch}', '4': f'SC{ch}'},
                 pinlabels=['3V3', 'GND', 'SDA', 'SCL']),
            part(f'R{11 + 2 * i}', 'Device:R', '10k', FP_R_SMD, jx, y,
                 {'1': '+3V3', '2': f'SD{ch}'}),
            part(f'R{12 + 2 * i}', 'Device:R', '10k', FP_R_SMD, jx + 10.16, y,
                 {'1': '+3V3', '2': f'SC{ch}'}),
        ]
    return p


def indoor():
    p = power_section(38.1)
    p.append(part('U1', 'hvac_carrier:ESP32_DevKit_30', 'ESP32 DevKit', FP_ESP32, 330.2, 68.58,
                  esp32_nets(VP='MODE_G', VN='MODE_OB', D34='MODE_Y', D35='MODE_W',
                             GND='GND', VIN='+5V', D22='SCL', D21='SDA', D4='ONEWIRE',
                             **LORA_PINS, **{'3V3': '+3V3'})))
    p += [
        part('R1', 'Device:R', '4.7k', FP_R_QW, 30.48, 106.68, {'1': '+3V3', '2': 'ONEWIRE'}),
        part('J3', 'Connector_Generic:Conn_01x03', 'DS18B20 supply air', fp_jst(3),
             71.12, 106.68, {'1': '+3V3', '2': 'ONEWIRE', '3': 'GND'},
             pinlabels=['3V3', 'DQ', 'GND']),
        part('J4', 'Connector_Generic:Conn_01x03', 'DS18B20 return air', fp_jst(3),
             121.92, 106.68, {'1': '+3V3', '2': 'ONEWIRE', '3': 'GND'},
             pinlabels=['3V3', 'DQ', 'GND']),
        part('J5', 'Connector_Generic:Conn_01x04', 'I2C expansion', fp_term(4),
             182.88, 106.68, {'1': '+3V3', '2': 'GND', '3': 'SDA', '4': 'SCL'},
             pinlabels=['3V3', 'GND', 'SDA', 'SCL']),
        part('J2', 'Connector_Generic:Conn_01x04', 'Mode in 24 VAC', fp_term(4),
             30.48, 160.02, {'1': 'Y_24V', '2': 'W_24V', '3': 'G_24V', '4': 'OB_24V'},
             pinlabels=['Y', 'W', 'G', 'O/B']),
    ]
    p += opto('R2', 'U3', 'R3', 'Y', 66.04, 160.02)
    p += opto('R4', 'U4', 'R5', 'W', 147.32, 160.02)
    p += opto('R6', 'U5', 'R7', 'G', 66.04, 205.74)
    p += opto('R8', 'U6', 'R9', 'OB', 147.32, 205.74)
    p += mux_section(22.86, 254.0)
    p += lora_section('U8', 'U9', 'C4', 'C5', 'C6', 'SW1', 22.86, 312.42)
    p += mounting_holes(274.32, 205.74)
    return p


# ---------------------------------------------------------------- thermostat carrier
# Display thermostat (thermostat/README.md), 2026-10-29. The carrier sits behind a Waveshare
# ESP32-S3-Touch-LCD-4.3. That board's RGB panel uses nearly every GPIO: only the I2C header
# (GPIO8/9), the sensor header (GPIO6) and the UART pins (43/44, shared with its USB-UART) are
# free, so everything on the carrier hangs off I2C:
#   * MCP23017 (0x20): six PhotoMOS outputs Y1 Y2 W1 W2 G O/B, six H11AA1 read-backs, the
#     TPS3823 watchdog input. The TPS3823 resets the MCP23017 if the firmware stops toggling WDI,
#     which turns every output off (the display board's EN isn't brought out).
#   * SC18IS602B I2C-to-SPI bridge (0x28) for the Wio-SX1262: SS0 = NSS, GPIO1 = BUSY (input),
#     GPIO2 = NRST, GPIO3 = RF_SW. DIO1 goes to the display's GPIO6 (sensor header) for the
#     receive interrupt; without that wire the firmware polls the radio's IRQ flags.
#   * SHT45 room sensor on a Qwiic cable (thermally isolated tab, away from the display heat).
# Terminals as on a thermostat sub-base: Rh Rc C, then G Y1 Y2 W1 W2 O/B. JP1 joins Rc and Rh
# (one transformer). Rc feeds Y1 Y2 G O/B and Rh feeds W1 W2, each through a 1.25 A PTC. The
# board runs from Rc-C (C = GND, half-wave as on the nodes) or, as a display-only node, from
# 5 V on the USB-C input; either way the display gets 5 V from the USB-A socket.

CALLS = [('G', 'RC'), ('Y1', 'RC'), ('Y2', 'RC'), ('W1', 'RH'), ('W2', 'RH'), ('OB', 'RC')]
# MCP23017 SOIC-28: GPA0-5 = pins 21-26 drive the outputs, GPB0-5 = pins 1-6 read them back
# (firmware board.h: OUT_Y 0, OUT_Y2 1, OUT_W 2, OUT_W2 3, OUT_G 4, OUT_OB 5; SENSE_Y 0,
# SENSE_Y2 1, SENSE_W 2, SENSE_G 3, SENSE_OB 4, SENSE_W2 5). GPA7/GPB7 are output-only parts.
MCP_OUT = {'Y1': '21', 'Y2': '22', 'W1': '23', 'W2': '24', 'G': '25', 'OB': '26'}
MCP_SENSE = {'Y1': '1', 'Y2': '2', 'W1': '3', 'G': '4', 'OB': '5', 'W2': '6'}
FP_C_EL63 = 'Capacitor_THT:CP_Radial_D6.3mm_P2.50mm'
FP_C1_BIG = 'Capacitor_THT:CP_Radial_D12.5mm_P5.00mm'
FP_PTC = 'Fuse:Fuse_BelFuse_0ZRE0125FF_L21.2mm_W5.3mm'
FP_SMA = 'Diode_SMD:D_SMA_Handsoldering'
FP_SOIC28 = 'Package_SO:SOIC-28W_7.5x17.9mm_P1.27mm'
FP_TSSOP16 = 'Package_SO:TSSOP-16_4.4x5mm_P0.65mm'
FP_SOT235 = 'Package_TO_SOT_SMD:SOT-23-5_HandSoldering'
FP_USB_A = 'Connector_USB:USB_A_Molex_67643_Horizontal'
FP_USB_C = 'Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal'
FP_QWIIC = 'Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal'


def fp_hdr(n):
    return f'Connector_PinHeader_2.54mm:PinHeader_1x0{n}_P2.54mm_Vertical'


def tstat_channel(i, call, src, x, y):
    """One output: MCP23017 pin -> 330 R -> AQV252G LED (10k pull-down keeps it off while the
    expander is in reset); the PhotoMOS switches Rc/Rh to the terminal, a TVS across it; an
    H11AA1 reads the terminal back against C (4.7k 1/2 W in, 10k pull-up out, as on the nodes)."""
    n = i + 1
    return [
        part(f'R{3 + i}', 'Device:R', '330', FP_R_SMD, x, y, {'1': f'DRV_{call}', '2': f'LED_{call}'}),
        part(f'R{9 + i}', 'Device:R', '10k', FP_R_SMD, x, y + 10.16, {'1': f'DRV_{call}', '2': 'GND'}),
        part(f'K{n}', 'hvac_carrier:AQV252G', 'AQV252G', FP_DIP6, x + 25.4, y,
             {'1': f'LED_{call}', '2': 'GND', '3': 'NC', '4': f'{src}L_24V', '5': 'NC',
              '6': f'{call}_24V'}),
        part(f'D{5 + i}', 'Device:D_TVS', 'SMBJ40CA', FP_D1, x + 50.8, y,
             {'1': f'{src}L_24V', '2': f'{call}_24V'}),
        part(f'R{15 + i}', 'Device:R', '4.7k ½W', FP_R_HW, x + 66.04, y,
             {'1': f'{call}_24V', '2': f'{call}_OPTO'}),
        part(f'U{7 + i}', 'Isolator:H11AA1', 'H11AA1', FP_DIP6, x + 91.44, y,
             {'1': f'{call}_OPTO', '2': 'GND', '3': 'NC', '4': 'GND', '5': f'SENSE_{call}',
              '6': 'NC'}),
        part(f'R{21 + i}', 'Device:R', '10k', FP_R_SMD, x + 116.84, y,
             {'1': '+3V3', '2': f'SENSE_{call}'}),
    ]


def thermostat():
    p = [
        part('J1', 'Connector_Generic:Conn_01x03', '24 VAC', fp_term(3), 30.48, 38.1,
             {'1': 'RH_24V', '2': 'RC_24V', '3': 'GND'}, pinlabels=['Rh', 'Rc', 'C']),
        part('JP1', 'Jumper:Jumper_2_Open', 'Rc=Rh', fp_hdr(2), 30.48, 55.88,
             {'1': 'RC_24V', '2': 'RH_24V'}),
        part('F1', 'Device:Fuse', '0.5 A fast', FP_FUSE, 50.8, 38.1, {'1': 'RC_24V', '2': 'VAC_F'}),
        part('D1', 'Device:D_TVS', 'SMBJ48CA', FP_D1, 71.12, 38.1, {'1': 'VAC_F', '2': 'GND'}),
        part('D2', 'Device:D', '1N4007', FP_D2, 101.6, 38.1, {'1': 'VRAW', '2': 'VAC_F'}),
        part('C1', 'Device:C_Polarized', '1000µF 63V', FP_C1_BIG, 124.46, 38.1,
             {'1': 'VRAW', '2': 'GND'}),
        part('C2', 'Device:C', '0.1µF', FP_C_DISC, 137.16, 38.1, {'1': 'VRAW', '2': 'GND'}),
        part('U1', 'hvac_carrier:LM2596HV_Module', 'LM2596HV', FP_BUCK, 175.26, 38.1,
             {'1': 'VRAW', '2': 'GND', '3': 'V5_BUCK', '4': 'GND'}),
        part('D3', 'Device:D_Schottky', 'SS34', FP_SMA, 210.82, 38.1, {'1': '+5V', '2': 'V5_BUCK'}),
        # display-only node: 5 V from a USB-C charger (5.1k CC pull-downs ask for 5 V)
        part('J3', 'Connector:USB_C_Receptacle_PowerOnly_6P', 'USB-C 5 V in', FP_USB_C, 30.48, 81.28,
             {'A9': 'VBUS_IN', 'B9': 'VBUS_IN', 'A12': 'GND', 'B12': 'GND', 'A5': 'CC1',
              'B5': 'CC2', 'SH': 'GND'}),
        part('R1', 'Device:R', '5.1k', FP_R_SMD, 60.96, 81.28, {'1': 'CC1', '2': 'GND'}),
        part('R2', 'Device:R', '5.1k', FP_R_SMD, 73.66, 81.28, {'1': 'CC2', '2': 'GND'}),
        part('D4', 'Device:D_Schottky', 'SS34', FP_SMA, 93.98, 81.28, {'1': '+5V', '2': 'VBUS_IN'}),
        part('C3', 'Device:C_Polarized', '220µF 16V', FP_C_EL63, 114.3, 81.28, {'1': '+5V', '2': 'GND'}),
        part('J4', 'Connector:USB_A', 'USB-A 5 V to display', FP_USB_A, 139.7, 81.28,
             {'1': '+5V', '2': 'NC', '3': 'NC', '4': 'GND', 'SH': 'GND'}),
        part('U2', 'Regulator_Linear:AMS1117-3.3', 'AMS1117-3.3', FP_LDO, 175.26, 81.28,
             {'1': 'GND', '2': '+3V3', '3': '+5V'}),
        part('C4', 'Device:C_Polarized', '10µF 16V', FP_C_EL, 195.58, 81.28, {'1': '+5V', '2': 'GND'}),
        part('C5', 'Device:C_Polarized', '22µF 10V', FP_C_EL, 208.28, 81.28, {'1': '+3V3', '2': 'GND'}),
        part('C6', 'Device:C', '0.1µF', FP_C_SMD, 220.98, 81.28, {'1': '+3V3', '2': 'GND'}),
        # Rc / Rh to the output switches
        part('F2', 'Device:Polyfuse', '0ZRE0125FF', FP_PTC, 30.48, 116.84, {'1': 'RC_24V', '2': 'RCL_24V'}),
        part('F3', 'Device:Polyfuse', '0ZRE0125FF', FP_PTC, 50.8, 116.84, {'1': 'RH_24V', '2': 'RHL_24V'}),
        part('J2', 'Connector_Generic:Conn_01x06', 'Calls 24 VAC', fp_term(6), 81.28, 116.84,
             {str(i + 1): f'{c}_24V' for i, (c, _) in enumerate(CALLS)},
             pinlabels=['G', 'Y1', 'Y2', 'W1', 'W2', 'O/B']),
    ]
    for i, (call, src) in enumerate(CALLS):
        p += tstat_channel(i, call, src, 22.86, 152.4 + 22.86 * i)
    mcp = {'9': '+3V3', '10': 'GND', '11': 'NC', '12': 'SCL', '13': 'SDA', '14': 'NC',
           '15': 'GND', '16': 'GND', '17': 'GND', '18': 'WD_RST', '19': 'NC', '20': 'NC',
           '27': 'WDI', '28': 'NC', '7': 'NC', '8': 'NC'}
    mcp.update({pin: f'DRV_{c}' for c, pin in MCP_OUT.items()})
    mcp.update({pin: f'SENSE_{c}' for c, pin in MCP_SENSE.items()})
    p += [
        part('U3', 'Interface_Expansion:MCP23017x-x-SO', 'MCP23017-E/SO', FP_SOIC28, 330.2, 76.2, mcp),
        part('C7', 'Device:C', '0.1µF', FP_C_SMD, 365.76, 60.96, {'1': '+3V3', '2': 'GND'}),
        part('U4', 'Power_Supervisor:TPS3823-xxDBV', 'TPS3823-33', FP_SOT235, 330.2, 121.92,
             {'1': 'WD_RST', '2': 'GND', '3': '+3V3', '4': 'WDI', '5': '+3V3'}),
        part('C8', 'Device:C', '0.1µF', FP_C_SMD, 365.76, 121.92, {'1': '+3V3', '2': 'GND'}),
        # LoRa: SC18IS602B bridge, Wio-SX1262 on the board's 3.3 V
        part('U5', 'hvac_carrier:SC18IS602B', 'SC18IS602B', FP_TSSOP16, 330.2, 167.64,
             {'1': 'LORA_NSS', '2': 'LORA_BUSY', '3': 'BR_RST', '4': 'GND', '5': 'LORA_MISO',
              '6': 'LORA_MOSI', '7': 'SDA', '8': 'SCL', '9': 'NC', '10': 'LORA_NRST',
              '11': 'LORA_SCK', '12': '+3V3', '13': 'LORA_RFSW', '14': 'GND', '15': 'GND',
              '16': 'GND'}),
        part('R27', 'Device:R', '10k', FP_R_SMD, 365.76, 154.94, {'1': '+3V3', '2': 'BR_RST'}),
        part('C9', 'Device:C', '0.1µF', FP_C_SMD, 365.76, 167.64, {'1': '+3V3', '2': 'GND'}),
        part('U6', 'hvac_carrier:Wio_SX1262', 'Wio-SX1262', FP_LORA, 330.2, 213.36,
             {'1': 'LORA_RFSW', '2': 'LORA_MISO', '3': 'LORA_MOSI', '4': 'LORA_SCK',
              '5': 'LORA_NRST', '6': 'LORA_NSS', '7': 'GND', '8': '+3V3', '9': 'NC',
              '10': 'GND', '11': 'LORA_BUSY', '12': 'LORA_DIO1'}),
        part('C10', 'Device:C', '0.1µF', FP_C_SMD, 365.76, 213.36, {'1': '+3V3', '2': 'GND'}),
        # to the display board: its I2C cable (its 3V3 wire parks on an unconnected pin) and
        # the sensor-header cable for DIO1 -> GPIO6
        part('J5', 'Connector_Generic:Conn_01x04', 'Display I2C', fp_hdr(4), 419.1, 76.2,
             {'1': 'NC', '2': 'GND', '3': 'SDA', '4': 'SCL'}, pinlabels=['3V3', 'GND', 'SDA', 'SCL']),
        part('J6', 'Connector_Generic:Conn_01x03', 'Display IO6', fp_hdr(3), 419.1, 101.6,
             {'1': 'NC', '2': 'GND', '3': 'LORA_DIO1'}, pinlabels=['3V3', 'GND', 'IO6']),
        part('J7', 'Connector_Generic_MountingPin:Conn_01x04_MountingPin', 'Room SHT45 (Qwiic)',
             FP_QWIIC, 419.1, 127.0, {'1': 'GND', '2': '+3V3', '3': 'SDA', '4': 'SCL', 'MP': 'GND'},
             pinlabels=['GND', '3V3', 'SDA', 'SCL']),
        # the display board already pulls SDA/SCL up; fit these only if the bus misbehaves
        part('R28', 'Device:R', '4.7k', FP_R_SMD, 419.1, 154.94, {'1': '+3V3', '2': 'SDA'}, dnp=True),
        part('R29', 'Device:R', '4.7k', FP_R_SMD, 431.8, 154.94, {'1': '+3V3', '2': 'SCL'}, dnp=True),
    ]
    p += mounting_holes(419.1, 190.5)
    return p


BOARDS = {
    'outdoor-carrier': dict(parts=outdoor, title='HVAC Monitor outdoor carrier',
                            size=(100, 100)),
    'indoor-carrier': dict(parts=indoor, title='HVAC Monitor indoor carrier',
                           size=(100, 88)),
    'thermostat-carrier': dict(parts=thermostat, title='Fullscope thermostat carrier',
                               size=(100, 100), rev='A', date='2026-10-29', driven_nets=('+3V3',)),
}
