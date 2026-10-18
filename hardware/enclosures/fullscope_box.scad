// Fullscope carrier-board enclosure library (parametric, OpenSCAD 2021+).
//
// Coordinates: board_holes and board ghost parts use KiCad board mm
// (origin top-left, y down), the same numbers as hardware/scripts/gen_pcb.py.
// Inside the box the board's top edge faces the box's top wall (+Y) and the
// cable glands sit in the bottom wall (-Y), so mount the box glands-down.
//
// Box frame: inner cavity spans x 0..inner_w, y 0..inner_l, floor top at z 0.
// The base's back (z < 0) goes against the wall. The lid screws to four
// external corner posts, outside the gasket ring, so no screw pierces the seal.

$fn = 48;

// ---------------------------------------------------------------- defaults
wall        = 3.2;   // side walls (ASA: 7-8 perimeters at 0.45 mm)
floor_t     = 3.0;   // base back
lid_t       = 4.0;   // lid plate (groove is cut into its underside)
inner_h     = 38;    // floor top to lid underside: 7 standoff + 1.6 PCB + 22 parts + margin
r_in        = 3;     // inner corner radius

standoff_h  = 7;     // floor top to board underside
standoff_d  = 7.5;
insert_d    = 4.0;   // M3 heat-set insert hole (Ruthex-style M3 x 5.7)
insert_l    = 6.0;

post_d      = 10;    // external lid-screw posts
post_inset  = 1.0;   // how far the post centre sits outside the wall corner
lid_screw_d = 3.4;   // M3 clearance in the lid
lid_head_d  = 6.5;   // M3 socket/pan head counterbore
lid_head_h  = 1.6;

gasket_w    = 2.2;   // groove for 1.75 mm TPU filament or 2 mm silicone cord
gasket_dep  = 1.4;

// Mounting bars: two separate parts screwed to the back of the corner posts
// (M3 countersunk into inserts), each with a slotted tab for the wall screw.
// Separate so every part fits a 180 x 180 mm bed (Bambu A1 mini).
bar_w       = 12;    // across the bar
bar_t       = 6;     // thickness (also the air gap behind the box)
tab_w       = 22;    // wall-screw tab, centred on the bar
tab_l       = 15;    // how far the tab reaches past the posts
ear_slot    = [5.2, 9];   // #10 / M5 screw slot

gland_z     = 19;    // gland centre above the floor top

// SETUP button (rev B): a 6 x 6 mm tact switch on the board, pressed through the lid by a
// captive printed plunger in a guide tube; an optional TPU boot snaps over the lid ring.
sw_h        = 5.0;   // switch actuator top above the PCB (6 x 6 x 5 mm part)
btn_d       = 7.0;   // plunger shaft
btn_clear   = 0.4;   // diametral clearance in the tube
tube_l      = 8;     // guide tube under the lid
tube_od     = 11;
btn_collar  = [10, 1.5];   // captive collar under the tube (d, h)
btn_proud   = 0.5;   // plunger top above the boot ring at rest
ring_d      = 14;    // boot ring on the lid top
ring_h      = 2;

// standard metric gland / vent holes (thread + 0.3 mm)
M12 = 12.3; M16 = 16.3; M20 = 20.3; M25 = 25.3;

// ---------------------------------------------------------------- helpers
module rrect(w, l, r, h) {
    // rounded rectangle from (0,0) to (w,l), height h
    hull() for (x = [r, w - r], y = [r, l - r]) translate([x, y, 0]) cylinder(r = r, h = h);
}

module ring2d(w, l, r, off_out, off_in) {
    difference() {
        offset(r = off_out) rrect2d(w, l, r);
        offset(r = off_in) rrect2d(w, l, r);
    }
}
module rrect2d(w, l, r) {
    hull() for (x = [r, w - r], y = [r, l - r]) translate([x, y]) circle(r = r);
}

function post_xy(iw, il) = let(o = wall + post_inset)
    [[-o, -o], [iw + o, -o], [-o, il + o], [iw + o, il + o]];

// board mm (y down) -> box xy
function b2box(p, m_left, m_bottom, bh) = [m_left + p[0], m_bottom + (bh - p[1])];

// ---------------------------------------------------------------- base
// glands: list of [wall, position_along_wall, hole_d, (centre height)]; wall is "bottom",
//         "top", "left" or "right"; position is measured in box x (bottom/top)
//         or box y (left/right) from the inner corner at the origin. The height above the
//         floor top defaults to gland_z (the CT jack openings give their own).
module fs_base(board, holes, m, glands = [], sma = undef, label = "") {
    bw = board[0]; bh = board[1];
    iw = m[0] + bw + m[1];          // m = [left, right, bottom, top]
    il = m[2] + bh + m[3];
    H  = floor_t + inner_h;         // base height, back to rim
    difference() {
        union() {
            // shell
            translate([0, 0, -floor_t]) difference() {
                translate([-wall, -wall, 0]) rrect(iw + 2 * wall, il + 2 * wall, r_in + wall, H);
                translate([0, 0, floor_t]) rrect(iw, il, r_in, H);
            }
            // corner posts
            for (p = post_xy(iw, il)) translate([p[0], p[1], -floor_t]) cylinder(d = post_d, h = H);
            // board standoffs
            for (h = holes) let(p = b2box(h, m[0], m[2], bh))
                translate([p[0], p[1], -0.01]) cylinder(d = standoff_d, h = standoff_h + 0.01);
        }
        // insert holes in standoffs
        for (h = holes) let(p = b2box(h, m[0], m[2], bh))
            translate([p[0], p[1], standoff_h - insert_l]) cylinder(d = insert_d, h = insert_l + 1);
        // insert holes in posts
        for (p = post_xy(iw, il)) translate([p[0], p[1], inner_h - insert_l - 2]) cylinder(d = insert_d, h = insert_l + 3);
        // inserts in the back of the posts for the mounting bars
        for (p = post_xy(iw, il)) translate([p[0], p[1], -floor_t - 1]) cylinder(d = insert_d, h = insert_l + 1);
        // glands and vent
        for (g = glands) gland_hole(g, iw, il);
        // SMA bulkhead for the LoRa antenna (6.35 mm 1/4-36 thread), on the top wall, above the
        // radio's u.FL; flat side down for the anti-rotation flat of most bulkheads
        if (sma != undef) translate([sma, il - 1, gland_z]) rotate([-90, 0, 0]) intersection() {
            cylinder(d = 6.6, h = wall + 2);
            translate([-4, -2.9, 0]) cube([8, 8, wall + 2]);
        }
        // label on the back
        if (label != "") translate([iw / 2, il / 2, -floor_t - 0.01]) mirror([1, 0, 0])
            linear_extrude(0.6) text(label, size = 7, halign = "center", valign = "center");
    }
}

module gland_hole(g, iw, il) {
    w = g[0]; a = g[1]; d = g[2]; z = len(g) > 3 ? g[3] : gland_z;
    if (w == "bottom") translate([a, 1, z]) rotate([90, 0, 0]) cylinder(d = d, h = wall + 2);
    if (w == "top")    translate([a, il - 1, z]) rotate([-90, 0, 0]) cylinder(d = d, h = wall + 2);
    if (w == "left")   translate([1, a, z]) rotate([0, -90, 0]) cylinder(d = d, h = wall + 2);
    if (w == "right")  translate([iw - 1, a, z]) rotate([0, 90, 0]) cylinder(d = d, h = wall + 2);
}

// ---------------------------------------------------------------- lid
// Modelled in place (sits on the rim). Print it flipped: groove side up.
// btn = SETUP switch centre in board mm (undef: no button opening).
module fs_lid(board, m, label = "", btn = undef) {
    iw = m[0] + board[0] + m[1];
    il = m[2] + board[1] + m[3];
    bp = btn == undef ? undef : b2box(btn, m[0], m[2], board[1]);
    translate([0, 0, inner_h]) difference() {
        union() {
            translate([-wall, -wall, 0]) rrect(iw + 2 * wall, il + 2 * wall, r_in + wall, lid_t);
            for (p = post_xy(iw, il)) translate([p[0], p[1], 0]) cylinder(d = post_d, h = lid_t);
            if (bp != undef) translate([bp[0], bp[1], 0]) {
                translate([0, 0, -tube_l]) cylinder(d = tube_od, h = tube_l + 0.01);   // guide tube
                translate([0, 0, lid_t - 0.01]) cylinder(d = ring_d, h = ring_h);      // boot ring
            }
        }
        if (bp != undef) translate([bp[0], bp[1], -tube_l - 1])
            cylinder(d = btn_d + btn_clear, h = tube_l + lid_t + ring_h + 2);
        // gasket groove on the wall centreline
        translate([0, 0, -0.01]) linear_extrude(gasket_dep + 0.01)
            ring2d(iw, il, r_in, wall / 2 + gasket_w / 2, wall / 2 - gasket_w / 2);
        // screws
        for (p = post_xy(iw, il)) translate([p[0], p[1], -1]) {
            cylinder(d = lid_screw_d, h = lid_t + 2);
            translate([0, 0, 1 + lid_t - lid_head_h]) cylinder(d = lid_head_d, h = lid_head_h + 1);
        }
        if (label != "") translate([iw / 2, il / 2, lid_t - 0.6])
            linear_extrude(1) text(label, size = 8, halign = "center", valign = "center");
        if (bp != undef) translate([bp[0], bp[1] - ring_d / 2 - 5, lid_t - 0.6])
            linear_extrude(1) text("SETUP", size = 4, halign = "center", valign = "center");
    }
}

// SETUP plunger, modelled in place: rests 0.6 mm above the switch, collar under the tube keeps
// it captive (fit it from inside before closing the lid). Prints upright, collar down.
function btn_len() = inner_h + lid_t + ring_h + btn_proud - (standoff_h + 1.6 + sw_h + 0.6);
module fs_button(board, m, btn) {
    bp = b2box(btn, m[0], m[2], board[1]);
    z0 = standoff_h + 1.6 + sw_h + 0.6;
    translate([bp[0], bp[1], z0]) {
        cylinder(d = btn_d, h = btn_len());
        // collar top touches the tube bottom at rest
        translate([0, 0, inner_h - tube_l - z0 - btn_collar[1]]) cylinder(d = btn_collar[0], h = btn_collar[1]);
    }
}

// Optional weather boot (print in TPU 95A): a cup that presses over the lid's boot ring, with
// 1.5 mm of room over the plunger; press through it. Prints open side down.
module fs_boot() {
    difference() {
        cylinder(d = ring_d + 2.4, h = ring_h + btn_proud + 1.5 + 0.8);
        translate([0, 0, -0.01]) cylinder(d = ring_d - 0.2, h = ring_h + btn_proud + 1.5);
    }
}

// ---------------------------------------------------------------- mounting bar
// One bar spans the two bottom posts; print two (the top one is the same part
// turned round). Modelled in place under the bottom posts, flat side down.
module fs_bar(board, m) {
    iw = m[0] + board[0] + m[1];
    il = m[2] + board[1] + m[3];
    p = post_xy(iw, il);
    y = p[0][1];
    difference() {
        union() {
            hull() for (x = [p[0][0], p[1][0]]) translate([x, y, 0]) cylinder(d = bar_w, h = bar_t);
            translate([iw / 2 - tab_w / 2, y - bar_w / 2 - tab_l, 0]) cube([tab_w, tab_l + bar_w / 2, bar_t]);
        }
        // M3 countersunk screws from the wall side into the post inserts
        for (x = [p[0][0], p[1][0]]) translate([x, y, -1]) {
            cylinder(d = lid_screw_d, h = bar_t + 2);
            cylinder(d1 = 6.6 + 2, d2 = lid_screw_d, h = (6.6 + 2 - lid_screw_d) / 2);
        }
        translate([iw / 2, y - bar_w / 2 - tab_l / 2 - 1, -1]) hull()
            for (dx = [-(ear_slot[1] - ear_slot[0]) / 2, (ear_slot[1] - ear_slot[0]) / 2])
                translate([dx, 0, 0]) cylinder(d = ear_slot[0], h = bar_t + 2);
    }
}

// ---------------------------------------------------------------- board ghost
// parts: list of [x0, y0, x1, y1, height] in board mm, drawn as boxes above the PCB.
module fs_board_ghost(board, m, parts) {
    bw = board[0]; bh = board[1];
    color("green", 0.6) translate([m[0], m[2], standoff_h]) cube([bw, bh, 1.6]);
    for (p = parts) let(a = b2box([p[0], p[3]], m[0], m[2], bh))
        color(p[5] == undef ? "silver" : p[5], 0.8)
            translate([a[0], a[1], standoff_h + 1.6]) cube([p[2] - p[0], p[3] - p[1], p[4]]);
}

// layout: "assembly" (box + ghost board + lid lifted), "base", "lid", "bar", "button", "boot"
module fs_render(layout, board, holes, m, glands, parts, sma = undef, name = "", btn = undef) {
    if (layout == "base") fs_base(board, holes, m, glands, sma, name);
    else if (layout == "bar") fs_bar(board, m);
    else if (layout == "lid")
        translate([0, 0, inner_h + lid_t]) rotate([180, 0, 0]) fs_lid(board, m, name, btn);
    else if (layout == "button" && btn != undef)
        let(bp = b2box(btn, m[0], m[2], board[1])) translate([-bp[0], -bp[1], -(standoff_h + 1.6 + sw_h + 0.6)])
            fs_button(board, m, btn);
    else if (layout == "boot") fs_boot();
    else {
        color("lightgray") fs_base(board, holes, m, glands, sma, name);
        il = m[2] + board[1] + m[3];
        color("darkgray") {
            translate([0, 0, -floor_t - bar_t]) fs_bar(board, m);
            translate([m[0] + board[0] + m[1], il, -floor_t - bar_t]) rotate([0, 0, 180]) fs_bar(board, m);
        }
        fs_board_ghost(board, m, parts);
        if (btn != undef) color("orange") fs_button(board, m, btn);
        color("white", 0.5) translate([0, 0, 25]) fs_lid(board, m, name, btn);
    }
}
