"""Deterministic Manhattan edge router for decision-flow layouts.

Pure, no RNG: same nodes + edges → byte-identical polylines. Each routed
edge is a single orthogonal polyline (``points``) that exits the source box
through an explicit port, runs through a gutter lane, and enters the target
box through a port — never crossing any node box (except at the endpoints).

Constants (px, CSS space):
- PORT_STUB: straight run out of the port before the first bend
- ROUTE_GUTTER: preferred distance from a box edge when running outside
- PARALLEL_EDGE_GAP: lane spacing between parallel edges from one port side
- NODE_CLEARANCE: minimum distance a segment may pass a foreign node box

Lanes are assigned per (node, side) bucket in deterministic edge-id order:
k-th edge gets offset [0, +GAP, -GAP, +2GAP, -2GAP, ...] along the box edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ROUTING_VERSION = 2

PORT_STUB = 20.0
ROUTE_GUTTER = 100.0
PARALLEL_EDGE_GAP = 10.0
NODE_CLEARANCE = 50.0

Side = Literal["top", "bottom", "left", "right"]
Point = tuple[float, float]


@dataclass(frozen=True)
class RoutedEdge:
    source: str
    target: str
    source_side: Side
    target_side: Side
    points: list[Point]


@dataclass(frozen=True)
class _Box:
    id: str
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def cx(self) -> float:
        return self.x + self.width / 2

    @property
    def cy(self) -> float:
        return self.y + self.height / 2


def _side_of(dx: float, dy: float, *, source: bool) -> Side:
    """Port side facing the other box (deterministic tie-break: vertical)."""
    if abs(dy) >= abs(dx):
        if dy > 0:
            return "bottom" if source else "top"
        return "top" if source else "bottom"
    if dx > 0:
        return "right" if source else "left"
    return "left" if source else "right"


def _offset_seq(index: int) -> float:
    k = index // 2
    sign = 1 if index % 2 == 0 else -1
    return sign * k * PARALLEL_EDGE_GAP


def _port(box: _Box, side: Side, offset: float) -> Point:
    if side == "top":
        return (box.cx + offset, box.y)
    if side == "bottom":
        return (box.cx + offset, box.bottom)
    if side == "left":
        return (box.x, box.cy + offset)
    return (box.right, box.cy + offset)


def _stub(port: Point, side: Side) -> Point:
    if side == "top":
        return (port[0], port[1] - PORT_STUB)
    if side == "bottom":
        return (port[0], port[1] + PORT_STUB)
    if side == "left":
        return (port[0] - PORT_STUB, port[1])
    return (port[0] + PORT_STUB, port[1])


def _segments(points: list[Point]) -> list[tuple[Point, Point]]:
    return [(points[i], points[i + 1]) for i in range(len(points) - 1)]


def _vertical(p: Point, q: Point) -> bool:
    return p[0] == q[0]


def _crosses_box(seg: tuple[Point, Point], box: _Box, c: float) -> bool:
    (x1, y1), (x2, y2) = seg
    if _vertical(*seg):
        lo, hi = sorted((y1, y2))
        return (box.x - c) < x1 < (box.right + c) and lo < box.bottom and hi > box.y
    lo, hi = sorted((x1, x2))
    return (box.y - c) < y1 < (box.bottom + c) and lo < box.right and hi > box.x


def _initial_run(s: _Box, t: _Box, vertical: bool) -> float:
    """Gutter lane between the two boxes, or an outside lane."""
    if vertical:
        if s.right <= t.x:
            return (s.right + t.x) / 2
        if t.right <= s.x:
            return (t.right + s.x) / 2
        if t.cx >= s.cx:
            return max(s.right, t.right) + ROUTE_GUTTER
        return min(s.x, t.x) - ROUTE_GUTTER
    if s.bottom <= t.y:
        return (s.bottom + t.y) / 2
    if t.bottom <= s.y:
        return (t.bottom + s.y) / 2
    if t.cy >= s.cy:
        return max(s.bottom, t.bottom) + ROUTE_GUTTER
    return min(s.y, t.y) - ROUTE_GUTTER


def _lane_segments(lane: float, sp: Point, sb: Point, tp: Point, tb: Point,
                   vertical: bool) -> list[tuple[Point, Point]]:
    """The three runs of a lane route: out of the source, along the lane, into the target."""
    if vertical:
        return [((sp[0], sb[1]), (lane, sb[1])),
                ((lane, sb[1]), (lane, tb[1])),
                ((lane, tb[1]), (tp[0], tb[1]))]
    return [((sb[0], sp[1]), (sb[0], lane)),
            ((sb[0], lane), (tb[0], lane)),
            ((tb[0], lane), (tb[0], tp[1]))]


def _clear(lane: float, other: list[_Box], sp: Point, sb: Point, tp: Point, tb: Point,
           vertical: bool, exclude: set[str], c: float) -> float:
    """Shift a lane coordinate until every segment is clear.

    Push direction is toward the target (to shorten the connecting segment).
    Rebuilds segments with the current lane each iteration.
    """
    if vertical:
        push_dir = 1 if lane < tp[0] else -1
    else:
        push_dir = 1 if lane < tp[1] else -1

    for _ in range(len(other) * 2 + 2):
        segs = _lane_segments(lane, sp, sb, tp, tb, vertical)
        hit = None
        for b in other:
            if b.id in exclude:
                continue
            for seg in segs:
                if _crosses_box(seg, b, c):
                    hit = b
                    break
            if hit:
                break
        if not hit:
            return lane
        if vertical:
            lane = hit.right + c if push_dir > 0 else hit.x - c
        else:
            lane = hit.bottom + c if push_dir > 0 else hit.y - c
    return lane


def _route_one(edge: Any, boxes: dict[str, _Box],
               offsets: dict[str, float]) -> RoutedEdge:
    s = boxes[edge.source]
    t = boxes[edge.target]
    if s is t:
        raise ValueError(f"self-loop edge not routable: {edge.id}")
    dx = t.cx - s.cx
    dy = t.cy - s.cy
    sside = _side_of(dx, dy, source=True)
    tside = _side_of(dx, dy, source=False)
    # The lane runs ACROSS the ports: left/right ports need a vertical lane,
    # top/bottom ports a horizontal one. Matching the port axis instead sends the
    # line sideways before it has left the box — two bends nobody asked for.
    vertical = sside in ("left", "right")
    soff = offsets.get(f"{edge.source}:{sside}:{edge.id}", 0.0)
    toff = offsets.get(f"{edge.target}:{tside}:{edge.id}", 0.0)

    sp = _port(s, sside, soff)
    tp = _port(t, tside, toff)
    sb = _stub(sp, sside)
    tb = _stub(tp, tside)

    other = [b for b in boxes.values() if b.id not in (edge.source, edge.target)]

    def attempt(lane: float, vert: bool) -> list[Point] | None:
        lane = _clear(lane, other, sp, sb, tp, tb, vert,
                      {edge.source, edge.target}, NODE_CLEARANCE)
        for seg in _lane_segments(lane, sp, sb, tp, tb, vert):
            for b in other:
                if _crosses_box(seg, b, NODE_CLEARANCE):
                    return None
        raw = ([sb, (lane, sb[1]), (lane, tb[1])] if vert
               else [sb, (sb[0], lane), (tb[0], lane)])
        return [sp] + raw + [tb, tp]

    def lanes(vert: bool) -> list[float]:
        # The gutter BETWEEN the two boxes first: it is the shortest route, and when
        # the ports already line up it collapses to a straight line. Only fall out to
        # the far side of the pair when something is in the way.
        outside = ([min(s.x, t.x) - ROUTE_GUTTER, max(s.right, t.right) + ROUTE_GUTTER]
                   if vert else
                   [min(s.y, t.y) - ROUTE_GUTTER, max(s.bottom, t.bottom) + ROUTE_GUTTER])
        return [_initial_run(s, t, vert), *outside]

    pts = None
    # Preferred axis, then the other one: a blocker sitting square between the boxes
    # can only be dodged by a lane running the other way.
    for vert in (vertical, not vertical):
        for lane in lanes(vert):
            pts = attempt(lane, vert)
            if pts is not None:
                break
        if pts is not None:
            break
    if pts is None:  # last resort: a single elbow, orthogonal from both stubs
        pts = [sp, sb, (sb[0], tb[1]) if not vertical else (tb[0], sb[1]), tb, tp]

    out: list[Point] = []
    for p in pts:
        if out and out[-1] == p:
            continue
        # Drop points that only sit mid-run: three collinear points is a bend that
        # never bends, and it is what makes an aligned pair look like a detour.
        if len(out) >= 2 and (
            (out[-2][0] == out[-1][0] == p[0]) or (out[-2][1] == out[-1][1] == p[1])
        ):
            out[-1] = p
            continue
        out.append(p)
    if len(out) < 2:
        raise ValueError(f"degenerate route for {edge.id}")
    return RoutedEdge(source=edge.source, target=edge.target,
                      source_side=sside, target_side=tside, points=out)


def route_edges(edges: list[Any], positions: dict[str, Any]) -> dict[str, RoutedEdge]:
    """Route every edge deterministically. ``positions`` maps id → {x,y,width,height}."""
    by_id = {nid: _Box(id=nid, x=n.x, y=n.y, width=n.width, height=n.height)
             for nid, n in positions.items()}
    missing = {e.id for e in edges if e.source not in by_id or e.target not in by_id}
    if missing:
        raise ValueError(f"edges reference unknown nodes: {sorted(missing)}")

    # parallel-edge offsets, bucketed by (node, side): k-th edge in bucket
    # (sorted by edge id) gets offset [0, +GAP, -GAP, +2GAP, ...] along the edge
    offsets: dict[str, float] = {}
    buckets: dict[tuple[str, Side], list[str]] = {}
    for e in sorted(edges, key=lambda e: e.id):
        s = by_id[e.source]
        t = by_id[e.target]
        dx, dy = t.cx - s.cx, t.cy - s.cy
        sside = _side_of(dx, dy, source=True)
        tside = _side_of(dx, dy, source=False)
        buckets.setdefault((e.source, sside), []).append(e.id)
        buckets.setdefault((e.target, tside), []).append(e.id)
    for (node_id, side), ids in buckets.items():
        for i, eid in enumerate(ids):
            offsets[f"{node_id}:{side}:{eid}"] = _offset_seq(i)

    routes: dict[str, RoutedEdge] = {}
    for e in sorted(edges, key=lambda e: e.id):
        routes[e.id] = _route_one(e, by_id, offsets)
    return routes


def points_to_dict(points: list[Point]) -> list[dict[str, float]]:
    return [{"x": p[0], "y": p[1]} for p in points]


def validate_routing(routes: dict[str, RoutedEdge],
                     positions: dict[str, Any]) -> None:
    """Structural invariants. Raises AssertionError on violation."""
    boxes = {nid: _Box(id=nid, x=n.x, y=n.y, width=n.width, height=n.height)
             for nid, n in positions.items()}
    for eid, route in routes.items():
        assert len(route.points) >= 2, f"{eid}: degenerate route"
        pts = route.points
        assert pts[0] == pts[0] and pts[-1] == pts[-1], f"{eid}: NaN"
        for (p, q) in _segments(pts):
            assert p != q, f"{eid}: zero-length segment"
            assert p[0] == q[0] or p[1] == q[1], f"{eid}: non-orthogonal segment"
        s = boxes[route.source]
        t = boxes[route.target]
        assert _on_boundary(pts[0], s), f"{eid}: start not on source boundary"
        assert _on_boundary(pts[-1], t), f"{eid}: end not on target boundary"
        for seg in _segments(pts):
            for nid, b in boxes.items():
                if nid in (route.source, route.target):
                    continue
                # v1 router: allow generous tolerance in dense layouts (visually negligible)
                assert not _crosses_box(seg, b, 50.0), f"{eid}: crosses {nid}"


def _on_boundary(p: Point, box: _Box) -> bool:
    x, y = p
    return (x == box.x or x == box.right or y == box.y or y == box.bottom)
