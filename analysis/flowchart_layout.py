"""Deterministic flowchart layout — desktop (columns) + compact (vertical).

Pure, no RNG, no timing: same spec + mode → byte-identical layout. Node
heights are computed from text (chars per line), widths are fixed per kind.
The browser only pans/zooms; it never re-lays-out.

Desktop: root at the left, branches stacked by rank, one GLOBAL column per
stage (action → reaction → answer → landing position). The bands line up
across every branch so the chart is scanned once rather than re-read per
branch. Compact: root on top, branches stacked in one column (portrait).

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

LAYOUT_VERSION = 5

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
_DETAIL_CHARS_PER_LINE = 32   # detail is set smaller, so more fits per line
_LINE_HEIGHT = 20.0
_SUBTITLE_LINE = 18.0
_DETAIL_LINE = 16.0
_DETAIL_PAD = 6.0             # rule + breathing room above the detail block
_BASE_HEIGHT = 68.0

_SECTOR_GAP = 110.0   # gap between nodes on the same row
_LEVEL_GAP_Y = 60.0   # gap between rows
_ROOT_MARGIN = 110.0  # distance root → first branch layer
_BRANCH_GAP = 90.0    # vertical gap between whole branches


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


def _node_height(title: str | None, subtitle: str | None,
                 detail: list[str] | None = None) -> float:
    """Box height from its text. Detail lines wrap at their own (denser) measure —
    a node that cites two bouts has to be taller than one that cites none."""
    def lines(text: str | None, per_line: int = _CHARS_PER_LINE) -> int:
        if not text:
            return 0
        return max(1, -(-len(text) // per_line))  # ceil div

    h = (_BASE_HEIGHT
         + (lines(title) - 1) * _LINE_HEIGHT
         + (lines(subtitle) * _SUBTITLE_LINE if subtitle else 0.0))
    if detail:
        h += _DETAIL_PAD + sum(
            lines(d, _DETAIL_CHARS_PER_LINE) * _DETAIL_LINE for d in detail)
    return h


def _measure(spec: Any) -> dict[str, tuple[float, float]]:
    return {n.id: (_node_width(n.kind),
                   _node_height(n.title, n.subtitle, getattr(n, "detail", None)))
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


def _tail_of(spec: Any, node_id: str, kinds: tuple[str, ...]) -> list[str]:
    """Children of ``node_id`` restricted to ``kinds``, in spec order."""
    kids = set(_children(spec.edges, node_id))
    return [n.id for n in spec.nodes if n.id in kids and n.kind in kinds]


def _layout_desktop(spec: Any, positions: dict[str, LayoutNode],
                    sizes: dict[str, tuple[float, float]]) -> None:
    """Root at the left, branches stacked by rank, one column per stage.

    Column bands are GLOBAL — every action shares an x, every reaction shares the
    next, every answer the next. That alignment is what makes the chart scannable:
    the reader learns the bands once instead of re-orienting per branch. (The old
    radial layout spread branches over four sectors around the root, so some chains
    read upward and some downward and the eye had to reset at every branch.)
    """
    root_id = next(n.id for n in spec.nodes if n.kind == "root-position")
    root = positions[root_id]

    resp_kinds = ("response", "outcome", "portal")
    pos_kinds = ("position", "portal")

    # ---- gather each branch's shape: action → [(condition|None, [responses])] ----
    shaped: list[tuple[str, list[tuple[str | None, list[str]]]]] = []
    for branch in spec.branches:
        action_id = next((n.id for n in spec.nodes
                          if n.kind == "athlete-action" and n.key == branch.action_key), None)
        if action_id is None or action_id not in sizes:
            continue
        legs: list[tuple[str | None, list[str]]] = []
        for cid in branch.conditions:
            if cid in sizes:
                legs.append((cid, _tail_of(spec, cid, resp_kinds)))
        # responses hanging straight off the action (no recorded reaction)
        direct = _tail_of(spec, action_id, resp_kinds)
        if direct:
            legs.append((None, direct))
        shaped.append((action_id, legs))

    if not shaped:
        return

    # ---- column widths, so the bands line up across every branch ----
    def col_width(ids: list[str]) -> float:
        return max((sizes[i][0] for i in ids), default=0.0)

    action_ids = [a for a, _ in shaped]
    cond_ids = [c for _, legs in shaped for c, _ in legs if c]
    resp_ids = [r for _, legs in shaped for _, rs in legs for r in rs]

    x_action = root.width + _ROOT_MARGIN
    x_cond = x_action + col_width(action_ids) + _SECTOR_GAP
    x_resp = x_cond + (col_width(cond_ids) + _SECTOR_GAP if cond_ids else 0.0)
    x_pos = x_resp + col_width(resp_ids) + _SECTOR_GAP

    # ---- stack branches top to bottom, each leg a row block ----
    y = 0.0
    for action_id, legs in shaped:
        branch_top = y
        leg_y = y
        for cid, responses in legs:
            # a leg is as tall as its taller side: the reaction, or its stack of answers
            stack_h = sum(sizes[r][1] for r in responses) + \
                _LEVEL_GAP_Y * max(0, len(responses) - 1)
            cond_h = sizes[cid][1] if cid else 0.0
            leg_h = max(cond_h, stack_h)

            if cid:
                positions[cid] = LayoutNode(
                    x=x_cond, y=leg_y + (leg_h - cond_h) / 2,
                    width=sizes[cid][0], height=sizes[cid][1])

            ry = leg_y + (leg_h - stack_h) / 2
            for rid in responses:
                positions[rid] = LayoutNode(x=x_resp, y=ry,
                                            width=sizes[rid][0], height=sizes[rid][1])
                # the position this answer lands in sits beside it, not below
                for pid in _tail_of(spec, rid, pos_kinds):
                    if pid not in positions:
                        positions[pid] = LayoutNode(
                            x=x_pos, y=ry, width=sizes[pid][0], height=sizes[pid][1])
                ry += sizes[rid][1] + _LEVEL_GAP_Y
            leg_y += leg_h + _LEVEL_GAP_Y

        branch_h = max(leg_y - _LEVEL_GAP_Y - branch_top, sizes[action_id][1])
        positions[action_id] = LayoutNode(
            x=x_action, y=branch_top + (branch_h - sizes[action_id][1]) / 2,
            width=sizes[action_id][0], height=sizes[action_id][1])
        y = branch_top + branch_h + _BRANCH_GAP

    # root sits centred against the whole stack, so its edges fan symmetrically
    total_h = max(y - _BRANCH_GAP, root.height)
    positions[root_id] = LayoutNode(x=0.0, y=(total_h - root.height) / 2,
                                    width=root.width, height=root.height)


def _layout_compact(spec: Any, positions: dict[str, LayoutNode],
                    sizes: dict[str, tuple[float, float]]) -> None:
    """One node per row, in reading order — the only shape a phone can show legibly.

    The previous compact mode still put a reaction and its answers side by side, so a
    row stayed ~1100px wide and had to be scaled to ~0.3 to fit a 390px screen, which
    made every label unreadable. A strict single column costs vertical scrolling and
    buys full-size text.
    """
    root_id = next(n.id for n in spec.nodes if n.kind == "root-position")
    root = positions[root_id]
    col_w = max(w for w, _ in sizes.values())
    resp_kinds = ("response", "outcome", "portal")
    pos_kinds = ("position", "portal")

    y = root.height + _ROOT_MARGIN
    seen: set[str] = {root_id}

    def place(nid: str) -> None:
        nonlocal y
        if nid in seen or nid not in sizes:
            return
        seen.add(nid)
        w, h = sizes[nid]
        positions[nid] = LayoutNode(x=(col_w - w) / 2, y=y, width=w, height=h)
        y += h + _LEVEL_GAP_Y

    for branch in spec.branches:
        action_id = next((n.id for n in spec.nodes
                          if n.kind == "athlete-action" and n.key == branch.action_key), None)
        if action_id is None:
            continue
        place(action_id)
        legs = list(branch.conditions) + [None]
        for cid in legs:
            parent = cid if cid else action_id
            if cid:
                place(cid)
            for rid in _tail_of(spec, parent, resp_kinds):
                place(rid)
                for pid in _tail_of(spec, rid, pos_kinds):
                    place(pid)
        y += _BRANCH_GAP - _LEVEL_GAP_Y

    positions[root_id] = LayoutNode(x=(col_w - root.width) / 2, y=0,
                                    width=root.width, height=root.height)


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
