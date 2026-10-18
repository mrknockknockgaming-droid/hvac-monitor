// Indoor carrier rev B (110 x 90 mm) enclosure, at the air handler.
// Render:  openscad -D 'layout="base"'   -o stl/indoor_base.stl   indoor_box.scad
//          openscad -D 'layout="lid"'    -o stl/indoor_lid.stl    indoor_box.scad
//          openscad -D 'layout="bar"'    -o stl/indoor_bar.stl    indoor_box.scad
//          openscad -D 'layout="button"' -o stl/indoor_button.stl indoor_box.scad
include <fullscope_box.scad>

layout = "assembly";   // "assembly", "base", "lid", "bar" (print 2), "button" or "boot"

BOARD = [110, 90];
HOLES = [[3.5, 35.5], [3.5, 86.5], [106.5, 86.5], [106.5, 3.5]];   // H1-H4

// clearance around the board: [left, right, bottom, top]
//   left 10:   ESP32 antenna overhang; DS18B20 jacks J3/J4 (vertical) on the left edge
//   right 26:  J5 main-bus I2C plug faces right
//   bottom 32: J2 mode-input plug faces down, mux sensor jacks J6-J10, gland nuts
//   top 26:    J1 24 VAC plug faces up; SMA bulkhead for the LoRa antenna
MARGIN = [10, 26, 32, 26];

// bottom wall, positions in box x (inner width 146 mm)
//   M20 + 2-hole insert: DS18B20 supply + return probes (J3, J4)
//   M16: Y W G O/B thermostat cable (J2)
//   M20 + 3-hole insert: SDP810 static-pressure sensors (J8-J10)
//   M20 + 2-hole insert: SHT45 return + supply probes (J6, J7)
//   M16: 24 VAC (J1);  M16: main-bus I2C (J5), fit a blanking plug until used
GLANDS = [
    ["bottom", 15, M20], ["bottom", 37, M16], ["bottom", 62, M20],
    ["bottom", 88, M20], ["bottom", 112, M16], ["bottom", 134, M16],
    ["left", 14, M12],   // breather vent
];

SMA_X = 10 + 58.8;       // above the Wio-SX1262's u.FL (board x 58.8)
SETUP = [59.75, 18.25];  // SW1 centre, board mm

PARTS = [
    [-3.5, 2.6, 48, 31.7, 14, "black"],      // ESP32 DevKit
    [55, 0.5, 70, 12.1, 3, "silver"],        // Wio-SX1262
    [55, 14.5, 64.5, 22, 5, "orange"],       // SW1 SETUP
    [66, 34.7, 109, 55.3, 20, "blue"],       // LM2596HV
    [83.5, 23, 93.5, 33, 20, "darkgreen"],   // C1
    [91.8, 0, 103.2, 10.9, 10, "green"],     // J1
    [7.3, 79.1, 28.8, 90, 10, "green"],      // J2
    [99.1, 58.7, 110, 80.2, 10, "green"],    // J5
    [0.6, 41, 7.4, 66, 9, "white"],          // J3, J4
    [29.3, 82.6, 98.8, 89.4, 9, "white"],    // J6-J10 mux sensor jacks
    [12.9, 40.8, 57.7, 51.6, 5, "gray"],     // optos
];

fs_render(layout, BOARD, HOLES, MARGIN, GLANDS, PARTS, SMA_X, "INDOOR", SETUP);
