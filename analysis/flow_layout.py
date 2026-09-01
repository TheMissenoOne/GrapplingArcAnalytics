"""Deterministic left-to-right layout for a bundled path graph (Fase 4/5,
``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`` §10.5).

Extracted VERBATIM from ``scripts/render_map_prototypes.py``'s variant-13 layout so the App can
mirror it (``src/services/map/flowLayout.ts``) against a golden fixture. The prototype now
imports these names instead of carrying its own copy — variants 1-14 render byte-identically
before and after the move (proved with ``diff -r``), because nothing about the algorithm
changed, only where it lives.

Pure and I/O-free on purpose: it reads a ``BundledGraph``'s point ids and segment endpoints and
returns world coordinates. No file, no clock, no random.

**The algorithm is the App's own** ``src/components/dataVisualization/decisionFlowLayout.ts``:
multi-source BFS ranks with a visited set (cycles are real in a technique map — a back edge is
skipped, never re-ranked), deterministic ordering inside a rank, ``x`` from the rank. Its
SPLIT/MERGE routing is deliberately left out: those junctions exist there to fan a node's
outgoing edges, and here the fan is already a first-class object (``path_bundling.Point`` with
``kind='branch'|'merge'|'branch-merge'``, produced from the ACTION PREFIX). elkjs/dagre stay
out (``userDecisionFlow.ts:32-45`` — measured, the density was the problem, the layout never
was).

**Anchors are a FRAME, not five more nodes in the crowd.** They never take a grid slot, but
their row still counts in the barycentre sweeps — a state whose only neighbour is "Por Cima"
belongs next to it, and an average computed from half the neighbours is not an average
(measured: leaving them out drew 76 crossings over 42 links on the owner's bundle). They are
then bolted onto the chosen structure's vertices, on an ELLIPSE sized to the grid it frames.
"""
from __future__ import annotations

import math
from typing import Any

from analysis.path_bundling import BundledGraph

__all__ = [
    "ANCHOR_STRUCTURES",
    "DEFAULT_ANCHOR_STRUCTURE",
    "FLOW_ANCHOR_ROW_SPREAD",
    "FLOW_ANCHOR_RX_SHARE",
    "FLOW_ANCHOR_RY_SHARE",
    "FLOW_BARYCENTRE_SWEEPS",
    "FLOW_RANK_GAP",
    "FLOW_ROW_GAP",
    "PENTAGON_ANGLES",
    "anchor_units",
    "flow_layout",
    "flow_order",
    "flow_ranks",
]

FLOW_RANK_GAP = 300.0    # world units between two consecutive ranks (x)
FLOW_ROW_GAP = 130.0     # world units between two nodes inside a rank (y)
# Two sweeps, not more: measured, a longer run OSCILLATES (the median heuristic has no
# monotonicity guarantee) and lands worse — 6 sweeps cost +6 crossings on the owner's bundle.
FLOW_BARYCENTRE_SWEEPS = 2
# The anchor frame's ellipse, relative to the flow it has to contain, and how far outside the
# widest rank an anchor's row counts in the barycentre. All three MEASURED, on both bundles, not
# eyeballed — a circular frame with anchors excluded from the ordering drew 83 crossings over 42
# links (owner) and 16 over 21 (the App's mock); these draw 41 and 5. Wide and flat on purpose:
# a left-hand vertex has to clear the first rank horizontally while staying inside a readable
# vertical band, and a circle put "Por Cima"/"Por Baixo" at mid-x with every edge into them
# crossing the whole picture.
FLOW_ANCHOR_RX_SHARE = 2.00
FLOW_ANCHOR_RY_SHARE = 1.30
FLOW_ANCHOR_ROW_SPREAD = 0.35

# Owner spec 2026-08-27: the five bolted anchors are the vertices of a REGULAR PENTAGON, walked
# from the far-left vertex at 72-degree steps — neutral start at the extreme left, top start
# upper-left, the two finishes on the right (yours upper-right, the opponent's lower-right),
# bottom start lower-left. Reading left to right is then reading start -> finish. Canvas
# convention: y grows DOWNWARD, so a positive maths angle becomes a negative y.
PENTAGON_ANGLES = {  # degrees, maths convention (0 = east, counter-clockwise)
    "neutral": 180.0,       # extreme left
    "top": 108.0,           # upper left
    "finish_you": 36.0,     # upper right
    "finish_opp": -36.0,    # lower right
    "bottom": -108.0,       # lower left
}

# Owner call 2026-08-31 (plan, "Layout de âncoras configurável"): now that the oriented anchors
# take a chain's END as well as its START (Fase 1b), "início à esquerda" stopped being true and
# the frame can no longer be one hardcoded constant. The pentagon becomes ONE ROW of a table of
# structures, byte-identical to what it always was so variants 1-12 do not move; the two new
# ones are the shapes the owner named. ``unified_finish`` folds the two per-actor finishes into
# a single vertex — the node is then drawn split in half by actor, because it is still two
# different athletes' finishes, only one place.
DEFAULT_ANCHOR_STRUCTURE = "pentagono"
ANCHOR_STRUCTURES: dict[str, dict[str, Any]] = {
    "pentagono": {
        "label": "Pentágono (atual)",
        "angles": PENTAGON_ANGLES,
        "unified_finish": False,
    },
    "losango": {
        # Four vertices, orientation on the VERTICAL axis (owner's own words) — neutral opens on
        # the left, the finish closes on the right, top/bottom are the poles between them.
        "label": "Losango",
        "angles": {"neutral": 180.0, "top": 90.0, "bottom": -90.0, "finish": 0.0},
        "unified_finish": True,
    },
    "triangulo": {
        # Three vertices + the neutral anchor on the middle of the left edge: neutral makes no
        # orientation claim, so it belongs where the two oriented openings meet, not on a corner
        # of its own (same reasoning as the owner's original "neutral to the centre" spec).
        "label": "Triângulo (finalização unificada)",
        "angles": {"top": 150.0, "bottom": -150.0, "finish": 0.0, "neutral": 180.0},
        "unified_finish": True,
    },
}


def anchor_units(structure: str) -> dict[str, tuple[float, float]]:
    """Unit vectors for a structure's vertices. Canvas convention: y grows DOWNWARD, so a
    positive maths angle becomes a negative y."""
    return {
        slot: (math.cos(math.radians(deg)), -math.sin(math.radians(deg)))
        for slot, deg in ANCHOR_STRUCTURES[structure]["angles"].items()
    }


def flow_ranks(points: list[str], out_of: dict[str, list[str]],
               in_deg: dict[str, int]) -> dict[str, int]:
    """``decisionFlowLayout.computeRanks``, same shape: multi-source BFS from every point with no
    incoming segment; a point already ranked is never re-queued, so a back edge (a real cycle in
    a technique map) is skipped instead of looping or re-ranking. Anything unreached still gets
    a slot at rank 0."""
    seeds = sorted(p for p in points if in_deg.get(p, 0) == 0)
    if not seeds and points:
        seeds = [sorted(points)[0]]  # fully cyclic — still needs somewhere to start
    rank: dict[str, int] = {p: 0 for p in seeds}
    queue = list(seeds)
    depth = 0
    while queue:
        nxt: list[str] = []
        for p in queue:
            for q in sorted(out_of.get(p, [])):
                if q not in rank:
                    rank[q] = depth + 1
                    nxt.append(q)
        queue = sorted(nxt)
        depth += 1
    for p in sorted(points):
        rank.setdefault(p, 0)
    return rank


def flow_order(by_rank: dict[int, list[str]], neighbours_in: dict[str, list[str]],
               neighbours_out: dict[str, list[str]], weight: dict[str, float],
               fixed_rows: dict[str, float]) -> None:
    """Median/barycentre heuristic, in place — the crossing-minimisation half of a layered
    layout. ``decisionFlowLayout`` sorts a rank by (branch order, support desc, id); there is no
    narrative branch order here, so the initial sort is (support desc, id) and the sweeps below
    then pull each node toward its neighbours' average row.

    ``fixed_rows`` are the ANCHORS: they never take a grid slot (the frame is not a lane), but
    their row still has to COUNT, because a state whose only neighbour is "Por Cima" belongs
    next to it. Leaving them out of the barycentre was measured at 76 crossings over 42 links
    on the owner's own bundle — an average computed from half the neighbours is not an average.

    Every tie breaks on the id and the sweep count is fixed, so the result is a pure function of
    the input."""
    for rank in sorted(by_rank):
        by_rank[rank].sort(key=lambda p: (-weight.get(p, 0.0), p))
    row_of: dict[str, float] = {p: float(i) for rank in sorted(by_rank)
                                for i, p in enumerate(by_rank[rank])}
    # centre each rank on 0 so a fixed anchor row (also centred on 0) is comparable to it
    for rank in sorted(by_rank):
        span = (len(by_rank[rank]) - 1) / 2.0
        for p in by_rank[rank]:
            row_of[p] -= span
    row_of.update(fixed_rows)

    for sweep in range(FLOW_BARYCENTRE_SWEEPS):
        ranks = sorted(by_rank) if sweep % 2 == 0 else sorted(by_rank, reverse=True)
        side = neighbours_in if sweep % 2 == 0 else neighbours_out
        other = neighbours_out if sweep % 2 == 0 else neighbours_in
        for rank in ranks:
            members = by_rank[rank]
            bary: dict[str, float] = {}
            for p in members:
                rows = sorted(row_of[q] for q in side.get(p, []) if q in row_of)
                if not rows:  # a source (or a sink) has neighbours on the OTHER side only
                    rows = sorted(row_of[q] for q in other.get(p, []) if q in row_of)
                bary[p] = (sum(rows) / len(rows)) if rows else row_of[p]
            members.sort(key=lambda p: (bary[p], -weight.get(p, 0.0), p))
            span = (len(members) - 1) / 2.0
            for i, p in enumerate(members):
                row_of[p] = i - span


def flow_layout(bundled: BundledGraph, *, structure: str, anchor_slots: dict[str, str],
                weight: dict[str, float]) -> dict[str, tuple[float, float]]:
    """Point id -> world (x, y). Free points land on a rank/row grid (flow reads left to right,
    the same direction the owner's map has always read); the ANCHORS are then bolted onto the
    chosen structure's vertices, on an ellipse sized to the grid it has to frame — so the frame
    contains the map instead of being five more nodes in the crowd, and an arrival at an anchor
    never has to cross the whole picture to reach it (the starts sit on the left arc, the
    finishes on the right, which is where the ranks already put their neighbours)."""
    ids = [p.id for p in bundled.points]
    out_of: dict[str, list[str]] = {}
    in_of: dict[str, list[str]] = {}
    for seg in bundled.segments:
        out_of.setdefault(seg.from_point, []).append(seg.to_point)
        in_of.setdefault(seg.to_point, []).append(seg.from_point)
    in_deg = {p: len(in_of.get(p, [])) for p in ids}

    rank = flow_ranks(ids, out_of, in_deg)
    free = [p for p in ids if p not in anchor_slots]
    by_rank: dict[int, list[str]] = {}
    for p in sorted(free):
        by_rank.setdefault(rank[p], []).append(p)

    units = anchor_units(structure)
    widest = max((len(v) for v in by_rank.values()), default=1)
    # The anchors' rows, in the same centred-on-0 units the grid uses, so the sweeps can see
    # them. `FLOW_ANCHOR_ROW_SPREAD` is how far outside the widest rank the frame sits.
    fixed_rows = {p: units[slot][1] * (widest / 2.0) * FLOW_ANCHOR_ROW_SPREAD
                  for p, slot in sorted(anchor_slots.items())}
    flow_order(by_rank, in_of, out_of, weight, fixed_rows)

    pos: dict[str, tuple[float, float]] = {}
    for r in sorted(by_rank):
        members = by_rank[r]
        span = (len(members) - 1) / 2.0
        for i, p in enumerate(members):
            pos[p] = (r * FLOW_RANK_GAP, (i - span) * FLOW_ROW_GAP)

    if pos:
        xs = [x for x, _ in pos.values()]
        ys = [y for _, y in pos.values()]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        half_w, half_h = (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2
    else:  # every point is an anchor (a bundle with nothing but generic openings)
        cx = cy = half_w = half_h = 0.0
    # The frame is an ELLIPSE around the flow, not a circle: a left-hand vertex has to clear the
    # first rank horizontally (hence the wide rx) while staying inside a readable vertical band
    # (hence the tight ry) — a circle put "Por Cima"/"Por Baixo" at mid-x and 480 units up, and
    # every edge into them then crossed the whole picture.
    rx = max(half_w * FLOW_ANCHOR_RX_SHARE, FLOW_RANK_GAP)
    ry = max(half_h * FLOW_ANCHOR_RY_SHARE, FLOW_ROW_GAP)
    for p, slot in sorted(anchor_slots.items()):
        ux, uy = units[slot]
        pos[p] = (cx + rx * ux, cy + ry * uy)
    return pos
