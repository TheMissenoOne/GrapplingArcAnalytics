"""Parse GrappleMap.txt into Python dataclasses + networkx DiGraph.

Format (discovered from source):
  - Lines starting with 4 spaces → base62-encoded frame data
  - Other lines → metadata (name, tags:, properties:, ref:, todo:, note:)
  - Single-frame entries = positions (nodes)
  - Multi-frame entries = transitions (edges) between positions

Encoding (from src/persistence.cpp):
  - Base62 charset: a-z (0-25), A-Z (26-51), 0-9 (52-61)
  - Each coordinate: 2 chars → (d0*62 + d1) / 1000.0
  - x and z have offset: coord - 2.0
  - 23 joints per player, 2 players → 276 chars per frame (4 lines × 69 chars)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx
import numpy as np

# ─── joint definitions (from src/players.hpp) ────────────────────────────────

JOINTS = [
    "LeftToe", "RightToe", "LeftHeel", "RightHeel",
    "LeftAnkle", "RightAnkle", "LeftKnee", "RightKnee",
    "LeftHip", "RightHip", "LeftShoulder", "RightShoulder",
    "LeftElbow", "RightElbow", "LeftWrist", "RightWrist",
    "LeftHand", "RightHand", "LeftFingers", "RightFingers",
    "Core", "Neck", "Head",
]
JOINT_COUNT = len(JOINTS)  # 23
PLAYER_COUNT = 2

# ─── base62 decode ───────────────────────────────────────────────────────────

_BASE62 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_B62 = {c: i for i, c in enumerate(_BASE62)}


def _decode_frame(lines: list[str]) -> np.ndarray:
    """Decode one frame from 4 indented lines → shape (2, 23, 3) float32."""
    raw = "".join("".join(ln.split()) for ln in lines)
    idx = 0

    def g(offset: float = 0.0) -> float:
        nonlocal idx
        val = float(_B62[raw[idx]] * 62 + _B62[raw[idx + 1]]) / 1000.0
        idx += 2
        return val + offset

    out = np.zeros((PLAYER_COUNT, JOINT_COUNT, 3), dtype=np.float32)
    for p in range(PLAYER_COUNT):
        for j in range(JOINT_COUNT):
            out[p, j, 0] = g(-2.0)  # x
            out[p, j, 1] = g(0.0)   # y
            out[p, j, 2] = g(-2.0)  # z
    return out


# ─── dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class GMapPosition:
    name: str
    tags: list[str]
    joints: np.ndarray  # (2, 23, 3) float32


@dataclass
class GMapTransition:
    name: str
    tags: list[str]
    properties: list[str]   # e.g. ["top"] or ["bottom"]
    frames: np.ndarray      # (n_frames, 2, 23, 3) float32
    from_pos: str = ""      # filled in after graph construction
    to_pos: str = ""


@dataclass
class GMapGraph:
    positions: dict[str, GMapPosition] = field(default_factory=dict)
    transitions: list[GMapTransition] = field(default_factory=list)
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)


# ─── parser ──────────────────────────────────────────────────────────────────

def _is_data_line(line: str) -> bool:
    return line.startswith("    ") and line.strip() != ""


def _parse_entries(path: Path) -> list[dict]:  # type: ignore[type-arg]
    """Read GrappleMap.txt → list of raw entry dicts."""
    entries: list[dict] = []  # type: ignore[type-arg]
    current: dict = {}  # type: ignore[type-arg]
    data_lines: list[str] = []
    frame_groups: list[list[str]] = []

    def _flush() -> None:
        nonlocal data_lines
        if data_lines:
            frame_groups.append(data_lines[:])
            data_lines = []

    def _commit() -> None:
        if current:
            _flush()
            current["frames"] = [_decode_frame(g) for g in frame_groups]
            entries.append(dict(current))

    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")

            if _is_data_line(line):
                data_lines.append(line)
                # each frame = 4 lines
                if len(data_lines) == 4:
                    _flush()
            else:
                text = line.strip()
                if not text:
                    continue

                if text.startswith("tags:"):
                    current["tags"] = text[5:].split()
                elif text.startswith("properties:"):
                    current["properties"] = text[11:].split()
                elif text.startswith(("ref:", "todo:", "note:")):
                    pass  # skip metadata we don't need
                else:
                    # New name line — commit previous entry
                    _commit()
                    current = {"name": text.replace("\\n", " ").strip()}
                    data_lines = []
                    frame_groups = []

        _commit()

    return entries


def _match_position(
    frame: np.ndarray,
    positions: dict[str, GMapPosition],
) -> str:
    """Find the position whose joint data is nearest (L2) to frame."""
    best_key = ""
    best_dist = float("inf")
    for key, pos in positions.items():
        dist = float(np.sum((pos.joints - frame) ** 2))
        if dist < best_dist:
            best_dist = dist
            best_key = key
    return best_key


def parse_grapplemap(path: Path | str) -> GMapGraph:
    """Parse GrappleMap.txt → GMapGraph."""
    path = Path(path)
    entries = _parse_entries(path)

    gmap = GMapGraph()
    transitions_raw: list[dict] = []  # type: ignore[type-arg]

    # First pass: collect positions (single-frame) and transitions (multi-frame)
    for entry in entries:
        if not entry.get("frames"):
            continue
        name = entry.get("name", "")
        tags = entry.get("tags", [])
        props = entry.get("properties", [])
        frames: list[np.ndarray] = entry["frames"]

        if len(frames) == 1 and tags and not props:
            # Position node
            key = name.lower().strip()
            gmap.positions[key] = GMapPosition(
                name=name,
                tags=tags,
                joints=frames[0],
            )
        elif frames:
            # Transition sequence
            transitions_raw.append(entry)

    # Second pass: resolve transition connectivity
    for entry in transitions_raw:
        frames = entry["frames"]
        if len(frames) < 2:
            continue
        first = frames[0]
        last = frames[-1]
        from_key = _match_position(first, gmap.positions)
        to_key = _match_position(last, gmap.positions)

        trans = GMapTransition(
            name=entry.get("name", ""),
            tags=entry.get("tags", []),
            properties=entry.get("properties", []),
            frames=np.stack(frames),  # (n, 2, 23, 3)
            from_pos=from_key,
            to_pos=to_key,
        )
        gmap.transitions.append(trans)

        # Add nodes + edge to DiGraph
        gmap.graph.add_node(from_key)
        gmap.graph.add_node(to_key)
        gmap.graph.add_edge(
            from_key,
            to_key,
            name=trans.name,
            tags=trans.tags,
            properties=trans.properties,
        )

    # Also add all position nodes (some may have no transitions)
    for key in gmap.positions:
        gmap.graph.add_node(key, **{
            "name": gmap.positions[key].name,
            "tags": gmap.positions[key].tags,
        })

    return gmap


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/GrappleMap.txt"
    print(f"Parsing {db_path} …")
    gmap = parse_grapplemap(db_path)
    print(f"  Positions : {len(gmap.positions)}")
    print(f"  Transitions: {len(gmap.transitions)}")
    print(f"  Graph nodes: {gmap.graph.number_of_nodes()}")
    print(f"  Graph edges: {gmap.graph.number_of_edges()}")
    sample = list(gmap.positions.values())[:3]
    for p in sample:
        print(f"  → {p.name!r:40s}  tags={p.tags[:3]}")
