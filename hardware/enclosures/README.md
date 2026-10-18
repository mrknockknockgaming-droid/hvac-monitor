# Enclosures for the rev B carrier boards

Two ways to box the boards, for the bench and the first install on the Mesa system:

1. **Buy**: one Hammond polycarbonate box, the same part for both boards.
2. **Print**: the parametric OpenSCAD boxes in this folder (STLs in `stl/`).

Board numbers (outline, H1-H4 holes, connector positions) come from
`hardware/*-carrier/*.kicad_pcb` and `hardware/scripts/gen_pcb.py`. If the board
layout changes, update `BOARD`, `HOLES`, `MARGIN`, `GLANDS`, `SMA_X`, `SETUP` and `PARTS` in
`outdoor_box.scad` / `indoor_box.scad` (and `SIZE` / `HOLES` / `TIES` in `mount_plate.scad`)
and re-export.

Rev B (2026-10-18): the boards are 100 x 100 (outdoor) and 100 x 88 (indoor). Both have a
LoRa radio (SMA bulkhead in the top wall) and a SETUP button (plunger through the lid). The
outdoor board also has three CT jacks on its left edge (plug openings in the left wall).
**The STLs in `stl/` and the PNGs in `png/` are still rev A** until they are re-exported.
OpenSCAD was not installed on the PC when the `.scad` files were updated, so run the commands
under Regenerating before printing.

## Option 1: off the shelf

| | Outdoor (100 x 100 board) | Indoor (100 x 88 board) |
|---|---|---|
| Box | Hammond **1554R2GY** (160 x 160 x 61 mm) | Hammond **1554R2GY** |
| Why | UV-stabilised polycarbonate, IP66/67/68, NEMA 4X, -40 to +110 °C | same box, one SKU; attic air handlers in Mesa run past ABS's 60 °C limit |
| Board mount | 1554RPL inner panel, drill the four H1-H4 holes, M3 x 8 nylon standoffs | same |
| Extra holes | 3x 9 mm in the left wall on the CT jack axes (board edge about 4.5 mm from the wall), 6.6 mm SMA in the top wall | 6.6 mm SMA in the top wall |

Fit: the 1554R's cavity is about 150 mm square. The outdoor board needs about 4.5 (CT jacks
against the left wall) + 100 + 22 (J2/J8 plugs and a wire bend) = 127 mm across and about
12 (J1 plug) + 100 + 10 (JST cables) = 122 mm down. The indoor board needs about 10 + 100 + 22
= 132 mm across and 12 + 88 + 22 (J2 plug, mux sensor cables) = 122 mm down. Both fit with
room to spare. Press SETUP with the lid open. Check
Hammond's drawing for the inner-panel pattern before drilling.

## Option 2: printed (this folder)

| File | Box outside (with lid) | Board clearance [left, right, bottom, top] |
|---|---|---|
| `outdoor_box.scad` | 149 x 168 x 53 mm with mounting bars | 4.5, 26, 24, 26 mm |
| `indoor_box.scad`  | 154 x 164 x 53 mm with mounting bars | 10, 26, 32, 26 mm |
| `sht30_shield.scad` | 90 mm dia louvre shield for the outdoor air probe | |
| `mount_plate.scad` | open test plates, see below | |

Design:
- Board on four 7 mm standoffs (M3 heat-set inserts) at H1-H4; 38 mm cavity
  height covers the tallest parts (LM2596 module and C1 on sockets, ~22 mm).
- Lid screws go into four external corner posts, outside the gasket, so no
  screw passes through the sealed area. Gasket: 1.75 mm TPU filament or 2 mm
  silicone cord pressed into the groove under the lid.
- All cable glands are in the bottom wall: mount the box glands-down so water
  drips off the cables. A breather vent sits low on the left wall.
- Two mounting bars (print 2 per box) screw to the back of the corner posts with
  M3 countersunk screws and carry slotted tabs for #10 / M5 wall screws. They are
  separate parts so the base stays within the 180 x 180 bed, and they hold the box 6 mm off
  the wall.
- **LoRa antenna**: `SMA_X` puts a 6.6 mm SMA bulkhead hole, with an anti-rotation flat, in
  the top wall straight above the Wio-SX1262's u.FL. Use a 10-15 cm u.FL to SMA-bulkhead
  pigtail and a 900 MHz whip outside.
- **CT plugs (outdoor)**: three 9 mm holes in the left wall on the jack axes, 2.5 mm above the
  PCB. The SCT-013's moulded plug passes through the wall into the jack on the board edge.
  Close unused holes with a 9 mm blanking grommet. With clamps plugged in, a dab of silicone
  around each plug keeps rain out. The box mounts glands-down, so these holes are in a side
  wall.
- **SETUP button**: the lid has a guide tube over SW1 with a printed plunger
  (`layout="button"`, one per box, printed upright). The plunger rests 0.6 mm above the
  6 x 6 x 5 mm tact switch, and its collar keeps it captive: drop it into the tube from inside
  before closing the lid. The optional `layout="boot"` cup (print in TPU 95A) presses over the
  14 mm ring on the lid to keep water out; press through it.
- Each `.scad` file has a `PARTS` list that draws the board's tall parts as
  ghost boxes in the preview (`png/`), so a fit check is just F5 in OpenSCAD.

Gland plan (holes are metric thread + 0.3 mm):

| Box | Hole | Cables |
|---|---|---|
| Outdoor | M20 | 3 pressure transducers (J3-J5), multi-hole insert |
| Outdoor | M20 | 4 thermistors (J6, J7, J9, J10), multi-hole insert |
| Outdoor | M16 x 3 | SHT30 probe (J8), 24 VAC (J1), Y + O/B (J2) |
| Outdoor | 9 mm x 3, left wall | CT clamp plugs (J11-J13) |
| Indoor | M20 | DS18B20 supply + return (J3, J4), 2-hole insert |
| Indoor | M16 | Y W G O/B thermostat cable (J2) |
| Indoor | M20 | SDP810 static-pressure sensors (J8-J10), 3-hole insert |
| Indoor | M20 | SHT45 return + supply probes (J6, J7), 2-hole insert |
| Indoor | M16 | 24 VAC (J1) |
| Indoor | M16, right wall | main I2C bus (J5), blanking plug until used |
| Both | M12 | breather vent (ePTFE membrane), stops the box pumping in humid air as it heats and cools |
| Both | 6.6 mm, top wall | SMA bulkhead for the LoRa antenna |

### Printing

- **Material: ASA** for anything outdoors or in an attic. PETG softens around
  75-80 °C, which a box in Mesa sun can reach; PLA will warp. White or light
  grey runs much cooler than dark colours.
- 0.2 mm layers, 7+ perimeters (the 3.2 mm walls end up solid), 40 % gyroid,
  enclosure or draft shield for ASA. Every part fits a 180 x 180 bed. Put the
  base, lid and bars on separate plates (in Bambu Studio: one part per plate, or
  Arrange with "Add plates") - a base and lid together don't fit one plate.
- Base prints back-down; the lid's `layout="lid"` export is already flipped
  (groove up). Shield parts print as exported, outer rim down, no supports.

### Hardware per box

- 12x M3 heat-set inserts (M3 x 5.7, 4.0 mm hole)
- 4x M3 x 6 stainless screws (board), 4x M3 x 10 stainless screws (lid),
  4x M3 x 10 countersunk stainless screws (mounting bars)
- Glands as above (UV-stabilised nylon, IP68), one M12 x 1.5 vent
- SMA female bulkhead + u.FL (IPEX MHF1) pigtail, 10-15 cm; 915 MHz whip antenna
- Outdoor: three 9 mm blanking grommets for unused CT openings
- ~600 mm of 1.75 mm TPU filament or 2 mm silicone cord for the gasket
- Shield: 3x M4 x 100 threaded rod (or screws), nuts, washers; one M16 gland

### Regenerating

```
openscad -D "layout=\"base\"" -o stl/outdoor_base.stl outdoor_box.scad
openscad -D "layout=\"lid\""  -o stl/outdoor_lid.stl  outdoor_box.scad
openscad -D "layout=\"bar\""  -o stl/outdoor_bar.stl  outdoor_box.scad
openscad -D "layout=\"button\"" -o stl/outdoor_button.stl outdoor_box.scad
openscad -D "layout=\"boot\""   -o stl/setup_boot.stl     outdoor_box.scad
openscad -D "layout=\"base\"" -o stl/indoor_base.stl  indoor_box.scad
openscad -D "layout=\"lid\""  -o stl/indoor_lid.stl   indoor_box.scad
openscad -D "layout=\"bar\""  -o stl/indoor_bar.stl   indoor_box.scad
openscad -D "layout=\"button\"" -o stl/indoor_button.stl indoor_box.scad
openscad -o png/outdoor_assembly.png --imgsize=1200,900 outdoor_box.scad
openscad -o png/indoor_assembly.png  --imgsize=1200,900 indoor_box.scad
openscad -D "part=\"louvre\"" -o stl/sht30_shield_louvre.stl sht30_shield.scad
openscad -D "part=\"cap\""    -o stl/sht30_shield_cap.stl    sht30_shield.scad
openscad -D "board=\"outdoor\"" -o stl/outdoor_plate.stl mount_plate.scad
openscad -D "board=\"indoor\""  -o stl/indoor_plate.stl  mount_plate.scad
```

On Windows the binary is `"C:\Program Files\OpenSCAD\openscad.exe"`.

## Test plates (no enclosure)

`mount_plate.scad` makes flat plates for testing the bare boards inside
equipment, such as the electrical compartment of a package unit:

| File | Plate |
|---|---|
| `stl/outdoor_plate.stl` | 132 x 132 x 4 mm |
| `stl/indoor_plate.stl` | 132 x 120 x 4 mm |

- The board sits on four 8 mm standoffs (M3 heat-set inserts, M3 x 6 screws),
  so its solder side is 12 mm off the sheet metal.
- Four corner slots take 1/4 in self-tapping sheet-metal screws (shanks up to
  #14 / 1/4 in). The slots point at the centre, so the plate can shift a few
  mm to land on solid metal. The 16 mm border leaves room for a 1/4 in nut
  driver beside the board.
- Pairs of cable-tie slots along the wire edges for strain relief.
- Print in ASA, flat side down, no supports.

In the cabinet: kill power at the disconnect first, keep the plate away from
the line-voltage terminals, contactor and capacitor, and route the 24 VAC and
sensor wires away from line-voltage wiring. Expect weak WiFi with the door
closed: the cabinet is sheet metal and the ESP32's antenna is on the board.

## Mounting in Mesa

- **Shade**: put the outdoor box on the north or east side, out of afternoon
  sun, or under a small sunshade. Summer air reaches 115-118 °F and a box in direct
  sun can run 30-40 °F hotter inside; the ESP32 is rated to 85 °C (185 °F).
- **Antenna**: the ESP32's PCB antenna is at the board's upper-left corner,
  overhanging the left edge. Plastic doesn't block it, sheet metal does. Mount
  the outdoor box on the wall or a post beside the condenser, not on its
  cabinet; if it has to go on the cabinet, stand it off at least 25 mm and keep
  the left side clear. Indoors, mount outside the air handler cabinet, not in a
  plenum, with the left side away from ducts.
- **Air sensor**: the SHT30 probe goes in the printed louvre shield, not in the
  box, mounted in shade at least 30 cm from the condenser's discharge air.
  A probe in sun or in the fan's exhaust reads high and skews the
  condenser-to-ambient check.
- Drip loops on every cable below the glands.
