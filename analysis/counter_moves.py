"""Counter Moves — the highest-value responses to each technique.

For every position/technique node, ranks the observed responses (out-transitions) by
**Path-to-Victory** value of where they land (``edge_ptv``), annotated with how often
the response was taken, its corpus success signal (``reward_risk``, Lamas 2024), and
the position it typically leads to next. Reuses the PtV machinery — no new model.

    from analysis.counter_moves import counter_moves
    cm = counter_moves(g)   # {node_key: [{counter, ptv, count, success, leads_to}, ...]}
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from analysis.path_to_victory import GAMMA, edge_ptv, path_to_victory


def counter_moves(
    g: nx.DiGraph,
    v: dict[str, float] | None = None,
    top_k: int = 3,
    min_count: int = 1,
    gamma: float = GAMMA,
) -> dict[str, list[dict[str, Any]]]:
    """Top-``k`` responses to each node, ranked by the PtV of where they lead.

    ``min_count`` drops one-off transitions (noise). Each response carries its
    ``ptv`` (discounted landing value), ``count`` (times observed), ``success``
    (the response node's reward_risk), and ``leads_to`` (its own best next move).
    """
    if v is None:
        v = path_to_victory(g, gamma=gamma)
    e = edge_ptv(g, v, gamma)

    def best_next(node: str) -> str | None:
        outs = [(b, e.get((node, b), 0.0)) for _, b in g.out_edges(node)]
        return max(outs, key=lambda t: t[1])[0] if outs else None

    out: dict[str, list[dict[str, Any]]] = {}
    for n in g:
        ranked = sorted(
            (
                (b, ed.get("weight", 1))
                for _, b, ed in g.out_edges(n, data=True)
                if ed.get("weight", 1) >= min_count
            ),
            key=lambda t: e.get((n, t[0]), 0.0),
            reverse=True,
        )
        counters = [
            {
                "counter": b,
                "ptv": e.get((n, b), 0.0),
                "count": cnt,
                "success": g.nodes[b].get("reward_risk", 0.0),
                "leads_to": best_next(b),
            }
            for b, cnt in ranked[:top_k]
        ]
        if counters:
            out[n] = counters
    return out


# ── self-check (ponytail: runnable, no DB) ────────────────────────────────────
def _demo() -> None:
    g = nx.DiGraph()
    # From "closed guard" you can go to a high-value back-take or a low-value stall.
    for node, attrs in {
        "closed guard": {"type": "guard", "occ": 10, "reward": 0, "risk": 1, "reward_risk": -0.1},
        "back control": {"type": "control", "occ": 8, "reward": 6, "risk": 0, "reward_risk": 0.75},
        "rnc": {"type": "submission", "occ": 6, "reward": 5, "risk": 0, "reward_risk": 0.8,
                "ok_count": 5},
        "stall": {"type": "transition", "occ": 4, "reward": 0, "risk": 0, "reward_risk": 0.0},
    }.items():
        g.add_node(node, **attrs)
    g.add_edge("closed guard", "back control", weight=7)
    g.add_edge("closed guard", "stall", weight=2)
    g.add_edge("back control", "rnc", weight=6)

    cm = counter_moves(g, top_k=2, min_count=1)
    guard = cm["closed guard"]
    assert guard[0]["counter"] == "back control", guard  # highest-PtV response ranks first
    assert guard[0]["count"] == 7
    assert guard[0]["leads_to"] == "rnc"  # back control's best next move
    assert guard[0]["ptv"] >= guard[1]["ptv"]  # sorted by PtV desc
    print("counter_moves demo OK")


if __name__ == "__main__":
    _demo()
