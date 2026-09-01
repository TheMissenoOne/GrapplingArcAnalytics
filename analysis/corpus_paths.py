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

**§5d — dynamic variant budget + category folding** (``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md``
§FASE 5d). ``min_count`` (a static drop) is gone: every payload now ranks its own variants by
``(support, strength)`` and keeps the top ``max_variants`` (~60) drawn individually; the rest are
never dropped, only FOLDED — grouped by family (source, target, actor) and, within a family, by
whether every action in the variant shares one category ("Submissions ×4") or not ("Other paths
×N"). A fold is render-only: it is ONE additional synthetic single-action path fed through the
same bundler/layout as everything else, so it gets a real stroke and position for free, and its
own folded variants ride along unabridged in the additive ``folded`` payload field. A payload
with fewer variants than the budget folds nothing — the mechanism is inert until there is volume
to manage, which is why a single-bout breakdown almost never folds.
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
    "OCEAN_FOLD_GROUP_BUDGET",
    "PATH_VARIANT_BUDGET",
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

# §5d — how many variants a payload draws individually before the rest fold into category
# strokes. ~60 is the owner's number (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md §FASE 5d);
# below it the mechanism never fires, which is the whole point for a one-bout breakdown.
PATH_VARIANT_BUDGET = 60

# Ocean's SECOND ceiling (docs §12, 2026-09-01): 60 kept variants + one stroke per fold GROUP
# is still a novelo — the full corpus folds into 877 groups, more strokes (937) than the OLD
# static gate it replaced (396). Measured over the real corpus: 340 caps `stats.segments` at
# ~407, close to the owner's ~400 target (`export/site_data.py` measurement, 2026-09-01). Only
# the ocean passes this; a dossier/breakdown never crosses it (`max_fold_groups=None` default).
OCEAN_FOLD_GROUP_BUDGET = 340

# A folded stroke's synthetic single action key — unique per fold group by construction (each
# carries its own bucket index), so it can never merge with a real action's segment and never
# needs to pass the corpus/generic-vocabulary check `scripts/check_site_bundle.py` runs (the
# fold link's `actions` stays empty — nothing here is offered as a node_key).
_FOLD_PREFIX = "$fold:"

# English plural for a folded category's label ("Submissions ×4") — site copy is English
# (GrapplingArc AGENTS.md rule 4); `None` (mixed-type chain) falls back to "Other paths ×N".
_CAT_PLURAL = {
    "guard": "Guard Pulls", "pass": "Guard Passes", "sweep": "Sweeps",
    "takedown": "Takedowns", "control": "Control Changes", "submission": "Submissions",
    "escape": "Escapes", "transition": "Transitions",
}

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


def _uniform_category(types: Sequence[str]) -> str | None:
    """The one category every action of a variant shares, or ``None`` when the chain mixes
    types — §5d's "cadeias mistas" case, which folds into "Other paths" instead of a named
    category. An empty sequence (should not happen — a variant always has >=1 action) also
    reads as mixed rather than guessing a category with nothing behind it."""
    cats = {_cat_of(t) for t in types}
    return next(iter(cats)) if len(cats) == 1 else None


def _join_labels_compressed(labels: Sequence[str]) -> str:
    """§5d item 3 — consecutive repeats of the SAME action label compress to ``"Triangle ×3"``.
    Display only: the payload's own ``actions[]`` stays exploded (one entry per action, §1
    invariant 3 — nothing here depends on position), this only shortens the joined string a
    stroke's tooltip/label shows. A run of length 1 is unchanged, so a chain with no repeats
    joins exactly as before."""
    out: list[str] = []
    i, n = 0, len(labels)
    while i < n:
        j = i
        while j < n and labels[j] == labels[i]:
            j += 1
        run = j - i
        out.append(labels[i] if run == 1 else f"{labels[i]} ×{run}")
        i = j
    return " → ".join(out)


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

    States key on ``(node_key, fighter)``. A VARIANT keys on ``(source, target, OBSERVED
    subsequence, actor)`` — §13.1-13.3 of the contract doc: the inferred fill of a gap is
    annotation of that occurrence, never identity, so two edges whose observed actions agree
    (in order) share a variant no matter where an inferred action landed among them. An
    occurrence with NO observed action at all never becomes its own variant: it resolves into
    ``unresolved`` (a counter on the RELATION ``(source, target, actor)``, §13.4) when the
    relation already has a concrete variant, or folds into one placeholder edge — a disguised
    ``unresolved`` bucket, §13.3 — when it does not. ``finalize()`` makes that call once, after
    every occurrence has been seen, so the decision does not depend on bout order."""

    def __init__(self, *, collapse_actors: bool = False) -> None:
        self.collapse_actors = collapse_actors
        self.states: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, tuple[str, ...], str], dict[str, Any]] = {}
        # One representative ChainEdge per key — path_metrics is keyed on a ChainEdge, and every
        # occurrence under one key carries the same action sequence/terminality by construction.
        self.edge_sample: dict[tuple[str, str, tuple[str, ...], str], ChainEdge] = {}
        self.display_labels: dict[str, str] = {}
        # Wholly-inferred occurrences, buffered until `finalize()` — see the class docstring.
        self._ghosts: list[dict[str, Any]] = []
        # (source, target, actor) -> {"count": int, "guesses": {action_key: count}}. Populated
        # ONLY for a relation that already carries >=1 concrete variant — a relation with none
        # gets its placeholder edge instead (`finalize()`).
        self.unresolved: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._finalized = False

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
        observed_seq = tuple(a.key for a in edge.actions if not a.inferred)
        action_labels = tuple(
            self.display_labels.get(a.key) or _generic_action_label(a.key) or a.label
            for a in edge.actions
        )
        action_inferred = tuple(a.inferred for a in edge.actions)
        action_types = tuple(a.type for a in edge.actions)  # §5d category folding
        # Video seek (breakdown pages): an ACTION is what happened at a moment, and in this
        # model the action rides the edge — so the timestamp does too. FIRST occurrence wins,
        # same convention as the display label.
        action_ts = tuple(
            (ts_of(a.source_event_index)
             if ts_of is not None and a.source_event_index is not None else None)
            for a in edge.actions
        )
        if not observed_seq:
            # §13.3: wholly inferred. Never its own variant — buffered, resolved by `finalize()`
            # once every occurrence has been seen (§2 already proves the empty-buffer case
            # yields exactly one action, so `action_seq[0]` is the whole guess).
            self._ghosts.append({
                "family": (source, target, actor), "guess": action_seq[0] if action_seq else "",
                "edge": edge, "actions": action_seq, "action_labels": action_labels,
                "action_inferred": action_inferred, "action_ts": action_ts,
                "action_types": action_types,
            })
            return
        key = (source, target, observed_seq, actor)
        row = self.edges.get(key)
        if row is None:
            self.edges[key] = {
                "source": source,
                "target": target,
                "actor": actor,
                "count": 1,
                "actions": action_seq,
                "action_labels": action_labels,
                "action_inferred": action_inferred,
                "action_ts": action_ts,
                "action_types": action_types,
            }
            self.edge_sample[key] = edge
        else:
            row["count"] += 1

    def finalize(self) -> None:
        """§13.3, the order-independent half: decide, for every buffered wholly-inferred
        occurrence, whether it lands as `unresolved` context on an existing relation or as one
        folded placeholder edge (a relation with no concrete variant at all — the placeholder
        IS `unresolved`, just disguised as a drawable edge so the family still renders
        something). Idempotent; safe to call once after every `add_edge`."""
        if self._finalized:
            return
        self._finalized = True
        concrete_families = {(k[0], k[1], k[3]) for k in self.edges}
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for ghost in self._ghosts:
            grouped.setdefault(ghost["family"], []).append(ghost)
        for family, occurrences in grouped.items():
            guesses: dict[str, int] = {}
            for occ in occurrences:
                guesses[occ["guess"]] = guesses.get(occ["guess"], 0) + 1
            if family in concrete_families:
                self.unresolved[family] = {
                    "count": len(occurrences), "guesses": dict(sorted(guesses.items())),
                }
                continue
            # No concrete variant anywhere for this relation — fold every guess into ONE
            # placeholder edge. Majority guess wins the representative action/label, tied
            # alphabetically for determinism; first occurrence carrying it wins the sample
            # (same "first seen wins" convention as everywhere else in this module).
            top_guess = min(guesses, key=lambda g: (-guesses[g], g))
            rep = next(o for o in occurrences if o["guess"] == top_guess)
            source, target, actor = family
            placeholder_key = (source, target, (top_guess,), actor)
            self.edges[placeholder_key] = {
                "source": source, "target": target, "actor": actor,
                "count": len(occurrences), "actions": rep["actions"],
                "action_labels": rep["action_labels"], "action_inferred": rep["action_inferred"],
                "action_ts": rep["action_ts"], "unresolved_guesses": dict(sorted(guesses.items())),
                "action_types": rep["action_types"],
            }
            self.edge_sample[placeholder_key] = rep["edge"]
        self._ghosts = []


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
    agg.finalize()
    return agg


def render_paths(agg: PathAggregate) -> list[RenderPath]:
    """Layer 2 — one ``RenderPath`` per aggregated occurrence. Ids come from the sorted
    aggregation key, never from dict order, so two runs over the same corpus agree."""
    collapse = agg.collapse_actors
    out: list[RenderPath] = []
    for i, key in enumerate(sorted(agg.edges)):
        source, target, _observed_key, actor = key
        row = agg.edges[key]
        out.append(
            RenderPath(
                path_id=f"p{i}",
                source=_qid(actor, source, collapse=collapse),
                target=_qid(actor, target, collapse=collapse),
                # The FULL action path (observed + inferred), never the variant's identity key
                # (which is the OBSERVED-only subsequence, §13.2) — the drawing/bundling needs
                # every action an occurrence actually carries.
                actions=row["actions"],
                actor=actor,
                count=row["count"],
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
    # §13.4: an unresolved occurrence propagates FAMILY support even though it never becomes a
    # variant's own `count` — it did happen, between the same two states.
    for rel, info in agg.unresolved.items():
        support[rel] = support.get(rel, 0) + info["count"]
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


def _rank_key(path: RenderPath, metrics: PathMetrics) -> tuple[int, float, int, str]:
    """§5d ranking — support first (the family's own traffic), then strength (an unrated
    variant, ``None``, ranks behind a rated one at the same support), then the variant's own
    occurrence count; ``path_id`` last only to make ties reproducible, never to break them on
    its own."""
    strength = metrics.strength if metrics.strength is not None else -1.0
    return (-metrics.support, -strength, -path.count, path.path_id)


def _variant_row(
    path: RenderPath, metrics: PathMetrics, labels: Mapping[str, str]
) -> dict[str, Any]:
    """One folded variant's compact summary — enough for a client to list it, unabridged, once
    its category stroke is expanded. Local `strength` avoids a `metrics_by_path[...]` re-lookup
    mypy cannot narrow through the `is None` check twice."""
    strength = metrics.strength
    return {
        "id": path.path_id, "count": path.count,
        "actions": [labels.get(k, k) for k in path.actions],
        "length": metrics.length, "support": metrics.support,
        "strength": None if strength is None else round(strength, 1),
    }


def _fold_overflow(
    overflow: Sequence[RenderPath],
    family_of: Mapping[str, tuple[str, str, str]],
    types_of: Mapping[str, tuple[str, ...]],
    metrics_by_path: Mapping[str, PathMetrics],
    labels: Mapping[str, str],
    max_fold_groups: int | None = None,
) -> tuple[list[RenderPath], dict[str, dict[str, Any]]]:
    """§5d, items 1-2 — group every budget-cut variant by family (source, target, actor), then
    by whether all its own actions share one category. Each group becomes ONE synthetic
    single-action ``RenderPath`` (fed through the same bundler/layout as everything else, so it
    gets a real stroke and position for free) plus a metadata row carrying every folded variant
    UNABRIDGED — folding is render-only (§13's own rule: agreggation never touches topology),
    nothing here is dropped, and nothing here changes ``support``/rating (those are read straight
    off ``metrics_by_path``, computed before any budget was applied).

    ``max_fold_groups`` — the Ocean's SECOND ceiling (docs §12, 2026-09-01): even with every
    variant folded, one group per fold bucket still means as many strokes as there are buckets
    (877 measured over the full corpus — a novelo again). Ranked by each bucket's own total
    occurrence count, only the top ``max_fold_groups`` get a synthetic ``RenderPath`` (``drawn``).
    The rest still get a full ``meta`` row (``drawn=False``) — never dropped, just not stroked;
    a client reveals them on demand from the payload it already has. ``None`` (the dossier/
    breakdown default) draws every group, unchanged from before this ceiling existed."""
    buckets: dict[tuple[str, str, str, str | None], list[RenderPath]] = {}
    for p in overflow:
        source, target, actor = family_of[p.path_id]
        cat = _uniform_category(types_of[p.path_id])
        buckets.setdefault((source, target, actor, cat), []).append(p)

    ranked = sorted(buckets, key=lambda k: (-sum(p.count for p in buckets[k]), repr(k)))
    drawn_keys = set(ranked) if max_fold_groups is None else set(ranked[:max_fold_groups])

    synth: list[RenderPath] = []
    meta: dict[str, dict[str, Any]] = {}
    for i, bkey in enumerate(sorted(buckets, key=repr)):
        group = sorted(buckets[bkey], key=lambda p: p.path_id)
        _source, _target, actor, cat = bkey
        total = sum(p.count for p in group)
        plural = _CAT_PLURAL.get(cat, "Other paths") if cat else "Other paths"
        fold_id = f"{_FOLD_PREFIX}{i}"
        drawn = bkey in drawn_keys
        if drawn:
            synth.append(RenderPath(
                path_id=fold_id, source=group[0].source, target=group[0].target,
                actions=(fold_id,), actor=actor, count=total,
            ))
        meta[fold_id] = {
            "id": fold_id, "source": group[0].source, "target": group[0].target,
            "category": cat, "label": f"{plural} ×{len(group)}", "count": total,
            "variantCount": len(group), "actor": actor, "drawn": drawn,
            "variants": [_variant_row(p, metrics_by_path[p.path_id], labels) for p in group],
        }
    return synth, meta


def path_payload(
    agg: PathAggregate,
    *,
    structure: str = DEFAULT_ANCHOR_STRUCTURE,
    rating_of: Callable[[str], float | None] | None = None,
    max_variants: int = PATH_VARIANT_BUDGET,
    max_fold_groups: int | None = None,
) -> dict[str, Any]:
    """Layers 3 + 4 — the bundled graph, laid out, as the site's ``{nodes, links, paths,
    folded}``.

    §5d replaced the old static ``min_count`` drop with a ranked budget: every variant is
    ordered by ``(support, strength)`` (``_rank_key``) and the top ``max_variants`` (~60) draw
    individually — the whole public corpus still bundles in under a second regardless of the
    budget (measured), so this stays a legibility gate, not a perf one; 2 370 individual strokes
    on one canvas is the hairball ``userDecisionFlow.ts:32-45`` already measured. What changed is
    what happens to the rest: nothing is dropped, every variant beyond the budget folds into a
    category stroke instead (``_fold_overflow``) — additive ``folded`` field below. A payload
    with fewer variants than the budget folds nothing, which is why a single-bout breakdown
    almost never does. Deterministic: the ranking key is total over the corpus, ties break on
    ``path_id``.

    ``max_fold_groups`` (docs §12, 2026-09-01) — the Ocean's SECOND ceiling. Folding fixes the
    ethics (grouping by family+category instead of a raw drop) but not the density: the full
    corpus folds into 877 groups, and 60 kept variants + 877 fold strokes is still a novelo
    (measured, more strokes than the OLD static gate it replaced). Only the top
    ``max_fold_groups`` fold groups (ranked by their own occurrence count) draw a stroke; the
    rest still ride in ``folded`` (``drawn=False``) and roll up into ``stats.undrawn`` — never
    dropped, just not stroked, for a client to reveal on demand. ``None`` (the dossier/breakdown
    default, and every caller before this ceiling existed) draws every fold group.
    """
    unified = bool(ANCHOR_STRUCTURES[structure]["unified_finish"])
    collapse = agg.collapse_actors
    metrics_by_path = _metrics_by_path(agg, rating_of)
    labels = {
        key: label
        for row in agg.edges.values()
        for key, label in zip(row["actions"], row["action_labels"], strict=True)
    }
    family_of = {f"p{i}": (key[0], key[1], key[3]) for i, key in enumerate(sorted(agg.edges))}
    types_of = {
        f"p{i}": agg.edges[key]["action_types"] for i, key in enumerate(sorted(agg.edges))
    }

    all_paths = render_paths(agg)

    opp_finish = _qid("b", _FINISH_KEY, collapse=collapse)
    if unified and opp_finish != _FINISH_KEY:
        all_paths = [
            RenderPath(
                path_id=p.path_id,
                source=_FINISH_KEY if p.source == opp_finish else p.source,
                target=_FINISH_KEY if p.target == opp_finish else p.target,
                actions=p.actions,
                actor=p.actor,
                count=p.count,
            )
            for p in all_paths
        ]

    fold_meta: dict[str, dict[str, Any]] = {}
    if len(all_paths) <= max_variants:
        paths, bundle_input = all_paths, all_paths
    else:
        ranked = sorted(all_paths, key=lambda p: _rank_key(p, metrics_by_path[p.path_id]))
        keep_ids = {p.path_id for p in ranked[:max_variants]}
        paths = [p for p in all_paths if p.path_id in keep_ids]
        overflow = [p for p in all_paths if p.path_id not in keep_ids]
        synth_paths, fold_meta = _fold_overflow(
            overflow, family_of, types_of, metrics_by_path, labels,
            max_fold_groups=max_fold_groups,
        )
        bundle_input = paths + synth_paths

    bundled = bundle_paths(bundle_input)
    count_of = {p.path_id: p.count for p in bundle_input}
    actor_of = {p.path_id: p.actor for p in bundle_input}
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
        fold = fold_meta.get(seg.actions[0]) if len(seg.actions) == 1 else None
        label_len[seg.id] = len(fold["label"]) if fold else len(
            _join_labels_compressed([labels.get(k, k) for k in seg.actions])
        )

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
            # A unified finish draws as a SOLID yellow disc (owner, 2026-09-01) — the arrowhead
            # arriving already carries the actor's colour, so who finished is said by the
            # stroke, not by the node. `_FINISH_COLOR` above already applies unconditionally.
            if node_key == _FINISH_KEY and not unified and side == "b":
                node["label"] = "Finish (opponent)"
        nodes.append(node)

    links: list[dict[str, Any]] = []
    for seg in bundled.segments:
        weight = _segment_weight(seg, count_of)
        fold = fold_meta.get(seg.actions[0]) if len(seg.actions) == 1 else None
        link: dict[str, Any]
        if fold is not None:
            # §5d — a folded category stroke. `actions` stays EMPTY on purpose:
            # `scripts/check_site_bundle.py` walks every link's `actions[].key` against the
            # canonical corpus, and the synthetic fold id is not a real node_key — offering it
            # there would false-flag as stale. The folded variants ride, unabridged, in
            # `folded.variants` for the client to reveal on selection (§5d item 2).
            link = {
                "id": seg.id, "from": seg.from_point, "to": seg.to_point,
                "weight": _clamp3(weight), "count": weight, "arrow": True,
                "actions": [], "label": fold["label"], "pathIds": sorted(seg.path_ids),
                "shared": False, "fighter": fold["actor"], "folded": fold,
            }
        else:
            acts = [
                {"key": k, "label": labels.get(k, k), "inferred": k in ghosts}
                for k in seg.actions
            ]
            for act in acts:
                ts = ts_by_action.get(act["key"])
                if ts is not None:
                    act["ts"] = ts
            fighters = {actor_of[pid] for pid in seg.path_ids}
            link = {
                "id": seg.id, "from": seg.from_point, "to": seg.to_point,
                "weight": _clamp3(weight), "count": weight, "arrow": True,
                "actions": acts,
                # §5d item 3: consecutive repeats of the same action compress ("Triangle ×3");
                # a chain with no repeats joins exactly as before.
                "label": _join_labels_compressed([a["label"] for a in acts]),
                "pathIds": sorted(seg.path_ids),
                # first moment on this stroke — what a click seeks the bout video to
                **({"ts": next((a["ts"] for a in acts if "ts" in a), None)}
                   if any("ts" in a for a in acts) else {}),
                "shared": len(seg.path_ids) > 1,
                "fighter": next(iter(sorted(fighters))) if len(fighters) == 1 else "x",
            }
            if all(a["inferred"] for a in acts):
                link["inf"] = True  # a named generic — still labelled, yields on a collision
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

    # §5d — the additive fold field. `fold_meta`'s `source`/`target` were qid strings (the
    # RenderPath's own, pre-layout); rewritten to POINT ids in place so `link["folded"]` (the
    # same dict, assigned above) and this top-level list agree with everything else the
    # payload addresses nodes by.
    for fm in fold_meta.values():
        fm["source"] = point_of_state.get(fm["source"], f"s:{fm['source']}")
        fm["target"] = point_of_state.get(fm["target"], f"s:{fm['target']}")
    folded_rows = [fold_meta[k] for k in sorted(fold_meta)]

    # §13.4/13.7: family context ONLY — never a variant's count, never rating. A relation with
    # no concrete variant at all has nothing to list here; its whole story is the disguised
    # placeholder edge already in `links` (`unresolved_guesses`).
    unresolved_rows: list[dict[str, Any]] = []
    for (source, target, actor), info in sorted(agg.unresolved.items()):
        src_qid = _qid(actor, source, collapse=collapse)
        tgt_qid = _qid(actor, target, collapse=collapse)
        variant_count = sum(
            1 for row in agg.edges.values()
            if row["source"] == source and row["target"] == target and row["actor"] == actor
        )
        top_guess = min(info["guesses"], key=lambda g: (-info["guesses"][g], g))
        unresolved_rows.append({
            "source": point_of_state.get(src_qid, f"s:{src_qid}"),
            "target": point_of_state.get(tgt_qid, f"s:{tgt_qid}"),
            "actor": actor, "count": info["count"], "variantCount": variant_count,
            "label": labels.get(top_guess, top_guess),
            "guesses": {labels.get(k, k): v for k, v in info["guesses"].items()},
        })

    # Ocean's second ceiling: fold groups the ranking cut from a stroke, still fully present in
    # `folded` (`drawn=False`) — this is the aggregate the meta line and the "not drawn" reveal
    # read, by GROUP count and by total OCCURRENCE count (never variant identity, same §13.4
    # convention as `unresolved`: context, not a redraw).
    undrawn_groups = [fm for fm in fold_meta.values() if not fm["drawn"]]

    return {
        "nodes": nodes,
        "links": links,
        "paths": path_rows,
        "unresolved": unresolved_rows,
        "folded": folded_rows,
        "stats": {
            "paths": len(paths),
            "variants": len(all_paths),
            "foldedGroups": len(fold_meta),
            "foldedVariants": sum(fm["variantCount"] for fm in fold_meta.values()),
            "undrawn": {
                "groups": len(undrawn_groups),
                "occurrences": sum(fm["count"] for fm in undrawn_groups),
            },
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
