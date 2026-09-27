"""Single source of truth for both carrier boards, transcribed from
docs/hvac-schematics.html (rev A, 2026-09-24).

Each part: ref, lib_id, value, footprint, schematic position (mm), and a
pin-number -> net map. 'NC' marks a no-connect. Nets named GND, +5V, +3V3 are
drawn as power symbols; every other net is a local label, so labels with the
same name are connected, exactly as on the rev A drawings.

Deviations from rev A (agreed with the user):
  * Outdoor: J9 + R15 + C9 add a third thermistor channel on T_TSUC
    (same circuit as T_SUC). TH3 is the off-board sensor, marked DNP.
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
        part(rbot, 'Device:R', '20k 1%', FP_R_QW, x + 33.02, y, {'1': net, '2': 'GND'}),
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


def outdoor():
    p = power_section(38.1)
    p.append(part('U1', 'hvac_carrier:ESP32_DevKit_30', 'ESP32 DevKit', FP_ESP32, 330.2, 68.58,
                  esp32_nets(D34='MODE_Y', D35='MODE_OB', GND='GND', VIN='+5V',
                             D22='SCL', D21='SDA', **{'3V3': '+3V3'})))
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
        part('R8', 'Device:R', '15k 1%', FP_R_QW, 269.24, 147.32, {'1': 'V5_MON', '2': 'GND'}),
        part('C6', 'Device:C', '0.1µF', FP_C_DISC, 281.94, 147.32, {'1': 'V5_MON', '2': 'GND'}),
    ]
    p += thermistor('R9', 'TH1', 'C7', 'J6', 'T_SUC', 'TH1 suction', 30.48, 190.5)
    p += thermistor('R10', 'TH2', 'C8', 'J7', 'T_LIQ', 'TH2 liquid', 111.76, 190.5)
    p += thermistor('R15', 'TH3', 'C9', 'J9', 'T_TSUC', 'TH3 t.suction', 193.04, 190.5,
                    th_dnp=True)
    p.append(part('J2', 'Connector_Generic:Conn_01x02', 'Mode in 24 VAC', fp_term(2),
                  30.48, 236.22, {'1': 'Y_24V', '2': 'OB_24V'}, pinlabels=['Y', 'O/B']))
    p += opto('R11', 'U5', 'R12', 'Y', 66.04, 236.22)
    p += opto('R13', 'U6', 'R14', 'OB', 147.32, 236.22)
    p += mounting_holes(274.32, 243.84)
    return p


def indoor():
    p = power_section(38.1)
    p.append(part('U1', 'hvac_carrier:ESP32_DevKit_30', 'ESP32 DevKit', FP_ESP32, 330.2, 68.58,
                  esp32_nets(VP='MODE_G', VN='MODE_OB', D34='MODE_Y', D35='MODE_W',
                             GND='GND', VIN='+5V', D22='SCL', D21='SDA', D4='ONEWIRE',
                             **{'3V3': '+3V3'})))
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
             pinlabels=['3V3', 'GND', 'SDA', 'SCL'], dnp=True),
        part('J2', 'Connector_Generic:Conn_01x04', 'Mode in 24 VAC', fp_term(4),
             30.48, 160.02, {'1': 'Y_24V', '2': 'W_24V', '3': 'G_24V', '4': 'OB_24V'},
             pinlabels=['Y', 'W', 'G', 'O/B']),
    ]
    p += opto('R2', 'U3', 'R3', 'Y', 66.04, 160.02)
    p += opto('R4', 'U4', 'R5', 'W', 147.32, 160.02)
    p += opto('R6', 'U5', 'R7', 'G', 66.04, 205.74)
    p += opto('R8', 'U6', 'R9', 'OB', 147.32, 205.74)
    p += mounting_holes(274.32, 205.74)
    return p


BOARDS = {
    'outdoor-carrier': dict(parts=outdoor, title='HVAC Monitor outdoor carrier',
                            size=(100, 100)),
    'indoor-carrier': dict(parts=indoor, title='HVAC Monitor indoor carrier',
                           size=(100, 80)),
}
