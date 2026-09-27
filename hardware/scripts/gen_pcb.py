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
KFP = r'C:\Program Files\KiCad\10.0\share\kicad\footprints'
LOCAL = {'hvac_carrier': os.path.join(HW, 'lib', 'hvac_carrier.pretty')}
BX, BY = 50.0, 50.0  # board top-left on the KiCad page


def P(x, y):
    return VECTOR2I(FromMM(BX + x), FromMM(BY + y))


# ------------------------------------------------------------------ placement

POWER = {  # identical power corner on both boards
    'U1': (2.5, 29.85, 0),     # ESP32 pin 1 (EN); D23..3V3 row at y = 4.45
    'J1': (90.0, 8.54, 180),   # 24 VAC, plug faces the top edge
    'F1': (80.0, 15.5, 180),   # clip pads 1 at x 80/75, pads 2 at x 65/60
    'D2': (70.5, 22.5, 180),   # K at x 70.5, A at x 60.34
    'D1': (64.5, 28.0, 0),
    'C1': (76.0, 28.0, 0),     # + at x 76
    'C2': (60.0, 32.2, 0),     # right at U2 IN+
    'U2': (77.5, 45.0, 0),     # module centre
    'H4': (96.5, 3.5, 0),
}

PLACE = {
    'outdoor-carrier': dict(POWER, **{
        'H1': (3.5, 37.0, 0), 'H2': (3.5, 96.5, 0), 'H3': (96.5, 96.5, 0),
        'U3': (6.5, 44.5, 0), 'U4': (6.5, 65.5, 0),
        # filter caps at the ADS1115 inputs (rev A general note 4)
        'C3': (17.0, 39.5, 0), 'C4': (22.6, 39.5, 0), 'C5': (28.2, 39.5, 0), 'C6': (33.8, 39.5, 0),
        'C7': (19.0, 61.95, 0), 'C8': (24.6, 61.95, 0), 'C9': (30.2, 61.95, 0),
        # divider column (pad 1 on top)
        'R1': (36.5, 45.0, 270), 'R2': (36.5, 59.5, 270), 'R3': (42.1, 45.0, 270),
        'R4': (42.1, 59.5, 270), 'R5': (47.7, 45.0, 270), 'R6': (47.7, 59.5, 270),
        'R7': (53.3, 45.0, 270), 'R8': (53.3, 59.5, 270),
        'R9': (36.0, 74.5, 0), 'R10': (36.0, 79.5, 0), 'R15': (36.0, 84.5, 0),
        # mode inputs: AC side toward J2, transistor side toward the ESP32
        'U5': (74.5, 66.0, 180), 'U6': (74.5, 79.0, 180),
        'R11': (84.2, 69.6, 180), 'R13': (84.2, 82.6, 180),
        'R12': (62.0, 58.0, 270), 'R14': (62.0, 71.5, 270),
        # right edge, plugs face out
        'J2': (91.46, 64.0, 90), 'J8': (91.46, 85.72, 90),
        # bottom edge, plugs face out
        # JST XH sensor headers along the bottom edge (body 0.5 mm in from the edge)
        'J3': (12.82, 95.5, 0), 'J4': (29.70, 95.5, 0), 'J5': (46.58, 95.5, 0),
        'J6': (62.17, 95.5, 0), 'J7': (73.89, 95.5, 0), 'J9': (85.29, 95.5, 0),
    }),
    'indoor-carrier': dict(POWER, **{
        'H1': (3.5, 37.0, 0), 'H2': (3.5, 76.5, 0), 'H3': (96.5, 76.5, 0),
        'U3': (10.5, 50.0, 90), 'U4': (22.1, 50.0, 90), 'U5': (33.7, 50.0, 90), 'U6': (45.3, 50.0, 90),
        'R2': (10.5, 66.0, 90), 'R4': (22.1, 66.0, 90), 'R6': (33.7, 66.0, 90), 'R8': (45.3, 66.0, 90),
        'R3': (18.85, 40.5, 270), 'R5': (30.45, 40.5, 270), 'R7': (42.05, 40.5, 270),
        'R9': (53.7, 40.5, 270),
        'R1': (48.5, 64.5, 0),
        'J2': (10.5, 71.46, 0), 'J3': (36.58, 75.5, 0), 'J4': (54.58, 75.5, 0), 'J5': (71.0, 71.46, 0),
    }),
}

TEXT = {
    'outdoor-carrier': [('HVAC Monitor rev A', 19.0, 83.4, 1.3), ('outdoor carrier', 19.0, 85.8, 1.3)],
    'indoor-carrier': [('HVAC Monitor rev A', 77.0, 60.5, 1.3), ('indoor carrier', 77.0, 63.0, 1.3)],
}
COMMON_TEXT = [('Set U2 to', 91.5, 26.5, 1.1), ('5.00 V first', 91.5, 28.3, 1.1),
               ('USB', 49.0, 17.0, 1.2), ('← antenna', 5.0, 11.0, 1.0)]

# reference designator overrides: 'body' = centred on the part, 'right' = right of it,
# or an absolute (x, y) in board mm
REF_AT = {
    'outdoor-carrier': {'C2': 'right', 'R11': 'body', 'R13': 'body',
                        'C7': (20.25, 59.2), 'C8': (25.85, 59.2), 'C9': (32.0, 59.2)},
    'indoor-carrier': {'C2': 'right', 'R3': 'body', 'R5': 'body', 'R7': 'body', 'R9': 'body',
                       'U3': 'above', 'U4': 'above', 'U5': 'above', 'U6': 'above'},
}

# ---------------------------------------------------------------- net classes

NETCLASSES = [
    # name, track, clearance, via dia, via drill, patterns
    ('Default', 0.3, 0.2, 0.6, 0.3, []),
    ('Power24', 1.0, 0.3, 0.9, 0.5, ['/VAC_IN', '/VAC_F', '/VRAW', '/*_24V']),   # 24 VAC side
    ('Opto24', 0.3, 0.3, 0.6, 0.3, ['/*_OPTO']),                             # series R to opto LED
    ('Rail5V', 1.0, 0.3, 0.9, 0.5, ['+5V']),
    ('Ground', 0.8, 0.25, 0.9, 0.5, ['GND']),
    ('Rail3V3', 0.5, 0.2, 0.7, 0.35, ['+3V3']),
    ('Analog', 0.3, 0.2, 0.6, 0.3, ['/P_*', '/V5_MON', '/T_*']),               # dividers, thermistors
]
ANALOG_TO_24VAC_MM = 3.0

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

def add_edge(board, w, h, r=2.0):
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
    seg((r, 0), (w - r, 0)); seg((w, r), (w, h - r)); seg((w - r, h), (r, h)); seg((0, h - r), (0, r))
    arc((w - r, 0), (w - k, k), (w, r)); arc((w, h - r), (w - k, h - k), (w - r, h))
    arc((r, h), (k, h - k), (0, h - r)); arc((0, r), (k, k), (r, 0))


def polygon(zone, pts):
    ol = zone.Outline()
    ol.NewOutline()
    for x, y in pts:
        ol.Append(FromMM(BX + x), FromMM(BY + y))


def add_rule_area(board, pts, name, no_copper=True, no_footprints=False):
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetZoneName(name)
    ls = pcbnew.LSET(); ls.AddLayer(pcbnew.F_Cu); ls.AddLayer(pcbnew.B_Cu)
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
    if ref.startswith('H'):
        t.SetVisible(False)
        return
    if ref in ('U1', 'U2', 'U3', 'U4') and fp.GetFPID().GetLibNickname() == 'hvac_carrier':
        return  # module footprints carry their own centred reference
    bb = fp.GetCourtyard(pcbnew.F_CrtYd).BBox()
    cx, cy = bb.GetCenter().x, bb.GetCenter().y
    gap = FromMM(0.75)
    t.SetTextAngleDegrees(0)
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
    tb.SetTitle(cfg['title']); tb.SetRevision('A'); tb.SetDate('2026-09-27')
    add_edge(board, w, h)

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
        if labels:
            for pad in fp.Pads():
                i = int(pad.GetNumber()) - 1
                px = pcbnew.ToMM(pad.GetPosition().x) - BX
                py = pcbnew.ToMM(pad.GetPosition().y) - BY
                if 'JST_XH' in c['fp']:  # 2.5 mm pitch: labels turned sideways
                    silk(board, labels[i], px, py - 4.2, 0.8, rot=90)
                elif rot == 0:    # bottom edge, plug faces down
                    silk(board, labels[i], px, py - 3.4, 1.0)
                elif rot == 180:  # top edge
                    silk(board, labels[i], px, py + 3.4, 1.0)
                elif rot == 90:   # right edge
                    silk(board, labels[i], px - 3.4, py, 1.0, just=1)
    if missing:
        raise SystemExit(f'{name}: no placement for {missing}')

    # antenna keep-out: board edge to 8 mm in, between the ESP32 socket rows (both layers)
    add_rule_area(board, [(0, 5.9), (8.0, 5.9), (8.0, 28.4), (0, 28.4)], 'ANTENNA_KEEPOUT')
    # USB access: no parts beyond the DevKit's USB end
    add_rule_area(board, [(43.5, 9.0), (54.5, 9.0), (54.5, 25.0), (43.5, 25.0)], 'USB_CLEAR',
                  no_copper=False, no_footprints=True)
    add_gnd_zones(board, nets['GND'], w, h)

    for s, x, y, size in TEXT[name] + COMMON_TEXT:
        silk(board, s, x, y, size)

    out = os.path.join(HW, name, name + '.kicad_pcb')
    pcbnew.SaveBoard(out, board)
    write_project_rules(name)  # after SaveBoard, which rewrites the project file with defaults
    print('wrote', out, len(board.GetFootprints()), 'footprints')


if __name__ == '__main__':
    for n in sys.argv[1:] or list(design.BOARDS):
        build(n)
