"""analysis.path_bundling — the owner's own five bundling cases plus the global invariant.

The fixtures are deliberately tiny and literal (Phase 4 of the plan: "fixtures antes dos dados
reais"). The last test runs the same invariant over the owner's real bundle when it is present.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from analysis.path_bundling import BundledGraph, RenderPath, bundle_paths


def _p(pid: str, source: str, actions: tuple[str, ...], target: str,
       count: int = 1) -> RenderPath:
    return RenderPath(path_id=pid, source=source, target=target, actions=actions,
                      actor="you", count=count)


def _inputs(paths: list[RenderPath]) -> set[tuple[str, tuple[str, ...], str]]:
    return {(p.source, p.actions, p.target) for p in paths}


def _assert_faithful(g: BundledGraph, paths: list[RenderPath]) -> None:
    """The two halves of "no invented route": every path comes back exactly as given, and the
    drawing licenses nothing else."""
    for p in paths:
        assert g.reconstruct(p.path_id) == (p.source, p.actions, p.target), p.path_id
    assert g.walkable_routes() == _inputs(paths)


def _seg_by_actions(g: BundledGraph, actions: tuple[str, ...]) -> Any:
    hits = [s for s in g.segments if s.actions == actions]
    assert len(hits) == 1, f"{actions} -> {[(s.actions, sorted(s.path_ids)) for s in g.segments]}"
    return hits[0]


# ── case 1 — shared PREFIX, fork after it ──────────────────────────────────────────────

def test_case1_shared_prefix_forks_after_the_common_run() -> None:
    paths = [_p("p1", "A", ("1", "2", "3"), "C"), _p("p2", "A", ("1", "2", "4"), "D")]

    g = bundle_paths(paths)

    trunk = _seg_by_actions(g, ("1", "2"))
    assert sorted(trunk.path_ids) == ["p1", "p2"]
    assert g.point(trunk.from_point).state_key == "A"
    fork = g.point(trunk.to_point)
    assert fork.kind == "branch" and fork.state_key is None  # never persisted, never a state
    assert sorted(_seg_by_actions(g, ("3",)).path_ids) == ["p1"]
    assert sorted(_seg_by_actions(g, ("4",)).path_ids) == ["p2"]
    _assert_faithful(g, paths)


# ── case 2 — an INTERNAL run shared with a path that opens elsewhere ───────────────────

def test_case2_internal_subsequence_shared_and_converging_on_the_same_state() -> None:
    """``B --[5,2,3]--> C`` added to case 1. ``[2,3]`` is p1/p3's common internal run, but p2
    also walks the ``2`` (through the ``[1,2]`` prefix), so the run SPLITS at the point where
    the set changes — a segment is shared by exactly one set of paths, that is the definition."""
    paths = [
        _p("p1", "A", ("1", "2", "3"), "C"),
        _p("p2", "A", ("1", "2", "4"), "D"),
        _p("p3", "B", ("5", "2", "3"), "C"),
    ]

    g = bundle_paths(paths)

    shared2 = _seg_by_actions(g, ("2",))
    assert sorted(shared2.path_ids) == ["p1", "p2", "p3"]
    assert g.point(shared2.from_point).kind == "merge"   # p1/p2's prefix meets p3's ``5``
    assert g.point(shared2.to_point).kind == "branch"    # then p2 leaves for D
    shared3 = _seg_by_actions(g, ("3",))
    assert sorted(shared3.path_ids) == ["p1", "p3"]      # convergence on C
    assert g.point(shared3.to_point).state_key == "C"
    _assert_faithful(g, paths)


def test_case2_in_isolation_keeps_the_internal_run_whole() -> None:
    """Without p2 there is nothing to split the run: ``[2,3]`` is ONE segment."""
    paths = [_p("p1", "A", ("1", "2", "3"), "C"), _p("p3", "B", ("5", "2", "3"), "C")]

    g = bundle_paths(paths)

    shared = _seg_by_actions(g, ("2", "3"))
    assert sorted(shared.path_ids) == ["p1", "p3"]
    assert g.point(shared.from_point).kind == "merge"
    assert g.point(shared.to_point).state_key == "C"
    _assert_faithful(g, paths)


# ── case 3 — shared SUFFIX, convergence at the first common element ────────────────────

def test_case3_shared_suffix_converges_at_the_first_common_action() -> None:
    paths = [_p("p1", "A", ("1", "3", "6"), "C"), _p("p2", "B", ("4", "5", "6"), "C")]

    g = bundle_paths(paths)

    tail = _seg_by_actions(g, ("6",))
    assert sorted(tail.path_ids) == ["p1", "p2"]
    assert g.point(tail.from_point).kind == "merge"
    assert g.point(tail.to_point).state_key == "C"
    assert sorted(_seg_by_actions(g, ("1", "3")).path_ids) == ["p1"]
    assert sorted(_seg_by_actions(g, ("4", "5")).path_ids) == ["p2"]
    _assert_faithful(g, paths)


# ── case 4 — same actions, different endpoints: NOT one route ─────────────────────────

def test_case4_same_actions_different_endpoints_never_collapse() -> None:
    """The owner's own trap. ``A --[1,2]--> C`` and ``B --[1,2]--> D`` share no state, so
    nothing merges — and the reconstruction proves ``A -> D`` / ``B -> C`` do not exist."""
    paths = [_p("p1", "A", ("1", "2"), "C"), _p("p2", "B", ("1", "2"), "D")]

    g = bundle_paths(paths)

    assert len(g.segments) == 2
    assert all(len(s.path_ids) == 1 for s in g.segments)
    assert all(p.kind == "state" for p in g.points)  # no artefact point at all
    _assert_faithful(g, paths)
    routes = g.walkable_routes()
    assert ("A", ("1", "2"), "D") not in routes
    assert ("B", ("1", "2"), "C") not in routes


# ── case 5 — nested fork inside a shared trunk ────────────────────────────────────────

def test_case5_nested_fork_inside_a_shared_trunk() -> None:
    """A trunk walked by {p1,p2,p3} contains a stretch walked by only {p1,p2}."""
    paths = [
        _p("p1", "A", ("1", "2", "3", "4"), "C"),
        _p("p2", "A", ("1", "2", "3", "5"), "D"),
        _p("p3", "A", ("1", "2", "6"), "E"),
    ]

    g = bundle_paths(paths)

    trunk = _seg_by_actions(g, ("1", "2"))
    assert sorted(trunk.path_ids) == ["p1", "p2", "p3"]
    inner = _seg_by_actions(g, ("3",))
    assert sorted(inner.path_ids) == ["p1", "p2"]
    assert inner.path_ids < trunk.path_ids  # strict subset — the nested case falls out
    assert g.point(trunk.to_point).kind == "branch"
    assert g.point(inner.to_point).kind == "branch"
    _assert_faithful(g, paths)


# ── the guarantees themselves ─────────────────────────────────────────────────────────

def test_an_internal_position_never_merges_into_a_state_point() -> None:
    """``A --[1,2]--> C`` must not be redrawn as passing through ``X`` just because
    ``A --[1]--> X`` exists — that would invent a state mid-chain, which Phase 1 deleted."""
    paths = [_p("p1", "A", ("1", "2"), "C"), _p("p2", "A", ("1",), "X")]

    g = bundle_paths(paths)

    assert g.reconstruct("p1") == ("A", ("1", "2"), "C")
    assert ("A", ("1", "2"), "X") not in g.walkable_routes()
    assert ("A", ("1",), "C") not in g.walkable_routes()
    _assert_faithful(g, paths)


def test_a_branch_merge_point_licenses_no_crossed_route() -> None:
    """The hard case for "nenhuma conectividade visual falsa": a point with several segments in
    AND several out. The path sets are what forbid taking p3's entry into p2's exit."""
    paths = [
        _p("p1", "A", ("1", "2"), "C"),
        _p("p2", "A", ("1", "3"), "C"),
        _p("p3", "B", ("4", "2"), "C"),
    ]

    g = bundle_paths(paths)

    kinds = {p.kind for p in g.points if p.state_key is None}
    assert "branch-merge" in kinds
    assert ("B", ("4", "3"), "C") not in g.walkable_routes()
    _assert_faithful(g, paths)


def test_identical_paths_share_one_stroke_and_a_single_path_is_untouched() -> None:
    solo = [_p("p1", "A", ("1", "2", "3"), "C")]
    g = bundle_paths(solo)
    assert len(g.segments) == 1 and g.segments[0].actions == ("1", "2", "3")
    _assert_faithful(g, solo)

    twins = [_p("p1", "A", ("1", "2"), "C"), _p("p2", "A", ("1", "2"), "C")]
    g2 = bundle_paths(twins)
    assert len(g2.segments) == 1
    assert sorted(g2.segments[0].path_ids) == ["p1", "p2"]
    _assert_faithful(g2, twins)


def test_a_repeated_action_never_makes_a_path_cross_itself() -> None:
    """Regression, found on the PUBLIC corpus, not invented here. ``back take --[t,t,t,t]-->
    back take`` and its two-attempt sibling share a prefix AND a suffix, and merging both put the
    long path's 1st and 3rd gaps on ONE point — it then crossed itself, `walkable_routes` stopped
    being its own chain, and the drawing licensed 19 routes nobody swam while losing 3 that
    happened. The merge that would do it is refused (`_Union._owners`)."""
    paths = [
        _p("p1", "A", ("t", "t", "t", "t"), "A"),
        _p("p2", "A", ("t", "t"), "A"),
        _p("p3", "A", ("t",), "A"),
    ]

    g = bundle_paths(paths)

    for p in paths:  # no point is left twice by the same path
        walked = [s.from_point for s in g.segments_of(p.path_id)]
        assert len(walked) == len(set(walked)), (p.path_id, walked)
    _assert_faithful(g, paths)
    assert ("A", ("t", "t", "t"), "A") not in g.walkable_routes()


def test_a_self_loop_path_still_reconstructs() -> None:
    """``start top --[headquarters pass]--> start top`` — real, in the owner's own bundle. Both
    endpoints are the same state point, so there is no in-degree-0 entry to infer."""
    paths = [_p("p1", "A", ("x",), "A"), _p("p2", "A", ("x", "y"), "B")]

    g = bundle_paths(paths)

    assert g.reconstruct("p1") == ("A", ("x",), "A")
    _assert_faithful(g, paths)


def test_a_run_anchored_at_neither_end_is_deliberately_not_bundled() -> None:
    """``A --[1,2,3]--> C`` and ``B --[4,2,5]--> D`` both walk a ``2`` in the middle. They keep
    their own: two chains that neither open nor close in the same place do not share a base,
    they reuse a verb — see the module docstring for why a free-standing k-gram index is the
    wrong answer here."""
    paths = [_p("p1", "A", ("1", "2", "3"), "C"), _p("p2", "B", ("4", "2", "5"), "D")]

    g = bundle_paths(paths)

    assert all(len(s.path_ids) == 1 for s in g.segments)
    assert all(p.kind == "state" for p in g.points)
    _assert_faithful(g, paths)


def test_output_is_deterministic_and_input_order_insensitive() -> None:
    paths = [
        _p("p1", "A", ("1", "2", "3"), "C"),
        _p("p2", "A", ("1", "2", "4"), "D"),
        _p("p3", "B", ("5", "2", "3"), "C"),
    ]

    a = bundle_paths(paths)
    b = bundle_paths(list(reversed(paths)))

    assert a == b
    assert a == bundle_paths(paths)


def test_duplicate_path_id_is_a_loud_error() -> None:
    with pytest.raises(ValueError, match="duplicate path_id"):
        bundle_paths([_p("p1", "A", ("1",), "B"), _p("p1", "A", ("2",), "C")])


# ── the same invariant over the owner's real bundle ───────────────────────────────────

_OWNER_BUNDLE = Path("/home/vetor/GrapplingArc/user_data_vmfs2000@gmail.com(1).json")


def test_reconstruction_invariant_holds_on_the_owners_real_bundle() -> None:
    """Private, owner-only data (LGPD) — skipped anywhere it is not on disk."""
    if not _OWNER_BUNDLE.is_file():
        pytest.skip("owner bundle not present on this machine")
    from scripts.render_map_prototypes import build_aggregate, render_paths

    bundle: dict[str, Any] = json.loads(_OWNER_BUNDLE.read_text(encoding="utf-8"))
    paths = render_paths(build_aggregate(bundle))
    assert paths, "the owner's bundle should compile to at least one render path"

    g = bundle_paths(paths)

    for p in paths:
        assert g.reconstruct(p.path_id) == (p.source, p.actions, p.target), p.path_id
    assert g.walkable_routes() == {(p.source, p.actions, p.target) for p in paths}
