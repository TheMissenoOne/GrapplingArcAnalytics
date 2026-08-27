"""Phase 1 (actions/states migration) — 8 comparison prototypes of ONE user's own map.

    uv run python -m scripts.render_map_prototypes --bundle PATH [--out DIR]

Reads a raw App user bundle (LGPD: private, owner-only data — the JSON never leaves this
machine, output is local-only). For every round, entries are partitioned by ``sequenceId``
(undefined -> the single legacy chain, same convention as the App's
``services/sequencePartition.ts``), then compiled actor-aware with
``analysis.chain_compiler.compile_two_sided`` (you/partner -> side 'a'/'b'). Results are
aggregated across the whole bundle into unique (node_key, actor) states and
(source, target, action_key, actor) edges, each carrying a usage/occurrence count and an
``inferred`` flag (True only if EVERY occurrence was structurally inferred, never observed).

You and your partner are fundamentally different — never the same node just because you both
passed through "mount". Every rendered node id is qualified by actor: your own node_key for
you, ``opp:{node_key}`` for partner (D4/userDecisionFlow's own actor-prefix convention). Edges
stay strictly within one actor (that's what the compiler produces); the two subgraphs only
interconnect through a synthetic **handover** link — derived here, not by the compiler, by
walking each round's raw entries in original order and, on every actor switch, bridging the
outgoing actor's live state (``CompiledChain.state_after_event``) to the incoming actor's first
state. Rendered as a neutral dashed link (``fighter:'x'``), the same convention the public site
already uses for contested links.

Renders 8 self-contained HTMLs (a single patched ``site/graph.js`` copy shared by all of
them — see ``_patch_graph_js``) + an ``index.html`` + ``metrics.json``. Deterministic:
dict/list order follows bundle read order (no unordered ``set`` in the render path),
community detection sorts every input/tie-break (cicatriz #10, same convention as
``analysis.network_metrics.detect_communities``), and ``metrics.json`` is written with
``sort_keys=True`` as a second belt.

**graph.js contract (read before editing, do not invent fields on the ORIGINAL — this module
patches its own COPY, never ``site/graph.js`` itself):** node {id,label,cat, size 1-3,
fighter 'a'|'b'|'x', color?}; link {from,to,fighter,weight 1-3,arrow,dashed}. The stock
renderer reads none of {label on a link, per-node icon, per-node ring} and gates node labels
behind ``cam.k>=1`` — three limitations this module works around by patching the COPY
(``_patch_graph_js``, string-replace, hard error if an anchor drifted) rather than the shared
site file: ``opts.forceLabels`` (always-show labels below a size threshold, computed client-side
from the graph's own node count), ``l.label`` drawn at an edge's midpoint (non-inferred action
edges only — handovers/inferred edges are the noise, they stay unlabelled), ``n.icon`` (a unicode
glyph centred on the node) and ``n.ring`` (a CSS colour string stroked around the node, variant
7's actor border when ``n.color`` is already the category colour). A real per-type/per-link
colour or label needs graph.js itself to grow these fields — that becomes a Phase 5 requirement
on the App's own renderer, not something to sneak into this repo's copy of the shared file.

**Finish + orientation (owner call, 2026-08-27, ADR alongside D1/D2):** the compiler now closes
every chain on the generic state ``finish`` instead of ``scramble`` when it ends on a submission
(``analysis.chain_compiler``'s ``$terminal`` sentinel) — rendered with its own glyph/colour in
every migrated variant (``_apply_finish_style``), never blended into a category or a ghost grey.
Every STATE also has a curated top/bottom/neutral orientation
(``analysis.taxonomy_kind.orientation_of``, keyed on the state's own ``node_key`` — already the
canonical normalized form the table is keyed on, so no extra lookup is needed) — shown as a
discreet ▲/▼ suffix next to a node's label in the side panel only, never on the canvas itself
(the canvas stays uncluttered; ``metrics.json`` carries the corpus-wide counts).
"""

# ruff: noqa: E501  (HTML/JS template strings are content)

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import networkx as nx

from analysis.chain_compiler import ChainEdge, ChainState, CompiledChain, compile_two_sided
from analysis.names import _normalize_name, canonicalize
from analysis.taxonomy_kind import load_inference_table, orientation_of, resolve_library_entry

logger = logging.getLogger(__name__)

_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "processed" / "map_prototypes"
_GRAPH_JS = Path(__file__).resolve().parents[2] / "GrapplingArc" / "site" / "graph.js"
_CATS = {"guard", "pass", "sweep", "takedown", "control", "submission", "escape", "transition"}

_ACTOR_SIDE = {"you": "a", "partner": "b"}
_TYPE_BUCKET = {  # variant 5's action-type -> fighter-slot approximation, see module docstring
    "submission": "a", "takedown": "a",
    "pass": "b", "sweep": "b",
}

# The terminal state D2 now resolves a chain-closing submission to (chain_compiler's
# `$terminal` sentinel + inference_table.json's `submission|$terminal -> finish` row).
_FINISH_KEY = "finish"
_FINISH_ICON = "\U0001f3c1"  # checkered flag
_FINISH_COLOR = "#facc15"

# App's src/types/session.ts NODE_TYPE_ICONS (Bootstrap Icons in the App; approximated here as
# unicode glyphs for canvas fillText — variant 7 only) / NODE_TYPE_COLORS (copied verbatim,
# same hex values, cross-checked against the App file 2026-08-27).
_TYPE_ICONS = {
    "guard": "\U0001f6e1", "submission": "\U0001f525", "control": "▣",
    "transition": "⇄", "sweep": "↻", "escape": "⏏",
    "pass": "⤴", "takedown": "⤵",
}
_TYPE_COLORS = {
    "submission": "#ef4444", "control": "#3b82f6", "transition": "#8b5cf6",
    "guard": "#22c55e", "sweep": "#06b6d4", "takedown": "#ec4899",
    "escape": "#10b981", "pass": "#f97316",
}
# site/graph.js's own FIG.a/FIG.b constants (module docstring's "no per-node stroke" gap) —
# mirrored here as literal hex so variant 7 can draw an actor RING while `color` is taken by
# the category. Keep in sync with graph.js's `const FIG` if that palette ever changes.
_FIG_HEX = {"a": "#4d86ff", "b": "#fc4c02"}


def _clamp3(n: float) -> int:
    return 1 if n <= 1 else (2 if n == 2 else 3)


def _qid(actor: str, node_key: str) -> str:
    """Node id qualified by actor — you and partner NEVER share a node, even on the same
    node_key (they are fundamentally different states: your closed guard is not their closed
    guard). ``opp:`` prefix convention per D4/userDecisionFlow."""
    return node_key if actor == "you" else f"opp:{node_key}"


def _orient_badge(node_key: str) -> str:
    """▲ top / ▼ bottom / '' neutral — side-panel-only suffix, see module docstring."""
    o = orientation_of(node_key)
    return {"top": " ▲", "bottom": " ▼"}.get(o, "")


def _apply_finish_style(node: dict[str, Any], node_key: str) -> None:
    """Finish is the terminal submission target — glyph + colour distinct from every category
    (and from the ghost grey a variant 6 finish would otherwise get, since a chain-closing
    state is always structurally inferred) in every MIGRATED variant. Called last so it
    overrides whatever colour the caller already set."""
    if node_key == _FINISH_KEY:
        node["color"] = _FINISH_COLOR
        node["icon"] = _FINISH_ICON


def _mount_knobs(n_nodes: int) -> dict[str, float]:
    """Layout tuning proportional to node count (owner: "não faz sentido ter um cluster de
    informação") — more nodes get more repulsion + a longer spring rest length so the
    force-sim actually opens up instead of clustering. Formula only, not eyeballed in a real
    browser (no headless-canvas check run here — ponytail: revisit with a playwright
    screenshot pass if a variant still reads as clustered once someone opens it)."""
    n = max(n_nodes, 1)
    charge = round(2600 * (1 + n / 40))
    link_dist = 110 if n <= 25 else max(92, round(110 - (n - 25) * 0.6))
    gravity = round(0.0016 * (1 + n / 60), 5)
    return {"charge": charge, "linkDist": link_dist, "gravity": gravity}


# ── 1. bundle -> compiled chains ────────────────────────────────────────────────

def partition_by_sequence(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Port of the App's ``sequencePartition.partitionEntriesBySequence`` — consecutive runs
    by ``sequenceId``; ``None`` (JSON: absent/undefined) groups together too, the legacy
    single chain. Never reorders/merges/drops."""
    if not entries:
        return []
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_id: str | None = None
    for i, e in enumerate(entries):
        sid = e.get("sequenceId")
        if i == 0 or sid != current_id:
            if current:
                groups.append(current)
            current = []
            current_id = sid
        current.append(e)
    if current:
        groups.append(current)
    return groups


def _side_of(e: dict[str, Any]) -> str | None:
    return _ACTOR_SIDE.get(e.get("actor"))


def _actor_of(e: dict[str, Any]) -> str | None:
    return e.get("actor")


def _resolve_group(
    group: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Bug fix (owner-confirmed, real bundle): the owner's raw entries carry a stale ``type``
    snapshot on 80/114 log lines (an action logged with a state's ``type``), and different
    entries log the SAME technique under different spellings/pt-vs-en grafia — both of which
    make ``compile_chain`` (which classifies + keys nodes/edges off exactly the ``label``/
    ``type`` it's handed) split one technique into several nodes, or misfile an action as a
    state. Fix: resolve every entry's label through the App's technique library
    (``taxonomy_kind.resolve_library_entry`` — the same lookup ``kind_of_entry`` uses) BEFORE
    handing events to ``compile_chain``, so classification and node/action keys are driven by
    the library's canonical English label + trustworthy type, not the logged snapshot. Entries
    outside the library keep their raw label/type unchanged (unresolvable, not misclassified).

    Returns the resolved copy of ``group`` (only ``label``/``type`` overridden, every other
    field — ``actor``, ``sequenceId``, ... — passed through) plus a ``{node/action key:
    ORIGINAL logged label}`` map (first occurrence wins) so the RENDERED node/edge can still
    show the owner's own wording instead of the internal canonical English one — grouping
    changes, display doesn't.
    """
    resolved: list[dict[str, Any]] = []
    display: dict[str, str] = {}
    for e in group:
        raw_label = str(e.get("label", e.get("event_label", "")) or "")
        raw_type = str(e.get("type", e.get("event_type", "")) or "")
        entry = resolve_library_entry(raw_label)
        canon_label, canon_type = entry if entry is not None else (raw_label, raw_type)
        new_e = dict(e)
        new_e["label"], new_e["type"] = canon_label, canon_type
        resolved.append(new_e)
        display.setdefault(canonicalize(_normalize_name(canon_label)), raw_label)
    return resolved, display


class Aggregate:
    """Unique (node_key, actor) states / (source, target, action_key, actor) edges across the
    whole bundle, each with an occurrence count + an inferred flag (True iff EVERY occurrence
    was inferred). Also keeps the RAW (non-deduped) inferred counts for the corpus-wide rate."""

    def __init__(self) -> None:
        self.states: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.handovers: dict[tuple[str, str], dict[str, Any]] = {}
        self.raw_states_total = 0
        self.raw_states_inferred = 0
        self.raw_edges_total = 0
        self.raw_edges_inferred = 0
        # node/action key -> the owner's own logged label (`_resolve_group`, first seen wins) —
        # display only, never affects grouping/classification.
        self.display_labels: dict[str, str] = {}

    def register_display_labels(self, display: dict[str, str]) -> None:
        for key, label in display.items():
            self.display_labels.setdefault(key, label)

    def add_state(self, s: ChainState) -> None:
        if s.actor not in ("you", "partner"):
            return
        self.raw_states_total += 1
        self.raw_states_inferred += 1 if s.inferred else 0
        key = (s.node_key, s.actor)
        row = self.states.get(key)
        if row is None:
            label = self.display_labels.get(s.node_key, s.label)
            self.states[key] = {"node_key": s.node_key, "label": label, "type": s.type,
                                 "actor": s.actor, "count": 1, "inferred": s.inferred}
        else:
            row["count"] += 1
            row["inferred"] = row["inferred"] and s.inferred

    def add_edge(self, e: ChainEdge) -> None:
        if e.actor not in ("you", "partner"):
            return
        self.raw_edges_total += 1
        self.raw_edges_inferred += 1 if e.inferred else 0
        key = (e.source_key, e.target_key, e.action_key, e.actor)
        row = self.edges.get(key)
        if row is None:
            action_label = self.display_labels.get(e.action_key, e.action_label)
            self.edges[key] = {"source": e.source_key, "target": e.target_key,
                                "action_key": e.action_key, "action_label": action_label,
                                "action_type": e.action_type, "actor": e.actor,
                                "count": 1, "inferred": e.inferred}
        else:
            row["count"] += 1
            row["inferred"] = row["inferred"] and e.inferred

    def add_handover(self, from_actor: str, from_key: str, to_actor: str, to_key: str) -> None:
        from_id, to_id = _qid(from_actor, from_key), _qid(to_actor, to_key)
        key = (from_id, to_id)
        row = self.handovers.get(key)
        if row is None:
            self.handovers[key] = {"from": from_id, "to": to_id, "from_actor": from_actor,
                                    "from_key": from_key, "to_actor": to_actor,
                                    "to_key": to_key, "count": 1}
        else:
            row["count"] += 1


def _handovers_in_group(group: list[dict[str, Any]],
                         compiled: dict[str, CompiledChain]) -> list[tuple[str, str, str, str]]:
    """You/partner subgraphs never touch through an edge any more (edges are strictly
    within-actor) — the only interconnection is a HANDOVER: the round's raw entries, in order,
    switch actor mid-stream, and that switch bridges the outgoing actor's live state to the
    incoming actor's first state (``ChainState`` -> ``ChainEdge`` never sees this, only the
    raw actor-interleaved stream does). Returns ``(from_actor, from_key, to_actor, to_key)``
    tuples; a switch is skipped if either side never reached a state yet (nothing to bridge)."""
    pairs: list[tuple[str, str, str, str]] = []
    prev_idx: int | None = None
    prev_actor: str | None = None
    for idx, e in enumerate(group):
        actor = _actor_of(e)
        if actor not in ("you", "partner"):
            continue
        if prev_actor is not None and actor != prev_actor:
            from_side, to_side = _ACTOR_SIDE[prev_actor], _ACTOR_SIDE[actor]
            from_key = compiled[from_side].state_after_event.get(prev_idx)
            to_key = compiled[to_side].state_after_event.get(idx)
            if from_key and to_key:
                pairs.append((prev_actor, from_key, actor, to_key))
        prev_idx, prev_actor = idx, actor
    return pairs


def build_aggregate(bundle: dict[str, Any]) -> Aggregate:
    table = load_inference_table()
    agg = Aggregate()
    for session in bundle.get("sessions", []):
        for round_ in session.get("rounds", []):
            entries = round_.get("entries", []) or []
            for group in partition_by_sequence(entries):
                resolved_group, display = _resolve_group(group)
                agg.register_display_labels(display)
                compiled = compile_two_sided(resolved_group, _side_of, actor_of=_actor_of,
                                              inference_table=table)
                for side in ("a", "b"):
                    for s in compiled[side].states:
                        agg.add_state(s)
                    for e in compiled[side].edges:
                        agg.add_edge(e)
                for from_actor, from_key, to_actor, to_key in _handovers_in_group(group, compiled):
                    agg.add_handover(from_actor, from_key, to_actor, to_key)
    return agg


# ── 2. variant node/link builders ───────────────────────────────────────────────

def _cat_of(type_: str) -> str:
    return type_ if type_ in _CATS else "control"


def _baseline_graphview(bundle: dict[str, Any]) -> dict[str, Any]:
    """Variant 1 — the graph as the App renders it TODAY: ``bundle.graph`` verbatim."""
    graph = bundle.get("graph") or {}
    nodes = []
    for n in graph.get("nodes", []):
        d = n.get("data", {}) or {}
        nodes.append({"id": n["id"], "label": n.get("label", ""),
                      "cat": _cat_of(d.get("type", "")), "size": _clamp3(d.get("usageCount", 1))})
    links = [{"from": e["source"], "to": e["target"], "weight": 1}
             for e in graph.get("edges", []) if e.get("source") and e.get("target")]
    return {"nodes": nodes, "links": links}


def _own_graphview(agg: Aggregate) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Variant 2 — you only. No fighter colouring needed (single actor)."""
    states = {k: v for k, v in agg.states.items() if v["actor"] == "you"}
    edges = [v for v in agg.edges.values() if v["actor"] == "you"]
    nodes = []
    for (node_key, _actor), v in states.items():
        node = {"id": v["node_key"], "label": v["label"], "cat": _cat_of(v["type"]),
                "size": _clamp3(v["count"])}
        _apply_finish_style(node, node_key)
        nodes.append(node)
    links = []
    for v in edges:
        link = {"from": v["source"], "to": v["target"], "weight": _clamp3(v["count"]), "arrow": True}
        if not v["inferred"]:
            link["label"] = v["action_label"]
        links.append(link)
    return {"nodes": nodes, "links": links}, edges


def _two_sided_graphview(states: dict, edges: list, handovers: list[dict[str, Any]]) -> dict[str, Any]:
    """Node per (node_key, actor) — you and partner are fundamentally different states, never
    merged, even on the same node_key. Links: within-actor action edges (blue/orange, labelled
    when not inferred) + neutral dashed HANDOVER links (grey, ``fighter:'x'``, the site's own
    contested-link convention) that bridge across the actor switch — the only interconnection
    between the two subgraphs now that edges never cross actors."""
    nodes = []
    for (node_key, actor), v in states.items():
        node = {"id": _qid(actor, node_key), "label": v["label"], "cat": _cat_of(v["type"]),
                "size": _clamp3(v["count"]), "fighter": _ACTOR_SIDE[actor]}
        _apply_finish_style(node, node_key)
        nodes.append(node)
    links = []
    for v in edges:
        link = {"from": _qid(v["actor"], v["source"]), "to": _qid(v["actor"], v["target"]),
                "weight": _clamp3(v["count"]), "arrow": True, "fighter": _ACTOR_SIDE[v["actor"]]}
        if not v["inferred"]:
            link["label"] = v["action_label"]
        links.append(link)
    links += [{"from": h["from"], "to": h["to"], "weight": _clamp3(h["count"]),
               "arrow": True, "dashed": True, "fighter": "x"} for h in handovers]
    return {"nodes": nodes, "links": links}


def _complete_two_sided(agg: Aggregate) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Variant 3 — every you + every partner element, every handover."""
    states = dict(agg.states)
    edges = list(agg.edges.values())
    handovers = list(agg.handovers.values())
    return _two_sided_graphview(states, edges, handovers), edges, handovers


def _sole_bridge_partner_nodes(you_node_keys: set[str], edges: list[dict[str, Any]],
                                handovers: list[dict[str, Any]]) -> set[str]:
    """Union-find bridge check over the NEW qualified-id graph: a low-usage partner node is kept
    if it connects two OTHERWISE disjoint you-components — i.e. it is the sole path between you
    elements, not just extra partner-side noise. 'you components' = connectivity using only
    edges where both ends are you nodes. Edges never cross actors any more, so the only way a
    partner node touches a you component at all is through a HANDOVER link — that's the bridge
    this now scans, not coincidental same-node_key adjacency (the merge bug this whole change
    removes)."""
    # union-find over you-only edges
    parent = {k: k for k in you_node_keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    you_you_edges = [e for e in edges if e["actor"] == "you"]
    for e in you_you_edges:
        s, t = e["source"], e["target"]
        if s in parent and t in parent:
            union(s, t)

    partner_neighbours: dict[str, set[str]] = {}
    for h in handovers:
        you_key = h["from_key"] if h["from_actor"] == "you" else h["to_key"]
        partner_key = h["to_key"] if h["from_actor"] == "you" else h["from_key"]
        if you_key in parent:
            partner_neighbours.setdefault(partner_key, set()).add(find(you_key))

    return {p for p, comps in partner_neighbours.items() if len(comps) >= 2}


def _selective_states_and_edges(
    agg: Aggregate,
) -> tuple[dict, list[dict[str, Any]], list[dict[str, Any]]]:
    """Variant 4's element set: partner state/edge enters only if usageCount>=2 OR it is the
    sole handover bridge to a you element. Returned separately (not just the graphview) so
    variants 5/6/7/8 can reuse the SAME filtered set instead of reverse-engineering it from
    rendered nodes."""
    you_keys = {k[0] for k in agg.states if k[1] == "you"}
    all_edges = list(agg.edges.values())
    all_handovers = list(agg.handovers.values())
    bridges = _sole_bridge_partner_nodes(you_keys, all_edges, all_handovers)

    kept_partner_keys = {
        k[0] for k, v in agg.states.items()
        if v["actor"] == "partner" and (v["count"] >= 2 or k[0] in bridges)
    }
    states = {k: v for k, v in agg.states.items()
              if v["actor"] == "you" or k[0] in kept_partner_keys}
    kept_qids = {_qid(v["actor"], node_key) for (node_key, _actor), v in states.items()}
    edges = [e for e in all_edges
             if _qid(e["actor"], e["source"]) in kept_qids and _qid(e["actor"], e["target"]) in kept_qids]
    handovers = [h for h in all_handovers if h["from"] in kept_qids and h["to"] in kept_qids]
    return states, edges, handovers


def _hubs_graphview(states: dict, edges: list, handovers: list[dict[str, Any]]) -> dict[str, Any]:
    """Variant 5 — node size by DEGREE percentile (not usage), edges coloured by a 3-bucket
    action-type approximation (see module docstring: graph.js has no per-link colour field).
    Handover links count toward degree too — they're real edges on screen."""
    degree: dict[str, int] = {}
    for e in edges:
        s, t = _qid(e["actor"], e["source"]), _qid(e["actor"], e["target"])
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1
    for h in handovers:
        degree[h["from"]] = degree.get(h["from"], 0) + 1
        degree[h["to"]] = degree.get(h["to"], 0) + 1
    max_deg = max(degree.values(), default=1) or 1

    nodes = []
    for (node_key, actor), v in states.items():
        qid = _qid(actor, node_key)
        d = degree.get(qid, 0)
        node = {"id": qid, "label": v["label"], "cat": _cat_of(v["type"]),
                "size": 1 + round(2 * d / max_deg)}
        _apply_finish_style(node, node_key)
        nodes.append(node)
    links = []
    for e in edges:
        link = {"from": _qid(e["actor"], e["source"]), "to": _qid(e["actor"], e["target"]),
                "weight": _clamp3(e["count"]), "arrow": True,
                "fighter": _TYPE_BUCKET.get(e["action_type"], "x")}
        if not e["inferred"]:
            link["label"] = e["action_label"]
        links.append(link)
    links += [{"from": h["from"], "to": h["to"], "weight": _clamp3(h["count"]),
               "arrow": True, "dashed": True, "fighter": "x"} for h in handovers]
    return {"nodes": nodes, "links": links}


def _ghost_graphview(states: dict, edges: list, handovers: list[dict[str, Any]]) -> dict[str, Any]:
    """Variant 6 — variant 4's selective set, with inferred elements rendered as ghosts:
    dashed + minimum weight/size, node colour a translucent grey (``color`` IS a real node
    field graph.js reads — see module docstring). Ghosting marks INFERRED only, never
    'shared' — you/partner nodes are never merged any more. Finish overrides the ghost grey
    (it is always structurally inferred, but must still read as ITS OWN colour, not noise)."""
    nodes = []
    for (node_key, actor), v in states.items():
        node = {"id": _qid(actor, node_key), "label": v["label"], "cat": _cat_of(v["type"]),
                "size": 1 if v["inferred"] else _clamp3(v["count"]), "fighter": _ACTOR_SIDE[actor]}
        if v["inferred"]:
            node["color"] = "rgba(150,150,160,0.35)"
        _apply_finish_style(node, node_key)
        nodes.append(node)
    links = []
    for e in edges:
        link = {"from": _qid(e["actor"], e["source"]), "to": _qid(e["actor"], e["target"]),
                "arrow": True, "fighter": _ACTOR_SIDE[e["actor"]]}
        if e["inferred"]:
            link["weight"], link["dashed"] = 1, True
        else:
            link["weight"] = _clamp3(e["count"])
            link["label"] = e["action_label"]
        links.append(link)
    links += [{"from": h["from"], "to": h["to"], "weight": _clamp3(h["count"]),
               "arrow": True, "dashed": True, "fighter": "x"} for h in handovers]
    return {"nodes": nodes, "links": links}


def _icons_graphview(states: dict, edges: list, handovers: list[dict[str, Any]]) -> dict[str, Any]:
    """Variant 7 — category icon + colour per node, approximating the App's own
    NODE_TYPE_ICONS/NODE_TYPE_COLORS (src/types/session.ts). Colour is taken by category here,
    so actor is shown as a border RING instead (``n.ring``, ``_FIG_HEX`` — a stroke-custom
    patch on the copy, see module docstring). Same selective element set as variant 4."""
    nodes = []
    for (node_key, actor), v in states.items():
        typ = _cat_of(v["type"])
        node = {"id": _qid(actor, node_key), "label": v["label"], "cat": typ,
                "size": _clamp3(v["count"]), "color": _TYPE_COLORS.get(typ, "#94a3b8"),
                "icon": _TYPE_ICONS.get(typ, ""), "ring": _FIG_HEX[_ACTOR_SIDE[actor]]}
        _apply_finish_style(node, node_key)
        nodes.append(node)
    links = []
    for e in edges:
        link = {"from": _qid(e["actor"], e["source"]), "to": _qid(e["actor"], e["target"]),
                "weight": _clamp3(e["count"]), "arrow": True, "fighter": _ACTOR_SIDE[e["actor"]]}
        if not e["inferred"]:
            link["label"] = e["action_label"]
        links.append(link)
    links += [{"from": h["from"], "to": h["to"], "weight": _clamp3(h["count"]),
               "arrow": True, "dashed": True, "fighter": "x"} for h in handovers]
    return {"nodes": nodes, "links": links}


# ── 2b. variant 8 — collapsible systems (community detection) ──────────────────

def _detect_systems(states: dict, edges: list[dict[str, Any]],
                     handovers: list[dict[str, Any]]) -> dict[str, Any]:
    """Greedy-modularity communities over variant 4's selective two-sided graph — "systems".
    Determinism (cicatriz #10, same convention as ``analysis.network_metrics.detect_communities``):
    nodes/edges added to the ``networkx`` graph in SORTED order, every tie (hub pick, community
    ordering) breaks on a stable sort key, never dict/set iteration order.

    Base is the SELECTIVE set (variant 4), not the full corpus — the same legibility mandate
    that trims variants 5-7 applies here too (a system map over the raw partner noise would
    just be a bigger cluster, not a smaller one)."""
    qid_of = {key: _qid(key[1], key[0]) for key in states}
    g: nx.Graph = nx.Graph()
    for key in sorted(states, key=lambda k: qid_of[k]):
        g.add_node(qid_of[key])

    weights: dict[tuple[str, str], int] = {}
    for e in edges:
        u, v = _qid(e["actor"], e["source"]), _qid(e["actor"], e["target"])
        if u == v or u not in g or v not in g:
            continue
        pair = (u, v) if u < v else (v, u)
        weights[pair] = weights.get(pair, 0) + e["count"]
    for h in handovers:
        u, v = h["from"], h["to"]
        if u == v or u not in g or v not in g:
            continue
        pair = (u, v) if u < v else (v, u)
        weights[pair] = weights.get(pair, 0) + h["count"]
    for u, v in sorted(weights):
        g.add_edge(u, v, weight=weights[(u, v)])

    if g.number_of_edges() == 0:
        comms = [[n] for n in sorted(g.nodes)]
    else:
        comms = [sorted(c) for c in nx.community.greedy_modularity_communities(g, weight="weight")]
    comms = sorted(comms, key=lambda c: (-len(c), c[0]))

    node_of = {qid_of[key]: (key, v) for key, v in states.items()}
    systems: list[dict[str, Any]] = []
    system_of: dict[str, str] = {}
    for idx, members in enumerate(comms):
        hub_qid = min(members, key=lambda qid: (-g.degree(qid), qid))  # max degree, tie by id
        hub_key, hub_v = node_of[hub_qid]
        actor_counts: dict[str, int] = {}
        for qid in members:
            actor = node_of[qid][0][1]
            actor_counts[actor] = actor_counts.get(actor, 0) + 1
        # majority actor, tie broken toward 'you' (owner call)
        actor = "you" if actor_counts.get("you", 0) >= actor_counts.get("partner", 0) else "partner"
        sys_id = f"sys:{idx}"
        for qid in members:
            system_of[qid] = sys_id
        systems.append({
            "id": sys_id, "hub_qid": hub_qid, "hub_key": hub_key[0],
            "label": f"Sistema: {hub_v['label']}", "members": members,
            "actor": actor, "size": _clamp3(len(members)),
        })
    return {"systems": systems, "system_of": system_of}


def _cross_system_links(system_of: dict[str, str], edges: list[dict[str, Any]],
                         handovers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Level-1 edges: aggregate of action edges + handovers whose endpoints land in TWO
    different systems. ``dashed`` iff every crossing link between that pair is a handover (no
    real action edge ever crosses there)."""
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for e in edges:
        u, v = system_of.get(_qid(e["actor"], e["source"])), system_of.get(_qid(e["actor"], e["target"]))
        if u is None or v is None or u == v:
            continue
        row = agg.setdefault((u, v), {"count": 0, "handover_only": True})
        row["count"] += e["count"]
        row["handover_only"] = False
    for h in handovers:
        u, v = system_of.get(h["from"]), system_of.get(h["to"])
        if u is None or v is None or u == v:
            continue
        row = agg.setdefault((u, v), {"count": 0, "handover_only": True})
        row["count"] += h["count"]
    return [
        {"from": u, "to": v, "weight": _clamp3(row["count"]), "arrow": True,
         "dashed": row["handover_only"]}
        for (u, v), row in sorted(agg.items())
    ]


def _systems_level1_view(systems: list[dict[str, Any]],
                          cross_links: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = [{"id": s["id"], "label": s["label"], "cat": "control", "size": s["size"],
              "fighter": _ACTOR_SIDE[s["actor"]]} for s in systems]
    return {"nodes": nodes, "links": cross_links}


def _system_level2(sys_row: dict[str, Any], system_of: dict[str, str], states: dict,
                    edges: list[dict[str, Any]], handovers: list[dict[str, Any]],
                    systems_by_id: dict[str, dict[str, Any]]
                    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One system's own subgraph (nodes/edges/labels as variant 4) + dashed stub links to a
    mini-node per neighbouring system for edges that leave this system — clickable (by id
    convention ``stub:<system_id>``) to navigate there."""
    member_qids = set(sys_row["members"])
    sub_states = {k: v for k, v in states.items() if _qid(k[1], k[0]) in member_qids}
    internal_edges = [e for e in edges
                       if _qid(e["actor"], e["source"]) in member_qids
                       and _qid(e["actor"], e["target"]) in member_qids]
    internal_handovers = [h for h in handovers
                           if h["from"] in member_qids and h["to"] in member_qids]
    gv = _two_sided_graphview(sub_states, internal_edges, internal_handovers)

    stub_nodes: dict[str, dict[str, Any]] = {}
    stub_links: list[dict[str, Any]] = []

    def _stub(this_qid: str, other_qid: str) -> None:
        other_sys = system_of.get(other_qid)
        if other_sys is None or other_sys == sys_row["id"]:
            return
        stub_id = f"stub:{other_sys}"
        if stub_id not in stub_nodes:
            stub_nodes[stub_id] = {"id": stub_id, "label": systems_by_id[other_sys]["label"],
                                    "cat": "control", "size": 1, "fighter": "x"}
        stub_links.append({"from": this_qid, "to": stub_id, "weight": 1,
                            "dashed": True, "fighter": "x"})

    for e in edges:
        u, v = _qid(e["actor"], e["source"]), _qid(e["actor"], e["target"])
        if u in member_qids and v not in member_qids:
            _stub(u, v)
        elif v in member_qids and u not in member_qids:
            _stub(v, u)
    for h in handovers:
        if h["from"] in member_qids and h["to"] not in member_qids:
            _stub(h["from"], h["to"])
        elif h["to"] in member_qids and h["from"] not in member_qids:
            _stub(h["to"], h["from"])

    gv["nodes"] = gv["nodes"] + sorted(stub_nodes.values(), key=lambda n: n["id"])
    gv["links"] = gv["links"] + stub_links
    return gv, internal_edges


# ── 3. HTML rendering ────────────────────────────────────────────────────────────

_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>{title}</title>
<style>
:root{{--bg:#0b0b0f;--panel:#14141a;--line:#26262e;--ink:#e9e9ee;--ink2:#9a9aa6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif;display:flex;height:100vh}}
#canvas{{flex:1;position:relative}}#canvas canvas{{width:100%;height:100%;display:block}}
#side{{width:360px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px}}
h1{{font-size:15px;margin:0 0 4px}}.muted{{color:var(--ink2);font-size:12px;margin-bottom:10px}}
.row{{padding:6px 8px;border:1px solid var(--line);border-radius:8px;margin-bottom:5px;font-size:12px}}
.g{{opacity:.45;border-style:dashed}}
.legend{{font-size:11px;color:var(--ink2);margin:10px 0;line-height:1.6}}
</style></head><body>
<div id="canvas"><canvas id="cv"></canvas></div>
<div id="side"><h1>{title}</h1><div class="muted">{subtitle}</div>
<div class="legend">{legend}</div>
<div id="list">{list_html}</div></div>
<script src="graph.js"></script>
<script>const GV = {graphview};
GAGraph.mount(document.getElementById('cv'),{{mode:'map',nodes:GV.nodes,links:GV.links,pan:true,zoom:true,collide:true,charge:{charge},linkDist:{link_dist},gravity:{gravity},forceLabels: GV.nodes.length < 40}});
</script></body></html>"""

# Variant 8's interactive two-level page — kept as a SEPARATE template (not `.format()`-shared
# with `_PAGE`) so its script body can use real `{`/`}` for JS objects/functions without the
# double-brace escaping that would need everywhere in `_PAGE`; substitution is plain
# `str.replace` on `__TOKEN__` placeholders instead.
_PAGE8 = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>__TITLE__</title>
<style>
:root{--bg:#0b0b0f;--panel:#14141a;--line:#26262e;--ink:#e9e9ee;--ink2:#9a9aa6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif;display:flex;height:100vh}
#canvas{flex:1;position:relative}#canvas canvas{width:100%;height:100%;display:block}
#side{width:360px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px}
h1{font-size:15px;margin:0 0 4px}.muted{color:var(--ink2);font-size:12px;margin-bottom:10px}
.row{padding:6px 8px;border:1px solid var(--line);border-radius:8px;margin-bottom:5px;font-size:12px}
.g{opacity:.45;border-style:dashed}
.legend{font-size:11px;color:var(--ink2);margin:10px 0;line-height:1.6}
#backBtn{display:none;margin-bottom:10px;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 10px;cursor:pointer;font:12px system-ui}
</style></head><body>
<div id="canvas"><canvas id="cv"></canvas></div>
<div id="side">
<button id="backBtn" onclick="show('global', null)">&larr; voltar ao mapa de sistemas</button>
<h1 id="sideTitle"></h1><div class="muted" id="sideSubtitle"></div>
<div class="legend">__LEGEND__</div>
<div id="list"></div></div>
<script src="graph.js"></script>
<script>
const LEVEL1 = __LEVEL1_JSON__;
const LEVEL2 = __LEVEL2_JSON__;
let mounted = null;
function freshCanvas() {
  const old = document.getElementById('cv');
  const next = old.cloneNode(false);
  old.parentNode.replaceChild(next, old);
  return next;
}
function show(level, id) {
  const data = level === 'global' ? LEVEL1 : LEVEL2[id];
  document.getElementById('sideTitle').textContent = data.title;
  document.getElementById('sideSubtitle').textContent = data.subtitle;
  document.getElementById('list').innerHTML = data.listHtml;
  document.getElementById('backBtn').style.display = level === 'global' ? 'none' : 'block';
  if (mounted && mounted.destroy) mounted.destroy();
  const cv = freshCanvas();
  mounted = GAGraph.mount(cv, {
    mode: 'map', nodes: data.gv.nodes, links: data.gv.links, pan: true, zoom: true, collide: true,
    charge: data.charge, linkDist: data.linkDist, gravity: data.gravity,
    forceLabels: data.gv.nodes.length < 40,
    onSelect: function (n) {
      if (!n) return;
      if (level === 'global' && n.id.indexOf('sys:') === 0) show('system', n.id);
      if (level === 'system' && n.id.indexOf('stub:') === 0) show('system', n.id.slice(5));
    }
  });
}
show('global', null);
</script></body></html>"""


def _edge_list_html(edges: list[dict[str, Any]], id_to_label: dict[str, str]) -> str:
    rows = []
    for e in sorted(edges, key=lambda e: (-e["count"], e["source"], e["target"])):
        # look up by the QUALIFIED id — `e["source"]`/`e["target"]` are raw node_keys, and for
        # the partner actor that's not the rendered node's id (`opp:{node_key}`); without
        # qualifying first, every partner-side row fell back to the raw key instead of its
        # label. Root-cause fix (was silently wrong on every two-sided variant's side panel).
        src_id, tgt_id = _qid(e["actor"], e["source"]), _qid(e["actor"], e["target"])
        src = id_to_label.get(src_id, e["source"]) + _orient_badge(e["source"])
        tgt = id_to_label.get(tgt_id, e["target"]) + _orient_badge(e["target"])
        cls = " g" if e.get("inferred") else ""
        rows.append(
            f'<div class="row{cls}">{src} —<b>{e["action_label"]}</b>→ {tgt} '
            f'<span class="muted">({e["actor"]}, x{e["count"]})</span></div>'
        )
    return "".join(rows)


def _write_page(out: Path, filename: str, title: str, subtitle: str, legend: str,
                 graphview: dict[str, Any], edges: list[dict[str, Any]] | None) -> dict[str, float]:
    id_to_label = {n["id"]: n["label"] for n in graphview["nodes"]}
    list_html = _edge_list_html(edges, id_to_label) if edges is not None else ""
    knobs = _mount_knobs(len(graphview["nodes"]))
    html = _PAGE.format(title=title, subtitle=subtitle, legend=legend, list_html=list_html,
                         graphview=json.dumps(graphview, ensure_ascii=False),
                         charge=knobs["charge"], link_dist=knobs["linkDist"], gravity=knobs["gravity"])
    (out / filename).write_text(html, encoding="utf-8")
    return knobs


def _render_variant8(out: Path, states4: dict, edges4: list[dict[str, Any]],
                      handovers4: list[dict[str, Any]]) -> dict[str, Any]:
    detected = _detect_systems(states4, edges4, handovers4)
    systems, system_of = detected["systems"], detected["system_of"]
    systems_by_id = {s["id"]: s for s in systems}
    cross_links = _cross_system_links(system_of, edges4, handovers4)
    level1_gv = _systems_level1_view(systems, cross_links)
    level1_knobs = _mount_knobs(len(level1_gv["nodes"]))
    level1_list = "".join(
        f'<div class="row">{s["label"]} <span class="muted">'
        f'({len(s["members"])} nos, {s["actor"]})</span></div>'
        for s in systems
    )
    level1 = {"title": "8 — Systems map (global)",
              "subtitle": f"{len(systems)} systems — greedy-modularity over variant 4's "
                          "selective two-sided graph",
              "listHtml": level1_list, "gv": level1_gv, **level1_knobs}

    level2: dict[str, Any] = {}
    for s in systems:
        gv2, internal_edges = _system_level2(s, system_of, states4, edges4, handovers4, systems_by_id)
        id_to_label = {n["id"]: n["label"] for n in gv2["nodes"]}
        list2 = _edge_list_html(internal_edges, id_to_label)
        knobs2 = _mount_knobs(len(gv2["nodes"]))
        level2[s["id"]] = {
            "title": s["label"],
            "subtitle": f"{len(s['members'])} nodes, {s['actor']}-dominant — dashed stub = "
                        "edge leaving this system, click to follow",
            "listHtml": list2, "gv": gv2, **knobs2,
        }

    html = (
        _PAGE8.replace("__TITLE__", "8 — Collapsible systems")
        .replace(
            "__LEGEND__",
            "click a system node to drill in; dashed grey = handover-only or a cross-system "
            "stub — solid = at least one real action edge crosses that pair. Inside a system: "
            "same convention as variant 4 (blue=you, orange=opponent), dashed stub nodes are "
            "the neighbouring systems, click to follow.",
        )
        .replace("__LEVEL1_JSON__", json.dumps(level1, ensure_ascii=False))
        .replace("__LEVEL2_JSON__", json.dumps(level2, ensure_ascii=False))
    )
    (out / "8-sistemas-colapsavel.html").write_text(html, encoding="utf-8")

    return {
        "nodes": len(level1_gv["nodes"]), "edges": len(level1_gv["links"]),
        "edges_per_node": round(len(level1_gv["links"]) / len(level1_gv["nodes"]), 2)
        if level1_gv["nodes"] else 0.0,
        "pct_inferred_edges": None,
        "partner_elements": sum(1 for s in systems if s["actor"] == "partner"),
        "handover_links": sum(1 for link in cross_links if link.get("dashed")),
        "systems": len(systems),
        "knobs": level1_knobs,
    }


_VARIANT_DESCRIPTIONS = [
    ("1-baseline.html", "the app's CURRENT graph — technique=node — the comparison ruler"),
    ("2-migrado-proprio.html", "new model, YOU only — states=nodes, edges=action"),
    ("3-migrado-oponente-completo.html", "+ every partner element (own node, never merged with yours) + handovers"),
    ("4-migrado-oponente-seletivo.html", "partner enters only if used >=2x or sole handover bridge to a you node"),
    ("5-hubs.html", "node size by degree, edges bucketed by action type (see legend)"),
    ("6-ghost-inferidos.html", "variant 4 + inferred states/edges rendered as ghosts (dashed/grey)"),
    ("7-icones-categoria.html", "variant 4 + category icon/colour per node (App parity), actor shown as a border ring"),
    ("8-sistemas-colapsavel.html", "variant 4 grouped into systems (greedy-modularity) — click a system to drill in"),
]

_INDEX_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<title>Map prototypes — Phase 1</title>
<style>body{{background:#0b0b0f;color:#e9e9ee;font:14px/1.6 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 20px}}
a{{color:#4d86ff}}li{{margin-bottom:10px}}</style></head><body>
<h1>Map prototypes — actions/states migration, Phase 1</h1>
<ul>{items}</ul>
</body></html>"""


def _patch_graph_js(js_text: str) -> str:
    """Programmatic patch on the COPY only — ``site/graph.js`` itself is never touched (see
    module docstring: this file exists precisely because the shared renderer can't yet do
    per-link label/icon/ring). Deterministic string-replace, one anchor each; any anchor not
    found raises — a prototype that silently shipped without one of these features is worse
    than a loud failure."""
    patches = [
        (
            "        const mapLabel = !useCam || cam.k >= 1 || (n.size || 1) >= 2 || inFocus;",
            "        const mapLabel = opts.forceLabels || !useCam || cam.k >= 1 || (n.size || 1) >= 2 || inFocus;  // map-prototype patch: force-visible labels\n",
        ),
        (
            "          ctx.closePath(); ctx.fill();\n        }\n      }",
            "          ctx.closePath(); ctx.fill();\n        }\n"
            "        if (l.label && (opts.forceLabels || !useCam || cam.k >= 1)) {  // map-prototype patch: edge label\n"
            "          const mx = (l.s.x + l.t.x) / 2, my = (l.s.y + l.t.y) / 2;\n"
            "          const ldx = l.t.x - l.s.x, ldy = l.t.y - l.s.y;\n"
            "          const ld = Math.sqrt(ldx * ldx + ldy * ldy) || 1;\n"
            "          const lpx = -ldy / ld, lpy = ldx / ld;\n"
            "          ctx.save();\n"
            "          ctx.font = `${useCam ? 10 / cam.k : 10}px 'Spline Sans Mono', monospace`;\n"
            "          ctx.fillStyle = col;\n"
            "          ctx.globalAlpha = hov ? (active ? 0.9 : 0.08) : 0.75;\n"
            "          ctx.textAlign = 'center';\n"
            "          ctx.fillText(l.label, mx + lpx * 7, my + lpy * 7);\n"
            "          ctx.restore();\n"
            "        }\n      }",
        ),
        (
            "        ctx.shadowBlur = 0;",
            "        ctx.shadowBlur = 0;\n"
            "        if (n.ring) {  // map-prototype patch: actor border ring\n"
            "          ctx.save();\n"
            "          ctx.globalAlpha = dim ? 0.18 : 1;\n"
            "          ctx.lineWidth = 2.5;\n"
            "          ctx.strokeStyle = n.ring;\n"
            "          ctx.beginPath(); ctx.arc(n.x, n.y, r + 3, 0, Math.PI * 2); ctx.stroke();\n"
            "          ctx.restore();\n"
            "        }",
        ),
        (
            "        ctx.beginPath(); ctx.fillStyle = col;\n"
            "        ctx.arc(n.x, n.y, Math.max(1, r - 4.5), 0, Math.PI * 2); ctx.fill();",
            "        ctx.beginPath(); ctx.fillStyle = col;\n"
            "        ctx.arc(n.x, n.y, Math.max(1, r - 4.5), 0, Math.PI * 2); ctx.fill();\n"
            "        if (n.icon) {  // map-prototype patch: category/finish glyph\n"
            "          ctx.save();\n"
            "          ctx.globalAlpha = dim ? 0.18 : 1;\n"
            "          ctx.font = `${Math.max(9, r)}px sans-serif`;\n"
            "          ctx.textAlign = 'center';\n"
            "          ctx.textBaseline = 'middle';\n"
            "          ctx.fillStyle = '#fff';\n"
            "          ctx.fillText(n.icon, n.x, n.y);\n"
            "          ctx.restore();\n"
            "        }",
        ),
    ]
    for old, new in patches:
        if old not in js_text:
            raise ValueError(
                "render_map_prototypes: graph.js patch anchor not found — site/graph.js "
                f"drifted from what this module expects. Anchor: {old[:70]!r}..."
            )
        js_text = js_text.replace(old, new, 1)
    return js_text


def _orientation_counts(agg: Aggregate) -> dict[str, int]:
    counts = {"top": 0, "bottom": 0, "neutral": 0}
    for node_key, _actor in agg.states:
        counts[orientation_of(node_key)] += 1
    return counts


def render_all(bundle: dict[str, Any], out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    if _GRAPH_JS.exists():
        patched = _patch_graph_js(_GRAPH_JS.read_text(encoding="utf-8"))
        (out / "graph.js").write_text(patched, encoding="utf-8")

    agg = build_aggregate(bundle)
    metrics: dict[str, Any] = {"variants": {}}

    # 1 — baseline (not compiled, no edge-label list)
    gv1 = _baseline_graphview(bundle)
    knobs1 = _write_page(out, "1-baseline.html", "1 — Baseline (current app graph)",
                          "technique=node, App's own graph today", "no legend — plain baseline",
                          gv1, None)
    metrics["variants"]["1-baseline"] = {**_variant_metrics(gv1, None, 0), "knobs": knobs1}

    # 2 — you only
    gv2, edges2 = _own_graphview(agg)
    knobs2 = _write_page(out, "2-migrado-proprio.html", "2 — Migrated, you only",
                          "states=nodes, edges=action label at the midpoint (non-inferred only)",
                          "arrows = your own chain order", gv2, edges2)
    metrics["variants"]["2-migrado-proprio"] = {**_variant_metrics(gv2, edges2, 0), "knobs": knobs2}

    # 3 — full two-sided
    gv3, edges3, handovers3 = _complete_two_sided(agg)
    partner3 = sum(1 for k in agg.states if k[1] == "partner") + sum(1 for e in edges3 if e["actor"] == "partner")
    knobs3 = _write_page(out, "3-migrado-oponente-completo.html", "3 — + full opponent",
                          "every you + every partner element",
                          "blue=you, orange=opponent (separate nodes, never merged), "
                          "grey dashed=handover (actor switch)", gv3, edges3)
    metrics["variants"]["3-migrado-oponente-completo"] = {
        **_variant_metrics(gv3, edges3, partner3, len(handovers3)), "knobs": knobs3}

    # 4 — selective opponent (states4/edges4/handovers4 reused by 5/6/7/8: same partner-noise gate)
    states4, edges4, handovers4 = _selective_states_and_edges(agg)
    gv4 = _two_sided_graphview(states4, edges4, handovers4)
    partner4 = sum(1 for k in states4 if k[1] == "partner") + sum(1 for e in edges4 if e["actor"] == "partner")
    knobs4 = _write_page(out, "4-migrado-oponente-seletivo.html", "4 — Selective opponent",
                          "partner element kept only if used >=2x or sole handover bridge to a you element",
                          "blue=you, orange=opponent, grey dashed=handover — fewer partner nodes than (3)",
                          gv4, edges4)
    metrics["variants"]["4-migrado-oponente-seletivo"] = {
        **_variant_metrics(gv4, edges4, partner4, len(handovers4)), "knobs": knobs4}

    # 5 — hubs (same element set as 4, different sizing/colour)
    gv5 = _hubs_graphview(states4, edges4, handovers4)
    knobs5 = _write_page(out, "5-hubs.html", "5 — Hubs (degree size, action-type colour)",
                          "node size = degree percentile (handover links count toward degree)",
                          "APPROXIMATION: graph.js has no per-link colour field, so action type reuses "
                          "the 3-slot fighter palette — blue=submission/takedown, orange=pass/sweep, "
                          "grey dashed=handover (actor switch). True per-type colour needs an App-side "
                          "graph.js change (Phase 5).",
                          gv5, edges4)
    metrics["variants"]["5-hubs"] = {
        **_variant_metrics(gv5, edges4, partner4, len(handovers4)), "knobs": knobs5}

    # 6 — ghost inferred (same element set as 4, inferred elements ghosted)
    gv6 = _ghost_graphview(states4, edges4, handovers4)
    knobs6 = _write_page(out, "6-ghost-inferidos.html", "6 — Ghost inferred",
                          "variant 4 + inferred states/edges rendered as ghosts",
                          "blue=you, orange=opponent, grey dashed=handover; "
                          "ghost (translucent) = structurally inferred (D2 gap-fill), never an observed "
                          "event — ghosting marks INFERRED only, never a shared/merged node; finish "
                          "keeps its own colour even when ghosted", gv6, edges4)
    metrics["variants"]["6-ghost-inferidos"] = {
        **_variant_metrics(gv6, edges4, partner4, len(handovers4)), "knobs": knobs6}

    # 7 — category icons (App parity), actor as a border ring
    gv7 = _icons_graphview(states4, edges4, handovers4)
    knobs7 = _write_page(out, "7-icones-categoria.html", "7 — Category icons",
                          "colour+glyph = category (App's NODE_TYPE_ICONS/COLORS), ring = actor",
                          "guard \U0001f6e1 submission \U0001f525 control ▣ transition ⇄ "
                          "sweep ↻ escape ⏏ pass ⤴ takedown ⤵ finish \U0001f3c1 — "
                          "blue ring=you, orange ring=opponent (APPROXIMATION: graph.js has no real "
                          "per-node stroke field, patched into this copy only, see module docstring)",
                          gv7, edges4)
    metrics["variants"]["7-icones-categoria"] = {
        **_variant_metrics(gv7, edges4, partner4, len(handovers4)), "knobs": knobs7}

    # 8 — collapsible systems (community detection, two-level interactive)
    metrics["variants"]["8-sistemas-colapsavel"] = _render_variant8(out, states4, edges4, handovers4)

    metrics["corpus_inference_rate"] = {
        "states_total": agg.raw_states_total,
        "states_inferred": agg.raw_states_inferred,
        "states_inferred_pct": _pct(agg.raw_states_inferred, agg.raw_states_total),
        "edges_total": agg.raw_edges_total,
        "edges_inferred": agg.raw_edges_inferred,
        "edges_inferred_pct": _pct(agg.raw_edges_inferred, agg.raw_edges_total),
    }
    metrics["orientation_counts"] = _orientation_counts(agg)

    items = "".join(
        f'<li><a href="{fname}">{fname}</a> — {desc}</li>' for fname, desc in _VARIANT_DESCRIPTIONS
    )
    (out / "index.html").write_text(_INDEX_PAGE.format(items=items), encoding="utf-8")
    (out / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return metrics


def _pct(n: int, total: int) -> float:
    return round(100.0 * n / total, 1) if total else 0.0


def _variant_metrics(gv: dict[str, Any], edges: list[dict[str, Any]] | None, partner_elements: int,
                      handover_links: int = 0) -> dict[str, Any]:
    n_nodes, n_edges = len(gv["nodes"]), len(gv["links"])
    inferred_edges = sum(1 for e in edges if e.get("inferred")) if edges is not None else None
    return {
        "nodes": n_nodes,
        "edges": n_edges,
        "edges_per_node": round(n_edges / n_nodes, 2) if n_nodes else 0.0,
        "pct_inferred_edges": _pct(inferred_edges, len(edges)) if edges else (0.0 if edges is not None else None),
        "partner_elements": partner_elements,
        "handover_links": handover_links,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Render 8 actions/states map prototypes from a user bundle")
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = ap.parse_args()

    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    metrics = render_all(bundle, args.out)

    print(f"{'variant':32} {'nodes':>6} {'edges':>6} {'e/n':>6} {'%inf':>6} {'partner':>8} {'handover':>9}")
    for name, m in metrics["variants"].items():
        pct = m["pct_inferred_edges"]
        print(f"{name:32} {m['nodes']:>6} {m['edges']:>6} {m['edges_per_node']:>6} "
              f"{'—' if pct is None else pct:>6} {m['partner_elements']:>8} {m['handover_links']:>9}")
    cir = metrics["corpus_inference_rate"]
    print(f"\ncorpus inference rate: states {cir['states_inferred_pct']}% "
          f"({cir['states_inferred']}/{cir['states_total']}), "
          f"edges {cir['edges_inferred_pct']}% ({cir['edges_inferred']}/{cir['edges_total']})")
    oc = metrics["orientation_counts"]
    print(f"orientation: top {oc['top']}, bottom {oc['bottom']}, neutral {oc['neutral']}")
    print(f"\n-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
