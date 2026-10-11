// Outdoor carrier (100 x 100 mm) enclosure, at the condenser.
// Render:  openscad -D 'layout="base"' -o stl/outdoor_base.stl outdoor_box.scad
//          openscad -D 'layout="lid"'  -o stl/outdoor_lid.stl  outdoor_box.scad
//          openscad -D 'layout="bar"'  -o stl/outdoor_bar.stl  outdoor_box.scad
include <fullscope_box.scad>

layout = "assembly";   // "assembly", "base", "lid" or "bar" (print 2 bars)

BOARD = [100, 100];
HOLES = [[3.5, 37.0], [3.5, 96.5], [96.5, 96.5], [96.5, 3.5]];   // H1-H4

// clearance around the board: [left, right, bottom, top]
//   left 10:   ESP32 antenna overhangs the left edge by 3.5 mm; keep it off the wall
//   right 26:  J2 mode inputs and J8 SHT30 plugs face right (plug + wire bend)
//   bottom 24: JST-XH sensor headers (vertical) along the bottom edge, gland nuts
//   top 26:    J1 24 VAC plug faces up
MARGIN = [10, 26, 24, 26];

// bottom wall, positions in box x (inner width 136 mm)
//   M20 + 3-hole insert: XDB307 pressure cables (J3-J5)
//   M20 + 3-hole insert: thermistor cables (J6, J7, J9)
//   M16: SHT30 probe cable (J8);  M16: 24 VAC (J1);  M16: Y + O/B (J2)
GLANDS = [
    ["bottom", 21, M20], ["bottom", 49, M20],
    ["bottom", 76, M16], ["bottom", 99, M16], ["bottom", 121, M16],
    ["left", 14, M12],   // breather vent (pressure equalising membrane), low on the left wall
];

SMA_X = undef;   // e.g. 40 to add a 6.6 mm SMA bulkhead hole in the top wall for LoRa

// ghost parts for the preview: [x0, y0, x1, y1, height above PCB, colour]
PARTS = [
    [-3.5, 2.6, 48, 31.7, 14, "black"],     // ESP32 DevKit on sockets (antenna overhangs left)
    [55.9, 34.6, 99.1, 55.4, 20, "blue"],   // LM2596HV module on sockets
    [71, 23, 81, 33, 20, "darkgreen"],      // C1 470 uF 63 V
    [81.9, 0, 93.2, 10.8, 10, "green"],     // J1
    [89.2, 55.9, 100, 67.2, 10, "green"],   // J2
    [89.2, 67.4, 100, 88.9, 10, "green"],   // J8
    [3.9, 42.7, 32.1, 60.2, 11, "purple"],  // U3 ADS1115
    [3.9, 63.7, 32.1, 81.2, 11, "purple"],  // U4 ADS1115
    [9.9, 92.7, 90.7, 99.4, 9, "white"],    // JST-XH row J3-J9
];

fs_render(layout, BOARD, HOLES, MARGIN, GLANDS, PARTS, SMA_X, "OUTDOOR");
