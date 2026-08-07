"""Parametric shell for the Claude Pet (Freenove FNK0104B) — FreeCAD headless.

Run:  /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd case/shell.py
Out:  case/export/{frame,back,stand}.stl + case/export/claude_pet_shell.FCStd

Three printed parts, all support-free in their stated orientations:
  frame — print FACE DOWN. Bezel + full-depth walls. Four countersunk M3
          self-tappers (flat head, x12 or x14) enter from the front, ride
          guide standoffs to the PCB holes, and bite into the back's bosses.
  back  — print OUTER FACE DOWN. Bosses seat the PCB; corner lips register
          the plate in the cavity; WS2812 glow window; BOOT/RESET pokeholes.
  stand — print BASE DOWN. 65-degree wedge dock, open cable mouth at the
          front. Separate part so angle experiments don't reprint the shell.

Clamp scheme: the screws pull the board onto the rear bosses (biased 0.3
short of the derived PCB plane so tolerance error can never crush the glass
into the bezel lip); the front standoffs guide the screws and back up the
countersinks, D-trimmed so they cannot touch the glass corners.

Portrait: USB-C edge down, cable exits through the dock mouth.

MEASURED 2026-08-07 (calipers): board, glass, hole grid, USB-C, MAX_BACK.
DERIVED: STACK_H (see note), PCB_T=1.6 standard.
EYEBALLED from photos (oversized to absorb error): button/mic/LED positions,
JST relief band. All parameters below.
"""

import os

import FreeCAD as App  # noqa: N813 — FreeCAD's own convention
import Part

# ---- measured ----
BOARD_L = 85.95      # long axis (portrait vertical)
BOARD_W = 50.99
GLASS_L = 69.69
GLASS_W = 50.11
MAX_BACK = 10.66     # glass top → tallest back component (measured)
# The earlier 9.50 "depth" was discarded (owner: ignore it — likely jaws on
# the SD cage). STACK_H is DERIVED: MAX_BACK minus a standard 3.2mm USB-C
# body, the tallest back component in the photos. Replace with a caliper
# reading of glass top → bare PCB underside AT THE WING when available.
STACK_H = MAX_BACK - 3.2   # ≈7.46, glass top → PCB underside (estimate)
HOLE_PITCH_L = 74.95
HOLE_PITCH_W = 39.59
USB_W = 9.10         # connector width
USB_GAP_SHORT = 19.5  # bottom-edge gap on the BOOT side (far side: 22.39)
# → connector center is 1.45mm off-center toward BOOT. Which way that mirrors
# into shell x is unverified, so the slot stays centered and wide enough to
# cover both orientations; narrow to USB_W + 3 once the direction is pinned.

# ---- assumed / eyeballed (params to refine) ----
PCB_T = 1.6          # standard FR4
CLR = 0.30           # board-to-shell clearance each side
WALL = 2.40
FACE_T = 2.00        # front bezel plate thickness
LIP = 1.20           # bezel overlap onto the glass border
AIR_GAP = 2.3        # air behind the tallest back component
BACK_T = 2.00
CORNER_R = 3.0
CHAMFER = 0.6        # elephant-foot chamfer on each part's bed face
WIN_R = 1.5          # window corner radius
BOSS_D = 7.0
BOSS_GAP = 0.3       # bosses stop short of the PCB plane: derived-stack
                     # error pulls the glass back at worst, never crushes it
PILOT_D = 2.8        # M3 self-tap pilot
SCREW_D = 3.4
CSK_D = 6.5          # countersink for M3 flat head (90 deg)
STANDOFF_D = 7.0     # front screw-guide standoffs (D-trimmed at the glass)
USB_SLOT_W = 14.5    # centered; covers the ±1.45 offset either way (see above)
BTN_FROM_EDGE = 6.0  # BOOT/RESET pokehole centers from bottom board edge
BTN_FROM_MID = 12.0  # ±x from centerline (±15 collided with the bosses)
BTN_HOLE_D = 6.0
LED_D = 9.0          # WS2812 glow window
LED_OFF_Y = 2.0      # LED sits slightly above board center (photo)
MIC_D = 3.5          # front mic hole — LOW CONFIDENCE position; the mic is
MIC_OFF_X = 6.0      # unused until the audio phase, so a miss only muffles.
MIC_FROM_TOP = 4.5   # on the wing, beside the top-left screw (x = grid+6)
RELIEF_INSET = 1.2   # long-wall relief for edge-mounted JST connectors
STAND_ANGLE = 65.0   # degrees from horizontal (72 was tippy under a hold)
STAND_SLOT_CLR = 0.4

# derived
INNER_W = BOARD_W + 2 * CLR
INNER_L = BOARD_L + 2 * CLR
OUT_W = INNER_W + 2 * WALL
OUT_L = INNER_L + 2 * WALL
PCB_BOTTOM_Z = FACE_T + STACK_H
PCB_TOP_Z = PCB_BOTTOM_Z - PCB_T
SHELL_DEPTH = FACE_T + MAX_BACK + AIR_GAP              # to back-cover seat
TOTAL_DEPTH = SHELL_DEPTH + BACK_T
GLASS_Y0 = (OUT_L - GLASS_L) / 2                       # glass span in y
GLASS_Y1 = GLASS_Y0 + GLASS_L
# hole grid centers, in shell coords (origin: outer front-bottom-left corner)
GRID_X = [(OUT_W - HOLE_PITCH_W) / 2, (OUT_W + HOLE_PITCH_W) / 2]
GRID_Y = [(OUT_L - HOLE_PITCH_L) / 2, (OUT_L + HOLE_PITCH_L) / 2]


def rounded_box(l, w, h, r):
    box = Part.makeBox(l, w, h)
    vertical = [e for e in box.Edges
                if abs(e.tangentAt(e.FirstParameter).z) > 0.99]
    return box.makeFillet(r, vertical)


def chamfer_at_z(solid, z_plane, size):
    """Chamfer the edge loop lying in the given z plane (bed-face edges)."""
    edges = [e for e in solid.Edges
             if all(abs(v.Point.z - z_plane) < 1e-6 for v in e.Vertexes)]
    return solid.makeChamfer(size, edges)


def cyl(d, h, x, y, z):
    return Part.makeCylinder(d / 2, h, App.Vector(x, y, z))


# ---------------- frame ----------------
frame = rounded_box(OUT_W, OUT_L, SHELL_DEPTH, CORNER_R)
frame = chamfer_at_z(frame, 0, CHAMFER)                # bed face
cavity = Part.makeBox(INNER_W, INNER_L, SHELL_DEPTH,
                      App.Vector(WALL, WALL, FACE_T))
frame = frame.cut(cavity)

# screw-guide standoffs: face → just above the PCB top, before the window
# cut so the window trims nothing it shouldn't
for gx in GRID_X:
    for gy in GRID_Y:
        frame = frame.fuse(cyl(STANDOFF_D, PCB_TOP_Z - 0.15 - FACE_T,
                               gx, gy, FACE_T))
# D-trim: no standoff material may enter the glass footprint (+0.3 margin).
# Starts a hair IN FRONT of the face's back surface — starting behind it
# left a 0.01mm skin of standoff base overlapping the display module (caught
# by the fit proof below).
glass_zone = Part.makeBox(INNER_W - 0.02, GLASS_L + 0.6,
                          SHELL_DEPTH,
                          App.Vector(WALL + 0.01, GLASS_Y0 - 0.3, FACE_T - 0.02))
frame = frame.cut(glass_zone)

window = Part.makeBox(GLASS_W - 2 * LIP, GLASS_L - 2 * LIP, FACE_T + 1,
                      App.Vector((OUT_W - GLASS_W) / 2 + LIP,
                                 GLASS_Y0 + LIP, -0.5))
win_vert = [e for e in window.Edges
            if abs(e.tangentAt(e.FirstParameter).z) > 0.99]
frame = frame.cut(window.makeFillet(WIN_R, win_vert))

for gx in GRID_X:
    for gy in GRID_Y:
        frame = frame.cut(cyl(SCREW_D, PCB_TOP_Z + 1, gx, gy, -0.5))
        head = Part.makeCone(CSK_D / 2, SCREW_D / 2, (CSK_D - SCREW_D) / 2,
                             App.Vector(gx, gy, 0))
        frame = frame.cut(head)

frame = frame.cut(cyl(MIC_D, FACE_T + 1,
                      GRID_X[0] + MIC_OFF_X,
                      OUT_L - WALL - CLR - MIC_FROM_TOP, -0.5))

# long-wall relief: side-entry JSTs (UART/BAT one side, IO/I2C/SPEAKER the
# other) may overhang the PCB edge; give them 1.2mm without thinning the
# whole wall
for x0 in (WALL - RELIEF_INSET, OUT_W - WALL):
    relief = Part.makeBox(RELIEF_INSET + 0.1, 66.0,
                          SHELL_DEPTH - (PCB_BOTTOM_Z - 0.5) + 0.1,
                          App.Vector(x0, (OUT_L - 66.0) / 2,
                                     PCB_BOTTOM_Z - 0.5))
    frame = frame.cut(relief)

# USB notch: through the bottom wall AND onward through the back-cover edge
# (cut again below) so a chunky cable overmold has ~8.5mm to pass through
usb_notch = Part.makeBox(USB_SLOT_W, WALL + 1.1, TOTAL_DEPTH,
                         App.Vector((OUT_W - USB_SLOT_W) / 2, -0.5,
                                    PCB_BOTTOM_Z - 1.0))
frame = frame.cut(usb_notch)

# ---------------- back cover ----------------
back = rounded_box(OUT_W, OUT_L, BACK_T, CORNER_R)
back.translate(App.Vector(0, 0, SHELL_DEPTH))
back = chamfer_at_z(back, TOTAL_DEPTH, CHAMFER)        # bed face
BOSS_TOP_Z = PCB_BOTTOM_Z + BOSS_GAP
for gx in GRID_X:
    for gy in GRID_Y:
        back = back.fuse(cyl(BOSS_D, SHELL_DEPTH - BOSS_TOP_Z, gx, gy,
                             BOSS_TOP_Z))
        back = back.cut(cyl(PILOT_D, SHELL_DEPTH - BOSS_TOP_Z + 1.5,
                            gx, gy, BOSS_TOP_Z - 0.5))
# registration lips drop 1.5mm into the cavity: one on the top wall, two
# flanking the USB notch on the bottom wall (long walls are relieved, so
# lips there would register against air)
for x0, x1, y0 in ((14.0, 42.0, OUT_L - WALL - 1.35),
                   (5.0, 18.0, WALL + 0.15),
                   (OUT_W - 18.0, OUT_W - 5.0, WALL + 0.15)):
    lip = Part.makeBox(x1 - x0, 1.2, 1.5,
                       App.Vector(x0, y0, SHELL_DEPTH - 1.5))
    back = back.fuse(lip)
back = back.cut(usb_notch)
back = back.cut(cyl(LED_D, BACK_T + 1, OUT_W / 2, OUT_L / 2 + LED_OFF_Y,
                    SHELL_DEPTH - 0.5))
for sx in (-1, 1):
    back = back.cut(cyl(BTN_HOLE_D, BACK_T + 1,
                        OUT_W / 2 + sx * BTN_FROM_MID,
                        WALL + CLR + BTN_FROM_EDGE, SHELL_DEPTH - 0.5))

# ---------------- stand ----------------
POCKET_W = OUT_W + 2 * STAND_SLOT_CLR
POCKET_T = TOTAL_DEPTH + 2 * STAND_SLOT_CLR
POCKET_DEPTH = 16.0
POCKET_Y = 24.0          # pocket center from the front face; long side aft
BASE_W = POCKET_W + 12.0
BASE_D = 68.0
BASE_H = 24.0
stand = rounded_box(BASE_W, BASE_D, BASE_H, 4.0)
stand = chamfer_at_z(stand, 0, 0.5)
slot = Part.makeBox(POCKET_W, POCKET_T, POCKET_DEPTH + 40)
slot.translate(App.Vector(-POCKET_W / 2, -POCKET_T / 2, 0))
slot.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 90 - STAND_ANGLE)
slot.translate(App.Vector(BASE_W / 2, POCKET_Y, BASE_H - POCKET_DEPTH))
stand = stand.cut(slot)
# open cable mouth: vertical slot from the pocket floor out the front face —
# no tunnel, no bridging, the cable drops in from above
mouth = Part.makeBox(16.0, POCKET_Y + 0.5, BASE_H,
                     App.Vector((BASE_W - 16.0) / 2, -0.5, 6.0))
stand = stand.cut(mouth)

# ---------------- fit proof ----------------
# A mock board at nominal position must not intersect either shell part.
# This runs on every build, so a future parameter tweak that re-introduces a
# collision fails loudly here instead of on the printer.
BX, BY = WALL + CLR, WALL + CLR                    # board front-bottom-left
mock = Part.makeBox(BOARD_W, BOARD_L, PCB_T,       # the PCB itself
                    App.Vector(BX, BY, PCB_TOP_Z))
mock = mock.fuse(Part.makeBox(GLASS_W, GLASS_L,    # display module block
                              PCB_TOP_Z - FACE_T,
                              App.Vector((OUT_W - GLASS_W) / 2, GLASS_Y0,
                                         FACE_T)))
mock = mock.fuse(Part.makeBox(USB_W, 8.0, 3.2,     # USB-C body +1mm proud
                              App.Vector((OUT_W - USB_W) / 2, BY - 1.0,
                                         PCB_BOTTOM_Z)))
env = Part.makeBox(BOARD_W - 6, BOARD_L - 6,       # back components up to
                   MAX_BACK - STACK_H,             # the measured envelope,
                   App.Vector(BX + 3, BY + 3, PCB_BOTTOM_Z))
for gx in GRID_X:                                  # minus mounting-hole
    for gy in GRID_Y:                              # keep-outs (boss zones)
        env = env.cut(cyl(11.0, MAX_BACK - STACK_H + 1, gx, gy,
                          PCB_BOTTOM_Z - 0.5))
mock = mock.fuse(env)
for jx in (BX - 0.6, BX + BOARD_W):                # JST sockets overhanging
    mock = mock.fuse(Part.makeBox(0.6, 60.0, 3.0,  # the long edges by 0.6
                                  App.Vector(jx, (OUT_L - 60.0) / 2,
                                             PCB_BOTTOM_Z)))
for pname, pshape in (("frame", frame), ("back", back)):
    clash = pshape.common(mock).Volume
    assert clash < 1e-3, f"board collides with {pname}: {clash:.2f} mm3"
print("fit proof: mock board clears frame and back")

# ---------------- document + export ----------------
doc = App.newDocument("claude_pet_shell")
for name, shape in (("frame", frame), ("back", back), ("stand", stand)):
    assert shape.isValid(), f"{name} shape invalid"
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
doc.recompute()

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export")
os.makedirs(out, exist_ok=True)
doc.saveAs(os.path.join(out, "claude_pet_shell.FCStd"))
import Mesh  # noqa: E402 — FreeCAD module, importable only after init
for obj in doc.Objects:
    Mesh.export([obj], os.path.join(out, f"{obj.Name}.stl"))
    print(f"{obj.Name}: volume={obj.Shape.Volume / 1000:.1f} cm3, "
          f"bbox={obj.Shape.BoundBox.XLength:.1f}x"
          f"{obj.Shape.BoundBox.YLength:.1f}x"
          f"{obj.Shape.BoundBox.ZLength:.1f}")
print("export →", out)
