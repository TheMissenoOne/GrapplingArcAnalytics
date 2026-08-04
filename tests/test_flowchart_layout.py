"""Tests for analysis/flowchart_layout.py — deterministic, no network/DB."""

from typing import Any

import pytest

from analysis.flowchart_compiler import compile_flowchart
from analysis.flowchart_layout import (
    LAYOUT_VERSION,
    ComputedLayout,
    layout_flowchart,
    layout_to_dict,
    validate_layout,
)
from analysis.flowchart_router import ROUTING_VERSION


def _spec(branches: int = 1, responses: int = 1, positions: int = 0) -> Any:
    from analysis.decision_flow import DecisionPattern

    patterns = []
    for i in range(branches):
        cond = f"cond:posts-hand-{i}" if branches > 1 else "cond:posts-hand"
        resp = f"response {i}" if branches > 1 else "hip bump sweep"
        patterns.append(DecisionPattern(
            source_position_key="closed guard",
            action_key=f"action {i}" if branches > 1 else "hip bump sweep",
            condition_key=cond,
            response_key=resp,
            resulting_position_key="mount" if positions else "closed guard",
            count=4, success_count=3, failure_count=1, match_count=2,
            confidence=0.7, outcome_type="sweep" if positions else "submission",
        ))
    from analysis.flowchart_compiler import FlowchartDefinition

    definition = FlowchartDefinition(
        key="test-flow",
        title="Test Flow",
        athlete_key="test athlete",
        root_position_key="closed guard",
    )
    return compile_flowchart(definition, patterns)


class TestDesktop:
    def test_root_anchors_canvas(self) -> None:
        layout = layout_flowchart(_spec())
        root = layout.nodes["n:root-position:closed guard"]
        assert root.x >= 0 and root.y >= 0
        assert root.width <= layout.width
        assert root.height <= layout.height

    def test_all_coordinates_non_negative(self) -> None:
        layout = layout_flowchart(_spec(branches=5))
        assert all(n.x >= 0 and n.y >= 0 for n in layout.nodes.values())

    def test_no_overlap_multi_branch(self) -> None:
        layout = layout_flowchart(_spec(branches=6))
        validate_layout(layout)

    def test_no_overlap_with_results_in_nodes(self) -> None:
        # 5 branches + results-in position nodes (mount/back control/turtle):
        # regression — a left-sector orphan used to drift onto the root node.
        from analysis.decision_flow import DecisionPattern
        from analysis.flowchart_compiler import FlowchartDefinition

        def pat(action: str, cond: str, resp: str, result_pos: str,
                count: int, matches: int) -> Any:
            return DecisionPattern(
                source_position_key="closed guard", action_key=action,
                condition_key=cond, response_key=resp,
                resulting_position_key=result_pos, count=count,
                success_count=count, failure_count=0, match_count=matches,
                outcome_type="sweep")

        defs = FlowchartDefinition(
            key="t", title="t", athlete_key="a", root_position_key="closed guard")
        spec = compile_flowchart(defs, [
            pat("hip bump sweep", "cond:posts-hand", "kimura grip",
                "mount", 4, 2),
            pat("hip bump sweep", "cond:postures", "back take",
                "back control", 3, 2),
            pat("scissor sweep", "cond:leans-forward", "scissor sweep to mount",
                "mount", 3, 2),
            pat("scissor sweep", "cond:postures", "ankle pick",
                "turtle", 1, 1),
            pat("armdrag", "cond:reaches-forward", "back take",
                "back control", 2, 2),
            pat("back take", "cond:frames", "rear naked choke",
                "back control", 4, 3),
            pat("triangle", "cond:arm-posted", "triangle choke",
                "closed guard", 2, 2),
        ])
        layout = layout_flowchart(spec, "desktop")
        validate_layout(layout)
        compact = layout_flowchart(spec, "compact")
        validate_layout(compact)

    def test_edges_reference_real_nodes(self) -> None:
        layout = layout_flowchart(_spec())
        ids = set(layout.nodes)
        assert set(layout.edges) == {e.id for e in _spec().edges}
        for e in _spec().edges:
            assert e.source in ids
            assert e.target in ids
            assert layout.edges[e.id].points
            assert len(layout.edges[e.id].points) >= 2

    def test_stages_form_aligned_columns(self) -> None:
        """Every branch shares the same x per stage — that alignment is the whole
        point of the layout: the reader learns the bands once."""
        layout = layout_flowchart(_spec(branches=5, responses=2))
        spec = _spec(branches=5, responses=2)
        by_kind: dict[str, set[float]] = {}
        for n in spec.nodes:
            if n.id in layout.nodes:
                by_kind.setdefault(n.kind, set()).add(layout.nodes[n.id].x)
        for kind in ("athlete-action", "opponent-condition"):
            assert len(by_kind.get(kind, {0})) == 1, f"{kind} not column-aligned"

    def test_reads_left_to_right(self) -> None:
        layout = layout_flowchart(_spec(branches=3, responses=1))
        spec = _spec(branches=3, responses=1)
        x = {n.kind: layout.nodes[n.id].x for n in spec.nodes if n.id in layout.nodes}
        assert x["root-position"] < x["athlete-action"] < x["opponent-condition"]

    def test_branches_stack_without_overlap(self) -> None:
        layout = layout_flowchart(_spec(branches=5, responses=2))
        boxes = list(layout.nodes.values())
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                assert not (a.x < b.x + b.width and b.x < a.x + a.width
                            and a.y < b.y + b.height and b.y < a.y + a.height)

    def test_deterministic(self) -> None:
        a = layout_flowchart(_spec(branches=6))
        b = layout_flowchart(_spec(branches=6))
        assert layout_to_dict(a) == layout_to_dict(b)


class TestCompact:
    def test_stacks_downward(self) -> None:
        layout = layout_flowchart(_spec(branches=3), mode="compact")
        root = layout.nodes["n:root-position:closed guard"]
        actions = [n for nid, n in layout.nodes.items()
                   if nid.startswith("n:athlete-action:")]
        assert all(a.y >= root.y + root.height for a in actions)
        # branch order preserved top→bottom
        assert actions[0].y < actions[1].y < actions[2].y

    def test_no_overlap(self) -> None:
        layout = layout_flowchart(_spec(branches=4, positions=1), mode="compact")
        validate_layout(layout)

    def test_deterministic(self) -> None:
        a = layout_flowchart(_spec(branches=3), mode="compact")
        b = layout_flowchart(_spec(branches=3), mode="compact")
        assert layout_to_dict(a) == layout_to_dict(b)


class TestValidate:
    def test_detects_overlap(self) -> None:
        layout = layout_flowchart(_spec(branches=2))
        nodes = dict(layout.nodes)
        first = next(iter(nodes))
        node = nodes[first]
        # force an overlap and expect validation to fail
        from analysis.flowchart_layout import LayoutNode

        nodes["n:athlete-action:action 1"] = LayoutNode(
            x=node.x + 1, y=node.y + 1, width=node.width, height=node.height)
        bad = ComputedLayout(mode="desktop", width=layout.width,
                             height=layout.height, nodes=nodes, edges=layout.edges)
        with pytest.raises(AssertionError):
            validate_layout(bad)

    def test_accepts_empty_edges(self) -> None:
        layout = layout_flowchart(_spec())
        validate_layout(layout)

    def test_layout_to_dict_shape(self) -> None:
        layout = layout_flowchart(_spec())
        d = layout_to_dict(layout)
        assert d["layoutVersion"] == LAYOUT_VERSION
        assert d["routingVersion"] == ROUTING_VERSION
        assert d["mode"] == "desktop"
        assert d["width"] > 0 and d["height"] > 0
        node = next(iter(d["nodes"].values()))
        assert set(node) == {"x", "y", "width", "height"}
        edge = next(iter(d["edges"].values()))
        assert "points" in edge
        assert len(edge["points"]) >= 2
        p0 = edge["points"][0]
        assert set(p0) == {"x", "y"}


class TestCompactIsSingleColumn:
    def test_every_node_on_its_own_row(self) -> None:
        """A phone can only read one node at a time — no two may share a band."""
        layout = layout_flowchart(_spec(branches=4, responses=2), mode="compact")
        boxes = sorted(layout.nodes.values(), key=lambda n: n.y)
        for a, b in zip(boxes, boxes[1:]):
            assert a.y + a.height <= b.y, "compact rows must not overlap vertically"

    def test_column_is_narrow_enough_for_a_phone(self) -> None:
        layout = layout_flowchart(_spec(branches=4, responses=2), mode="compact")
        widest = max(n.width for n in layout.nodes.values())
        # the whole world may be no wider than one node — that is what makes it fit
        assert layout.width <= widest + 1
