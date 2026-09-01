"""Render paths -> bundled visual graph (Phase 4 of docs/taxonomy/03_ARESTA_COMO_CAMINHO.md).

An edge is a PATH now: `state --[a1, a2, a3]--> state`. Drawing one polyline per occurrence
buries the reading the owner asked for ("de onde compartilham base, onde divergem, onde
reconvergem"). This module is the middle layer that answers it, and it is pure Python on
purpose: the algorithm is where the correctness lives, the renderer only draws what comes out.

Layer 2 (``RenderPath``) -> layer 3 (``BundledGraph``) of the owner's four:

    semantic graph -> render paths -> BUNDLED VISUAL GRAPH -> renderer

**What a segment is.** A maximal contiguous run of actions traversed by EXACTLY one set of
paths, between two points. ``[1,2]`` is one segment only while every path that walks the ``1``
also walks the ``2`` and nobody else joins in between; the moment a third path shares just the
``2``, the run splits, because the two halves no longer carry the same set. That is the whole
definition — everything below is bookkeeping for it.

**How the sharing is decided.** Only canonical action-key equality, never a label, never a
similarity score. Two boundary positions (the gap before action ``i`` of a path) become the same
point when they agree on a PREFIX (same source state + same actions so far) or on a SUFFIX (same
remaining actions + same target state), transitively. Prefix agreement is the forward trie,
suffix agreement the inverted one, and TRANSITIVITY is what turns those two into the contiguous
k-gram index the plan asks for: ``A --[1,2,3]--> C`` and ``B --[5,2,3]--> C`` share the internal
``2`` through their common suffix even though their sources differ.

What that deliberately does NOT bundle is a run anchored at NEITHER end — ``A --[1,2,3]--> C``
and ``B --[4,2,5]--> D`` both walk a ``2`` in the middle and keep their own. Merging those would
be a free-standing k-gram index, and the claim it makes is wrong: two chains that neither start
in the same place nor end in the same place do not share a base, they merely reuse a verb. It
would also collapse every mid-chain ``sweep`` in the corpus onto one stroke and hand the reader a
crowd of ``branch-merge`` points, which is the exact shape of "false visual connectivity" the
owner ruled out. If a future reading wants it, it is a third pass over the same two dicts.

**A state is never inferred, so an internal position never merges into a state point.** If
``A --[1,2]--> C`` and ``A --[1]--> X`` shared a point after the ``1``, the picture would claim
the first path passes through ``X`` — an invented state in the middle of a chain, which is
exactly what Phase 1 deleted. Internal positions merge only with internal positions.

**Why false recombination cannot happen.** Every segment carries the set of ``path_id`` that
walk it. A route exists only where a single path_id survives the whole walk, so a
``branch-merge`` point (several in, several out) never licenses in-of-p1 + out-of-p2: the
intersection is empty. ``reconstruct``/``walkable_routes`` are that guarantee made runnable, and
``tests/test_path_bundling.py`` asserts BOTH directions — every input path comes back exactly,
and nothing else does.

Determinism (cicatriz #10): every dict is built in sorted key order, no ``set`` is ever iterated
without ``sorted()``, and ids are assigned from a canonical sort key, not from insertion order.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BundledGraph",
    "Point",
    "RenderPath",
    "Segment",
    "bundle_paths",
]


@dataclass(frozen=True)
class RenderPath:
    """One OCCURRENCE of a path, expanded for drawing only (layer 2).

    ``actions`` are canonical action keys (``canonicalize(_normalize_name(label))``) — the
    identity invariant 1 of the contract. ``source``/``target`` are whatever node id the caller
    uses for a state (the prototype passes actor-qualified ids, so your mount and the opponent's
    never collide); this module treats them as opaque strings and only ever compares them.
    ``count`` is how many times the occurrence was seen, ``metrics`` an opaque
    ``analysis.path_metrics.PathMetrics`` carried through untouched.
    """

    path_id: str
    source: str
    target: str
    actions: tuple[str, ...]
    actor: str
    count: int = 1
    metrics: Any | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Point:
    """A junction in the drawn graph. ``state`` points are real (a vertex of the semantic
    graph); ``branch``/``merge``/``branch-merge`` are VISUAL ARTEFACTS — they exist so shared
    ink can fork and rejoin, they are never persisted, never exported, never a node_key."""

    id: str
    kind: str  # 'state' | 'branch' | 'merge' | 'branch-merge'
    state_key: str | None


@dataclass(frozen=True)
class Segment:
    """One stroke of shared ink: the contiguous ``actions`` that ``path_ids`` all walk, in that
    order, from ``from_point`` to ``to_point``."""

    id: str
    actions: tuple[str, ...]
    path_ids: frozenset[str]
    from_point: str
    to_point: str


@dataclass(frozen=True)
class BundledGraph:
    segments: tuple[Segment, ...]
    points: tuple[Point, ...]
    # path_id -> the point its walk STARTS at. A closed walk (``A --[x]--> A``, which the owner's
    # own bundle has: ``start top --[headquarters pass]--> start top``) has no in-degree-0 point
    # to infer an entry from, and guessing one would silently rotate the reconstruction. The
    # bundler knows it for free, so it says it — the ORDER is still derived, never stored.
    path_entry: dict[str, str] = field(default_factory=dict)

    def point(self, point_id: str) -> Point:
        for p in self.points:
            if p.id == point_id:
                return p
        raise KeyError(point_id)

    def segments_of(self, path_id: str) -> tuple[Segment, ...]:
        """This path's own segments, in walking order — the reconstruction the invariant test
        rides on, and what the renderer highlights when one occurrence is selected."""
        mine_list = [s for s in self.segments if path_id in s.path_ids]
        if not mine_list:
            return ()
        mine = {s.from_point: s for s in mine_list}
        if len(mine) != len(mine_list):  # pragma: no cover — a broken bundle, never a valid one
            raise ValueError(f"path {path_id!r} leaves one point twice — bundle is not a walk")
        start = self.path_entry.get(path_id)
        if start is None:
            incoming = {s.to_point for s in mine_list}
            starts = [p for p in sorted(mine) if p not in incoming]
            if len(starts) != 1:  # pragma: no cover
                raise ValueError(f"path {path_id!r} has no unique entry point: {starts}")
            start = starts[0]
        out: list[Segment] = []
        cursor: str | None = start
        while cursor is not None and cursor in mine and len(out) < len(mine_list):
            seg = mine[cursor]
            out.append(seg)
            cursor = seg.to_point
        if len(out) != len(mine_list):  # pragma: no cover
            raise ValueError(f"path {path_id!r} does not walk a single chain from {start!r}")
        return tuple(out)

    def reconstruct(self, path_id: str) -> tuple[str, tuple[str, ...], str]:
        """``(source, actions, target)`` rebuilt by walking only this path's own segments."""
        segs = self.segments_of(path_id)
        if not segs:
            raise KeyError(path_id)
        actions = tuple(a for s in segs for a in s.actions)
        return (
            self.point(segs[0].from_point).state_key or segs[0].from_point,
            actions,
            self.point(segs[-1].to_point).state_key or segs[-1].to_point,
        )

    def walkable_routes(self) -> set[tuple[str, tuple[str, ...], str]]:
        """EVERY route the drawing licenses: all walks from a state point to a state point whose
        segments share at least one path_id all the way through. The "no phantom route" proof —
        if this set is bigger than the input, the picture claims something the data never said.

        Terminates because each segment is used at most once per walk (a path_id's own chain is
        acyclic by construction, and the running intersection can only shrink)."""
        out_of: dict[str, list[Segment]] = {}
        for seg in self.segments:
            out_of.setdefault(seg.from_point, []).append(seg)
        kind_of = {p.id: p.kind for p in self.points}
        state_key = {p.id: p.state_key for p in self.points}
        routes: set[tuple[str, tuple[str, ...], str]] = set()

        def walk(at: str, alive: frozenset[str], acc: tuple[str, ...], src: str,
                 used: frozenset[str]) -> None:
            if acc and kind_of[at] == "state":
                routes.add((src, acc, state_key[at] or at))
                return
            for seg in sorted(out_of.get(at, []), key=lambda s: s.id):
                if seg.id in used:
                    continue
                nxt = alive & seg.path_ids
                if not nxt:
                    continue
                walk(seg.to_point, nxt, acc + seg.actions, src, used | {seg.id})

        every = frozenset(pid for s in self.segments for pid in s.path_ids)
        for p in sorted(self.points, key=lambda p: p.id):
            if p.kind != "state":
                continue
            walk(p.id, every, (), p.state_key or p.id, frozenset())
        return routes


class _Union:
    """Union-find over boundary positions. Ranked by nothing on purpose — path compression is
    enough at this size, and a rank array is one more thing to keep deterministic.

    ``union`` can REFUSE, and that refusal is the fidelity guarantee. See ``_owners``."""

    def __init__(self) -> None:
        self._parent: dict[tuple[str, int], tuple[str, int]] = {}
        # root -> the paths that already have a position in this class. Merging two classes that
        # share a path would let that path visit one point TWICE, and a self-crossing walk is
        # what breaks the whole model: `walkable_routes` stops being the path's own chain, so the
        # picture starts licensing routes nobody swam. MEASURED on the public corpus (668 paths,
        # 989 occurrences): without this refusal, 20 paths crossed themselves, and the drawing
        # licensed 19 routes that never happened while losing 3 that did — e.g. `half guard
        # --[sweep, sweep]--> start top`, assembled out of two halves of `back take --[rear naked
        # choke, sweep]--> start top`. Repeated identical actions are what produce it (four
        # `triangle attempt` in a row bundles its own 1st and 3rd gap together through a
        # two-attempt sibling path), and the corpus is full of them.
        self._owners: dict[tuple[str, int], set[str]] = {}

    def find(self, x: tuple[str, int]) -> tuple[str, int]:
        if x not in self._parent:
            self._parent[x] = x
            self._owners[x] = {x[0]}
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def force(self, a: tuple[str, int], b: tuple[str, int]) -> None:
        """Merge unconditionally — only for STATE positions, where the identity is the state
        itself and a path's own two endpoints are allowed to be the same point."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        lo, hi = (ra, rb) if ra <= rb else (rb, ra)
        self._parent[hi] = lo
        self._owners[lo] |= self._owners.pop(hi)

    def union(self, a: tuple[str, int], b: tuple[str, int]) -> bool:
        """Merge, unless it would make a path cross itself. Returns whether it happened."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if self._owners[ra] & self._owners[rb]:
            return False
        # Deterministic winner: the smaller position key, never insertion order. Which candidate
        # gets refused therefore depends on the (sorted) order the merges are attempted in —
        # deterministic, and no more arbitrary than any other maximal set of compatible merges.
        lo, hi = (ra, rb) if ra <= rb else (rb, ra)
        self._parent[hi] = lo
        self._owners[lo] |= self._owners.pop(hi)
        return True


def _grouped(pairs: Iterable[tuple[Any, tuple[str, int]]]) -> list[list[tuple[str, int]]]:
    """key -> positions, buckets and their contents both in sorted order."""
    buckets: dict[Any, list[tuple[str, int]]] = {}
    for key, pos in pairs:
        buckets.setdefault(key, []).append(pos)
    return [sorted(buckets[k]) for k in sorted(buckets, key=repr)]


def bundle_paths(paths: Sequence[RenderPath]) -> BundledGraph:
    """Fuse the paths into shared segments. Pure, deterministic, order-insensitive on input
    (the paths are sorted by ``path_id`` first, so two callers building the same set in a
    different order get byte-identical output)."""
    ordered = sorted(paths, key=lambda p: p.path_id)
    if len({p.path_id for p in ordered}) != len(ordered):
        raise ValueError("duplicate path_id — a RenderPath id must identify one occurrence")
    by_id = {p.path_id: p for p in ordered}

    # ── 1. boundary positions -> points ────────────────────────────────────────────────
    # Position (pid, i) is the gap BEFORE action i; i == 0 is the source state, i == len is the
    # target state. State positions are keyed by the state itself (every path leaving A leaves
    # the one point A); internal positions merge on prefix or suffix agreement, and never with a
    # state position — that would invent a state mid-chain.
    uf = _Union()
    state_of_pos: dict[tuple[str, int], str] = {}
    for p in ordered:
        state_of_pos[(p.path_id, 0)] = p.source
        state_of_pos[(p.path_id, len(p.actions))] = p.target

    # State positions merge by STATE IDENTITY and are never refused: every path leaving A leaves
    # the one point A, and a path whose source IS its target (`back take --[x]--> back take`, real
    # in the corpus) legitimately has both of its own endpoints there.
    for group in _grouped(
        (state, pos) for pos, state in sorted(state_of_pos.items())
    ):
        for other in group[1:]:
            uf.force(group[0], other)

    internal = [
        (p.path_id, i) for p in ordered for i in range(1, len(p.actions))
    ]
    prefix_key = {
        (pid, i): (by_id[pid].source, by_id[pid].actions[:i]) for pid, i in internal
    }
    suffix_key = {
        (pid, i): (by_id[pid].actions[i:], by_id[pid].target) for pid, i in internal
    }
    for keyed in (prefix_key, suffix_key):
        for group in _grouped((key, pos) for pos, key in sorted(keyed.items())):
            for other in group[1:]:
                uf.union(group[0], other)  # may refuse — see `_Union._owners`

    # ── 2. one edge per action occurrence, then dissolve pass-through points ──────────
    # Each edge is (from_root, to_root, actions, path_ids). A point with exactly one edge in and
    # one out is a pass-through: every path entering it leaves by the same edge, so the two runs
    # carry the same set by construction and concatenate into one segment.
    edges: list[dict[str, Any]] = []
    for p in ordered:
        for i, action in enumerate(p.actions):
            edges.append({
                "from": uf.find((p.path_id, i)),
                "to": uf.find((p.path_id, i + 1)),
                "actions": (action,),
                "paths": {p.path_id},
            })

    # Merge parallel duplicates first (two paths walking the identical action between the same
    # two points are ONE stroke, which is the entire point of bundling).
    merged: dict[tuple[Any, Any, tuple[str, ...]], dict[str, Any]] = {}
    for e in edges:
        key = (e["from"], e["to"], e["actions"])
        row = merged.get(key)
        if row is None:
            merged[key] = dict(e)
        else:
            row["paths"] |= e["paths"]
    edges = [merged[k] for k in sorted(merged, key=repr)]

    is_state = {uf.find(pos) for pos in state_of_pos}

    changed = True
    while changed:
        changed = False
        out_of: dict[Any, list[dict[str, Any]]] = {}
        in_to: dict[Any, list[dict[str, Any]]] = {}
        for e in edges:
            out_of.setdefault(e["from"], []).append(e)
            in_to.setdefault(e["to"], []).append(e)
        for pt in sorted(set(out_of) | set(in_to), key=repr):
            if pt in is_state:
                continue
            ins, outs = in_to.get(pt, []), out_of.get(pt, [])
            if len(ins) != 1 or len(outs) != 1 or ins[0] is outs[0]:
                continue
            a, b = ins[0], outs[0]
            edges = [e for e in edges if e is not a and e is not b]
            edges.append({
                "from": a["from"], "to": b["to"],
                "actions": a["actions"] + b["actions"],
                "paths": a["paths"] | b["paths"],
            })
            changed = True
            break  # the in/out maps are stale now — rebuild before the next candidate

    # ── 3. name everything from a canonical sort key, never from insertion order ──────
    live = sorted({e["from"] for e in edges} | {e["to"] for e in edges}, key=repr)
    for pos, state in sorted(state_of_pos.items()):  # an isolated state (no actions) still draws
        root = uf.find(pos)
        if root not in live:
            live.append(root)
    live = sorted(set(live), key=repr)

    state_key_of: dict[Any, str] = {}
    for pos, state in sorted(state_of_pos.items()):
        state_key_of.setdefault(uf.find(pos), state)

    degree_in: dict[Any, int] = {}
    degree_out: dict[Any, int] = {}
    for e in edges:
        degree_out[e["from"]] = degree_out.get(e["from"], 0) + 1
        degree_in[e["to"]] = degree_in.get(e["to"], 0) + 1

    point_id: dict[Any, str] = {}
    points: list[Point] = []
    artefacts = 0
    for root in live:
        if root in state_key_of:
            pid_ = f"s:{state_key_of[root]}"
            points.append(Point(id=pid_, kind="state", state_key=state_key_of[root]))
        else:
            forks, joins = degree_out.get(root, 0) > 1, degree_in.get(root, 0) > 1
            kind = ("branch-merge" if forks and joins else
                    "branch" if forks else
                    "merge" if joins else "branch")
            pid_ = f"j:{artefacts}"
            artefacts += 1
            points.append(Point(id=pid_, kind=kind, state_key=None))
        point_id[root] = pid_

    segments = [
        Segment(id="", actions=e["actions"], path_ids=frozenset(e["paths"]),
                from_point=point_id[e["from"]], to_point=point_id[e["to"]])
        for e in edges
    ]
    segments.sort(key=lambda s: (s.from_point, s.to_point, s.actions, sorted(s.path_ids)))
    segments = [
        Segment(id=f"g:{i}", actions=s.actions, path_ids=s.path_ids,
                from_point=s.from_point, to_point=s.to_point)
        for i, s in enumerate(segments)
    ]

    entry = {p.path_id: point_id[uf.find((p.path_id, 0))] for p in ordered}
    return BundledGraph(segments=tuple(segments), points=tuple(points), path_entry=entry)
