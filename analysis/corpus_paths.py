"""Public-corpus bout sequences → the "edge = path" payload the public site renders.

Layer 1 → 4 of the owner's four (``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`` §10), for the
PUBLIC data only: a bout's ``Match.sequence`` is compiled by ``analysis.chain_compiler`` into
states + transitions carrying an ordered ``actions[]``, the occurrences become
``analysis.path_bundling.RenderPath``s, the bundler fuses their shared contiguous runs, and
``analysis.flow_layout`` fixes every point's world coordinates. The client only draws.

This is the same pipeline ``scripts/render_map_prototypes.py`` variant 13 runs over the OWNER'S
PRIVATE App bundle. It is deliberately a second front door rather than an import of that script:
the private side aggregates ``you``/``partner`` from a user's logged rounds, the public side
aggregates two REAL athletes from a competition sequence. Same three analysis modules
underneath (``chain_compiler`` → ``path_bundling`` → ``path_metrics``/``flow_layout``); only
the actor model and the perspective rule differ, and those are exactly the two things that must
NOT be shared between a private bundle and a public artefact.

**Privacy (root CLAUDE.md).** Everything here derives from ``matches.sequence`` — competition
footage, already published by the event. Nothing in this module opens ``graphs``, and the only
private-capable input it can even accept is ``rating_of``, which the caller supplies; the site
callers pass athlete (``owner_kind='athlete'``) ratings. There is no code path from a user
bundle into this module.

Public entry points, in pipeline order::

    aggregate_bouts(bouts)      # [[{label,type,side,...}, ...], ...] -> PathAggregate
    render_paths(agg)           # layer 2 — one RenderPath per aggregated occurrence
    path_payload(agg)           # layers 3+4 — {nodes, links, paths, stats} for site/graph.js

``path_payload``'s output is the site bundle contract: ``nodes`` carry fixed ``x``/``y`` +
``pin``, ``links`` carry ``actions[]`` + ``pathIds`` (the multi-label field ``site/graph.js``
was missing, §0.2), ``paths`` carry the per-occurrence metrics.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from analysis.chain_compiler import ChainEdge, ChainState, compile_two_sided
from analysis.flow_layout import (
    ANCHOR_STRUCTURES,
    DEFAULT_ANCHOR_STRUCTURE,
    flow_layout,
)
from analysis.markov_weights import block_for_family, load_markov_weights
from analysis.path_bundling import RenderPath, Segment, bundle_paths
from analysis.path_metrics import PathMetrics, path_metrics
from analysis.taxonomy_kind import load_inference_table, orientation_of

__all__ = [
    "PathAggregate",
    "aggregate_bouts",
    "path_payload",
    "render_paths",
]

# site/graph.js's own category vocabulary (`CAT` there); anything else falls back to 'control'.
_CATS = frozenset(
    {"guard", "pass", "sweep", "takedown", "control", "submission", "escape", "transition"}
)
_FINISH_KEY = "finish"
_FINISH_COLOR = "#facc15"   # graph.js has no per-node yellow; the anchor carries its own
_START_COLOR = "#34d399"
_JUNCTION_COLOR = "#6b7280"  # scaffolding dot — never a technique, so never a category hue
_SIDES = ("a", "b")

# Anchors are the map's FRAME, so their size is fixed (owner 2026-08-27) — deriving it from
# usage made the landmark grow and shrink between renders.
_ANCHOR_SIZE = 3

# The anchors' labels come from ``data/taxonomy/inference_table.json`` in pt-BR (the App's own
# locale). Site copy is English (GrapplingArc AGENTS.md rule 4), and the label must follow the
# node_key AFTER ``_perspective_key`` has mirrored it — reading the compiled state's own
# ``label`` there prints "Por Cima" on the node the flip just renamed ``start bottom``.
_ANCHOR_LABELS = {
    "start top": "Top", "start bottom": "Bottom", "start neutral": "Neutral",
    _FINISH_KEY: "Finish",
}


def _generic_action_label(key: str) -> str | None:
    """English wording for a generic (rule-invented) action, or ``None`` when the key is not
    one. ``data/taxonomy/inference_table.json`` labels the generics in pt-BR (the App's locale)
    and the site's copy is English, but the vocabulary's own ``action_key`` already IS the
    English name — so title-casing the key is the translation, with no second table to drift.
    A key that the corpus also LOGS never reaches here: the corpus's own wording is registered
    in ``display_labels`` and wins (the `sweep`/`reversal`/`guard pass` collision, §8)."""
    table = load_inference_table().get("generic_actions", {})
    return key.title() if key in table else None


def _clamp3(n: float) -> int:
    return 1 if n <= 1 else (2 if n == 2 else 3)


def _cat_of(event_type: str) -> str:
    return event_type if event_type in _CATS else "control"


def _generic(node_key: str) -> Mapping[str, Any]:
    entry = load_inference_table().get("generic_states", {}).get(node_key)
    return entry if isinstance(entry, Mapping) else {}


def _is_anchor(node_key: str) -> bool:
    return _generic(node_key).get("role") == "anchor"


def _is_shared(node_key: str) -> bool:
    """A generic state the table flags ``shared``: a situation NEITHER athlete owns."""
    return bool(_generic(node_key).get("shared"))


def _perspective_key(node_key: str, side: str) -> str:
    """The map has ONE vertical axis, so both athletes' anchors have to mean the same thing on
    it. The compiler is actor-agnostic — it names the opening anchor from the action's own
    orientation, so athlete B's chain that opens with a pass names ``start top`` meaning *B*
    was on top. Read from athlete A's side (the page's left athlete, the dossier's owner) that
    is the bottom. Mirroring here, never in the compiler, keeps ``start top``/``start bottom``
    meaning what the axis says. Same rule as the private prototype's own ``_perspective_key``."""
    if side != "b" or not _is_anchor(node_key):
        return node_key
    return {"start top": "start bottom", "start bottom": "start top"}.get(node_key, node_key)


def _actor_for(node_key: str, side: str, *, collapse: bool) -> str:
    """Which fighter slot a state belongs to. An ANCHOR or a ``shared`` generic is never
    actor-qualified — the frame is one frame, and a situation nobody owns counted twice is one
    physical event drawn as two. ``collapse`` folds every state onto side ``a``: the Ocean is
    the corpus's technique space, where A's mount and B's mount are the same fact."""
    if collapse or _is_anchor(node_key) or _is_shared(node_key):
        return "a"
    return side


def _qid(side: str, node_key: str, *, collapse: bool) -> str:
    """Node id qualified by fighter — A's closed guard is not B's closed guard. Anchors and
    shared generics are the documented exception (``_actor_for``)."""
    return node_key if _actor_for(node_key, side, collapse=collapse) == "a" else f"opp:{node_key}"


def _anchor_slot(node_key: str, side: str, structure: str) -> str | None:
    """This state's vertex in the anchor frame, or ``None`` for an ordinary node."""
    if node_key == _FINISH_KEY:
        if ANCHOR_STRUCTURES[structure]["unified_finish"]:
            return "finish"
        return "finish_opp" if side == "b" else "finish_you"
    if _is_anchor(node_key):
        return orientation_of(node_key)  # 'top' | 'bottom' | 'neutral' — same key set
    return None


class PathAggregate:
    """Unique states / path-occurrences across a set of bouts.

    States key on ``(node_key, fighter)``; a transition keys on the WHOLE ordered action
    sequence — two occurrences whose first action matches but whose trails diverge are two
    different paths and must not share a bucket (the Fase 1 key, not the ``actions[0]`` one)."""

    def __init__(self, *, collapse_actors: bool = False) -> None:
        self.collapse_actors = collapse_actors
        self.states: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, tuple[str, ...], str], dict[str, Any]] = {}
        # One representative ChainEdge per key — path_metrics is keyed on a ChainEdge, and every
        # occurrence under one key carries the same action sequence/terminality by construction.
        self.edge_sample: dict[tuple[str, str, tuple[str, ...], str], ChainEdge] = {}
        self.display_labels: dict[str, str] = {}

    # ── ingestion ───────────────────────────────────────────────────────────────────
    def register_labels(self, events: Iterable[Mapping[str, Any]]) -> None:
        """Canonical key → the corpus's own wording, first seen wins (display only)."""
        from analysis.names import _normalize_name, canonicalize

        for ev in events:
            label = str(ev.get("label") or "")
            if label:
                self.display_labels.setdefault(canonicalize(_normalize_name(label)), label)

    def add_state(self, state: ChainState, side: str) -> None:
        if side not in _SIDES:
            return
        node_key = _perspective_key(state.node_key, side)
        actor = _actor_for(node_key, side, collapse=self.collapse_actors)
        key = (node_key, actor)
        row = self.states.get(key)
        if row is None:
            self.states[key] = {
                "node_key": node_key,
                "label": self.display_labels.get(node_key) or state.label,
                "type": state.type,
                "actor": actor,
                "count": 1,
            }
        else:
            row["count"] += 1

    def add_edge(
        self, edge: ChainEdge, side: str, ts_of: Callable[[int], int | None] | None = None
    ) -> None:
        if side not in _SIDES:
            return
        source = _perspective_key(edge.source_key, side)
        target = _perspective_key(edge.target_key, side)
        actor = "a" if self.collapse_actors else side
        action_seq = tuple(a.key for a in edge.actions)
        key = (source, target, action_seq, actor)
        row = self.edges.get(key)
        if row is None:
            # Video seek (breakdown pages): an ACTION is what happened at a moment, and in this
            # model the action rides the edge — so the timestamp does too. FIRST occurrence
            # wins, same convention as the display label: across many bouts a relation has many
            # moments and no single one is "the" one, so only a single-bout payload (which is
            # what a breakdown is) gets a meaningful seek out of this.
            action_ts = tuple(
                (ts_of(a.source_event_index)
                 if ts_of is not None and a.source_event_index is not None else None)
                for a in edge.actions
            )
            self.edges[key] = {
                "source": source,
                "target": target,
                "actor": actor,
                "count": 1,
                "actions": action_seq,
                "action_labels": tuple(
                    self.display_labels.get(a.key) or _generic_action_label(a.key) or a.label
                    for a in edge.actions
                ),
                "action_inferred": tuple(a.inferred for a in edge.actions),
                "action_ts": action_ts,
            }
            self.edge_sample[key] = edge
        else:
            row["count"] += 1


def _ts_reader(events: Sequence[Mapping[str, Any]]) -> Callable[[int], int | None]:
    """``source_event_index`` → that event's own ``ts``, when it carries one. The index is
    already rewritten back to the ORIGINAL position in ``events`` by ``compile_two_sided``."""

    def read(index: int) -> int | None:
        if 0 <= index < len(events):
            ts = events[index].get("ts")
            if isinstance(ts, int) and not isinstance(ts, bool):
                return ts
        return None

    return read


def aggregate_bouts(
    bouts: Iterable[Sequence[Mapping[str, Any]]],
    *,
    collapse_actors: bool = False,
) -> PathAggregate:
    """Compile every bout and fold it into one aggregate.

    Each bout is a list of raw events carrying at least ``label``, ``type`` and ``side``
    (``'a'``/``'b'``); an event with any other side is dropped by the compiler into its own
    audit bucket, never silently. Feeding only one side's events (the dossier case) is valid —
    the compiler already compiles each side independently.
    """
    table = load_inference_table()
    agg = PathAggregate(collapse_actors=collapse_actors)
    for events in bouts:
        if not events:
            continue
        agg.register_labels(events)
        compiled = compile_two_sided(
            events,
            lambda e: (str(e.get("side")) if e.get("side") is not None else None),
            # ``actor`` names WHOSE action it is; on a two-athlete bout that IS the side, and
            # the site's own event rows (``match_breakdown._sequence_view``) carry only the
            # side. Falling back keeps the Fase 2 role rule readable instead of abstaining.
            actor_of=lambda e: (str(e.get("actor") or e.get("side") or "") or None),
            inference_table=table,
        )
        ts_of = _ts_reader(events)
        for side in _SIDES:
            for state in compiled[side].states:
                agg.add_state(state, side)
            for edge in compiled[side].edges:
                agg.add_edge(edge, side, ts_of)
    return agg


def render_paths(agg: PathAggregate) -> list[RenderPath]:
    """Layer 2 — one ``RenderPath`` per aggregated occurrence. Ids come from the sorted
    aggregation key, never from dict order, so two runs over the same corpus agree."""
    collapse = agg.collapse_actors
    out: list[RenderPath] = []
    for i, key in enumerate(sorted(agg.edges)):
        source, target, actions, actor = key
        out.append(
            RenderPath(
                path_id=f"p{i}",
                source=_qid(actor, source, collapse=collapse),
                target=_qid(actor, target, collapse=collapse),
                actions=actions,
                actor=actor,
                count=agg.edges[key]["count"],
            )
        )
    return out


def _metrics_by_path(
    agg: PathAggregate, rating_of: Callable[[str], float | None] | None
) -> dict[str, PathMetrics]:
    block = block_for_family(None, load_markov_weights())
    rate = rating_of if rating_of is not None else (lambda _k: None)
    support: dict[tuple[str, str, str], int] = {}
    for key, row in agg.edges.items():
        rel = (key[0], key[1], key[3])
        support[rel] = support.get(rel, 0) + row["count"]
    out: dict[str, PathMetrics] = {}
    for i, key in enumerate(sorted(agg.edges)):
        rel = (key[0], key[1], key[3])
        out[f"p{i}"] = path_metrics(
            agg.edge_sample[key], support=support[rel], rating_of=rate, block=block
        )
    return out


def _segment_weight(seg: Segment, count_of: Mapping[str, int]) -> int:
    """Thickness = frequency: the summed occurrence count of every path walking this stroke."""
    return sum(count_of.get(pid, 0) for pid in sorted(seg.path_ids))


def _ghost_action_keys(agg: PathAggregate) -> set[str]:
    """Action keys NEVER observed anywhere in this corpus. A key inferred on one path and logged
    on another is not a ghost — ``sweep``/``reversal``/``guard pass`` are both generic verdicts
    and real corpus labels, and ghosting them would call a logged sweep an invention."""
    observed: set[str] = set()
    inferred: set[str] = set()
    for row in agg.edges.values():
        for key, is_inf in zip(row["actions"], row["action_inferred"], strict=True):
            (inferred if is_inf else observed).add(key)
    return inferred - observed


def _index_parallel_links(links: list[dict[str, Any]]) -> None:
    """Two segments between the SAME pair of points draw as one overlapping line with both
    labels stacked on one midpoint. Index by UNORDERED pair (a return edge shares the fan) and
    hand the client a stable slot; a lone link is left untouched and draws straight."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for link in links:
        groups.setdefault(tuple(sorted((link["from"], link["to"]))), []).append(link)
    for group in groups.values():
        if len(group) <= 1:
            continue
        for i, link in enumerate(group):
            link["par"], link["parCount"] = i, len(group)


def path_payload(
    agg: PathAggregate,
    *,
    structure: str = DEFAULT_ANCHOR_STRUCTURE,
    rating_of: Callable[[str], float | None] | None = None,
    min_count: int = 1,
) -> dict[str, Any]:
    """Layers 3 + 4 — the bundled graph, laid out, as the site's ``{nodes, links, paths}``.

    ``min_count`` drops occurrences seen fewer than N times BEFORE bundling. It is a
    legibility gate, not a perf one — the whole public corpus bundles in ~1 s (measured), but
    2 370 paths on one canvas is the hairball ``userDecisionFlow.ts:32-45`` already measured
    and already answered with the same knob (``minEdgeSupport = 2``): the density was the
    problem, the layout never was. Deterministic: the gate is on the count alone, and the
    surviving paths keep their original ids.
    """
    unified = bool(ANCHOR_STRUCTURES[structure]["unified_finish"])
    collapse = agg.collapse_actors
    metrics_by_path = _metrics_by_path(agg, rating_of)
    paths = [p for p in render_paths(agg) if p.count >= min_count]

    opp_finish = _qid("b", _FINISH_KEY, collapse=collapse)
    if unified and opp_finish != _FINISH_KEY:
        paths = [
            RenderPath(
                path_id=p.path_id,
                source=_FINISH_KEY if p.source == opp_finish else p.source,
                target=_FINISH_KEY if p.target == opp_finish else p.target,
                actions=p.actions,
                actor=p.actor,
                count=p.count,
            )
            for p in paths
        ]

    bundled = bundle_paths(paths)
    count_of = {p.path_id: p.count for p in paths}
    actor_of = {p.path_id: p.actor for p in paths}
    labels = {
        key: label
        for row in agg.edges.values()
        for key, label in zip(row["actions"], row["action_labels"], strict=True)
    }
    ghosts = _ghost_action_keys(agg)
    ts_by_action: dict[str, int] = {}
    for row in agg.edges.values():
        for key, ts in zip(row["actions"], row.get("action_ts") or (), strict=False):
            if ts is not None:
                ts_by_action.setdefault(key, ts)
    state_rows = {
        _qid(actor, node_key, collapse=collapse): ((node_key, actor), row)
        for (node_key, actor), row in agg.states.items()
    }

    # node weight feeds the layout's barycentre sweeps — a busy state should not be shuffled
    # around by a one-off neighbour.
    node_weight: dict[str, float] = {}
    for seg in bundled.segments:
        w = float(_segment_weight(seg, count_of))
        node_weight[seg.from_point] = node_weight.get(seg.from_point, 0.0) + w
        node_weight[seg.to_point] = node_weight.get(seg.to_point, 0.0) + w

    anchor_slots: dict[str, str] = {}
    for point in bundled.points:
        if point.state_key is None:
            continue
        found = state_rows.get(point.state_key)
        node_key = found[0][0] if found else point.state_key.removeprefix("opp:")
        side = found[0][1] if found else "a"
        slot = _anchor_slot(node_key, side, structure)
        if slot is not None:
            anchor_slots[point.id] = slot

    # Label bubbles for the layout's relaxation pass: the name each node draws (anchors carry
    # their own display label) and the joined action sequence each stroke draws. English on this
    # side — the site's locale — which is exactly why the count is the caller's to supply.
    label_len: dict[str, int] = {}
    for point in bundled.points:
        if point.state_key is None:
            continue
        found = state_rows.get(point.state_key) or state_rows.get(opp_finish)
        if found is None:
            continue
        (node_key, _side), row = found
        text = _ANCHOR_LABELS.get(node_key, str(row["label"])) \
            if _anchor_slot(node_key, "a", structure) else str(row["label"])
        label_len[point.id] = len(text)
    for seg in bundled.segments:
        label_len[seg.id] = len(" → ".join(labels.get(k, k) for k in seg.actions))

    pos = flow_layout(bundled, structure=structure, anchor_slots=anchor_slots,
                      weight=node_weight, label_len=label_len)

    nodes: list[dict[str, Any]] = []
    for point in sorted(bundled.points, key=lambda p: p.id):
        x, y = pos[point.id]
        if point.state_key is None:
            nodes.append({
                "id": point.id, "label": "", "kind": point.kind, "cat": "control", "size": 1,
                "color": _JUNCTION_COLOR, "junction": True,
                "x": round(x, 1), "y": round(y, 1), "pin": True,
            })
            continue
        found = state_rows.get(point.state_key)
        if found is not None:
            (node_key, side), row = found
        else:  # defensive: a unified-finish point folded from the opponent's own row
            found_opp = state_rows.get(opp_finish)
            if found_opp is not None:
                (node_key, side), row = found_opp
            else:
                node_key, side = point.state_key, "a"
                row = {"label": point.state_key, "type": "control", "count": 1}
        anchor = _anchor_slot(node_key, side, structure)
        node: dict[str, Any] = {
            "id": point.id,
            "stateKey": node_key,
            "kind": "anchor" if anchor else "state",
            "label": row["label"],
            "cat": _cat_of(str(row["type"])),
            "size": _ANCHOR_SIZE if anchor else _clamp3(int(row["count"])),
            "fighter": side,
            "x": round(x, 1), "y": round(y, 1), "pin": True,
        }
        if anchor:
            node["orient"] = orientation_of(node_key) if node_key != _FINISH_KEY else "finish"
            node["shape"] = "diamond"
            node["color"] = _FINISH_COLOR if node_key == _FINISH_KEY else _START_COLOR
            node["label"] = _ANCHOR_LABELS.get(node_key, node["label"])
            if node_key == _FINISH_KEY and unified:
                # one vertex, two athletes — the split fill is what keeps that honest
                node["split"] = ["a", "b"]
            elif node_key == _FINISH_KEY and side == "b":
                node["label"] = "Finish (opponent)"
        nodes.append(node)

    links: list[dict[str, Any]] = []
    for seg in bundled.segments:
        weight = _segment_weight(seg, count_of)
        acts = [
            {"key": k, "label": labels.get(k, k), "inferred": k in ghosts} for k in seg.actions
        ]
        for act in acts:
            ts = ts_by_action.get(act["key"])
            if ts is not None:
                act["ts"] = ts
        fighters = {actor_of[pid] for pid in seg.path_ids}
        link: dict[str, Any] = {
            "id": seg.id, "from": seg.from_point, "to": seg.to_point,
            "weight": _clamp3(weight), "count": weight, "arrow": True,
            "actions": acts,
            "label": " → ".join(a["label"] for a in acts),
            "pathIds": sorted(seg.path_ids),
            # first moment on this stroke — what a click seeks the bout video to
            **({"ts": next((a["ts"] for a in acts if "ts" in a), None)}
               if any("ts" in a for a in acts) else {}),
            "shared": len(seg.path_ids) > 1,
            "fighter": next(iter(sorted(fighters))) if len(fighters) == 1 else "x",
        }
        if all(a["inferred"] for a in acts):
            link["inf"] = True   # a named generic — still labelled, yields on a label collision
        # A RETURN edge (target at or behind the source in the flow) is a real cycle in a
        # technique map, not a defect — bowed out and thinner, so the forward reading stays loud.
        if pos[seg.to_point][0] <= pos[seg.from_point][0]:
            link["bow"], link["back"] = 0.22, True
        links.append(link)
    _index_parallel_links(links)

    label_of_point = {n["id"]: n["label"] for n in nodes}
    point_of_state = {n["stateKey"]: n["id"] for n in nodes if n.get("stateKey")}
    path_rows: list[dict[str, Any]] = []
    for p in paths:
        m = metrics_by_path[p.path_id]
        src_point = point_of_state.get(p.source, f"s:{p.source}")
        tgt_point = point_of_state.get(p.target, f"s:{p.target}")
        path_rows.append({
            "id": p.path_id, "actor": p.actor, "count": p.count,
            "source": src_point, "target": tgt_point,
            "sourceLabel": label_of_point.get(src_point, p.source),
            "targetLabel": label_of_point.get(tgt_point, p.target),
            "actions": [labels.get(k, k) for k in p.actions],
            "length": m.length, "observed": m.observed,
            "observedRatio": round(m.observed_ratio, 3),
            "support": m.support, "terminal": m.terminal, "roleDelta": m.role_delta,
            "strength": None if m.strength is None else round(m.strength, 1),
        })

    lengths: dict[str, int] = {}
    for p in paths:
        lengths[str(len(p.actions))] = lengths.get(str(len(p.actions)), 0) + 1
    shared_actions = sum(len(s.actions) for s in bundled.segments if len(s.path_ids) > 1)
    total_actions = sum(len(s.actions) for s in bundled.segments)

    return {
        "nodes": nodes,
        "links": links,
        "paths": path_rows,
        "stats": {
            "paths": len(paths),
            "segments": len(bundled.segments),
            "states": sum(1 for p in bundled.points if p.kind == "state"),
            "branchPoints": sum(
                1 for p in bundled.points if p.kind in ("branch", "branch-merge")
            ),
            "mergePoints": sum(1 for p in bundled.points if p.kind in ("merge", "branch-merge")),
            "sharedActionPct": (
                0.0 if not total_actions else round(100.0 * shared_actions / total_actions, 1)
            ),
            "lengths": lengths,
        },
    }
