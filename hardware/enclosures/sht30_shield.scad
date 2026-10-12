// Radiation shield for the FS400-SHT30 outdoor air probe (J8 on the outdoor board).
// Stacked 45-degree louvres keep sun and rain off the probe while air moves through.
//
// Kit: N_LOUVRES identical louvres + 1 cap, stacked on three M4 rods
// (3x M4 x 100 threaded rod + 6 nuts + washers, or M4 x 100 screws).
// Each louvre carries its own spacer tubes, so the parts print one at a time,
// outer rim on the bed, no supports (all overhangs 45 degrees).
// Print in white or light-grey ASA. Mount on the shaded side of the condenser
// pad or a wall, 30 cm+ from the condenser's discharge air.
//
// Render: openscad -D 'part="louvre"' -o stl/sht30_shield_louvre.stl sht30_shield.scad
//         openscad -D 'part="cap"'    -o stl/sht30_shield_cap.stl    sht30_shield.scad

part = "assembly";   // "assembly", "louvre" or "cap"

$fn = 96;

N_LOUVRES  = 5;
cable_hole = 16.3;   // M16 x 1.5 gland in the cap holds the cable and sets the probe height
ro    = 45;          // louvre outer radius
ri    = 22;          // louvre inner radius (open core; FS400 probes are ~16 mm)
t     = 1.8;         // louvre thickness (vertical)
pitch = 13;          // spacer tube length = vertical spacing between louvres
rod_r = 40;          // rod circle
rod_d = 4.4;         // M4 clearance
tube_d = 8;
cap_boss = 11;       // flat tops for washers above the cap's slope
plate = [34, 4, 30]; // back plate: width, thickness, height above the cap
slot  = [5.2, 10];

drop = ro - ri;      // 45 degrees

module cone(open) {
    rotate_extrude() polygon(open
        ? [[ri, drop], [ri, drop + t], [ro, t], [ro, 0]]
        : [[0, drop], [0, drop + t], [ro, t], [ro, 0]]);
}

module rods(h) {
    for (a = [90, 210, 330]) rotate([0, 0, a]) translate([rod_r, 0, -1]) cylinder(d = rod_d, h = h + 2);
}

module louvre() {
    difference() {
        union() {
            cone(true);
            for (a = [90, 210, 330]) rotate([0, 0, a]) translate([rod_r, 0, 0]) cylinder(d = tube_d, h = pitch);
        }
        rods(pitch);
    }
}

module cap() {
    H = cap_boss + plate[2];
    difference() {
        union() {
            cone(false);
            for (a = [90, 210, 330]) rotate([0, 0, a]) translate([rod_r, 0, 0]) cylinder(d = tube_d + 2, h = cap_boss);
            // back plate, set 5 mm into the cap so it joins across its full width
            translate([-ro + 5 - plate[1], -plate[0] / 2, 0]) cube([plate[1], plate[0], H]);
        }
        rods(cap_boss);
        translate([0, 0, -1]) cylinder(d = cable_hole, h = drop + t + 2);
        for (z = [cap_boss + 9, H - 7]) translate([-ro + 5 - plate[1] - 1, 0, z]) rotate([0, 90, 0])
            hull() for (dz = [-(slot[1] - slot[0]) / 2, (slot[1] - slot[0]) / 2])
                translate([dz, 0, 0]) cylinder(d = slot[0], h = plate[1] + 2);
    }
}

if (part == "louvre") louvre();
else if (part == "cap") cap();
else {
    for (i = [0 : N_LOUVRES - 1]) translate([0, 0, i * pitch]) louvre();
    translate([0, 0, N_LOUVRES * pitch]) cap();
}
