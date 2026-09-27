"""Autoroute a board with Freerouting and bring the result back into KiCad.
Run with KiCad's Python:

    python.exe route.py export <board>   -> <board>/<board>.dsn
    python.exe route.py import <board>   <- <board>/<board>.ses, fill zones, save
"""
import os
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HW = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def paths(name):
    d = os.path.join(HW, name)
    return (os.path.join(d, name + '.kicad_pcb'), os.path.join(d, name + '.dsn'),
            os.path.join(d, name + '.ses'))


def add_preroutes(board, name):
    """Lay down the locked hand routes from preroute.py (replacing any earlier copy)."""
    import preroute
    import gen_pcb
    segs, vias = preroute.PREROUTES.get(name, ([], []))
    for t in list(board.GetTracks()):
        board.Delete(t)
    mm = pcbnew.FromMM
    for net, layer, pts in segs:
        ni = board.FindNet(net)
        for a, b in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(gen_pcb.P(*a)); t.SetEnd(gen_pcb.P(*b))
            t.SetWidth(mm(preroute.W))
            t.SetLayer(board.GetLayerID(layer))
            t.SetNet(ni)
            t.SetLocked(True)
            board.Add(t)
    for net, at in vias:
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(gen_pcb.P(*at))
        v.SetWidth(mm(0.6)); v.SetDrill(mm(0.3))
        v.SetNet(board.FindNet(net))
        v.SetLocked(True)
        board.Add(v)
    return len(segs)


def export(name):
    pcb, dsn, _ = paths(name)
    board = pcbnew.LoadBoard(pcb)
    print('prerouted paths:', add_preroutes(board, name))
    pcbnew.SaveBoard(pcb, board)
    board.GetDesignSettings().m_NetSettings.RecomputeEffectiveNetclasses()
    board.SynchronizeNetsAndNetClasses(True)
    for z in list(board.Zones()):
        if z.GetIsRuleArea():
            # the USB area only keeps parts out; Freerouting would treat it as a copper keep-out
            if z.GetZoneName() == 'USB_CLEAR':
                board.Remove(z)
        else:
            z.UnFill()  # route on bare copper; the GND pours are filled after routing
    ok = pcbnew.ExportSpecctraDSN(board, dsn)
    add_class_rules(dsn)
    print('export', dsn, ok)


def add_class_rules(dsn):
    """Tell Freerouting to keep the Analog class 3 mm from the 24 VAC classes."""
    import re
    import gen_pcb
    t = open(dsn, encoding='utf-8').read()
    classes = set(re.findall(r'\(class (\S+)', t))
    gap = int(gen_pcb.ANALOG_TO_24VAC_MM * 1000)
    rules = ''.join(f'    (class_class (classes Analog {c}) (rule (clearance {gap})))\n'
                    for c in ('Power24', 'Opto24') if 'Analog' in classes and c in classes)
    if rules:
        i = t.rindex('(class ')
        depth, j = 0, i
        while True:
            depth += {'(': 1, ')': -1}.get(t[j], 0)
            j += 1
            if depth == 0:
                break
        t = t[:j] + '\n' + rules + t[j:]
        open(dsn, 'w', encoding='utf-8').write(t)
    print('class_class rules:', rules.count('class_class'))


def remove_stubs(board):
    """Delete track segments with an end that touches nothing else on the same net."""
    removed = 0
    while True:
        tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
        others = list(board.GetTracks())
        pads = list(board.GetPads())
        dead = None
        for t in tracks:
            for end in (t.GetStart(), t.GetEnd()):
                layer = t.GetLayer()
                hit = any(p.GetNetCode() == t.GetNetCode() and p.IsOnLayer(layer) and p.HitTest(end)
                          for p in pads)
                hit = hit or any(o is not t and o.GetNetCode() == t.GetNetCode() and o.IsOnLayer(layer)
                                 and (o.GetStart() == end or o.GetEnd() == end
                                      or (o.Type() == pcbnew.PCB_VIA_T and o.HitTest(end)))
                                 for o in others)
                if not hit:
                    dead = t
                    break
            if dead:
                break
        if not dead:
            return removed
        board.Delete(dead)
        removed += 1


def import_(name):
    pcb, _, ses = paths(name)
    board = pcbnew.LoadBoard(pcb)
    for t in list(board.GetTracks()):
        if not t.IsLocked():
            board.Delete(t)
    ok = pcbnew.ImportSpecctraSES(board, ses)
    # drop any duplicate the SES carries of a locked hand route
    seen = {}
    for t in list(board.GetTracks()):
        key = (t.Type(), t.GetNetCode(), t.GetLayer(),
               tuple(sorted([(t.GetStart().x, t.GetStart().y), (t.GetEnd().x, t.GetEnd().y)])))
        if key in seen:
            board.Delete(t if not t.IsLocked() else seen[key])
            if t.IsLocked():
                seen[key] = t
        else:
            seen[key] = t
    print('import', ses, ok)
    print('removed dangling track stubs:', remove_stubs(board))
    # ESP32 GND socket pins: diagonal thermal spokes clear the neighbouring traces
    u1 = board.FindFootprintByReference('U1')
    for num in ('14', '29'):
        u1.FindPadByNumber(num).SetThermalSpokeAngleDegrees(45)
    # same for GND pins of the 2.5 mm pitch JST XH headers
    for fp in board.GetFootprints():
        if 'JST_XH' in str(fp.GetFPID().GetLibItemName()):
            for pad in fp.Pads():
                if pad.GetNetname() == 'GND':
                    pad.SetThermalSpokeAngleDegrees(45)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(pcb, board)


if __name__ == '__main__':
    cmd, name = sys.argv[1], sys.argv[2]
    {'export': export, 'import': import_}[cmd](name)
