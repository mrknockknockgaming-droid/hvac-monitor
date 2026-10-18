"""Write the order and review files for each routed carrier board with kicad-cli
(run after route-all.sh). Any Python 3.10+:

    python make_fab.py [outdoor-carrier] [indoor-carrier]

Per board:  <board>-schematic.pdf, erc.rpt, drc.rpt, and in fab/: gerbers/ (JLCPCB layer
set, Protel extensions, PTH/NPTH Excellon + maps), <board>-JLCPCB-gerbers.zip,
<board>-BOM.csv, renders/ (top, bottom, iso).
"""
import os
import subprocess
import sys
import zipfile

import design

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(r'C:\Program Files', 'KiCad', '10.0', 'bin', 'kicad-cli.exe')
LAYERS = 'F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,Edge.Cuts'


def run(*args):
    r = subprocess.run([CLI, *args], capture_output=True, text=True)
    if r.returncode not in (0, 5):  # 5 = DRC/ERC found violations; the report says which
        raise SystemExit(f'kicad-cli {" ".join(args[:3])} failed:\n{r.stdout}\n{r.stderr}')
    return r.stdout


def board(name):
    d = os.path.join(HW, name)
    sch, pcb = os.path.join(d, name + '.kicad_sch'), os.path.join(d, name + '.kicad_pcb')
    fab = os.path.join(d, 'fab')
    gerb, renders = os.path.join(fab, 'gerbers'), os.path.join(fab, 'renders')
    for p in (gerb, renders):
        os.makedirs(p, exist_ok=True)
        for f in os.listdir(p):
            os.remove(os.path.join(p, f))

    run('sch', 'export', 'pdf', '-o', os.path.join(d, name + '-schematic.pdf'), sch)
    run('sch', 'erc', '-o', os.path.join(d, 'erc.rpt'), sch)
    run('pcb', 'drc', '--severity-all', '--schematic-parity', '-o', os.path.join(d, 'drc.rpt'), pcb)

    run('pcb', 'export', 'gerbers', '-o', gerb + os.sep, '--layers', LAYERS,
        '--use-drill-file-origin', '--subtract-soldermask', pcb)
    run('pcb', 'export', 'drill', '-o', gerb + os.sep, '--format', 'excellon',
        '--excellon-separate-th', '--generate-map', '--map-format', 'gerberx2',
        '--excellon-units', 'mm', pcb)
    z = os.path.join(fab, name + '-JLCPCB-gerbers.zip')
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(os.listdir(gerb)):
            zf.write(os.path.join(gerb, f), f)

    run('sch', 'export', 'bom', '-o', os.path.join(fab, name + '-BOM.csv'),
        '--fields', 'Reference,Value,Footprint,${QUANTITY},${DNP},${EXCLUDE_FROM_BOARD}',
        '--labels', 'Refs,Value,Footprint,Qty,DNP,Off-board',
        '--group-by', 'Value,Footprint,${DNP},${EXCLUDE_FROM_BOARD}',
        '--sort-field', 'Reference', '--ref-range-delimiter', '-', sch)

    for side, extra in (('top', []), ('bottom', []),
                        ('iso', ['--rotate', '-45,0,45', '--perspective'])):
        view = 'top' if side == 'iso' else side
        run('pcb', 'render', '--side', view, '--width', '2000', '--height', '2000',
            '--quality', 'high', '-o', os.path.join(renders, f'{name}-{side}.png'), *extra, pcb)

    drc = open(os.path.join(d, 'drc.rpt'), encoding='utf-8').read()
    erc = open(os.path.join(d, 'erc.rpt'), encoding='utf-8').read()
    print(name, [l for l in drc.splitlines() if l.startswith('** Found')],
          [l.strip() for l in erc.splitlines() if 'ERC messages' in l])


if __name__ == '__main__':
    for n in sys.argv[1:] or list(design.BOARDS):
        board(n)
