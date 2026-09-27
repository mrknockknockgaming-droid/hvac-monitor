"""Export each schematic's netlist with kicad-cli, verify every pin against
design.py, and print a per-connector pin/net summary.

    python check_netlist.py
"""
import os
import subprocess
from collections import defaultdict

from sexp import find, first, parse
import design

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = r'C:\Program Files\KiCad\10.0\bin\kicad-cli.exe'


PIN_NAMES = {
    'hvac_carrier:ESP32_DevKit_30': design.ESP32_LEFT + design.ESP32_RIGHT,
    'hvac_carrier:ADS1115_Module': ['VDD', 'GND', 'SCL', 'SDA', 'ADDR', 'ALRT', 'A0', 'A1', 'A2', 'A3'],
    'hvac_carrier:LM2596HV_Module': ['IN+', 'IN-', 'OUT+', 'OUT-'],
}


def netlist(name):
    d = os.path.join(HW, name)
    out = os.path.join(d, name + '.net')
    subprocess.run([CLI, 'sch', 'export', 'netlist', '--format', 'kicadsexpr', '-o', out,
                    os.path.join(d, name + '.kicad_sch')], check=True, capture_output=True)
    root = parse(open(out, encoding='utf-8').read())[0]
    pins = {}
    members = defaultdict(list)
    for n in find(first(root, 'nets'), 'net'):
        net = str(first(n, 'name')[1])
        for node in find(n, 'node'):
            ref, pin = str(first(node, 'ref')[1]), str(first(node, 'pin')[1])
            pins[(ref, pin)] = net
            members[net].append(f'{ref}.{pin}')
    comps = {str(first(c, 'ref')[1]): c for c in find(first(root, 'components'), 'comp')}
    return pins, members, comps


def main():
    for name, cfg in design.BOARDS.items():
        pins, members, comps = netlist(name)
        errors = []
        parts = cfg['parts']()
        for p in parts:
            if not p.get('on_board', True):
                continue  # off-board sensors (TH1-TH3) are excluded from the netlist by design
            for pin, net in p['nets'].items():
                got = pins.get((p['ref'], pin))
                if net == 'NC':
                    if got and not got.startswith('unconnected-'):
                        errors.append(f"{p['ref']}.{pin}: expected NC, got {got}")
                    continue
                got = (got or '').lstrip('/')
                if got != net:
                    errors.append(f"{p['ref']}.{pin}: expected {net}, got {got or 'nothing'}")
        print(f'\n=== {name}: {len(comps)} components, '
              f'{sum(1 for n in members if not n.startswith("unconnected-"))} nets, '
              f'{len(errors)} mismatches')
        for e in errors:
            print('  !!', e)
        # per-connector summary: every J (and module socket) pin with the other members of its net
        for p in parts:
            if not (p['ref'].startswith('J') or p['ref'] in ('U1', 'U2', 'U3', 'U4')
                    and p['lib'].startswith('hvac_carrier')):
                continue
            extra = ' (DNP)' if p.get('dnp') else ''
            print(f"\n{p['ref']}  {p['value']}{extra}")
            labels = p.get('pinlabels') or PIN_NAMES.get(p['lib'], [])
            nc = []
            for pin in sorted(p['nets'], key=int):
                net = pins.get((p['ref'], pin), 'NC')
                net = 'NC' if net.startswith('unconnected-') else net.lstrip('/')
                if net == 'NC':
                    nc.append(f"{pin}:{labels[int(pin) - 1]}" if labels else pin)
                    continue
                others = [m for m in members.get('/' + net, members.get(net, []))
                          if not m.startswith(p['ref'] + '.')]
                lbl = f' {labels[int(pin) - 1]}' if labels else ''
                if net in design.POWER_NETS:
                    others = [f'{len(others)} pins']
                print(f"  {pin:>2}{lbl:<6} {net:<10} {', '.join(others)}")
            if nc:
                print('  not connected: ' + ', '.join(nc))


if __name__ == '__main__':
    main()
