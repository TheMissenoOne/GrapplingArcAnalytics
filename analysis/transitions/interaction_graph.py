"""Actor-aware INTERACTION graph — nodes ``(role, label)``, edges = chronological succession.

The second graph product, deliberately NOT a variant of ActionFlow
(``transitions/build_graph.network_from_sequences``). The two answer different
questions and callers route to one of them explicitly:

    ActionFlow    within-actor. Edges are one fighter's own ordered flow, so the
                  network is a map of real technique transitions. Interaction is
                  kept as edge *metadata* (``reactions``) and as PtV's risk term.
    Interaction   actor-aware. Nodes carry a ROLE, edges are plain chronological
                  succession regardless of who acted, so ``you:Turtle →
                  opp:Back Control`` exists as topology — an edge ActionFlow
                  cannot represent at all, since its two endpoints belong to
                  different fighters.

Provenance: PoC-E8 (``docs/research/03_POC_PLANS.md``), raised by the external
PoC review (``docs/research/05_EXTERNAL_POC_REVIEW.md`` §1). App-data-first by
design — on the app the actors are structurally reliable (you / partner), while
43.9% of corpus bouts file every event under one athlete, so corpus callers MUST
gate on ``attribution.bout_flags(...)["perspective_reliable"]`` before building
one. Ungated, the actor-switch edges measure the ingest batch, not the grappling.

Node ids are strings (``"you:half guard"``) so the graph drops straight into the
``networkx`` + ``path_to_victory`` machinery that ActionFlow already feeds; the
``role``/``label`` node attributes are the truth, the id is just their join.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

import networkx as nx

from analysis.names import _normalize_name, canonicalize
from analysis.technique_match import clean_label

YOU = "you"
OPP = "opp"
_SUBMISSION = "submission"

# App-side actor vocabulary (``analysis/poc/fixtures.py``); corpus events carry athlete UUIDs.
_APP_ROLES = {"you": YOU, "partner": OPP, "opponent": OPP, "opp": OPP}


def node_key(label: str, type_hint: str = "") -> str:
    """The system's node-key chain: ``clean_label`` → ``_normalize_name`` → ``canonicalize``.

    Same key space as the athlete graphs, the map and the site export, so an
    interaction node can be joined against everything else by name.
    """
    return canonicalize(_normalize_name(clean_label(str(label or ""), str(type_hint or ""))))


def node_id(role: str, key: str) -> str:
    return f"{role}:{key}"


def role_map(events: Sequence[Mapping[str, Any]], perspective: Any | None) -> dict[Any, str]:
    """actor value → role, decided ONCE per sequence.

    ``perspective`` names the actor that is ``you`` (corpus: an athlete id). Without
    it: the app vocabulary if the sequence speaks it, else first-seen actor = ``you``
    and every other actor = ``opp``. Never inferred per event — a role that flips
    mid-bout is not a role.
    """
    actors: list[Any] = []
    for e in events:
        a = e.get("actor_id", e.get("actor"))
        if a is not None and a not in actors:
            actors.append(a)
    if perspective is not None:
        return {a: (YOU if a == perspective else OPP) for a in actors}
    if actors and all(str(a).lower() in _APP_ROLES for a in actors):
        return {a: _APP_ROLES[str(a).lower()] for a in actors}
    return {a: (YOU if i == 0 else OPP) for i, a in enumerate(actors)}


def _chain(
    sequence: Sequence[Mapping[str, Any]], perspective: Any | None
) -> list[list[dict[str, Any]]]:
    """One bout → the segments of consecutive events that carry a readable actor.

    An event with no actor BREAKS the chain instead of being dropped through: joining
    its neighbours would invent a succession across an event nobody can attribute.
    """
    roles = role_map(sequence, perspective)
    segments: list[list[dict[str, Any]]] = [[]]
    for e in sequence or []:
        actor = e.get("actor_id", e.get("actor"))
        key = node_key(str(e.get("label", "")), str(e.get("type", "")))
        if actor is None or not key:
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append({
            "id": node_id(roles[actor], key),
            "role": roles[actor],
            "key": key,
            "type": str(e.get("type", "")),
            "ok": bool(e.get("successful", False)),
        })
    return [s for s in segments if s]


def interaction_graph(
    sequences: Sequence[Sequence[Mapping[str, Any]]],
    perspectives: Sequence[Any] | None = None,
) -> nx.DiGraph:
    """Actor-tagged sequences → aggregate role-aware succession ``DiGraph``.

    Every consecutive pair inside one sequence is an edge — within-actor pairs AND
    actor-switch pairs, which is the whole point ("edges = consecutive events
    regardless of actor", PoC-E8 §1). Only a pair whose two endpoints are the SAME
    node (same role and same label) is dropped: that is a self-loop, and an ``A → A``
    edge is a claim about a transition no reading of the data supports
    (``attribution.normalize_chain``'s rule, applied to the role-aware node).

    ``perspectives`` gives one "who is ``you``" actor per sequence (corpus: an athlete
    id). Mirror a corpus bout by passing it twice with the two athletes — that is the
    symmetric construction, since a competition bout has no privileged side.

    Node attrs mirror ``network_from_sequences`` so ``path_to_victory`` runs unchanged
    on this kernel: ``type``, ``occ``, ``ok_count``, ``denom`` (appearances with a
    successor), ``reward``, ``risk``, ``reward_risk``, plus ``role``/``label``.
    Reward and risk are the IMMEDIATE-successor versions (the next event is a landed
    submission by the same role → reward; by the other role → risk) because this
    kernel's edges are immediate succession — ActionFlow's next-*own*-event definition
    would put the Bellman reward on a step its own kernel does not take.

    Edge attrs: ``weight`` (count), ``ok`` (how many landed on a successful target),
    ``switch`` (the endpoints' roles differ — the edges ActionFlow cannot represent),
    ``dist`` = 1/weight for shortest-path work.
    """
    if perspectives is not None and len(perspectives) != len(sequences):
        raise ValueError(f"{len(perspectives)} perspectives for {len(sequences)} sequences")

    g = nx.DiGraph()
    occ: defaultdict[str, float] = defaultdict(float)
    ok_count: defaultdict[str, float] = defaultdict(float)
    denom: defaultdict[str, float] = defaultdict(float)
    reward: defaultdict[str, float] = defaultdict(float)
    risk: defaultdict[str, float] = defaultdict(float)
    node_type: dict[str, str] = {}
    meta: dict[str, tuple[str, str]] = {}

    for i, seq in enumerate(sequences):
        persp = perspectives[i] if perspectives is not None else None
        for events in _chain(seq, persp):
            for e in events:
                occ[e["id"]] += 1
                if e["ok"]:
                    ok_count[e["id"]] += 1
                node_type.setdefault(e["id"], e["type"])
                meta.setdefault(e["id"], (e["role"], e["key"]))
            for a, b in zip(events, events[1:], strict=False):
                denom[a["id"]] += 1
                if b["type"] == _SUBMISSION and b["ok"]:
                    (reward if b["role"] == a["role"] else risk)[a["id"]] += 1
                if a["id"] == b["id"]:
                    continue
                if g.has_edge(a["id"], b["id"]):
                    g[a["id"]][b["id"]]["weight"] += 1
                    g[a["id"]][b["id"]]["ok"] += 1 if b["ok"] else 0
                else:
                    g.add_edge(a["id"], b["id"], weight=1, ok=1 if b["ok"] else 0,
                               switch=a["role"] != b["role"])

    for n, c in occ.items():
        g.add_node(n)
        role, key = meta[n]
        g.nodes[n].update({
            "role": role, "label": key, "type": node_type.get(n, ""),
            "occ": c, "ok_count": ok_count[n], "denom": denom[n],
            "reward": reward[n], "risk": risk[n],
            "reward_risk": round((reward[n] - risk[n]) / denom[n], 3) if denom[n] else 0.0,
        })
    for _, _, ed in g.edges(data=True):
        ed["dist"] = 1.0 / ed["weight"]
    return g


def switch_edges(g: nx.DiGraph) -> list[tuple[str, str]]:
    """The actor-switch edges — the ones ActionFlow cannot represent by construction."""
    return [(u, v) for u, v, ed in g.edges(data=True) if ed.get("switch")]
