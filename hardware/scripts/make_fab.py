"""Write the order and review files for each routed carrier board with kicad-cli
(run after route-all.sh). Any Python 3.10+:

    python make_fab.py [outdoor-carrier] [indoor-carrier]

Per board:  <board>-schematic.pdf, erc.rpt, drc.rpt, and in fab/: gerbers/ (JLCPCB layer
set, Protel extensions, PTH/NPTH Excellon + maps), <board>-JLCPCB-gerbers.zip,
<board>-BOM.csv, renders/ (top, bottom, iso). Boards listed in JLC_PARTS also get
<board>-CPL.csv and <board>-JLC-BOM.csv for JLCPCB's SMT assembly (the parts below; the rest
are hand-soldered).
"""
import csv
import os
import subprocess
import sys
import zipfile

import design

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.environ.get('KICAD_CLI', os.path.join(r'C:\Program Files', 'KiCad', '10.0', 'bin', 'kicad-cli.exe'))
LAYERS = 'F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,Edge.Cuts'


def run(*args):
    r = subprocess.run([CLI, *args], capture_output=True, text=True)
    if r.returncode not in (0, 5):  # 5 = DRC/ERC found violations; the report says which
        raise SystemExit(f'kicad-cli {" ".join(args[:3])} failed:\n{r.stdout}\n{r.stderr}')
    return r.stdout


# LCSC numbers for JLCPCB assembly, by (value, footprint): SMD parts only. Thermostat numbers
# were looked up 2026-10-29 (check stock when ordering).
JLC_PARTS = {
    'thermostat-carrier': {
        ('10k', design.FP_R_SMD): 'C17414',
        ('330', design.FP_R_SMD): 'C17630',
        ('5.1k', design.FP_R_SMD): 'C27834',
        ('0.1µF', design.FP_C_SMD): 'C49678',
        ('AMS1117-3.3', design.FP_LDO): 'C6186',
        ('SMBJ48CA', design.FP_D1): 'C173522',
        ('SMBJ40CA', design.FP_D1): 'C114004',
        ('SS34', design.FP_SMA): 'C8678',
        ('MCP23017-E/SO', design.FP_SOIC28): 'C47023',
        ('TPS3823-33', design.FP_SOT235): 'C7719',
        ('SC18IS602B', design.FP_TSSOP16): 'C2678036',
    },
}
# JLCPCB's placement preview turns these packages differently from KiCad (seen on rev B)
JLC_ROTATE = {design.FP_LDO: 180}


def jlc_files(name, d, fab):
    """CPL (from kicad-cli's position file) and the assembly BOM, for the parts in JLC_PARTS."""
    lcsc = JLC_PARTS[name]
    parts = {p['ref']: p for p in design.BOARDS[name]['parts']()}
    pos = os.path.join(fab, name + '-pos.csv')
    run('pcb', 'export', 'pos', '--format', 'csv', '--units', 'mm', '--side', 'front',
        '-o', pos, os.path.join(d, name + '.kicad_pcb'))
    rows, groups = [], {}
    for r in csv.DictReader(open(pos, encoding='utf-8')):
        p = parts.get(r['Ref'])
        if not p or p.get('dnp') or (p['value'], p['fp']) not in lcsc:
            continue
        rot = (float(r['Rot']) + JLC_ROTATE.get(p['fp'], 0)) % 360
        rows.append([r['Ref'], r['Val'], r['Package'], f"{float(r['PosX']):.4f}mm",
                     f"{float(r['PosY']):.4f}mm", 'Top', f'{rot:g}'])
        groups.setdefault((p['value'], p['fp']), []).append(r['Ref'])
    os.remove(pos)
    with open(os.path.join(fab, name + '-CPL.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Designator', 'Val', 'Package', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        w.writerows(sorted(rows, key=lambda r: (r[0][0], int(''.join(c for c in r[0] if c.isdigit()) or 0))))
    with open(os.path.join(fab, name + '-JLC-BOM.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['Comment', 'Designator', 'Footprint', 'LCSC Part #'])
        for (value, fp), refs in sorted(groups.items()):
            refs.sort(key=lambda r: (r[0], int(''.join(c for c in r if c.isdigit()))))
            w.writerow([value, ','.join(refs), fp.split(':')[1], lcsc[(value, fp)]])


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

    if name in JLC_PARTS:
        jlc_files(name, d, fab)

    drc = open(os.path.join(d, 'drc.rpt'), encoding='utf-8').read()
    erc = open(os.path.join(d, 'erc.rpt'), encoding='utf-8').read()
    print(name, [l for l in drc.splitlines() if l.startswith('** Found')],
          [l.strip() for l in erc.splitlines() if 'ERC messages' in l])


if __name__ == '__main__':
    for n in sys.argv[1:] or list(design.BOARDS):
        board(n)
