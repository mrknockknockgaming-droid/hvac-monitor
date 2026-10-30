"""Generate the custom symbol library, KiCad projects and schematics for both
carrier boards from design.py. Run with any Python 3.10+.

    python gen_sch.py [board ...]      (default: every board in design.BOARDS)

Regenerating a board's schematic gives its symbols new UUIDs, so rebuild its PCB after.
KICAD_SHARE overrides KiCad's share folder (e.g. /usr/share/kicad in the kicad/kicad Docker image).
"""
import json
import os
import sys
import uuid

from sexp import Str, dump, find, first, parse
import design

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KICAD_SYMS = os.path.join(os.environ.get('KICAD_SHARE', r'C:\Program Files\KiCad\10.0\share\kicad'),
                          'symbols')
LIBNAME = 'hvac_carrier'
FONT = ['effects', ['font', ['size', '1.27', '1.27']]]


def uid():
    return Str(uuid.uuid4())


def num(v):
    s = f'{v:.4f}'.rstrip('0').rstrip('.')
    return '0' if s == '-0' else s


# ---------------------------------------------------------------- symbols

def box_symbol(name, ref, value, fp, desc, left, right, half_w, pitch=2.54):
    """Rectangle symbol with passive pins; left/right = [(number, name)]."""
    rows = max(len(left), len(right))
    top = (rows - 1) * pitch / 2
    half_h = top + pitch
    pins = []
    for side, plist in (('L', left), ('R', right)):
        for i, (n, pname) in enumerate(plist):
            x = -half_w - 2.54 if side == 'L' else half_w + 2.54
            y = top - i * pitch
            pins.append(['pin', 'passive', 'line',
                         ['at', num(x), num(y), '0' if side == 'L' else '180'],
                         ['length', '2.54'],
                         ['name', Str(pname), FONT],
                         ['number', Str(n), FONT]])
    return ['symbol', Str(name),
            ['pin_names', ['offset', '1.016']],
            ['exclude_from_sim', 'no'], ['in_bom', 'yes'], ['on_board', 'yes'],
            ['property', Str('Reference'), Str(ref),
             ['at', num(-half_w), num(half_h + 1.27), '0'], FONT + [['justify', 'left']]],
            ['property', Str('Value'), Str(value),
             ['at', num(-half_w), num(-half_h - 1.27), '0'], FONT + [['justify', 'left']]],
            ['property', Str('Footprint'), Str(fp), ['at', '0', '0', '0'], ['hide', 'yes'], FONT],
            ['property', Str('Datasheet'), Str(''), ['at', '0', '0', '0'], ['hide', 'yes'], FONT],
            ['property', Str('Description'), Str(desc), ['at', '0', '0', '0'], ['hide', 'yes'], FONT],
            ['symbol', Str(f'{name}_0_1'),
             ['rectangle', ['start', num(-half_w), num(half_h)], ['end', num(half_w), num(-half_h)],
              ['stroke', ['width', '0.254'], ['type', 'default']], ['fill', ['type', 'background']]]],
            ['symbol', Str(f'{name}_1_1')] + pins]


def custom_symbols():
    L = design.ESP32_LEFT
    R = design.ESP32_RIGHT
    return [
        box_symbol('ESP32_DevKit_30', 'U', 'ESP32 DevKit', design.FP_ESP32,
                   'ESP32 DevKit 30-pin on two 1x15 sockets, rows 25.4 mm apart',
                   [(str(i + 1), n) for i, n in enumerate(L)],
                   [(str(i + 16), n) for i, n in enumerate(R)], 12.7),
        box_symbol('ADS1115_Module', 'U', 'ADS1115', design.FP_ADS,
                   'ADS1115 ADC breakout on a 1x10 socket',
                   [('1', 'VDD'), ('2', 'GND'), ('3', 'SCL'), ('4', 'SDA'), ('5', 'ADDR'),
                    ('6', 'ALRT')],
                   [('7', 'A0'), ('8', 'A1'), ('9', 'A2'), ('10', 'A3')], 7.62),
        box_symbol('LM2596HV_Module', 'U', 'LM2596HV', design.FP_BUCK,
                   'LM2596HVS buck module on four single-pin sockets',
                   [('1', 'IN+'), ('2', 'IN−')], [('3', 'OUT+'), ('4', 'OUT−')], 10.16),
        box_symbol('Wio_SX1262', 'U', 'Wio-SX1262', design.FP_LORA,
                   'Seeed Wio-SX1262 LoRa module (SX1262 + TCXO, u.FL antenna)',
                   [('8', 'VCC'), ('7', 'GND'), ('10', 'GND'), ('9', 'ANT'), ('1', 'RF_SW')],
                   [('6', 'NSS'), ('4', 'SCK'), ('3', 'MOSI'), ('2', 'MISO'), ('5', 'NRST'),
                    ('11', 'BUSY'), ('12', 'DIO1')], 10.16),
        box_symbol('AQV252G', 'K', 'AQV252G', design.FP_DIP6,
                   'Panasonic AQV252G PhotoMOS relay, 60 V 2.5 A, AC/DC output (pins 4-6)',
                   [('1', 'LED+'), ('2', 'LED−'), ('3', 'NC')],
                   [('6', 'D'), ('5', 'S'), ('4', 'D')], 7.62),
        box_symbol('SC18IS602B', 'U', 'SC18IS602B', design.FP_TSSOP16,
                   'NXP SC18IS602B I2C-bus to SPI bridge, TSSOP-16',
                   [('12', 'VDD'), ('4', 'VSS'), ('7', 'SDA'), ('8', 'SCL'), ('3', '~RESET'),
                    ('9', '~INT'), ('14', 'A0'), ('15', 'A1'), ('16', 'A2')],
                   [('1', 'SS0/GPIO0'), ('2', 'SS1/GPIO1'), ('10', 'SS2/GPIO2'),
                    ('13', 'SS3/GPIO3'), ('11', 'SPICLK'), ('6', 'MOSI'), ('5', 'MISO')], 10.16),
    ]


_libcache = {}


def lib_symbol(lib_id):
    lib, name = lib_id.split(':')
    if lib == LIBNAME:
        sym = {s[1]: s for s in custom_symbols()}[name]
    else:
        if lib not in _libcache:
            root = parse(open(os.path.join(KICAD_SYMS, lib + '.kicad_sym'), encoding='utf-8').read())[0]
            _libcache[lib] = {s[1]: s for s in find(root, 'symbol')}
        sym = _libcache[lib][name]
        ext = first(sym, 'extends')
        if ext:  # derived symbol (e.g. AMS1117-3.3 extends AP1117-15): flatten onto the parent
            parent = _libcache[lib][str(ext[1])]
            own = {p[1]: p for p in find(sym, 'property')}
            sym = [c for c in parent if not (isinstance(c, list) and c and c[0] == 'property'
                                             and c[1] in own)]
            sym = sym[:2] + list(own.values()) + sym[2:]
            # the parent's unit names carry its own name; rename them to the child's
            pname, cname = str(parent[1]), name
            sym = [['symbol', Str(str(c[1]).replace(pname, cname, 1))] + c[2:]
                   if isinstance(c, list) and c and c[0] == 'symbol' else c for c in sym]
    out = list(sym)
    out[1] = Str(lib_id)
    return out


def pins_of(sym):
    """[(number, x, y, angle)] in library coordinates (y up)."""
    res = []
    for sub in find(sym, 'symbol'):
        for p in find(sub, 'pin'):
            at = first(p, 'at')
            res.append((str(first(p, 'number')[1]), float(at[1]), float(at[2]), int(float(at[3]))))
    return res


# ---------------------------------------------------------------- schematic

class Sheet:
    def __init__(self, project, title):
        self.project = project
        self.root = uid()
        self.items = []
        self.libs = {}
        self.pwr = 0
        self.title = title
        self.comment = 'Rev A from docs/hvac-schematics.html; rev B additions in design.py'

    def _instances(self, ref):
        return ['instances', ['project', Str(self.project),
                              ['path', Str('/' + self.root), ['reference', Str(ref)], ['unit', '1']]]]

    def symbol(self, lib_id, ref, value, x, y, rot=0, fp='', in_bom=True, on_board=True,
               dnp=False, fields=None, hide_ref=False, hide_value=False):
        sym = self.libs.setdefault(lib_id, lib_symbol(lib_id))
        props = {p[1]: p for p in find(sym, 'property')}

        def prop(key, val, hide=False):
            at = first(props[key], 'at') if key in props else None
            px, py, pa = (float(at[1]), float(at[2]), int(float(at[3]))) if at else (0.0, 0.0, 0)
            # rotate the library offset by the symbol rotation (CCW, y up)
            c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[rot % 360]
            rx, ry = px * c - py * s, px * s + py * c
            ang = (pa + rot) % 180  # keep text readable (0 or 90)
            node = ['property', Str(key), Str(val), ['at', num(x + rx), num(y - ry), str(ang)]]
            if hide:
                node.append(['hide', 'yes'])
            eff = first(props[key], 'effects') if key in props else None
            just = first(eff, 'justify') if eff else None
            node.append(FONT + [just] if just and rot == 0 else FONT)
            return node

        node = ['symbol', ['lib_id', Str(lib_id)], ['at', num(x), num(y), str(rot)], ['unit', '1'],
                ['exclude_from_sim', 'no'], ['in_bom', 'yes' if in_bom else 'no'],
                ['on_board', 'yes' if on_board else 'no'], ['dnp', 'yes' if dnp else 'no'],
                ['uuid', uid()],
                prop('Reference', ref, hide_ref), prop('Value', value, hide_value),
                prop('Footprint', fp, True), prop('Datasheet', '', True),
                prop('Description', '', True)]
        for k, v in (fields or {}).items():
            node.append(prop(k, v, True))
        for n, *_ in pins_of(sym):
            node.append(['pin', Str(n), ['uuid', uid()]])
        node.append(self._instances(ref))
        self.items.append(node)
        return sym

    def power(self, net, x, y, pin_dir):
        """Power symbol whose pin sits at (x, y), pointing away from a pin
        whose own direction (toward its body) is pin_dir degrees."""
        self.pwr += 1
        sym = lib_symbol('power:' + net)
        a_p = pins_of(sym)[0][3]
        rot = (pin_dir + 180 - a_p) % 360
        self.symbol('power:' + net, f'#PWR{self.pwr:03d}', net, x, y, rot, in_bom=False,
                    on_board=False, hide_ref=True)

    def flag(self, net, x, y):
        """PWR_FLAG on a power net, drawn on a short stub."""
        self.power(net, x, y, 270)
        self.pwr += 1
        self.symbol('power:PWR_FLAG', f'#FLG{self.pwr:03d}', 'PWR_FLAG', x, y + 5.08, 180,
                    in_bom=False, on_board=False, hide_ref=True)
        self.wire(x, y, x, y + 5.08)

    def flag_label(self, net, x, y):
        """PWR_FLAG on a net that has no power symbol (e.g. a rectified input), via a label."""
        self.label(net, x, y, 270)
        self.pwr += 1
        self.symbol('power:PWR_FLAG', f'#FLG{self.pwr:03d}', 'PWR_FLAG', x, y + 5.08, 180,
                    in_bom=False, on_board=False, hide_ref=True)
        self.wire(x, y, x, y + 5.08)

    def label(self, net, x, y, pin_dir):
        ang = (pin_dir + 180) % 360
        just = 'left' if ang in (0, 90) else 'right'
        self.items.append(['label', Str(net), ['at', num(x), num(y), str(ang)],
                           ['effects', ['font', ['size', '1.27', '1.27']], ['justify', just, 'bottom']],
                           ['uuid', uid()]])

    def noconnect(self, x, y):
        self.items.append(['no_connect', ['at', num(x), num(y)], ['uuid', uid()]])

    def wire(self, x1, y1, x2, y2):
        self.items.append(['wire', ['pts', ['xy', num(x1), num(y1)], ['xy', num(x2), num(y2)]],
                           ['stroke', ['width', '0'], ['type', 'default']], ['uuid', uid()]])

    def text(self, s, x, y, size=2.0):
        self.items.append(['text', Str(s), ['exclude_from_sim', 'no'],
                           ['at', num(x), num(y), '0'],
                           ['effects', ['font', ['size', num(size), num(size)], ['bold', 'yes']],
                            ['justify', 'left', 'bottom']], ['uuid', uid()]])

    def place(self, p):
        fields = {}
        if p.get('pinlabels'):
            fields['Silkscreen'] = ' / '.join(p['pinlabels'])
        sym = self.symbol(p['lib'], p['ref'], p['value'], p['x'], p['y'], fp=p['fp'],
                          in_bom=p.get('in_bom', True), on_board=p.get('on_board', True),
                          dnp=p.get('dnp', False), fields=fields)
        for n, px, py, a in pins_of(sym):
            net = p['nets'].get(n)
            ex, ey = p['x'] + px, p['y'] - py
            if net is None:
                if p['lib'] == 'Isolator:H11AA1' and n == '3':
                    continue
                raise SystemExit(f"{p['ref']} pin {n} has no net")
            if net == 'NC':
                self.noconnect(ex, ey)
            elif net in design.POWER_NETS:
                self.power(net, ex, ey, a)
            else:
                self.label(net, ex, ey, a)

    def write(self, path, date, rev='B'):
        tb = ['title_block', ['title', Str(self.title)], ['date', Str(date)], ['rev', Str(rev)],
              ['company', Str('HVAC Monitor')],
              ['comment', '1', Str(self.comment)],
              ['comment', '2', Str('Labels with the same name are connected')]]
        doc = ['kicad_sch', ['version', '20231120'], ['generator', Str('eeschema')],
               ['generator_version', Str('8.0')], ['uuid', self.root], ['paper', Str('A2')], tb,
               ['lib_symbols'] + list(self.libs.values())] + self.items + [
              ['sheet_instances', ['path', Str('/'), ['page', Str('1')]]]]
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(dump(doc) + '\n')


ZONES = {
    'outdoor-carrier': [('A  Power supply', 20.32, 22.86), ('B  Controller', 289.56, 22.86),
                        ('C  I2C devices', 20.32, 83.82), ('D  Analog inputs', 20.32, 129.54),
                        ('D  Thermistors (J9/R15/C9/TH3 added: T_TSUC channel)', 20.32, 172.72),
                        ('E  Mode inputs', 20.32, 218.44), ('Mounting', 269.24, 228.6),
                        ('F  CT inputs (electrical module version 1)', 20.32, 264.16),
                        ('F  CT_BIAS 1.34 V', 340.36, 228.6),
                        ('G  LoRa radio and SETUP button', 20.32, 302.26)],
    'indoor-carrier': [('A  Power supply', 20.32, 22.86), ('B  Controller', 289.56, 22.86),
                       ('C  1-Wire temperatures / E  Expansion', 20.32, 88.9),
                       ('D  Mode inputs', 20.32, 142.24), ('Mounting', 269.24, 190.5),
                       ('F  I2C mux: SHT45 ch 0-1, SDP810 ch 2-4', 20.32, 241.3),
                       ('G  LoRa radio and SETUP button', 20.32, 294.64)],
    'thermostat-carrier': [('A  Power: Rc-C 24 VAC (TPS54360B buck) or USB-C 5 V in; USB-C 5 V out to the display', 20.32, 22.86),
                           ('B  Rc / Rh to the output switches', 20.32, 104.14),
                           ('C  Outputs (PhotoMOS) and read-back (H11AA1): G Y1 Y2 W1 W2 O/B', 20.32, 139.7),
                           ('D  I/O expander + watchdog (all outputs off on reset)', 289.56, 48.26),
                           ('E  LoRa: I2C-SPI bridge + Wio-SX1262', 289.56, 147.32),
                           ('F  Display board and room sensor', 401.32, 48.26),
                           ('Mounting', 401.32, 180.34)],
}


def write_lib():
    os.makedirs(os.path.join(HW, 'lib'), exist_ok=True)
    doc = ['kicad_symbol_lib', ['version', '20231120'], ['generator', Str('hvac_gen')]] + custom_symbols()
    with open(os.path.join(HW, 'lib', LIBNAME + '.kicad_sym'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(dump(doc) + '\n')


def write_project(name):
    d = os.path.join(HW, name)
    os.makedirs(d, exist_ok=True)
    pro = os.path.join(d, name + '.kicad_pro')
    if not os.path.exists(pro):
        with open(pro, 'w', encoding='utf-8') as f:
            json.dump({'meta': {'filename': name + '.kicad_pro', 'version': 3}}, f, indent=2)
    with open(os.path.join(d, 'sym-lib-table'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('(sym_lib_table\n\t(version 7)\n\t(lib (name "hvac_carrier")(type "KiCad")'
                '(uri "${KIPRJMOD}/../lib/hvac_carrier.kicad_sym")(options "")(descr "HVAC carrier modules"))\n)\n')
    with open(os.path.join(d, 'fp-lib-table'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('(fp_lib_table\n\t(version 7)\n\t(lib (name "hvac_carrier")(type "KiCad")'
                '(uri "${KIPRJMOD}/../lib/hvac_carrier.pretty")(options "")(descr "HVAC carrier modules"))\n)\n')
    return d


def main(names=None):
    write_lib()
    for name in names or list(design.BOARDS):
        cfg = design.BOARDS[name]
        d = write_project(name)
        sh = Sheet(name, cfg['title'])
        if name == 'thermostat-carrier':
            sh.comment = 'Fullscope display thermostat carrier; parts and nets in design.py'
        for p in cfg['parts']():
            sh.place(p)
        for label, x, y in ZONES[name]:
            sh.text(label, x, y)
        fy = 27.94
        for i, net in enumerate(design.POWER_NETS):
            if net in cfg.get('driven_nets', ()):
                continue  # a regulator's power output already drives it
            sh.flag(net, 223.52 + 12.7 * i, fy)
        for i, net in enumerate(cfg.get('flag_nets', ())):
            sh.flag_label(net, 223.52 + 12.7 * (len(design.POWER_NETS) + i), fy)
        sh.write(os.path.join(d, name + '.kicad_sch'), cfg.get('date', '2026-10-18'), cfg.get('rev', 'B'))
        print('wrote', name)


if __name__ == '__main__':
    main(sys.argv[1:])
