"""Render the shell STLs to a shaded PNG — FreeCAD headless (bundled mpl).

Run:  /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd case/render.py
Out:  docs/assets/shell-render.png

Layout: frame (face up), back (bosses up), stand — the three parts as they
come off the printer, lit from the upper left.
"""

import os

import Mesh
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "docs", "assets", "shell-render.png")

LIGHT = np.array([-0.4, -0.3, 0.85])
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def load(name):
    m = Mesh.Mesh(os.path.join(HERE, "export", f"{name}.stl"))
    pts, faces = m.Topology
    return np.array([[p.x, p.y, p.z] for p in pts]), np.array(faces)


def flip_x(verts):
    """Rotate 180° about the x axis (show the bed face upward)."""
    v = verts.copy()
    v[:, 1] *= -1
    v[:, 2] *= -1
    return v


def spin_z(verts):
    """Rotate 180° about the z axis (face the stand's pocket forward)."""
    v = verts.copy()
    v[:, 0] *= -1
    v[:, 1] *= -1
    return v


def place(verts, dx, dy):
    v = verts.copy()
    v[:, 0] += dx - v[:, 0].min()
    v[:, 1] += dy - v[:, 1].min()
    v[:, 2] -= v[:, 2].min()
    return v


def shade(verts, faces, base):
    tri = verts[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    lum = 0.35 + 0.65 * np.clip(n @ LIGHT, 0, 1)
    return np.clip(np.array(base)[None, :] * lum[:, None], 0, 1)


fig = plt.figure(figsize=(12, 6.4), dpi=150)
ax = fig.add_subplot(111, projection="3d")

parts = (
    ("frame", flip_x(load("frame")[0]), load("frame")[1], (0.36, 0.54, 0.86), 0, 0),
    ("back", flip_x(load("back")[0]), load("back")[1], (0.35, 0.72, 0.63), 68, 0),
    ("stand", spin_z(load("stand")[0]), load("stand")[1], (0.85, 0.62, 0.35), 136, 10),
)
for name, verts, faces, color, dx, dy in parts:
    v = place(verts, dx, dy)
    coll = Poly3DCollection(v[faces], facecolors=shade(v, faces, color),
                            edgecolors="none")
    ax.add_collection3d(coll)
    ax.text(v[:, 0].mean(), -16, 0, name, ha="center",
            fontsize=11, color="0.25")

ax.set_xlim(0, 208)
ax.set_ylim(-22, 100)
ax.set_zlim(0, 55)
ax.set_box_aspect((208, 122, 55))
ax.view_init(elev=52, azim=-88)
ax.set_axis_off()
ax.set_title("claude-pet shell — printed parts, bed face up",
             fontsize=12, color="0.2")
fig.tight_layout()
os.makedirs(os.path.dirname(OUT), exist_ok=True)
fig.savefig(OUT, bbox_inches="tight", facecolor="white")
print("render →", os.path.abspath(OUT))
