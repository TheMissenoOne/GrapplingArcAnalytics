"""Deterministic left-to-right layout for a bundled path graph (Fase 4/5,
``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`` §10.5).

Four stages, in this order: **rank** (multi-source BFS) -> **order** (barycentre sweeps) ->
**bend** (``_compact``, the long axis toward ``FLOW_TARGET_ASPECT`` at constant area) ->
**relax** (``_relax``, label boxes push each other apart while a decaying spring holds the
layer). The last two are the owner call of 2026-09-01 — see §10.5 of the contract doc.

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
then bolted onto the chosen structure's vertices, on an ELLIPSE sized to the grid it frames,
and the relaxation treats them as immovable — "keeping the anchor nodes fixed", owner, verbatim.
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
    "FLOW_COMPACT_MIN",
    "FLOW_LABEL_CHAR",
    "FLOW_LABEL_EM",
    "FLOW_LABEL_PAD_X",
    "FLOW_LABEL_PAD_Y",
    "FLOW_NODE_RADIUS",
    "FLOW_RANK_GAP",
    "FLOW_RELAX_MAX_BUBBLES",
    "FLOW_RELAX_PULL",
    "FLOW_RELAX_PUSH",
    "FLOW_RELAX_SLACK",
    "FLOW_RELAX_ROUNDS",
    "FLOW_ROW_GAP",
    "FLOW_TARGET_ASPECT",
    "PENTAGON_ANGLES",
    "anchor_units",
    "flow_layout",
    "flow_order",
    "flow_ranks",
    "label_half_extent",
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

# ── the label bubble, and the relaxation that separates them (owner call 2026-09-01) ────────
#
# "The layout is clustering the labels because all the states are aligned in layers." A rank is
# a COLUMN, so two neighbouring ranks share a row and their labels — which are 10x wider than
# the dot they name — overlap even though the DOTS are 300 units apart. The grid is right about
# ORDER and wrong about the only thing the eye reads. So the grid becomes the starting point,
# not the answer: every drawn label is a BOX, and a fixed number of rounds pushes overlapping
# boxes apart while a weak spring pulls each point back to its own layer slot. The layer is a
# soft constraint, the anchors are hard — they never move.
#
# It is a BOX and not a disc on purpose. A label is ~200 world units wide and ~12 tall; a disc
# big enough to hold it would reserve 200 units of VERTICAL room as well, which is the opposite
# of the second half of the same complaint ("it might be too stretched"). The separation is
# therefore the minimum translation on the axis of least penetration — the smallest move that
# makes the two readable at once.
#
# No RNG, no clock, no convergence test — a FIXED round count over a SORTED iteration, with
# +,-,*,/ and comparisons only (the single `sqrt` is in the compaction, and `math.sqrt` /
# `Math.sqrt` are both IEEE-correctly-rounded). That is what lets the TS port be bit-identical
# instead of merely close.

#: The label font size the two renderers draw INSIDE the world transform (`graphRenderRecords`
#: node label `size = 12`, `EdgeRenderer` action label `11`) — so a world-space box is
#: zoom-invariant: the fit scales positions and glyphs by the same factor. One constant for
#: both; 12 over an 11pt action label is a pixel of slack, not a second constant to drift.
FLOW_LABEL_EM = 12.0
#: `components/dataVisualization/labelLayout.AVERAGE_CHAR_RATIO` — the App's own advance-width
#: estimate for its UI face. Same number here so a bubble is the width the renderer will draw.
FLOW_LABEL_CHAR = 0.55
FLOW_LABEL_PAD_X = 14.0
FLOW_LABEL_PAD_Y = 10.0
#: A labelless point (a branch/merge dot) is still a bubble, just a small round one.
FLOW_NODE_RADIUS = 16.0
FLOW_RELAX_ROUNDS = 120
#: Share of a pair's penetration each side resolves per round. 0.5 + 0.5 = a full resolution in
#: one round when both are free; anything higher oscillates once a point is in three collisions.
FLOW_RELAX_PUSH = 0.5
#: Resolve a hair MORE than the overlap, so a separated pair lands with a visible gap instead of
#: converging asymptotically onto "touching". Without it the fixed point IS zero penetration —
#: mathematically clean, and a test asserting "no overlap" fails on 1e-9.
FLOW_RELAX_SLACK = 2.0
#: How hard the layer slot pulls back. Weak on purpose — the rank still ORDERS the reading, it
#: just stops being a line.
FLOW_RELAX_PULL = 0.06
#: Owner: "it might be too stretched." Measured on the App's mock bundle, before this existed:
#: 1200x260, aspect 4.62 (triangle) / 3.55 (diamond) / 3.38 (pentagon). The picture is bent
#: toward this aspect at CONSTANT AREA, BEFORE the relaxation, so the relaxation cleans up what
#: the bend costs. An anisotropic scale is an AFFINE map, so it cannot create or remove a single
#: edge crossing — the 41-over-42 measurement the ellipse constants were tuned on survives it.
FLOW_TARGET_ASPECT = 1.5
FLOW_COMPACT_MIN = 0.4
#: ponytail: the relaxation is O(bubbles^2) per round. Under this many it is microseconds; over
#: it, it is skipped entirely and the layout is the grid it always was. That is the ocean
#: (§12.4 of the contract doc already declares a layered layout wrong for a graph with 3 sources
#: and 139 nodes in one rank) — upgrade path is a uniform grid hash, not a bigger budget.
FLOW_RELAX_MAX_BUBBLES = 240

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
# Owner call 2026-09-01 (second pass): the TRIANGLE is the frame, everywhere — the App's Map has
# no structure switch any more, and the site's three path displays (ocean, dossier, breakdown)
# follow this default. The table stays: it is three dicts, the prototype still renders all of
# them side by side, and deleting a row would cost more than keeping it. Only the DEFAULT moved.
# The key stays `triangulo` (pt-BR, as minted) — renaming it would churn every golden for a
# spelling.
DEFAULT_ANCHOR_STRUCTURE = "triangulo"
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


def label_half_extent(chars: int, *, node: bool) -> tuple[float, float]:
    """Half-width / half-height of what a label actually occupies, in WORLD units.

    ``chars`` is the character count of the text the renderer will draw — the caller's, because
    only the caller knows the locale ("Finalização" is 11, ``finish`` is 6, and a bubble sized
    on the wrong one is a bubble sized for a picture nobody sees).

    A NODE bubble covers the dot AND the name under it (the renderers place a node label at
    ``radius + 16`` on one side or the other, so the box has to reach both ways); a SEGMENT
    bubble is the action text alone, floating at the middle of its stroke."""
    if chars <= 0:
        return (FLOW_NODE_RADIUS, FLOW_NODE_RADIUS) if node else (0.0, 0.0)
    half_w = chars * FLOW_LABEL_EM * FLOW_LABEL_CHAR / 2.0 + FLOW_LABEL_PAD_X
    if node:
        return (max(half_w, FLOW_NODE_RADIUS),
                FLOW_NODE_RADIUS + FLOW_LABEL_EM + FLOW_LABEL_PAD_Y)
    return (half_w, FLOW_LABEL_EM / 2.0 + FLOW_LABEL_PAD_Y)


#: ``(id, half_w, half_h, riders)`` — ``riders`` are the point ids the bubble's centre is the
#: mean of: one for a node, the two endpoints for a segment's action label.
_Bubble = tuple[str, float, float, tuple[str, ...]]


def _bubbles(bundled: BundledGraph, label_len: dict[str, int]) -> list[_Bubble]:
    """Every readable box on the canvas, in sorted-id order (point ids are ``s:``/``j:``,
    segment ids ``g:`` — one namespace each, so one ``label_len`` map covers both).

    Segment labels come in only while the whole picture is small enough for the O(n^2) round;
    they are the SECOND layer of the drawing, and a hairball that has to drop something drops
    that one first.

    ponytail: TWO classes of stroke are deliberately left out, because a bubble on the chord's
    midpoint is not where the renderer draws their label and moving points cannot separate them
    anyway. A SELF LOOP (``start top --[headquarters pass]--> start top``, real in the owner's
    bundle) has its midpoint ON the node — pushing the node pushes the label by exactly as much,
    a fixed point the relaxation would spend every round on. PARALLEL strokes between the same
    pair are fanned onto their own arcs by ``mapEdgeArcGeometry`` and labelled at the ARC's
    midpoint, so their chord midpoints coincide while their labels do not. Both are the
    renderer's job (the arc/loop offset), not the layout's. Upgrade path: thread ``parIndex`` /
    ``parCount`` in and offset the bubble on the same perpendicular the fan uses."""
    out: list[_Bubble] = []
    for p in bundled.points:
        hw, hh = label_half_extent(label_len.get(p.id, 0), node=True)
        out.append((p.id, hw, hh, (p.id,)))
    if len(bundled.points) + len(bundled.segments) <= FLOW_RELAX_MAX_BUBBLES:
        pair_count: dict[tuple[str, str], int] = {}
        for s in bundled.segments:
            pair = (s.from_point, s.to_point) if s.from_point <= s.to_point \
                else (s.to_point, s.from_point)
            pair_count[pair] = pair_count.get(pair, 0) + 1
        for s in bundled.segments:
            if s.from_point == s.to_point:
                continue
            pair = (s.from_point, s.to_point) if s.from_point <= s.to_point \
                else (s.to_point, s.from_point)
            if pair_count[pair] > 1:
                continue
            hw, hh = label_half_extent(label_len.get(s.id, 0), node=False)
            if hw <= 0.0:
                continue
            out.append((s.id, hw, hh, (s.from_point, s.to_point)))
    out.sort(key=lambda b: b[0])
    return out


def _compact(pos: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """Bend the picture toward ``FLOW_TARGET_ASPECT`` about its own centre, at CONSTANT AREA:
    the long axis shrinks by ``k``, the short one grows by ``1/k``.

    Area is the half of this that took a measurement to learn. Squashing x alone hit the target
    aspect and made the box 2.4x too small to hold the labels it has to hold — the relaxation
    then had nowhere to put anything and settled with 19 overlaps it could not resolve. Constant
    area keeps the same room and only changes its SHAPE, which is the whole of the owner's
    complaint ("it might be too stretched").

    Affine and uniform, so no edge crossing is created or destroyed and the reading order is
    untouched: the 41-over-42 crossing count the ellipse constants were tuned on survives it
    exactly. A flat picture (one row, or one column) has no aspect to bend and is left alone —
    the relaxation is what spaces those. ``math.sqrt`` is IEEE-correctly-rounded, and so is
    ``Math.sqrt``, so the two ports agree to the bit."""
    if not pos:
        return pos
    xs = [x for x, _ in pos.values()]
    ys = [y for _, y in pos.values()]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if width <= 0.0 or height <= 0.0:
        return pos
    aspect = width / height
    k = math.sqrt(FLOW_TARGET_ASPECT / aspect) if aspect > FLOW_TARGET_ASPECT \
        else math.sqrt(aspect / FLOW_TARGET_ASPECT)
    if k >= 1.0:
        return pos
    k = max(k, FLOW_COMPACT_MIN)
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    if aspect > FLOW_TARGET_ASPECT:
        return {p: (cx + (x - cx) * k, cy + (y - cy) / k) for p, (x, y) in pos.items()}
    return {p: (cx + (x - cx) / k, cy + (y - cy) * k) for p, (x, y) in pos.items()}


def _relax(pos: dict[str, tuple[float, float]], bubbles: list[_Bubble],
           fixed: set[str]) -> None:
    """Push overlapping boxes apart, in place, for a FIXED number of rounds.

    Each round: recompute every bubble's centre, accumulate one minimum-translation push per
    overlapping pair, add a weak spring back to the layer slot, apply. Anchors are in the pair
    loop as obstacles and never in the displacement map — the frame does not move.

    Determinism is not a property of the maths, it is a property of the ORDER: every loop walks
    a sorted list, every tie breaks on the id, and the arithmetic is +,-,*,/ only. Two runs
    agree bit for bit, and so does the TS port."""
    home = {p: xy for p, xy in pos.items() if p not in fixed}
    if not home or len(bubbles) > FLOW_RELAX_MAX_BUBBLES:
        return
    movable = [p for p, _ in sorted(home.items())]
    n = len(bubbles)
    for round_ in range(FLOW_RELAX_ROUNDS):
        # The spring DECAYS to nothing. Early rounds keep the layer's shape while the boxes
        # find room; late rounds are pure separation, because a constant spring settles at
        # push == pull, which is an EQUILIBRIUM WITH OVERLAP — measured, 11 pairs still touching
        # after 400 rounds. Nothing readable overlapping is the requirement; "close to its rank"
        # is only the taste.
        pull = FLOW_RELAX_PULL * (1.0 - float(round_) / float(FLOW_RELAX_ROUNDS))
        centre: dict[str, tuple[float, float]] = {}
        for bid, _hw, _hh, riders in bubbles:
            if len(riders) == 1:
                centre[bid] = pos[riders[0]]
            else:
                (ax, ay), (bx, by) = pos[riders[0]], pos[riders[1]]
                centre[bid] = ((ax + bx) / 2.0, (ay + by) / 2.0)
        dx: dict[str, float] = {p: 0.0 for p in movable}
        dy: dict[str, float] = {p: 0.0 for p in movable}

        def _push(riders: tuple[str, ...], axis: dict[str, float], amount: float) -> None:
            share = amount / float(len(riders))
            for r in riders:
                if r in axis:
                    axis[r] += share

        for i in range(n):
            a_id, a_hw, a_hh, a_riders = bubbles[i]
            ax, ay = centre[a_id]
            for j in range(i + 1, n):
                b_id, b_hw, b_hh, b_riders = bubbles[j]
                # A stroke's label sits on the MIDDLE of the stroke, so it is always within
                # half a stroke of its own two endpoints. That pair is not a layout problem —
                # no arrangement of points separates a midpoint from its own ends — it is the
                # renderer's, and the site already answered it (contract doc §12.5: the action
                # label is drawn OFF the stroke, on the perpendicular, the node label under the
                # node). Spending rounds on it only bloats the picture.
                if len(a_riders) + len(b_riders) == 3 and (
                        a_riders[0] in b_riders or b_riders[0] in a_riders):
                    continue
                bx, by = centre[b_id]
                gap_x = ax - bx
                gap_y = ay - by
                over_x = (a_hw + b_hw) - (gap_x if gap_x >= 0.0 else -gap_x)
                if over_x <= 0.0:
                    continue
                over_y = (a_hh + b_hh) - (gap_y if gap_y >= 0.0 else -gap_y)
                if over_y <= 0.0:
                    continue
                if over_x <= over_y:  # separate on the axis that needs the smaller move
                    step = FLOW_RELAX_PUSH * (over_x + FLOW_RELAX_SLACK)
                    sign = 1.0 if (gap_x > 0.0 or (gap_x == 0.0 and a_id > b_id)) else -1.0
                    _push(a_riders, dx, step * sign)
                    _push(b_riders, dx, -step * sign)
                else:
                    step = FLOW_RELAX_PUSH * (over_y + FLOW_RELAX_SLACK)
                    sign = 1.0 if (gap_y > 0.0 or (gap_y == 0.0 and a_id > b_id)) else -1.0
                    _push(a_riders, dy, step * sign)
                    _push(b_riders, dy, -step * sign)

        for p in movable:
            x, y = pos[p]
            hx, hy = home[p]
            pos[p] = (x + dx[p] + (hx - x) * pull,
                      y + dy[p] + (hy - y) * pull)


def flow_layout(bundled: BundledGraph, *, structure: str, anchor_slots: dict[str, str],
                weight: dict[str, float],
                label_len: dict[str, int] | None = None) -> dict[str, tuple[float, float]]:
    """Point id -> world (x, y). Free points land on a rank/row grid (flow reads left to right,
    the same direction the owner's map has always read); the ANCHORS are then bolted onto the
    chosen structure's vertices, on an ellipse sized to the grid it has to frame — so the frame
    contains the map instead of being five more nodes in the crowd, and an arrival at an anchor
    never has to cross the whole picture to reach it (the starts sit on the left arc, the
    finishes on the right, which is where the ranks already put their neighbours).

    The grid is where the picture STARTS. ``label_len`` — character counts keyed by point id and
    by segment id, in the caller's own locale — turns every drawn name into a box, and the last
    two stages bend the picture to a readable aspect and push those boxes off each other. Pass
    nothing and every point is a bare 16-unit dot: the layout still runs, it just has no names
    to keep apart."""
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

    # The grid is now the STARTING point, not the answer: compact the long axis, then let the
    # label boxes push each other out of the layers they were stacked in. Anchors are obstacles
    # in that pass and never move.
    pos = _compact(pos)
    fixed = set(anchor_slots)
    bubbles = _bubbles(bundled, label_len or {})
    _relax(pos, bubbles, fixed)
    # Second pass, STATE NAMES ONLY, and it is not a retry — it is the priority order the
    # contract doc already states ("rótulos de ação são a camada secundária", §10.7). Measured:
    # in one pass a node can sit in a three-body standoff where the two action labels riding on
    # it push it back exactly as hard as the anchor beside it pushes it away, and it settles
    # 13 units inside the anchor's name. Nothing else may outvote a state's own name, so the
    # last word is a pass in which nothing else is present.
    _relax(pos, [b for b in bubbles if len(b[3]) == 1], fixed)
    return pos
