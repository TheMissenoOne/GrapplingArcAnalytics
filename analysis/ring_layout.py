"""Concentric-ring layout for a bundled path graph (prototype variants 16/17).

The owner's own words: *"use a ring style layout so that the submissions anchor node is in the
center and the generic top bottom and neutral anchor nodes are external… since they are only
three, they form an arc. And the other states are also organized in the rings, all
concentric"*.

The semantics of a ring — the thing a reader has to be told, and the reason this is not just a
prettier circle — is **proximity to a finish**:

    ring(p) = the fewest strokes on a directed walk from p to a finish point.

Ring 0 is the finish itself. Ring 1 is every state one action away from finishing. A state with
no observed route to a finish at all is not hidden and not guessed at: it lands one ring beyond
the deepest reachable one, which is exactly what "we have never seen this end a match" looks
like. Ties inside a ring are broken by support and then by id, so two runs agree to the bit.

The ANGLE is the other half, and it is what keeps the radii from becoming spaghetti. Each point
sits in the sector of its own orientation (``top`` up, ``neutral`` left, ``bottom`` down — the
same vertical axis every other display in this project reads), and inside a sector the order is
the barycentre of the point's already-placed inner neighbours. Orientation was chosen over
community detection for two reasons: it exists for every source (a community pass needs the
private aggregate's own graph, which the public corpus sources do not have), and it is what the
three external anchors already mean, so a ``top`` state sits on the side its anchor is on.

``analysis.flow_layout`` owns the bubble/relaxation machinery and this module reuses it rather
than growing a second copy — a label box is a label box, and the two layouts differ only in
where the points START.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analysis.flow_layout import (
    FLOW_COMPACT_MIN,
    _bubbles,
    _relax,
    _spread,
    label_half_extent,
)
from analysis.path_bundling import BundledGraph

__all__ = [
    "ANCHOR_MODES",
    "ANCHOR_PLACEMENTS",
    "DEFAULT_RING_MODE",
    "DEFAULT_RING_PLACEMENT",
    "RING_ANCHOR_GAP",
    "RING_MIN_GAP",
    "SECTOR_CENTRE",
    "SECTOR_INSET",
    "SECTOR_SPAN",
    "RingLayout",
    "layout_quality",
    "ring_guides",
    "ring_index",
    "ring_layout",
]

#: World units between two consecutive rings, before a ring is widened to fit its own labels.
#: Measured, not guessed — swept over the owner's bundle and three athlete dossiers at fixed
#: anchors (readable-ink coverage / fit scale / crossings / overlapping names, desktop):
#:
#:   230/300 -> 3.9-6.3% ink, fit 0.50-0.83, 0-2 overlaps
#:   200/250 -> 5.2-8.2%,     fit 0.54-0.95, 0-2
#:   170/220 -> 6.6-10.6%,    fit 0.57-1.07, 0-2   <- chosen
#:   140/200 -> 7.8-14.5%,    fit 0.61-1.25, 1-2   (overlaps start on every source)
#:
#: At 170 the picture is ~75% more readable ink and 33% bigger text than at 230 for the SAME
#: overlap count, and on the owner's own bundle the crossings actually fall (72 -> 31) because
#: a tighter disc gives `_compact` less to stretch. 140 buys more ink and starts breaking the
#: hard criterion (§10.5.3: zero overlapping state names), so it is the wrong side of the line.
RING_MIN_GAP = 170.0
#: How far outside the widest ring the three generic anchors sit.
RING_ANCHOR_GAP = 220.0
#: Maths convention (0 = east, counter-clockwise); the canvas flips y, same as ``anchor_units``.
SECTOR_CENTRE = {"top": 90.0, "neutral": 180.0, "bottom": 270.0}
#: Degrees each sector may use. 110 of the available 120 leaves a readable gutter between them.
SECTOR_SPAN = 110.0
#: Fraction of a sector's span left empty at each end, so two sectors never touch.
SECTOR_INSET = 0.06
#: How many strokes the crossing count looks at (see ``layout_quality``).
_CROSSING_CAP = 600

#: Where the three generic anchors go. The rings and the sector rule are identical in all
#: three — only these three points move, which is the whole point of the comparison.
ANCHOR_PLACEMENTS: dict[str, dict[str, Any]] = {
    "arco": {
        "label": "Arco externo (topo)",
        # The owner's literal suggestion: three anchors, so an ARC rather than a ring. Read
        # left to right it is top / neutral / bottom, the same order the pentagon's own left
        # column has always had.
        "angles": {"top": 115.0, "neutral": 90.0, "bottom": 65.0},
        "radius": {"top": 1.0, "neutral": 1.0, "bottom": 1.0},
    },
    "tercos": {
        "label": "Terços (topo / esquerda / baixo)",
        # One anchor per sector, on the sector's own centre line — every radial edge into an
        # anchor then runs along the sector it came from instead of across the disc.
        "angles": {"top": 90.0, "neutral": 180.0, "bottom": 270.0},
        "radius": {"top": 1.0, "neutral": 1.0, "bottom": 1.0},
    },
    "bipolar": {
        "label": "Bipolar (Neutro = segundo centro)",
        # Two foci: the finish at the centre, Neutro pulled INSIDE the disc on the left, so the
        # neutral sector reads as the corridor between the two poles. Top/Bottom stay external.
        "angles": {"top": 90.0, "neutral": 180.0, "bottom": 270.0},
        "radius": {"top": 1.0, "neutral": 0.45, "bottom": 1.0},
    },
}

#: What the layout is allowed to move afterwards. ``fixo`` keeps the frame; the two free modes
#: are the owner's 2026-09-01 addendum ("as âncoras genéricas NÃO são fixas — entram na
#: simulação como nós normais"), one keeping the centre pinned and one letting go of everything.
ANCHOR_MODES: dict[str, dict[str, Any]] = {
    "fixo": {"label": "Âncoras fixas", "spread": False, "free_anchors": False,
             "free_centre": False},
    "livre": {"label": "Âncoras livres (centro fixo)", "spread": True, "free_anchors": True,
              "free_centre": False},
    "livre-total": {"label": "Tudo livre", "spread": True, "free_anchors": True,
                    "free_centre": True},
}


#: What the PRODUCT draws (owner, 2026-09-01 night, on the variant-17 demo): *"I like the
#: seventeen example demo. with the bipolar anchor points, fixed anchor, and labels on all the
#: actions."* — Neutro pulled inside as a second pole, Top/Bottom outside, nothing relaxed off
#: its ring. The other placements and both free modes stay in the table because the prototype
#: still renders them side by side; only the DEFAULT is the decision.
DEFAULT_RING_PLACEMENT = "bipolar"
DEFAULT_RING_MODE = "fixo"


@dataclass(frozen=True)
class RingLayout:
    pos: dict[str, tuple[float, float]]
    ring: dict[str, int]            #: point id -> ring index (anchors carry the outer ring)
    radius: dict[int, float]        #: ring index -> its radius
    sector: dict[str, str]          #: point id -> 'top' | 'neutral' | 'bottom'
    unreachable: tuple[str, ...]    #: points with no observed route to a centre, sorted
    anchor_seed: dict[str, tuple[float, float]]  #: where an anchor was placed BEFORE relaxing
    bend: tuple[float, float]       #: the (kx, ky) the viewport bend applied to every radius
    centre: tuple[float, float]     #: where the finish landed — the guides' own origin


def ring_index(bundled: BundledGraph, centre_ids: Sequence[str]) -> dict[str, int]:
    """Point id -> fewest strokes on a DIRECTED walk to any centre point.

    Reverse BFS, so it answers "how far am I from finishing", not "how far is the finish from
    me" — the direction that matters when the centre is the thing every chain aims at. A point
    that cannot reach a centre is left OUT of the result; the caller decides its ring (this
    module puts it one beyond the deepest reachable one).
    """
    back: dict[str, list[str]] = {}
    for seg in bundled.segments:
        back.setdefault(seg.to_point, []).append(seg.from_point)
    out: dict[str, int] = {c: 0 for c in sorted(centre_ids)}
    frontier = sorted(out)
    depth = 0
    while frontier:
        depth += 1
        nxt: list[str] = []
        for point in frontier:
            for prev in sorted(back.get(point, ())):
                if prev not in out:
                    out[prev] = depth
                    nxt.append(prev)
        frontier = sorted(nxt)
    return out


def _sectors(bundled: BundledGraph, ring: dict[str, int],
             sector_of: Mapping[str, str]) -> dict[str, str]:
    """Every point's sector. A junction (``branch``/``merge``) has no orientation of its own —
    it is scaffolding, not a state — so it inherits from whichever neighbour sits closest to the
    centre, which is the one whose radial line it is already on."""
    neigh: dict[str, list[str]] = {}
    for seg in bundled.segments:
        neigh.setdefault(seg.from_point, []).append(seg.to_point)
        neigh.setdefault(seg.to_point, []).append(seg.from_point)
    out = {p.id: sector_of[p.id] for p in bundled.points if p.id in sector_of}
    pending = [p.id for p in bundled.points if p.id not in out]
    for point in sorted(pending, key=lambda p: (ring.get(p, 10**6), p)):
        candidates = sorted(
            (n for n in neigh.get(point, ()) if n in out),
            key=lambda n: (ring.get(n, 10**6), n),
        )
        out[point] = out[candidates[0]] if candidates else "neutral"
    return out


def _ring_radii(members: Mapping[int, list[str]], sector: Mapping[str, str],
                label_len: Mapping[str, int]) -> dict[int, float]:
    """Radius per ring: far enough out that the labels on its busiest SECTOR fit side by side
    along that sector's arc, and never closer than ``RING_MIN_GAP`` to the ring inside it.

    Sizing on the busiest sector rather than the whole circumference is what stops one crowded
    orientation from being solved by space the other two are not using.
    """
    span = math.radians(SECTOR_SPAN * (1.0 - 2.0 * SECTOR_INSET))
    radii: dict[int, float] = {0: 0.0}
    for k in sorted(members):
        if k == 0:
            continue
        need = 0.0
        for name in SECTOR_CENTRE:
            width = sum(
                2.0 * label_half_extent(label_len.get(p, 0), node=True)[0] + 24.0
                for p in members[k] if sector.get(p) == name
            )
            need = max(need, width / span)
        radii[k] = max(radii.get(k - 1, 0.0) + RING_MIN_GAP, need)
    return radii


def _separate_on_ring(angle_of: dict[str, float], row: Sequence[str], radius: float,
                       label_len: Mapping[str, int]) -> None:
    """Push overlapping members of ONE ring apart **in angle only**, in place.

    The ring is the claim the page makes (radius = proximity to a finish), so nothing here may
    change a radius. Two neighbours in angular order need at least the angle their two label
    half-widths subtend at this radius; the pass walks the sorted order forward enforcing that
    minimum, then re-centres the whole run on its original mean so a crowded ring grows
    symmetrically instead of drifting one way. Sector bounds YIELD when a ring is too crowded to
    honour them — better a state at a slightly wrong angle than a state at the wrong radius,
    because only the radius carries a claim about the data.

    Deterministic: one forward pass over an order sorted by (angle, id), no RNG, no convergence
    test. A single member, or a radius of zero, is a no-op.
    """
    if radius <= 0.0 or len(row) < 2:
        return
    order = sorted(row, key=lambda p: (angle_of[p], p))
    half = {p: math.degrees(label_half_extent(label_len.get(p, 0), node=True)[0] / radius)
             for p in order}
    before = sum(angle_of[p] for p in order) / len(order)
    for i in range(1, len(order)):
        prev, cur = order[i - 1], order[i]
        floor = angle_of[prev] + half[prev] + half[cur]
        if angle_of[cur] < floor:
            angle_of[cur] = floor
    after = sum(angle_of[p] for p in order) / len(order)
    for p in order:
        angle_of[p] -= after - before


def _polar(radius: float, degrees: float) -> tuple[float, float]:
    rad = math.radians(degrees)
    return (radius * math.cos(rad), -radius * math.sin(rad))  # canvas y grows DOWNWARD


def _angle_of(point: tuple[float, float]) -> float:
    return math.degrees(math.atan2(-point[1], point[0])) % 360.0


def ring_layout(
    bundled: BundledGraph,
    *,
    centre_ids: Sequence[str],
    anchor_slots: Mapping[str, str],
    sector_of: Mapping[str, str],
    support: Mapping[str, float],
    label_len: Mapping[str, int] | None = None,
    placement: str = DEFAULT_RING_PLACEMENT,
    mode: str = DEFAULT_RING_MODE,
    target_aspect: float | None = None,
) -> RingLayout:
    """Lay the bundled graph out as concentric rings around ``centre_ids``.

    ``anchor_slots`` maps a point id to ``top``/``bottom``/``neutral`` (the three generic
    anchors — the finish anchors are the CENTRE and are passed as ``centre_ids`` instead).
    ``sector_of`` gives every STATE point its orientation; junctions inherit one.

    ``target_aspect`` is the long-axis ratio of the SURFACE this will be drawn on. It matters
    more here than in ``flow_layout``: a ring layout is a DISC (aspect 1.0), and a disc on a
    16:10 desktop or a 9:19 phone wastes whatever the mismatch is — measured, 35% of a 1280x800
    viewport unbent. The bend runs in the same place ``flow_layout`` runs it, BEFORE the final
    relaxation, and that ordering is not cosmetic: bending afterwards squashes one axis and puts
    label boxes the relaxation had already separated back on top of each other (visible on the
    390-wide phone shot, two state names overlapping in a layout that measured zero overlaps).
    """
    labels = dict(label_len or {})
    ids = [p.id for p in bundled.points]
    centres = [c for c in sorted(centre_ids) if c in set(ids)]
    anchors = {p: s for p, s in sorted(anchor_slots.items()) if p in set(ids)}

    reach = ring_index(bundled, centres)
    inner = [p for p in ids if p not in anchors]
    deepest = max((reach[p] for p in inner if p in reach), default=0)
    unreachable = tuple(sorted(p for p in inner if p not in reach))
    outer_ring = deepest + (1 if unreachable else 0)
    ring = {p: reach.get(p, outer_ring) for p in inner}
    ring.update({p: outer_ring + 1 for p in anchors})

    members: dict[int, list[str]] = {}
    for point in sorted(inner):
        members.setdefault(ring[point], []).append(point)
    sector = _sectors(bundled, ring, sector_of)
    radii = _ring_radii(members, sector, labels)

    pos: dict[str, tuple[float, float]] = {}
    if len(centres) == 1:
        pos[centres[0]] = (0.0, 0.0)
    else:  # two per-actor finishes and no unified vertex — a tiny pair at the origin
        for i, c in enumerate(centres):
            pos[c] = _polar(RING_MIN_GAP / 3.0, 90.0 + 180.0 * i)

    in_of: dict[str, list[str]] = {}
    for seg in bundled.segments:
        in_of.setdefault(seg.from_point, []).append(seg.to_point)
        in_of.setdefault(seg.to_point, []).append(seg.from_point)

    angle_of: dict[str, float] = {}
    for k in sorted(members):
        if k == 0:
            continue
        for name, centre_deg in sorted(SECTOR_CENTRE.items()):
            row = [p for p in members[k] if sector.get(p) == name]
            if not row:
                continue
            # barycentre: the mean angle of this point's already-placed inner neighbours, read
            # as an offset from the sector's own centre line so the sort is inside the sector.
            def _key(point: str, centre_deg: float = centre_deg) -> tuple[float, float, str]:
                placed = [pos[n] for n in sorted(in_of.get(point, ())) if n in pos]
                offsets = [((_angle_of(q) - centre_deg + 180.0) % 360.0) - 180.0
                           for q in placed if q != (0.0, 0.0)]
                bary = sum(offsets) / len(offsets) if offsets else 0.0
                return (bary, -support.get(point, 0.0), point)

            row.sort(key=_key)
            usable = SECTOR_SPAN * (1.0 - 2.0 * SECTOR_INSET)
            start = centre_deg - usable / 2.0
            if len(row) == 1:
                # A LONE member of a sector keeps its barycentre instead of snapping to the
                # sector's centre line. Snapping is what turned the owner's 19-state map into a
                # vertical string: most rings hold one or two points per sector, "top" is 90 deg
                # and "bottom" is 270, so almost every point landed on the same x. Clamped into
                # the sector, so the reading (top up / neutral left / bottom down) still holds.
                bary = _key(row[0])[0]
                offset = max(-usable / 2.0, min(usable / 2.0, bary))
                angle_of[row[0]] = centre_deg + offset
                continue
            step = usable / (len(row) - 1)
            for i, point in enumerate(row):
                angle_of[point] = start + i * step
        _separate_on_ring(angle_of, members[k], radii[k], labels)
        for point in members[k]:
            pos[point] = _polar(radii[k], angle_of[point])

    place = ANCHOR_PLACEMENTS[placement]
    outer_r = max(radii.values(), default=RING_MIN_GAP) + RING_ANCHOR_GAP
    for point, slot in anchors.items():
        pos[point] = _polar(outer_r * float(place["radius"][slot]), float(place["angles"][slot]))
    anchor_seed = {p: pos[p] for p in anchors}

    opts = ANCHOR_MODES[mode]
    fixed: set[str] = set()
    if not opts["free_anchors"]:
        fixed |= set(anchors)
    if not opts["free_centre"]:
        fixed |= set(centres)
    bubbles = _bubbles(bundled, labels)
    neighbours: dict[str, list[str]] = {}
    for seg in bundled.segments:
        neighbours.setdefault(seg.from_point, []).append(seg.to_point)
        neighbours.setdefault(seg.to_point, []).append(seg.from_point)
    # ⚠️ Owner call 2026-09-01: in `fixo` EVERY state sits ON a ring — no free position between
    # two of them. That rules out the cartesian relaxation `flow_layout` ends with, because its
    # whole job is to move a box on the axis of least penetration, which for a ring layout is
    # almost always the RADIAL one: it was quietly lifting states off the ring the page exists to
    # show. Overlap is resolved by `_separate_on_ring` instead, which only ever changes an ANGLE.
    # The free modes are the opposite claim and keep the full spread+relax — the rings are meant
    # to dissolve there, which is exactly what the comparison is for.
    if opts["spread"]:
        _spread(pos, bubbles, fixed, neighbours)
    bend = (1.0, 1.0)
    if target_aspect is not None:
        bend = _bend(pos, pos.get(centres[0], (0.0, 0.0)) if centres else (0.0, 0.0),
                      target_aspect)
        anchor_seed = {p: pos[p] for p in anchors}  # the seed is where the FRAME put it, bent
    if opts["spread"]:
        _relax(pos, bubbles, fixed)
        _relax(pos, [b for b in bubbles if len(b[3]) == 1], fixed)

    centre = pos[centres[0]] if centres else (0.0, 0.0)
    return RingLayout(pos=pos, ring=ring, radius=radii, sector=sector,
                      unreachable=unreachable, anchor_seed=anchor_seed, bend=bend,
                      centre=centre)


def ring_guides(layout: RingLayout) -> list[dict[str, Any]]:
    """The concentric guides a renderer draws under the graph, in the SAME world units the
    positions are in.

    They are ellipses and not circles because the layout is a disc bent toward its surface's
    aspect at constant area (``target_aspect``); an affine, centre-preserving bend turns a
    concentric circle into a concentric ellipse, so ``rx``/``ry`` come from the bend the points
    themselves went through and never from a second computation. Ring 0 (the finish itself) has
    no guide — it is the node.

    A guide is a CLAIM about the data ("everything on this line is N strokes from a finish"), so
    a caller that relaxes points off their ring must not draw them; ``ring_layout``'s free modes
    are exactly that case and the prototype drops the guides there.
    """
    kx, ky = layout.bend
    return [{"ring": k, "rx": round(layout.radius[k] * kx, 1),
              "ry": round(layout.radius[k] * ky, 1)}
             for k in sorted(layout.radius) if layout.radius[k] > 0.0]


def _bend(pos: dict[str, tuple[float, float]], centre: tuple[float, float],
           target: float) -> tuple[float, float]:
    """Shape the DISC to its surface, about the RING CENTRE, at constant area. In place.

    ``target`` is the surface's true ratio (``width / height``); on a LANDSCAPE surface the
    result is a picture whose rings have exactly that aspect, which is what "a disc shaped like
    the screen" means. A PORTRAIT surface is left alone — see the branch below for why.

    ⚠️ This is deliberately NOT ``flow_layout._compact``, and the difference is not cosmetic.
    ``_compact`` reads the CURRENT aspect off the whole point cloud's bounding box and corrects
    by the mismatch. A ring layout's cloud is a disc PLUS three outlying anchors, so when a
    bundle only has one of them (measured, the App's own mock: a single ``start top`` and no
    bottom) the box is tall and narrow, the correction overshoots, and the guides come out at an
    eccentricity of ~4 on a 2:1 screen — concentric rings drawn as flat ovals. Here the geometry
    is KNOWN (a disc, aspect 1), so the factor is known too: ``sqrt(target)`` on x and its
    reciprocal on y, one formula for both orientations, no branch, area preserved.

    Bending about the ring centre rather than the cloud's centre is the other half: it is what
    keeps every ring concentric with the finish after the bend, and therefore what makes a guide
    ellipse a true description of where the points are.

    ``FLOW_COMPACT_MIN`` is the same floor the flow uses: an extreme surface must not flatten the
    disc into a line.
    """
    if target < 1.0:
        # A PORTRAIT surface gets no bend, and that is a measurement, not a symmetry. The ring
        # frame is ALREADY portrait: the bipolar poles sit at 90 and 270 degrees, so the cloud is
        # ~2x outer_radius tall and ~1.45x wide before anything is bent. Stretching it further to
        # match a phone buys nothing and costs the only axis the labels have — measured on the
        # App's own mock at 390x700, bent vs. unbent: 20 vs 25 names drawn, 11 vs 15 ACTION names,
        # and the top pole's own name lost. The disc stays round; the frame is what fills the
        # screen.
        return (1.0, 1.0)
    k = math.sqrt(target)
    kx = max(FLOW_COMPACT_MIN, min(1.0 / FLOW_COMPACT_MIN, k))
    ky = max(FLOW_COMPACT_MIN, min(1.0 / FLOW_COMPACT_MIN, 1.0 / k))
    cx, cy = centre
    for point, (x, y) in list(pos.items()):
        pos[point] = (cx + (x - cx) * kx, cy + (y - cy) * ky)
    return (kx, ky)


def _crosses(a: tuple[float, float], b: tuple[float, float],
             c: tuple[float, float], d: tuple[float, float]) -> bool:
    def side(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1, d2 = side(c, d, a), side(c, d, b)
    d3, d4 = side(a, b, c), side(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def layout_quality(pos: Mapping[str, tuple[float, float]], bundled: BundledGraph,
                   label_len: Mapping[str, int], viewport: tuple[float, float]) -> dict[str, Any]:
    """The three numbers §10.5.3 already measures layouts by, for one viewport.

    ``occupancy`` is the drawn box (points PLUS the label boxes riding on them) scaled to fit
    the viewport, over the viewport's area — the same "how much of the screen is picture"
    reading. ``crossings`` counts straight-chord segment intersections that do not share an
    endpoint (the renderer bows some of them, so this is an upper bound and comparable across
    layouts, which is all a comparison needs). ``labelOverlaps`` counts overlapping pairs of
    NODE-name boxes, the hard criterion the relaxation is judged on.
    """
    if not pos:
        return {"occupancy": 0.0, "inkCoverage": 0.0, "crossings": 0,
                "crossingsCapped": False, "labelOverlaps": 0, "fitScale": 0.0}
    boxes = []
    for point in bundled.points:
        if point.id not in pos:
            continue
        half_w, half_h = label_half_extent(label_len.get(point.id, 0), node=True)
        x, y = pos[point.id]
        boxes.append((point.id, x - half_w, y - half_h, x + half_w, y + half_h))
    min_x = min(b[1] for b in boxes)
    min_y = min(b[2] for b in boxes)
    max_x = max(b[3] for b in boxes)
    max_y = max(b[4] for b in boxes)
    width, height = max(max_x - min_x, 1e-6), max(max_y - min_y, 1e-6)
    vw, vh = viewport
    scale = min(vw / width, vh / height)
    crossings = 0
    chords = [(s.from_point, s.to_point) for s in sorted(bundled.segments, key=lambda s: s.id)
              if s.from_point != s.to_point and s.from_point in pos and s.to_point in pos]
    # ponytail: O(n^2) pair scan, capped. The corpus draws ~2 200 strokes and the count is only
    # ever used to COMPARE two layouts of the same graph, so the same deterministic prefix in
    # both is a fair comparison; a sweep-line is the upgrade if the absolute number ever matters.
    capped = len(chords) > _CROSSING_CAP
    chords = chords[:_CROSSING_CAP]
    for i, (a1, a2) in enumerate(chords):
        for b1, b2 in chords[i + 1:]:
            if {a1, a2} & {b1, b2}:
                continue
            if _crosses(pos[a1], pos[a2], pos[b1], pos[b2]):
                crossings += 1
    overlaps = 0
    for i, first in enumerate(boxes):
        for second in boxes[i + 1:]:
            if (first[1] < second[3] and first[3] > second[1]
                    and first[2] < second[4] and first[4] > second[2]):
                overlaps += 1
    # Occupancy is the BOUNDING BOX, and a box can be full while the picture inside it is
    # mostly empty — `tercos` parks one anchor alone at the far left and reads 99% while half
    # the frame is black. `inkCoverage` is the §10.5.3 "cobertura legível" reading: how much of
    # the frame is actually covered by something readable. The two together say what one does not.
    ink = sum((b[3] - b[1]) * (b[4] - b[2]) for b in boxes)
    return {
        "occupancy": round(100.0 * (width * scale) * (height * scale) / (vw * vh), 1),
        "inkCoverage": round(100.0 * ink / (width * height), 1),
        "crossings": crossings,
        "crossingsCapped": capped,
        "labelOverlaps": overlaps,
        "fitScale": round(scale, 4),
    }
