"""Path-to-Victory (PtV) — discounted Markov-reward valuation of the transition network.

``reward_risk`` (Lamas 2024) is the 1-step case; PtV is its multi-step discounted
generalization (xT / VAEP / hockey-Q family — see docs/path_to_victory.md):

    v(n) = clamp₋₁¹( shaping(n) + p_reward(n) − p_risk(n)
                     + γ·(1 − p_reward − p_risk)·Σⱼ P(n→j)·v(j) )

All functions are pure on the ``network_from_sequences`` graph (or raw sequences where
noted), mirroring ``network_metrics``. Derived metrics each reuse a named existing brick.

ponytail: fixed γ=0.8 + fixed shaping weights (keyword knobs); ceiling = calibrating γ
against held-out finish prediction, VAEP-style.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import numpy as np

from analysis.athlete_elo import _points_for_entry
from analysis.decision_space import position_decision_space, sequence_decision_space
from analysis.names import _normalize_name

GAMMA = 0.8            # see docs/path_to_victory.md — 3 own-steps out ≈ 0.51
_SHAPING_W = 0.1       # per-term shaping weight; |shaping| ≤ 0.175 so terminals dominate
_PTV_EDGE_THRESHOLD = 0.15   # a branch worth taking (dilemma / funnel cutoff)
_DEV_SCALE = 200.0     # ELO points per unit of centered PtV
_DEV_CLAMP = 150       # ≈ 70% expected score on the /400 logistic — a strong prior


def _shaping(label: str, typ: str) -> float:
    """Small positional prior — reuses the app point map + decision-space defaults."""
    pts = _points_for_entry({"label": label, "type": typ}) / 4.0
    ds = position_decision_space(typ)
    return _SHAPING_W * pts + _SHAPING_W * (ds["attacker_score"] - ds["defender_score"])


def _rates(g: nx.DiGraph, n: str) -> tuple[float, float]:
    d = g.nodes[n]
    denom = d.get("denom", 0)
    if not denom:
        return 0.0, 0.0
    return d.get("reward", 0) / denom, d.get("risk", 0) / denom


def _terminal_rate(g: nx.DiGraph, n: str) -> float:
    """Submission attempts absorb at their observed success rate (a landed finish IS
    the +1 terminal — without this, edges into a finish would look worthless because
    the Lamas reward credit sits on the predecessor)."""
    d = g.nodes[n]
    if d.get("type") != "submission":
        return 0.0
    occ = d.get("occ", 0)
    return d.get("ok_count", 0) / occ if occ else 0.0


def _kernel(g: nx.DiGraph) -> dict[str, list[tuple[str, float]]]:
    """Row-normalized within-actor transition kernel (empirical, pure-weighted)."""
    out: dict[str, list[tuple[str, float]]] = {}
    for n in g:
        edges = [(v, ed.get("weight", 1.0)) for _, v, ed in g.out_edges(n, data=True)]
        s = sum(w for _, w in edges)
        out[n] = [(v, w / s) for v, w in edges] if s > 0 else []
    return out


def path_to_victory(
    g: nx.DiGraph, gamma: float = GAMMA, max_iter: int = 200, tol: float = 1e-6
) -> dict[str, float]:
    """Node PtV v(n) ∈ [−1, 1] by value iteration (γ-contraction → converges)."""
    kernel = _kernel(g)
    shaping = {n: _shaping(n, g.nodes[n].get("type", "")) for n in g}
    rates = {n: _rates(g, n) for n in g}
    terminal = {n: _terminal_rate(g, n) for n in g}
    v = dict.fromkeys(g, 0.0)
    for _ in range(max_iter):
        delta = 0.0
        for n in g:
            p_r, p_k = rates[n]
            p_t = terminal[n]
            cont = sum(p * v[j] for j, p in kernel[n])
            stay = max(0.0, 1.0 - p_r - p_k - p_t)
            new = shaping[n] + p_r + p_t - p_k + gamma * stay * cont
            new = max(-1.0, min(1.0, new))
            delta = max(delta, abs(new - v[n]))
            v[n] = new
        if delta < tol:
            break
    return {n: round(x, 4) for n, x in v.items()}


def edge_ptv(
    g: nx.DiGraph, v: dict[str, float], gamma: float = GAMMA
) -> dict[tuple[str, str], float]:
    """PtV of taking a branch = discounted value of where it lands."""
    return {(a, b): round(gamma * v.get(b, 0.0), 4) for a, b in g.edges}


# ── derived metrics ──────────────────────────────────────────────────────────

def continuity(g: nx.DiGraph) -> dict[str, float]:
    """Expected safe own-chain continuation: (1 − own risk) · Σ P(n→j)·(1 − risk(j)).

    ponytail: 1-step lookahead; ceiling = full discounted survival chain.
    """
    kernel = _kernel(g)
    out: dict[str, float] = {}
    for n in g:
        _, own_risk = _rates(g, n)
        nxt = sum(p * (1.0 - _rates(g, j)[1]) for j, p in kernel[n])
        out[n] = round((1.0 - own_risk) * nxt, 4)
    return out


def dilemmas(
    g: nx.DiGraph,
    v: dict[str, float],
    gamma: float = GAMMA,
    threshold: float = _PTV_EDGE_THRESHOLD,
    max_depth: int = 3,
) -> list[dict[str, Any]]:
    """Nodes with ≥2 out-edges of high PtV — real either/or forks.

    Each: ``{node, ptv, branches: [(target, edge_ptv)...], subtree: [...]}`` where the
    subtree is every position reachable through high-PtV branches (depth-capped).
    """
    e = edge_ptv(g, v, gamma)
    found: list[dict[str, Any]] = []
    for n in g:
        branches = sorted(
            ((b, e[(a, b)]) for a, b in g.out_edges(n) if e[(a, b)] >= threshold),
            key=lambda x: x[1], reverse=True,
        )
        if len(branches) < 2:
            continue
        subtree: list[str] = []
        seen = {n}
        frontier = [b for b, _ in branches]
        for _ in range(max_depth):
            nxt: list[str] = []
            for node in frontier:
                if node in seen:
                    continue
                seen.add(node)
                subtree.append(node)
                nxt.extend(b for _, b in g.out_edges(node) if e[(node, b)] >= threshold)
            frontier = nxt
        found.append({
            "node": n, "ptv": v.get(n, 0.0),
            "branches": branches, "subtree": subtree,
        })
    found.sort(key=lambda d: d["ptv"], reverse=True)
    return found


def ptv_momentum(sequence: list[dict[str, Any]], v: dict[str, float]) -> list[float]:
    """Running cumulative signed PtV over a match sequence, share-shaped (0..1, 0.5 =
    even) — drop-in for ``momentum_series`` (the canvas maps x → x·2−1)."""
    from analysis.technique_match import clean_label

    # side tag preferred (breakdown sequences carry it); raw DB sequences fall back to
    # first-seen actor = 'a' (matches match_breakdown's corner assignment).
    actors = [e.get("actor_id") for e in sequence or [] if e.get("actor_id") is not None]
    order = list(dict.fromkeys(actors))
    side_of = {a: ("a" if i == 0 else "b") for i, a in enumerate(order[:2])}
    cum = 0.0
    raw: list[float] = []
    for ev in sequence or []:
        side = ev.get("side") or side_of.get(ev.get("actor_id"))
        if side not in ("a", "b"):
            continue
        label = clean_label(str(ev.get("label", "")), str(ev.get("type", "")))
        val = v.get(label, 0.0)
        cum += val if side == "a" else -val
        raw.append(cum)
    if not raw:
        return []
    peak = max(1e-9, max(abs(x) for x in raw))
    return [round(0.5 + 0.5 * x / peak, 4) for x in raw]


def countering_nodes(sequences: list[list[dict[str, Any]]]) -> dict[str, float]:
    """How often a position is THE move that flips the dominance lead — aggregated
    ``decision_space.turning_points`` credited to the flipping label, per appearance."""
    from collections import Counter

    flips: Counter[str] = Counter()
    occ: Counter[str] = Counter()
    for seq in sequences:
        # decision_space expects side-tagged events; map actor order → sides.
        actors = [e.get("actor_id") for e in seq if e.get("actor_id") is not None]
        order = list(dict.fromkeys(actors))
        side_of = {a: ("a" if i == 0 else "b") for i, a in enumerate(order[:2])}
        tagged = [
            {**e, "side": e.get("side") or side_of.get(e.get("actor_id"))}
            for e in seq
        ]
        for e in tagged:
            if e.get("label"):
                occ[str(e["label"])] += 1
        for tp in sequence_decision_space(tagged)["turning_points"]:
            if tp.get("label"):
                flips[str(tp["label"])] += 1
    return {
        label: round(flips[label] / occ[label], 4)
        for label in occ if flips.get(label)
    }


def decision_funnel(
    g: nx.DiGraph, v: dict[str, float], gamma: float = GAMMA,
    threshold: float = _PTV_EDGE_THRESHOLD,
) -> dict[str, float]:
    """Share of a node's out-transition mass flowing through high-PtV branches."""
    e = edge_ptv(g, v, gamma)
    kernel = _kernel(g)
    return {
        n: round(float(sum(p for j, p in kernel[n] if e[(n, j)] >= threshold)), 4)
        for n in g if kernel[n]
    }


def control_score(g: nx.DiGraph, v: dict[str, float], gamma: float = GAMMA) -> dict[str, float]:
    """Consistently good options: mean − std of out-edge PtV (≥1 out-edge)."""
    e = edge_ptv(g, v, gamma)
    out: dict[str, float] = {}
    for n in g:
        vals = [e[(n, j)] for _, j in g.out_edges(n)]
        if vals:
            out[n] = round(float(np.mean(vals) - np.std(vals)), 4)
    return out


# ── ELO deviance (CF8 — cross-module contract with the App) ─────────────────

def node_elo_deviance(
    g: nx.DiGraph,
    v: dict[str, float] | None = None,
    scale: float = _DEV_SCALE,
    clamp: int = _DEV_CLAMP,
) -> dict[str, int]:
    """Signed ELO offset per ``node_key`` from occ-weighted-centered PtV.

    Rides ``@grapplingarch:nodes_library`` as ``eloDeviance``; the app's
    ``findOrCreateNode`` seeds ``computedElo = baseline + eloDeviance`` (clamped).
    Centering keeps the corpus-average node at ±0 so deviance is a prior, not a rank.
    """
    if v is None:
        v = path_to_victory(g)
    total_occ = sum(d.get("occ", 0) for _, d in g.nodes(data=True)) or 1
    mean = sum(v[n] * d.get("occ", 0) for n, d in g.nodes(data=True)) / total_occ
    out: dict[str, int] = {}
    for n in g:
        offset = round((v[n] - mean) * scale)
        out[_normalize_name(n)] = max(-clamp, min(clamp, offset))
    return out
