// Outdoor carrier rev B (100 x 100 mm) enclosure, at the condenser.
// Render:  openscad -D 'layout="base"'   -o stl/outdoor_base.stl   outdoor_box.scad
//          openscad -D 'layout="lid"'    -o stl/outdoor_lid.stl    outdoor_box.scad
//          openscad -D 'layout="bar"'    -o stl/outdoor_bar.stl    outdoor_box.scad
//          openscad -D 'layout="button"' -o stl/outdoor_button.stl outdoor_box.scad
//          openscad -D 'layout="boot"'   -o stl/setup_boot.stl     outdoor_box.scad   (TPU)
include <fullscope_box.scad>

layout = "assembly";   // "assembly", "base", "lid", "bar" (print 2), "button" or "boot"

BOARD = [100, 100];
HOLES = [[3.5, 35.5], [3.5, 96.5], [96.5, 96.5], [96.5, 3.5]];   // H1-H4

// clearance around the board: [left, right, bottom, top]
//   left 4.5:  CT jacks J11-J13 sit on the left edge (nose 1.8 mm past it) so the SCT-013
//              plugs go in through the left wall; the ESP32 antenna overhang (3.5 mm) clears
//              the wall by 1 mm
//   right 26:  J2 mode inputs and J8 SHT30 plugs face right (plug + wire bend)
//   bottom 24: JST-XH sensor headers (vertical) along the bottom edge, gland nuts
//   top 26:    J1 24 VAC plug faces up; SMA bulkhead for the LoRa antenna
MARGIN = [4.5, 26, 24, 26];

// bottom wall, positions in box x (inner width 130.5 mm)
//   M20 + 3-hole insert: XDB307 pressure cables (J3-J5)
//   M20 + 4-hole insert: thermistor cables (J6, J7, J9, J10)
//   M16: SHT30 probe cable (J8);  M16: 24 VAC (J1);  M16: Y + O/B (J2)
// left wall: CT plug openings on the jack axes (2.5 mm above the PCB), box y = 24 + 100 - board y
CT_Z = standoff_h + 1.6 + 2.5;
CT_D = 9;        // passes the moulded plug collar; plug a grommet or blanking plug into unused ones
GLANDS = [
    ["bottom", 22, M20], ["bottom", 48, M20],
    ["bottom", 72, M16], ["bottom", 94, M16], ["bottom", 116, M16],
    ["left", 14, M12],   // breather vent (pressure equalising membrane), low on the left wall
    ["left", 24 + 100 - 46, CT_D, CT_Z],   // J11 CT1 compressor
    ["left", 24 + 100 - 60, CT_D, CT_Z],   // J12 CT2 condenser fan
    ["left", 24 + 100 - 74, CT_D, CT_Z],   // J13 CT3 HERM lead
];

SMA_X = 4.5 + 58.8;      // above the Wio-SX1262's u.FL (board x 58.8)
SETUP = [94.71, 16.25];  // SW1 centre, board mm

// ghost parts for the preview: [x0, y0, x1, y1, height above PCB, colour]
PARTS = [
    [-3.5, 2.6, 48, 31.7, 14, "black"],      // ESP32 DevKit on sockets (antenna overhangs left)
    [55, 0.5, 70, 12.1, 3, "silver"],        // Wio-SX1262 (u.FL at the top)
    [90, 12.5, 99.5, 20, 5, "orange"],       // SW1 SETUP
    [55.9, 34.6, 99.1, 55.4, 20, "blue"],    // LM2596HV module on sockets
    [71, 23, 81, 33, 20, "darkgreen"],       // C1 470 uF 63 V
    [81.9, 0, 93.2, 10.9, 10, "green"],      // J1
    [89.1, 55.8, 100, 67.2, 10, "green"],    // J2
    [89.1, 67.4, 100, 88.9, 10, "green"],    // J8
    [18.9, 39.7, 47.1, 57.2, 11, "purple"],  // U3 ADS1115
    [18.9, 60.7, 47.1, 78.2, 11, "purple"],  // U4 ADS1115
    [-1.8, 39.7, 12.5, 80.3, 5, "gray"],     // CT jacks J11-J13
    [8, 92.6, 93, 99.4, 9, "white"],         // JST-XH row J3-J10
];

fs_render(layout, BOARD, HOLES, MARGIN, GLANDS, PARTS, SMA_X, "OUTDOOR", SETUP);
