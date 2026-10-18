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


def fanout_ground(board):
    """Each SMD ground pad gets a short locked track to its own via (to the bottom pour), so a
    top-pour island cut off by routing can't strand it. Neighbouring ground pads of the same part
    (TSSOP A0/A1) share one via through a pad-to-pad link."""
    mm = pcbnew.FromMM
    gnd = board.FindNet('GND')
    done = []   # (footprint ref, via position, pad position)
    n = 0
    for fp in board.GetFootprints():
        c = fp.GetPosition()
        for pad in fp.Pads():
            if pad.GetNetname() != 'GND' or pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            p = pad.GetPosition()
            near = [d for d in done if d[0] == fp.GetReference()
                    and abs(d[2].x - p.x) + abs(d[2].y - p.y) < mm(1.5)]
            if near:
                end = near[0][2]
            else:
                # outward along the pad's long axis, away from the part's centre
                sz = pad.GetBoundingBox()
                horiz = sz.GetWidth() >= sz.GetHeight()
                if horiz:
                    sgn = 1 if p.x >= c.x else -1
                    end = pcbnew.VECTOR2I(p.x + sgn * (sz.GetWidth() // 2 + mm(0.55)), p.y)
                else:
                    sgn = 1 if p.y >= c.y else -1
                    end = pcbnew.VECTOR2I(p.x, p.y + sgn * (sz.GetHeight() // 2 + mm(0.55)))
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(end)
                v.SetWidth(mm(0.6)); v.SetDrill(mm(0.3))
                v.SetNet(gnd)
                v.SetLocked(True)
                board.Add(v)
                n += 1
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(p); t.SetEnd(end)
            t.SetWidth(mm(0.3))
            t.SetLayer(pcbnew.F_Cu)
            t.SetNet(gnd)
            t.SetLocked(True)
            board.Add(t)
            done.append((fp.GetReference(), end, p))
    return n


def export(name):
    pcb, dsn, _ = paths(name)
    board = pcbnew.LoadBoard(pcb)
    print('prerouted paths:', add_preroutes(board, name))
    print('ground fan-out vias:', fanout_ground(board))
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
            locked = t.IsLocked()   # read before a Delete frees t
            board.Delete(t if not locked else seen[key])
            if locked:
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
    # SMD ground pads (TSSOP, radio castellations, 0805s) join the pours solidly
    for pad in board.GetPads():
        if pad.GetNetname() == 'GND' and pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
            pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    print('ground stitching vias:', stitch_ground(board))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(pcb, board)
    fix_ground(name)


def stitch_ground(board, pitch=5.0, dia=0.9, drill=0.5, offset=0.0):
    """Tie the top and bottom GND pours together (and join pour fragments the routing cut off)
    with vias on a grid, only where both pours are solid copper all round the via."""
    import math
    import gen_pcb
    mm = pcbnew.FromMM
    gnd = board.FindNet('GND')
    fills, keepouts = {}, []
    for z in board.Zones():
        if z.GetIsRuleArea():
            keepouts.append(z.Outline())
        elif z.GetNetname() == 'GND':
            fills[z.GetLayer()] = z.GetFilledPolysList(z.GetLayer())
    if len(fills) < 2:
        return 0
    bb = board.GetBoardEdgesBoundingBox()
    w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
    ring = dia / 2 + 0.25
    holes = [(t.GetPosition(), t.GetWidth(pcbnew.F_Cu)) for t in board.GetTracks()
             if t.Type() == pcbnew.PCB_VIA_T]
    holes += [(p.GetPosition(), max(p.GetSize(pcbnew.F_Cu).x, p.GetSize(pcbnew.F_Cu).y))
              for p in board.GetPads()]
    n = 0
    y = pitch / 2 + offset
    while y < h:
        x = pitch / 2 + offset
        while x < w:
            c = gen_pcb.P(x, y)
            if any(math.hypot(c.x - q.x, c.y - q.y) < mm(dia / 2 + 0.6) + d / 2 for q, d in holes):
                x += pitch
                continue
            pts = [c] + [gen_pcb.P(x + ring * math.cos(a * math.pi / 4), y + ring * math.sin(a * math.pi / 4))
                         for a in range(8)]
            if (all(f.Contains(p) for f in fills.values() for p in pts)
                    and not any(k.Contains(p) for k in keepouts for p in pts)):
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(c)
                v.SetWidth(mm(dia)); v.SetDrill(mm(drill))
                v.SetNet(gnd)
                board.Add(v)
                holes.append((c, mm(dia)))
                n += 1
            x += pitch
        y += pitch
    return n


CLI = os.path.join(r'C:\Program Files', 'KiCad', '10.0', 'bin', 'kicad-cli.exe')


def ground_problems(pcb):
    """(ref, pad) of GND pads that DRC finds starved or not joined to a pour."""
    import json
    import re
    import subprocess
    import tempfile
    out = os.path.join(tempfile.gettempdir(), 'hvac-drc.json')
    subprocess.run([CLI, 'pcb', 'drc', '--format', 'json', '-o', out, pcb], capture_output=True)
    d = json.load(open(out, encoding='utf-8'))
    bad = set()
    for v in d['violations'] + d['unconnected_items']:
        if v.get('type', 'unconnected_items') not in ('starved_thermal', 'unconnected_items'):
            continue
        for item in v['items']:
            m = re.match(r'(?:PTH )?[Pp]ad (\S+) \[GND\] of (\S+)', item['description'])
            if m:
                bad.add((m.group(2), m.group(1)))
    return bad


def zone_fragments(pcb):
    import json
    import subprocess
    import tempfile
    out = os.path.join(tempfile.gettempdir(), 'hvac-drc.json')
    subprocess.run([CLI, 'pcb', 'drc', '--format', 'json', '-o', out, pcb], capture_output=True)
    d = json.load(open(out, encoding='utf-8'))
    return sum(1 for v in d['unconnected_items']
               if all(i['description'].startswith('Zone') for i in v['items']))


def fix_ground(name):
    """THT ground pads the pours can't reach with 90 degree spokes: try 45 degree spokes, then
    a solid connection (still hand-solderable on a 2-layer 1 oz board)."""
    pcb = paths(name)[0]
    # pour fragments that touch no pad: a finer, offset grid of stitching vias
    for pitch, off in ((2.5, 1.25), (1.6, 0.4)):
        if not zone_fragments(pcb):
            break
        board = pcbnew.LoadBoard(pcb)
        print(f'extra stitching vias ({pitch} mm):', stitch_ground(board, pitch=pitch, offset=off))
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        pcbnew.SaveBoard(pcb, board)
    for step in ('spokes45', 'solid', 'check'):
        bad = ground_problems(pcb)
        if not bad or step == 'check':
            print('ground pads still unresolved:', sorted(bad) or 'none')
            return
        board = pcbnew.LoadBoard(pcb)
        for ref, num in sorted(bad):
            for pad in board.FindFootprintByReference(ref).Pads():
                if pad.GetNumber() != num:
                    continue
                if step == 'spokes45':
                    pad.SetThermalSpokeAngleDegrees(45)
                else:
                    pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
        print(step, sorted(bad))
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        pcbnew.SaveBoard(pcb, board)


if __name__ == '__main__':
    cmd, name = sys.argv[1], sys.argv[2]
    {'export': export, 'import': import_}[cmd](name)
