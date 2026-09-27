"""Write the project footprint library (hardware/lib/hvac_carrier.pretty) for
the three plug-in modules, from the user's measurements (2026-09-27):

  LM2596HVS module: 43 x 20.66 mm; corner pads 39.5 x 17.75 mm apart,
    IN+ top-left, IN- bottom-left, OUT+ top-right, OUT- bottom-right
    (component side up). Pad grid assumed centred on the outline.
  ADS1115 module: 28 x 17.3 mm; 1x10 header VDD GND SCL SDA ADDR ALRT A0-A3,
    pin 1 is 2.5 mm from the short edge and 1.7 mm from the long edge.
    Two 2.3 mm mounting holes 2.5 mm from the edges, assumed on the long edge
    opposite the header.
  ESP32 DevKit 30-pin: two 1x15 rows 25.4 mm apart; antenna end of the board
    6 mm beyond the first pin.

Carrier-side contacts are standard 2.54 mm female sockets (KiCad PinSocket pad:
1.7 mm, 1.0 mm drill).
"""
import os
import uuid

from sexp import Str, dump

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HW, 'lib', 'hvac_carrier.pretty')
SILK, FAB, CRT = 'F.SilkS', 'F.Fab', 'F.CrtYd'
W = {SILK: '0.12', FAB: '0.1', CRT: '0.05'}


def n(v):
    s = f'{v:.4f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def u():
    return ['uuid', Str(uuid.uuid4())]


def font(size=1.0, thick=0.15):
    return ['effects', ['font', ['size', n(size), n(size)], ['thickness', n(thick)]]]


def line(x1, y1, x2, y2, layer):
    return ['fp_line', ['start', n(x1), n(y1)], ['end', n(x2), n(y2)],
            ['stroke', ['width', W[layer]], ['type', 'solid']], ['layer', Str(layer)], u()]


def rect(x1, y1, x2, y2, layer):
    return ['fp_rect', ['start', n(x1), n(y1)], ['end', n(x2), n(y2)],
            ['stroke', ['width', W[layer]], ['type', 'solid']], ['fill', 'no'], ['layer', Str(layer)], u()]


def circle(cx, cy, r, layer):
    return ['fp_circle', ['center', n(cx), n(cy)], ['end', n(cx + r), n(cy)],
            ['stroke', ['width', W[layer]], ['type', 'solid']], ['fill', 'no'], ['layer', Str(layer)], u()]


def text(s, x, y, layer=SILK, size=1.0, rot=0):
    return ['fp_text', 'user', Str(s), ['at', n(x), n(y), str(rot)], ['layer', Str(layer)], u(),
            font(size, 0.15 if size >= 1 else 0.12)]


def pad(num, x, y, square=False):
    return ['pad', Str(num), 'thru_hole', 'rect' if square else 'oval', ['at', n(x), n(y)],
            ['size', '1.7', '1.7'], ['drill', '1'], ['layers', Str('*.Cu'), Str('*.Mask')],
            ['remove_unused_layers', 'no'], u()]


def footprint(name, descr, ref_at, val_at, items):
    return ['footprint', Str(name), ['version', '20241229'], ['generator', Str('hvac_gen')],
            ['layer', Str('F.Cu')], ['descr', Str(descr)], ['tags', Str('module socket carrier')],
            ['property', Str('Reference'), Str('REF**'), ['at', n(ref_at[0]), n(ref_at[1]), '0'],
             ['layer', Str(SILK)], u(), font()],
            ['property', Str('Value'), Str(name), ['at', n(val_at[0]), n(val_at[1]), '0'],
             ['layer', Str(FAB)], u(), font()],
            ['property', Str('Datasheet'), Str(''), ['at', '0', '0', '0'], ['layer', Str(FAB)],
             ['hide', 'yes'], u(), font()],
            ['property', Str('Description'), Str(descr), ['at', '0', '0', '0'], ['layer', Str(FAB)],
             ['hide', 'yes'], u(), font()],
            ['attr', 'through_hole']] + items


def esp32():
    P, ROW = 2.54, 25.4
    items = []
    for i in range(15):
        items.append(pad(str(i + 1), i * P, 0, square=(i == 0)))
        items.append(pad(str(i + 16), i * P, -ROW, square=(i == 0)))
    L, R = -1.27, 14 * P + 1.27
    for y in (0, -ROW):  # socket bodies
        items.append(rect(L - 0.06, y - 1.33, R + 0.06, y + 1.33, SILK))
        items.append(rect(L, y - 1.27, R, y + 1.27, FAB))
    # antenna end of the DevKit, 6 mm beyond pin 1 (overhangs the board edge)
    items.append(line(-6.0, 0.5, -6.0, -ROW - 0.5, FAB))
    items.append(text('antenna end', -6.6, -ROW / 2, FAB, 0.8, 90))
    items.append(text('EN', 0, 2.3, SILK, 0.8))
    items.append(text('VIN', 14 * P, 2.3, SILK, 0.8))
    items.append(text('D23', 0, -ROW - 2.3, SILK, 0.8))
    items.append(text('3V3', 14 * P, -ROW - 2.3, SILK, 0.8))
    items.append(text('ESP32 DevKit 30-pin, USB this end →', 7 * P, -ROW / 2, SILK, 1.0))
    # courtyard: both socket rows and the area between them (the DevKit sits over it)
    items.append(rect(L - 0.54, -ROW - 1.81, R + 0.54, 1.81, CRT))
    return footprint('ESP32_DevKit_30pin_W25.4mm',
                     'ESP32 DevKit 30-pin on two 1x15 female sockets, rows 25.4 mm apart; '
                     'pins 1-15 EN..VIN, 16-30 D23..3V3; antenna end 6 mm beyond pin 1',
                     (7 * P, -ROW / 2 + 2.2), (7 * P, -ROW / 2 - 2.2), items)


def ads1115():
    P = 2.54
    x0, y0, x1, y1 = -2.5, -1.7, 28 - 2.5, 17.3 - 1.7  # module outline relative to pin 1
    names = ['VDD', 'GND', 'SCL', 'SDA', 'ADDR', 'ALRT', 'A0', 'A1', 'A2', 'A3']
    items = [pad(str(i + 1), i * P, 0, square=(i == 0)) for i in range(10)]
    items.append(rect(x0, y0, x1, y1, SILK))
    items.append(rect(x0, y0, x1, y1, FAB))
    for i, nm in enumerate(names):
        items.append(text(nm, i * P, 2.8, SILK, 0.8, 90))
    for hx in (0.0, 23.0):  # module mounting holes (marked, not drilled)
        items.append(circle(hx, 13.1, 1.15, SILK))
    items.append(rect(x0 - 0.1, y0 - 0.1, x1 + 0.1, y1 + 0.1, CRT))
    return footprint('ADS1115_Module',
                     'ADS1115 breakout 28 x 17.3 mm on a 1x10 female socket (VDD..A3); '
                     'pin 1 2.5 mm from short edge, 1.7 mm from long edge',
                     (11.43, 9.0), (11.43, 11.0), items)


def lm2596():
    hx, hy = 39.5 / 2, 17.75 / 2
    ox, oy = 43 / 2, 20.66 / 2
    pads = [('1', -hx, -hy, 'IN+'), ('2', -hx, hy, 'IN−'), ('3', hx, -hy, 'OUT+'), ('4', hx, hy, 'OUT−')]
    items = [pad(num, x, y, square=(num == '1')) for num, x, y, _ in pads]
    items.append(rect(-ox, -oy, ox, oy, SILK))
    items.append(rect(-ox, -oy, ox, oy, FAB))
    for num, x, y, lbl in pads:
        items.append(text(lbl, x + (3.4 if x < 0 else -3.6), y, SILK, 1.0))
    items.append(rect(-ox - 0.1, -oy - 0.1, ox + 0.1, oy + 0.1, CRT))
    return footprint('LM2596HVS_Module',
                     'LM2596HVS buck module 43 x 20.66 mm on four single female sockets, '
                     'pads 39.5 x 17.75 mm apart',
                     (0, -2.0), (0, 2.0), items)


def main():
    os.makedirs(OUT, exist_ok=True)
    for fp in (esp32(), ads1115(), lm2596()):
        with open(os.path.join(OUT, fp[1] + '.kicad_mod'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(dump(fp) + '\n')
        print('wrote', fp[1])


if __name__ == '__main__':
    main()
