"""Hand-planned, locked routes laid down before autorouting (board mm, see gen_pcb.PLACE).

Rev A locked the outdoor divider-to-ADC nets. Rev B moved the whole analog block (CT
jacks now on the left edge, dividers right of U3, reference resistors right of U4), and
the 3 mm Analog-to-24 VAC rule plus the block's distance from the mode-input section
keep those nets clean without hand routes, so there are none for now. (route.py adds its own
locked ground fan-out vias for SMD ground pads.) Add entries as
    (net, layer, [(x, y), ...])  and vias as  (net, (x, y))
to lock a route again.
"""
W = 0.3

PREROUTES = {}
