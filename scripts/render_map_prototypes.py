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

Renders 9 self-contained HTMLs (a single patched ``site/graph.js`` copy shared by all of
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
on the App's own renderer, not something to sneak into this repo's copy of the shared file. The
patch set also nudges two constants that ARE just tuning, not new fields — node radius (smaller)
and label font size (bigger) — after a real screenshot review found several variants read as
clustered (see ``_mount_knobs``'s own docstring for the layout side of that same fix).

**Finish + orientation (owner call, 2026-08-27, ADR alongside D1/D2):** the compiler now closes
every chain on the generic state ``finish`` instead of ``scramble`` when it ends on a submission
(``analysis.chain_compiler``'s ``$terminal`` sentinel) — rendered with its own highlighted colour
in every migrated variant (``_apply_finish_style``), never blended into a category or a ghost
grey. The glyph (🏁) is variant 7 (icons) ONLY — every other variant keeps colour, no icon,
same as every other node there. Finish is a state like any other w.r.t. actor: you and the
opponent NEVER share a finish node (``_qid`` already qualifies it same as any node_key — ``finish``
vs ``opp:finish``), and since both read the same colour, the actor **ring** (`_FIG_HEX`, the
same field variant 7's patch already draws) is applied to a finish node in EVERY variant, not
just 7, so the two are still visually distinct; the opponent's rendered label also gets an
explicit ``" (oponente)"`` suffix (``_finish_label``) for the same reason (adendo 2026-08-27).
Every STATE also has a curated top/bottom/neutral orientation
(``analysis.taxonomy_kind.orientation_of``, keyed on the state's own ``node_key`` — already the
canonical normalized form the table is keyed on, so no extra lookup is needed) — shown as a
discreet ▲/▼ suffix next to a node's label in the side panel only, never on the canvas itself
(the canvas stays uncluttered; ``metrics.json`` carries the corpus-wide counts).

**11/12 — separate systems view (owner request, Frente 2 of the taxonomy lote, 2026-08-27):**
two more pages, ``_render_systems_page`` with two thin wrappers. 11 (A) = a locked read: the
GLOBAL level shows every node/edge (systems collapsed, but EVERY bridge, never variant 8/9's
top-N display cut) at combo ``("complete", 1, "all")``; clicking a system opens ITS OWN induced
subgraph + fronteira (never variant 8/9's ``_system_level2`` embedded-real-neighbour model —
here a crossing collapses to one STUB mini-node per destination PLACE, from
``_cross_member_links``, the shared model). 12 (B) = the same page with the controls live:
36 precomputed (inference policy × min_support × opponent mode) combos server-side, `bridgeRank`
cut / action-type hide / flow-bias live on the client. Direction: a per-action/handover/stub
link is always structurally arrowed; only the GLOBAL aggregate link (``_collapse_directed``)
asks ``analysis.network_metrics.edge_arrow`` whether the volume earns a direction.

**13 default (owner call, 2026-09-01):** pentágono + Global + rótulos "todos" — every control
still there, only the three defaults changed (structure/scope were already the approved default;
only ``labelMode`` moved from ``'main'`` to ``'all'``). Documented here because it is the ONLY
byte difference in ``13-caminhos.html`` from before this change (``diff -r`` gate, see
``tests/test_render_map_prototypes.py``).

**14 — "Caminhos por sistema" (owner request, 2026-09-01):** 13's own paths with systems
collapsible in the 11/12 model, ``_render_variant14``/``_paths_systems_view``. Same
``_detect_systems`` 11/12/13 already share; every system's members fold into one
``_system_node`` at the GLOBAL page (bridges/anchors/opponent states stay first-class, never
folded — owner call). A path is still a PATH through the fold: its ``path_id`` and metrics never
change, only an endpoint that sits inside a collapsed system draws at the system's node instead
of the member's own (``_paths_scope_paths``) — a path fully swallowed by one system (both
endpoints its members) draws nothing at the global level, that is the expansion's own job.
Clicking a system (its node, via ``n.sysId``, or its own SCOPES pill — reused verbatim from 13,
a system pill already IS "expand in place") swaps to that system's own page: its members become
real states again under 13's own ``flow_layout``, restricted to the paths that touch it, and
every outside touch reduces to 11/12's own compact stub (``_stub_node``) with a boundary ring on
the crossing member (``_system_boundary_view``'s own convention) — the stub/boundary STYLING is
reused verbatim; only the node/link assembly around them is new, because a path (segments) is a
different shape than a two-sided graphview edge. Same shell as 13 (structure pills, label modes,
selection model) — ``_PAGE14`` is a clone of ``_PAGE13`` (module convention: no
``.format()``-shared JS body across variants), not a parametrisation of it.
"""

# ruff: noqa: E501  (HTML/JS template strings are content)

from __future__ import annotations

import argparse
import json
import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import networkx as nx

from analysis.chain_compiler import ChainEdge, ChainState, CompiledChain, compile_two_sided
from analysis.constellations.detect import detect as constellation_detect
from analysis.flow_layout import (
    ANCHOR_STRUCTURES,
    DEFAULT_ANCHOR_STRUCTURE,
    PENTAGON_ANGLES,
    anchor_units,
    flow_layout,
)
from analysis.markov_weights import block_for_family, load_markov_weights
from analysis.names import _normalize_name, canonicalize
from analysis.network_metrics import edge_arrow
from analysis.path_bundling import RenderPath, Segment, bundle_paths
from analysis.path_metrics import PathMetrics, path_metrics
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
_FINISH_ICON = "F"  # A.4: letter, not emoji — see _icons_graphview's own docstring for why
_FINISH_COLOR = "#facc15"

# App's src/types/session.ts NODE_TYPE_ICONS (Bootstrap Icons in the App) / NODE_TYPE_COLORS
# (copied verbatim, same hex values, cross-checked against the App file 2026-08-27). Icons here
# are a single bold pt-BR-mnemonic LETTER (variant 7 canvas fillText only), not a Bootstrap
# icon or an emoji — see _icons_graphview's docstring for the A.4 measurement + decision. No
# collisions across the 8 categories + finish ('F') + start-anchor ('A', _START_ICON).
_TYPE_ICONS = {
    "guard": "G", "submission": "S", "control": "C",
    "transition": "T", "sweep": "V", "escape": "E",
    "pass": "P", "takedown": "D",
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

# D (owner addendum 2026-08-27; role renamed 2026-08-31) — organisational ANCHOR nodes. The
# other builder's `inference_table.json` carries three generic states — ``start neutral``/
# ``start top``/``start bottom`` (node_key, unchanged) — each tagged ``"role": "anchor"``
# (renamed from ``"start"`` — Fase 1b: the same three nodes now close a chain that ends
# unanchored too, not just open one; the node_key itself, not the role value, carries the
# neutral/top/bottom distinction — same table's existing ``orientation`` field /
# ``analysis.taxonomy_kind.orientation_of`` already encodes that, reused here rather than
# re-deriving it from the key string). Finish carries ``"role": "finish"``, already handled
# separately via ``_FINISH_KEY``/actor. Lookup stays defensive (``_role_of`` returns ``None`` on
# a missing entry/field) so nothing here depends on the table shape beyond what's actually
# landed.
_START_COLOR = "#34d399"  # teal — distinct from finish's yellow (_FINISH_COLOR)
_START_ICON = "A"  # variant 7 only — "Âncora"; letters not emoji, see _TYPE_ICONS docstring

# Owner spec, literal (2026-08-27, corrects the earlier uniform-circle/pentagon pass which lost
# this semantic): "the neutral node will be anchored to the center, the top to the top, the
# bottom to the bottom, and Left is the opponent finish. Right is your finish." The 5
# organisational anchors (3 start orientations + 2 actor-qualified finishes) are "bolted in
# place" — pinned, never simulated — at these 5 fixed points: center + the 4 cardinal directions
# of a cross, which is uniform-by-construction (nothing clumps) without needing an arbitrary
# pentagon. ``_anchor_slot`` maps a node to its direction key (or ``None`` for an ordinary node);
# ``_ANCHOR_UNIT`` is that key's unit vector, scaled by ``_apply_anchor``'s radius (neutral's
# vector is the zero vector, so it always lands at the origin regardless of radius). Shared by
# every variant that renders a node (`_apply_anchor` is the single choke point). Canvas
# convention: y grows DOWNWARD, so "top" is negative y.
# Owner spec 2026-08-27 (third arrangement, replacing the cross): the five bolted anchors are
# the vertices of a REGULAR PENTAGON, walked from the far-left vertex at 72-degree steps —
# neutral start at the extreme left, top start upper-left, the two finishes on the right (yours
# upper-right, the opponent's lower-right), bottom start lower-left. Reading left to right is
# then reading start -> finish, which is the same direction the flow bias pushes; reading top to
# bottom on the left is the orientation axis it always was. Canvas convention: y grows DOWNWARD,
# so a positive maths angle becomes a negative y.
# The table itself now lives in ``analysis/flow_layout.py`` — the App mirrors that module
# (``src/services/map/flowLayout.ts``) against a golden fixture, and a layout constant with two
# homes is a contract with two answers. Aliased to the private names this file has always used
# so nothing else in it moves; variants 1-14 render byte-identically across the move.
_PENTAGON_ANGLES = PENTAGON_ANGLES
_ANCHOR_STRUCTURES = ANCHOR_STRUCTURES
_DEFAULT_ANCHOR_STRUCTURE = DEFAULT_ANCHOR_STRUCTURE
_anchor_units = anchor_units
#: Variants 1-12 were BUILT as a pentagon frame and carry a byte-identity guarantee
#: (`tests/test_render_map_prototypes.py`'s `diff -r` gate). They are pinned to that row by name,
#: not to `DEFAULT_ANCHOR_STRUCTURE` — which moved to the triangle on 2026-09-01 and is now a
#: statement about the PRODUCTS (the App's Map and the site's three path displays), not about a
#: comparison page whose whole job is to hold the old arrangements still.
_PENTAGON_STRUCTURE = "pentagono"
_ANCHOR_UNIT = anchor_units(_PENTAGON_STRUCTURE)


def _anchor_slot(node_key: str, actor: str,
                  structure: str = _DEFAULT_ANCHOR_STRUCTURE) -> str | None:
    """Stable vertex key for a bolted anchor in this structure, or ``None`` for an ordinary
    node."""
    if node_key == _FINISH_KEY:
        if _ANCHOR_STRUCTURES[structure]["unified_finish"]:
            return "finish"
        return "finish_opp" if actor == "partner" else "finish_you"
    if _is_start(node_key):
        return orientation_of(node_key)  # "top" / "bottom" / "neutral" — same key set
    return None


def _role_of(node_key: str) -> str | None:
    """Defensive lookup of a generic state's ``role`` field (``data/taxonomy/inference_table.json``
    ``generic_states``) — returns ``None`` if the entry or the field doesn't exist yet."""
    entry = load_inference_table().get("generic_states", {}).get(node_key)
    return entry.get("role") if isinstance(entry, dict) else None


def _is_start(node_key: str) -> bool:
    return _role_of(node_key) == "anchor"


def _is_shared(node_key: str) -> bool:
    """A generic state flagged ``shared`` in the table: a situation NEITHER athlete owns."""
    entry = load_inference_table().get("generic_states", {}).get(node_key)
    return bool(entry.get("shared")) if isinstance(entry, dict) else False


# Anchors are the map's frame, so their size is FIXED (owner 2026-08-27) — deriving it from
# usage count made the landmark shrink or grow between renders and between gate combos.
_ANCHOR_SIZE = 3
# Fase 1b (2026-08-31): labels lost the "Início" prefix — the anchors serve both ends of a
# chain now, not just openings. Mirrors `data/taxonomy/inference_table.json`'s own `label`.
_START_LABELS = {"start top": "Por Cima", "start bottom": "Por Baixo",
                  "start neutral": "Neutro"}


def _perspective_key(node_key: str, actor: str | None) -> str:
    """Start anchors are read from the USER's side, always (owner 2026-08-27). The compiler is
    actor-agnostic — it names the opening anchor from the action's own orientation — so an
    opponent chain that opens with a PASS names ``start top``, meaning *they* were on top. From
    the user's perspective that is the bottom, and the map only has one set of anchors. Mirroring
    here (never in the compiler, where perspective is deliberately not decided) keeps
    ``start top``/``start bottom`` meaning what the map's vertical axis says they mean."""
    if actor != "partner" or not _is_start(node_key):
        return node_key
    return {"start top": "start bottom", "start bottom": "start top"}.get(node_key, node_key)


def _actor_for(node_key: str, actor: str) -> str:
    """D: a role='start' node is always the USER's, even reached from the opponent's own
    chain — never ``opp:``-qualified. Single choke point: called from ``_qid`` (id string) and
    ``Aggregate.add_state`` (aggregation key), so a start node reached from both sides merges
    into ONE node/count instead of two ghost duplicates.

    ``shared`` generics merge the same way, for the opposite reason: the rule that keeps your
    mount separate from theirs exists because a position is OWNED — somebody plays it. A
    scramble is the interval where nobody owns anything, so "my scramble" and "their scramble"
    would be one physical event counted twice. Ownership is the test, not actor presence."""
    return "you" if (_is_start(node_key) or _is_shared(node_key)) else actor


def _clamp3(n: float) -> int:
    return 1 if n <= 1 else (2 if n == 2 else 3)


def _qid(actor: str, node_key: str) -> str:
    """Node id qualified by actor — you and partner NEVER share a node, even on the same
    node_key (they are fundamentally different states: your closed guard is not their closed
    guard). ``opp:`` prefix convention per D4/userDecisionFlow. Start-role nodes are the one
    exception (``_actor_for`` — D, always the user's)."""
    actor = _actor_for(node_key, actor)
    return node_key if actor == "you" else f"opp:{node_key}"


def _anchor_radius(n_nodes: int) -> float:
    """World-coordinate distance from origin to an anchor axis endpoint (D addendum) — scales
    with graph size so the anchors sit just beyond the free nodes' own typical spread (same
    formula family as ``_mount_knobs``), never so far the camera's ``fitTarget()`` zooms the
    rest of the graph down to unreadable. Formula only, not eyeballed in a browser (ponytail:
    same caveat as ``_mount_knobs`` — revisit with a real screenshot pass if an anchor still
    reads too close/far once someone opens it)."""
    # Sized so the free nodes settle INSIDE the pentagon (owner 2026-08-27: "spaced in a way
    # that the map will be mostly contained inside of the pentagon") — the anchors are the frame,
    # not five more nodes in the crowd. Scales with the same sqrt(n) family as ``_mount_knobs``
    # so a bigger graph gets a bigger frame instead of bursting out of a fixed one.
    return max(430.0, 125.0 * math.sqrt(max(n_nodes, 1)))


def _apply_start_style(node: dict[str, Any], node_key: str, *, icon: bool = False) -> None:
    """D: organisational start nodes — own colour (teal, distinct from finish's yellow), no
    actor ring (they are never actor-specific once ``_actor_for`` has qualified them as the
    user's — the ring would otherwise misleadingly read as "your" possession). Icon (variant 7
    only) is a single letter, same convention as ``_TYPE_ICONS`` (see its docstring for why
    letters, not emoji)."""
    if _is_start(node_key):
        node["color"] = _START_COLOR
        node["shape"] = "diamond"  # owner 2026-08-27 — shape, not just hue, marks where a chain opens or closes
        node["size"] = _ANCHOR_SIZE  # a landmark's size can't wobble with how often it was hit
        node.pop("ring", None)
        if icon:
            node["icon"] = _START_ICON


def _apply_anchor(node: dict[str, Any], node_key: str, actor: str, radius: float) -> None:
    """Owner spec (2026-08-27): the 5 organisational anchors are the vertices of a PENTAGON
    (``_PENTAGON_ANGLES``) sized to contain the rest of the map — never simulated, so the graph
    organises itself around fixed, legible landmarks instead of reshuffling on every load/expand.
    World coordinates (not viewport) — stays coherent under pan/zoom and with edge geometry;
    ``graph.js``'s copy-only ``n.pin`` patch (`_patch_graph_js`) makes ``step()`` skip physics
    entirely for a pinned node, so once placed here it never drifts. Called last (after style
    helpers) so ``x``/``y``/``pin`` always win over a random seed."""
    slot = _anchor_slot(node_key, actor, _PENTAGON_STRUCTURE)
    if slot is not None:
        ux, uy = _ANCHOR_UNIT[slot]
        node["x"], node["y"] = radius * ux, radius * uy
        node["pin"] = True


def _orient_badge(node_key: str) -> str:
    """▲ top / ▼ bottom / '' neutral — side-panel-only suffix, see module docstring."""
    o = orientation_of(node_key)
    return {"top": " ▲", "bottom": " ▼"}.get(o, "")


def _finish_label(node_key: str, actor: str, label: str) -> str:
    """Finish reuses the exact same colour (and, on variant 7, glyph) for both actors — D2's
    terminal state is generic, not actor-specific — so the label is what tells the opponent's
    finish apart from your own in the side panel/canvas. Owner call, adendo 2026-08-27."""
    return f"{label} (oponente)" if node_key == _FINISH_KEY and actor == "partner" else label


def _apply_finish_style(node: dict[str, Any], node_key: str, actor: str, *, icon: bool = False) -> None:
    """Finish is the terminal submission target — colour distinct from every category (and from
    the ghost grey a variant 6 finish would otherwise get, since a chain-closing state is always
    structurally inferred) in every MIGRATED variant; the glyph (``icon=True``) is variant 7
    only. Colour is shared between you and the opponent's own finish, so the actor RING
    (``_FIG_HEX``) is set here too, in every variant — the only way the two stay visually
    distinct once colour can't (adendo 2026-08-27). Called last so it overrides whatever colour
    the caller already set."""
    if node_key == _FINISH_KEY:
        node["color"] = _FINISH_COLOR
        node["ring"] = _FIG_HEX[_ACTOR_SIDE[actor]]
        if icon:
            node["icon"] = _FINISH_ICON


# Belt colour — the same blue the user's own nodes and edges carry (``_FIG_HEX["a"]``). It was
# neutral grey until the owner asked for the belt colour (2026-08-27): a bridge is still HIS
# node, so it should read as his game, not as a third party. Structure is carried by the panel
# listing and by label priority instead of by hue.
_BRIDGE_COLOR = _FIG_HEX["a"]


def _apply_bridge_style(node: dict[str, Any], *, is_bridge: bool) -> None:
    """C (owner whiteboard 2026-08-27, corrects the earlier system-to-system-edge design): a
    BRIDGE is a node whose neighbours span >=2 different systems — member of NONE, first-class,
    rendered individually at every level (never collapsed into a system, never inside a
    region). Neutral/grey, and flagged (``bridge``) so the label-collision pass (`_patch_graph_js`)
    gives it top priority — it's the node that explains cross-system structure, so its label
    must win over an ordinary same-priority node/edge."""
    if is_bridge:
        node["color"] = _BRIDGE_COLOR
        node["bridge"] = True


def _mount_knobs(n_nodes: int) -> dict[str, float]:
    """Layout tuning proportional to node count (owner: "não faz sentido ter um cluster de
    informação"). Retuned AGGRESSIVE 2026-08-27 after a real screenshot review ("quite a few of
    the views are true clustered... I can't read") — more repulsion, longer spring rest length,
    less centre pull than the first pass; erring toward too much space over too little per the
    owner's explicit call (A.3: linkDist ~240 for <=25 nodes, charge ~12000+, gravity ~0.0004).
    Formula only, not eyeballed in a real browser (no headless-canvas check run here — ponytail:
    revisit with a playwright screenshot pass if a variant still reads as clustered once someone
    opens it). Mirrored client-side by ``_PAGE9``'s own ``knobsFor()`` — same formula, JS-side
    because variant 9's assembled node count only exists once the user has expanded a system."""
    n = max(n_nodes, 1)
    charge = round(12000 + 300 * n)
    link_dist = 240 if n <= 25 else max(190, round(240 - (n - 25) * 0.9))
    gravity = round(0.0004 * (1 + n / 100), 5)
    return {"charge": charge, "linkDist": link_dist, "gravity": gravity}


def _index_parallel_links(links: list[dict[str, Any]]) -> None:
    """B (owner-reported bug): multiple links between the SAME two nodes (>=2 different actions
    bridging the same pair of states) used to draw as N identical overlapping lines with N
    labels stacked at one shared midpoint. Indexes by UNORDERED pair (both directions share one
    fan, so a reverse-direction edge between the same two nodes doesn't overlap either) and
    assigns a stable ``par``/``parCount`` — the client (`_patch_graph_js`'s quadratic-curve
    patch) draws each as its own arc, offset by ``par``'s slot among ``parCount`` siblings, with
    the label on ITS OWN arc's midpoint. Mutates in place; a pair with only one link is
    untouched (no ``par``/``parCount`` fields — degenerates to the original straight line)."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for link in links:
        key = tuple(sorted((link["from"], link["to"])))
        groups.setdefault(key, []).append(link)
    for group in groups.values():
        n = len(group)
        if n <= 1:
            continue
        for i, link in enumerate(group):
            link["par"], link["parCount"] = i, n


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


def _side_of(e: Mapping[str, Any]) -> str | None:
    actor = e.get("actor")
    return _ACTOR_SIDE.get(actor) if isinstance(actor, str) else None


def _actor_of(e: Mapping[str, Any]) -> str | None:
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
        # Phase 1 (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md): keyed on the WHOLE canonical
        # action sequence, not just `actions[0]` (the P3 gap `tests/test_actions_parity.py`
        # documented) — an edge can now carry more than one action, and two edges whose
        # sequences differ must land in different buckets even when their FIRST action matches.
        self.edges: dict[tuple[str, str, tuple[str, ...], str], dict[str, Any]] = {}
        # Phase 4: one representative ``ChainEdge`` per aggregation key — ``analysis.path_metrics``
        # is keyed on a ChainEdge, and every occurrence under one key carries the same action
        # sequence/terminality by construction (that IS the key). Kept OUT of the row dicts so
        # nothing that walks a row's fields can trip over a dataclass.
        self.edge_sample: dict[tuple[str, str, tuple[str, ...], str], ChainEdge] = {}
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
        node_key = _perspective_key(s.node_key, s.actor)  # opponent's top IS the user's bottom
        actor = _actor_for(node_key, s.actor)  # D: start-role nodes always merge into 'you'
        key = (node_key, actor)
        row = self.states.get(key)
        if row is None:
            label = self.display_labels.get(node_key, _START_LABELS.get(node_key, s.label))
            self.states[key] = {"node_key": node_key, "label": label, "type": s.type,
                                 "actor": actor, "count": 1, "inferred": s.inferred,
                                 "nascent": s.nascent}
        else:
            row["count"] += 1
            row["inferred"] = row["inferred"] and s.inferred
            # a state is only NASCENT if nothing ever preceded it — one chain reaching it
            # through an action is enough to settle that something did
            row["nascent"] = row.get("nascent", False) and s.nascent

    def add_edge(self, e: ChainEdge) -> None:
        if e.actor not in ("you", "partner"):
            return
        # Fase 5: an occurrence counts as INFERRED only when nothing on it was observed. This
        # read `e.inferred`, i.e. `actions[0].inferred` — the position dependence the contract
        # doc names in §5 and told this phase to fix. It was live on the owner's own bundle:
        # `half guard --[sweep(inferred), knee cut pass(observed)]--> side control` read
        # "inferred" while its mirror `de la riva --[berimbolo(observed), sweep(inferred)]-->
        # back control` read "observed", for no reason but the order. The gate policies
        # (`no_inferred_edges`, `inferred_min2`) filter on this field, so the old reading hid a
        # real observation behind an inferred one and dropped the edge.
        wholly_inferred = all(a.inferred for a in e.actions)
        self.raw_edges_total += 1
        self.raw_edges_inferred += 1 if wholly_inferred else 0
        source_key = _perspective_key(e.source_key, e.actor)
        target_key = _perspective_key(e.target_key, e.actor)
        action_seq = tuple(a.key for a in e.actions)
        key = (source_key, target_key, action_seq, e.actor)
        row = self.edges.get(key)
        if row is None:
            action_label = self.display_labels.get(e.action_key, e.action_label)
            self.edges[key] = {"source": source_key, "target": target_key,
                                "action_key": e.action_key, "action_label": action_label,
                                "action_type": e.action_type, "actor": e.actor,
                                "count": 1, "inferred": wholly_inferred,
                                # Phase 4 — the WHOLE ordered path, not just actions[0]. Additive:
                                # every variant 1-12 reads the scalar fields above and is
                                # untouched by these three.
                                "actions": action_seq,
                                "action_labels": tuple(
                                    self.display_labels.get(a.key, a.label) for a in e.actions),
                                "action_inferred": tuple(a.inferred for a in e.actions)}
            self.edge_sample[key] = e
        else:
            row["count"] += 1
            row["inferred"] = row["inferred"] and wholly_inferred

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
        if prev_actor is not None and prev_idx is not None and actor != prev_actor:
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


# ── 1b. gating (owner adendo 2026-08-27) ────────────────────────────────────────
#
# "Se um grafo está conectado demais, pode ser porque ... ações e estados não estão devidamente
# gateados" — before tuning a bridge threshold, measure whether density is an ARTEFACT of
# generic/inferred connective tissue (scramble, top transition, ...) gluing every community
# together. Two independent axes: minimum edge support (same knob as the App's
# ``DEFAULT_MIN_EDGE_SUPPORT`` in ``userDecisionFlow.ts``, born of an identical hairball there)
# and how much of the INFERRED (never-observed, D2 gap-filled) population survives.

_GATE_MIN_SUPPORTS = (1, 2, 3)
_GATE_POLICIES = ("all", "no_inferred_edges", "no_inferred", "inferred_min2")
_GATE_POLICY_LABELS = {
    "all": "tudo (sem gate de inferência)",
    "no_inferred_edges": "sem edges inferidas",
    "no_inferred": "sem edges nem estados inferidos (splice)",
    "inferred_min2": "inferidos só com suporte ≥2",
}

# Chosen defaults for variants 8/9 — measured with `sweep_gates` against the owner's real bundle
# (10 you-eligible nodes at the time of measurement; see the builder's report for the full 3x4
# table). ``min_support=2`` (the App's own precedent) turned out too aggressive HERE — most edges
# in this small/sparse bundle only have count 1-2, so it collapsed almost everything into
# near-isolated bridges (measured: 1 system, 7 of 10 nodes as bridges). ``min_support=1`` is a
# no-op on the support axis (every edge already has count>=1) — the density WAS the generic
# single-occurrence inferred edges, not the low-but-real observed ones, so ``inferred_min2``
# (drop inferred edges seen only once, keep ones that repeated) alone is what produced the
# target shape: 2 systems (4 and 3 members, both >=3) + 3 bridges, vs. 1 system + 7 bridges
# under the old ungated/undominance-tuned reading. The dominance threshold (above) is tuned
# AFTER this gate, never before — a graph this cleaned up no longer saturates on "touches >=2
# communities" the way the raw one did.
_GATE_MIN_SUPPORT_DEFAULT = 1
_GATE_POLICY_DEFAULT = "inferred_min2"


def _splice_inferred_states(
    states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Gate axis 2c: an INFERRED state with exactly one incoming and one outgoing action edge is
    a structural gap-fill (D2), never an observed decision point — remove it and reconnect its
    predecessor directly to its successor. The spliced edge's ``count`` is the MIN of the two
    hops it replaces (can't exceed either hop's own support), ``inferred`` stays True (the splice
    is itself never observed, even when both original hops happened to be). Repeats until no more
    splices are possible, so a RUN of several inferred states collapses to zero. A state with any
    other in/out shape (0, or >1 either side) can't be safely spliced without inventing a fan-out
    — it stays, and downstream ``inferred``-state filtering still applies to it."""
    states = dict(states)
    edges = list(edges)
    changed = True
    while changed:
        changed = False
        incoming: dict[tuple[str, str], list[dict[str, Any]]] = {}
        outgoing: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for e in edges:
            outgoing.setdefault((e["source"], e["actor"]), []).append(e)
            incoming.setdefault((e["target"], e["actor"]), []).append(e)
        for key, v in states.items():
            node_key, actor = key
            if not v["inferred"]:
                continue
            ins, outs = incoming.get(key, []), outgoing.get(key, [])
            if len(ins) != 1 or len(outs) != 1 or ins[0] is outs[0]:
                continue
            in_e, out_e = ins[0], outs[0]
            spliced = {
                "source": in_e["source"], "target": out_e["target"],
                "action_key": f"{in_e['action_key']}>{out_e['action_key']}",
                "action_label": f"{in_e['action_label']} → {out_e['action_label']}",
                "action_type": out_e["action_type"], "actor": actor,
                "count": min(in_e["count"], out_e["count"]), "inferred": True,
            }
            edges = [e for e in edges if e is not in_e and e is not out_e]
            edges.append(spliced)
            del states[key]
            changed = True
            break  # in/out maps are now stale — rebuild before the next candidate
    return states, edges


def _apply_gate(
    states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]],
    handovers: list[dict[str, Any]], *, min_support: int, inference_policy: str,
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Both gating axes, composed. ``inference_policy`` — see ``_GATE_POLICY_LABELS``."""
    edges = list(edges)
    if inference_policy == "no_inferred_edges":
        edges = [e for e in edges if not e["inferred"]]
    elif inference_policy == "no_inferred":
        states, edges = _splice_inferred_states(states, edges)
        edges = [e for e in edges if not e["inferred"]]
        states = {k: v for k, v in states.items() if not v["inferred"]}
    elif inference_policy == "inferred_min2":
        edges = [e for e in edges if not e["inferred"] or e["count"] >= 2]
    edges = [e for e in edges if e["count"] >= min_support]
    handovers = [h for h in handovers if h["count"] >= min_support]
    kept_qids = {_qid(v["actor"], node_key) for (node_key, _actor), v in states.items()}
    edges = [e for e in edges
             if _qid(e["actor"], e["source"]) in kept_qids and _qid(e["actor"], e["target"]) in kept_qids]
    handovers = [h for h in handovers if h["from"] in kept_qids and h["to"] in kept_qids]
    return states, edges, handovers


def sweep_gates(
    states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]],
    handovers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Diagnostic sweep over both gating axes × the SAME dominance-based system detection —
    lets the owner see whether the ungated graph's "everything touches >=2 communities" reading
    was a real property of the data or an artefact of generic/inferred connective tissue. One row
    per (min_support, policy) combination. Feeds both the CLI report and
    ``10-gating-comparado.html``."""
    rows = []
    for min_support in _GATE_MIN_SUPPORTS:
        for policy in _GATE_POLICIES:
            g_states, g_edges, g_handovers = _apply_gate(
                states, edges, handovers, min_support=min_support, inference_policy=policy)
            detected = _detect_systems(g_states, g_edges, g_handovers)
            n_nodes = sum(1 for k in g_states if _system_eligible(k[0], k[1]))
            n_edges = sum(1 for e in g_edges if e["actor"] == "you")
            sizes = sorted((len(s["members"]) for s in detected["systems"]), reverse=True)
            rows.append({
                "min_support": min_support, "policy": policy,
                "nodes": n_nodes, "edges": n_edges,
                "edges_per_node": round(n_edges / n_nodes, 2) if n_nodes else 0.0,
                "systems": len(detected["systems"]), "system_sizes": sizes,
                "bridges": len(detected["bridge_qids"]),
            })
    return rows


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
    radius = _anchor_radius(len(states))
    nodes = []
    for (node_key, _actor), v in states.items():
        node = {"id": v["node_key"], "label": _finish_label(node_key, "you", v["label"]),
                "cat": _cat_of(v["type"]), "size": _clamp3(v["count"])}
        _apply_finish_style(node, node_key, "you")
        _apply_start_style(node, node_key)
        _apply_anchor(node, node_key, "you", radius)
        nodes.append(node)
    links = []
    for v in edges:
        link = {"from": v["source"], "to": v["target"], "weight": _clamp3(v["count"]), "arrow": True}
        link["label"] = v["action_label"]
        if v["inferred"]:
            link["inf"] = True  # named generic: still labelled, but yields on label collision
        links.append(link)
    _index_parallel_links(links)
    return {"nodes": nodes, "links": links}, edges


def _style_single_node(node_key: str, actor: str, v: dict[str, Any], *, radius: float,
                        is_bridge: bool = False) -> dict[str, Any]:
    """One rendered node's full styling — shared by ``_two_sided_graphview`` (a whole two-sided
    element set) and the "individual" nodes shown alongside a collapsed system (opponent/
    finish/start-anchor/bridge — anything ``_system_eligible`` excludes from a community,
    variants 8/9's level1/level2). Finish/start/anchor/bridge are mutually exclusive
    categories (bridges only ever come from the you-eligible pool, which already excludes
    finish/start), so applying all four style helpers in sequence is safe — at most one fires."""
    node: dict[str, Any] = {"id": _qid(actor, node_key), "label": _finish_label(node_key, actor, v["label"]),
                             "cat": _cat_of(v["type"]), "size": _clamp3(v["count"]),
                             "fighter": _ACTOR_SIDE[actor]}
    _apply_finish_style(node, node_key, actor)
    _apply_start_style(node, node_key)
    _apply_bridge_style(node, is_bridge=is_bridge)
    _apply_anchor(node, node_key, actor, radius)
    return node


def _two_sided_graphview(states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]], handovers: list[dict[str, Any]],
                          bridge_qids: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Node per (node_key, actor) — you and partner are fundamentally different states, never
    merged, even on the same node_key. Links: within-actor action edges (blue/orange, labelled
    when not inferred) + neutral dashed HANDOVER links (grey, ``fighter:'x'``, the site's own
    contested-link convention) that bridge across the actor switch — the only interconnection
    between the two subgraphs now that edges never cross actors. ``bridge_qids`` (C, owner
    whiteboard) is only ever non-empty when the caller is rendering a sub-selection that still
    contains cut-vertex nodes (rare — see ``_system_level2``'s own subgraph, which never does,
    since bridges are excluded from ``member_qids`` upstream); the default keeps every other
    caller unchanged."""
    radius = _anchor_radius(len(states))
    nodes = []
    for (node_key, actor), v in states.items():
        nodes.append(_style_single_node(node_key, actor, v, radius=radius,
                                         is_bridge=_qid(actor, node_key) in bridge_qids))
    links = []
    for v in edges:
        link = {"from": _qid(v["actor"], v["source"]), "to": _qid(v["actor"], v["target"]),
                "weight": _clamp3(v["count"]), "arrow": True, "fighter": _ACTOR_SIDE[v["actor"]]}
        link["label"] = v["action_label"]
        if v["inferred"]:
            link["inf"] = True  # named generic: still labelled, but yields on label collision
        links.append(link)
    links += [{"from": h["from"], "to": h["to"], "weight": _clamp3(h["count"]),
               "arrow": True, "dashed": True, "fighter": "x"} for h in handovers]
    _index_parallel_links(links)
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
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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


def _hubs_graphview(states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]], handovers: list[dict[str, Any]]) -> dict[str, Any]:
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

    radius = _anchor_radius(len(states))
    nodes = []
    for (node_key, actor), v in states.items():
        qid = _qid(actor, node_key)
        d = degree.get(qid, 0)
        node = {"id": qid, "label": _finish_label(node_key, actor, v["label"]),
                "cat": _cat_of(v["type"]), "size": 1 + round(2 * d / max_deg)}
        _apply_finish_style(node, node_key, actor)
        _apply_start_style(node, node_key)
        _apply_anchor(node, node_key, actor, radius)
        nodes.append(node)
    links = []
    for e in edges:
        link = {"from": _qid(e["actor"], e["source"]), "to": _qid(e["actor"], e["target"]),
                "weight": _clamp3(e["count"]), "arrow": True,
                "fighter": _TYPE_BUCKET.get(e["action_type"], "x")}
        link["label"] = e["action_label"]
        if e["inferred"]:
            link["inf"] = True  # named generic: still labelled, but yields on label collision
        links.append(link)
    links += [{"from": h["from"], "to": h["to"], "weight": _clamp3(h["count"]),
               "arrow": True, "dashed": True, "fighter": "x"} for h in handovers]
    _index_parallel_links(links)
    return {"nodes": nodes, "links": links}


def _ghost_graphview(states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]], handovers: list[dict[str, Any]]) -> dict[str, Any]:
    """Variant 6 — variant 4's selective set, with inferred elements rendered as ghosts:
    dashed + minimum weight/size, node colour a translucent grey (``color`` IS a real node
    field graph.js reads — see module docstring). Ghosting marks INFERRED only, never
    'shared' — you/partner nodes are never merged any more. Finish overrides the ghost grey
    (it is always structurally inferred, but must still read as ITS OWN colour, not noise)."""
    radius = _anchor_radius(len(states))
    nodes = []
    for (node_key, actor), v in states.items():
        node = {"id": _qid(actor, node_key), "label": _finish_label(node_key, actor, v["label"]),
                "cat": _cat_of(v["type"]),
                "size": 1 if v["inferred"] else _clamp3(v["count"]), "fighter": _ACTOR_SIDE[actor]}
        if v["inferred"]:
            node["color"] = "rgba(150,150,160,0.35)"
        _apply_finish_style(node, node_key, actor)
        _apply_start_style(node, node_key)
        _apply_anchor(node, node_key, actor, radius)
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
    _index_parallel_links(links)
    return {"nodes": nodes, "links": links}


def _icons_graphview(states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]], handovers: list[dict[str, Any]]) -> dict[str, Any]:
    """Variant 7 — category icon + colour per node, approximating the App's own
    NODE_TYPE_ICONS/NODE_TYPE_COLORS (src/types/session.ts). Colour is taken by category here,
    so actor is shown as a border RING instead (``n.ring``, ``_FIG_HEX`` — a stroke-custom
    patch on the copy, see module docstring). Same selective element set as variant 4. Icons are
    a single bold LETTER, not an emoji — measured (a headless Chrome render of this exact
    ``fillText`` call DID paint a coloured glyph in this sandbox's Noto Color Emoji setup) but
    kept as letters anyway: colour-emoji font availability is a client-machine dependency this
    module can't guarantee for every viewer, and at the common size-1 node radius (the
    post-A.3-shrink majority) a detailed pictogram reads worse than a bold single character
    regardless of font support. A.4 decision, 2026-08-27."""
    radius = _anchor_radius(len(states))
    nodes = []
    for (node_key, actor), v in states.items():
        typ = _cat_of(v["type"])
        node = {"id": _qid(actor, node_key), "label": _finish_label(node_key, actor, v["label"]),
                "cat": typ, "size": _clamp3(v["count"]), "color": _TYPE_COLORS.get(typ, "#94a3b8"),
                "icon": _TYPE_ICONS.get(typ, ""), "ring": _FIG_HEX[_ACTOR_SIDE[actor]]}
        _apply_finish_style(node, node_key, actor, icon=True)
        _apply_start_style(node, node_key, icon=True)
        _apply_anchor(node, node_key, actor, radius)
        nodes.append(node)
    links = []
    for e in edges:
        link = {"from": _qid(e["actor"], e["source"]), "to": _qid(e["actor"], e["target"]),
                "weight": _clamp3(e["count"]), "arrow": True, "fighter": _ACTOR_SIDE[e["actor"]]}
        link["label"] = e["action_label"]
        if e["inferred"]:
            link["inf"] = True  # named generic: still labelled, but yields on label collision
        links.append(link)
    links += [{"from": h["from"], "to": h["to"], "weight": _clamp3(h["count"]),
               "arrow": True, "dashed": True, "fighter": "x"} for h in handovers]
    _index_parallel_links(links)
    return {"nodes": nodes, "links": links}


# ── 2b. variants 8/9 — collapsible systems (community detection) ───────────────

def _system_eligible(node_key: str, actor: str) -> bool:
    """C.3/C.4: only the user's own normal states can join a system — the opponent never gets
    one (never a member, never a hub), and finish/start-anchor nodes are global landmarks, not
    a system (the App has one guard/pass/submission per graph, not a 'system of finishes')."""
    if actor != "you":
        return False
    if node_key == _FINISH_KEY:
        return False
    if _is_start(node_key):
        return False
    return True


# Owner adendo 2026-08-27 (item 1, reframed by the density adendo): the old "neighbours touch
# >=2 communities" bridge rule saturates on a small/dense graph — nearly every node's neighbours
# span 2 communities, so nearly every node became a memberless bridge. Replaced by DOMINANCE: a
# node belongs to whichever community holds the LARGEST share of its incident edge weight; it's
# only a bridge when no community holds a clear majority. Measured against the real bundle AFTER
# the gate above (`_GATE_MIN_SUPPORT_DEFAULT`/`_GATE_POLICY_DEFAULT`) removes the generic/inferred
# connective tissue that was gluing everything together — the threshold below is what's left to
# tune once density is no longer an artefact. See the module docstring's gating table.
_DOMINANCE_THRESHOLD = 0.5


def _weighted_incidence(g: nx.Graph, comm_of: dict[str, int]) -> dict[str, dict[int, int]]:
    """qid -> {community_idx: incident_weight}, off the graph's OWN edges and the community
    assignment passed in (a single static pass, same convention the old span-based bridge check
    used) — never recomputed against a mid-reassignment state."""
    dist: dict[str, dict[int, int]] = {}
    for u, v, data in g.edges(data=True):
        w = data.get("weight", 1)
        dist.setdefault(u, {}).setdefault(comm_of[v], 0)
        dist[u][comm_of[v]] += w
        dist.setdefault(v, {}).setdefault(comm_of[u], 0)
        dist[v][comm_of[u]] += w
    return dist


def _dominant_pick(dist: dict[int, int], alive: set[int]) -> tuple[int | None, float]:
    """argmax community among ALIVE candidates + its fraction of the node's TOTAL incident
    weight (every neighbour, alive or not — weight that only ever pointed at an already-dissolved
    community has nothing left to dominate, so it reads as unclear, i.e. a bridge, rather than a
    false member of whatever else survives). Tie-break: smallest community id."""
    total = sum(dist.values())
    if not total:
        return None, 0.0
    alive_dist = {c: w for c, w in dist.items() if c in alive}
    if not alive_dist:
        return None, 0.0
    best_comm, best_w = max(sorted(alive_dist.items()), key=lambda kv: kv[1])
    return best_comm, best_w / total


def _resolve_systems(g: nx.Graph, comm_of: dict[str, int]) -> dict[str, int | None]:
    """qid -> its dominant community id, or ``None`` (bridge). First pass: dominance over the
    ORIGINAL greedy-modularity communities. Then a bounded fixed point dissolves any community
    left with < 2 members — "a system of 1 isn't a system" — re-picking each of its lone members
    among the communities still standing (or making it a bridge, if nothing dominant survives).
    Every iteration strictly shrinks the alive set, so this always terminates."""
    dist = _weighted_incidence(g, comm_of)
    alive = set(comm_of.values())

    def _assign_all() -> dict[str, int | None]:
        out: dict[str, int | None] = {}
        for qid in g.nodes:
            best, frac = _dominant_pick(dist.get(qid, {}), alive)
            touched = len(dist.get(qid, {}))
            out[qid] = None if best is None or (frac < _DOMINANCE_THRESHOLD and touched >= 2) else best
        return out

    assign = _assign_all()
    for _ in range(len(g.nodes) + 1):
        sizes: dict[int, int] = {}
        for c in assign.values():
            if c is not None:
                sizes[c] = sizes.get(c, 0) + 1
        weak = {c for c, n in sizes.items() if n < 2}
        if not weak:
            break
        alive -= weak
        assign = _assign_all()
    return assign


def _detect_systems(states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]],
                     handovers: list[dict[str, Any]]) -> dict[str, Any]:
    """Greedy-modularity communities (as a starting partition only) over the ELIGIBLE subset
    (``_system_eligible`` — you-only, never finish/start) of the (already gated, by the caller)
    two-sided graph, then reassigned by DOMINANCE (``_resolve_systems``) into final "systems" —
    a node belongs to whichever community holds most of its incident weight; a bridge is one
    where nothing does. Determinism (cicatriz #10, same convention as
    ``analysis.network_metrics.detect_communities``): nodes/edges added in SORTED order, every
    tie (hub pick, community ordering) breaks on a stable sort key, never dict/set order.

    ``bridge_strength`` (owner adendo 2026-08-27, "só as mais fortes"): each bridge's own total
    incident weight — the caller ranks/truncates the DISPLAYED bridge set from this without
    recomputing anything; the full set (``bridge_qids``) always stays the honest backend truth."""
    eligible = {k: v for k, v in states.items() if _system_eligible(k[0], k[1])}
    qid_of = {key: _qid(key[1], key[0]) for key in eligible}
    g: nx.Graph = nx.Graph()
    for key in sorted(eligible, key=lambda k: qid_of[k]):
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

    # App parity (owner call 2026-08-27): the partition comes from the SAME detector the App
    # runs on the device — `analysis.constellations.detect`, of which the App's
    # `src/services/constellations/detect.ts` is a line-by-line port (Louvain, resolution 1.0,
    # seeded, plus the ADR-07 split of any internally disconnected community). It was greedy
    # modularity here, which is a different algorithm and could hand him systems his own App
    # would never show. Membership is topology only — no rating argument exists on that
    # signature and none should ever be added. The dominance pass below still runs on top: it
    # is what extracts BRIDGES, a concept the App's pure partition has no room for.
    dg: nx.DiGraph = nx.DiGraph()
    dg.add_nodes_from(sorted(g.nodes))
    directed: dict[tuple[str, str], int] = {}
    for e in edges:
        u, v = _qid(e["actor"], e["source"]), _qid(e["actor"], e["target"])
        if u == v or u not in g or v not in g:
            continue
        directed[(u, v)] = directed.get((u, v), 0) + e["count"]
    for u, v in sorted(directed):
        dg.add_edge(u, v, weight=directed[(u, v)])

    if g.number_of_edges() == 0:
        comms = [[n] for n in sorted(g.nodes)]
    else:
        comms = [sorted(c.members) for c in constellation_detect(dg).constellations]
    comms = sorted(comms, key=lambda c: (-len(c), c[0]))

    comm_of: dict[str, int] = {}
    for idx, members in enumerate(comms):
        for qid in members:
            comm_of[qid] = idx

    assign = _resolve_systems(g, comm_of)
    dist = _weighted_incidence(g, comm_of)
    bridge_qids = {qid for qid, c in assign.items() if c is None}
    bridge_strength = {qid: sum(dist.get(qid, {}).values()) for qid in bridge_qids}

    groups: dict[int, list[str]] = {}
    for qid, c in assign.items():
        if c is not None:
            groups.setdefault(c, []).append(qid)
    group_list = sorted((sorted(members) for members in groups.values()),
                         key=lambda ms: (-len(ms), ms[0]))

    node_of = {qid_of[key]: (key, v) for key, v in eligible.items()}
    systems: list[dict[str, Any]] = []
    system_of: dict[str, str] = {}
    for idx, members in enumerate(group_list):
        hub_qid = min(members, key=lambda qid: (-g.degree(qid), qid))  # max degree, tie by id
        hub_key, hub_v = node_of[hub_qid]
        sys_id = f"sys:{idx}"
        for qid in members:
            system_of[qid] = sys_id
        systems.append({
            "id": sys_id, "hub_qid": hub_qid, "hub_key": hub_key[0],
            "label": f"Sistema: {hub_v['label']}", "members": members,
            "actor": "you", "size": _clamp3(len(members)),
        })
    return {"systems": systems, "system_of": system_of, "bridge_qids": sorted(bridge_qids),
            "bridge_strength": bridge_strength}


_BRIDGE_DISPLAY_TOP_N = 4  # owner adendo: show only the STRONGEST bridges; easy knob to retune


def _select_displayed_bridges(bridge_strength: dict[str, int], top_n: int = _BRIDGE_DISPLAY_TOP_N
                               ) -> frozenset[str]:
    """Top-N bridges by incident weight (tie-break: qid) — the ones the VIEW shows. Every other
    bridge stays in the honest backend count (``bridge_qids``) but never reaches a rendered node,
    same convention variant 4 already uses to drop low-usage opponent elements."""
    ranked = sorted(bridge_strength.items(), key=lambda kv: (-kv[1], kv[0]))
    return frozenset(qid for qid, _w in ranked[:top_n])


def _excluded_render_rank(node_key: str, is_bridge: bool) -> int:
    """Constructive render order (owner adendo): systems first (handled by the caller placing
    system nodes before this bucket), then the bolted ANCHORS (start/finish — the skeleton's
    fixed landmarks), then the strongest BRIDGES (already display-filtered upstream), then
    everyone else (opponent elements)."""
    if node_key == _FINISH_KEY or _is_start(node_key):
        return 0
    if is_bridge:
        return 1
    return 2


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


def _cross_member_links(system_of: dict[str, str], edges: list[dict[str, Any]],
                         handovers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Variant 9's cross-system data at MEMBER granularity — same crossing edges as
    `_cross_system_links`, but keyed by the two real qids (never collapsed to a system id) plus
    each side's system id, so the CLIENT can re-resolve an endpoint to either the real member
    (its system is expanded) or the collapsed system node (it isn't) as the user expands/
    collapses systems in place. ``count`` stays raw — the client clamps after summing, same
    order as `_cross_system_links`'s own server-side clamp."""
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for e in edges:
        u, v = _qid(e["actor"], e["source"]), _qid(e["actor"], e["target"])
        su, sv = system_of.get(u), system_of.get(v)
        if su is None or sv is None or su == sv:
            continue
        row = agg.setdefault((u, v), {"count": 0, "handover_only": True, "from_sys": su,
                                      "to_sys": sv, "actions": {}})
        row["count"] += e["count"]
        row["handover_only"] = False
        # the ACTION that crosses, not just that something did — the frontier stub labels itself
        # with it, so "where does this system go" also answers "by doing what".
        row["actions"][e["action_label"]] = row["actions"].get(e["action_label"], 0) + e["count"]
    for h in handovers:
        u, v = h["from"], h["to"]
        su, sv = system_of.get(u), system_of.get(v)
        if su is None or sv is None or su == sv:
            continue
        row = agg.setdefault((u, v), {"count": 0, "handover_only": True, "from_sys": su,
                                      "to_sys": sv, "actions": {}})
        row["count"] += h["count"]
    return [
        {"from": u, "to": v, "count": row["count"], "dashed": row["handover_only"],
         "fromSys": row["from_sys"], "toSys": row["to_sys"],
         "action_label": (sorted(row["actions"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                          if row["actions"] else "")}
        for (u, v), row in sorted(agg.items())
    ]


def _excluded_states(states: dict[tuple[str, str], dict[str, Any]], system_of: dict[str, str]) -> dict[tuple[str, str], dict[str, Any]]:
    """Every state NOT a system member (opponent, finish, start-anchor, or a bridge — anything
    ``_detect_systems`` didn't put in ``system_of``) — rendered individually at every level
    (level1/level2 of variant 8, the always-visible SYSTEMS array of variant 9), never
    collapsed, never inside a region."""
    return {k: v for k, v in states.items() if _qid(k[1], k[0]) not in system_of}


def _place_of(system_of: dict[str, str], excluded: dict[tuple[str, str], dict[str, Any]]) -> dict[str, str]:
    """Extends ``system_of`` so EVERY rendered qid resolves to a "placement" id — a system
    member maps to its collapsed ``sys:N``; anything excluded (opponent/finish/start/bridge)
    maps to ITSELF. ``_cross_system_links``/``_cross_member_links`` then need no special case
    at all: aggregating by ``place_of[u] != place_of[v]`` naturally produces system<->bridge,
    system<->opponent, bridge<->bridge, etc — the sistema-ponte-sistema topology (C, owner
    whiteboard 2026-08-27) falls out of the SAME aggregation that used to (wrongly) produce
    direct system-to-system edges, because a real cross-system edge's endpoint is now always a
    bridge (pulled out of its community precisely because it touches >=2 systems), never a
    plain member of either side."""
    place = dict(system_of)
    for k in excluded:
        qid = _qid(k[1], k[0])
        place[qid] = qid
    return place


# Violet — a collapsed system is neither an actor state (blue/orange), finish (yellow), a
# start-anchor (teal) nor a bridge (grey): its own colour so it never reads as a normal node.
_SYSTEM_COLOR = _FIG_HEX["a"]  # owner 2026-08-27: belt colour, not violet — the double ring is the mark

# Pink — 11/12's own boundary marker (a system MEMBER with >=1 crossing link), distinct from
# every other ring colour in this module (system violet, finish/actor blue-orange, bridge grey,
# start teal) so a member that also happens to touch the boundary never gets mistaken for one of
# those other categories.
_BOUNDARY_COLOR = "#f472b6"


def _system_node(s: dict[str, Any]) -> dict[str, Any]:
    """Owner addendum 2026-08-27: a collapsed system must look like "several things folded",
    not a normal state — a bigger radius that grows with member count + the member count baked
    into the label ("Sistema: X · N"). ``system: True`` gives it the TOP label-collision priority
    (above a bridge's) — the collapsed/expanded pair should read as the same entity at both
    levels, and a system whose own label loses to a member's is illegible.

    Owner correction (2026-08-27, second pass): drop the violet FILL — the distinction must come
    from the ring alone (plus the size/label already above), so a system node doesn't steal
    attention by colour. No explicit ``color`` key here means the client falls back to the same
    category colour an ORDINARY node of this ``cat`` would use (`graph.js`'s own
    ``n.color || (n.fighter?...:CAT[n.cat])`` — never re-derived here, just relying on the
    existing fallback). The ring alone isn't enough at a glance once it's no longer paired with a
    unique fill, so it's now a literal double stroke (`n.system` — the copy's ring patch draws a
    second, wider concentric ring only for a system node; see `_patch_graph_js`)."""
    return {
        "id": s["id"], "label": f"{s['label']} · {len(s['members'])}", "cat": "control",
        "size": min(6, 3 + len(s["members"]) // 2),
        "ring": _SYSTEM_COLOR, "system": True,
    }


def _systems_level1_view(systems: list[dict[str, Any]], excluded: dict[tuple[str, str], dict[str, Any]],
                          cross_links: list[dict[str, Any]], bridge_qids: frozenset[str],
                          radius: float) -> dict[str, Any]:
    """Constructive order (owner adendo): systems (the skeleton) first, then ``excluded`` ranked
    anchors-before-bridges-before-opponent (``_excluded_render_rank``) — ``bridge_qids`` here is
    already the DISPLAY-filtered set (top-N by strength), so every bridge reaching this function
    renders; the caller drops the rest from ``excluded`` before calling."""
    nodes = [_system_node(s) for s in systems]
    nodes += [
        _style_single_node(k[0], k[1], v, radius=radius, is_bridge=_qid(k[1], k[0]) in bridge_qids)
        for k, v in sorted(
            excluded.items(),
            key=lambda kv: (_excluded_render_rank(kv[0][0], _qid(kv[0][1], kv[0][0]) in bridge_qids),
                             _qid(kv[0][1], kv[0][0])),
        )
    ]
    return {"nodes": nodes, "links": cross_links}


def _system_members(sys_row: dict[str, Any], states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]],
                     handovers: list[dict[str, Any]]
                     ) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """One system's own (states, internal edges, internal handovers) slice, member_qids-filtered
    — shared by variant 8's drill-down (`_system_level2`, which adds stub nodes on top) and
    variant 9's in-place expansion (`_render_variant9`, which needs the bare subgraph — the
    CROSS payload reconnects it to the rest of the view, no stubs needed)."""
    member_qids = set(sys_row["members"])
    sub_states = {k: v for k, v in states.items() if _qid(k[1], k[0]) in member_qids}
    internal_edges = [e for e in edges
                       if _qid(e["actor"], e["source"]) in member_qids
                       and _qid(e["actor"], e["target"]) in member_qids]
    internal_handovers = [h for h in handovers
                           if h["from"] in member_qids and h["to"] in member_qids]
    return sub_states, internal_edges, internal_handovers


def _system_level2(sys_row: dict[str, Any], states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]],
                    handovers: list[dict[str, Any]], excluded: dict[tuple[str, str], dict[str, Any]], bridge_qids: frozenset[str],
                    radius: float) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """One system's own subgraph (nodes/edges/labels as variant 4), a REGION covering only its
    own members (C.1 — hull/circle behind everything, drawn by the client), plus every neighbour
    that touches it from outside. Owner whiteboard, 2026-08-27: by construction (`_detect_systems`)
    a system member's only external neighbours are BRIDGE nodes or non-eligible individuals
    (opponent/finish/start) — never another system directly, since any node touching >=2
    systems was pulled out as a bridge already — so those neighbours are embedded here as their
    own REAL, individually-styled nodes (no synthetic "stub" indirection any more)."""
    member_qids = set(sys_row["members"])
    sub_states, internal_edges, internal_handovers = _system_members(sys_row, states, edges, handovers)
    gv = _two_sided_graphview(sub_states, internal_edges, internal_handovers)

    excluded_by_qid = {_qid(k[1], k[0]): (k, v) for k, v in excluded.items()}
    extra_nodes: dict[str, dict[str, Any]] = {}
    extra_links: list[dict[str, Any]] = []

    def _connect(this_qid: str, other_qid: str, weight: int, dashed: bool, label: str | None) -> None:
        found = excluded_by_qid.get(other_qid)
        if found is None:
            return  # not a neighbour this system needs to show (e.g. another system's member)
        (o_key, o_actor), o_v = found
        if other_qid not in extra_nodes:
            extra_nodes[other_qid] = _style_single_node(
                o_key, o_actor, o_v, radius=radius, is_bridge=other_qid in bridge_qids)
        link = {"from": this_qid, "to": other_qid, "weight": _clamp3(weight), "arrow": True,
                "dashed": dashed}
        if label:
            link["label"] = label
        extra_links.append(link)

    for e in edges:
        u, v = _qid(e["actor"], e["source"]), _qid(e["actor"], e["target"])
        label = None if e["inferred"] else e["action_label"]
        if u in member_qids and v not in member_qids:
            _connect(u, v, e["count"], bool(e["inferred"]), label)
        elif v in member_qids and u not in member_qids:
            _connect(v, u, e["count"], bool(e["inferred"]), label)
    for h in handovers:
        if h["from"] in member_qids and h["to"] not in member_qids:
            _connect(h["from"], h["to"], h["count"], True, None)
        elif h["to"] in member_qids and h["from"] not in member_qids:
            _connect(h["to"], h["from"], h["count"], True, None)

    gv["nodes"] = gv["nodes"] + sorted(extra_nodes.values(), key=lambda n: n["id"])
    gv["links"] = gv["links"] + extra_links
    _index_parallel_links(gv["links"])
    regions = [{"label": sys_row["label"], "members": sys_row["members"]}]
    return gv, internal_edges, regions


# ── 2c. variants 11/12 — separate systems view (global + per-system stubs) ─────
#
# Frente 2 (owner request, 2026-08-27): NOT variant 8/9's model. There the system view embeds
# every real outside neighbour individually (`_system_level2`); here it collapses each crossing
# to a STUB mini-node keyed by DESTINATION PLACE (`_cross_member_links`'s own {from,to,fromSys,
# toSys} — the model, never reinvented). Different views, both already owner-approved — see the
# module docstring's "11/12" paragraph.

_OPPONENT_MODES = ("complete", "selective", "none")
_OPPONENT_MODE_LABELS = {
    "complete": "oponente completo", "selective": "oponente seletivo (>=2x ou ponte)",
    "none": "só você",
}


def _opponent_scoped(agg: Aggregate, mode: str) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """The third combo axis (11/12's "modo de oponente") — same (states, edges, handovers) shape
    as `_complete_two_sided`/`_selective_states_and_edges`, generalised with a THIRD mode
    (``"none"``) that drops the opponent's own subgraph entirely: states/edges filtered to
    actor=='you', no handovers (a handover always needs a partner endpoint on one side).
    ``"complete"``/``"selective"`` reuse the existing element-set builders verbatim."""
    if mode == "complete":
        return dict(agg.states), list(agg.edges.values()), list(agg.handovers.values())
    if mode == "selective":
        return _selective_states_and_edges(agg)
    if mode == "none":
        states = {k: v for k, v in agg.states.items() if k[1] == "you"}
        edges = [e for e in agg.edges.values() if e["actor"] == "you"]
        return states, edges, []
    raise ValueError(f"unknown opponent mode: {mode!r}")


def _collapse_directed(place_of: dict[str, str], edges: list[dict[str, Any]],
                        handovers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Level-1 aggregated links for 11/12's GLOBAL view — same crossing-edge aggregation as
    `_cross_system_links` (never that function itself, variant 8/9 stay untouched), but direction
    is decided by `edge_arrow` (imported from `analysis.network_metrics`, never re-derived) over
    each UNORDERED pair's forward/reverse weight: at THIS aggregate level the site's own
    directed-edge contract applies (root CLAUDE.md's directed-edges row) — a per-action/handover/
    stub link stays structurally directed (arrow=True always, decided elsewhere), but a volume
    aggregate between two systems/bridges/anchors can and should read as undirected when it's a
    genuine two-way exchange or too sparse to call. ``dashed`` iff every crossing between that
    pair was a handover, same convention as `_cross_system_links`."""
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    direct: list[dict[str, Any]] = []

    def _aggregated(u: str, v: str) -> bool:
        """Only a COLLAPSED SYSTEM stands for many underlying edges — that is the one place a
        volume aggregate is the honest rendering. Two real nodes (opponent, bridge, anchor) keep
        one link PER ACTION, so several actions bridging the same pair fan into their own arcs
        with their own labels (owner 2026-08-27) instead of merging into a single mute stroke."""
        return u.startswith("sys:") or v.startswith("sys:")

    def _add(u: str | None, v: str | None, w: int, is_action: bool) -> None:
        if u is None or v is None or u == v:
            return
        lo, hi = (u, v) if u <= v else (v, u)
        row = agg.setdefault((lo, hi), {"fwd": 0, "rev": 0, "handover_only": True})
        if u == lo:
            row["fwd"] += w
        else:
            row["rev"] += w
        if is_action:
            row["handover_only"] = False

    for e in edges:
        u = place_of.get(_qid(e["actor"], e["source"]))
        v = place_of.get(_qid(e["actor"], e["target"]))
        if u is None or v is None or u == v:
            continue
        if _aggregated(u, v):
            _add(u, v, e["count"], True)
        else:
            link = {"from": u, "to": v, "weight": _clamp3(e["count"]), "arrow": True,
                    "label": e["action_label"], "fighter": _ACTOR_SIDE[e["actor"]]}
            if e["inferred"]:
                link["inf"] = True
            direct.append(link)
    for h in handovers:
        u, v = place_of.get(h["from"]), place_of.get(h["to"])
        if u is None or v is None or u == v:
            continue
        if _aggregated(u, v):
            _add(u, v, h["count"], False)
        else:
            direct.append({"from": u, "to": v, "weight": _clamp3(h["count"]), "arrow": True,
                            "dashed": True, "fighter": "x"})

    links = []
    for (lo, hi), row in sorted(agg.items()):
        f, r = row["fwd"], row["rev"]
        src, tgt = (lo, hi) if f >= r else (hi, lo)
        links.append({"from": src, "to": tgt, "weight": _clamp3(f + r),
                      "arrow": edge_arrow(f, r), "dashed": row["handover_only"]})
    links += direct
    _index_parallel_links(links)
    return links


def _stub_node(dest_place: str, systems_by_id: dict[str, dict[str, Any]],
                excluded_by_qid: dict[str, tuple[tuple[str, str], dict[str, Any]]],
                bridge_qids: frozenset[str]) -> dict[str, Any]:
    """One boundary stub — a mini-node per DESTINATION (never per traversal), styled by what kind
    of thing it points at: another SYSTEM (violet, `_system_node`'s own styling), a BRIDGE (grey,
    `is_bridge=True`), an organisational ANCHOR (finish/start's own colour, both via
    `_style_single_node`), or an OPPONENT element (no override needed — `_style_single_node`'s own
    ``fighter`` field already reads orange client-side). Radius=0.0: a stub is never itself a
    bolted anchor even when its DESTINATION is one — `_apply_anchor` would otherwise pin/place it
    at the shared cross landmark, so x/y/pin are stripped after."""
    if dest_place.startswith("sys:"):
        node = dict(_system_node(systems_by_id[dest_place]))
    else:
        (node_key, actor), v = excluded_by_qid[dest_place]
        node = _style_single_node(node_key, actor, v, radius=0.0, is_bridge=dest_place in bridge_qids)
        node.pop("x", None)
        node.pop("y", None)
        node.pop("pin", None)
    node["id"] = dest_place
    node["size"] = 1
    node["stub"] = True
    return node


def _system_boundary_view(
    sys_row: dict[str, Any], states: dict[tuple[str, str], dict[str, Any]], edges: list[dict[str, Any]], handovers: list[dict[str, Any]],
    cross: list[dict[str, Any]], place_of: dict[str, str],
    excluded_by_qid: dict[str, tuple[tuple[str, str], dict[str, Any]]],
    systems_by_id: dict[str, dict[str, Any]], bridge_qids: frozenset[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    """11/12's OWN system view — induced subgraph + fronteira, stub model (never
    `_system_level2`'s embedded-real-neighbour model, see module docstring). A boundary MEMBER
    gets a ring (`_BOUNDARY_COLOR`) + a ``[→out ←in]`` suffix; every crossing (from
    `_cross_member_links`, the shared model, never reinvented) collapses to ONE stub node per
    DESTINATION PLACE (several real members of the same other system, or several crossings into
    the same bridge/anchor/opponent, share one mini-node) with one link per (member, place,
    direction) — ida+volta land on the same unordered pair, `_index_parallel_links` already fans
    them into two opposite arcs with their own arrowheads. Returns (graphview, internal action
    edges, regions, boundary panel HTML)."""
    member_qids = set(sys_row["members"])
    sub_states, internal_edges, internal_handovers = _system_members(sys_row, states, edges, handovers)
    gv = _two_sided_graphview(sub_states, internal_edges, internal_handovers)
    for link, e in zip(gv["links"][: len(internal_edges)], internal_edges):
        link["at"] = e["action_type"]  # 12's client-side type filter — action links only

    relevant = [c for c in cross if c["fromSys"] == sys_row["id"] or c["toSys"] == sys_row["id"]]
    out_count: dict[str, int] = {}
    in_count: dict[str, int] = {}
    stub_links: dict[tuple[str, str, bool], dict[str, Any]] = {}
    for c in relevant:
        if c["from"] in member_qids:
            member_qid, dest_qid, member_is_source = c["from"], c["to"], True
        else:
            member_qid, dest_qid, member_is_source = c["to"], c["from"], False
        dest_place = place_of[dest_qid]
        if member_is_source:
            out_count[member_qid] = out_count.get(member_qid, 0) + 1
        else:
            in_count[member_qid] = in_count.get(member_qid, 0) + 1
        row = stub_links.setdefault((member_qid, dest_place, member_is_source),
                                    {"count": 0, "actions": {}})
        row["count"] += c["count"]
        # keep the action that carries the crossing — a frontier stub whose label only said
        # "there is a way out" would answer half the owner's question ("para onde o sistema vai").
        if c.get("action_label"):
            row["actions"][c["action_label"]] = row["actions"].get(c["action_label"], 0) + c["count"]

    for node in gv["nodes"]:
        o, i = out_count.get(node["id"], 0), in_count.get(node["id"], 0)
        if o or i:
            node["ring"] = _BOUNDARY_COLOR
            node["label"] = f"{node['label']} [→{o} ←{i}]"

    stub_nodes: dict[str, dict[str, Any]] = {}
    for (member_qid, dest_place, member_is_source), row in sorted(stub_links.items()):
        if dest_place not in stub_nodes:
            stub_nodes[dest_place] = _stub_node(dest_place, systems_by_id, excluded_by_qid, bridge_qids)
        src, tgt = (member_qid, dest_place) if member_is_source else (dest_place, member_qid)
        top = sorted(row["actions"].items(), key=lambda kv: (-kv[1], kv[0]))
        if top:
            # a real action leaves/enters the system: belt colour + the short stub dash, so it
            # reads with the same weight as the action edges inside the region
            link = {"from": src, "to": tgt, "weight": _clamp3(row["count"]), "arrow": True,
                     "dash": [2, 3], "label": f"{top[0][0]} ×{row['count']}",
                     "fighter": _ACTOR_SIDE["you"], "inf": True}
        else:
            # nothing crossed but a HANDOVER — the exchange changed hands, there is no action to
            # name. It used to render as a mute "×1" stub, which read as a missing label rather
            # than as the thing it is; it now carries the map's own handover vocabulary (grey,
            # long dash) and says so.
            link = {"from": src, "to": tgt, "weight": _clamp3(row["count"]), "arrow": True,
                     "dashed": True, "label": f"troca de mãos ×{row['count']}",
                     "fighter": "x", "inf": True}
        gv["links"].append(link)
    gv["nodes"] = gv["nodes"] + sorted(stub_nodes.values(), key=lambda n: n["id"])
    _index_parallel_links(gv["links"])

    id_to_label = {n["id"]: n["label"] for n in gv["nodes"]}
    boundary_rows = []
    for (member_qid, dest_place, member_is_source), row in sorted(stub_links.items()):
        src_id, tgt_id = (member_qid, dest_place) if member_is_source else (dest_place, member_qid)
        src, tgt = id_to_label.get(src_id, src_id), id_to_label.get(tgt_id, tgt_id)
        boundary_rows.append(
            f'<div class="row">{src} → {tgt} <span class="muted">(travessia, x{row["count"]})</span></div>'
        )
    boundary_html = "".join(boundary_rows)

    regions = [{"label": sys_row["label"], "members": sys_row["members"]}]
    return gv, internal_edges, regions, boundary_html


def _member_chips_html(sys_row: dict[str, Any], qid_to_state: dict[str, dict[str, Any]]) -> str:
    """One clickable chip per system member, sorted by usage count desc (tie: qid) — a system
    here only ever has a handful of members (2-6, measured), so no truncation. ``data-sys``/
    ``data-qid`` are read by the page's own delegated click handler (`_PAGE_SYSTEMS`) to open
    that system and highlight the member (``mounted.select``)."""
    rows = sorted(
        ((qid_to_state[qid]["label"], qid_to_state[qid]["count"], qid) for qid in sys_row["members"]),
        key=lambda t: (-t[1], t[0]),
    )
    return "".join(
        f'<span class="chip" data-sys="{sys_row["id"]}" data-qid="{qid}">{label}</span>'
        for label, _count, qid in rows
    )


def _combo_key(opponent_mode: str, min_support: int, inference_policy: str) -> str:
    return f"{opponent_mode}|{min_support}|{inference_policy}"


# Owner call 2026-08-27, after reading the gating comparison on his own data: this is THE
# criterion, so both 11 and 12 open on it. min_support=1 because the App's own
# DEFAULT_MIN_EDGE_SUPPORT=2 measured too aggressive for a bundle this sparse; the inference gate
# is what actually carries the cleanup (a single-occurrence generic edge is the table shrugging,
# and it was wiring everything to everything); selective opponent keeps a partner element only
# when it recurs or is the sole bridge to something of his. On his bundle: 2 systems [5,2],
# 3 bridges, 15 nodes / 39 edges against 18/48 wide open.
_DEFAULT_SYSTEMS_COMBO: tuple[str, int, str] = ("selective", 1, "inferred_min2")
_WIDEST_SYSTEMS_COMBO: tuple[str, int, str] = ("complete", 1, "all")  # nothing gated — the "vs" reference

# Adaptive gate (owner 2026-08-27): the right amount of gating is a function of how much the
# map is about to draw, not a fixed policy. A beginner's graph is small enough to show whole —
# gating it hides the little he has; a mature graph drowns without a gate. Thresholds are on the
# UNGATED artefact count (nodes + edges of the widest combo), so the decision is made against
# what would actually be rendered, and the ladder only ever tightens as the map grows.
# Calibrated against the owner's own judgement, not a round number: his bundle renders 59
# artefacts ungated, and having compared both he chose the gated reading — so the permissive band
# ends below that. A map only shows everything while it is genuinely thin.
_ADAPTIVE_GATE_LADDER: tuple[tuple[int, tuple[str, int, str]], ...] = (
    (40, ("complete", 1, "all")),               # thin: show everything, opponent included
    (110, ("selective", 1, "inferred_min2")),   # growing: drop one-off generics, thin the opponent
    (10**9, ("selective", 2, "inferred_min2")), # large: also require an edge to have recurred
)


def adaptive_combo(artefacts: int) -> tuple[str, int, str]:
    """Pick the gate from how many artefacts the ungated map would render (see the ladder)."""
    for ceiling, combo in _ADAPTIVE_GATE_LADDER:
        if artefacts < ceiling:
            return combo
    return _ADAPTIVE_GATE_LADDER[-1][1]
_ALL_SYSTEMS_COMBOS: tuple[tuple[str, int, str], ...] = tuple(
    (om, ms, pol) for om in _OPPONENT_MODES for ms in _GATE_MIN_SUPPORTS for pol in _GATE_POLICIES
)  # 3 x 3 x 4 = 36, literal nested-loop order (deterministic — never a set on this path)


def _combo_payload(agg: Aggregate, opponent_mode: str, min_support: int, inference_policy: str
                    ) -> dict[str, Any]:
    """One (opponent_mode, min_support, inference_policy) combo's full page data — global + every
    system, self-contained enough to embed straight into the HTML template. Bridges here are
    NEVER display-truncated (unlike 8/9's `_gated_systems` top-N) — 11/12 show every bridge the
    dominance rule found (owner: "o dono liberou a densidade porque o detalhe mora na vista de
    sistema"); ``bridgeRank`` (rank by `bridge_strength`, 0=strongest) lets B's own slider cut the
    DISPLAYED count client-side without recomputing anything server-side."""
    raw_states, raw_edges, raw_handovers = _opponent_scoped(agg, opponent_mode)
    states, edges, handovers = _apply_gate(
        raw_states, raw_edges, raw_handovers, min_support=min_support, inference_policy=inference_policy)
    detected = _detect_systems(states, edges, handovers)
    systems, system_of = detected["systems"], detected["system_of"]
    bridge_qids = frozenset(detected["bridge_qids"])
    excluded = _excluded_states(states, system_of)
    place_of = _place_of(system_of, excluded)
    cross = _cross_member_links(place_of, edges, handovers)
    radius = _anchor_radius(len(states))

    strength_rank = {
        qid: rank for rank, (qid, _w) in enumerate(
            sorted(detected["bridge_strength"].items(), key=lambda kv: (-kv[1], kv[0])))
    }

    global_links = _collapse_directed(place_of, edges, handovers)
    global_gv = _systems_level1_view(systems, excluded, global_links, bridge_qids, radius)
    for node in global_gv["nodes"]:
        if node["id"] in strength_rank:
            node["bridgeRank"] = strength_rank[node["id"]]
    global_knobs = _mount_knobs(len(global_gv["nodes"]))
    # owner adendo (2026-08-27, second pass): a state that's now a system MEMBER has no node of
    # its own in the global view any more (it's folded into the collapsed system) — the owner
    # asked "cadê o sistema quatro apoios?" about exactly this, with no way to tell which system a
    # technique landed in short of opening every one. `qid_to_state` covers every state (member
    # AND excluded) so each system's row can list its own members by name.
    qid_to_state = {_qid(k[1], k[0]): v for k, v in states.items()}
    global_list = "".join(
        f'<div class="row">{s["label"]} <span class="muted">({len(s["members"])} nos)</span>'
        f'<div class="members">{_member_chips_html(s, qid_to_state)}</div></div>'
        for s in systems
    )

    systems_by_id = {s["id"]: s for s in systems}
    excluded_by_qid = {_qid(k[1], k[0]): (k, v) for k, v in excluded.items()}

    system_pages: dict[str, Any] = {}
    for s in systems:
        gv, internal_edges, regions, boundary_html = _system_boundary_view(
            s, states, edges, handovers, cross, place_of, excluded_by_qid, systems_by_id, bridge_qids)
        id_to_label = {n["id"]: n["label"] for n in gv["nodes"]}
        system_pages[s["id"]] = {
            "title": s["label"],
            "subtitle": f"{len(s['members'])} nos, {s['actor']}-dominant",
            "gv": gv, "regions": regions,
            "listHtml": _edge_list_html(internal_edges, id_to_label) + boundary_html,
            **_mount_knobs(len(gv["nodes"])),
        }

    return {
        "global": {"title": "Global", "subtitle": f"{len(systems)} sistema(s), {len(bridge_qids)} ponte(s)",
                   "gv": global_gv, "regions": [], "listHtml": global_list, **global_knobs},
        "systems": system_pages,
        "meta": {
            "opponent_mode": opponent_mode, "min_support": min_support, "inference_policy": inference_policy,
            "nodes": len(global_gv["nodes"]), "edges": len(global_gv["links"]),
            "systems": len(systems), "bridges": len(bridge_qids),
            "system_sizes": sorted((len(s["members"]) for s in systems), reverse=True),
        },
    }


# ── 2d. variant 13 — "Caminhos": render paths -> bundled visual graph ─────────────────
#
# Phase 4 of docs/taxonomy/03_ARESTA_COMO_CAMINHO.md, the owner's four layers:
#   1 semantic graph   — `Aggregate` (states + transitions keyed on the WHOLE action sequence)
#   2 render paths     — `render_paths` below: one `RenderPath` per aggregated occurrence
#   3 bundled graph    — `analysis.path_bundling.bundle_paths` (pure, tested on its own)
#   4 renderer         — `flow_layout` + `_PAGE13` + the graph.js copy's own patches
#
# Layout is computed HERE, in Python, and shipped as pinned x/y — not ported into the page's
# JavaScript as the plan first sketched. Three reasons, in the order they mattered:
#   * `graph.js` already honours `n.x`/`n.y` + the copy's `n.pin` patch, so a fully positioned
#     graph needs NO new renderer concept — the physics is simply never allowed to run;
#   * determinism becomes provable by `pytest` and by the `diff -r` gate, instead of resting on
#     a JS function nothing in this repo can execute;
#   * this repo's own convention for "the App owns the TS, we own the mirror" is a PYTHON mirror
#     (`network_metrics` ↔ `directedEdges.ts`, `presentation.py` ↔ `ratingV2Presentation.ts`).
#     The App already HAS `decisionFlowLayout.ts`; a JS transcription living inside a Python
#     string would be a third copy, not a shorter path to the port.
# The algorithm below is `decisionFlowLayout.ts`'s, not a new one: multi-source BFS ranks with a
# visited set (cycles are real here — `returns-to` back-edges), deterministic ordering inside a
# rank, x from the rank. Its SPLIT/MERGE routing is the one part deliberately dropped: those
# junctions exist there to fan a node's outgoing edges, and here the fan is already a first-class
# object — `Point(kind='branch'|'merge'|'branch-merge')`, produced by the bundler from the ACTION
# PREFIX, which is exactly the change the plan asked for ("o SPLIT vira por prefixo de ação").
# elkjs/dagre stay out (userDecisionFlow.ts:32-45 — measured, the density was the problem, the
# layout never was).

# Constants + the three layout functions moved to ``analysis/flow_layout.py`` (see the import
# at the top of this file) so the App can mirror ONE source against a golden fixture.


def render_paths(agg: Aggregate) -> list[RenderPath]:
    """Layer 2. One `RenderPath` per aggregated occurrence — endpoints actor-qualified through
    `_qid` (so your mount and the opponent's are different states, and the shared anchors merge
    exactly as in every other variant). Deterministic ids from the sorted aggregation key, never
    from dict order."""
    out: list[RenderPath] = []
    for i, key in enumerate(sorted(agg.edges)):
        source_key, target_key, actions, actor = key
        v = agg.edges[key]
        out.append(RenderPath(
            path_id=f"p{i}",
            source=_qid(actor, source_key),
            target=_qid(actor, target_key),
            actions=actions,
            actor=actor,
            count=v["count"],
        ))
    return out


def _rating_of_bundle(bundle: dict[str, Any]) -> dict[str, float]:
    """`graph.nodes[].data.computedElo` keyed by the node's own CANONICAL label — the bundle is
    what the App ships, and `computedElo` is the only per-node number in it (Rating V2 projects
    onto that field at the producer, root CLAUDE.md's Rating V2 row). First value wins on a
    duplicate canonical key, same convention as `_resolve_group`'s display map."""
    out: dict[str, float] = {}
    for n in (bundle.get("graph") or {}).get("nodes", []) or []:
        data = n.get("data") or {}
        elo = data.get("computedElo")
        label = data.get("label") or n.get("label") or ""
        if isinstance(elo, int | float) and not isinstance(elo, bool) and label:
            out.setdefault(canonicalize(_normalize_name(str(label))), float(elo))
    return out


_JUNCTION_COLOR = "#6b7280"  # a branch/merge dot is scaffolding, never a technique — no category hue
_PATH_SCOPE_GLOBAL = "global"


def _display_action_labels(agg: Aggregate) -> dict[str, str]:
    """Canonical action key -> the owner's own wording (first seen wins, same convention as
    ``_resolve_group``'s display map)."""
    out: dict[str, str] = {}
    for v in agg.edges.values():
        for key, label in zip(v["actions"], v["action_labels"], strict=True):
            out.setdefault(key, label)
    return out


def _inferred_action_keys(agg: Aggregate) -> set[str]:
    """Action keys that were NEVER observed anywhere in the bundle. A key that is inferred on one
    path and logged on another is NOT a ghost — the Fase 2 collision (``sweep``/``reversal``/
    ``guard pass`` are both generic verdicts and real corpus labels) is exactly that case, and
    ghosting it would call the owner's own logged sweep an invention."""
    observed: set[str] = set()
    inferred: set[str] = set()
    for v in agg.edges.values():
        for key, is_inf in zip(v["actions"], v["action_inferred"], strict=True):
            (inferred if is_inf else observed).add(key)
    return inferred - observed


def _segment_weight(seg: Segment, count_of: dict[str, int]) -> int:
    """Thickness = frequency: the summed occurrence count of every path that walks this stroke."""
    return sum(count_of.get(pid, 0) for pid in sorted(seg.path_ids))


def _paths_view(
    agg: Aggregate,
    *,
    structure: str,
    member_qids: frozenset[str] | None,
    metrics_by_path: dict[str, PathMetrics],
    paths: list[RenderPath],
) -> dict[str, Any]:
    """One (anchor structure × scope) page payload: the bundled graph laid out, plus everything
    the side panel reads. ``member_qids`` is ``None`` for the global view; for a system it is
    that system's members, and a path enters when either of its state endpoints is one — the
    endpoint OUTSIDE the system still draws, as a stub, so "para onde esse sistema vai" has an
    answer instead of a severed edge (same frontier convention as 11/12)."""
    unified = bool(_ANCHOR_STRUCTURES[structure]["unified_finish"])
    opp_finish = _qid("partner", _FINISH_KEY)

    def _fold(qid: str) -> str:
        return _FINISH_KEY if (unified and qid == opp_finish) else qid

    scoped = [
        p for p in paths
        if member_qids is None or _fold(p.source) in member_qids or _fold(p.target) in member_qids
    ]
    if unified:
        scoped = [
            RenderPath(path_id=p.path_id, source=_fold(p.source), target=_fold(p.target),
                       actions=p.actions, actor=p.actor, count=p.count)
            for p in scoped
        ]
    bundled = bundle_paths(scoped)

    count_of = {p.path_id: p.count for p in scoped}
    actor_of = {p.path_id: p.actor for p in scoped}
    labels = _display_action_labels(agg)
    ghosts = _inferred_action_keys(agg)
    qid_to_state = {_qid(k[1], k[0]): (k, v) for k, v in agg.states.items()}

    anchor_slots: dict[str, str] = {}
    node_weight: dict[str, float] = {}
    for seg in bundled.segments:
        w = float(_segment_weight(seg, count_of))
        node_weight[seg.from_point] = node_weight.get(seg.from_point, 0.0) + w
        node_weight[seg.to_point] = node_weight.get(seg.to_point, 0.0) + w
    for pt in bundled.points:
        if pt.state_key is None:
            continue
        found = qid_to_state.get(pt.state_key)
        node_key = found[0][0] if found else pt.state_key
        actor = found[0][1] if found else "you"
        slot = _anchor_slot(node_key, actor, structure)
        if slot is not None:
            anchor_slots[pt.id] = slot

    # Label bubbles for the relaxation: the STATE's rendered name and the SEGMENT's joined
    # action sequence — exactly the two strings this view draws.
    label_len: dict[str, int] = {}
    for pt in bundled.points:
        if pt.state_key is None:
            continue
        found = qid_to_state.get(pt.state_key) or qid_to_state.get(opp_finish)
        if found is None:
            continue
        (node_key, actor), v = found
        text = (v["label"] if (unified and node_key == _FINISH_KEY)
                else _finish_label(node_key, actor, v["label"]))
        label_len[pt.id] = len(str(text))
    for seg in bundled.segments:
        label_len[seg.id] = len(" → ".join(labels.get(k, k) for k in seg.actions))

    pos = flow_layout(bundled, structure=structure, anchor_slots=anchor_slots,
                        weight=node_weight, label_len=label_len)

    nodes: list[dict[str, Any]] = []
    for pt in sorted(bundled.points, key=lambda p: p.id):
        x, y = pos[pt.id]
        if pt.state_key is None:
            nodes.append({"id": pt.id, "label": "", "cat": "control", "size": 1,
                           "color": _JUNCTION_COLOR, "junction": True, "kind": pt.kind,
                           "x": x, "y": y, "pin": True})
            continue
        found = qid_to_state.get(pt.state_key) or qid_to_state.get(opp_finish)
        if found is not None:
            (node_key, actor), v = found
        else:  # defensive only — every state point comes from a row `Aggregate` already holds
            node_key, actor = "", "you"
            v = {"label": pt.state_key, "type": "control", "count": 1}
        in_scope = member_qids is None or pt.state_key in member_qids
        node: dict[str, Any] = {
            "id": pt.id, "stateKey": pt.state_key, "kind": "state",
            "label": v["label"] if (unified and node_key == _FINISH_KEY)
                      else _finish_label(node_key, actor, v["label"]),
            "cat": _cat_of(v["type"]), "size": _clamp3(v["count"]),
            "fighter": _ACTOR_SIDE[actor], "x": x, "y": y, "pin": True,
        }
        _apply_finish_style(node, node_key, actor)
        _apply_start_style(node, node_key)
        if unified and node_key == _FINISH_KEY:
            # one vertex, two athletes — the split fill is what keeps that honest
            node["split"] = [_FIG_HEX["a"], _FIG_HEX["b"]]
            node.pop("ring", None)
        if not in_scope:
            node["stub"] = True
            node["size"] = 1
        nodes.append(node)

    links: list[dict[str, Any]] = []
    seg_meta: dict[str, Any] = {}
    for seg in bundled.segments:
        acts = [labels.get(k, k) for k in seg.actions]
        weight = _segment_weight(seg, count_of)
        all_ghost = all(k in ghosts for k in seg.actions)
        fighters = {actor_of[p] for p in seg.path_ids}
        link = {
            "id": seg.id, "from": seg.from_point, "to": seg.to_point,
            "weight": _clamp3(weight), "arrow": True,
            "label": " → ".join(acts),
            "fighter": _ACTOR_SIDE[next(iter(sorted(fighters)))] if len(fighters) == 1 else "x",
        }
        if all_ghost:
            link["inf"] = True   # named generic: still labelled, yields on a label collision
            link["dash"] = [3, 4]
        # A RETURN edge (the target sits at or behind the source in the flow) is a real cycle in
        # a technique map, not a defect — but drawn as a straight line it runs backwards through
        # everything between. Bowed out and thinner, so the forward reading stays the loud one.
        if pos[seg.to_point][0] <= pos[seg.from_point][0]:
            link["bow"], link["back"] = 0.22, True
        links.append(link)
        seg_meta[seg.id] = {"pathIds": sorted(seg.path_ids), "actions": acts, "weight": weight,
                             "shared": len(seg.path_ids) > 1}
    _index_parallel_links(links)  # two segments between the SAME pair of points fan into arcs

    label_of_state = {n["stateKey"]: n["label"] for n in nodes if n.get("stateKey")}
    path_meta: dict[str, Any] = {}
    for p in scoped:
        m = metrics_by_path[p.path_id]
        segs = [s.id for s in bundled.segments_of(p.path_id)]
        path_meta[p.path_id] = {
            "actor": p.actor, "count": p.count, "segIds": segs,
            "actions": [labels.get(k, k) for k in p.actions],
            "source": p.source, "target": p.target,
            # the panel lists several paths that carry the SAME action; without the endpoints
            # they read as duplicated rows instead of as different transitions
            "sourceLabel": label_of_state.get(p.source, p.source),
            "targetLabel": label_of_state.get(p.target, p.target),
            "length": m.length, "observed": m.observed,
            "observedRatio": round(m.observed_ratio, 3), "support": m.support,
            "terminal": m.terminal, "roleDelta": m.role_delta,
            "strength": None if m.strength is None else round(m.strength, 1),
        }

    lengths: dict[str, int] = {}
    for p in scoped:
        lengths[str(len(p.actions))] = lengths.get(str(len(p.actions)), 0) + 1
    shared_actions = sum(len(s.actions) for s in bundled.segments if len(s.path_ids) > 1)
    total_actions = sum(len(s.actions) for s in bundled.segments)
    biggest = max(bundled.segments, key=lambda s: (len(s.path_ids), s.id), default=None)

    return {
        "gv": {"nodes": nodes, "links": links},
        "segMeta": seg_meta,
        "pathMeta": path_meta,
        "stats": {
            "paths": len(scoped),
            "segments": len(bundled.segments),
            "points": len(bundled.points),
            "branchPoints": sum(1 for p in bundled.points if p.kind in ("branch", "branch-merge")),
            "mergePoints": sum(1 for p in bundled.points if p.kind in ("merge", "branch-merge")),
            "statePoints": sum(1 for p in bundled.points if p.kind == "state"),
            "sharedActionPct": _pct(shared_actions, total_actions),
            "biggestTrunk": None if biggest is None else {
                "id": biggest.id, "paths": len(biggest.path_ids),
                "actions": [labels.get(k, k) for k in biggest.actions],
            },
            "lengths": lengths,
        },
    }


def _paths_and_metrics(agg: Aggregate, bundle: dict[str, Any]
                        ) -> tuple[list[RenderPath], dict[str, PathMetrics]]:
    """Layer 2 (``render_paths``) + Fase 3's per-path metrics (``analysis.path_metrics``), shared
    by variant 13's own view (``_paths_payloads``) and variant 14's collapsed-systems view
    (``_paths_systems_payloads``) — same paths, same metrics, only the DRAWING differs."""
    paths = render_paths(agg)
    ratings = _rating_of_bundle(bundle)
    block = block_for_family(None, load_markov_weights())  # the App has no ruleset -> `global`
    support: dict[tuple[str, str, str], int] = {}
    for key, v in agg.edges.items():
        rel = (key[0], key[1], key[3])
        support[rel] = support.get(rel, 0) + v["count"]

    metrics_by_path: dict[str, PathMetrics] = {}
    for i, key in enumerate(sorted(agg.edges)):
        rel = (key[0], key[1], key[3])
        metrics_by_path[f"p{i}"] = path_metrics(
            agg.edge_sample[key], support=support[rel],
            rating_of=lambda k: ratings.get(k), block=block)
    return paths, metrics_by_path


def _paths_payloads(agg: Aggregate, bundle: dict[str, Any]) -> dict[str, Any]:
    """Every (anchor structure × scope) combo variant 13 can show, precomputed. Structures are
    the owner's configurable frame; scopes are Global + one per detected system (the 11/12 pills,
    reused as a PREDICATE over paths)."""
    paths, metrics_by_path = _paths_and_metrics(agg, bundle)

    detected = _detect_systems(dict(agg.states), list(agg.edges.values()),
                               list(agg.handovers.values()))
    scopes: list[dict[str, Any]] = [{"id": _PATH_SCOPE_GLOBAL, "label": "Global", "members": None}]
    for s in detected["systems"]:
        scopes.append({"id": s["id"], "label": s["label"], "members": frozenset(s["members"])})

    pages: dict[str, Any] = {}
    for structure in sorted(_ANCHOR_STRUCTURES):
        for scope in scopes:
            pages[f"{structure}|{scope['id']}"] = _paths_view(
                agg, structure=structure, member_qids=scope["members"],
                metrics_by_path=metrics_by_path, paths=paths)
    return {
        "pages": pages,
        "structures": [{"id": k, "label": _ANCHOR_STRUCTURES[k]["label"]}
                        for k in sorted(_ANCHOR_STRUCTURES)],
        "scopes": [{"id": s["id"], "label": s["label"]} for s in scopes],
        "default": f"{_DEFAULT_ANCHOR_STRUCTURE}|{_PATH_SCOPE_GLOBAL}",
    }


# ── 2d. variant 14 — "Caminhos por sistema" (owner request, 2026-09-01) ────────────
#
# 13 with COLLAPSIBLE systems, in the 11/12 model: every detected system (same
# `_detect_systems` 11/12/13 already share) folds into one node (`_system_node`, reused
# verbatim) at the GLOBAL level — bridges/anchors/opponent states stay first-class (never
# folded, owner call: "pontes como nós de primeira classe"). A path is still a PATH: its
# `path_id` survives the fold, only its endpoint(s) that sit inside a collapsed system draw at
# the system's node instead of the member's own — a path fully swallowed by one system (both
# endpoints its members) draws nothing at the global level, it is the expansion's own job.
# Clicking a system node/pill "expands in place": that ONE system's members become real states
# again (13's own `flow_layout`, restricted to the touching paths), every outside touch reduced
# to 11/12's own compact stub (`_stub_node`) with a boundary ring on the member that crosses
# (`_system_boundary_view`'s own convention) — REUSED, not reimplemented; only the node/link
# assembly around them is new, because a path (segments) is a different shape than a two-sided
# graphview edge.


def _paths_scope_paths(paths: list[RenderPath], place_of: dict[str, str], focus: str | None,
                        members: frozenset[str] | None) -> list[RenderPath]:
    """Fold every path's endpoints through the system collapse: every system's members map to
    its own node EXCEPT `focus`'s own members, which stay real states (the system currently
    expanded in place). `members` (``focus``'s own member set, or ``None`` for the global page —
    no touch filter) restricts entry to paths that actually touch `focus` — a member never draws
    on a page it has nothing to do with just because it also touches something else. A path
    entirely swallowed by ONE collapsed system (both folded endpoints land on the same "sys:"
    place) draws nothing here — that is exactly what the expansion is for."""
    def fold(qid: str) -> str:
        place = place_of.get(qid, qid)
        return qid if (focus is not None and place == focus) else place

    out = []
    for p in paths:
        if members is not None and p.source not in members and p.target not in members:
            continue
        src, tgt = fold(p.source), fold(p.target)
        if src == tgt and src.startswith("sys:"):
            continue
        out.append(RenderPath(path_id=p.path_id, source=src, target=tgt,
                               actions=p.actions, actor=p.actor, count=p.count))
    return out


def _paths_systems_view(
    agg: Aggregate, *, structure: str, focus: str | None,
    metrics_by_path: dict[str, PathMetrics], paths: list[RenderPath],
    detected: dict[str, Any], place_of: dict[str, str],
    systems_by_id: dict[str, dict[str, Any]],
    excluded_by_qid: dict[str, tuple[tuple[str, str], dict[str, Any]]],
    bridge_qids: frozenset[str],
) -> dict[str, Any]:
    """One (anchor structure × focus) page — `_paths_view`'s own model (bundle -> `flow_layout`
    -> panel metrics), with systems COLLAPSED instead of hidden-as-stub. `focus=None` is the
    GLOBAL page (every system folded); `focus="sys:N"` is that system expanded in place, its
    members real, every outside touch a compact stub."""
    unified = bool(_ANCHOR_STRUCTURES[structure]["unified_finish"])
    opp_finish = _qid("partner", _FINISH_KEY)
    members = frozenset(systems_by_id[focus]["members"]) if focus is not None else None

    def _fold_unified(qid: str) -> str:
        return _FINISH_KEY if (unified and qid == opp_finish) else qid

    scoped = _paths_scope_paths(paths, place_of, focus, members)
    if unified:
        scoped = [
            RenderPath(path_id=p.path_id, source=_fold_unified(p.source),
                       target=_fold_unified(p.target), actions=p.actions,
                       actor=p.actor, count=p.count)
            for p in scoped
        ]
    bundled = bundle_paths(scoped)

    count_of = {p.path_id: p.count for p in scoped}
    actor_of = {p.path_id: p.actor for p in scoped}
    labels = _display_action_labels(agg)
    ghosts = _inferred_action_keys(agg)
    qid_to_state = {_qid(k[1], k[0]): (k, v) for k, v in agg.states.items()}

    anchor_slots: dict[str, str] = {}
    node_weight: dict[str, float] = {}
    for seg in bundled.segments:
        w = float(_segment_weight(seg, count_of))
        node_weight[seg.from_point] = node_weight.get(seg.from_point, 0.0) + w
        node_weight[seg.to_point] = node_weight.get(seg.to_point, 0.0) + w
    for pt in bundled.points:
        if pt.state_key is None or pt.state_key.startswith("sys:"):
            continue  # a junction or a collapsed system never sits on an anchor slot
        found = qid_to_state.get(pt.state_key)
        node_key = found[0][0] if found else pt.state_key
        actor = found[0][1] if found else "you"
        slot = _anchor_slot(node_key, actor, structure)
        if slot is not None:
            anchor_slots[pt.id] = slot

    pos = flow_layout(bundled, structure=structure, anchor_slots=anchor_slots, weight=node_weight)

    # Boundary marks (only meaningful once a system is expanded): a MEMBER point with a segment
    # reaching a non-member point is a crossing — `_system_boundary_view`'s own [->out <-in]
    # convention, counted here at the SEGMENT level (the visual object actually drawn) rather
    # than re-deriving it from the raw pre-bundle crossings.
    out_count: dict[str, int] = {}
    in_count: dict[str, int] = {}
    if focus is not None:
        assert members is not None
        for seg in bundled.segments:
            f_key, t_key = bundled.point(seg.from_point).state_key, bundled.point(seg.to_point).state_key
            f_member, t_member = f_key in members, t_key in members
            if f_member and not t_member:
                out_count[seg.from_point] = out_count.get(seg.from_point, 0) + 1
            if t_member and not f_member:
                in_count[seg.to_point] = in_count.get(seg.to_point, 0) + 1

    nodes: list[dict[str, Any]] = []
    for pt in sorted(bundled.points, key=lambda p: p.id):
        x, y = pos[pt.id]
        if pt.state_key is None:
            nodes.append({"id": pt.id, "label": "", "cat": "control", "size": 1,
                           "color": _JUNCTION_COLOR, "junction": True, "kind": pt.kind,
                           "x": x, "y": y, "pin": True})
            continue
        if pt.state_key.startswith("sys:"):
            node = dict(_system_node(systems_by_id[pt.state_key]))
            node["id"], node["sysId"] = pt.id, pt.state_key
            node["x"], node["y"], node["pin"] = x, y, True
            nodes.append(node)
            continue
        is_member = focus is not None and members is not None and pt.state_key in members
        if focus is not None and not is_member:
            # an outside touch while a system is expanded — always a bridge/anchor/opponent
            # state (a member of ANOTHER system already folded to "sys:" above), reduced to the
            # SAME compact stub 11/12 draws for exactly this situation.
            node = dict(_stub_node(pt.state_key, systems_by_id, excluded_by_qid, bridge_qids))
            node["id"] = pt.id
            node["x"], node["y"], node["pin"] = x, y, True
            nodes.append(node)
            continue
        found = qid_to_state.get(pt.state_key) or qid_to_state.get(opp_finish)
        if found is not None:
            (node_key, actor), v = found
        else:  # defensive only — every state point comes from a row `Aggregate` already holds
            node_key, actor = "", "you"
            v = {"label": pt.state_key, "type": "control", "count": 1}
        node = {
            "id": pt.id, "stateKey": pt.state_key, "kind": "state",
            "label": v["label"] if (unified and node_key == _FINISH_KEY)
                      else _finish_label(node_key, actor, v["label"]),
            "cat": _cat_of(v["type"]), "size": _clamp3(v["count"]),
            "fighter": _ACTOR_SIDE[actor], "x": x, "y": y, "pin": True,
        }
        _apply_finish_style(node, node_key, actor)
        _apply_start_style(node, node_key)
        if unified and node_key == _FINISH_KEY:
            node["split"] = [_FIG_HEX["a"], _FIG_HEX["b"]]
            node.pop("ring", None)
        o, i = out_count.get(pt.id, 0), in_count.get(pt.id, 0)
        if o or i:
            node["ring"] = _BOUNDARY_COLOR
            node["label"] = f"{node['label']} [→{o} ←{i}]"
        nodes.append(node)

    links: list[dict[str, Any]] = []
    seg_meta: dict[str, Any] = {}
    for seg in bundled.segments:
        acts = [labels.get(k, k) for k in seg.actions]
        weight = _segment_weight(seg, count_of)
        all_ghost = all(k in ghosts for k in seg.actions)
        fighters = {actor_of[p] for p in seg.path_ids}
        link = {
            "id": seg.id, "from": seg.from_point, "to": seg.to_point,
            "weight": _clamp3(weight), "arrow": True,
            "label": " → ".join(acts),
            "fighter": _ACTOR_SIDE[next(iter(sorted(fighters)))] if len(fighters) == 1 else "x",
        }
        if all_ghost:
            link["inf"] = True
            link["dash"] = [3, 4]
        if pos[seg.to_point][0] <= pos[seg.from_point][0]:
            link["bow"], link["back"] = 0.22, True
        links.append(link)
        seg_meta[seg.id] = {"pathIds": sorted(seg.path_ids), "actions": acts, "weight": weight,
                             "shared": len(seg.path_ids) > 1}
    _index_parallel_links(links)

    label_of_state = {n["stateKey"]: n["label"] for n in nodes if n.get("stateKey")}
    path_meta: dict[str, Any] = {}
    for p in scoped:
        m = metrics_by_path[p.path_id]
        segs = [s.id for s in bundled.segments_of(p.path_id)]
        path_meta[p.path_id] = {
            "actor": p.actor, "count": p.count, "segIds": segs,
            "actions": [labels.get(k, k) for k in p.actions],
            "source": p.source, "target": p.target,
            "sourceLabel": label_of_state.get(p.source, p.source),
            "targetLabel": label_of_state.get(p.target, p.target),
            "length": m.length, "observed": m.observed,
            "observedRatio": round(m.observed_ratio, 3), "support": m.support,
            "terminal": m.terminal, "roleDelta": m.role_delta,
            "strength": None if m.strength is None else round(m.strength, 1),
        }

    lengths: dict[str, int] = {}
    for p in scoped:
        lengths[str(len(p.actions))] = lengths.get(str(len(p.actions)), 0) + 1
    shared_actions = sum(len(s.actions) for s in bundled.segments if len(s.path_ids) > 1)
    total_actions = sum(len(s.actions) for s in bundled.segments)
    biggest = max(bundled.segments, key=lambda s: (len(s.path_ids), s.id), default=None)

    # Systems panel extra: how many DISTINCT systems each rendered path's own ORIGINAL (unfolded)
    # endpoints touch — 0, 1 or 2, never more (one path is exactly one state->state hop).
    orig_by_id = {p.path_id: p for p in paths}
    crossing = {"0": 0, "1": 0, "2": 0}
    for p in scoped:
        orig = orig_by_id[p.path_id]
        touched = len({s for s in (detected["system_of"].get(orig.source),
                                    detected["system_of"].get(orig.target)) if s is not None})
        crossing[str(touched)] = crossing.get(str(touched), 0) + 1

    return {
        "gv": {"nodes": nodes, "links": links},
        "segMeta": seg_meta,
        "pathMeta": path_meta,
        "stats": {
            "paths": len(scoped),
            "segments": len(bundled.segments),
            "points": len(bundled.points),
            "branchPoints": sum(1 for p in bundled.points if p.kind in ("branch", "branch-merge")),
            "mergePoints": sum(1 for p in bundled.points if p.kind in ("merge", "branch-merge")),
            "statePoints": sum(1 for p in bundled.points if p.kind == "state"),
            "sharedActionPct": _pct(shared_actions, total_actions),
            "biggestTrunk": None if biggest is None else {
                "id": biggest.id, "paths": len(biggest.path_ids),
                "actions": [labels.get(k, k) for k in biggest.actions],
            },
            "lengths": lengths,
            "systems": len(systems_by_id),
            "systemMembers": {sid: len(s["members"]) for sid, s in sorted(systems_by_id.items())},
            "crossingHistogram": crossing,
        },
    }


def _paths_systems_payloads(agg: Aggregate, bundle: dict[str, Any]) -> dict[str, Any]:
    """Variant 14's own payload builder — same (structure × scope) combinatorics as `_paths_payloads`
    (13's), so 14 reuses 13's exact scope-pill/structure-pill navigation chrome; only the page
    itself comes from `_paths_systems_view` (systems collapse/expand) instead of `_paths_view`
    (systems only ever hidden-as-stub)."""
    paths, metrics_by_path = _paths_and_metrics(agg, bundle)

    states = dict(agg.states)
    edges = list(agg.edges.values())
    handovers = list(agg.handovers.values())
    detected = _detect_systems(states, edges, handovers)
    systems = detected["systems"]
    bridge_qids = frozenset(detected["bridge_qids"])
    excluded = _excluded_states(states, detected["system_of"])
    place_of = _place_of(detected["system_of"], excluded)
    systems_by_id = {s["id"]: s for s in systems}
    excluded_by_qid = {_qid(k[1], k[0]): (k, v) for k, v in excluded.items()}

    scopes: list[dict[str, Any]] = [{"id": _PATH_SCOPE_GLOBAL, "label": "Global"}]
    scopes += [{"id": s["id"], "label": s["label"]} for s in systems]

    pages: dict[str, Any] = {}
    for structure in sorted(_ANCHOR_STRUCTURES):
        for scope in scopes:
            focus = None if scope["id"] == _PATH_SCOPE_GLOBAL else scope["id"]
            pages[f"{structure}|{scope['id']}"] = _paths_systems_view(
                agg, structure=structure, focus=focus, metrics_by_path=metrics_by_path,
                paths=paths, detected=detected, place_of=place_of, systems_by_id=systems_by_id,
                excluded_by_qid=excluded_by_qid, bridge_qids=bridge_qids)
    return {
        "pages": pages,
        "structures": [{"id": k, "label": _ANCHOR_STRUCTURES[k]["label"]}
                        for k in sorted(_ANCHOR_STRUCTURES)],
        "scopes": scopes,
        "default": f"{_DEFAULT_ANCHOR_STRUCTURE}|{_PATH_SCOPE_GLOBAL}",
        "systems": len(systems),
    }


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
GAGraph.mount(document.getElementById('cv'),{{mode:'map',nodes:GV.nodes,links:GV.links,pan:true,zoom:true,collide:true,bounded:false,charge:{charge},linkDist:{link_dist},gravity:{gravity},forceLabels: GV.nodes.length < 40}});
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
    bounded: false, charge: data.charge, linkDist: data.linkDist, gravity: data.gravity,
    forceLabels: data.gv.nodes.length < 40, regions: data.regions || [],
    onSelect: function (n) {
      if (!n) return;
      if (level === 'global' && n.id.indexOf('sys:') === 0) show('system', n.id);
    }
  });
}
show('global', null);
</script></body></html>"""

# Variant 9's in-place-expansion page — sibling of `_PAGE8`, own template for the same
# double-brace-escaping reason. Global view = one collapsed node per system (SYSTEMS); clicking
# one swaps it for its real member subgraph (MEMBERS[id]) while every OTHER system stays
# collapsed — several can be expanded at once, unlike 8's single-level drill-down. Inter-system
# edges (CROSS, member-granularity) are re-resolved to either the real member endpoint (its
# system is expanded) or the collapsed system node (it isn't) on every rebuild, then merged by
# resulting pair. Recollapse: a chip per expanded system in the side panel (`__LEGEND__` below
# explains why — a member node has no natural "go back" click target once its system is open,
# so the chip is the only recollapse control).
_PAGE9 = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
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
.sechead{font-size:11px;color:var(--ink2);margin:12px 0 6px;text-transform:uppercase;letter-spacing:.04em}
.sysrow{cursor:pointer}.sysrow:hover{border-color:#4d86ff}
</style></head><body>
<div id="canvas"><canvas id="cv"></canvas></div>
<div id="side">
<h1>9 — Systems, expand in place</h1>
<div class="muted" id="sideSubtitle"></div>
<div class="legend">__LEGEND__</div>
<div id="list"></div></div>
<script src="graph.js"></script>
<script>
const SYSTEMS = __SYSTEMS_JSON__;   // [{kind:'system',id,label,size,fighter} | {kind:'solo',node:{...,bridge?,bridgeConnects?}}]
const MEMBERS = __MEMBERS_JSON__;   // {sysId: {nodes, links, members, listHtml, subtitle}}
const CROSS = __CROSS_JSON__;       // [{from, to, count, dashed, fromSys, toSys}] — real qids (place_of)
const expanded = new Set();
let mounted = null;
let lastPositions = {};  // id -> {x,y} — snapshot across rebuilds, C.2 freeze-on-expand
function clamp3(n) { return n <= 1 ? 1 : (n === 2 ? 2 : 3); }  // mirrors Python's _clamp3
function freshCanvas() {
  const old = document.getElementById('cv');
  const next = old.cloneNode(false);
  old.parentNode.replaceChild(next, old);
  return next;
}
function knobsFor(n) {  // mirrors Python's _mount_knobs — same formula, JS-side because the
  const nn = Math.max(n, 1);   // assembled node count only exists once the user has expanded
  return {
    charge: Math.round(12000 + 300 * nn),
    linkDist: nn <= 25 ? 240 : Math.max(190, Math.round(240 - (nn - 25) * 0.9)),
    gravity: Math.round(0.0004 * (1 + nn / 100) * 1e5) / 1e5,
  };
}
function frozen(node) {  // C.2: re-seed at the last known position + pin, unless it's already
  const p = lastPositions[node.id];               // a server-pinned anchor (that never moves anyway)
  return (p && !node.pin) ? Object.assign({}, node, { x: p.x, y: p.y, pin: true }) : node;
}
function computeView(justExpandedId) {
  const nodes = [];
  const links = [];
  const regions = [];
  for (const s of SYSTEMS) {
    if (s.kind === 'solo') { nodes.push(frozen(s.node)); continue; }
    if (expanded.has(s.id)) {
      const m = MEMBERS[s.id];
      const freeze = s.id !== justExpandedId;  // the JUST-expanded system's own members settle fresh
      for (const n of m.nodes) nodes.push(freeze ? frozen(n) : n);
      for (const l of m.links) links.push(l);
      regions.push({ label: s.node.label, members: m.members });
    } else {
      nodes.push(frozen(s.node));
    }
  }
  const merged = new Map();
  for (const c of CROSS) {
    const from = expanded.has(c.fromSys) ? c.from : c.fromSys;
    const to = expanded.has(c.toSys) ? c.to : c.toSys;
    if (from === to) continue;
    const key = from + '|' + to;
    const row = merged.get(key) || { from, to, count: 0, dashed: true };
    row.count += c.count;
    row.dashed = row.dashed && c.dashed;
    merged.set(key, row);
  }
  for (const row of merged.values()) {
    links.push({ from: row.from, to: row.to, weight: clamp3(row.count), arrow: true, dashed: row.dashed });
  }
  return { nodes, links, regions };
}
function rebuild(justExpandedId) {
  const gv = computeView(justExpandedId);
  const list = document.getElementById('list');
  list.innerHTML = '';
  const systemRows = SYSTEMS.filter(function (s) { return s.kind === 'system'; });
  const bridgeRows = SYSTEMS.filter(function (s) { return s.kind === 'solo' && s.node.bridge; });

  const sysHead = document.createElement('div');
  sysHead.className = 'sechead';
  sysHead.textContent = 'Sistemas (' + systemRows.length + ')';
  list.appendChild(sysHead);
  for (const s of systemRows) {
    const isOpen = expanded.has(s.id);
    const row = document.createElement('div');
    row.className = 'row sysrow';
    row.innerHTML = (isOpen ? '▣ ' : '▢ ') + s.node.label +
      ' <span class="muted">(' + (isOpen ? 'expandido — clique p/ recolher' : 'clique p/ expandir') + ')</span>';
    row.onclick = function () {
      if (expanded.has(s.id)) { expanded.delete(s.id); rebuild(); }
      else { expanded.add(s.id); rebuild(s.id); }
    };
    list.appendChild(row);
    if (isOpen && MEMBERS[s.id] && MEMBERS[s.id].listHtml) {
      const sub = document.createElement('div');
      sub.style.marginLeft = '10px';
      sub.innerHTML = MEMBERS[s.id].listHtml;
      list.appendChild(sub);
    }
  }

  const bridgeHead = document.createElement('div');
  bridgeHead.className = 'sechead';
  bridgeHead.textContent = 'Pontes (' + bridgeRows.length + ')';
  list.appendChild(bridgeHead);
  for (const s of bridgeRows) {
    const row = document.createElement('div');
    row.className = 'row';
    const conn = (s.node.bridgeConnects || []).join(', ') || '—';
    row.innerHTML = s.node.label + ' <span class="muted">↔ ' + conn + '</span>';
    list.appendChild(row);
  }

  document.getElementById('sideSubtitle').textContent = expanded.size
    ? (expanded.size + ' sistema(s) expandido(s) de ' + systemRows.length)
    : (systemRows.length + ' sistemas, todos recolhidos');
  if (mounted) {
    if (mounted.positions) lastPositions = Object.assign({}, lastPositions, mounted.positions());
    if (mounted.destroy) mounted.destroy();
  }
  const cv = freshCanvas();
  const knobs = knobsFor(gv.nodes.length);
  mounted = GAGraph.mount(cv, {
    mode: 'map', nodes: gv.nodes, links: gv.links, pan: true, zoom: true, collide: true,
    bounded: false, charge: knobs.charge, linkDist: knobs.linkDist, gravity: knobs.gravity,
    forceLabels: gv.nodes.length < 40, regions: gv.regions,
    onSelect: function (n) {
      if (!n) return;
      if (n.id.indexOf('sys:') === 0) { expanded.add(n.id); rebuild(n.id); }
    }
  });
}
rebuild();
</script></body></html>"""

# Variant 10 (owner adendo, density-gate experiment) — the SAME collapsed systems-level view as
# variant 8's global level, side by side across the 4 inference policies at the chosen
# min_support — lets the owner SEE the gate's effect instead of trusting a number in a report.
# Button toggle, not tabs/iframes: cheapest thing that lets one canvas swap between precomputed
# levels, same pattern as `_PAGE8`'s own `show()`.
_PAGE10 = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>__TITLE__</title>
<style>
:root{--bg:#0b0b0f;--panel:#14141a;--line:#26262e;--ink:#e9e9ee;--ink2:#9a9aa6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif;display:flex;height:100vh}
#canvas{flex:1;position:relative}#canvas canvas{width:100%;height:100%;display:block}
#side{width:360px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px}
h1{font-size:15px;margin:0 0 4px}.muted{color:var(--ink2);font-size:12px;margin-bottom:10px}
.row{padding:6px 8px;border:1px solid var(--line);border-radius:8px;margin-bottom:5px;font-size:12px}
.legend{font-size:11px;color:var(--ink2);margin:10px 0;line-height:1.6}
.btns{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
.btn{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px 10px;cursor:pointer;font:12px system-ui;text-align:left}
.btn.active{border-color:#4d86ff;background:#1a1f2e}
</style></head><body>
<div id="canvas"><canvas id="cv"></canvas></div>
<div id="side">
<h1>__TITLE__</h1>
<div class="legend">__LEGEND__</div>
<div class="btns" id="btns"></div>
<div id="list"></div></div>
<script src="graph.js"></script>
<script>
const LEVELS = __LEVELS_JSON__;      // {policyKey: {title, subtitle, gv, charge, linkDist, gravity}}
const POLICIES = __POLICIES_JSON__;  // [{key, label}] in a fixed, documented order
let mounted = null;
function show(key) {
  const data = LEVELS[key];
  document.getElementById('list').innerHTML =
    '<div class="row"><b>' + data.title + '</b><br/><span class="muted">' + data.subtitle + '</span></div>';
  const btns = document.getElementById('btns');
  for (const b of btns.children) b.className = 'btn' + (b.dataset.key === key ? ' active' : '');
  if (mounted && mounted.destroy) mounted.destroy();
  const old = document.getElementById('cv');
  const cv = old.cloneNode(false);
  old.parentNode.replaceChild(cv, old);
  mounted = GAGraph.mount(cv, {
    mode: 'map', nodes: data.gv.nodes, links: data.gv.links, pan: true, zoom: true, collide: true,
    bounded: false, charge: data.charge, linkDist: data.linkDist, gravity: data.gravity,
    forceLabels: data.gv.nodes.length < 40,
  });
}
const btns = document.getElementById('btns');
for (const p of POLICIES) {
  const b = document.createElement('button');
  b.className = 'btn';
  b.dataset.key = p.key;
  b.textContent = p.label;
  b.onclick = function () { show(p.key); };
  btns.appendChild(b);
}
show(POLICIES[0].key);
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


def _gated_systems(states4: dict[tuple[str, str], dict[str, Any]], edges4: list[dict[str, Any]], handovers4: list[dict[str, Any]]
                    ) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any],
                               frozenset[str], dict[tuple[str, str], dict[str, Any]]]:
    """Shared by variants 8/9: gate (owner adendo, `_GATE_MIN_SUPPORT_DEFAULT`/
    `_GATE_POLICY_DEFAULT`) THEN detect systems on the cleaned graph THEN truncate the bridge
    set to the ones the VIEW shows (top-N by strength) — dominance runs on the gated graph, the
    display cut runs on top of dominance's honest answer, never instead of it. Returns
    ``(g_states, g_edges, g_handovers, detected, displayed_bridge_qids, excluded)`` where
    ``excluded`` already has the non-displayed bridges removed."""
    g_states, g_edges, g_handovers = _apply_gate(
        states4, edges4, handovers4,
        min_support=_GATE_MIN_SUPPORT_DEFAULT, inference_policy=_GATE_POLICY_DEFAULT)
    detected = _detect_systems(g_states, g_edges, g_handovers)
    all_bridges = frozenset(detected["bridge_qids"])
    displayed = _select_displayed_bridges(detected["bridge_strength"])
    hidden = all_bridges - displayed
    excluded = _excluded_states(g_states, detected["system_of"])
    excluded = {k: v for k, v in excluded.items() if _qid(k[1], k[0]) not in hidden}
    return g_states, g_edges, g_handovers, detected, displayed, excluded


def _render_variant8(out: Path, states4: dict[tuple[str, str], dict[str, Any]], edges4: list[dict[str, Any]],
                      handovers4: list[dict[str, Any]]) -> dict[str, Any]:
    g_states, g_edges, g_handovers, detected, bridge_qids, excluded = _gated_systems(
        states4, edges4, handovers4)
    systems, system_of = detected["systems"], detected["system_of"]
    all_bridges = len(detected["bridge_qids"])
    place_of = _place_of(system_of, excluded)
    cross_links = _cross_system_links(place_of, g_edges, g_handovers)
    radius = _anchor_radius(len(g_states))
    level1_gv = _systems_level1_view(systems, excluded, cross_links, bridge_qids, radius)
    level1_knobs = _mount_knobs(len(level1_gv["nodes"]))
    level1_list = "".join(
        f'<div class="row">{s["label"]} <span class="muted">'
        f'({len(s["members"])} nos, {s["actor"]})</span></div>'
        for s in systems
    )
    level1 = {"title": "8 — Systems map (global)",
              "subtitle": f"{len(systems)} systems, {len(bridge_qids)}/{all_bridges} bridges shown — "
                          f"dominance over the gated graph (min_support={_GATE_MIN_SUPPORT_DEFAULT}, "
                          f"{_GATE_POLICY_LABELS[_GATE_POLICY_DEFAULT]})",
              "listHtml": level1_list, "gv": level1_gv, "regions": [], **level1_knobs}

    level2: dict[str, Any] = {}
    for s in systems:
        gv2, internal_edges, regions2 = _system_level2(s, g_states, g_edges, g_handovers, excluded,
                                                         bridge_qids, radius)
        id_to_label = {n["id"]: n["label"] for n in gv2["nodes"]}
        list2 = _edge_list_html(internal_edges, id_to_label)
        knobs2 = _mount_knobs(len(gv2["nodes"]))
        level2[s["id"]] = {
            "title": s["label"],
            "subtitle": f"{len(s['members'])} nodes, {s['actor']}-dominant — grey nodes are "
                        "bridges/opponent/anchors, never part of this system",
            "listHtml": list2, "gv": gv2, "regions": regions2, **knobs2,
        }

    html = (
        _PAGE8.replace("__TITLE__", "8 — Collapsible systems")
        .replace(
            "__LEGEND__",
            "click a system node to drill in; grey nodes are the STRONGEST BRIDGES (dominance found "
            "more, only the top few show — see subtitle) or the opponent/finish/start anchors, never "
            "collapsed. Inside a system: same convention as variant 4 (blue=you, orange=opponent), "
            "the dashed region outline marks this system's own members.",
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
        "partner_elements": sum(1 for k in excluded if k[1] == "partner"),
        "handover_links": sum(1 for link in cross_links if link.get("dashed")),
        "systems": len(systems),
        "bridges": all_bridges,
        "bridges_shown": len(bridge_qids),
        "knobs": level1_knobs,
    }


def _render_variant9(out: Path, states4: dict[tuple[str, str], dict[str, Any]], edges4: list[dict[str, Any]],
                      handovers4: list[dict[str, Any]]) -> dict[str, Any]:
    """Variant 9 — same systems as variant 8, but expansion happens IN PLACE: the global view
    shows every system as a node PLUS every bridge/opponent/anchor individually (always
    visible, never collapsed — C, owner whiteboard); expanding a system swaps its collapsed
    node for its real member subgraph while every OTHER system stays collapsed (several can be
    open at once). Inter-placement edges (CROSS, member granularity, built over the SAME
    ``_place_of`` extension as variant 8 — bridges self-mapped, never a direct system-to-system
    pair) re-resolve each endpoint to either the real member (its system is expanded) or the
    collapsed system node (it isn't) on every rebuild (`computeView` in `_PAGE9`), which ALSO
    freezes (pins) every node that isn't part of the just-expanded system at its last known
    position (`positions()`/``n.pin`` — C.2, no more "explodes on every click"). Metrics mirror
    variant 8's shape (same fields, computed over the fully-collapsed default view, for a
    stable/deterministic number)."""
    g_states, g_edges, g_handovers, detected, bridge_qids, excluded = _gated_systems(
        states4, edges4, handovers4)
    systems, system_of = detected["systems"], detected["system_of"]
    all_bridges = len(detected["bridge_qids"])
    place_of = _place_of(system_of, excluded)
    cross_links = _cross_system_links(place_of, g_edges, g_handovers)  # metrics parity w/ variant 8
    radius = _anchor_radius(len(g_states))
    level1_gv = _systems_level1_view(systems, excluded, cross_links, bridge_qids, radius)
    level1_knobs = _mount_knobs(len(level1_gv["nodes"]))

    # Owner adendo (side panel, item 3): each DISPLAYED bridge's own connected systems, so the
    # panel can say "with which systems it connects" — derived from the same system-granularity
    # cross_links used for metrics, never recomputed.
    sys_label_of = {s["id"]: s["label"] for s in systems}
    bridge_connects: dict[str, list[str]] = {}
    for c in cross_links:
        u, v = c["from"], c["to"]
        if u in bridge_qids and v in sys_label_of:
            bridge_connects.setdefault(u, []).append(sys_label_of[v])
        if v in bridge_qids and u in sys_label_of:
            bridge_connects.setdefault(v, []).append(sys_label_of[u])
    bridge_connects = {qid: sorted(set(labels)) for qid, labels in bridge_connects.items()}

    sys_payload = [{"kind": "system", "id": s["id"], "node": _system_node(s)} for s in systems]
    for k, v in sorted(
        excluded.items(),
        key=lambda kv: (_excluded_render_rank(kv[0][0], _qid(kv[0][1], kv[0][0]) in bridge_qids),
                         _qid(kv[0][1], kv[0][0])),
    ):
        qid = _qid(k[1], k[0])
        node = _style_single_node(k[0], k[1], v, radius=radius, is_bridge=qid in bridge_qids)
        if qid in bridge_qids:
            node["bridgeConnects"] = bridge_connects.get(qid, [])
        sys_payload.append({"kind": "solo", "node": node})

    members: dict[str, Any] = {}
    for s in systems:
        sub_states, internal_edges, internal_handovers = _system_members(s, g_states, g_edges, g_handovers)
        gv = _two_sided_graphview(sub_states, internal_edges, internal_handovers)
        id_to_label = {n["id"]: n["label"] for n in gv["nodes"]}
        members[s["id"]] = {
            "nodes": gv["nodes"], "links": gv["links"], "members": s["members"],
            "listHtml": _edge_list_html(internal_edges, id_to_label),
            "subtitle": f"{len(s['members'])} nos, {s['actor']}-dominant",
        }

    cross = _cross_member_links(place_of, g_edges, g_handovers)

    html = (
        _PAGE9.replace("__TITLE__", "9 — Systems, expand in place")
        .replace(
            "__LEGEND__",
            "click a system node to expand it in place — its real nodes/edges replace it while "
            "every other system stays collapsed (multiple can be open at once); grey nodes are the "
            "STRONGEST BRIDGES (only the top few of what dominance found — see the side panel) or "
            "the opponent/finish/start anchors — always visible, never collapsed. Everything not "
            "just-expanded stays pinned in place (no reshuffle on click). Click a system row in the "
            "list, or its node, to expand/recolher — same control either way.",
        )
        .replace("__SYSTEMS_JSON__", json.dumps(sys_payload, ensure_ascii=False))
        .replace("__MEMBERS_JSON__", json.dumps(members, ensure_ascii=False))
        .replace("__CROSS_JSON__", json.dumps(cross, ensure_ascii=False))
    )
    (out / "9-sistemas-expande-in-place.html").write_text(html, encoding="utf-8")

    return {
        "nodes": len(level1_gv["nodes"]), "edges": len(level1_gv["links"]),
        "edges_per_node": round(len(level1_gv["links"]) / len(level1_gv["nodes"]), 2)
        if level1_gv["nodes"] else 0.0,
        "pct_inferred_edges": None,
        "partner_elements": sum(1 for k in excluded if k[1] == "partner"),
        "handover_links": sum(1 for link in cross_links if link.get("dashed")),
        "systems": len(systems),
        "bridges": all_bridges,
        "bridges_shown": len(bridge_qids),
        "knobs": level1_knobs,
    }


def _render_variant10(out: Path, states4: dict[tuple[str, str], dict[str, Any]], edges4: list[dict[str, Any]],
                       handovers4: list[dict[str, Any]]) -> dict[str, Any]:
    """Owner adendo — the density-gate EXPERIMENT made visible: the collapsed systems-level view
    (same shape as variant 8's global level), rendered once per inference policy at the chosen
    ``_GATE_MIN_SUPPORT_DEFAULT``, switchable by button. Returns one metrics row per policy
    (nodes/edges/systems/bridges) — the same numbers `sweep_gates` reports for this min_support,
    kept in the artifact so the choice stays checkable without re-running the sweep."""
    radius = _anchor_radius(len(states4))
    levels: dict[str, Any] = {}
    row_metrics: dict[str, Any] = {}
    for policy in _GATE_POLICIES:
        g_states, g_edges, g_handovers = _apply_gate(
            states4, edges4, handovers4,
            min_support=_GATE_MIN_SUPPORT_DEFAULT, inference_policy=policy)
        detected = _detect_systems(g_states, g_edges, g_handovers)
        systems, system_of = detected["systems"], detected["system_of"]
        bridge_qids = frozenset(detected["bridge_qids"])
        excluded = _excluded_states(g_states, system_of)
        place_of = _place_of(system_of, excluded)
        cross_links = _cross_system_links(place_of, g_edges, g_handovers)
        gv = _systems_level1_view(systems, excluded, cross_links, bridge_qids, radius)
        knobs = _mount_knobs(len(gv["nodes"]))
        sizes = sorted((len(s["members"]) for s in systems), reverse=True)
        levels[policy] = {
            "title": f"min_support={_GATE_MIN_SUPPORT_DEFAULT} · {_GATE_POLICY_LABELS[policy]}",
            "subtitle": f"{len(systems)} sistemas {sizes}, {len(bridge_qids)} pontes, "
                        f"{len(g_edges)} edges de ação sobreviventes",
            "gv": gv, **knobs,
        }
        row_metrics[policy] = {"nodes": len(gv["nodes"]), "edges": len(gv["links"]),
                                "systems": len(systems), "system_sizes": sizes,
                                "bridges": len(bridge_qids)}

    html = (
        _PAGE10.replace("__TITLE__", "10 — Gating comparado")
        .replace(
            "__LEGEND__",
            f"mesmo min_support ({_GATE_MIN_SUPPORT_DEFAULT}, precedente: App's "
            "DEFAULT_MIN_EDGE_SUPPORT) nos 4 botões — só a política de inferência muda. Escolha "
            f"padrão de 8/9: {_GATE_POLICY_LABELS[_GATE_POLICY_DEFAULT]!r}.",
        )
        .replace("__LEVELS_JSON__", json.dumps(levels, ensure_ascii=False))
        .replace("__POLICIES_JSON__", json.dumps(
            [{"key": p, "label": _GATE_POLICY_LABELS[p]} for p in _GATE_POLICIES], ensure_ascii=False))
    )
    (out / "10-gating-comparado.html").write_text(html, encoding="utf-8")
    return row_metrics


# 11/12 (Frente 2) — shared template: pills + system-node click + stub-node click all write the
# SAME `view` state ('global' | sysId); `Global` is the real default (App's own "Global chip
# shows another graph" quirk deliberately NOT copied). Canvas recreated every `show()`/`rebuild()`
# (same anti-leak pattern as `_PAGE8`/`_PAGE9`). `__CONTROLS_CLASS__` is the ONLY difference
# between A and B — B's own combo/bridge/type/flow controls, hidden (not removed) on A, which is
# permanently locked to `_DEFAULT_SYSTEMS_COMBO`.
_PAGE_SYSTEMS = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>__TITLE__</title>
<style>
:root{--bg:#0b0b0f;--panel:#14141a;--line:#26262e;--ink:#e9e9ee;--ink2:#9a9aa6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif;display:flex;height:100vh}
#canvas{flex:1;position:relative}#canvas canvas{width:100%;height:100%;display:block}
#side{width:380px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px}
h1{font-size:15px;margin:0 0 4px}.muted{color:var(--ink2);font-size:12px;margin-bottom:10px}
.row{padding:6px 8px;border:1px solid var(--line);border-radius:8px;margin-bottom:5px;font-size:12px}
.g{opacity:.45;border-style:dashed}
.legend{font-size:11px;color:var(--ink2);margin:10px 0;line-height:1.6}
.pills{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.pill{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:4px 10px;cursor:pointer;font:11px system-ui}
.pill.active{border-color:#4d86ff;background:#1a1f2e}
.controls{border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:12px;font-size:12px}
.controls.hidden{display:none}
.controls label{display:block;margin-bottom:8px}
.controls select,.controls input[type=range]{width:100%}
.controls .tchk{display:inline-block;margin:0 8px 4px 0}
.members{margin-top:4px}
.chip{display:inline-block;background:var(--bg);color:var(--ink2);border:1px solid var(--line);border-radius:999px;padding:2px 8px;margin:2px 4px 0 0;cursor:pointer;font:11px system-ui}
.chip:hover{color:var(--ink);border-color:#4d86ff}
</style></head><body>
<div id="canvas"><canvas id="cv"></canvas></div>
<div id="side">
<h1 id="sideTitle">__TITLE__</h1><div class="muted" id="sideSubtitle"></div>
<div class="legend">Navegue pelas pills, clicando num nó de sistema (anel duplo), ou num mini-nó
pontilhado (stub — um por destino, não por travessia); os três controles escrevem o mesmo estado.
Losango = âncora de início; anel duplo = sistema colapsado; anel rosa = nó de fronteira, com
<code>[→saídas ←entradas]</code> no rótulo. Cor da faixa (azul) = você, incluindo as pontes;
laranja = oponente; amarelo = finalização. Sólido = ação
observada/inferida; tracejado longo cinza <code>[5,5]</code> = handover; pontilhado curto
<code>[2,3]</code> terminando num mini-nó = stub de fronteira. Seta: SEMPRE em ação/handover/stub
(estrutural); só no nível agregado (sistema↔sistema/ponte/âncora) a seta vem de
<code>edge_arrow</code> (pode sumir se o volume for parelho/esparso demais). Filtro de tipo é
DISPLAY-ONLY — não recalcula sistemas nem pontes (ponytail: recalcular exigiria portar a
detecção de sistemas para o cliente; troque o combo para isso).</div>
<div class="controls__CONTROLS_CLASS__" id="controls">
  <label>Política de inferência<select id="selPolicy"></select></label>
  <label>Suporte mínimo<select id="selSupport"></select></label>
  <label>Oponente<select id="selOpponent"></select></label>
  <label>Pontes exibidas: <span id="bridgeCountLabel"></span>
    <input type="range" id="bridgeSlider" min="0" max="0" value="0"/></label>
  <label>Viés de fluxo: <span id="flowLabel">2</span>
    <input type="range" id="flowSlider" min="0" max="3" step="0.5" value="2"/></label>
  <div id="typeChecks"></div>
</div>
<div class="pills" id="pills"></div>
<div class="row" id="summary"></div>
<div id="list"></div></div>
<script src="graph.js"></script>
<script>
const COMBOS = __COMBOS_JSON__;          // {comboKey: {global, systems:{sysId:...}, meta}}
const COMBO_OPTIONS = __COMBO_OPTIONS_JSON__;
const OPPONENT_LABELS = __OPPONENT_MODE_LABELS_JSON__;
const POLICY_LABELS = __POLICY_LABELS_JSON__;
const DEFAULT_KEY = __DEFAULT_KEY__;
const WIDEST = __WIDEST_JSON__;          // {key, nodes, edges} — the "vs" reference, always the widest combo
let comboKey = DEFAULT_KEY;
let view = 'global';
let hiddenTypes = new Set();
let flowBias = 2;   // measured: at 0 the monotonicity read is noise (38-88% across reloads of the SAME view), at 2 it settles to 75-88%
let bridgeCount = 0;
let mounted = null;

function freshCanvas() {
  const old = document.getElementById('cv');
  const next = old.cloneNode(false);
  old.parentNode.replaceChild(next, old);
  return next;
}
function currentPageData() {
  const data = COMBOS[comboKey];
  return view === 'global' ? data.global : data.systems[view];
}
function bridgeNodesOf(data) { return data.global.gv.nodes.filter(function (n) { return n.bridgeRank !== undefined; }); }
function applyFilters(pageData) {
  const nodes = pageData.gv.nodes.filter(function (n) {
    if (n.bridgeRank !== undefined && n.bridgeRank >= bridgeCount) return false;
    if (hiddenTypes.has(n.cat) && !n.system && !n.pin && !n.stub) return false;
    return true;
  });
  const keep = new Set(nodes.map(function (n) { return n.id; }));
  const links = pageData.gv.links.filter(function (l) {
    if (!keep.has(l.from) || !keep.has(l.to)) return false;
    if (l.at && hiddenTypes.has(l.at)) return false;
    return true;
  });
  return { nodes: nodes, links: links };
}
function flowPct(gv, positions) {  // % of ACTION edges (l.at set) whose target.x > source.x
  let total = 0, mono = 0;
  for (const l of gv.links) {
    if (!l.at) continue;
    const s = positions[l.from], t = positions[l.to];
    if (!s || !t) continue;
    total++;
    if (t.x > s.x) mono++;
  }
  return total ? Math.round(100 * mono / total) : null;
}
function renderSummary(data, filtered) {
  const meta = data.meta;
  const bridgesShown = bridgeNodesOf(data).filter(function (n) { return n.bridgeRank < bridgeCount; }).length;
  document.getElementById('summary').innerHTML =
    '<b>' + POLICY_LABELS[meta.inference_policy] + '</b>, suporte&ge;' + meta.min_support + ', '
    + OPPONENT_LABELS[meta.opponent_mode] + '<br/>'
    + meta.systems + ' sistema(s) ' + JSON.stringify(meta.system_sizes) + ', '
    + bridgesShown + '/' + meta.bridges + ' pontes exibidas<br/>'
    + filtered.nodes.length + ' nos / ' + filtered.links.length + ' edges (vs '
    + WIDEST.nodes + '/' + WIDEST.edges + ' no combo mais permissivo — ' + WIDEST.key + ')';
}
function rebuild() {
  const data = COMBOS[comboKey];
  const pageData = currentPageData();
  const filtered = applyFilters(pageData);

  document.getElementById('sideTitle').textContent = pageData.title;
  document.getElementById('sideSubtitle').textContent = pageData.subtitle;
  document.getElementById('list').innerHTML = pageData.listHtml || '';

  const pills = document.getElementById('pills');
  pills.innerHTML = '';
  const gPill = document.createElement('div');
  gPill.className = 'pill' + (view === 'global' ? ' active' : '');
  gPill.textContent = 'Global';
  gPill.onclick = function () { view = 'global'; rebuild(); };
  pills.appendChild(gPill);
  for (const sysId of Object.keys(data.systems)) {
    const p = document.createElement('div');
    p.className = 'pill' + (view === sysId ? ' active' : '');
    p.textContent = data.systems[sysId].title;
    p.onclick = (function (id) { return function () { view = id; rebuild(); }; })(sysId);
    pills.appendChild(p);
  }

  if (mounted && mounted.destroy) mounted.destroy();
  const cv = freshCanvas();
  mounted = GAGraph.mount(cv, {
    mode: 'map', nodes: filtered.nodes, links: filtered.links, pan: true, zoom: true, collide: true,
    bounded: false, charge: pageData.charge, linkDist: pageData.linkDist, gravity: pageData.gravity,
    forceLabels: filtered.nodes.length < 40, regions: pageData.regions || [], flowBias: flowBias,
    onSelect: function (n) {
      if (!n) return;
      if (String(n.id).indexOf('sys:') === 0) { view = n.id; rebuild(); }
    }
  });
  renderSummary(data, filtered);
  setTimeout(function () {
    if (!mounted || !mounted.positions) return;
    const pct = flowPct(filtered, mounted.positions());
    if (pct !== null) {
      document.getElementById('summary').innerHTML += '<br/>fluxo (alvo.x&gt;origem.x): ' + pct + '% das ações';
    }
  }, 1500);
}

// owner adendo (2026-08-27, second pass): a member chip inside the global systems list opens
// its own system and highlights that node — `#list`'s own DIV survives every rebuild() (only
// its innerHTML is replaced), so ONE delegated listener here keeps working across reloads.
document.getElementById('list').addEventListener('click', function (e) {
  const chip = e.target.closest('.chip');
  if (!chip) return;
  view = chip.getAttribute('data-sys');
  const qid = chip.getAttribute('data-qid');
  rebuild();
  if (mounted && mounted.select) mounted.select(qid);
});

function resetControlsForCombo() {
  const bridges = bridgeNodesOf(COMBOS[comboKey]);
  bridgeCount = bridges.length;
  const slider = document.getElementById('bridgeSlider');
  slider.max = bridges.length; slider.value = bridges.length;
  document.getElementById('bridgeCountLabel').textContent = bridges.length + '/' + bridges.length;
}

const selPolicy = document.getElementById('selPolicy');
const selSupport = document.getElementById('selSupport');
const selOpponent = document.getElementById('selOpponent');
function uniq(arr) { return arr.filter(function (v, i) { return arr.indexOf(v) === i; }); }
for (const p of uniq(COMBO_OPTIONS.map(function (o) { return o.policy; }))) {
  const o = document.createElement('option'); o.value = p; o.textContent = POLICY_LABELS[p]; selPolicy.appendChild(o);
}
for (const s of uniq(COMBO_OPTIONS.map(function (o) { return o.minSupport; }))) {
  const o = document.createElement('option'); o.value = s; o.textContent = s; selSupport.appendChild(o);
}
for (const om of uniq(COMBO_OPTIONS.map(function (o) { return o.opponentMode; }))) {
  const o = document.createElement('option'); o.value = om; o.textContent = OPPONENT_LABELS[om]; selOpponent.appendChild(o);
}
(function () {
  const parts = DEFAULT_KEY.split('|');
  selOpponent.value = parts[0]; selSupport.value = parts[1]; selPolicy.value = parts[2];
})();
function onComboChange() {
  comboKey = selOpponent.value + '|' + selSupport.value + '|' + selPolicy.value;
  view = 'global';  // trocar de combo reseta view (sys:N não é estável entre combos)
  resetControlsForCombo();
  rebuild();
}
selPolicy.onchange = onComboChange;
selSupport.onchange = onComboChange;
selOpponent.onchange = onComboChange;
document.getElementById('bridgeSlider').oninput = function () {
  bridgeCount = Number(this.value);
  document.getElementById('bridgeCountLabel').textContent = bridgeCount + '/' + bridgeNodesOf(COMBOS[comboKey]).length;
  rebuild();
};
document.getElementById('flowSlider').oninput = function () {
  flowBias = Number(this.value);
  document.getElementById('flowLabel').textContent = flowBias;
  rebuild();
};
const ALL_TYPES = ['guard', 'pass', 'sweep', 'takedown', 'control', 'submission', 'escape', 'transition'];
const typeChecks = document.getElementById('typeChecks');
for (const ty of ALL_TYPES) {
  const label = document.createElement('label');
  label.className = 'tchk';
  const cb = document.createElement('input');
  cb.type = 'checkbox'; cb.checked = true;
  cb.onchange = (function (t, box) { return function () {
    if (box.checked) hiddenTypes.delete(t); else hiddenTypes.add(t);
    rebuild();
  }; })(ty, cb);
  label.appendChild(cb);
  label.appendChild(document.createTextNode(' ' + ty));
  typeChecks.appendChild(label);
}

resetControlsForCombo();
rebuild();
</script></body></html>"""


def _render_systems_page(out: Path, filename: str, title: str, agg: Aggregate,
                          combos: tuple[tuple[str, int, str], ...], *,
                          default_combo: tuple[str, int, str], controls: bool) -> dict[str, Any]:
    """Frente 2 §2.1: ONE function, two wrappers. A = B with the controls hidden and permanently
    locked to `default_combo` (only that combo is even precomputed for A — cheap, and the reason
    A's own payload is byte-identical to B's payload at that same combo key, never two different
    code paths producing "the same" graph)."""
    payloads = {_combo_key(*c): _combo_payload(agg, *c) for c in combos}
    default_key = _combo_key(*default_combo)
    # The "vs" line compares against the WIDEST combo, never against whatever happens to be the
    # default — once the default became a gated criterion, comparing to itself made the line
    # meaningless. A only precomputes one combo, so the reference travels as bare counts.
    widest_key = _combo_key(*_WIDEST_SYSTEMS_COMBO)
    widest_meta = payloads.get(widest_key, {}).get("meta") or _combo_payload(
        agg, *_WIDEST_SYSTEMS_COMBO)["meta"]
    widest = {"key": widest_key, "nodes": widest_meta["nodes"], "edges": widest_meta["edges"],
              "artefacts": widest_meta["nodes"] + widest_meta["edges"]}
    combos_json = json.dumps(payloads, ensure_ascii=False)
    payload_bytes = len(combos_json.encode("utf-8"))

    combo_options = [{"key": _combo_key(*c), "opponentMode": c[0], "minSupport": c[1], "policy": c[2]}
                      for c in combos]
    html = (
        _PAGE_SYSTEMS
        .replace("__TITLE__", title)
        .replace("__CONTROLS_CLASS__", "" if controls else " hidden")
        .replace("__DEFAULT_KEY__", json.dumps(default_key))
        .replace("__WIDEST_JSON__", json.dumps(widest, ensure_ascii=False))
        .replace("__COMBOS_JSON__", combos_json)
        .replace("__COMBO_OPTIONS_JSON__", json.dumps(combo_options, ensure_ascii=False))
        .replace("__OPPONENT_MODE_LABELS_JSON__", json.dumps(_OPPONENT_MODE_LABELS, ensure_ascii=False))
        .replace("__POLICY_LABELS_JSON__", json.dumps(_GATE_POLICY_LABELS, ensure_ascii=False))
    )
    (out / filename).write_text(html, encoding="utf-8")

    default_payload = payloads[default_key]
    handover_links = sum(1 for link in default_payload["global"]["gv"]["links"] if link.get("dashed"))
    return {
        "nodes": default_payload["meta"]["nodes"], "edges": default_payload["meta"]["edges"],
        "handover_links": handover_links,
        "systems": default_payload["meta"]["systems"], "bridges": default_payload["meta"]["bridges"],
        "combos": len(combos), "payload_bytes": payload_bytes,
        "knobs": {"charge": default_payload["global"]["charge"], "linkDist": default_payload["global"]["linkDist"],
                  "gravity": default_payload["global"]["gravity"]},
    }


def _adaptive_default(agg: Aggregate) -> tuple[str, int, str]:
    """The gate this graph should OPEN on, measured from its own ungated size."""
    widest = _combo_payload(agg, *_WIDEST_SYSTEMS_COMBO)["meta"]
    return adaptive_combo(widest["nodes"] + widest["edges"])


def _render_variant11(out: Path, agg: Aggregate) -> dict[str, Any]:
    default = _adaptive_default(agg)
    combos = (default,) if default == _WIDEST_SYSTEMS_COMBO else (default, _WIDEST_SYSTEMS_COMBO)
    return _render_systems_page(
        out, "11-sistemas-vista-separada.html", "11 — Sistemas, vista separada", agg,
        combos, default_combo=default, controls=False)


def _render_variant12(out: Path, agg: Aggregate) -> dict[str, Any]:
    return _render_systems_page(
        out, "12-sistemas-vista-separada-seletiva.html", "12 — Sistemas, vista separada (seletiva)", agg,
        _ALL_SYSTEMS_COMBOS, default_combo=_adaptive_default(agg), controls=True)


# Variant 13 — "Caminhos". Own template (same double-brace reason as `_PAGE8`/`_PAGE9`): the
# page swaps between precomputed (anchor structure × scope) payloads, and drives ONE selection
# model across three entry points — a path row in the panel, a state node, or a shared segment.
# Everything it draws is already positioned server-side (`flow_layout`), so `GAGraph.mount` runs
# as a static painter with pan/zoom: no physics, no reshuffle between renders, no `charge` knob.
# Mobile is first-class rather than a shrunk desktop: the panel becomes a sheet under the canvas,
# the canvas keeps ~62vh instead of the ~25% the force layout used to leave it, and the flow
# TRANSPOSES to vertical (x<->y) on a narrow viewport — reading a chain top-to-bottom is what
# fits a phone, and it is a coordinate swap, not a second layout.
_PAGE13 = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>__TITLE__</title>
<style>
:root{--bg:#0b0b0f;--panel:#14141a;--line:#26262e;--ink:#e9e9ee;--ink2:#9a9aa6;--accent:#4d86ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif;display:flex;height:100vh;height:100dvh}
#canvas{flex:1;position:relative;min-width:0}#canvas canvas{width:100%;height:100%;display:block;touch-action:none}
#side{width:380px;flex:none;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px;-webkit-overflow-scrolling:touch}
h1{font-size:15px;margin:0 0 2px;letter-spacing:-.01em}
.muted{color:var(--ink2);font-size:12px}
.sechead{font-size:10px;color:var(--ink2);margin:16px 0 6px;text-transform:uppercase;letter-spacing:.09em}
.pills{display:flex;flex-wrap:wrap;gap:6px}
.pill{background:transparent;color:var(--ink2);border:1px solid var(--line);border-radius:999px;padding:4px 11px;cursor:pointer;font:11px system-ui;white-space:nowrap}
.pill.active{border-color:var(--accent);background:#141c2e;color:var(--ink)}
.row{padding:7px 9px;border:1px solid var(--line);border-radius:8px;margin-bottom:5px;font-size:12px;cursor:pointer}
.row:hover{border-color:var(--accent)}
.row.on{border-color:var(--accent);background:#141c2e}
.row .n{font:11px/1.4 'Spline Sans Mono',ui-monospace,monospace;color:var(--ink2)}
.g{opacity:.5;border-style:dashed}
.legend{font-size:11px;color:var(--ink2);margin:10px 0 0;line-height:1.65}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font:11px/1.6 'Spline Sans Mono',ui-monospace,monospace;margin-top:6px}
.kv b{color:var(--ink2);font-weight:400}
.kv span{text-align:right}
.card{border:1px solid var(--accent);border-radius:10px;padding:10px 11px;background:#111726}
.hist{display:flex;align-items:flex-end;gap:3px;height:44px;margin-top:6px}
.hist i{flex:1;background:#2b3550;border-radius:2px 2px 0 0;position:relative;min-height:2px}
.hist i b{position:absolute;bottom:-15px;left:0;right:0;text-align:center;font:9px 'Spline Sans Mono',monospace;color:var(--ink2);font-weight:400}
.hist+.muted{margin-top:18px}
button.reset{background:transparent;color:var(--ink2);border:1px solid var(--line);border-radius:8px;padding:5px 10px;cursor:pointer;font:11px system-ui;margin-top:8px}
@media (max-width:760px){
  body{flex-direction:column;height:auto;min-height:100dvh}
  #canvas{height:62dvh;flex:none}
  #side{width:auto;border-left:none;border-top:1px solid var(--line);max-height:none;overflow:visible;padding:14px}
  .row{padding:10px 11px;font-size:13px}
  .pill{padding:7px 13px;font-size:12px}
}
</style></head><body>
<div id="canvas"><canvas id="cv"></canvas></div>
<div id="side">
<h1>__TITLE__</h1><div class="muted" id="sub"></div>

<div class="sechead">Âncoras</div><div class="pills" id="structs"></div>
<div class="sechead">Escopo</div><div class="pills" id="scopes"></div>
<div class="sechead">Rótulos das ações</div><div class="pills" id="labels"></div>

<div class="sechead">Seleção</div>
<div id="sel"></div>

<div class="sechead">Comprimento das trilhas</div>
<div class="hist" id="hist"></div>
<div class="muted" id="histNote"></div>

<div class="sechead">Caminhos mais fortes</div><div id="strong"></div>
<div class="sechead">Caminhos mais frequentes</div><div id="freq"></div>

<div class="legend">Um traço = um SEGMENTO: a maior sequência contígua de ações percorrida
pelo mesmo conjunto de caminhos. Ponto cinza pequeno = bifurcação/convergência — artefato de
desenho, nunca um estado (a Fase 1 apagou os estados inventados e eles não voltam por aqui).
Espessura = frequência somada dos caminhos que passam pelo traço. Tracejado curto = ação que
NUNCA foi observada em lugar nenhum do bundle (inferida pela regra). Losango = âncora; amarelo =
finalização. Clique num caminho da lista, num estado ou num segmento compartilhado: a ocorrência
inteira acende através dos traços que ela divide com as outras.</div>
</div>
<script src="graph.js"></script>
<script>
const PAGES = __PAGES_JSON__;
const STRUCTURES = __STRUCTURES_JSON__;
const SCOPES = __SCOPES_JSON__;
let structure = __DEFAULT_STRUCTURE__;
let scope = __DEFAULT_SCOPE__;
let sel = null;           // {kind:'path'|'segment'|'state', id}
let mounted = null;
// Action labels are the SECONDARY layer (the owner's mobile requirement: "rótulos principais
// legíveis sem zoom extremo, secundários sob demanda"). At 20 states / 42 segments every
// midpoint label fights every other one, so by default only the recurring segments are named —
// a selected path always names all of its own, whatever the mode.
const LABEL_MODES = [{ id: 'main', label: 'principais' }, { id: 'all', label: 'todos' },
                     { id: 'none', label: 'nenhum' }];
let labelMode = 'all';  // owner default 2026-09-01: pentágono + Global já eram default, faltava só isto

const vertical = () => window.matchMedia('(max-width:760px)').matches;
function page() { return PAGES[structure + '|' + scope]; }
function freshCanvas() {
  const old = document.getElementById('cv');
  const next = old.cloneNode(false);
  old.parentNode.replaceChild(next, old);
  return next;
}
function fmt(v) { return v === null || v === undefined ? '—' : v; }

// Which links/nodes stay lit for the current selection. A PATH lights its own segments across
// every trunk it shares; a SEGMENT lights every occurrence that uses it; a STATE lights every
// chain passing through it. One resolver, three entry points.
function highlightOf(p) {
  if (!sel) return null;
  const segs = new Set();
  const pathIds = new Set();
  if (sel.kind === 'path') pathIds.add(sel.id);
  else if (sel.kind === 'segment') for (const id of p.segMeta[sel.id].pathIds) pathIds.add(id);
  else if (sel.kind === 'state') {
    for (const l of p.gv.links) {
      if (l.from !== sel.id && l.to !== sel.id) continue;
      for (const id of p.segMeta[l.id].pathIds) pathIds.add(id);
    }
  }
  const nodes = new Set();
  for (const id of pathIds) {
    const meta = p.pathMeta[id];
    if (!meta) continue;
    for (const s of meta.segIds) segs.add(s);
  }
  for (const l of p.gv.links) if (segs.has(l.id)) { nodes.add(l.from); nodes.add(l.to); }
  if (sel.kind === 'state') nodes.add(sel.id);
  return { links: segs, nodes: nodes, paths: pathIds };
}

function pathRow(p, id, extra) {
  const m = p.pathMeta[id];
  const on = sel && sel.kind === 'path' && sel.id === id ? ' on' : '';
  const ghost = m.observedRatio === 0 ? ' g' : '';
  const div = document.createElement('div');
  div.className = 'row' + on + ghost;
  div.innerHTML = m.sourceLabel + ' <b>—' + m.actions.join(' → ') + '→</b> ' + m.targetLabel
    + '<div class="n">' + extra + (m.actor === 'partner' ? ' · oponente' : '') + '</div>';
  div.onclick = function () { sel = { kind: 'path', id: id }; rebuild(); };
  return div;
}

function renderSelection(p) {
  const box = document.getElementById('sel');
  box.innerHTML = '';
  if (!sel) {
    box.innerHTML = '<div class="muted">Nada selecionado — clique num caminho, num estado ou num segmento.</div>';
    return;
  }
  const hl = highlightOf(p);
  if (sel.kind === 'path') {
    const m = p.pathMeta[sel.id];
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<b>' + m.sourceLabel + ' —' + m.actions.join(' → ') + '→ ' + m.targetLabel + '</b>'
      + '<div class="kv">'
      + '<b>length</b><span>' + m.length + '</span>'
      + '<b>observed</b><span>' + m.observed + '/' + m.length + '</span>'
      + '<b>observed_ratio</b><span>' + m.observedRatio.toFixed(2) + '</span>'
      + '<b>support</b><span>' + m.support + '</span>'
      + '<b>ocorrências</b><span>' + m.count + '</span>'
      + '<b>terminal</b><span>' + (m.terminal ? 'sim' : 'não') + '</span>'
      + '<b>role_delta</b><span>' + m.roleDelta + '</span>'
      + '<b>strength</b><span>' + fmt(m.strength) + '</span>'
      + '<b>segmentos</b><span>' + m.segIds.length + '</span>'
      + '</div>';
    box.appendChild(card);
  } else if (sel.kind === 'segment') {
    const s = p.segMeta[sel.id];
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<b>' + s.actions.join(' → ') + '</b><div class="n">'
      + (s.shared ? s.pathIds.length + ' caminhos dividem este traço' : 'traço exclusivo de um caminho')
      + ' · frequência ' + s.weight + '</div>';
    box.appendChild(card);
    for (const id of s.pathIds) box.appendChild(pathRow(p, id, 'x' + p.pathMeta[id].count));
  } else {
    const node = p.gv.nodes.find(function (n) { return n.id === sel.id; });
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<b>' + (node ? node.label : sel.id) + '</b><div class="n">'
      + hl.paths.size + ' cadeia(s) passam por aqui</div>';
    box.appendChild(card);
    for (const id of Array.from(hl.paths).sort()) box.appendChild(pathRow(p, id, 'x' + p.pathMeta[id].count));
  }
  const reset = document.createElement('button');
  reset.className = 'reset';
  reset.textContent = 'limpar seleção';
  reset.onclick = function () { sel = null; rebuild(); };
  box.appendChild(reset);
}

function renderLists(p) {
  const ids = Object.keys(p.pathMeta);
  const strong = ids.filter(function (i) { return p.pathMeta[i].strength !== null; })
    .sort(function (a, b) { return p.pathMeta[b].strength - p.pathMeta[a].strength || (a < b ? -1 : 1); })
    .slice(0, 10);
  const freq = ids.slice()
    .sort(function (a, b) { return p.pathMeta[b].count - p.pathMeta[a].count || (a < b ? -1 : 1); })
    .slice(0, 10);
  const sBox = document.getElementById('strong'); sBox.innerHTML = '';
  for (const id of strong) sBox.appendChild(pathRow(p, id, 'strength ' + p.pathMeta[id].strength + ' · x' + p.pathMeta[id].count));
  if (!strong.length) sBox.innerHTML = '<div class="muted">Nenhuma ação observada deste bundle tem rating — strength fica indefinida.</div>';
  const fBox = document.getElementById('freq'); fBox.innerHTML = '';
  for (const id of freq) fBox.appendChild(pathRow(p, id, 'x' + p.pathMeta[id].count + ' · length ' + p.pathMeta[id].length));

  const hist = document.getElementById('hist'); hist.innerHTML = '';
  const keys = Object.keys(p.stats.lengths).map(Number).sort(function (a, b) { return a - b; });
  const max = Math.max.apply(null, keys.map(function (k) { return p.stats.lengths[k]; }).concat([1]));
  for (const k of keys) {
    const bar = document.createElement('i');
    bar.style.height = Math.round(100 * p.stats.lengths[k] / max) + '%';
    bar.innerHTML = '<b>' + k + '</b>';
    bar.title = p.stats.lengths[k] + ' caminho(s) de ' + k + ' ação(ões)';
    hist.appendChild(bar);
  }
  const t = p.stats.biggestTrunk;
  document.getElementById('histNote').textContent =
    p.stats.sharedActionPct + '% das ações desenhadas estão num traço compartilhado'
    + (t ? ' · maior tronco: ' + t.actions.join(' → ') + ' (' + t.paths + ' caminhos)' : '');
}

function rebuild() {
  const p = page();
  document.getElementById('sub').textContent =
    p.stats.paths + ' caminhos · ' + p.stats.segments + ' segmentos · '
    + p.stats.statePoints + ' estados · ' + p.stats.branchPoints + ' bifurcações / '
    + p.stats.mergePoints + ' convergências';

  const sBox = document.getElementById('structs'); sBox.innerHTML = '';
  for (const s of STRUCTURES) {
    const b = document.createElement('div');
    b.className = 'pill' + (s.id === structure ? ' active' : '');
    b.textContent = s.label;
    b.onclick = (function (id) { return function () { structure = id; sel = null; rebuild(); }; })(s.id);
    sBox.appendChild(b);
  }
  const cBox = document.getElementById('scopes'); cBox.innerHTML = '';
  for (const s of SCOPES) {
    const b = document.createElement('div');
    b.className = 'pill' + (s.id === scope ? ' active' : '');
    b.textContent = s.label;
    b.onclick = (function (id) { return function () { scope = id; sel = null; rebuild(); }; })(s.id);
    cBox.appendChild(b);
  }

  renderSelection(p);
  renderLists(p);

  const lBox = document.getElementById('labels'); lBox.innerHTML = '';
  for (const m of LABEL_MODES) {
    const b = document.createElement('div');
    b.className = 'pill' + (m.id === labelMode ? ' active' : '');
    b.textContent = m.label;
    b.onclick = (function (id) { return function () { labelMode = id; rebuild(); }; })(m.id);
    lBox.appendChild(b);
  }

  const hl = highlightOf(p);
  const flip = vertical();
  // Phone rules, in the owner's own priority order: the flow turns VERTICAL (a chain reads
  // top-to-bottom on a tall screen, and it is a coordinate swap, not a second layout); only the
  // PRIMARY labels stay up (anchors + recurring states + whatever is selected), the rest are on
  // demand via a tap; and a long label is elided rather than clipped by the fit margin.
  const nodes = p.gv.nodes.map(function (n) {
    const lit = !hl || hl.nodes.has(n.id);
    let label = n.label;
    if (flip) {
      const primary = n.pin && (n.shape === 'diamond' || n.color === '#facc15');
      if (!primary && (n.size || 1) < 3 && !(hl && hl.nodes.has(n.id))) label = '';
      else if (label.length > 16) label = label.slice(0, 15) + '…';
    }
    const base = label === n.label ? n : Object.assign({}, n, { label: label });
    return flip ? Object.assign({}, base, { x: n.y, y: n.x }) : base;
  });
  const links = p.gv.links.map(function (l) {  // flip => phone: action labels are on demand only
    const lit = hl && hl.links.has(l.id);
    const show = lit || (!flip && (labelMode === 'all' || (labelMode === 'main' && l.weight >= 2)));
    return show ? l : Object.assign({}, l, { label: undefined });
  });
  if (mounted && mounted.destroy) mounted.destroy();
  mounted = GAGraph.mount(freshCanvas(), {
    mode: 'map', nodes: nodes, links: links, pan: true, zoom: true,
    collide: false, bounded: false, charge: 0, linkDist: 1, gravity: 0,
    forceLabels: true, minZoom: 0.08,
    highlightLinks: hl ? Array.from(hl.links) : null,
    highlightNodes: hl ? Array.from(hl.nodes) : null,
    onSelect: function (n) {
      if (!n) { sel = null; rebuild(); return; }
      if (n.junction) return;              // a scaffolding dot is not a thing to select
      sel = { kind: 'state', id: n.id };
      rebuild();
    },
    onLinkSelect: function (l) { sel = { kind: 'segment', id: l.id }; rebuild(); },
  });
}
let lastVertical = vertical();
window.addEventListener('resize', function () {
  if (vertical() !== lastVertical) { lastVertical = vertical(); rebuild(); }
});
rebuild();
</script></body></html>"""


def _render_variant13(out: Path, agg: Aggregate, bundle: dict[str, Any]) -> dict[str, Any]:
    payload = _paths_payloads(agg, bundle)
    default_structure, default_scope = payload["default"].split("|")
    html = (
        _PAGE13
        .replace("__TITLE__", "13 — Caminhos")
        .replace("__PAGES_JSON__", json.dumps(payload["pages"], ensure_ascii=False))
        .replace("__STRUCTURES_JSON__", json.dumps(payload["structures"], ensure_ascii=False))
        .replace("__SCOPES_JSON__", json.dumps(payload["scopes"], ensure_ascii=False))
        .replace("__DEFAULT_STRUCTURE__", json.dumps(default_structure))
        .replace("__DEFAULT_SCOPE__", json.dumps(default_scope))
    )
    (out / "13-caminhos.html").write_text(html, encoding="utf-8")
    default = payload["pages"][payload["default"]]
    stats = default["stats"]
    return {
        "nodes": len(default["gv"]["nodes"]), "edges": len(default["gv"]["links"]),
        "edges_per_node": round(len(default["gv"]["links"]) / len(default["gv"]["nodes"]), 2)
        if default["gv"]["nodes"] else 0.0,
        "pct_inferred_edges": _pct(
            sum(1 for link in default["gv"]["links"] if link.get("inf")),
            len(default["gv"]["links"])),
        "partner_elements": sum(1 for n in default["gv"]["nodes"] if n.get("fighter") == "b"),
        "handover_links": 0,  # paths never cross actors — the compiler's own guarantee
        "knobs": {"charge": 0, "linkDist": 1, "gravity": 0},  # fully positioned, no physics
        "paths": stats["paths"], "segments": stats["segments"], "points": stats["points"],
        "branch_points": stats["branchPoints"], "merge_points": stats["mergePoints"],
        "shared_action_pct": stats["sharedActionPct"],
        "biggest_trunk_paths": (stats["biggestTrunk"] or {}).get("paths", 0),
        "length_distribution": stats["lengths"],
        "structures": len(payload["structures"]), "scopes": len(payload["scopes"]),
    }


# Variant 14 — "Caminhos por sistema". Same shell/navigation as 13's own template (structure
# pills, scope pills, selection model, label modes) — the `SCOPES` pills already double as system
# navigation (Global + one per detected system), so 14 only adds: a node click on a collapsed
# system (`n.sysId`) jumps scope exactly like its own pill; clicking empty canvas also returns to
# Global (13's background click only ever cleared the selection); and a small "Sistemas" panel
# block for member/crossing counts. Cloned rather than parametrised — same reason `_PAGE8`/
# `_PAGE9`/`_PAGE_SYSTEMS`/`_PAGE13` are each their own template (module convention: no
# `.format()`-shared JS body across variants).
_PAGE14 = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/><title>__TITLE__</title>
<style>
:root{--bg:#0b0b0f;--panel:#14141a;--line:#26262e;--ink:#e9e9ee;--ink2:#9a9aa6;--accent:#4d86ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif;display:flex;height:100vh;height:100dvh}
#canvas{flex:1;position:relative;min-width:0}#canvas canvas{width:100%;height:100%;display:block;touch-action:none}
#side{width:380px;flex:none;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px;-webkit-overflow-scrolling:touch}
h1{font-size:15px;margin:0 0 2px;letter-spacing:-.01em}
.muted{color:var(--ink2);font-size:12px}
.sechead{font-size:10px;color:var(--ink2);margin:16px 0 6px;text-transform:uppercase;letter-spacing:.09em}
.pills{display:flex;flex-wrap:wrap;gap:6px}
.pill{background:transparent;color:var(--ink2);border:1px solid var(--line);border-radius:999px;padding:4px 11px;cursor:pointer;font:11px system-ui;white-space:nowrap}
.pill.active{border-color:var(--accent);background:#141c2e;color:var(--ink)}
.row{padding:7px 9px;border:1px solid var(--line);border-radius:8px;margin-bottom:5px;font-size:12px;cursor:pointer}
.row:hover{border-color:var(--accent)}
.row.on{border-color:var(--accent);background:#141c2e}
.row .n{font:11px/1.4 'Spline Sans Mono',ui-monospace,monospace;color:var(--ink2)}
.g{opacity:.5;border-style:dashed}
.legend{font-size:11px;color:var(--ink2);margin:10px 0 0;line-height:1.65}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font:11px/1.6 'Spline Sans Mono',ui-monospace,monospace;margin-top:6px}
.kv b{color:var(--ink2);font-weight:400}
.kv span{text-align:right}
.card{border:1px solid var(--accent);border-radius:10px;padding:10px 11px;background:#111726}
.hist{display:flex;align-items:flex-end;gap:3px;height:44px;margin-top:6px}
.hist i{flex:1;background:#2b3550;border-radius:2px 2px 0 0;position:relative;min-height:2px}
.hist i b{position:absolute;bottom:-15px;left:0;right:0;text-align:center;font:9px 'Spline Sans Mono',monospace;color:var(--ink2);font-weight:400}
.hist+.muted{margin-top:18px}
button.reset{background:transparent;color:var(--ink2);border:1px solid var(--line);border-radius:8px;padding:5px 10px;cursor:pointer;font:11px system-ui;margin-top:8px}
@media (max-width:760px){
  body{flex-direction:column;height:auto;min-height:100dvh}
  #canvas{height:62dvh;flex:none}
  #side{width:auto;border-left:none;border-top:1px solid var(--line);max-height:none;overflow:visible;padding:14px}
  .row{padding:10px 11px;font-size:13px}
  .pill{padding:7px 13px;font-size:12px}
}
</style></head><body>
<div id="canvas"><canvas id="cv"></canvas></div>
<div id="side">
<h1>__TITLE__</h1><div class="muted" id="sub"></div>

<div class="sechead">Âncoras</div><div class="pills" id="structs"></div>
<div class="sechead">Sistemas (Global ou um sistema expandido)</div><div class="pills" id="scopes"></div>
<div class="sechead">Rótulos das ações</div><div class="pills" id="labels"></div>

<div class="sechead">Sistemas</div>
<div id="sysInfo"></div>

<div class="sechead">Seleção</div>
<div id="sel"></div>

<div class="sechead">Comprimento das trilhas</div>
<div class="hist" id="hist"></div>
<div class="muted" id="histNote"></div>

<div class="sechead">Caminhos mais fortes</div><div id="strong"></div>
<div class="sechead">Caminhos mais frequentes</div><div id="freq"></div>

<div class="legend">Anel duplo = sistema colapsado (clique nele, ou na pill acima, para
expandir NO LUGAR); voltar = pill Global ou clique no fundo. Um caminho que atravessa um
sistema continua sendo UM caminho — desenha até o nó do sistema e sai dele, o path_id nunca se
perde; ao expandir, a ocorrência inteira ainda acende, inclusive o trecho que estava escondido.
Anel rosa = membro que cruza a fronteira do sistema aberto, com <code>[→saídas ←entradas]</code>
no rótulo; mini-nó pontilhado = um stub por DESTINO (outro sistema, ponte, âncora ou oponente),
nunca por travessia. Um traço = um SEGMENTO: a maior sequência contígua de ações percorrida pelo
mesmo conjunto de caminhos. Ponto cinza pequeno = bifurcação/convergência — artefato de desenho,
nunca um estado. Espessura = frequência somada dos caminhos que passam pelo traço. Tracejado
curto = ação que NUNCA foi observada em lugar nenhum do bundle (inferida pela regra). Losango =
âncora; amarelo = finalização. Clique num caminho da lista, num estado ou num segmento
compartilhado: a ocorrência inteira acende através dos traços que ela divide com as outras.</div>
</div>
<script src="graph.js"></script>
<script>
const PAGES = __PAGES_JSON__;
const STRUCTURES = __STRUCTURES_JSON__;
const SCOPES = __SCOPES_JSON__;
let structure = __DEFAULT_STRUCTURE__;
let scope = __DEFAULT_SCOPE__;
let sel = null;           // {kind:'path'|'segment'|'state', id}
let mounted = null;
// Same mobile requirement as 13: "rótulos principais legíveis sem zoom extremo, secundários sob
// demanda" — default 'all' (owner call 2026-09-01, same as 13's own new default).
const LABEL_MODES = [{ id: 'main', label: 'principais' }, { id: 'all', label: 'todos' },
                     { id: 'none', label: 'nenhum' }];
let labelMode = 'all';

const vertical = () => window.matchMedia('(max-width:760px)').matches;
function page() { return PAGES[structure + '|' + scope]; }
function freshCanvas() {
  const old = document.getElementById('cv');
  const next = old.cloneNode(false);
  old.parentNode.replaceChild(next, old);
  return next;
}
function fmt(v) { return v === null || v === undefined ? '—' : v; }

// Which links/nodes stay lit for the current selection. A PATH lights its own segments across
// every trunk it shares; a SEGMENT lights every occurrence that uses it; a STATE lights every
// chain passing through it. One resolver, three entry points — unchanged from 13: a path_id
// still identifies one whole occurrence whether its endpoint is drawn as a real state, a
// collapsed system, or a boundary stub.
function highlightOf(p) {
  if (!sel) return null;
  const segs = new Set();
  const pathIds = new Set();
  if (sel.kind === 'path') pathIds.add(sel.id);
  else if (sel.kind === 'segment') for (const id of p.segMeta[sel.id].pathIds) pathIds.add(id);
  else if (sel.kind === 'state') {
    for (const l of p.gv.links) {
      if (l.from !== sel.id && l.to !== sel.id) continue;
      for (const id of p.segMeta[l.id].pathIds) pathIds.add(id);
    }
  }
  const nodes = new Set();
  for (const id of pathIds) {
    const meta = p.pathMeta[id];
    if (!meta) continue;
    for (const s of meta.segIds) segs.add(s);
  }
  for (const l of p.gv.links) if (segs.has(l.id)) { nodes.add(l.from); nodes.add(l.to); }
  if (sel.kind === 'state') nodes.add(sel.id);
  return { links: segs, nodes: nodes, paths: pathIds };
}

function pathRow(p, id, extra) {
  const m = p.pathMeta[id];
  const on = sel && sel.kind === 'path' && sel.id === id ? ' on' : '';
  const ghost = m.observedRatio === 0 ? ' g' : '';
  const div = document.createElement('div');
  div.className = 'row' + on + ghost;
  div.innerHTML = m.sourceLabel + ' <b>—' + m.actions.join(' → ') + '→</b> ' + m.targetLabel
    + '<div class="n">' + extra + (m.actor === 'partner' ? ' · oponente' : '') + '</div>';
  div.onclick = function () { sel = { kind: 'path', id: id }; rebuild(); };
  return div;
}

function renderSelection(p) {
  const box = document.getElementById('sel');
  box.innerHTML = '';
  if (!sel) {
    box.innerHTML = '<div class="muted">Nada selecionado — clique num caminho, num estado, num sistema ou num segmento.</div>';
    return;
  }
  const hl = highlightOf(p);
  if (sel.kind === 'path') {
    const m = p.pathMeta[sel.id];
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<b>' + m.sourceLabel + ' —' + m.actions.join(' → ') + '→ ' + m.targetLabel + '</b>'
      + '<div class="kv">'
      + '<b>length</b><span>' + m.length + '</span>'
      + '<b>observed</b><span>' + m.observed + '/' + m.length + '</span>'
      + '<b>observed_ratio</b><span>' + m.observedRatio.toFixed(2) + '</span>'
      + '<b>support</b><span>' + m.support + '</span>'
      + '<b>ocorrências</b><span>' + m.count + '</span>'
      + '<b>terminal</b><span>' + (m.terminal ? 'sim' : 'não') + '</span>'
      + '<b>role_delta</b><span>' + m.roleDelta + '</span>'
      + '<b>strength</b><span>' + fmt(m.strength) + '</span>'
      + '<b>segmentos</b><span>' + m.segIds.length + '</span>'
      + '</div>';
    box.appendChild(card);
  } else if (sel.kind === 'segment') {
    const s = p.segMeta[sel.id];
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<b>' + s.actions.join(' → ') + '</b><div class="n">'
      + (s.shared ? s.pathIds.length + ' caminhos dividem este traço' : 'traço exclusivo de um caminho')
      + ' · frequência ' + s.weight + '</div>';
    box.appendChild(card);
    for (const id of s.pathIds) box.appendChild(pathRow(p, id, 'x' + p.pathMeta[id].count));
  } else {
    const node = p.gv.nodes.find(function (n) { return n.id === sel.id; });
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = '<b>' + (node ? node.label : sel.id) + '</b><div class="n">'
      + hl.paths.size + ' cadeia(s) passam por aqui</div>';
    box.appendChild(card);
    for (const id of Array.from(hl.paths).sort()) box.appendChild(pathRow(p, id, 'x' + p.pathMeta[id].count));
  }
  const reset = document.createElement('button');
  reset.className = 'reset';
  reset.textContent = 'limpar seleção';
  reset.onclick = function () { sel = null; rebuild(); };
  box.appendChild(reset);
}

function renderLists(p) {
  const ids = Object.keys(p.pathMeta);
  const strong = ids.filter(function (i) { return p.pathMeta[i].strength !== null; })
    .sort(function (a, b) { return p.pathMeta[b].strength - p.pathMeta[a].strength || (a < b ? -1 : 1); })
    .slice(0, 10);
  const freq = ids.slice()
    .sort(function (a, b) { return p.pathMeta[b].count - p.pathMeta[a].count || (a < b ? -1 : 1); })
    .slice(0, 10);
  const sBox = document.getElementById('strong'); sBox.innerHTML = '';
  for (const id of strong) sBox.appendChild(pathRow(p, id, 'strength ' + p.pathMeta[id].strength + ' · x' + p.pathMeta[id].count));
  if (!strong.length) sBox.innerHTML = '<div class="muted">Nenhuma ação observada deste bundle tem rating — strength fica indefinida.</div>';
  const fBox = document.getElementById('freq'); fBox.innerHTML = '';
  for (const id of freq) fBox.appendChild(pathRow(p, id, 'x' + p.pathMeta[id].count + ' · length ' + p.pathMeta[id].length));

  const hist = document.getElementById('hist'); hist.innerHTML = '';
  const keys = Object.keys(p.stats.lengths).map(Number).sort(function (a, b) { return a - b; });
  const max = Math.max.apply(null, keys.map(function (k) { return p.stats.lengths[k]; }).concat([1]));
  for (const k of keys) {
    const bar = document.createElement('i');
    bar.style.height = Math.round(100 * p.stats.lengths[k] / max) + '%';
    bar.innerHTML = '<b>' + k + '</b>';
    bar.title = p.stats.lengths[k] + ' caminho(s) de ' + k + ' ação(ões)';
    hist.appendChild(bar);
  }
  const t = p.stats.biggestTrunk;
  document.getElementById('histNote').textContent =
    p.stats.sharedActionPct + '% das ações desenhadas estão num traço compartilhado'
    + (t ? ' · maior tronco: ' + t.actions.join(' → ') + ' (' + t.paths + ' caminhos)' : '');
}

// owner request: sistemas/membros + "caminhos que cruzam N sistemas" — additive to 13's own
// panel, computed server-side per page (`_paths_systems_view`'s own `stats`).
function renderSysInfo(p) {
  const ids = Object.keys(p.stats.systemMembers).sort();
  const rows = ids.map(function (id) {
    return '<b>' + id + (id === scope ? ' (aberto)' : '') + '</b><span>'
      + p.stats.systemMembers[id] + ' membro(s)</span>';
  }).join('');
  const c = p.stats.crossingHistogram;
  document.getElementById('sysInfo').innerHTML =
    '<div class="muted">' + ids.length + ' sistema(s) detectado(s)</div>'
    + (rows ? '<div class="kv">' + rows + '</div>' : '')
    + '<div class="muted" style="margin-top:6px">caminhos por nº de sistemas tocados — 0: '
    + c['0'] + ' · 1: ' + c['1'] + ' · 2: ' + c['2'] + '</div>';
}

function rebuild() {
  const p = page();
  document.getElementById('sub').textContent =
    p.stats.paths + ' caminhos · ' + p.stats.segments + ' segmentos · '
    + p.stats.statePoints + ' estados · ' + p.stats.branchPoints + ' bifurcações / '
    + p.stats.mergePoints + ' convergências';

  const sBox = document.getElementById('structs'); sBox.innerHTML = '';
  for (const s of STRUCTURES) {
    const b = document.createElement('div');
    b.className = 'pill' + (s.id === structure ? ' active' : '');
    b.textContent = s.label;
    b.onclick = (function (id) { return function () { structure = id; sel = null; rebuild(); }; })(s.id);
    sBox.appendChild(b);
  }
  const cBox = document.getElementById('scopes'); cBox.innerHTML = '';
  for (const s of SCOPES) {
    const b = document.createElement('div');
    b.className = 'pill' + (s.id === scope ? ' active' : '');
    b.textContent = s.label;
    b.onclick = (function (id) { return function () { scope = id; sel = null; rebuild(); }; })(s.id);
    cBox.appendChild(b);
  }

  renderSelection(p);
  renderLists(p);
  renderSysInfo(p);

  const lBox = document.getElementById('labels'); lBox.innerHTML = '';
  for (const m of LABEL_MODES) {
    const b = document.createElement('div');
    b.className = 'pill' + (m.id === labelMode ? ' active' : '');
    b.textContent = m.label;
    b.onclick = (function (id) { return function () { labelMode = id; rebuild(); }; })(m.id);
    lBox.appendChild(b);
  }

  const hl = highlightOf(p);
  const flip = vertical();
  // Phone rules, same as 13: the flow turns VERTICAL on a narrow viewport (coordinate swap, not
  // a second layout); only PRIMARY labels stay up (anchors, systems — size>=3 by construction —
  // and whatever is selected); a long label is elided rather than clipped.
  const nodes = p.gv.nodes.map(function (n) {
    let label = n.label;
    if (flip) {
      const primary = n.pin && (n.shape === 'diamond' || n.color === '#facc15' || n.system);
      if (!primary && (n.size || 1) < 3 && !(hl && hl.nodes.has(n.id))) label = '';
      else if (label.length > 16) label = label.slice(0, 15) + '…';
    }
    const base = label === n.label ? n : Object.assign({}, n, { label: label });
    return flip ? Object.assign({}, base, { x: n.y, y: n.x }) : base;
  });
  const links = p.gv.links.map(function (l) {  // flip => phone: action labels are on demand only
    const lit = hl && hl.links.has(l.id);
    const show = lit || (!flip && (labelMode === 'all' || (labelMode === 'main' && l.weight >= 2)));
    return show ? l : Object.assign({}, l, { label: undefined });
  });
  if (mounted && mounted.destroy) mounted.destroy();
  mounted = GAGraph.mount(freshCanvas(), {
    mode: 'map', nodes: nodes, links: links, pan: true, zoom: true,
    collide: false, bounded: false, charge: 0, linkDist: 1, gravity: 0,
    forceLabels: true, minZoom: 0.08,
    highlightLinks: hl ? Array.from(hl.links) : null,
    highlightNodes: hl ? Array.from(hl.nodes) : null,
    onSelect: function (n) {
      if (!n) {  // click no fundo: limpa a seleção e, se um sistema estava aberto, volta ao Global
        sel = null;
        if (scope !== __DEFAULT_SCOPE__) scope = __DEFAULT_SCOPE__;
        rebuild();
        return;
      }
      if (n.junction) return;              // a scaffolding dot is not a thing to select
      if (n.sysId) { scope = n.sysId; sel = null; rebuild(); return; }  // system node = its own pill
      sel = { kind: 'state', id: n.id };
      rebuild();
    },
    onLinkSelect: function (l) { sel = { kind: 'segment', id: l.id }; rebuild(); },
  });
}
let lastVertical = vertical();
window.addEventListener('resize', function () {
  if (vertical() !== lastVertical) { lastVertical = vertical(); rebuild(); }
});
rebuild();
</script></body></html>"""


def _render_variant14(out: Path, agg: Aggregate, bundle: dict[str, Any]) -> dict[str, Any]:
    payload = _paths_systems_payloads(agg, bundle)
    default_structure, default_scope = payload["default"].split("|")
    html = (
        _PAGE14
        .replace("__TITLE__", "14 — Caminhos por sistema")
        .replace("__PAGES_JSON__", json.dumps(payload["pages"], ensure_ascii=False))
        .replace("__STRUCTURES_JSON__", json.dumps(payload["structures"], ensure_ascii=False))
        .replace("__SCOPES_JSON__", json.dumps(payload["scopes"], ensure_ascii=False))
        .replace("__DEFAULT_STRUCTURE__", json.dumps(default_structure))
        .replace("__DEFAULT_SCOPE__", json.dumps(default_scope))
    )
    (out / "14-caminhos-sistemas.html").write_text(html, encoding="utf-8")
    default = payload["pages"][payload["default"]]
    stats = default["stats"]
    return {
        "nodes": len(default["gv"]["nodes"]), "edges": len(default["gv"]["links"]),
        "edges_per_node": round(len(default["gv"]["links"]) / len(default["gv"]["nodes"]), 2)
        if default["gv"]["nodes"] else 0.0,
        "pct_inferred_edges": _pct(
            sum(1 for link in default["gv"]["links"] if link.get("inf")),
            len(default["gv"]["links"])),
        "partner_elements": sum(1 for n in default["gv"]["nodes"] if n.get("fighter") == "b"),
        "handover_links": 0,  # paths never cross actors — the compiler's own guarantee
        "knobs": {"charge": 0, "linkDist": 1, "gravity": 0},  # fully positioned, no physics
        "paths": stats["paths"], "segments": stats["segments"], "points": stats["points"],
        "branch_points": stats["branchPoints"], "merge_points": stats["mergePoints"],
        "shared_action_pct": stats["sharedActionPct"],
        "biggest_trunk_paths": (stats["biggestTrunk"] or {}).get("paths", 0),
        "length_distribution": stats["lengths"],
        "structures": len(payload["structures"]), "scopes": len(payload["scopes"]),
        "systems": payload["systems"], "system_members": stats["systemMembers"],
        "crossing_histogram": stats["crossingHistogram"],
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
    ("9-sistemas-expande-in-place.html", "same systems as 8, but expansion stays in the SAME view — click a system row/node to expand in place (multiple at once)"),
    ("10-gating-comparado.html", "same gated base, side by side across inference policies — see the effect of the density gate before trusting the dominance/bridge numbers"),
    ("11-sistemas-vista-separada.html", "global (every node/edge, systems collapsed, ALL bridges) + a separate per-system view with a stub mini-node per boundary destination — locked combo, no controls"),
    ("12-sistemas-vista-separada-seletiva.html", "same as 11, controls live — 36 precomputed (policy x min_support x opponent mode) combos, client-side bridge/type/flow-bias filters"),
    ("13-caminhos.html", "edge = PATH: every occurrence expanded to state→a1→a2→state, contiguous shared runs bundled into segments with branch/merge points, hierarchical (non-force) layout, configurable anchor frame, per-path metrics panel"),
    ("14-caminhos-sistemas.html", "13's own paths, systems collapsible in place: every detected system folds into one node (bridges/anchors/opponent stay first-class), a crossing path keeps its path_id and draws through the system's own node; click a system (node or pill) to expand it with 13's flow layout restricted to its subgraph + 11/12's own boundary stubs"),
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
            "  function mount(canvas, opts) {\n    const ctx = canvas.getContext('2d');",
            "  function mount(canvas, opts) {\n    const ctx = canvas.getContext('2d');\n"
            "    // map-prototype patch: node silhouette — a start anchor is a DIAMOND (owner\n"
            "    // 2026-08-27), everything else stays a circle. One helper, used by every layer\n"
            "    // of the node draw (glow, dark core, inner fill, ring) so the shape is coherent.\n"
            "    function gaNodePath(c, n, rad) {\n"
            "      c.beginPath();\n"
            "      if (n.shape === 'diamond') {\n"
            "        const d = rad * 1.25;  // equal-area-ish: a diamond looks smaller than its circle\n"
            "        c.moveTo(n.x, n.y - d); c.lineTo(n.x + d, n.y);\n"
            "        c.lineTo(n.x, n.y + d); c.lineTo(n.x - d, n.y); c.closePath();\n"
            "      } else {\n"
            "        c.arc(n.x, n.y, rad, 0, Math.PI * 2);\n"
            "      }\n"
            "    }",
        ),
        (
            "        const mapLabel = !useCam || cam.k >= 1 || (n.size || 1) >= 2 || inFocus;",
            "        const mapLabel = opts.forceLabels || !useCam || cam.k >= 1 || (n.size || 1) >= 2 || inFocus;  // map-prototype patch: force-visible labels\n",
        ),
        (
            "      r: 5 + (n.size || 1) * 4,",
            "      r: n.junction ? 3 : 4 + (n.size || 1) * 3.2,  // map-prototype patch: smaller radius, more room to breathe (adendo 2026-08-27); a variant-13 junction is a 3px scaffolding dot, not a node",
        ),
        (
            "        ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();",
            "        gaNodePath(ctx, n, r); ctx.fill();  // map-prototype patch: diamond-aware glow",
        ),
        (
            "        ctx.arc(n.x, n.y, Math.max(1.5, r - 2.6), 0, Math.PI * 2); ctx.fill();",
            "        gaNodePath(ctx, n, Math.max(1.5, r - 2.6)); ctx.fill();  // map-prototype patch: diamond-aware core",
        ),
        (
            "        ctx.shadowBlur = 0;",
            "        ctx.shadowBlur = 0;\n"
            "        if (n.ring) {  // map-prototype patch: actor border ring\n"
            "          ctx.save();\n"
            "          ctx.globalAlpha = dim ? 0.18 : 1;\n"
            "          ctx.lineWidth = 2.5;\n"
            "          ctx.strokeStyle = n.ring;\n"
            "          gaNodePath(ctx, n, r + 3); ctx.stroke();\n"
            "          if (n.system) {  // map-prototype patch: system node — TRUE double ring (owner\n"
            "                          // correction: no fill colour of its own any more, ring is the\n"
            "                          // only distinguishing mark, one ring alone reads too thin)\n"
            "            gaNodePath(ctx, n, r + 7); ctx.stroke();\n"
            "          }\n"
            "          ctx.restore();\n"
            "        }",
        ),
        (
            "        ctx.beginPath(); ctx.fillStyle = col;\n"
            "        ctx.arc(n.x, n.y, Math.max(1, r - 4.5), 0, Math.PI * 2); ctx.fill();",
            "        ctx.fillStyle = col;\n"
            "        gaNodePath(ctx, n, Math.max(1, r - 4.5)); ctx.fill();  // map-prototype patch: diamond-aware fill\n"
            "        if (n.split) {  // map-prototype patch: split fill — one vertex, two athletes\n"
            "                        // (variant 13's unified finish). Half and half, vertically, so\n"
            "                        // folding the two finishes into one place never hides WHOSE.\n"
            "          const sr = Math.max(1, r - 4.5);\n"
            "          for (let si = 0; si < 2; si++) {\n"
            "            ctx.save();\n"
            "            ctx.beginPath();\n"
            "            ctx.rect(si === 0 ? n.x - sr - 1 : n.x, n.y - sr - 1, sr + 1, 2 * sr + 2);\n"
            "            ctx.clip();\n"
            "            ctx.fillStyle = n.split[si];\n"
            "            gaNodePath(ctx, n, sr); ctx.fill();\n"
            "            ctx.restore();\n"
            "          }\n"
            "        }\n"
            "        if (n.icon) {  // map-prototype patch: category/finish glyph — bold LETTER, not\n"
            "                       // emoji (A.4: colour-emoji font isn't guaranteed on every viewer,\n"
            "                       // and reads worse than a letter at the common size-1 node radius)\n"
            "          ctx.save();\n"
            "          ctx.globalAlpha = dim ? 0.18 : 1;\n"
            "          ctx.font = `bold ${Math.max(11, r)}px sans-serif`;\n"
            "          ctx.textAlign = 'center';\n"
            "          ctx.textBaseline = 'middle';\n"
            "          ctx.fillStyle = '#fff';\n"
            "          ctx.fillText(n.icon, n.x, n.y);\n"
            "          ctx.restore();\n"
            "        }",
        ),
        (
            # map-prototype patch: a node dims from the explicit highlight set when there is one
            # (variant 13's path selection lights nodes that are nowhere near the clicked one).
            "        const dim = hov && !conn.has(n.id);",
            "        const dim = gaHN ? !gaHN.has(n.id) : (hov && !conn.has(n.id));  // map-prototype patch: explicit highlight set wins",
        ),
        (
            # map-prototype patch: LINK picking + `opts.onLinkSelect` (variant 13). `pick()` only
            # ever knew about nodes, so a shared segment — the thing the owner asked to be able to
            # click — had no hit target at all. Samples each link's own quadratic (the same control
            # point the draw pass computes) rather than the straight chord, so the hit area follows
            # the arc a fanned parallel edge is actually drawn on.
            "    function pick(mx, my) {  // screen \u2192 world, then nearest node",
            "    function pickLink(mx, my) {  // map-prototype patch: link picking (variant 13)\n"
            "      const wx = (mx - cam.x) / cam.k, wy = (my - cam.y) / cam.k;\n"
            "      const tol = (10 / cam.k) ** 2;\n"
            "      let best = null, bd = tol;\n"
            "      for (const l of links) {\n"
            "        const parN = l.parCount || 1, parI = l.par || 0;\n"
            "        let cpx = (l.s.x + l.t.x) / 2, cpy = (l.s.y + l.t.y) / 2;\n"
            "        if (l.bow) {\n"
            "          const bdx = l.t.x - l.s.x, bdy = l.t.y - l.s.y;\n"
            "          const bd = Math.sqrt(bdx * bdx + bdy * bdy) || 1;\n"
            "          cpx += (-bdy / bd) * bd * l.bow; cpy += (bdx / bd) * bd * l.bow;\n"
            "        }\n"
            "        if (parN > 1) {\n"
            "          const ddx = l.t.x - l.s.x, ddy = l.t.y - l.s.y;\n"
            "          const dd = Math.sqrt(ddx * ddx + ddy * ddy) || 1;\n"
            "          const step = Math.max(30, Math.min(dd * 0.13, 120));\n"
            "          const off = (parI - (parN - 1) / 2) * step;\n"
            "          cpx += (-ddy / dd) * off; cpy += (ddx / dd) * off;\n"
            "        }\n"
            "        for (let i = 0; i <= 16; i++) {\n"
            "          const u = i / 16, v = 1 - u;\n"
            "          const px = v * v * l.s.x + 2 * v * u * cpx + u * u * l.t.x;\n"
            "          const py = v * v * l.s.y + 2 * v * u * cpy + u * u * l.t.y;\n"
            "          const d = (px - wx) ** 2 + (py - wy) ** 2;\n"
            "          if (d < bd) { bd = d; best = l; }\n"
            "        }\n"
            "      }\n"
            "      return best;\n"
            "    }\n"
            "    function pick(mx, my) {  // screen \u2192 world, then nearest node",
        ),
        (
            "      const n = pick(e.clientX - r.left, e.clientY - r.top);  // null on empty space \u2192 deselect + zoom out\n"
            "      selected = n || null;\n"
            "      focusOn(selected);\n"
            "      if (onSelect) onSelect(selected);",
            "      const n = pick(e.clientX - r.left, e.clientY - r.top);  // null on empty space \u2192 deselect + zoom out\n"
            "      if (!n && opts.onLinkSelect) {  // map-prototype patch: a click that missed every node may still have hit a segment\n"
            "        const l = pickLink(e.clientX - r.left, e.clientY - r.top);\n"
            "        if (l) { opts.onLinkSelect(l); return; }\n"
            "      }\n"
            "      selected = n || null;\n"
            "      focusOn(selected);\n"
            "      if (onSelect) onSelect(selected);",
        ),
        (
            # A (label collision) + B (parallel edges) share one array + one draw pass, declared
            # at mount() scope (not per-frame) so a sibling helper doesn't need it passed around.
            "    let hover = null, selected = null, raf = null, t = 0;",
            "    let hover = null, selected = null, raf = null, t = 0;\n"
            "    let labelCandidates = [];  // map-prototype patch: label collision — collected each frame, drawn after a priority sort\n"
            "    // map-prototype patch: explicit highlight sets (variant 13). The stock renderer can\n"
            "    // only light a node's IMMEDIATE neighbours; a selected PATH is lit across every trunk\n"
            "    // it shares with other paths, which is not a neighbourhood and cannot be derived here.\n"
            "    // Caller-computed, ids only; absent -> every other variant behaves exactly as before.\n"
            "    const gaHL = opts.highlightLinks ? new Set(opts.highlightLinks) : null;\n"
            "    const gaHN = opts.highlightNodes ? new Set(opts.highlightNodes) : null;",
        ),
        (
            "        if (n === hover && pointer.active && pointer.moved) {  // pin to cursor (screen→world)\n"
            "          n.x = (pointer.x - cam.x) / cam.k; n.y = (pointer.y - cam.y) / cam.k;\n"
            "          n.vx = n.vy = 0; continue;\n"
            "        }",
            "        if (n === hover && pointer.active && pointer.moved) {  // pin to cursor (screen→world)\n"
            "          n.x = (pointer.x - cam.x) / cam.k; n.y = (pointer.y - cam.y) / cam.k;\n"
            "          n.vx = n.vy = 0; continue;\n"
            "        }\n"
            "        if (n.pin) { n.vx = n.vy = 0; continue; }  // map-prototype patch: pinned anchor node — never simulated (D addendum)",
        ),
        (
            # 11/12 fix (measured, screenshot repro): a system-boundary view has NO pinned
            # anchors (unlike the global view's finish/start cross), so its whole layout depends
            # on the free-body sim reaching a good spread from a random seed. `d2 = ... || 1`
            # only guards a LITERAL zero — two nodes seeded a few px apart (`Math.random()`
            # jitter is ±20px on each axis, so near-coincidence is common at n~20) still get
            # `force = CHARGE/d2` in the thousands for one step, and the velocity that imparts
            # decays only 14%/step (DAMP=0.86), so its geometric-sum travel distance
            # (`v/(1-DAMP)`) is enormous — a single kick can fling a low-degree stub node
            # 700-900 world units from the rest before gravity/springs claw it back, and `warm`'s
            # fixed step budget often runs out before it does. Reproduced empirically: 12 reloads
            # of the same "Sistema: Montada" combo, ~1/6 produced an outlier that far away,
            # which blows up `fitTarget()`'s bbox and renders the real cluster small and
            # off-centre — exactly the reported "amontoado no canto". Flooring `d2` caps the
            # worst-case one-step force the same way `COLLIDE`'s hard-separation branch already
            # caps overlap, just for the force path too.
            "          let dx = a.x - b.x, dy = a.y - b.y;\n"
            "          let d2 = dx * dx + dy * dy || 1;\n"
            "          let d = Math.sqrt(d2);",
            "          let dx = a.x - b.x, dy = a.y - b.y;\n"
            "          let d2 = Math.max(dx * dx + dy * dy, 36);  // map-prototype patch: floor repulsion distance — no near-coincident-seed catapult\n"
            "          let d = Math.sqrt(d2);",
        ),
        (
            "      destroy() { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); },\n"
            "      // programmatic select (search-to-locate): highlight + zoom onto a node by id\n"
            "      select(id) {\n"
            "        selected = byId[id] || null;\n"
            "        if (selected) focusOn(selected); else { focusOn(null); alpha = Math.max(alpha, 0.05); }\n"
            "        if (onSelect) onSelect(selected);\n"
            "        return selected;\n"
            "      },",
            "      destroy() { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); },\n"
            "      // programmatic select (search-to-locate): highlight + zoom onto a node by id\n"
            "      select(id) {\n"
            "        selected = byId[id] || null;\n"
            "        if (selected) focusOn(selected); else { focusOn(null); alpha = Math.max(alpha, 0.05); }\n"
            "        if (onSelect) onSelect(selected);\n"
            "        return selected;\n"
            "      },\n"
            "      positions() {  // map-prototype patch: snapshot live x/y per id — lets a caller\n"
            "        const out = {};                          // re-seed + pin on the NEXT mount (freeze-on-expand, variant 9)\n"
            "        for (const n of nodes) out[n.id] = { x: n.x, y: n.y };\n"
            "        return out;\n"
            "      },",
        ),
        (
            "      ctx.setTransform(dpr * cam.k, 0, 0, dpr * cam.k, cam.x * dpr, cam.y * dpr);\n"
            "      const hov = hover || selected;  // hover wins; selection keeps a sticky focus",
            "      ctx.setTransform(dpr * cam.k, 0, 0, dpr * cam.k, cam.x * dpr, cam.y * dpr);\n"
            "      labelCandidates.length = 0;  // map-prototype patch: label collision — reset per frame\n"
            "      if (opts.regions && opts.regions.length) {  // map-prototype patch: system regions (C) — hull/circle behind everything, no colour (colour is the actor's)\n"
            "        for (const reg of opts.regions) {\n"
            "          const pts = reg.members.map(id => byId[id]).filter(Boolean);\n"
            "          if (!pts.length) continue;\n"
            "          ctx.save();\n"
            "          ctx.fillStyle = 'rgba(255,255,255,0.04)';\n"
            "          ctx.strokeStyle = 'rgba(255,255,255,0.22)';\n"
            "          ctx.lineWidth = 1.2;\n"
            "          ctx.setLineDash([4, 4]);\n"
            "          ctx.beginPath();\n"
            "          if (pts.length < 3) {\n"
            "            let cx = 0, cy = 0, r = 40;\n"
            "            for (const p of pts) { cx += p.x; cy += p.y; }\n"
            "            cx /= pts.length; cy /= pts.length;\n"
            "            if (pts.length === 2) r = Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y) / 2 + 30;\n"
            "            ctx.arc(cx, cy, r, 0, Math.PI * 2);\n"
            "          } else {\n"
            "            const hull = convexHull(pts.map(p => ({ x: p.x, y: p.y })));\n"
            "            let hcx = 0, hcy = 0;\n"
            "            for (const p of hull) { hcx += p.x; hcy += p.y; }\n"
            "            hcx /= hull.length; hcy /= hull.length;\n"
            "            const pad = 26;\n"
            "            hull.forEach((p, i) => {\n"
            "              const ddx = p.x - hcx, ddy = p.y - hcy, dd = Math.hypot(ddx, ddy) || 1;\n"
            "              const ex = p.x + (ddx / dd) * pad, ey = p.y + (ddy / dd) * pad;\n"
            "              if (i === 0) ctx.moveTo(ex, ey); else ctx.lineTo(ex, ey);\n"
            "            });\n"
            "            ctx.closePath();\n"
            "          }\n"
            "          ctx.fill(); ctx.stroke();\n"
            "          ctx.setLineDash([]);\n"
            "          if (reg.label) {\n"
            "            let lx = 0, ly = 1e9;\n"
            "            for (const p of pts) { lx += p.x; ly = Math.min(ly, p.y - 44); }\n"
            "            lx /= pts.length;\n"
            "            ctx.fillStyle = 'rgba(233,233,238,0.55)';\n"
            "            ctx.font = \"11px 'Spline Sans Mono', monospace\";\n"
            "            ctx.textAlign = 'center';\n"
            "            ctx.fillText(reg.label, lx, ly);\n"
            "          }\n"
            "          ctx.restore();\n"
            "        }\n"
            "      }\n"
            "      const hov = hover || selected;  // hover wins; selection keeps a sticky focus",
        ),
        (
            # Consolidated links loop (B: parallel-edge arcs via a per-pair control-point offset,
            # `_index_parallel_links`' `l.par`/`l.parCount` — degenerates to a straight line when
            # parCount<=1, control point ON the line) + edge labels now COLLECTED (A.1), not drawn
            # inline — the final priority-sorted pass (node patch, below) draws them with a halo.
            "      // links\n"
            "      for (const l of links) {\n"
            "        const active = !hov || (conn.has(l.s.id) && conn.has(l.t.id) && (l.s === hov || l.t === hov));\n"
            "        const contested = l.fighter === 'x';\n"
            "        const col = l.fighter ? FIG[l.fighter] : '#3a3a45';\n"
            "        ctx.strokeStyle = col;\n"
            "        ctx.globalAlpha = hov ? (active ? 0.85 : 0.08) : (contested ? 0.28 : (l.fighter ? 0.5 : 0.32));\n"
            "        ctx.lineWidth = edgeWidth(l.weight, wMin, wMax) * (active ? 1.4 : 1) * (contested ? 0.85 : 1);\n"
            "        ctx.setLineDash(l.dashed ? [5, 5] : (contested ? [3, 4] : []));  // low-success / handover dashed\n"
            "        ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();\n"
            "        ctx.setLineDash([]);\n"
            "        // arrowhead at the target — only when zoomed in enough to read (same gate as labels).\n"
            "        // Size stays fixed rather than tracking edgeWidth — the 0.75-4px range is narrow enough\n"
            "        // that a scaled triangle risks overlapping into a blob on the thickest edges; fixed is\n"
            "        // the safer default without a rendered visual to tune against.\n"
            "        if (l.arrow && (!useCam || cam.k >= 1)) {\n"
            "          const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;\n"
            "          const d = Math.sqrt(dx * dx + dy * dy) || 1;\n"
            "          const ux = dx / d, uy = dy / d, px = -uy, py = ux;\n"
            "          const tip = l.t.r + 2, back = 7, spread = 4.2;\n"
            "          const ax = l.t.x - ux * tip, ay = l.t.y - uy * tip;\n"
            "          const bx = ax - ux * back, by = ay - uy * back;\n"
            "          ctx.fillStyle = col;\n"
            "          ctx.beginPath();\n"
            "          ctx.moveTo(ax, ay);\n"
            "          ctx.lineTo(bx + px * spread, by + py * spread);\n"
            "          ctx.lineTo(bx - px * spread, by - py * spread);\n"
            "          ctx.closePath(); ctx.fill();\n"
            "        }\n"
            "      }\n"
            "      ctx.setLineDash([]);\n"
            "      ctx.globalAlpha = 1;\n"
            "\n"
            "      ",
            "      // links\n"
            "      for (const l of links) {\n"
            "        const active = gaHL ? gaHL.has(l.id) : (!hov || (conn.has(l.s.id) && conn.has(l.t.id) && (l.s === hov || l.t === hov)));  // map-prototype patch: explicit highlight set wins\n"
            "        const contested = l.fighter === 'x';\n"
            "        const col = l.fighter ? FIG[l.fighter] : '#3a3a45';\n"
            "        // map-prototype patch: parallel edges (B) — offset the control point perpendicular\n"
            "        // to the line by l.par's slot among l.parCount siblings sharing the same node pair\n"
            "        // (unordered — both directions share one fan), so N different actions between the\n"
            "        // same two states draw as a fan of arcs instead of one overlapping line, each with\n"
            "        // its own midpoint for the label. parCount<=1 degenerates to a straight line.\n"
            "        const parN = l.parCount || 1, parI = l.par || 0;\n"
            "        let cpx = (l.s.x + l.t.x) / 2, cpy = (l.s.y + l.t.y) / 2;\n"
            "        if (l.bow) {  // map-prototype patch: return edge (variant 13) — bow it out of\n"
            "          const bdx = l.t.x - l.s.x, bdy = l.t.y - l.s.y;   // the forward reading\n"
            "          const bd = Math.sqrt(bdx * bdx + bdy * bdy) || 1;\n"
            "          cpx += (-bdy / bd) * bd * l.bow; cpy += (bdx / bd) * bd * l.bow;\n"
            "        }\n"
            "        if (parN > 1) {\n"
            "          const ddx = l.t.x - l.s.x, ddy = l.t.y - l.s.y;\n"
            "          const dd = Math.sqrt(ddx * ddx + ddy * ddy) || 1;\n"
            "          const opx = -ddy / dd, opy = ddx / dd;\n"
            "          // the fan's step scales with the edge's own LENGTH: a fixed offset is a wide\n"
            "          // fan on a short edge and an invisible one on a long edge, which is exactly\n"
            "          // how several actions between two distant states collapsed into one stroke.\n"
            "          const step = Math.max(30, Math.min(dd * 0.13, 120));\n"
            "          const off = (parI - (parN - 1) / 2) * step;\n"
            "          cpx += opx * off; cpy += opy * off;\n"
            "        }\n"
            "        ctx.strokeStyle = col;\n"
            "        ctx.globalAlpha = (gaHL || hov) ? (active ? 0.85 : 0.08) : (contested ? 0.28 : (l.fighter ? 0.5 : 0.32));  // map-prototype patch: a highlight set dims exactly like a hover does\n"
            "        ctx.lineWidth = edgeWidth(l.weight, wMin, wMax) * (active ? 1.4 : 1) * (contested ? 0.85 : 1) * (l.back ? 0.6 : 1);  // map-prototype patch: a return edge draws thinner\n"
            "        ctx.setLineDash(l.dashed ? [5, 5] : (contested ? [3, 4] : []));  // low-success / handover dashed\n"
            "        ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.quadraticCurveTo(cpx, cpy, l.t.x, l.t.y); ctx.stroke();\n"
            "        ctx.setLineDash([]);\n"
            "        // arrowhead at the target, tangent to the curve (control point → end) — only when\n"
            "        // zoomed in enough to read (same gate as labels). Size stays fixed rather than\n"
            "        // tracking edgeWidth — the 0.75-4px range is narrow enough that a scaled triangle\n"
            "        // risks overlapping into a blob on the thickest edges; fixed is the safer default\n"
            "        // without a rendered visual to tune against.\n"
            "        if (l.arrow && (!useCam || cam.k >= 1)) {\n"
            "          const dx = l.t.x - cpx, dy = l.t.y - cpy;\n"
            "          const d = Math.sqrt(dx * dx + dy * dy) || 1;\n"
            "          const ux = dx / d, uy = dy / d, px = -uy, py = ux;\n"
            "          // owner 2026-08-27: direction was not readable on the system view — a 7px\n"
            "          // triangle under a 0.32 stroke alpha is a smudge. Bigger, plus its own alpha\n"
            "          // floor below, so the arrow reads even where the stroke is deliberately faint.\n"
            "          const tip = l.t.r + 2, back = 12, spread = 6.5;\n"
            "          const ax = l.t.x - ux * tip, ay = l.t.y - uy * tip;\n"
            "          const bx = ax - ux * back, by = ay - uy * back;\n"
            "          ctx.save();\n"
            "          ctx.globalAlpha = Math.max(ctx.globalAlpha, (gaHL || hov) && !active ? 0.08 : 0.85);\n"
            "          ctx.fillStyle = col;\n"
            "          ctx.beginPath();\n"
            "          ctx.moveTo(ax, ay);\n"
            "          ctx.lineTo(bx + px * spread, by + py * spread);\n"
            "          ctx.lineTo(bx - px * spread, by - py * spread);\n"
            "          ctx.closePath(); ctx.fill();\n"
            "          ctx.restore();\n"
            "        }\n"
            "        if (l.label && (opts.forceLabels || !useCam || cam.k >= 1)) {  // map-prototype patch: edge label — collected here, drawn in the label-collision pass\n"
            "          const mx = 0.25 * l.s.x + 0.5 * cpx + 0.25 * l.t.x, my = 0.25 * l.s.y + 0.5 * cpy + 0.25 * l.t.y;\n"
            "          labelCandidates.push({\n"
            "            text: l.label, x: mx, y: my,\n"
            "            font: `${useCam ? 11 / cam.k : 11}px 'Spline Sans Mono', monospace`,\n"
            "            color: col, alpha: (gaHL || hov) ? (active ? 0.9 : 0.08) : 0.75,\n"
            "            kind: 'edge', priority: (l.weight || 1) - (l.inf ? 0.5 : 0),  // named generics yield to real actions\n"
            "          });\n"
            "        }\n"
            "      }\n"
            "      ctx.setLineDash([]);\n"
            "      ctx.globalAlpha = 1;\n"
            "\n"
            "      ",
        ),
        (
            # Node label — collected (A.1), not drawn inline; the block right after nodes.forEach
            # closes (still inside draw()) does ONE priority-sorted pass over every candidate
            # (nodes before edges; among nodes a collapsed `system` always wins, then a `bridge`,
            # then bigger size — owner addenda 2026-08-27) with a halo (A.2), skipping on overlap.
            "        if (showLabel) {\n"
            "          const ls = (useCam ? 11 / cam.k : 11);\n"
            "          ctx.globalAlpha = dim ? 0.18 : (mode === 'hero' && n !== hov ? 0.6 : 0.92);\n"
            "          ctx.fillStyle = '#cfcfd6';\n"
            "          ctx.font = `${n === hov ? '600 ' : ''}${ls}px 'Spline Sans Mono', monospace`;\n"
            "          ctx.textAlign = 'center';\n"
            "          ctx.fillText(n.label, n.x, n.y + r + ls + 2);\n"
            "        }\n"
            "        ctx.globalAlpha = 1;\n"
            "      });\n"
            "    }",
            "        if (showLabel) {\n"
            "          const ls = (useCam ? 12 / cam.k : 12);  // map-prototype patch: bigger node label (adendo 2026-08-27)\n"
            "          labelCandidates.push({  // map-prototype patch: label collision — collect, draw after sort\n"
            "            text: n.label, x: n.x, y: n.y + r + ls + 2,\n"
            "            font: `${n === hov ? '600 ' : ''}${ls}px 'Spline Sans Mono', monospace`,\n"
            "            color: '#cfcfd6', alpha: dim ? 0.18 : (mode === 'hero' && n !== hov ? 0.6 : 0.92),\n"
            # anchors (pinned start/finish) sit just under systems and bridges: they are the map's
            # frame of reference, so a plain node's label must never bury them (measured: the two
            # finish anchors lost their labels to ordinary neighbours once flow bias was on).
            "            kind: 'node', priority: n.system ? 999 : (n.bridge ? 99 : (n.pin ? 89 : (n.size || 1))),\n"
            "          });\n"
            "        }\n"
            "        ctx.globalAlpha = 1;\n"
            "      });\n"
            "      {  // map-prototype patch: label collision — node>edge; among nodes: system>bridge>bigger size/weight; halo behind text; overlap = silently skipped (hover still works via pick())\n"
            "        const placed = [];\n"
            "        const items = labelCandidates.slice().sort((a, b) => {\n"
            "          if (a.kind !== b.kind) return a.kind === 'node' ? -1 : 1;\n"
            "          return b.priority - a.priority;\n"
            "        });\n"
            "        for (const c of items) {\n"
            "          ctx.font = c.font;\n"
            "          const w = ctx.measureText(c.text).width;\n"
            "          const pad = 3, rx = c.x - w / 2 - pad, ry = c.y - 9 - pad, rw = w + pad * 2, rh = 14 + pad * 2;\n"
            "          let hit = false;\n"
            "          for (const p of placed) {\n"
            "            if (rx < p.x + p.w && rx + rw > p.x && ry < p.y + p.h && ry + rh > p.y) { hit = true; break; }\n"
            "          }\n"
            "          if (hit) continue;\n"
            "          placed.push({ x: rx, y: ry, w: rw, h: rh });\n"
            "          ctx.globalAlpha = 1;\n"
            "          ctx.fillStyle = 'rgba(11,11,15,0.78)';\n"
            "          ctx.fillRect(rx, ry, rw, rh);\n"
            "          ctx.globalAlpha = c.alpha;\n"
            "          ctx.fillStyle = c.color;\n"
            "          ctx.textAlign = 'center';\n"
            "          ctx.fillText(c.text, c.x, c.y);\n"
            "        }\n"
            "        ctx.globalAlpha = 1;\n"
            "      }\n"
            "    }",
        ),
        (
            "  function edgeWidth(w, wMin, wMax) {\n"
            "    w = w || 1;\n"
            "    if (!(wMax > wMin)) return (EDGE_PX_MIN + EDGE_PX_MAX) / 2;  // every edge same weight\n"
            "    const t = (Math.sqrt(w) - Math.sqrt(wMin)) / (Math.sqrt(wMax) - Math.sqrt(wMin));\n"
            "    return EDGE_PX_MIN + Math.max(0, Math.min(1, t)) * (EDGE_PX_MAX - EDGE_PX_MIN);\n"
            "  }",
            "  function edgeWidth(w, wMin, wMax) {\n"
            "    w = w || 1;\n"
            "    if (!(wMax > wMin)) return (EDGE_PX_MIN + EDGE_PX_MAX) / 2;  // every edge same weight\n"
            "    const t = (Math.sqrt(w) - Math.sqrt(wMin)) / (Math.sqrt(wMax) - Math.sqrt(wMin));\n"
            "    return EDGE_PX_MIN + Math.max(0, Math.min(1, t)) * (EDGE_PX_MAX - EDGE_PX_MIN);\n"
            "  }\n"
            "\n"
            "  function convexHull(points) {  // map-prototype patch: system regions (C) — Andrew's monotone chain\n"
            "    const pts = points.slice().sort((a, b) => a.x - b.x || a.y - b.y);\n"
            "    if (pts.length < 3) return pts;\n"
            "    const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);\n"
            "    const lower = [];\n"
            "    for (const p of pts) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop(); lower.push(p); }\n"
            "    const upper = [];\n"
            "    for (let i = pts.length - 1; i >= 0; i--) { const p = pts[i]; while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop(); upper.push(p); }\n"
            "    lower.pop(); upper.pop();\n"
            "    return lower.concat(upper);\n"
            "  }",
        ),
        (
            # map-prototype patch: fit margin aware of label width + node radius (#2 clipping fix,
            # owner screenshot review 2026-08-27; magnitude corrected same day — the first pass
            # stacked the old flat 70 UNDER a doubled label reserve, which over-margined every
            # variant into a thumbnail) — the stock `fitTarget()` only bounds by node x/y, so a
            # wide label ("Finalização (oponente)") or a big anchor/system node past the bbox edge
            # draws off-canvas. Labels render at a FIXED ~12px screen size regardless of zoom
            # (`ls = useCam?12/cam.k:12` in the label pass below), so `ctx.measureText` at that
            # same font gives an honest SCREEN-space half-width; `pad` is subtracted from W/H
            # BEFORE dividing by spanX/spanY to get `k`, so it reserves exactly `pad` screen px of
            # margin regardless of zoom — that mechanism was already correct, only the VALUE was
            # too big. `2 * (halfLabel + r + slack)` alone already reserves the worst-case overhang
            # on BOTH sides of the bbox (no extra flat constant needed on top); slack shrunk from
            # 16 to 8 (one screen-space cushion, not stacked with a second implicit one).
            "    function fitTarget() {\n"
            "      if (!nodes.length) return { x: 0, y: 0, k: 1 };\n"
            "      let mnX = 1e9, mnY = 1e9, mxX = -1e9, mxY = -1e9;\n"
            "      for (const n of nodes) {\n"
            "        mnX = Math.min(mnX, n.x); mxX = Math.max(mxX, n.x);\n"
            "        mnY = Math.min(mnY, n.y); mxY = Math.max(mxY, n.y);\n"
            "      }\n"
            "      const pad = 70, spanX = (mxX - mnX) || 1, spanY = (mxY - mnY) || 1;\n"
            "      const k = Math.max(0.25, Math.min(1.3, Math.min((W - pad) / spanX, (H - pad) / spanY)));\n"
            "      return { k, x: W / 2 - (mnX + mxX) / 2 * k, y: H / 2 - (mnY + mxY) / 2 * k };\n"
            "    }",
            "    function fitTarget() {\n"
            "      if (!nodes.length) return { x: 0, y: 0, k: 1 };\n"
            "      let mnX = 1e9, mnY = 1e9, mxX = -1e9, mxY = -1e9;\n"
            "      for (const n of nodes) {\n"
            "        mnX = Math.min(mnX, n.x); mxX = Math.max(mxX, n.x);\n"
            "        mnY = Math.min(mnY, n.y); mxY = Math.max(mxY, n.y);\n"
            "      }\n"
            "      ctx.font = \"12px 'Spline Sans Mono', monospace\";  // map-prototype patch: label/radius-aware fit margin (#2)\n"
            "      let maxHalfLabel = 0, maxR = 0;\n"
            "      for (const n of nodes) {\n"
            "        if (n.label) maxHalfLabel = Math.max(maxHalfLabel, ctx.measureText(n.label).width / 2);\n"
            "        maxR = Math.max(maxR, n.r || 5);\n"
            "      }\n"
            "      const pad = 2 * (maxHalfLabel + maxR + 8), spanX = (mxX - mnX) || 1, spanY = (mxY - mnY) || 1;\n"
            "      // map-prototype patch: `opts.minZoom` — the 0.25 floor is right for a force graph\n"
            "      // that can always settle tighter, and wrong for a FULLY POSITIONED one (variant 13),\n"
            "      // where refusing to zoom out just clips the frame off the side of a phone.\n"
            "      const kMin = opts.minZoom != null ? opts.minZoom : 0.25;\n"
            "      const k = Math.max(kMin, Math.min(1.3, Math.min((W - pad) / spanX, (H - pad) / spanY)));\n"
            "      return { k, x: W / 2 - (mnX + mxX) / 2 * k, y: H / 2 - (mnY + mxY) / 2 * k };\n"
            "    }",
        ),
        (
            # 11/12 fix (owner-reported, ONLY patch that touches an already-approved page's look —
            # screenshot 4 and 8 before/after): the arrowhead gate used to hide on cam.k<1, which
            # is every small graph this module renders after fitTarget() zooms to fit. Respect
            # forceLabels the same way the label pass already does.
            "        if (l.arrow && (!useCam || cam.k >= 1)) {\n",
            "        if (l.arrow && (opts.forceLabels || !useCam || cam.k >= 1)) {  // map-prototype patch: arrowhead gate now respects forceLabels\n",
        ),
        (
            # 11/12's own dash vocabulary — a stub boundary link ([2,3]) is neither the low-
            # success/handover long dash ([5,5]) nor the contested short dash ([3,4]); `l.dash`
            # (an explicit array) wins when present, everything else keeps its old behaviour.
            "        ctx.setLineDash(l.dashed ? [5, 5] : (contested ? [3, 4] : []));  // low-success / handover dashed\n",
            "        ctx.setLineDash(l.dash || (l.dashed ? [5, 5] : (contested ? [3, 4] : [])));  // map-prototype patch: per-link custom dash (11/12 stub links, [2,3])\n",
        ),
        (
            # 11/12's flow-bias layout (`opts.flowBias`, default undefined -> falsy -> every other
            # variant byte-identical): push the target +x and the source -x, scaled by the link's
            # own weight, BEFORE the node loop integrates positions below — a pinned node zeroes
            # its own vx there regardless, so the anchor cross never moves.
            "        l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;\n      }\n",
            "        l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;\n"
            "        if (opts.flowBias && l.arrow) {  // map-prototype patch: flow-bias layout (11/12)\n"
            "          const fb = opts.flowBias * (l.weight || 1) * alpha;\n"
            "          l.t.vx += fb; l.s.vx -= fb;\n"
            "        }\n"
            "      }\n",
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

    # 9 — same systems, expansion in place (multi-expand, chip to recolher)
    metrics["variants"]["9-sistemas-expande-in-place"] = _render_variant9(out, states4, edges4, handovers4)

    # 10 — density-gate experiment made visible (owner adendo): same systems view, one button
    # per inference policy, at the min_support chosen for 8/9.
    metrics["gating_by_policy"] = _render_variant10(out, states4, edges4, handovers4)

    # 11/12 — separate systems view: global (every node/edge, systems collapsed, ALL bridges) +
    # a per-system boundary+stub view. 11 = locked combo, no controls; 12 = the same page, live.
    metrics["variants"]["11-sistemas-vista-separada"] = _render_variant11(out, agg)
    metrics["variants"]["12-sistemas-vista-separada-seletiva"] = _render_variant12(out, agg)

    # 13 — Phase 4: render paths -> bundled visual graph. Its own layer, not a re-skin of the
    # others: nodes are POINTS (states + branch/merge artefacts), links are SEGMENTS of shared
    # ink, and every position is computed here (`flow_layout`) instead of simulated.
    metrics["variants"]["13-caminhos"] = _render_variant13(out, agg, bundle)

    # 14 — 13's paths with systems collapsible in place (owner request 2026-09-01): same layer,
    # same layout engine, systems fold/expand instead of the plain hide-as-stub 13's own scopes do.
    metrics["variants"]["14-caminhos-sistemas"] = _render_variant14(out, agg, bundle)

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
    pct_inferred: float | None
    if edges:
        pct_inferred = _pct(sum(1 for e in edges if e.get("inferred")), len(edges))
    else:
        pct_inferred = 0.0 if edges is not None else None
    return {
        "nodes": n_nodes,
        "edges": n_edges,
        "edges_per_node": round(n_edges / n_nodes, 2) if n_nodes else 0.0,
        "pct_inferred_edges": pct_inferred,
        "partner_elements": partner_elements,
        "handover_links": handover_links,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Render 9 actions/states map prototypes from a user bundle")
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = ap.parse_args()

    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    metrics = render_all(bundle, args.out)

    print(f"{'variant':32} {'nodes':>6} {'edges':>6} {'e/n':>6} {'%inf':>6} {'partner':>8} {'handover':>9}")
    for name, m in metrics["variants"].items():
        pct = m.get("pct_inferred_edges")
        e_n = m.get("edges_per_node", "—")
        partner = m.get("partner_elements", "—")
        print(f"{name:32} {m['nodes']:>6} {m['edges']:>6} {e_n!s:>6} "
              f"{'—' if pct is None else pct:>6} {partner!s:>8} {m['handover_links']:>9}")
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
