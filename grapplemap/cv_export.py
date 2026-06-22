"""Export GrappleMap joint data as CV training assets.

Outputs normalized joint arrays (.npy) and a labels.json for
downstream ViCoS-style classifier training.

Normalization:
  - Center on midpoint of LeftHip and RightHip (for each player independently)
  - Scale to unit torso length (hip-midpoint → Core distance)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from grapplemap.parser import JOINTS, GMapGraph

_J = {name: i for i, name in enumerate(JOINTS)}
_LEFT_HIP  = _J["LeftHip"]
_RIGHT_HIP = _J["RightHip"]
_CORE      = _J["Core"]


def normalize_joints(joints: np.ndarray) -> np.ndarray:
    """Normalize one frame (2, 23, 3) to be scale/translation invariant.

    Centers each player on their hip midpoint; scales by torso length.
    """
    out = joints.copy()
    for p in range(2):
        hip_mid = (joints[p, _LEFT_HIP] + joints[p, _RIGHT_HIP]) / 2.0
        torso_len = float(np.linalg.norm(joints[p, _CORE] - hip_mid))
        if torso_len < 1e-6:
            torso_len = 1.0
        out[p] = (joints[p] - hip_mid) / torso_len
    return out


def _safe_key(name: str) -> str:
    n = name.lower().strip()
    n = re.sub(r"[^a-z0-9 ]", "", n)
    n = re.sub(r"\s+", "_", n.strip())
    return n[:80]  # cap filename length


def export_cv_dataset(
    gmap: GMapGraph,
    out_dir: Path | str,
    verbose: bool = True,
) -> None:
    """Save normalized joint arrays and labels.json.

    positions/ — one .npy per position  shape (2, 23, 3)
    transitions/ — one .npy per transition  shape (n_frames, 2, 23, 3)
    labels.json — {relative_path → {name, tags, type}}
    """
    out_dir = Path(out_dir)
    pos_dir   = out_dir / "positions"
    trans_dir = out_dir / "transitions"
    pos_dir.mkdir(parents=True, exist_ok=True)
    trans_dir.mkdir(parents=True, exist_ok=True)

    labels: dict[str, dict] = {}  # type: ignore[type-arg]

    # Positions
    for name, pos in gmap.positions.items():
        norm = normalize_joints(pos.joints)
        key = _safe_key(pos.name)
        rel = f"positions/{key}.npy"
        np.save(out_dir / rel, norm.astype(np.float32))
        labels[rel] = {"name": pos.name, "tags": pos.tags, "type": "position"}

    # Transitions
    seen: dict[str, int] = {}
    for trans in gmap.transitions:
        frames = np.stack([normalize_joints(f) for f in trans.frames])
        key = _safe_key(trans.name)
        count = seen.get(key, 0)
        seen[key] = count + 1
        suffix = f"_{count}" if count else ""
        rel = f"transitions/{key}{suffix}.npy"
        np.save(out_dir / rel, frames.astype(np.float32))
        labels[rel] = {
            "name": trans.name,
            "tags": trans.tags,
            "properties": trans.properties,
            "from": trans.from_pos,
            "to": trans.to_pos,
            "type": "transition",
            "n_frames": len(trans.frames),
        }

    (out_dir / "labels.json").write_text(json.dumps(labels, indent=2))
    if verbose:
        print(f"  {len(gmap.positions)} positions → {pos_dir}")
        print(f"  {len(gmap.transitions)} transitions → {trans_dir}")
        print(f"  labels.json written ({len(labels)} entries)")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    from grapplemap.parser import parse_grapplemap

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/GrappleMap.txt"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "data/grapplemap_cv"

    print(f"Parsing {db_path} …")
    gmap = parse_grapplemap(db_path)
    print(f"Exporting CV dataset → {out_path}")
    export_cv_dataset(gmap, out_path)
    print("Done.")
