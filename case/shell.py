"""Parametric shell for the Claude Pet (Freenove FNK0104B) — FreeCAD headless.

Run:  /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd case/shell.py
Out:  case/export/{frame,back,stand}.stl + case/export/claude_pet_shell.FCStd

Three printed parts:
  frame — front bezel + full-depth side walls; the glass shows through the
          window, four countersunk M3 self-tappers enter from the front.
  back  — flat cover whose four bosses rise to the PCB underside; the same
          front screws pass through the PCB holes and bite into the bosses,
          clamping board and shell in one go. Carries the WS2812 glow window
          and BOOT/RESET pokeholes.
  stand — separate wedge dock; the closed shell drops into its slot. Separate
          on purpose: angle experiments don't cost a shell reprint.

Portrait orientation: USB-C edge faces down, cable exits through the dock.

MEASURED 2026-08-07 (calipers): board/glass/stack/hole-grid numbers.
ASSUMED (verify with calipers before trusting): hole_d (M3 guess), pcb_t,
usb/button/mic/led positions eyeballed from photos — all parameters below.
"""

import os

import FreeCAD as App  # noqa: N813 — FreeCAD's own convention
import Part

# ---- measured ----
BOARD_L = 85.95      # long axis (portrait vertical)
BOARD_W = 50.99
GLASS_L = 69.69
GLASS_W = 50.11
STACK_H = 9.50       # glass top → PCB underside (reading A — see MAX_BACK)
MAX_BACK = 10.66     # glass top → tallest back component (measured)
# If 9.50 was actually glass→SD-cage-top, PCB underside is ~7.5 and STACK_H
# must drop; discriminator: caliper glass top → bare PCB bottom AT THE WING.
HOLE_PITCH_L = 74.95
HOLE_PITCH_W = 39.59
USB_W = 9.10         # connector width
USB_GAP_SHORT = 19.5  # bottom-edge gap on the BOOT side (far side: 22.39)
# → connector center is 1.45mm off-center toward BOOT. Which way that mirrors
# into shell x is unverified, so the slot stays centered and wide enough to
# cover both orientations; narrow to USB_W + 3 once the direction is pinned.

# ---- assumed / eyeballed (params to refine) ----
HOLE_D = 3.2         # M3 clearance in PCB (screw bites plastic boss below)
CLR = 0.30           # board-to-shell clearance each side
WALL = 2.40
FACE_T = 2.00        # front bezel plate thickness
LIP = 1.20           # bezel overlap onto the glass border
REAR_CAVITY = (MAX_BACK - STACK_H) + 2.3   # tallest component + air gap
BACK_T = 2.00
CORNER_R = 3.0
BOSS_D = 7.0
PILOT_D = 2.8        # M3 self-tap pilot
SCREW_D = 3.4
CSK_D = 6.5          # countersink head diameter
USB_SLOT_W = 14.5    # centered; covers the ±1.45 offset either way (see above)
USB_SLOT_H = 7.0
BTN_FROM_EDGE = 6.0  # BOOT/RESET pokehole centers from bottom board edge
BTN_FROM_MID = 15.0  # ... and ±x from centerline
BTN_HOLE_D = 5.0
LED_D = 8.0          # WS2812 glow window, board center (eyeballed)
MIC_D = 2.5          # front mic hole
MIC_FROM_TOP = 8.0   # from top board edge, near the top-left hole
MIC_FROM_LEFT = 5.5
STAND_ANGLE = 72.0   # degrees from horizontal; upright like a tiny monitor
STAND_SLOT_CLR = 0.4

# derived
INNER_W = BOARD_W + 2 * CLR
INNER_L = BOARD_L + 2 * CLR
OUT_W = INNER_W + 2 * WALL
OUT_L = INNER_L + 2 * WALL
SHELL_DEPTH = FACE_T + STACK_H + REAR_CAVITY          # to back-cover seat
TOTAL_DEPTH = SHELL_DEPTH + BACK_T
PCB_BOTTOM_Z = FACE_T + STACK_H                        # z of PCB underside
# hole grid centers, in shell coords (origin: outer front-bottom-left corner)
GRID_X = [(OUT_W - HOLE_PITCH_W) / 2, (OUT_W + HOLE_PITCH_W) / 2]
GRID_Y = [(OUT_L - HOLE_PITCH_L) / 2, (OUT_L + HOLE_PITCH_L) / 2]


def rounded_box(l, w, h, r):
    box = Part.makeBox(l, w, h)
    vertical = [e for e in box.Edges
                if abs(e.tangentAt(e.FirstParameter).z) > 0.99]
    return box.makeFillet(r, vertical)


def cyl(d, h, x, y, z):
    return Part.makeCylinder(d / 2, h, App.Vector(x, y, z))


# ---------------- frame ----------------
frame = rounded_box(OUT_W, OUT_L, SHELL_DEPTH, CORNER_R)
cavity = Part.makeBox(INNER_W, INNER_L, SHELL_DEPTH,
                      App.Vector(WALL, WALL, FACE_T))
frame = frame.cut(cavity)
window = Part.makeBox(GLASS_W - 2 * LIP, GLASS_L - 2 * LIP, FACE_T + 1,
                      App.Vector((OUT_W - GLASS_W) / 2 + LIP,
                                 (OUT_L - GLASS_L) / 2 + LIP, -0.5))
frame = frame.cut(window)
for gx in GRID_X:
    for gy in GRID_Y:
        frame = frame.cut(cyl(SCREW_D, FACE_T + 1, gx, gy, -0.5))
        head = Part.makeCone(CSK_D / 2, SCREW_D / 2, (CSK_D - SCREW_D) / 2,
                             App.Vector(gx, gy, 0))
        frame = frame.cut(head)
frame = frame.cut(cyl(MIC_D, FACE_T + 1,
                      WALL + CLR + MIC_FROM_LEFT,
                      OUT_L - WALL - CLR - MIC_FROM_TOP, -0.5))
usb_slot = Part.makeBox(USB_SLOT_W, WALL + 1, USB_SLOT_H,
                        App.Vector((OUT_W - USB_SLOT_W) / 2, -0.5,
                                   PCB_BOTTOM_Z - 1.0))
frame = frame.cut(usb_slot)

# ---------------- back cover ----------------
back = rounded_box(OUT_W, OUT_L, BACK_T, CORNER_R)
back.translate(App.Vector(0, 0, SHELL_DEPTH))
for gx in GRID_X:
    for gy in GRID_Y:
        boss = cyl(BOSS_D, SHELL_DEPTH - PCB_BOTTOM_Z, gx, gy, PCB_BOTTOM_Z)
        back = back.fuse(boss)
        back = back.cut(cyl(PILOT_D, SHELL_DEPTH - PCB_BOTTOM_Z + 1,
                            gx, gy, PCB_BOTTOM_Z - 0.5))
back = back.cut(cyl(LED_D, BACK_T + 1, OUT_W / 2, OUT_L / 2,
                    SHELL_DEPTH - 0.5))
for sx in (-1, 1):
    back = back.cut(cyl(BTN_HOLE_D, BACK_T + 1,
                        OUT_W / 2 + sx * BTN_FROM_MID,
                        WALL + CLR + BTN_FROM_EDGE, SHELL_DEPTH - 0.5))

# ---------------- stand ----------------
POCKET_W = OUT_W + 2 * STAND_SLOT_CLR
POCKET_T = TOTAL_DEPTH + 2 * STAND_SLOT_CLR
POCKET_DEPTH = 14.0
BASE_W = POCKET_W + 2 * 6.0
BASE_D = 46.0
BASE_H = 26.0
stand = rounded_box(BASE_W, BASE_D, BASE_H, 4.0)
slot = Part.makeBox(POCKET_W, POCKET_T, POCKET_DEPTH + BASE_H)
slot.translate(App.Vector(-POCKET_W / 2, -POCKET_T / 2, 0))
slot.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), STAND_ANGLE - 90)
slot.translate(App.Vector(BASE_W / 2, BASE_D / 2,
                          BASE_H - POCKET_DEPTH))
stand = stand.cut(slot)
# cable channel: USB cable leaves the shell's bottom edge inside the pocket
cable = Part.makeBox(16, BASE_D / 2 + 8, BASE_H)
cable.translate(App.Vector((BASE_W - 16) / 2, -4, BASE_H - POCKET_DEPTH - 12))
cable.rotate(App.Vector(BASE_W / 2, BASE_D / 2, BASE_H - POCKET_DEPTH),
             App.Vector(1, 0, 0), STAND_ANGLE - 90)
stand = stand.cut(cable)

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
