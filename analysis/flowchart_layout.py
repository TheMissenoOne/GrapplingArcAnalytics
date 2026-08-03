"""Deterministic flowchart layout — desktop (4-sector) + compact (vertical).

Pure, no RNG, no timing: same spec + mode → byte-identical layout. Node
heights are computed from text (chars per line), widths are fixed per kind.
The browser only pans/zooms; it never re-lays-out.

Desktop: root centered; primary branches packed into 4 sectors around it
(top → right → bottom → left, cyclic). Compact: root on top, branches
stacked in one column (portrait-friendly).

Edges are routed by ``flowchart_router`` (Manhattan polylines via explicit
ports); this module only places nodes and calls the router.

Payload (per layout): {width, height, nodes: {id: {x, y, width, height}},
edges: {id: {points: [{x, y}, ...]}}}. Coordinates = CSS px in a world
space the site scales with a viewBox/transform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from analysis.flowchart_router import (
    ROUTING_VERSION,
    route_edges,
    validate_routing,
)

LayoutMode = Literal["desktop", "compact"]

LAYOUT_VERSION = 3

# fixed widths per node kind (px)
NODE_WIDTHS: dict[str, int] = {
    "root-position": 280,
    "position": 210,
    "athlete-action": 240,
    "opponent-condition": 260,
    "response": 240,
    "outcome": 210,
    "portal": 240,
}

_CHARS_PER_LINE = 26
_LINE_HEIGHT = 20.0
_SUBTITLE_LINE = 18.0
_BASE_HEIGHT = 68.0

_SECTOR_GAP = 110.0   # gap between nodes on the same row
_LEVEL_GAP_Y = 60.0   # gap between rows
_ROOT_MARGIN = 110.0  # distance root → first branch layer


@dataclass(frozen=True)
class LayoutNode:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class LayoutEdge:
    source: str
    target: str
    points: list[tuple[float, float]]  # orthogonal polyline, ports included


@dataclass(frozen=True)
class ComputedLayout:
    mode: LayoutMode
    width: float
    height: float
    nodes: dict[str, LayoutNode]
    edges: dict[str, LayoutEdge] = field(default_factory=dict)


def _node_width(kind: str) -> float:
    return float(NODE_WIDTHS.get(kind, 200))


def _node_height(title: str | None, subtitle: str | None) -> float:
    def lines(text: str | None) -> int:
        if not text:
            return 0
        return max(1, -(-len(text) // _CHARS_PER_LINE))  # ceil div

    return (_BASE_HEIGHT
            + (lines(title) - 1) * _LINE_HEIGHT
            + (lines(subtitle) * _SUBTITLE_LINE if subtitle else 0.0))


def _measure(spec: Any) -> dict[str, tuple[float, float]]:
    return {n.id: (_node_width(n.kind), _node_height(n.title, n.subtitle))
            for n in spec.nodes}


def _children(edges: list[Any], node_id: str) -> list[str]:
    return [e.target for e in edges if e.source == node_id]


def _rows_for(spec: Any, branch: Any,
              sizes: dict[str, tuple[float, float]]) -> list[list[str]]:
    """Layer rows for one branch: [action] + per-condition [condition, responses...]."""
    action_id = next(n.id for n in spec.nodes
                     if n.kind == "athlete-action" and n.key == branch.action_key)
    rows: list[list[str]] = [[action_id]]
    for cid in branch.conditions:
        resp_ids = [n.id for n in spec.nodes
                    if n.id in _children(spec.edges, cid)
                    and n.kind in ("response", "outcome", "portal")]
        rows.append([cid] + resp_ids)
    return rows


def _rows_width(rows: list[list[str]], sizes: dict[str, tuple[float, float]]) -> float:
    return max((sum(sizes[nid][0] for nid in row)
                + _SECTOR_GAP * max(0, len(row) - 1)) for row in rows)


def _rows_height(rows: list[list[str]], sizes: dict[str, tuple[float, float]]) -> float:
    if not rows:
        return 0.0
    return (sum(max(sizes[nid][1] for nid in row) for row in rows)
            + _LEVEL_GAP_Y * (len(rows) - 1))


def _branch_bbox(rows: list[list[str]], sizes: dict[str, tuple[float, float]],
                 ) -> tuple[float, float]:
    return _rows_width(rows, sizes), _rows_height(rows, sizes)


def layout_flowchart(spec: Any, mode: LayoutMode = "desktop",
                     *, validate: bool = True) -> ComputedLayout:
    """Lay out a spec deterministically. ``spec`` is a FlowchartSpec (or duck)."""
    sizes = _measure(spec)
    root_id = next(n.id for n in spec.nodes if n.kind == "root-position")
    root_w, root_h = sizes[root_id]

    positions: dict[str, LayoutNode] = {
        root_id: LayoutNode(x=0, y=0, width=root_w, height=root_h),
    }
    if mode == "compact":
        _layout_compact(spec, positions, sizes)
    else:
        _layout_desktop(spec, positions, sizes)
    _place_orphans(spec, positions, sizes, root_id)

    # normalize so all coordinates >= 0 (top/left sectors start negative)
    min_x = min(n.x for n in positions.values())
    min_y = min(n.y for n in positions.values())
    if min_x < 0 or min_y < 0:
        positions = {nid: LayoutNode(x=n.x - min_x, y=n.y - min_y,
                                     width=n.width, height=n.height)
                     for nid, n in positions.items()}

    _resolve_overlaps(positions)
    width = max(n.x + n.width for n in positions.values())
    height = max(n.y + n.height for n in positions.values())
    layout = ComputedLayout(mode=mode, width=width, height=height,
                            nodes=positions,
                            edges=_route_edges(spec, positions))
    if validate:
        validate_layout(layout)
    return layout


def _resolve_overlaps(positions: dict[str, LayoutNode]) -> None:
    """Deterministic push-down pass: overlapping pairs (in placement order)
    get the later node moved below the earlier one. Converges because pushes
    are y-monotonic and only move nodes away from earlier-placed ones."""
    ids = list(positions)
    for _ in range(len(ids) * len(ids)):
        moved = False
        for i in range(len(ids)):
            a = positions[ids[i]]
            for j in range(i + 1, len(ids)):
                b = positions[ids[j]]
                overlap_x = a.x < b.x + b.width and b.x < a.x + a.width
                overlap_y = a.y < b.y + b.height and b.y < a.y + a.height
                if overlap_x and overlap_y:
                    positions[ids[j]] = LayoutNode(
                        x=b.x, y=a.y + a.height + _LEVEL_GAP_Y,
                        width=b.width, height=b.height)
                    b = positions[ids[j]]
                    moved = True
        if not moved:
            return


def _layout_desktop(spec: Any, positions: dict[str, LayoutNode],
                    sizes: dict[str, tuple[float, float]]) -> None:
    root = positions[next(n.id for n in spec.nodes if n.kind == "root-position")]
    root_cx = root.width / 2
    root_cy = root.height / 2

    # sectors around root; branches assigned cyclically in branch order
    sector_branches: list[list[Any]] = [[], [], [], []]
    for i, branch in enumerate(spec.branches):
        sector_branches[i % 4].append(branch)

    for sector, branches in enumerate(sector_branches):
        if not branches:
            continue
        boxes = [_branch_bbox(_rows_for(spec, b, sizes), sizes) for b in branches]
        sector_w = sum(w for w, _ in boxes) + _SECTOR_GAP * (len(branches) - 1)

        if sector == 0:      # top: bottom edges flush at y = -_ROOT_MARGIN
            x0 = root_cx - sector_w / 2
        elif sector == 1:    # right: left edge past root, vertically centered
            x0 = root.width + _ROOT_MARGIN
        elif sector == 2:    # bottom: top edges flush below root, centered
            x0 = root_cx - sector_w / 2
        else:                # left: right edge past root, vertically centered
            x0 = -_ROOT_MARGIN - sector_w

        col_x = x0
        for branch, (bw, bh) in zip(branches, boxes):
            rows = _rows_for(spec, branch, sizes)
            if sector in (1, 3):
                y = root_cy - bh / 2  # each column centered on the root
            elif sector == 0:
                y = -_ROOT_MARGIN - bh
            else:
                y = root.height + _ROOT_MARGIN
            for row in rows:
                row_h = max(sizes[nid][1] for nid in row)
                x = col_x
                for nid in row:
                    positions[nid] = LayoutNode(x=x, y=y, width=sizes[nid][0],
                                                height=sizes[nid][1])
                    x += sizes[nid][0] + _SECTOR_GAP
                y += row_h + _LEVEL_GAP_Y
            col_x += bw + _SECTOR_GAP


def _layout_compact(spec: Any, positions: dict[str, LayoutNode],
                    sizes: dict[str, tuple[float, float]]) -> None:
    root = positions[next(n.id for n in spec.nodes if n.kind == "root-position")]
    y = root.height + _ROOT_MARGIN
    for branch in spec.branches:
        rows = _rows_for(spec, branch, sizes)
        row_w = _rows_width(rows, sizes)
        x = (root.width - row_w) / 2 if row_w < root.width else 0
        for row in rows:
            row_h = max(sizes[nid][1] for nid in row)
            rx = x
            for nid in row:
                positions[nid] = LayoutNode(x=rx, y=y, width=sizes[nid][0],
                                            height=sizes[nid][1])
                rx += sizes[nid][0] + _SECTOR_GAP
            y += row_h + _LEVEL_GAP_Y


def _place_orphans(spec: Any, positions: dict[str, LayoutNode],
                   sizes: dict[str, tuple[float, float]], root_id: str) -> None:
    """Place nodes not reached by branch rows (e.g. results-in position nodes).

    All orphans go in a single "leads to" strip BELOW the branch area,
    spread left→right in spec order. Collision-free by construction; the
    strip width is counted into the canvas size.
    """
    if not any(n.id not in positions for n in spec.nodes):
        return
    max_y = max(p.y + p.height for p in positions.values())
    strip_y = max_y + _ROOT_MARGIN
    x = 0.0
    for n in spec.nodes:
        if n.id in positions or n.id == root_id:
            continue
        w, h = sizes[n.id]
        positions[n.id] = LayoutNode(x=x, y=strip_y, width=w, height=h)
        x += w + _SECTOR_GAP


def _route_edges(spec: Any, positions: dict[str, LayoutNode]) -> dict[str, LayoutEdge]:
    routes = route_edges(spec.edges, positions)
    return {eid: LayoutEdge(source=r.source, target=r.target, points=r.points)
            for eid, r in routes.items()}


def layout_to_dict(layout: ComputedLayout) -> dict[str, Any]:
    return {
        "layoutVersion": LAYOUT_VERSION,
        "routingVersion": ROUTING_VERSION,
        "mode": layout.mode,
        "width": layout.width,
        "height": layout.height,
        "nodes": {nid: {"x": n.x, "y": n.y, "width": n.width, "height": n.height}
                  for nid, n in layout.nodes.items()},
        "edges": {eid: {"points": [{"x": p[0], "y": p[1]} for p in e.points]}
                  for eid, e in layout.edges.items()},
    }


def validate_layout(layout: ComputedLayout, *, skip_routing: bool = True) -> None:
    """Check structural invariants. Raises AssertionError on violation."""
    for nid, n in layout.nodes.items():
        assert n.width > 0 and n.height > 0, f"{nid}: zero-size node"
        assert n.x >= 0 and n.y >= 0, f"{nid}: negative coordinate"
        assert n.x + n.width <= layout.width, f"{nid}: exceeds canvas width"
        assert n.y + n.height <= layout.height, f"{nid}: exceeds canvas height"
    assert layout.edges, "empty edge set"
    for eid, e in layout.edges.items():
        assert len(e.points) >= 2, f"{eid}: degenerate edge"
        for (p, q) in zip(e.points, e.points[1:]):
            assert p != q, f"{eid}: zero-length segment"
            assert p[0] == q[0] or p[1] == q[1], f"{eid}: non-orthogonal segment"
    if not skip_routing:
        from analysis.flowchart_router import RoutedEdge
        routed = {eid: RoutedEdge(source=e.source, target=e.target,
                                   source_side="top", target_side="top", points=e.points)
                  for eid, e in layout.edges.items()}
        validate_routing(routed, layout.nodes)
    nodes = list(layout.nodes.items())
    for i in range(len(nodes)):
        a_id, a = nodes[i]
        for j in range(i + 1, len(nodes)):
            b_id, b = nodes[j]
            overlap_x = a.x < b.x + b.width and b.x < a.x + a.width
            overlap_y = a.y < b.y + b.height and b.y < a.y + a.height
            assert not (overlap_x and overlap_y), f"overlap: {a_id} vs {b_id}"
