"""Section 5 (constellations) glue for the category trend report.

**PURO** — no DB, no file, no network. Combines the shared ``analysis/transitions`` +
``analysis/constellations`` layer (wave 4, ``docs/rating_v2/``) with the category-vs-baseline
framing ``docs/superpowers/specs/2026-08-16-relatorio-categoria-tendencias-design.md`` section 5
asks for. Consumes exactly the ``per_athlete_bouts`` dict
``scripts.scouting_division_report._division_tables`` already builds for sections 1-4/6-9 — no
independent roster resolution, no separate DB read. If section 1 says one athlete concentrates
85% of the category's events, this module sees the same athlete as dominant, because it reads
the same bout list.

Rating never enters here (same rule as ``analysis/constellations``): membership, prevalence,
lift and stability are topology/frequency only.

5.1 construction -> ``division_constellations`` calls ``athlete_balanced_category_graph`` +
``constellations.detect`` (resolution=1.0, the wave-4-confirmed default).
5.2 baseline comparison -> prevalence/lift, same weighting symmetry rule as ``category_profile``
(category athlete-balanced vs baseline athlete-balanced, never crossed).
5.3 robustness -> ``constellations.stability`` (bootstrap Jaccard, leave-one-out, support).
5.4 publication gate -> ``gate_text``: interpretive metagame text only when more than one
athlete contributes AND the constellation is not athlete-driven. An ``ATHLETE_DRIVEN``
constellation's text names the athlete — that is what makes the report useful for scouting
instead of misleading.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

import networkx as nx

from analysis.constellations import Stability, bootstrap_jaccard, classify_stability, detect, leave_one_out_athlete_driven
from analysis.transitions import athlete_balanced_category_graph, network_from_sequences

#: Wave 4 measured flat modularity between 0.8 and 1.4 — 1.0 is not a guess.
RESOLUTION = 1.0

#: ponytail: stability.py's own default is 200; a report run detects constellations for up to
#: 2 divisions x 2 baseline-bearing scopes, each resample re-running Louvain on a small (~8
#: athlete) graph — 60 is enough signal for the STABLE/PARTIALLY_STABLE/ATHLETE_DRIVEN cut at
#: report runtime cost. Raise if a published number needs tighter Jaccard estimates.
N_RESAMPLES = 60

#: 5.4 gate: "mais de uma atleta contribuindo".
MIN_SUPPORT_ATHLETES = 2

#: How many members to name as "nós centrais" / list as "transições características".
TOP_CENTRAL_NODES = 3
TOP_TRANSITIONS = 5


# ── 5.1: actor-tagged sequences from the report's own corpus ────────────────────────────────

def _own_events(bout: Mapping[str, Any], athlete: str) -> list[dict[str, Any]]:
    return [
        {"label": e.get("label"), "type": e.get("type"), "actor_id": athlete,
         "successful": e.get("successful")}
        for e in bout.get("events", []) if e.get("actor") == athlete
    ]


def sequences_by_athlete(
    per_athlete_bouts: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[list[dict[str, Any]]]]:
    """Category side: roster's own bout list -> per-athlete actor-tagged sequences, filtered to
    that athlete's own events (an intra-roster bout is stored under both participants' names;
    filtering to ``actor == athlete`` keeps one athlete's own graph from absorbing her
    opponent's edges too)."""
    out: dict[str, list[list[dict[str, Any]]]] = {}
    for athlete, bouts in per_athlete_bouts.items():
        seqs = [seq for bout in bouts if (seq := _own_events(bout, athlete))]
        out[athlete] = seqs
    return out


def sequences_by_actor(bouts: Sequence[Mapping[str, Any]]) -> dict[str, list[list[dict[str, Any]]]]:
    """Baseline side: no fixed roster — group every bout's events by whichever actor they
    belong to."""
    out: dict[str, list[list[dict[str, Any]]]] = defaultdict(list)
    for bout in bouts:
        by_actor: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in bout.get("events", []):
            actor = e.get("actor")
            if not actor:
                continue
            by_actor[str(actor)].append({"label": e.get("label"), "type": e.get("type"),
                                          "actor_id": str(actor), "successful": e.get("successful")})
        for actor, events in by_actor.items():
            out[actor].append(events)
    return dict(out)


def athlete_node_sets(
    athlete_sequences: Mapping[str, Sequence[Sequence[dict[str, Any]]]],
) -> dict[str, set[str]]:
    """Each athlete's own node set (labels her sequences touch) — the ``support()`` gate's
    "how many distinct athletes contribute to this constellation" input."""
    return {a: set(network_from_sequences(list(seqs)).nodes) for a, seqs in athlete_sequences.items()}


# ── bootstrap/leave-one-out units: one per (athlete, bout) ──────────────────────────────────

def _bout_units(
    athlete_sequences: Mapping[str, Sequence[Sequence[dict[str, Any]]]],
) -> tuple[list[str], dict[str, tuple[str, list[dict[str, Any]]]]]:
    units: list[str] = []
    lookup: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for athlete in sorted(athlete_sequences):
        for i, seq in enumerate(athlete_sequences[athlete]):
            key = f"{athlete}::{i}"
            units.append(key)
            lookup[key] = (athlete, seq)
    return units, lookup


def _units_by_athlete(units: Sequence[str], lookup: Mapping[str, tuple[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for u in units:
        out[lookup[u][0]].append(u)
    return dict(out)


def _build_graph_from_units(
    units: Sequence[str], lookup: Mapping[str, tuple[str, list[dict[str, Any]]]],
) -> nx.DiGraph:
    by_athlete: dict[str, list[list[dict[str, Any]]]] = defaultdict(list)
    for u in units:
        hit = lookup.get(u)
        if hit is None:
            continue
        athlete, seq = hit
        by_athlete[athlete].append(seq)
    return athlete_balanced_category_graph(dict(by_athlete))


# ── 5.2: prevalence / lift / central nodes / characteristic transitions ─────────────────────

def _total_weight(graph: nx.DiGraph) -> float:
    return sum(d.get("weight", 0.0) for _, _, d in graph.edges(data=True))


def _internal_weight(graph: nx.DiGraph, members: Sequence[str]) -> float:
    m = set(members)
    return sum(d.get("weight", 0.0) for u, v, d in graph.edges(data=True) if u in m and v in m)


def _central_nodes(graph: nx.DiGraph, members: Sequence[str], top: int = TOP_CENTRAL_NODES) -> list[str]:
    deg = {n: graph.in_degree(n, weight="weight") + graph.out_degree(n, weight="weight")
           for n in members if n in graph}
    return sorted(deg, key=lambda n: (-deg[n], n))[:top]


def _characteristic_transitions(
    graph: nx.DiGraph, members: Sequence[str], top: int = TOP_TRANSITIONS,
) -> list[dict[str, Any]]:
    m = set(members)
    edges = sorted(
        ((u, v, d["weight"]) for u, v, d in graph.edges(data=True) if u in m and v in m),
        key=lambda e: -e[2],
    )
    return [{"de": u, "para": v, "weight": round(w, 4)} for u, v, w in edges[:top]]


# ── 5.4: publication gate ────────────────────────────────────────────────────────────────────

def gate_text(
    hub: str, classification: Stability, support_athletes: int, driver_athlete: str | None,
) -> tuple[bool, str]:
    """The interpretive sentence for one constellation, gated on 5.4's rule: publishable only
    with support from more than one athlete AND not athlete-driven. An athlete-driven pattern's
    text names the athlete instead of silently omitting the caveat."""
    if classification == Stability.ATHLETE_DRIVEN or support_athletes < MIN_SUPPORT_ATHLETES:
        if driver_athlete:
            return False, (
                f"Este padrão aparece no corpus da divisão, mas hoje é sustentado "
                f"principalmente por {driver_athlete} — evidência insuficiente para tendência "
                f"de categoria."
            )
        return False, "Padrão observado, evidência insuficiente para tendência de categoria."
    return True, (
        f"Constelação em torno de {hub}, sustentada por {support_athletes} atletas da "
        f"divisão — estabilidade {classification.value.lower().replace('_', ' ')}."
    )


# ── orchestration ─────────────────────────────────────────────────────────────────────────

def division_constellations(
    per_athlete_bouts: Mapping[str, Sequence[Mapping[str, Any]]],
    baseline_bouts: Sequence[Mapping[str, Any]] = (),
    *, resolution: float = RESOLUTION, n_resamples: int = N_RESAMPLES, seed: int = 42,
) -> dict[str, Any]:
    """Section 5, end to end: the same ``per_athlete_bouts`` sections 1-4/6-9 use, an already
    roster-excluded ``baseline_bouts`` (``category_profile.exclude_roster_from_baseline``) —
    never re-resolved here."""
    athlete_sequences = sequences_by_athlete(per_athlete_bouts)
    category_graph = athlete_balanced_category_graph(athlete_sequences)
    detection = detect(category_graph, resolution=resolution, seed=seed)
    members_multi = [c for c in detection.constellations if len(c.members) > 1]
    if not members_multi:
        return {"constelacoes": [], "modularity": detection.modularity,
                "rejected_rate": detection.rejected_rate}

    baseline_graph = (
        athlete_balanced_category_graph(sequences_by_actor(baseline_bouts))
        if baseline_bouts else None
    )

    units, lookup = _bout_units(athlete_sequences)
    units_by_athlete = _units_by_athlete(units, lookup)
    athlete_nodes = athlete_node_sets(athlete_sequences)

    def build_graph(sample_units: list[str]) -> nx.DiGraph:
        return _build_graph_from_units(sample_units, lookup)

    _, mean_jaccard = bootstrap_jaccard(
        units, build_graph, baseline=detection, n_resamples=n_resamples,
        resolution=resolution, detect_seed=seed, rng_seed=seed,
    )
    driver_by_key = leave_one_out_athlete_driven(
        units_by_athlete, build_graph, baseline=detection, resolution=resolution, seed=seed,
    )
    stability_by_key = {
        frozenset(r.members): r
        for r in classify_stability(detection, mean_jaccard, driver_by_key, athlete_nodes)
    }

    category_total = _total_weight(category_graph)
    baseline_total = _total_weight(baseline_graph) if baseline_graph is not None else 0.0

    rows: list[dict[str, Any]] = []
    for c in members_multi:
        key = frozenset(c.members)
        st = stability_by_key[key]
        driver = driver_by_key.get(key)

        prevalencia_categoria = _internal_weight(category_graph, c.members) / category_total if category_total else 0.0
        prevalencia_baseline: float | None = None
        log2_lift: float | None = None
        inedito_no_baseline = False
        if baseline_graph is not None:
            prevalencia_baseline = (
                _internal_weight(baseline_graph, c.members) / baseline_total if baseline_total else 0.0
            )
            if prevalencia_categoria > 0 and prevalencia_baseline > 0:
                log2_lift = round(math.log2(prevalencia_categoria / prevalencia_baseline), 3)
            elif prevalencia_categoria > 0:
                inedito_no_baseline = True

        passa_gate, texto = gate_text(c.hub, st.classification, st.support_athletes, driver)
        rows.append({
            "members": list(c.members), "hub": c.hub, "internal_edges": c.internal_edges,
            "support": c.support,
            "prevalencia_categoria": round(prevalencia_categoria, 4),
            "prevalencia_baseline": round(prevalencia_baseline, 4) if prevalencia_baseline is not None else None,
            "log2_lift": log2_lift, "inedito_no_baseline": inedito_no_baseline,
            "nos_centrais": _central_nodes(category_graph, c.members),
            "transicoes_caracteristicas": _characteristic_transitions(category_graph, c.members),
            "mean_jaccard": st.mean_jaccard, "classification": st.classification.value,
            "driver_athlete": driver, "support_athletes": st.support_athletes,
            "passa_gate": passa_gate, "texto": texto,
        })
    return {"constelacoes": rows, "modularity": detection.modularity,
            "rejected_rate": detection.rejected_rate}
