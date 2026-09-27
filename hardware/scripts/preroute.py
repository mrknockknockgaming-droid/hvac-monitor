"""Hand-planned, locked routes for the outdoor divider-to-ADC nets, laid down
before autorouting so they stay short and away from the 24 VAC section.
Coordinates are board mm (see gen_pcb.PLACE). Each ADS1115 input pin runs up
to its filter cap and down, under the module, to its divider junction.
"""
W = 0.3

OUTDOOR = [
    # net, layer, points
    # U3 inputs -> filter caps C3..C6 (above the module)
    ('/P_LIQ', 'F.Cu', [(21.74, 44.5), (21.74, 41.6), (17.0, 41.6), (17.0, 39.5)]),
    ('/P_VAP', 'F.Cu', [(24.28, 44.5), (24.28, 41.6), (22.6, 41.6), (22.6, 39.5)]),
    ('/P_TSUC', 'F.Cu', [(26.82, 44.5), (26.82, 41.6), (28.2, 41.6), (28.2, 39.5)]),
    ('/V5_MON', 'F.Cu', [(29.36, 44.5), (29.36, 41.6), (33.8, 41.6), (33.8, 39.5)]),
    # U3 inputs -> divider junctions (nested lanes under the module)
    ('/P_LIQ', 'F.Cu', [(21.74, 44.5), (21.74, 51.5), (34.5, 51.5), (36.5, 53.5), (36.5, 55.16)]),
    ('/P_VAP', 'F.Cu', [(24.28, 44.5), (24.28, 50.0), (40.1, 50.0), (42.1, 52.0), (42.1, 55.16)]),
    ('/P_TSUC', 'F.Cu', [(26.82, 44.5), (26.82, 48.5), (45.7, 48.5), (47.7, 50.5), (47.7, 55.16)]),
    ('/V5_MON', 'F.Cu', [(29.36, 44.5), (29.36, 47.0), (51.3, 47.0), (53.3, 49.0), (53.3, 55.16)]),
    # divider top/bottom resistor links
    ('/P_LIQ', 'F.Cu', [(36.5, 55.16), (36.5, 59.5)]),
    ('/P_VAP', 'F.Cu', [(42.1, 55.16), (42.1, 59.5)]),
    ('/P_TSUC', 'F.Cu', [(47.7, 55.16), (47.7, 59.5)]),
    ('/V5_MON', 'F.Cu', [(53.3, 55.16), (53.3, 59.5)]),
    # U4 inputs -> filter caps C7..C9 (between the modules)
    ('/T_SUC', 'F.Cu', [(21.74, 65.5), (21.74, 63.6), (19.0, 63.6), (19.0, 61.95)]),
    ('/T_LIQ', 'F.Cu', [(24.28, 65.5), (24.28, 63.6), (24.6, 63.6), (24.6, 61.95)]),
    ('/T_TSUC', 'F.Cu', [(26.82, 65.5), (26.82, 63.6), (30.2, 63.6), (30.2, 61.95)]),
    # U4 inputs -> reference resistors R9 / R10 / R15 (lanes between the resistor rows)
    ('/T_SUC', 'B.Cu', [(21.74, 65.5), (21.74, 76.9), (43.76, 76.9), (46.16, 74.5)]),
    ('/T_LIQ', 'F.Cu', [(24.28, 65.5), (24.28, 81.7), (43.96, 81.7), (46.16, 79.5)]),
    ('/T_TSUC', 'F.Cu', [(26.82, 65.5), (26.82, 79.0)]),
    ('/T_TSUC', 'B.Cu', [(26.82, 79.0), (26.82, 86.9), (43.76, 86.9), (46.16, 84.5)]),
]
OUTDOOR_VIAS = [('/T_TSUC', (26.82, 79.0))]

PREROUTES = {'outdoor-carrier': (OUTDOOR, OUTDOOR_VIAS)}
