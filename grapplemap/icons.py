"""Generate stick-figure PNG icons for each GrappleMap position.

Renders a top-down (XZ plane) projection with both players visible.
Player 0 = red, Player 1 = blue.
Output: 128×128 PNG per position.
"""

from __future__ import annotations

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from analysis.names import _normalize_name
from grapplemap.parser import JOINTS, GMapGraph, GMapPosition

# ─── skeleton connectivity (joint index pairs to draw lines between) ─────────

_J = {name: i for i, name in enumerate(JOINTS)}

# (from_joint, to_joint) pairs defining skeleton limbs
_SKELETON_LINKS = [
    # spine
    ("LeftHip",  "Core"),
    ("RightHip", "Core"),
    ("Core",     "Neck"),
    ("Neck",     "Head"),
    # left leg
    ("LeftHip",    "LeftKnee"),
    ("LeftKnee",   "LeftAnkle"),
    ("LeftAnkle",  "LeftHeel"),
    ("LeftAnkle",  "LeftToe"),
    # right leg
    ("RightHip",   "RightKnee"),
    ("RightKnee",  "RightAnkle"),
    ("RightAnkle", "RightHeel"),
    ("RightAnkle", "RightToe"),
    # left arm
    ("Neck",         "LeftShoulder"),
    ("LeftShoulder", "LeftElbow"),
    ("LeftElbow",    "LeftWrist"),
    ("LeftWrist",    "LeftHand"),
    ("LeftHand",     "LeftFingers"),
    # right arm
    ("Neck",          "RightShoulder"),
    ("RightShoulder", "RightElbow"),
    ("RightElbow",    "RightWrist"),
    ("RightWrist",    "RightHand"),
    ("RightHand",     "RightFingers"),
]
_LINKS = [(_J[a], _J[b]) for a, b in _SKELETON_LINKS]

_COLORS = ["#e63946", "#457b9d"]   # player 0 red, player 1 blue
_BG     = "#1a1a2e"


def _render_frame(joints: np.ndarray, size: int = 128) -> Image.Image:
    """Render one frame (2, 23, 3) → PIL Image (top-down XZ projection)."""
    dpi = 72
    fig_size = size / dpi
    fig, ax = plt.subplots(figsize=(fig_size, fig_size), dpi=dpi)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)
    ax.set_aspect("equal")
    ax.axis("off")

    # Compute bounds across both players for tight framing
    xs = joints[:, :, 0].ravel()
    zs = joints[:, :, 2].ravel()
    cx, cz = xs.mean(), zs.mean()
    span = max(float(xs.max() - xs.min()), float(zs.max() - zs.min()), 0.8) * 0.6
    ax.set_xlim(cx - span, cx + span)
    ax.set_ylim(cz - span, cz + span)

    for p in range(2):
        color = _COLORS[p]
        pj = joints[p]  # (23, 3)
        x, z = pj[:, 0], pj[:, 2]

        # Draw limbs
        for a, b in _LINKS:
            ax.plot([x[a], x[b]], [z[a], z[b]], color=color, lw=1.2, solid_capstyle="round")

        # Draw joints (small dots)
        ax.scatter(x, z, color=color, s=4, zorder=5)

        # Highlight head
        hi = _J["Head"]
        ax.scatter(
            [x[hi]], [z[hi]], color=color, s=18, zorder=6, edgecolors="white", linewidths=0.4
        )

    plt.tight_layout(pad=0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0, facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    img = Image.open(buf).convert("RGBA")
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    return img


def render_position_icon(pos: GMapPosition, size: int = 128) -> Image.Image:
    """Render a single position → PIL Image."""
    return _render_frame(pos.joints, size=size)


def _safe_filename(name: str) -> str:
    """Convert position name to safe filename — _normalize_name + underscores."""
    return _normalize_name(name).replace(" ", "_")


def export_all_icons(
    gmap: GMapGraph,
    out_dir: Path | str,
    size: int = 128,
    verbose: bool = True,
) -> dict[str, Path]:
    """Render + save PNG icons for all positions.

    Returns mapping: normalized_name → saved path.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved: dict[str, Path] = {}
    for name, pos in gmap.positions.items():
        try:
            img = render_position_icon(pos, size=size)
            fname = _safe_filename(pos.name) + ".png"
            dest = out_dir / fname
            img.save(dest, format="PNG")
            saved[name] = dest
            if verbose:
                print(f"  ✓ {fname}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {pos.name!r}: {exc}")

    return saved


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    from grapplemap.parser import parse_grapplemap

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/GrappleMap.txt"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "data/grapplemap_icons"
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 128

    print(f"Parsing {db_path} …")
    gmap = parse_grapplemap(db_path)
    print(f"Rendering {len(gmap.positions)} icons → {out_path}")
    saved = export_all_icons(gmap, out_path, size=size)
    print(f"Done. {len(saved)} icons saved.")
