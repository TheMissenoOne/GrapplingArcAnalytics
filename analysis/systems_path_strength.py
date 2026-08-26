"""Strength of a path *inside a system*, as an absorbing Markov chain.

A **system** here is a member set of the directed ActionFlow graph
(``analysis/transitions/build_graph.network_from_sequences``) — whatever detector produced
it. ``constellations.detect`` (the canonical one, ADR-08) and
``network_metrics.detect_communities`` (the older greedy one, what ``analysis/insights.py``
already reports as "game families") both hand back exactly ``list[str]`` member sets, so this
module takes the member set and never picks a detector. An athlete's own graph and the corpus
graph are likewise the same argument.

The **desired node** is the system's goal — its finishing node by default (highest-``occ``
``type == "submission"`` member), the weighted-degree hub when the system finishes nothing.
Callers may name any node instead.

## The chain

Over the system's members plus two absorbing states::

    DESIRED   the goal node, absorbing at 1
    EXIT      left the system, or was finished by the opponent — absorbing at 0

For a transient member ``n``::

    p_risk(n)          = risk(n) / denom(n)            → EXIT
    (1 - p_risk(n)) · P(n → j)                         → j     (j inside the system)
                                                       → EXIT  (j outside, or n is a dead end)

``P(n → j)`` is the row-normalised empirical kernel over ``n``'s *whole* out-neighbourhood —
the same kernel ``path_to_victory._kernel`` uses — so mass leaving the system is counted as
leaving, not renormalised away. That is the honesty the second absorbing state buys.

**Why risk is injected and reward is not.** ``network_from_sequences`` edges are
*within-actor*: the opponent finishing you is never an out-edge of your own node, so without
``p_risk`` the kernel would silently pretend a fighter always got another turn. Your own finish,
by contrast, IS an out-edge (``n → <submission>``) and is already in the kernel — adding
``reward`` on top would count it twice.

Absorption ``h(n) = P(reach DESIRED before EXIT | start at n)`` and expected steps to
absorption are solved by the same in-place iteration ``path_to_victory.path_to_victory`` uses,
not by inverting ``(I − Q)``: a system can contain a closed cycle with no leak, which makes
``I − Q`` singular. The iteration converges to the minimal non-negative solution and simply
reports ``expected_steps = None`` for the trapped nodes instead of raising.

## The composite

Three signals the owner named, one factor each, all in ``[0, 1]``::

    strength(n) = p_desired(n) · usage(n) · direction(n) · prize

    p_desired   absorption probability at the desired node (the chain above)
    usage       occ(n) / Σ occ(members) — this entry's share of the system's volume
    direction   kernel-mass-weighted mean of n's out-edge direction factors (below)
    prize       (1 + PtV(desired)) / 2 — the goal's own Path-to-Victory value, rescaled

``Σ usage = 1`` over the transient members, so the system's strength is *exactly* the sum of
its nodes' strengths — the row that contributes most is readable straight off the table::

    system_strength = Σ strength(n) = prize · E_usage[ p_desired · direction ]

For one concrete route ``π``, ``p_chain(π)`` (product of the chain's own step probabilities)
replaces ``p_desired`` and the path's weakest step supplies ``direction``::

    strength(π) = p_chain(π) · usage(π₀) · min_step_direction(π) · prize

``p_chain(π) ≤ p_desired(π₀)`` by construction — one simple route can never be worth more than
every route from the same start, cycles included.

**Score = PtV, not reward-risk.** ``reward_risk`` is the one-step special case of PtV
(``docs/path_to_victory.md``) and the chain above is already a multi-step object; multiplying
it by a one-step balance would re-charge the same events. PtV is the established multi-step
node value in this repo and is what the graph's own consumers use.

**Directionality never enters the probability.** ``edge_arrow``'s verdict is a *rendering*
contract shared char-for-char with the App (``services/directedEdges.ts``) — the constants are
imported, never redefined, and never touched. An absorption probability has to stay a
measurement of what the corpus did, so directionality is applied only in the composite:

* ``edge_arrow(f, r)`` false → undirected (too sparse to call, or a genuine two-way
  exchange) → **1.0**, neutral;
* true and ``f >= r`` → the majority direction, the arrow the data draws → **1.0**;
* true and ``f < r`` → the *minority* direction of an edge the data calls directed the other
  way — counterflow → ``COUNTERFLOW_FACTOR``.

Privacy class **A, public competition data**. Pure on a graph; the DB-backed caller
(``analysis/insights.py``) builds its graph from ``matches`` (``owner_kind='athlete'``
corpus). Nothing here reads a user graph or a session, and nothing here writes to ``site/``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

import networkx as nx

from analysis.network_metrics import edge_arrow
from analysis.path_to_victory import _rates, path_to_victory

_SUBMISSION = "submission"

#: A lone node is not a system — same rule ``analysis/systems.propose_from_network`` applies.
MIN_MEMBERS = 2

#: Occurrence floor on the desired node before the row may be *interpreted*. Same inferential
#: floor as ``network_metrics.reward_risk_ranking``/``reward_risk_with_ci`` (``min_occ=5``).
#: Read on ``occ``, not ``denom``: a landed finish ends the bout, so a submission node's
#: ``denom`` (appearances with a successor) is near-zero by construction and would gate out
#: precisely the nodes a system aims at.
MIN_DESIRED_OCC = 5

#: Credit kept by a step that runs *against* the arrow ``edge_arrow`` draws.
#: ponytail: half credit, an unfitted knob (keyword arg on every entry point). Ceiling =
#: calibrating it against held-out finish prediction, the way PoC-E4 swept γ; upgrade path is
#: that same harness, not a feel-based retune here.
COUNTERFLOW_FACTOR = 0.5

#: Route horizon — ``network_metrics.route_to_submission``'s ``max_steps``, and the depth at
#: which PtV's γ=0.8 has already decayed to 0.26.
MAX_PATH_LEN = 6
TOP_PATHS = 5

#: Simple-path enumeration is exponential in a dense subgraph, so each entry point gets a
#: capped share of the scan. PER ENTRY, not global: a global budget is spent entirely by
#: whichever member sorts first (measured 2026-08-25 on the corpus's 48-member ``Armbar``
#: community — every route reported started at ``50/50 Guard``, because the digit sorts before
#: every letter), which is a ranking artefact, not a finding.
#: ponytail: a flat cap. Ceiling = k-shortest-paths per entry if a dense system ever needs the
#: *best* 2000 rather than the first 2000.
_MAX_PATHS_PER_ENTRY = 2000

_MAX_ITER = 200          # same iteration budget/tolerance as path_to_victory.path_to_victory
_TOL = 1e-6


# ── the pieces ───────────────────────────────────────────────────────────────

def desired_node(g: nx.DiGraph, members: Sequence[str]) -> str | None:
    """The system's goal: its most-used submission member, else its weighted-degree hub.

    Deterministic — ties break on the label, the same rule as
    ``constellations.detect._hub``. ``None`` when no member is in ``g`` at all.
    """
    present = [n for n in sorted(set(members)) if n in g]
    if not present:
        return None
    subs = [n for n in present if g.nodes[n].get("type") == _SUBMISSION]
    pool = subs or present
    if subs:
        return sorted(pool, key=lambda n: (-float(g.nodes[n].get("occ", 0.0)), n))[0]
    return sorted(
        pool,
        key=lambda n: (
            -(g.in_degree(n, weight="weight") + g.out_degree(n, weight="weight")), n,
        ),
    )[0]


def direction_factor(
    g: nx.DiGraph, a: str, b: str, counterflow: float = COUNTERFLOW_FACTOR
) -> float:
    """How much strength the step ``a → b`` is allowed to carry, per the directed-edge
    contract (``network_metrics.edge_arrow`` — constants imported, never redefined).

    ``edge_arrow`` is annotated ``int`` because it was written for the raw ActionFlow graph,
    where weights are counts; it is pure numeric comparison, so a share-weighted graph
    (``transitions.normalize.athlete_balanced_category_graph``, every weight < 1) flows
    through it and lands on "below ``MIN_EDGE_ARROW``" → undirected → 1.0 everywhere. That is
    honest (a share carries no sample size) but uninformative: run this module on the
    unnormalised graph if you want direction to say anything.
    """
    f = g[a][b].get("weight", 0)
    r = g[b][a].get("weight", 0) if g.has_edge(b, a) else 0
    if not edge_arrow(f, r):
        return 1.0
    return 1.0 if f >= r else counterflow


def transition_rows(
    g: nx.DiGraph, members: Sequence[str], desired: str
) -> dict[str, dict[str, float]]:
    """Absorbing-chain rows: ``{transient n: {in-system target j: probability}}``.

    ``desired`` is absorbing and gets no row. Whatever a row does not account for
    (``1 − sum(row.values())``) is absorption at EXIT — mass that left the system, died on a
    dead end, or was the opponent's finish (``p_risk``).
    """
    inside = set(members) | {desired}
    rows: dict[str, dict[str, float]] = {}
    for n in sorted(inside):
        if n == desired or n not in g:
            continue
        # `_rates` rather than a local copy of `risk/denom`: one definition of the Lamas rates
        # for PtV and for this chain, so the two can never drift apart.
        _, p_risk = _rates(g, n)
        survive = max(0.0, 1.0 - p_risk)
        out = sorted(
            ((v, float(d.get("weight", 0.0))) for _, v, d in g.out_edges(n, data=True)),
            key=lambda kv: kv[0],
        )
        total = sum(w for _, w in out)
        row: dict[str, float] = {}
        if total > 0:
            for v, w in out:
                if v in inside:
                    row[v] = row.get(v, 0.0) + survive * w / total
        rows[n] = row
    return rows


def absorption(
    rows: Mapping[str, Mapping[str, float]], desired: str,
    max_iter: int = _MAX_ITER, tol: float = _TOL,
) -> dict[str, float]:
    """``P(absorbed at DESIRED before EXIT | start at n)`` for every transient ``n``.

    In-place iteration from 0 (``path_to_victory``'s style): monotone increasing, bounded by
    1, converging to the minimal non-negative solution — which is the absorption probability.
    Iterating in sorted order makes the fixed point reproducible byte-for-byte.
    """
    h = dict.fromkeys(rows, 0.0)
    order = sorted(rows)
    for _ in range(max_iter):
        delta = 0.0
        for n in order:
            new = sum(p if j == desired else p * h.get(j, 0.0) for j, p in rows[n].items())
            delta = max(delta, abs(new - h[n]))
            h[n] = new
        if delta < tol:
            break
    return {n: round(v, 6) for n, v in h.items()}


def expected_steps(
    rows: Mapping[str, Mapping[str, float]], desired: str,
    max_iter: int = _MAX_ITER, tol: float = _TOL,
) -> dict[str, float | None]:
    """Expected number of steps until absorption (either state), per transient node.

    ``None`` where the value diverges — a closed cycle inside the system with no leak, or a
    node feeding one. That is the case that makes ``(I − Q)`` singular; reporting it as
    "not established" beats inverting and raising.
    """
    t = dict.fromkeys(rows, 0.0)
    order = sorted(rows)
    last: dict[str, float] = dict.fromkeys(rows, float("inf"))
    for _ in range(max_iter):
        for n in order:
            new = 1.0 + sum(p * t.get(j, 0.0) for j, p in rows[n].items() if j != desired)
            last[n] = abs(new - t[n])
            t[n] = new
        if max(last.values(), default=0.0) < tol:
            break
    return {n: (round(t[n], 4) if last[n] < tol else None) for n in order}


# ── results ──────────────────────────────────────────────────────────────────

@dataclass
class NodeStrength:
    """One entry point of the system, valued toward the desired node."""

    node: str
    occ: float
    usage: float
    p_desired: float
    expected_steps: float | None
    direction: float
    strength: float


@dataclass
class PathStrength:
    """One concrete route to the desired node, with its chained probability."""

    path: list[str]
    p_chain: float
    direction: float
    strength: float

    @property
    def label(self) -> str:
        return " → ".join(self.path)


@dataclass
class SystemStrength:
    """A system's strength toward its desired node, and where that strength sits."""

    members: list[str]
    desired: str
    prize: float
    direction: float
    strength: float
    gated: bool
    gate_reason: str
    nodes: list[NodeStrength] = field(default_factory=list)
    paths: list[PathStrength] = field(default_factory=list)


def _prize(v: Mapping[str, float], desired: str) -> float:
    return round(min(1.0, max(0.0, (1.0 + v.get(desired, 0.0)) / 2.0)), 6)


def _gate(g: nx.DiGraph, members: Sequence[str], desired: str) -> str:
    """The reason this system's numbers must not be *interpreted*, or ``""``.

    Same shape as ``category_constellations.gate_text``: the numbers are still computed and
    published — a gate is a refusal to narrate, not a refusal to measure.
    """
    if len({*members}) < MIN_MEMBERS:
        return "system_too_small"
    if float(g.nodes[desired].get("occ", 0.0)) < MIN_DESIRED_OCC:
        return "desired_below_occ_floor"
    return ""


def top_paths(
    g: nx.DiGraph,
    rows: Mapping[str, Mapping[str, float]],
    desired: str,
    usage: Mapping[str, float],
    prize: float,
    top: int = TOP_PATHS,
    max_len: int = MAX_PATH_LEN,
    counterflow: float = COUNTERFLOW_FACTOR,
) -> list[PathStrength]:
    """The strongest concrete routes to ``desired``, simple paths only, ranked deterministically.

    Simple (no repeated node) because a route is something a coach says out loud; the cyclic
    mass is not lost, it is what ``absorption`` already counted.
    """
    if desired not in g:
        return []
    sub = g.subgraph([n for n in sorted({*rows, desired}) if n in g])
    found: list[PathStrength] = []
    for src in sorted(rows):
        if src == desired or src not in sub:
            continue
        walks = nx.all_simple_paths(sub, src, desired, cutoff=max_len)
        for raw in islice(walks, _MAX_PATHS_PER_ENTRY):
            path = [str(n) for n in raw]
            p = 1.0
            direction = 1.0
            for a, b in zip(path, path[1:], strict=False):
                step = rows.get(a, {}).get(b, 0.0)
                if step <= 0.0:
                    p = 0.0
                    break
                p *= step
                direction = min(direction, direction_factor(g, a, b, counterflow))
            if p <= 0.0:
                continue
            found.append(PathStrength(
                path=path,
                p_chain=round(p, 6),
                direction=round(direction, 4),
                strength=round(p * usage.get(src, 0.0) * direction * prize, 6),
            ))
    found.sort(key=lambda r: (-r.strength, -r.p_chain, r.path))
    return found[:top]


def system_path_strength(
    g: nx.DiGraph,
    members: Sequence[str],
    desired: str | None = None,
    v: Mapping[str, float] | None = None,
    top: int = TOP_PATHS,
    max_len: int = MAX_PATH_LEN,
    counterflow: float = COUNTERFLOW_FACTOR,
) -> SystemStrength | None:
    """One system, end to end. ``None`` when no member of ``members`` is in ``g``.

    ``v`` is a precomputed ``path_to_victory`` map — pass the caller's own so a report that
    already values the graph does not value it a second time.
    """
    target = desired if desired is not None else desired_node(g, members)
    if target is None or target not in g:
        return None
    if v is None:
        v = path_to_victory(g)

    rows = transition_rows(g, members, target)
    prize = _prize(v, target)
    gate_reason = _gate(g, members, target)

    occ = {n: float(g.nodes[n].get("occ", 0.0)) for n in rows}
    total_occ = sum(occ.values())
    usage = {n: (occ[n] / total_occ if total_occ else 0.0) for n in occ}

    h = absorption(rows, target)
    steps = expected_steps(rows, target)

    node_rows: list[NodeStrength] = []
    for n in sorted(rows):
        mass = sum(rows[n].values())
        direction = (
            sum(p * direction_factor(g, n, j, counterflow) for j, p in sorted(rows[n].items()))
            / mass
        ) if mass > 0 else 1.0
        node_rows.append(NodeStrength(
            node=n,
            occ=occ[n],
            usage=round(usage[n], 6),
            p_desired=h[n],
            expected_steps=steps[n],
            direction=round(direction, 4),
            strength=round(h[n] * usage[n] * direction * prize, 6),
        ))
    node_rows.sort(key=lambda r: (-r.strength, r.node))

    return SystemStrength(
        members=sorted(set(members)),
        desired=target,
        prize=prize,
        direction=round(sum(r.usage * r.direction for r in node_rows), 4),
        strength=round(sum(r.strength for r in node_rows), 6),
        gated=bool(gate_reason),
        gate_reason=gate_reason,
        nodes=node_rows,
        paths=top_paths(g, rows, target, usage, prize, top, max_len, counterflow),
    )


def rank_systems(
    g: nx.DiGraph,
    systems: Iterable[Sequence[str]],
    v: Mapping[str, float] | None = None,
    top: int = TOP_PATHS,
    max_len: int = MAX_PATH_LEN,
    counterflow: float = COUNTERFLOW_FACTOR,
) -> list[SystemStrength]:
    """Every system measured toward its own desired node, strongest first.

    Detector-agnostic: ``constellations.detect(...).constellations`` (``.members``) and
    ``network_metrics.detect_communities(...)`` both feed this directly.
    """
    if v is None:
        v = path_to_victory(g)
    out = [
        r for members in systems
        if (r := system_path_strength(g, members, None, v, top, max_len, counterflow))
        is not None
    ]
    out.sort(key=lambda r: (-r.strength, r.desired))
    return out


def to_dict(r: SystemStrength) -> dict[str, Any]:
    """JSON-safe view — ``dataclasses.asdict`` plus the path labels a report renders."""
    from dataclasses import asdict

    d = asdict(r)
    for path, src in zip(d["paths"], r.paths, strict=True):
        path["label"] = src.label
    return d
