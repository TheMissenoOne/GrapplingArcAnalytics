"""Route shape: the router must not add bends the layout does not require."""

from dataclasses import dataclass

from analysis.flowchart_router import route_edges, validate_routing


@dataclass(frozen=True)
class _Edge:
    id: str
    source: str
    target: str


@dataclass(frozen=True)
class _Pos:
    x: float
    y: float
    width: float
    height: float


def _bends(points: list[tuple[float, float]]) -> int:
    return max(0, len(points) - 2)


def test_aligned_boxes_get_a_straight_line() -> None:
    pos = {"a": _Pos(0, 0, 200, 80), "b": _Pos(0, 400, 200, 80)}
    routes = route_edges([_Edge("e", "a", "b")], pos)
    pts = routes["e"].points
    assert _bends(pts) == 0, f"straight drop routed with {_bends(pts)} bends: {pts}"
    assert {p[0] for p in pts} == {100.0}
    validate_routing(routes, pos)


def test_offset_boxes_get_a_single_z() -> None:
    """Down-and-across is two bends. Any more is the lane running the wrong way."""
    pos = {"a": _Pos(0, 0, 200, 80), "b": _Pos(500, 400, 200, 80)}
    routes = route_edges([_Edge("e", "a", "b")], pos)
    pts = routes["e"].points
    assert _bends(pts) <= 2, f"{_bends(pts)} bends: {pts}"
    validate_routing(routes, pos)


def test_side_by_side_boxes_route_through_the_gap() -> None:
    pos = {"a": _Pos(0, 0, 200, 80), "b": _Pos(600, 0, 200, 80)}
    routes = route_edges([_Edge("e", "a", "b")], pos)
    pts = routes["e"].points
    assert routes["e"].source_side == "right"
    assert _bends(pts) == 0, f"aligned pair routed with {_bends(pts)} bends: {pts}"
    validate_routing(routes, pos)


def test_route_detours_around_a_blocking_box() -> None:
    pos = {
        "a": _Pos(0, 0, 200, 80),
        "b": _Pos(0, 600, 200, 80),
        "mid": _Pos(-40, 280, 280, 80),
    }
    routes = route_edges([_Edge("e", "a", "b")], pos)
    validate_routing(routes, pos)
    assert _bends(routes["e"].points) >= 2


def test_routing_is_deterministic() -> None:
    pos = {"a": _Pos(0, 0, 200, 80), "b": _Pos(320, 400, 200, 80),
           "c": _Pos(-320, 400, 200, 80)}
    edges = [_Edge("e1", "a", "b"), _Edge("e2", "a", "c")]
    first = route_edges(edges, pos)
    second = route_edges(list(reversed(edges)), pos)
    assert {k: v.points for k, v in first.items()} == {k: v.points for k, v in second.items()}
