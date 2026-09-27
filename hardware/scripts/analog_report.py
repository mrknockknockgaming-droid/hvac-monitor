"""Report track length per analog net and its closest approach to 24 VAC copper.
Run with KiCad's Python:  python.exe analog_report.py <board>
"""
import math
import os
import sys

import pcbnew

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANALOG = ['/P_LIQ_IN', '/P_VAP_IN', '/P_TSUC_IN', '/P_LIQ', '/P_VAP', '/P_TSUC', '/V5_MON',
          '/T_SUC', '/T_LIQ', '/T_TSUC']
HV_PREFIX = ('/VAC_', '/VRAW')
HV_SUFFIX = ('_24V', '_OPTO')


def is_hv(net):
    return net.startswith(HV_PREFIX) or net.endswith(HV_SUFFIX)


def seg_pts(t, step=0.25):
    a, b = t.GetStart(), t.GetEnd()
    ax, ay, bx, by = (pcbnew.ToMM(v) for v in (a.x, a.y, b.x, b.y))
    n = max(1, int(math.hypot(bx - ax, by - ay) / step))
    return [(ax + (bx - ax) * i / n, ay + (by - ay) * i / n) for i in range(n + 1)]


def main(name):
    b = pcbnew.LoadBoard(os.path.join(HW, name, name + '.kicad_pcb'))
    hv = []
    for t in b.GetTracks():
        if is_hv(t.GetNetname()):
            hv += [(x, y, pcbnew.ToMM(t.GetWidth()) / 2) for x, y in seg_pts(t)]
    for p in b.GetPads():
        if is_hv(p.GetNetname()):
            pos = p.GetPosition()
            hv.append((pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y), pcbnew.ToMM(p.GetSize(pcbnew.F_Cu).x) / 2))
    print(f'{name}: net, track length (mm), vias, closest edge-to-edge distance to 24 VAC copper (mm)')
    for net in ANALOG:
        tr = [t for t in b.GetTracks() if t.GetNetname() == net]
        if not tr:
            continue
        length = sum(pcbnew.ToMM(t.GetLength()) for t in tr if t.Type() == pcbnew.PCB_TRACE_T)
        vias = sum(1 for t in tr if t.Type() == pcbnew.PCB_VIA_T)
        dmin = min((math.hypot(x - hx, y - hy) - r - pcbnew.ToMM(t.GetWidth()) / 2
                    for t in tr if t.Type() == pcbnew.PCB_TRACE_T for x, y in seg_pts(t)
                    for hx, hy, r in hv), default=float('inf'))
        print(f'  {net:<12} {length:6.1f}  {vias}  {dmin:5.1f}')


if __name__ == '__main__':
    main(sys.argv[1])
