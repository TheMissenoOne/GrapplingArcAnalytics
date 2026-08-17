"""Actor-tagged sequences → directed weighted transition graph.

Extracted from ``analysis/network_metrics.py`` (wave 4, ``docs/rating_v2/``) so the
Rating Engine V2 and the category trend report share ONE graph-construction function
instead of diverging. Behaviour unchanged from the original — see
``tests/test_network_metrics.py`` for the (still-passing) contract.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

import networkx as nx

from analysis.technique_match import clean_label

_SUBMISSION = "submission"


def _events(sequence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise a stored sequence to ``[{label, type, actor, ok}]`` (labels canonicalised)."""
    out: list[dict[str, Any]] = []
    for e in sequence or []:
        label = clean_label(str(e.get("label", "")), str(e.get("type", "")))
        if not label:
            continue
        out.append({
            "label": label,
            "type": str(e.get("type", "")),
            "actor": e.get("actor_id"),
            "ok": bool(e.get("successful", False)),
        })
    return out


def network_from_sequences(
    sequences: list[list[dict[str, Any]]],
    weight_fn: Callable[[Any], float] | None = None,
) -> nx.DiGraph:
    """Pure builder: actor-tagged sequences → aggregate transition ``DiGraph``.

    **Edges are within-actor** — one fighter's own ordered flow (their next *own* action),
    so the network is a map of real technique transitions, never a cross-fighter artifact
    (e.g. A's escape followed by B's takedown is *not* an edge). Edge ``weight`` = count,
    edge ``ok`` = how many of those transitions landed on a *successful* target event
    (drives the data-directed / dashed rendering — see ``edge_arrow``/``edge_dashed``
    in ``analysis/network_metrics.py``).

    Markov reward-risk per node (Lamas et al. 2024), over appearances that have a successor
    (a position that simply ends the recorded sequence is excluded from the denominator):
      - **reward**: the fighter's own next action from here is a finished submission;
      - **risk**: the very next event (either fighter) is the *opponent* finishing a submission
        — only when both actors are known (unknown attribution is left neutral, never charged).
    Node attrs: ``type``, ``occ`` (total appearances), ``reward``/``risk``, ``reward_risk``.

    ``weight_fn`` (optional): per-event multiplier keyed by the raw ``actor`` id — every
    count this function produces (node ``occ``/``ok_count``/``denom``/``reward``/``risk``,
    edge ``weight``/``ok``) is attributed to the actor who owns the *appearance* being
    counted, so confidence-weighting the corpus by athlete (``analysis/confidence_weight.py``,
    ``docs/rating_v2/07_PONDERACAO_POR_CONFIANCA.md``) is exactly "pass a ``weight_fn``" —
    no separate builder needed. Default (``None``) is unweighted and produces byte-identical
    output to before this parameter existed (int counts, not float).
    """
    g = nx.DiGraph()
    occ: Counter[str] = Counter()
    denom: Counter[str] = Counter()  # appearances that have a successor
    reward: Counter[str] = Counter()
    risk: Counter[str] = Counter()
    ok_count: Counter[str] = Counter()  # successful appearances (PtV terminal rates)
    node_type: dict[str, str] = {}

    for seq in sequences:
        events = _events(seq)
        n = len(events)
        wts = [weight_fn(e["actor"]) if weight_fn is not None else 1 for e in events]
        for e, wt in zip(events, wts, strict=True):
            occ[e["label"]] += wt
            if e["ok"]:
                ok_count[e["label"]] += wt
            node_type.setdefault(e["label"], e["type"])

        # index of each event's next *own*-actor event (None actor = no attributable flow)
        by_actor: dict[Any, list[int]] = defaultdict(list)
        for i, e in enumerate(events):
            if e["actor"] is not None:
                by_actor[e["actor"]].append(i)
        next_own: dict[int, int] = {}
        for idxs in by_actor.values():
            for k in range(len(idxs) - 1):
                next_own[idxs[k]] = idxs[k + 1]
            # within-actor flow edges (ok = target event's own success flag); both ends
            # share one actor, so wts[idxs[k]] == wts[idxs[k + 1]] whenever weight_fn is set.
            for k in range(len(idxs) - 1):
                a, b = events[idxs[k]]["label"], events[idxs[k + 1]]["label"]
                if a != b:
                    edge_wt = wts[idxs[k]]
                    b_ok = edge_wt if events[idxs[k + 1]]["ok"] else 0
                    if g.has_edge(a, b):
                        g[a][b]["weight"] += edge_wt
                        g[a][b]["ok"] += b_ok
                    else:
                        g.add_edge(a, b, weight=edge_wt, ok=b_ok)

        for i, e in enumerate(events):
            if i + 1 >= n:
                continue  # terminal appearance (no successor) — not in the denominator
            label = e["label"]
            wt = wts[i]
            denom[label] += wt
            own = next_own.get(i)
            nxt = events[i + 1]
            if own is not None and events[own]["type"] == _SUBMISSION and events[own]["ok"]:
                reward[label] += wt
            elif (nxt["type"] == _SUBMISSION and nxt["ok"]
                  and e["actor"] is not None and nxt["actor"] is not None
                  and nxt["actor"] != e["actor"]):
                risk[label] += wt

    for label, c in occ.items():
        g.add_node(label)
        g.nodes[label]["type"] = node_type.get(label, "")
        g.nodes[label]["occ"] = c
        g.nodes[label]["reward"] = reward[label]
        g.nodes[label]["risk"] = risk[label]
        g.nodes[label]["denom"] = denom[label]  # appearances with a successor (PtV rates)
        g.nodes[label]["ok_count"] = ok_count[label]
        d = denom[label]
        g.nodes[label]["reward_risk"] = round((reward[label] - risk[label]) / d, 3) if d else 0.0
    # distance = inverse weight, for shortest-path-based betweenness
    for _, _, ed in g.edges(data=True):
        ed["dist"] = 1.0 / ed["weight"]
    return g
