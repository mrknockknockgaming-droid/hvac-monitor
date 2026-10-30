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
    outline = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(outline, True)
    others = [q for f in board.GetFootprints() for q in f.Pads() if q.GetNetname() != 'GND']

    def via_ok(at):
        # inside the board by 0.8 mm and clear of every other net's pad (thermostat: parts
        # crowd the edges and the notch)
        d = mm(1.1)
        if not all(outline.Contains(pcbnew.VECTOR2I(at.x + dx, at.y + dy))
                   for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d))):
            return False
        return all(not q.HitTest(at, mm(0.6)) for q in others)

    done = []   # (footprint ref, via position, pad position)
    n = 0
    for fp in board.GetFootprints():
        c = fp.GetPosition()
        pth = {q.GetNumber() for q in fp.Pads() if q.GetAttribute() == pcbnew.PAD_ATTRIB_PTH}
        for pad in fp.Pads():
            if pad.GetNetname() != 'GND' or pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            if pad.GetNumber() in pth:
                continue  # exposed pad with its own thermal vias
            p = pad.GetPosition()
            near = [d for d in done if d[0] == fp.GetReference()
                    and abs(d[2].x - p.x) + abs(d[2].y - p.y) < mm(1.5)]
            if near:
                end = near[0][2]
            else:
                # outward along the pad's long axis, away from the part's centre
                sz = pad.GetBoundingBox()
                horiz = sz.GetWidth() >= sz.GetHeight()
                ends = []
                for sgn in (1, -1):   # outward first, then inward
                    if horiz:
                        s0 = sgn if p.x >= c.x else -sgn
                        ends.append(pcbnew.VECTOR2I(p.x + s0 * (sz.GetWidth() // 2 + mm(0.55)), p.y))
                    else:
                        s0 = sgn if p.y >= c.y else -sgn
                        ends.append(pcbnew.VECTOR2I(p.x, p.y + s0 * (sz.GetHeight() // 2 + mm(0.55))))
                ok = [e for e in ends if via_ok(e)]
                if not ok:
                    continue   # the GND pour reaches it
                end = ok[0]
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
        if 'ESP32' in str(u1.GetFPID().GetLibItemName()):
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


CLI = os.environ.get('KICAD_CLI', os.path.join(r'C:\Program Files', 'KiCad', '10.0', 'bin', 'kicad-cli.exe'))


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


def fragment_vias(board, dia=0.6, drill=0.3):
    """Join pour fragments that the grid stitching left apart: group the GND pour pieces of
    both layers by what joins them (vias, PTH pads, GND tracks), then for each group other
    than the biggest, add a via where one of its pieces lies over a piece of the main group."""
    import math
    mm = pcbnew.FromMM
    gnd = board.FindNet('GND')
    frags = []   # (layer, SHAPE_POLY_SET)
    for z in board.Zones():
        if not z.GetIsRuleArea() and z.GetNetname() == 'GND':
            fp = z.GetFilledPolysList(z.GetLayer())
            for i in range(fp.OutlineCount()):
                frags.append((z.GetLayer(), pcbnew.SHAPE_POLY_SET(fp.Outline(i))))
    parent = list(range(len(frags)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def at(p, layer):
        return [i for i, (l, f) in enumerate(frags) if l == layer and f.Contains(p)]

    links = []
    for t in board.GetTracks():
        if t.GetNetname() != 'GND':
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            links.append(at(t.GetPosition(), pcbnew.F_Cu) + at(t.GetPosition(), pcbnew.B_Cu))
        else:
            links.append(at(t.GetStart(), t.GetLayer()) + at(t.GetEnd(), t.GetLayer()))
    for p in board.GetPads():
        if p.GetNetname() == 'GND' and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
            links.append(at(p.GetPosition(), pcbnew.F_Cu) + at(p.GetPosition(), pcbnew.B_Cu))
        elif p.GetNetname() == 'GND':
            # an SMD pad joins the pieces its thermal spokes reach, just past each side's middle
            pb = p.GetBoundingBox()
            c, hx, hy = p.GetPosition(), pb.GetWidth() // 2 + mm(0.15), pb.GetHeight() // 2 + mm(0.15)
            ids = []
            for dx, dy in ((hx, 0), (-hx, 0), (0, hy), (0, -hy)):
                ids += at(pcbnew.VECTOR2I(c.x + dx, c.y + dy), p.GetLayer())
            links.append(ids)
    for ids in links:
        for j in ids[1:]:
            parent[find(j)] = find(ids[0])
    groups = {}
    for i in range(len(frags)):
        groups.setdefault(find(i), []).append(i)
    if len(groups) < 2:
        return 0
    main = max(groups.values(), key=lambda g: sum(frags[i][1].Area() for i in g))
    ring = dia / 2 + 0.05   # a pour's edge already keeps its clearance from other copper
    n = 0
    for g in groups.values():
        if g is main:
            continue
        placed = False
        for i in g:
            layer, f = frags[i]
            others = [frags[j][1] for j in main if frags[j][0] != layer]
            bb = f.BBox()
            y = bb.GetTop()
            while y <= bb.GetBottom() and not placed:
                x = bb.GetLeft()
                while x <= bb.GetRight():
                    pts = [pcbnew.VECTOR2I(x, y)] + [
                        pcbnew.VECTOR2I(int(x + mm(ring) * math.cos(a * math.pi / 4)),
                                        int(y + mm(ring) * math.sin(a * math.pi / 4))) for a in range(8)]
                    if all(f.Contains(p) for p in pts) and any(all(o.Contains(p) for p in pts) for o in others):
                        v = pcbnew.PCB_VIA(board)
                        v.SetPosition(pts[0])
                        v.SetWidth(mm(dia)); v.SetDrill(mm(drill))
                        v.SetNet(gnd)
                        board.Add(v)
                        n += 1
                        placed = True
                        break
                    x += mm(0.1)
                y += mm(0.1)
            if placed:
                break
        if not placed:
            k = bridge_group(board, [frags[i] for i in g], [frags[i] for i in main], gnd)
            for w, dia, drill in ((0.3, 0.6, 0.3), (0.2, 0.6, 0.3)):
                if k:
                    break
                k = maze_bridge(board, [frags[i] for i in g], [frags[i] for i in main], gnd, w=w, dia=dia, drill=drill)
            n += k
    return n


def maze_bridge(board, pieces, main, gnd, w=0.3, gap=0.27, dia=0.6, drill=0.3, cell=0.1):
    """Shortest GND track (both layers, vias allowed) from a cut-off pour group to the main
    pour on a fine grid, clear of every other net's copper, holes and the edge keep-outs."""
    import heapq, math
    mm = pcbnew.FromMM
    layers = (pcbnew.F_Cu, pcbnew.B_Cu)
    outline = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(outline, True)
    bb = outline.BBox()
    x0, y0, step = bb.GetLeft(), bb.GetTop(), mm(cell)
    nx, ny = bb.GetWidth() // step + 1, bb.GetHeight() // step + 1
    pt = lambda i, j: pcbnew.VECTOR2I(x0 + i * step, y0 + j * step)
    keep = [z.Outline() for z in board.Zones() if z.GetIsRuleArea()]

    def blocked_grid(items, layer, r):
        g = bytearray(nx * ny)
        for it in items:
            if not it.IsOnLayer(layer):
                continue
            shape = it.GetEffectiveShape(layer)
            ib = it.GetBoundingBox()
            rr = r + max(0, pcbnew.ToMM(it.GetOwnClearance(layer)) - gap)   # the net class's own clearance
            m = mm(rr) + step
            for j in range(max(0, (ib.GetTop() - m - y0) // step), min(ny, (ib.GetBottom() + m - y0) // step + 1)):
                for i in range(max(0, (ib.GetLeft() - m - x0) // step), min(nx, (ib.GetRight() + m - x0) // step + 1)):
                    if not g[j * nx + i] and shape.Collide(pt(i, j), mm(rr)):
                        g[j * nx + i] = 1
        return g

    foreign = [i for i in list(board.GetTracks()) + list(board.GetPads()) if i.GetNetname() != 'GND']
    free = {layer: blocked_grid(foreign, layer, w / 2 + gap) for layer in layers}   # 1 = blocked
    edge = {}

    def off_board(k):
        if k not in edge:
            p = pt(k % nx, k // nx)
            edge[k] = not outline.Contains(p) or any(z.Contains(p) for z in keep)
        return edge[k]
    vfree = bytearray(nx * ny)
    vb = [blocked_grid(foreign, l, dia / 2 + 0.251 + (gap - 0.27)) for l in layers]   # a via sits on a grid point: no rounding margin
    holes = [i for i in list(board.GetTracks()) + list(board.GetPads())
             if i.Type() == pcbnew.PCB_VIA_T or i.Type() == pcbnew.PCB_PAD_T and i.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)]
    hb = blocked_grid(holes, pcbnew.F_Cu, dia / 2 + 0.2)   # 0.5 mm hole to hole
    smd = blocked_grid([p for p in board.GetPads() if p.GetNetname() == 'GND' and p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD],
                       pcbnew.F_Cu, dia / 2 + 0.1)
    for k in range(nx * ny):
        vfree[k] = not (vb[0][k] or vb[1][k] or hb[k] or smd[k])
    tgt = {l: [m for ml, m in main if ml == l] for l in layers}
    cache = {}

    def is_target(li, k):
        if (li, k) not in cache:
            p = pt(k % nx, k // nx)
            cache[(li, k)] = any(m.Contains(p) for m in tgt[layers[li]])
        return cache[(li, k)]

    dist, prev, heap = {}, {}, []
    for layer, f in pieces:
        li = layers.index(layer)
        fb = f.BBox()
        for j in range(max(0, (fb.GetTop() - y0) // step), min(ny, (fb.GetBottom() - y0) // step + 1)):
            for i in range(max(0, (fb.GetLeft() - x0) // step), min(nx, (fb.GetRight() - x0) // step + 1)):
                k = j * nx + i
                if not free[layer][k] and f.Contains(pt(i, j)):
                    dist[(li, k)] = 0
                    heapq.heappush(heap, (0, li, k))
    moves = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1), (1, 1, 1.42), (1, -1, 1.42), (-1, 1, 1.42), (-1, -1, 1.42)]
    end = None
    while heap:
        d, li, k = heapq.heappop(heap)
        if d > dist.get((li, k), 1e18) or d > 1500:
            continue
        if d > 0 and is_target(li, k):
            end = (li, k)
            break
        i, j = k % nx, k // nx
        nb = []
        for di, dj, c in moves:
            if 0 <= i + di < nx and 0 <= j + dj < ny:
                nb.append((li, (j + dj) * nx + i + di, c))
        if vfree[k]:
            nb.append((1 - li, k, 15))
        for nl, nk, c in nb:
            if free[layers[nl]][nk] or off_board(nk):
                continue
            nd = d + c
            if nd < dist.get((nl, nk), 1e18):
                dist[(nl, nk)] = nd
                prev[(nl, nk)] = (li, k)
                heapq.heappush(heap, (nd, nl, nk))
    if end is None:
        return 0
    path = [end]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    path.reverse()

    def add_track(a, b, li):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pt(a % nx, a // nx)); t.SetEnd(pt(b % nx, b // nx)); t.SetWidth(mm(w))
        t.SetLayer(layers[li]); t.SetNet(gnd)
        board.Add(t)

    start, last, dirn = path[0], path[0], None
    for li, k in path[1:]:
        pl, pk = last
        if li != pl:
            if start[1] != pk:
                add_track(start[1], pk, pl)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pt(k % nx, k // nx)); v.SetWidth(mm(dia)); v.SetDrill(mm(drill)); v.SetNet(gnd)
            board.Add(v)
            start, last, dirn = (li, k), (li, k), None
            continue
        nd = (k % nx - pk % nx, k // nx - pk // nx)
        if dirn is not None and nd != dirn:
            add_track(start[1], pk, li)
            start = last
        dirn = nd
        last = (li, k)
    if start[1] != last[1]:
        add_track(start[1], last[1], last[0])
    return 1


def bridge_group(board, pieces, main, gnd, w=0.3, gap=0.3):
    """A short straight GND track from a cut-off pour piece to the main pour on the same layer,
    clear of every other net's copper."""
    import math
    mm = pcbnew.FromMM
    for layer, f in pieces:
        targets = [m for l, m in main if l == layer]
        foreign = [i for i in list(board.GetTracks()) + list(board.GetPads())
                   if i.GetNetname() != 'GND' and i.IsOnLayer(layer)]
        bb = f.BBox()
        y = bb.GetTop()
        while y <= bb.GetBottom():
            x = bb.GetLeft()
            while x <= bb.GetRight():
                a = pcbnew.VECTOR2I(x, y)
                if f.Contains(a):
                    for k in range(16):
                        for ln in (0.8, 1.2, 1.6, 2.0, 2.5, 3.0):
                            q = pcbnew.VECTOR2I(int(x + mm(ln) * math.cos(k * math.pi / 8)),
                                                int(y + mm(ln) * math.sin(k * math.pi / 8)))
                            if not any(m.Contains(q) for m in targets):
                                continue
                            seg = pcbnew.SEG(a, q)
                            if any(i.GetEffectiveShape(layer).Collide(seg, mm(w / 2 + gap)) for i in foreign):
                                continue
                            t = pcbnew.PCB_TRACK(board)
                            t.SetStart(a); t.SetEnd(q); t.SetWidth(mm(w))
                            t.SetLayer(layer); t.SetNet(gnd)
                            board.Add(t)
                            return 1
                x += mm(0.2)
            y += mm(0.2)
    return 0


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
    if zone_fragments(pcb):
        board = pcbnew.LoadBoard(pcb)
        print('fragment vias:', fragment_vias(board))
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
