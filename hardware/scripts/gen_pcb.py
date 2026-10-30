"""Build each carrier board (outline, placed footprints with nets from the
schematic netlist, net classes, keep-outs, ground zones, silkscreen).
Run with KiCad's Python:

    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" gen_pcb.py

Board coordinates below are mm from the board's top-left corner; (x, y, rot)
is the footprint origin (pad 1 for most parts, centre for D1/U2/H*).
Rotation is KiCad's (counter-clockwise, degrees).
"""
import json
import os
import sys

import pcbnew
from pcbnew import FromMM, VECTOR2I

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sexp import find, first, parse  # noqa: E402
import design  # noqa: E402

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KFP = os.path.join(os.environ.get('KICAD_SHARE', r'C:\Program Files\KiCad\10.0\share\kicad'), 'footprints')
LOCAL = {'hvac_carrier': os.path.join(HW, 'lib', 'hvac_carrier.pretty')}
BX, BY = 50.0, 50.0  # board top-left on the KiCad page


def P(x, y):
    return VECTOR2I(FromMM(BX + x), FromMM(BY + y))


# ------------------------------------------------------------------ placement

def shift(d, dx=0.0, dy=0.0):
    return {k: (x + dx, y + dy, r) for k, (x, y, r) in d.items()}


# Top band, identical on both boards (100 mm wide): ESP32 (antenna off the left edge), the
# LoRa radio at the top edge with its u.FL toward the top wall (SMA bulkhead above it), its
# caps beside it, and the rev A power corner. The radio's regulator, input cap and the SETUP
# button sit right of C1, where rev A had only the "Set U2" note.
TOP = {
    'U1': (2.5, 29.85, 0),     # ESP32 pin 1 (EN); D23..3V3 row at y = 4.45
    'J1': (90.0, 8.54, 180),   # 24 VAC, plug faces the top edge
    'F1': (80.0, 15.5, 180),   # clip pads 1 at x 80/75, pads 2 at x 65/60
    'D2': (70.5, 22.5, 180),   # K at x 70.5, A at x 60.34
    'D1': (64.5, 28.0, 0),
    'C1': (76.0, 28.0, 0),     # + at x 76
    'C2': (60.0, 32.2, 0),     # right at U2 IN+
    'U2': (77.5, 45.0, 0),     # module centre
    'H4': (96.5, 3.5, 0),
    'LORA': (62.5, 6.3, 90),       # Wio-SX1262 centre; pins 1-8 on the right, u.FL at the top
    'LORA_C': (72.0, 2.4, 0),      # 0.1 µF at the module's VCC pin
    'LORA_COUT': (72.1, 8.2, 0),   # 22 µF on +3V3_RF
    'SW1': (91.46, 14.0, 0),       # SETUP, centre (94.71, 16.25), clear of J1's labels
    'LORA_LDO': (89.5, 24.0, 0),   # AMS1117 centre
    'LORA_CIN': (86.5, 31.0, 0),   # 10 µF on its +5V input
}

# ref names of the LoRa parts on each board (design.lora_section arguments)
LORA_REFS = {'outdoor-carrier': dict(LORA='U7', LORA_LDO='U8', LORA_CIN='C15', LORA_COUT='C16',
                                     LORA_C='C17'),
             'indoor-carrier': dict(LORA='U8', LORA_LDO='U9', LORA_CIN='C4', LORA_COUT='C5',
                                    LORA_C='C6')}


def top(name):
    m = LORA_REFS[name]
    return {m.get(k, k): v for k, v in TOP.items()}


def mux_jacks():
    """Indoor: J6-J10 along the bottom edge, two 0805 pull-ups above each (J10's sit higher,
    clear of J5)."""
    d = {}
    for i in range(5):
        x = 32.3 + 14.0 * i
        d[f'J{6 + i}'] = (x, 83.5, 0)
        d[f'R{11 + 2 * i}'] = (x + 1.6, 70.6, 0)   # SDn pull-up
        d[f'R{12 + 2 * i}'] = (x + 6.4, 70.6, 0)   # SCn pull-up
    d['R19'], d['R20'] = (80.5, 62.5, 0), (80.5, 66.0, 0)
    return d


PLACE = {
    'outdoor-carrier': dict(top('outdoor-carrier'), **{
        'H1': (3.5, 35.5, 0), 'H2': (3.5, 96.5, 0), 'H3': (96.5, 96.5, 0),
        # CT inputs: 3.5 mm jacks on the left edge (sockets face out), 0805 parts beside each
        # origin x 4.5 puts the footprint's own edge line (with the nose notch) on x = 0
        'J11': (4.5, 46.0, 270), 'J12': (4.5, 60.0, 270), 'J13': (4.5, 74.0, 270),
        'R19': (14.5, 42.0, 0), 'R20': (14.5, 46.0, 0), 'C12': (14.5, 50.0, 0),
        'R21': (14.5, 56.0, 0), 'R22': (14.5, 60.0, 0), 'C13': (14.5, 64.0, 0),
        'R23': (14.5, 70.0, 0), 'R24': (14.5, 74.0, 0), 'C14': (14.5, 78.0, 0),
        'R17': (14.5, 83.0, 0), 'R18': (14.5, 86.0, 0), 'C11': (4.5, 85.5, 0),   # CT_BIAS
        'U3': (21.5, 41.5, 0), 'U4': (21.5, 62.5, 0),
        # filter caps at the ADS1115 inputs (rev A general note 4)
        'C3': (32.0, 36.5, 0), 'C4': (37.6, 36.5, 0), 'C5': (43.2, 36.5, 0), 'C6': (48.8, 36.5, 0),
        'C7': (23.5, 58.95, 0), 'C8': (29.1, 58.95, 0), 'C9': (34.7, 58.95, 0),
        'C10': (40.3, 58.95, 0),
        # divider columns (pad 1 on top), under U2 and right of U3/U4
        'R1': (49.5, 58.0, 270), 'R2': (49.5, 70.5, 270), 'R3': (53.0, 58.0, 270),
        'R4': (53.0, 70.5, 270), 'R5': (56.5, 58.0, 270), 'R6': (56.5, 70.5, 270),
        'R7': (60.0, 58.0, 270), 'R8': (60.0, 70.5, 270),
        # thermistor reference resistors, under U4
        'R9': (21.1, 81.0, 0), 'R10': (35.1, 81.0, 0), 'R15': (21.1, 85.0, 0),
        'R16': (35.1, 85.0, 0),
        # JST XH sensor headers along the bottom edge (body 0.5 mm in from the edge)
        'J3': (11.0, 95.5, 0), 'J4': (25.0, 95.5, 0), 'J5': (39.0, 95.5, 0),
        'J6': (53.0, 95.5, 0), 'J7': (64.5, 95.5, 0), 'J9': (76.0, 95.5, 0),
        'J10': (87.5, 95.5, 0),
        # mode inputs: AC side toward J2, transistor side toward the ESP32 (rev A positions)
        'U5': (75.0, 66.0, 180), 'U6': (75.0, 79.0, 180),
        'R11': (84.2, 69.6, 180), 'R13': (84.2, 82.6, 180),
        'R12': (63.8, 58.0, 270), 'R14': (63.8, 71.5, 270),
        # right edge, plugs face out
        'J2': (91.46, 64.0, 90), 'J8': (91.46, 85.72, 90),
    }),
    'indoor-carrier': dict(top('indoor-carrier'), **mux_jacks(), **{
        'H1': (3.5, 35.5, 0), 'H2': (3.5, 84.5, 0), 'H3': (60.5, 60.5, 0),   # H3 under U2
        # DS18B20 jacks on the left edge, pin 1 at the top
        'J3': (4.5, 44.0, 270), 'J4': (4.5, 60.0, 270),
        'J2': (10.5, 79.46, 0),            # mode inputs, bottom edge
        'J5': (91.46, 74.0, 90),           # main-bus I2C terminal, right edge under U2
        'U7': (72.0, 62.0, 0), 'R10': (66.0, 57.2, 0), 'C3': (78.8, 58.0, 0),   # TCA9548A
        'R9': (51.3, 55.4, 270),           # U6 pull-up, below U6 (clear of U2)
        'R1': (55.3, 56.9, 270),           # 1-Wire pull-up
    }, **shift({
        'U3': (10.5, 50.0, 90), 'U4': (22.1, 50.0, 90), 'U5': (33.7, 50.0, 90), 'U6': (45.3, 50.0, 90),
        'R2': (10.5, 66.0, 90), 'R4': (22.1, 66.0, 90), 'R6': (33.7, 66.0, 90), 'R8': (45.3, 66.0, 90),
        'R3': (18.85, 40.5, 270), 'R5': (30.45, 40.5, 270), 'R7': (42.05, 40.5, 270),
    }, dx=2.0)),
}



def tstat_outputs():
    """Thermostat: one 10.6 mm column per output, G Y1 Y2 W1 W2 O/B left to right: pull-down and
    330 R, PhotoMOS (LED pins up), its TVS beside the 4.7k 2010, then the H11AA1 read-back and
    its pull-up."""
    d = {}
    for i in range(6):
        x = 35.3 + 10.6 * i
        d[f'R{9 + i}'] = (x - 2.2, 19.3, 90)      # DRV pull-down
        d[f'R{3 + i}'] = (x + 2.2, 19.3, 90)      # LED 330 R
        d[f'K{1 + i}'] = (x, 28.4, 270)           # AQV252GS
        d[f'D{5 + i}'] = (x - 2.2, 38.4, 90)      # SMBJ40CA across the switch
        d[f'R{15 + i}'] = (x + 2.8, 38.4, 90)     # 4.7k to the opto
        d[f'U{7 + i}'] = (x - 0.6, 48.4, 270)     # H11AA1SM
        d[f'R{21 + i}'] = (x + 4.5, 49.4, 90)     # SENSE pull-up
    return d


# Thermostat carrier: the display's outline (106 x 68), its standoffs at the corners (98 x 60),
# the notch on the right edge under its side sockets. Seen from the wall side (parts side), so
# the display's USB-C ports are on the left edge and its HY2.0 sockets on the right.
TSTAT_NOTCH = (95.0, 11.4, 54.2)   # x from, y from, y to
PLACE['thermostat-carrier'] = dict(tstat_outputs(), **{
    'H1': (4.0, 4.0, 0), 'H2': (102.0, 4.0, 0), 'H3': (4.0, 64.0, 0), 'H4': (102.0, 64.0, 0),
    # 3.3 V regulator and the expander along the top
    'U2': (14.0, 5.5, 0), 'C4': (8.0, 11.5, 0), 'C5': (20.5, 3.5, 90), 'C6': (20.5, 7.5, 90),
    'U3': (32.0, 7.2, 270), 'C7': (21.0, 12.0, 90),
    'U4': (45.0, 3.5, 0), 'C8': (45.0, 7.5, 0),
    # LoRa: bridge, module (u.FL on its right end), DIO1 socket to the display
    'U5': (53.5, 7.5, 0), 'R27': (48.4, 7.0, 90), 'C9': (55.5, 13.0, 0),
    'U6': (70.0, 6.4, 270), 'C10': (60.5, 14.0, 90), 'J6': (86.5, 5.5, 0),
    # buck, top left
    'U1': (14.0, 20.0, 0), 'C11': (14.0, 15.0, 0), 'C2': (7.0, 19.5, 90),
    'L1': (23.5, 20.5, 0), 'D11': (23.5, 28.0, 0), 'C14': (23.5, 31.8, 0), 'C15': (23.5, 34.6, 0),
    'R30': (9.0, 25.0, 90), 'R33': (12.5, 25.0, 90), 'C13': (15.0, 25.0, 90), 'R31': (17.5, 25.0, 90),
    'C12': (12.5, 29.0, 90), 'R32': (17.5, 29.0, 90), 'D3': (8.5, 33.5, 0),
    # USB-C out to the display and USB-C in, left edge (mouths out)
    'J4': (3.5, 41.5, 270), 'R34': (10.0, 39.0, 0), 'R35': (10.0, 42.0, 0), 'C3': (14.5, 41.5, 90),
    'J3': (3.5, 53.5, 270), 'R1': (10.0, 51.0, 0), 'R2': (10.0, 54.0, 0), 'D4': (15.5, 54.0, 90),
    # 24 VAC in: bulk cap, rectifier, fuse, TVS
    'C1': (23.5, 43.0, 90), 'D2': (24.0, 51.9, 0), 'F1': (20.8, 56.5, 0), 'D1': (16.5, 61.5, 90),
    # terminals along the bottom (plugs face the wall), Rc/Rh PTCs between them
    'J1': (26.5, 63.6, 0), 'JP1': (25.0, 55.8, 90), 'F2': (42.0, 58.0, 0), 'F3': (42.0, 64.6, 0),
    'J2': (51.5, 63.6, 0),
    # room sensor and the display's I2C lead
    'J7': (78.0, 64.6, 0), 'J5': (91.0, 59.5, 0), 'R28': (74.0, 59.5, 90), 'R29': (76.0, 59.5, 90),
})

TEXT = {
    'outdoor-carrier': [('HVAC Monitor rev B', 56.0, 84.2, 1.0), ('outdoor carrier', 56.0, 86.0, 1.0),
                        ('CT1 comp', 18.15, 48.0, 0.9, 90), ('CT2 fan', 18.15, 62.0, 0.9, 90),
                        ('CT3 HERM', 18.15, 76.0, 0.9, 90)],
    'indoor-carrier': [('HVAC Monitor rev B', 21.0, 69.3, 1.1), ('indoor carrier', 21.0, 71.3, 1.1)],
    'thermostat-carrier': [('Fullscope thermostat carrier rev A', 44.0, 15.4, 0.9),
                           ('5 V out', 10.0, 45.3, 0.8), ('5 V in', 10.0, 56.9, 0.8),
                           ('Rc=Rh', 31.8, 55.8, 0.8),
                           ('3V3', 84.5, 11.0, 0.8, 90), ('GND', 86.5, 11.0, 0.8, 90),
                           ('IO6', 88.5, 11.0, 0.8, 90), ('display IO6', 86.5, 14.6, 0.8),
                           ('3V3', 88.0, 65.0, 0.8, 90), ('GND', 90.0, 65.0, 0.8, 90),
                           ('SDA', 92.0, 65.0, 0.8, 90), ('SCL', 94.0, 65.0, 0.8, 90),
                           ('display I2C', 91.0, 67.3, 0.8), ('room', 80.6, 58.6, 0.8),
                           ('SHT45', 80.6, 60.0, 0.8), ('trim THT leads to 1.5 mm', 60.0, 56.3, 0.8)],
}
COMMON_TEXT = [('Set U2 to', 77.5, 49.5, 1.1), ('5.00 V first', 77.5, 51.3, 1.1),
               ('USB', 49.0, 17.0, 1.2), ('← antenna', 5.0, 11.0, 1.0),
               ('SETUP', 88.6, 17.25, 1.0, 90), ('LoRa ANT ↑', 48.5, 2.6, 1.0)]

DIVIDERS_BODY = {r: 'body' for r in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8', 'R9', 'R10',
                                     'R15', 'R16')}
# C7-C10 sit between the two ADS1115 sockets: refs just inside U3's outline (as on rev A)
DIVIDERS_BODY.update({c: (x + 1.25, 56.4) for c, x in
                      (('C7', 23.5), ('C8', 29.1), ('C9', 34.7), ('C10', 40.3))})
LORA_TEXT = {'F1': 'body', 'C17': (73.25, 4.65), 'C16': (76.8, 11.3), 'C15': (93.5, 31.0), 'SW1': (86.4, 17.25, 90)}

# reference designator overrides: 'body' = centred on the part, 'right' = right of it,
# or an absolute (x, y) in board mm
REF_AT = {
    'outdoor-carrier': dict(DIVIDERS_BODY, **LORA_TEXT, **{
        'C2': 'right', 'R11': 'body', 'R13': 'body', 'R12': 'body', 'R14': 'body',
        'R17': 'right', 'R18': 'right', 'U8': 'right',
        'J11': (18.15, 42.0, 90), 'J12': (18.15, 56.0, 90), 'J13': (18.15, 70.0, 90),
        'C11': (4.5, 81.5)}),
    'thermostat-carrier': dict(
        {r: 'hide' for r in [f'R{n}' for n in range(1, 36)] + [f'C{n}' for n in range(1, 16)] +
         [f'D{n}' for n in range(1, 12)] + ['F1', 'L1', 'U1', 'U4', 'J1', 'J2', 'J7', 'JP1']},
        **{f'K{n}': 'body' for n in range(1, 7)}, **{f'U{n}': 'body' for n in (2, 3, 5, 6, *range(7, 13))},
        **{'F2': 'body', 'F3': 'body', 'J3': (10.0, 48.9), 'J4': (10.0, 36.9),
           'J5': (91.0, 56.0), 'J6': (79.0, 3.0)}),
    'indoor-carrier': {'C2': 'right', 'R3': 'body', 'R5': 'body', 'R7': 'body', 'R9': 'body',
                       'R1': 'body', 'R2': 'body', 'R4': 'body', 'R6': 'body', 'R8': 'body',
                       'J10': (86.9, 78.5, 90), 'U9': 'right', 'R10': (66.0, 59.3),
                       'U3': 'above', 'U4': 'above', 'U5': 'above', 'U6': 'above',
                       'J3': (4.5, 39.6), 'J4': (4.5, 55.6), 'C6': LORA_TEXT['C17'],
                       'C5': LORA_TEXT['C16'], 'C4': LORA_TEXT['C15'], 'SW1': LORA_TEXT['SW1'],
                       'F1': 'body'},
}

# ---------------------------------------------------------------- net classes

NETCLASSES = [
    # name, track, clearance, via dia, via drill, patterns
    ('Default', 0.3, 0.2, 0.6, 0.3, []),
    ('Power24', 1.0, 0.3, 0.9, 0.5, ['/VAC_IN', '/VAC_F', '/VRAW', '/*_24V']),   # 24 VAC side
    ('Opto24', 0.3, 0.3, 0.6, 0.3, ['/*_OPTO']),                             # series R to opto LED
    ('Rail5V', 1.0, 0.2, 0.9, 0.5, ['+5V']),
    ('Ground', 0.8, 0.25, 0.9, 0.5, ['GND']),
    ('Rail3V3', 0.5, 0.2, 0.7, 0.35, ['+3V3', '/+3V3_RF']),
    ('Analog', 0.3, 0.2, 0.6, 0.3, ['/P_*', '/V5_MON', '/T_*', '/CT*']),       # dividers, thermistors, CTs
]
ANALOG_TO_24VAC_MM = 3.0
# the thermostat's SMD pins (PowerPAD, USB-C, TSSOP) are too narrow for 1 mm tracks; its loads
# are light (<= 1 A per call wire, 0.7 A at 5 V)
NETCLASS_WIDTH = {'thermostat-carrier': {'Power24': 0.6, 'Rail5V': 0.6, 'Ground': 0.5}}

DRU = f"""(version 1)
(rule "Analog away from 24 VAC"
  (condition "A.hasNetclass('Analog') && (B.hasNetclass('Power24') || B.hasNetclass('Opto24'))")
  (constraint clearance (min {ANALOG_TO_24VAC_MM}mm)))
(rule "24 VAC away from analog"
  (condition "B.hasNetclass('Analog') && (A.hasNetclass('Power24') || A.hasNetclass('Opto24'))")
  (constraint clearance (min {ANALOG_TO_24VAC_MM}mm)))
"""


def write_project_rules(name):
    path = os.path.join(HW, name, name + '.kicad_pro')
    d = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    classes = []
    for cname, tw, cl, vd, vr, _ in NETCLASSES:
        tw = NETCLASS_WIDTH.get(name, {}).get(cname, tw)
        classes.append({'name': cname, 'track_width': tw, 'clearance': cl, 'via_diameter': vd,
                        'via_drill': vr, 'microvia_diameter': 0.3, 'microvia_drill': 0.1,
                        'diff_pair_gap': 0.25, 'diff_pair_width': 0.2, 'diff_pair_via_gap': 0.25,
                        'bus_width': 12, 'wire_width': 6, 'line_style': 0,
                        'pcb_color': 'rgba(0, 0, 0, 0.000)', 'schematic_color': 'rgba(0, 0, 0, 0.000)',
                        'priority': 2147483647 if cname == 'Default' else len(classes)})
    d['net_settings'] = {
        'classes': classes, 'meta': {'version': 5}, 'net_colors': None,
        'netclass_assignments': None,
        'netclass_patterns': [{'netclass': c[0], 'pattern': p} for c in NETCLASSES for p in c[5]],
    }
    board = d.setdefault('board', {})
    ds = board.setdefault('design_settings', {})
    # JLCPCB standard 2-layer process, with margin
    ds['rules'] = {
        'max_error': 0.005, 'min_clearance': 0.15, 'min_connection': 0.0,
        'min_copper_edge_clearance': 0.5, 'min_groove_width': 0.0, 'min_hole_clearance': 0.25,
        'min_hole_to_hole': 0.5, 'min_microvia_diameter': 0.2, 'min_microvia_drill': 0.1,
        'min_resolved_spokes': 2, 'min_silk_clearance': 0.0, 'min_text_height': 0.8,
        'min_text_thickness': 0.12, 'min_through_hole_diameter': 0.3, 'min_track_width': 0.2,
        'min_via_annular_width': 0.13, 'min_via_diameter': 0.6, 'solder_mask_to_copper_clearance': 0.0,
        'use_height_for_length_calcs': True,
    }
    ds['track_widths'] = [0.0, 0.3, 0.5, 0.8, 1.0]
    ds['via_dimensions'] = [{'diameter': 0.0, 'drill': 0.0}, {'diameter': 0.6, 'drill': 0.3},
                            {'diameter': 0.9, 'drill': 0.5}]
    d.setdefault('meta', {'filename': name + '.kicad_pro', 'version': 3})
    json.dump(d, open(path, 'w', encoding='utf-8'), indent=2)
    with open(os.path.join(HW, name, name + '.kicad_dru'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(DRU)


# ---------------------------------------------------------------- netlist

def read_netlist(name):
    root = parse(open(os.path.join(HW, name, name + '.net'), encoding='utf-8').read())[0]
    comps = {}
    for c in find(first(root, 'components'), 'comp'):
        ref = str(first(c, 'ref')[1])
        fpn = first(c, 'footprint')
        fields = {}
        for f in find(first(c, 'fields') or [], 'field'):
            vals = [v for v in f[1:] if not isinstance(v, list)]
            fields[str(first(f, 'name')[1])] = str(vals[0]) if vals else ''
        comps[ref] = dict(value=str(first(c, 'value')[1]), fp=str(fpn[1]) if fpn else '',
                          tstamp=str(first(c, 'tstamps')[1]), fields=fields)
    pads = {}
    for n in find(first(root, 'nets'), 'net'):
        net = str(first(n, 'name')[1])
        for node in find(n, 'node'):
            pads[(str(first(node, 'ref')[1]), str(first(node, 'pin')[1]))] = net
    return comps, pads


# ---------------------------------------------------------------- helpers

def add_edge(board, w, h, r=2.0, left_gaps=(), right_notch=None):
    """Rounded rectangle; left_gaps = (y0, y1) spans of the left edge drawn by a footprint
    (the CT jacks carry their own Edge.Cuts notch for the jack's nose)."""
    def seg(a, b):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(P(*a)); s.SetEnd(P(*b))
        s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(FromMM(0.1))
        board.Add(s)

    def arc(start, mid, end):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_ARC)
        s.SetArcGeometry(P(*start), P(*mid), P(*end))
        s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(FromMM(0.1))
        board.Add(s)

    k = r * (1 - 0.70710678)
    seg((r, 0), (w - r, 0)); seg((w - r, h), (r, h))
    if right_notch:   # (x, y0, y1): a cut-out from the right edge in to x
        nx, n0, n1 = right_notch
        seg((w, r), (w, n0)); seg((w, n0), (nx, n0)); seg((nx, n0), (nx, n1)); seg((nx, n1), (w, n1))
        seg((w, n1), (w, h - r))
    else:
        seg((w, r), (w, h - r))
    y = h - r
    for y0, y1 in sorted(left_gaps, reverse=True):
        seg((0, y), (0, y1))
        y = y0
    seg((0, y), (0, r))
    arc((w - r, 0), (w - k, k), (w, r)); arc((w, h - r), (w - k, h - k), (w - r, h))
    arc((r, h), (k, h - k), (0, h - r)); arc((0, r), (k, k), (r, 0))


def polygon(zone, pts):
    ol = zone.Outline()
    ol.NewOutline()
    for x, y in pts:
        ol.Append(FromMM(BX + x), FromMM(BY + y))


def add_rule_area(board, pts, name, no_copper=True, no_footprints=False, layers=None):
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetZoneName(name)
    ls = pcbnew.LSET()
    for layer in layers or (pcbnew.F_Cu, pcbnew.B_Cu):
        ls.AddLayer(layer)
    z.SetLayerSet(ls)
    z.SetDoNotAllowTracks(no_copper)
    z.SetDoNotAllowVias(no_copper)
    z.SetDoNotAllowZoneFills(no_copper)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowFootprints(no_footprints)
    polygon(z, pts)
    board.Add(z)


def add_gnd_zones(board, gnd, w, h):
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(gnd)
        z.SetZoneName('GND_' + ('F' if layer == pcbnew.F_Cu else 'B'))
        z.SetLocalClearance(FromMM(0.4))
        z.SetMinThickness(FromMM(0.3))
        z.SetThermalReliefGap(FromMM(0.5))
        z.SetThermalReliefSpokeWidth(FromMM(0.6))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        polygon(z, [(0, 0), (w, 0), (w, h), (0, h)])
        board.Add(z)


def silk(board, s, x, y, size=1.0, rot=0, just=0):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(P(x, y))
    t.SetLayer(pcbnew.F_SilkS)
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(0.15 if size >= 1 else 0.12))
    if rot:
        t.SetTextAngleDegrees(rot)
    if just:
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT if just < 0 else pcbnew.GR_TEXT_H_ALIGN_RIGHT)
    board.Add(t)


def sync_fields(fp, fields):
    """Match the footprint's user fields to the schematic symbol (parity check)."""
    for name, val in fields.items():
        if name in ('Reference', 'Value', 'Footprint', 'Datasheet'):
            continue
        new = not fp.HasField(name)
        fp.SetField(name, val)
        if new:
            f = fp.GetField(name)
            f.SetLayer(pcbnew.F_Fab)
            f.SetVisible(False)


def place_reference(fp, ref, rot, how=None):
    """Put the reference designator just outside the part's courtyard."""
    t = fp.Reference()
    if how == 'hide':
        t.SetVisible(False)
        return
    if ref.startswith('H'):
        t.SetVisible(False)
        return
    if ref.startswith('U') and fp.GetFPID().GetLibNickname() == 'hvac_carrier':
        return  # module footprints carry their own centred reference
    bb = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
    cx, cy = bb.GetCenter().x, bb.GetCenter().y
    gap = FromMM(0.75)
    t.SetTextAngleDegrees(0)
    if isinstance(how, tuple):  # (x, y) or (x, y, angle)
        t.SetPosition(P(*how[:2]))
        if len(how) > 2:
            t.SetTextAngleDegrees(how[2])
        return
    if ref.startswith('J'):
        # on the header body, clear of the pin labels behind the pads
        p1 = fp.FindPadByNumber('1').GetPosition()
        off = FromMM(5.5)
        if 'JST_XH' in str(fp.GetFPID().GetLibItemName()):
            t.SetPosition(VECTOR2I(cx, p1.y - FromMM(6.6)))  # above the pin labels
        elif rot == 0:
            t.SetPosition(VECTOR2I(cx, p1.y + off))
        elif rot == 180:
            t.SetPosition(VECTOR2I(cx, p1.y - off))
        else:
            t.SetTextAngleDegrees(90)
            t.SetPosition(VECTOR2I(p1.x + off, cy))
        return
    if isinstance(how, tuple):
        t.SetPosition(P(*how))
    elif how == 'body':
        if rot in (90, 270):
            t.SetTextAngleDegrees(90)
        t.SetPosition(VECTOR2I(cx, cy))
    elif how == 'right':
        t.SetPosition(VECTOR2I(bb.GetRight() + gap + FromMM(0.9), cy))
    elif how == 'above':
        t.SetPosition(VECTOR2I(cx, bb.GetTop() - gap))
    elif rot in (90, 270):  # vertical part: label to its left, reading upward
        t.SetTextAngleDegrees(90)
        t.SetPosition(VECTOR2I(bb.GetLeft() - gap, cy))
    else:                 # horizontal part: label above
        t.SetPosition(VECTOR2I(cx, bb.GetTop() - gap))


def load_fp(fpid):
    lib, name = fpid.split(':', 1)
    path = LOCAL.get(lib, os.path.join(KFP, lib + '.pretty'))
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        raise SystemExit(f'footprint not found: {fpid}')
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


# ---------------------------------------------------------------- build

def build(name):
    cfg = design.BOARDS[name]
    w, h = cfg['size']
    comps, padnets = read_netlist(name)
    parts = {p['ref']: p for p in cfg['parts']()}
    place = PLACE[name]

    board = pcbnew.BOARD()
    board.GetDesignSettings().SetBoardThickness(FromMM(1.6))
    board.SetCopperLayerCount(2)
    tb = board.GetTitleBlock()
    tb.SetTitle(cfg['title']); tb.SetRevision(cfg.get('rev', 'B')); tb.SetDate(cfg.get('date', '2026-10-18'))
    # CT jacks (outdoor): footprint edge line 6.5 mm either side of the jack centre
    gaps = [(y - 6.5, y + 6.5) for ref, (x, y, rot) in PLACE[name].items()
            if 'Jack_3.5mm' in parts.get(ref, {}).get('fp', '')]
    add_edge(board, w, h, left_gaps=gaps,
             right_notch=TSTAT_NOTCH if name == 'thermostat-carrier' else None)

    nets = {}
    for net in sorted(set(padnets.values())):
        ni = pcbnew.NETINFO_ITEM(board, net)
        board.Add(ni)
        nets[net] = ni

    missing = []
    for ref, c in comps.items():
        if not c['fp']:
            continue
        if ref not in place:
            missing.append(ref)
            continue
        fp = load_fp(c['fp'])
        fp.SetReference(ref)
        fp.SetValue(c['value'])
        fp.SetPath(pcbnew.KIID_PATH('/' + c['tstamp']))
        board.Add(fp)
        x, y, rot = place[ref]
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(P(x, y))
        fp.Reference().SetTextSize(VECTOR2I(FromMM(0.9), FromMM(0.9)))
        fp.Reference().SetTextThickness(FromMM(0.13))
        if parts.get(ref, {}).get('dnp'):
            fp.SetDNP(True)
        sync_fields(fp, c['fields'])
        place_reference(fp, ref, rot, REF_AT[name].get(ref))
        for pad in fp.Pads():
            net = padnets.get((ref, pad.GetNumber()))
            if net:
                pad.SetNet(nets[net])
        # terminal pin labels (e.g. +5 / SIG / GND) behind each pad
        labels = parts.get(ref, {}).get('pinlabels')
        if labels and ('JST_XH' in c['fp'] or 'Phoenix' in c['fp']):  # others: TEXT entries
            for pad in fp.Pads():
                i = int(pad.GetNumber()) - 1
                px = pcbnew.ToMM(pad.GetPosition().x) - BX
                py = pcbnew.ToMM(pad.GetPosition().y) - BY
                if 'JST_XH' in c['fp'] and rot == 270:  # left edge: labels right of the pins
                    silk(board, labels[i], px + 3.6, py, 0.8, just=-1)
                elif 'JST_XH' in c['fp']:  # 2.5 mm pitch: labels turned sideways
                    silk(board, labels[i], px, py - 4.2, 0.8, rot=90)
                elif 'MCV' in c['fp']:  # vertical plug: labels above its body
                    silk(board, labels[i], px, py - 5.7, 0.9)
                elif rot == 0:    # bottom edge, plug faces down
                    silk(board, labels[i], px, py - 3.4, 1.0)
                elif rot == 180:  # top edge
                    silk(board, labels[i], px, py + 3.4, 1.0)
                elif rot == 90:   # right edge
                    silk(board, labels[i], px - 3.4, py, 1.0, just=1)
    if missing:
        raise SystemExit(f'{name}: no placement for {missing}')

    if name != 'thermostat-carrier':
        # antenna keep-out: board edge to 8 mm in, between the ESP32 socket rows (both layers)
        add_rule_area(board, [(0, 5.9), (8.0, 5.9), (8.0, 28.4), (0, 28.4)], 'ANTENNA_KEEPOUT')
        # USB access: no parts beyond the DevKit's USB end
        add_rule_area(board, [(43.5, 9.0), (54.5, 9.0), (54.5, 25.0), (43.5, 25.0)], 'USB_CLEAR',
                      no_copper=False, no_footprints=True)
    # LoRa module: no top-side tracks or vias under its body (exposed test points underneath);
    # inset 1.3 mm on the pad sides so the pads' own connections stay legal
    lx, ly, lr = place[{'thermostat-carrier': 'U6'}.get(name, LORA_REFS.get(name, {}).get('LORA'))]
    kx, ky = (4.2, 5.6) if lr in (90, 270) else (5.6, 4.2)   # inside the two pad rows
    add_rule_area(board, [(lx - kx, ly - ky), (lx + kx, ly - ky), (lx + kx, ly + ky),
                          (lx - kx, ly + ky)], 'LORA_KEEPOUT', layers=(pcbnew.F_Cu,))
    if name == 'thermostat-carrier':
        # Freerouting ignores the board-edge clearance: keep its tracks and vias 0.7 mm in
        e, (nx, n0, n1) = 0.7, TSTAT_NOTCH
        for pts in ([(0, 0), (w, 0), (w, e), (0, e)], [(0, h - e), (w, h - e), (w, h), (0, h)],
                    [(0, 0), (e, 0), (e, h), (0, h)], [(w - e, 0), (w, 0), (w, h), (w - e, h)],
                    [(nx - e, n0 - e), (w, n0 - e), (w, n1 + e), (nx - e, n1 + e)]):
            add_rule_area(board, pts, 'EDGE_KEEPOUT')
    add_gnd_zones(board, nets['GND'], w, h)

    for s, x, y, size, *more in TEXT[name] + (COMMON_TEXT if name != 'thermostat-carrier' else []):
        silk(board, s, x, y, size, rot=more[0] if more else 0, just=more[1] if len(more) > 1 else 0)

    out = os.path.join(HW, name, name + '.kicad_pcb')
    pcbnew.SaveBoard(out, board)
    write_project_rules(name)  # after SaveBoard, which rewrites the project file with defaults
    print('wrote', out, len(board.GetFootprints()), 'footprints')


if __name__ == '__main__':
    for n in sys.argv[1:] or list(design.BOARDS):
        build(n)
