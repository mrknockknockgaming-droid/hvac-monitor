// Indoor carrier (100 x 80 mm) enclosure, at the air handler.
// Render:  openscad -D 'layout="base"' -o stl/indoor_base.stl indoor_box.scad
//          openscad -D 'layout="lid"'  -o stl/indoor_lid.stl  indoor_box.scad
//          openscad -D 'layout="bar"'  -o stl/indoor_bar.stl  indoor_box.scad
include <fullscope_box.scad>

layout = "assembly";   // "assembly", "base", "lid" or "bar" (print 2 bars)

BOARD = [100, 80];
HOLES = [[3.5, 37.0], [3.5, 76.5], [96.5, 76.5], [96.5, 3.5]];   // H1-H4

// clearance around the board: [left, right, bottom, top]
//   left 10:   ESP32 antenna overhang
//   bottom 32: J2 mode inputs and J5 I2C plugs face down, plus gland nuts
//   top 26:    J1 24 VAC plug faces up
MARGIN = [10, 10, 32, 26];

// bottom wall, positions in box x (inner width 120 mm)
//   M16: 24 VAC (J1);  M16: Y W G O/B thermostat cable (J2)
//   M20 + 2-hole insert: DS18B20 supply + return probes (J3, J4)
//   M16: I2C expansion (J5), fit a blanking plug until used
GLANDS = [
    ["bottom", 17, M16], ["bottom", 40, M16],
    ["bottom", 66, M20], ["bottom", 96, M16],
    ["left", 14, M12],   // breather vent
];

SMA_X = undef;

PARTS = [
    [-3.5, 2.6, 48, 31.7, 14, "black"],     // ESP32 DevKit
    [55.9, 34.6, 99.1, 55.4, 20, "blue"],   // LM2596HV
    [71, 23, 81, 33, 20, "darkgreen"],      // C1
    [81.9, 0, 93.2, 10.8, 10, "green"],     // J1
    [7.3, 69.2, 28.8, 80, 10, "green"],     // J2
    [67.8, 69.2, 89.3, 80, 10, "green"],    // J5
    [33.6, 72.7, 62.5, 79.4, 9, "white"],   // J3, J4
    [8.9, 40.9, 52, 51.5, 5, "gray"],       // optos
];

fs_render(layout, BOARD, HOLES, MARGIN, GLANDS, PARTS, SMA_X, "INDOOR");
