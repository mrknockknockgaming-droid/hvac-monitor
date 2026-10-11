// Open mounting plates for testing the carrier boards inside equipment
// (e.g. the electrical compartment of a package unit), no enclosure.
// The plate keeps the board's solder side off the sheet metal and holds it
// on four standoffs; four corner slots take 1/4 in self-tapping sheet-metal screws
// (fits up to a #14 / 1/4 in shank; the border is wide enough for a 1/4 in nut driver).
//
// Render: openscad -D 'board="outdoor"' -o stl/outdoor_plate.stl mount_plate.scad
//         openscad -D 'board="indoor"'  -o stl/indoor_plate.stl  mount_plate.scad

board = "outdoor";   // "outdoor" or "indoor"

$fn = 48;

plate_t   = 4;       // plate thickness
margin    = 16;      // plate border around the board (screw heads + driver clearance)
corner_r  = 5;
standoff_h = 8;      // plate top to board underside
standoff_d = 7.5;
insert_d  = 4.0;     // M3 heat-set insert (M3 x 5.7)
insert_l  = 6.0;

screw_slot = [6.6, 11];  // corner slots, diagonal, so the plate can shift a few mm to land on solid metal
screw_at  = 8;       // slot centre in from each plate corner
tie_slot  = [4, 2];  // cable-tie slots (length, width)

SIZE  = board == "outdoor" ? [100, 100] : [100, 80];
HOLES = board == "outdoor"
    ? [[3.5, 37.0], [3.5, 96.5], [96.5, 96.5], [96.5, 3.5]]
    : [[3.5, 37.0], [3.5, 76.5], [96.5, 76.5], [96.5, 3.5]];
// cable-tie slot pairs (board mm, y down) just outside the board edge where
// the sensor and 24 VAC wires leave, for strain relief
TIES = board == "outdoor"
    ? [[20, 105], [40, 105], [75, 105], [105, 66], [105, 85], [70, -5]]
    : [[20, 85], [45, 85], [80, 85], [70, -5]];

W = SIZE[0] + 2 * margin;
L = SIZE[1] + 2 * margin;
function bxy(p) = [margin + p[0], margin + SIZE[1] - p[1]];   // board mm -> plate xy

module rr(w, l, r, h) {
    hull() for (x = [r, w - r], y = [r, l - r]) translate([x, y, 0]) cylinder(r = r, h = h);
}

difference() {
    union() {
        rr(W, L, corner_r, plate_t);
        for (h = HOLES) translate([bxy(h)[0], bxy(h)[1], 0]) cylinder(d = standoff_d, h = plate_t + standoff_h);
    }
    for (h = HOLES) translate([bxy(h)[0], bxy(h)[1], plate_t + standoff_h - insert_l]) cylinder(d = insert_d, h = insert_l + 1);
    // corner screw slots, pointing at the plate centre
    for (cx = [0, 1], cy = [0, 1]) let(x = cx ? W - screw_at : screw_at, y = cy ? L - screw_at : screw_at,
                                       a = atan2(L / 2 - y, W / 2 - x), d = (screw_slot[1] - screw_slot[0]) / 2)
        translate([x, y, -1]) hull() for (k = [-1, 1]) translate([k * d * cos(a), k * d * sin(a), 0])
            cylinder(d = screw_slot[0], h = plate_t + 2);
    // cable-tie slot pairs
    for (t = TIES) let(c = bxy(t), along = (t[0] > SIZE[0] || t[0] < 0))
        for (s = [-4, 4]) translate([c[0] + (along ? 0 : s), c[1] + (along ? s : 0), plate_t / 2])
            cube(along ? [tie_slot[1], tie_slot[0], plate_t + 2] : [tie_slot[0], tie_slot[1], plate_t + 2], center = true);
    // label
    translate([W / 2, L / 2, plate_t - 0.6]) linear_extrude(1)
        text(board == "outdoor" ? "OUTDOOR" : "INDOOR", size = 8, halign = "center", valign = "center");
}

// board ghost for the preview only
%translate([margin, margin, plate_t + standoff_h]) cube([SIZE[0], SIZE[1], 1.6]);
