"""Parametric shell v2 for the Claude Pet (Freenove FNK0104B) — FreeCAD headless.

Run:  /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd case/shell_v2.py
Out:  case/export/{frame_v2,back_v2,gauge_v2}.stl + case/export/claude_pet_shell_v2.FCStd

v2 — 2026-08-07, after the first print revealed the mounting holes off:
  * Hole grid RE-MEASURED with calipers on the bare board:
    77.18 x 41.50 center-to-center (v1 used 74.95 x 39.59 — >2mm off on both
    axes, which is exactly why the printed holes looked blocked/misaligned).
  * Fastening switched from self-tap pilots to M3 HEAT-SET INSERTS in the
    back bosses (owner's call). Front countersunk M3 machine screws
    (flat head, M3x14 or M3x16) ride the guide standoffs, pass the PCB
    holes, and thread into the inserts.
  * PCB seat thickness measured 1.69 (owner: "metal where the thing sits").
  * New GAUGE part: a 1.2mm plate the exact board footprint with the hole
    grid — print it first (minutes, not hours), lay the PCB on it flush at
    the edges, and confirm every hole lines up BEFORE printing the shell.

The stand from v1 is unchanged and still fits: outer dims and total depth
are identical, so do not reprint it (case/shell.py remains the v1 record).

Cross-check note: the owner also measured 2.43 board-edge -> hole-edge.
With PCB_HOLE_D 3.18 that puts centers 4.02 from the edge, implying a long
pitch of 85.95 - 2*4.02 = 77.91, i.e. 0.73mm off the direct 77.18 pitch
reading (width side disagrees by ~1.4mm). The direct center-to-center
reading wins; the grid is centered on the board outline. The gauge print
exists to catch exactly this class of residual error cheaply.

Print orientations (unchanged from v1): frame FACE DOWN, back OUTER FACE
DOWN, gauge flat. All support-free.
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
STACK_H = MAX_BACK - 3.2   # ≈7.46, glass top → PCB underside (derived, v1 note)
HOLE_PITCH_L = 77.18  # MEASURED 2026-08-07 — center-to-center, long axis
HOLE_PITCH_W = 41.50  # MEASURED 2026-08-07 — center-to-center, short axis
EDGE_TO_HOLE = 2.43   # MEASURED 2026-08-07 — board edge → hole edge (cross-check)
PCB_HOLE_D = 3.18    # mounting holes ≈1/8in — standard M3
PCB_T = 1.69         # MEASURED 2026-08-07 — seat thickness at the mounts
USB_W = 9.10         # connector width
USB_GAP_SHORT = 19.5  # bottom-edge gap on the BOOT side (far side: 22.39)

# ---- assumed / eyeballed (params to refine) ----
CLR = 0.30           # board-to-shell clearance each side
WALL = 2.40
FACE_T = 2.00        # front bezel plate thickness
LIP = 1.20           # bezel overlap onto the glass border
AIR_GAP = 2.3        # air behind the tallest back component
BACK_T = 2.00
CORNER_R = 3.0
CHAMFER = 0.6        # elephant-foot chamfer on each part's bed face
WIN_R = 1.5          # window corner radius
BOSS_D = 8.0         # up from 7.0: heat-set inserts need more meat
BOSS_GAP = 0.3       # bosses stop short of the PCB plane (never crush glass)
INSERT_D = 4.0       # M3 heat-set insert bore (Ruthex/CNC-Kitchen spec)
INSERT_DEPTH = 6.8   # fits M3 x 5.7 inserts +1mm; leaves ~0.4 outer skin.
                     # Press inserts FLUSH with the boss top or a hair below
                     # so they can never prop the PCB off its seat.
SCREW_D = 3.6        # face/standoff guide bore for M3 machine screws
                     # (+0.2 over v1: slop budget for the pitch cross-check
                     # discrepancy noted above)
CSK_D = 6.5          # countersink for M3 flat head (90 deg)
STANDOFF_D = 7.0     # front screw-guide standoffs (D-trimmed at the glass)
USB_SLOT_W = 14.5    # centered; covers the ±1.45 offset either way
BTN_FROM_EDGE = 6.0  # BOOT/RESET pokehole centers from bottom board edge
BTN_FROM_MID = 12.0  # ±x from centerline
BTN_HOLE_D = 6.0
LED_D = 9.0          # WS2812 glow window
LED_OFF_Y = 2.0      # LED sits slightly above board center (photo)
MIC_D = 3.5          # front mic hole — LOW CONFIDENCE position (v1 note)
MIC_OFF_X = 6.0
MIC_FROM_TOP = 4.5
RELIEF_INSET = 1.2   # long-wall relief for edge-mounted JST connectors
GAUGE_T = 1.2        # alignment-gauge plate thickness
GAUGE_HOLE_D = 3.2   # gauge holes: look-through fit for the 3.18 PCB holes

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

# v1 stand compatibility: the stand's pocket was cut for v1's OUT_W x
# TOTAL_DEPTH. Board dims and the depth stack are unchanged in v2, so these
# must still equal v1's values — if a future edit changes them, reprint the
# stand too (this assert is the reminder).
assert abs(OUT_W - 56.39) < 1e-6 and abs(TOTAL_DEPTH - 16.96) < 1e-6, \
    "outer envelope changed — v1 stand no longer fits, reprint it"


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

# long-wall relief: side-entry JSTs may overhang the PCB edge
for x0 in (WALL - RELIEF_INSET, OUT_W - WALL):
    relief = Part.makeBox(RELIEF_INSET + 0.1, 66.0,
                          SHELL_DEPTH - (PCB_BOTTOM_Z - 0.5) + 0.1,
                          App.Vector(x0, (OUT_L - 66.0) / 2,
                                     PCB_BOTTOM_Z - 0.5))
    frame = frame.cut(relief)

# USB notch: through the bottom wall AND onward through the back-cover edge
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
        # heat-set insert bore, blind from the boss top toward the outside
        back = back.cut(cyl(INSERT_D, INSERT_DEPTH, gx, gy, BOSS_TOP_Z))
# registration lips drop 1.5mm into the cavity (unchanged from v1)
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

# ---------------- alignment gauge ----------------
# Exact board footprint, GAUGE_T thick, with the hole grid. Print flat,
# rest the bare PCB on it with the edges flush, and look through the holes:
# every hole must show clear daylight before the shell is worth printing.
gauge = Part.makeBox(BOARD_W, BOARD_L, GAUGE_T)
for gx_b in ((BOARD_W - HOLE_PITCH_W) / 2, (BOARD_W + HOLE_PITCH_W) / 2):
    for gy_b in ((BOARD_L - HOLE_PITCH_L) / 2, (BOARD_L + HOLE_PITCH_L) / 2):
        gauge = gauge.cut(cyl(GAUGE_HOLE_D, GAUGE_T + 1, gx_b, gy_b, -0.5))
# corner nick marks the board's bottom-left so orientation can't flip
gauge = gauge.cut(Part.makeBox(3.0, 3.0, GAUGE_T + 1,
                               App.Vector(-0.5, -0.5, -0.5)))

# ---------------- fit proof ----------------
# A mock board at nominal position must not intersect either shell part.
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

# insert-bore sanity: the blind bore must not break through the outer face
skin = TOTAL_DEPTH - (BOSS_TOP_Z + INSERT_DEPTH)
assert skin >= 0.3, f"insert bore breaks the back skin ({skin:.2f}mm left)"
print(f"insert bore: {INSERT_DEPTH}mm deep, {skin:.2f}mm outer skin remains")

# ---------------- document + export ----------------
doc = App.newDocument("claude_pet_shell_v2")
for name, shape in (("frame_v2", frame), ("back_v2", back),
                    ("gauge_v2", gauge)):
    assert shape.isValid(), f"{name} shape invalid"
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
doc.recompute()

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export")
os.makedirs(out, exist_ok=True)
doc.saveAs(os.path.join(out, "claude_pet_shell_v2.FCStd"))
import Mesh  # noqa: E402 — FreeCAD module, importable only after init
for obj in doc.Objects:
    Mesh.export([obj], os.path.join(out, f"{obj.Name}.stl"))
    print(f"{obj.Name}: volume={obj.Shape.Volume / 1000:.1f} cm3, "
          f"bbox={obj.Shape.BoundBox.XLength:.1f}x"
          f"{obj.Shape.BoundBox.YLength:.1f}x"
          f"{obj.Shape.BoundBox.ZLength:.1f}")
print("export →", out)
