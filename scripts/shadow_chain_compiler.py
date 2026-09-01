"""Shadow run — Phase 1 chain compiler vs. the current flat-node derivation.

Reads matches from the offline dump (default: workspace-root ``_analytics_export.json``, a
flat ``[{sequence, athlete_a_key, athlete_b_key, ...}]`` list — no DB, no prod write, no
persistence at all). For every match it splits ``sequence`` by fighter
(``analysis.names.athlete_key(actor) == athlete_a_key/b_key``; anything else — referee,
unattributed — falls into the 'dropped, no side' bucket, same as ``build_graph``'s
``actor_id is None`` exclusion), runs ``chain_compiler.compile_two_sided`` on each match, and
compares the aggregate against ``transitions.build_graph.network_from_sequences`` — the
derivation Phase 1 is meant to replace — fed the SAME actor-id split so the two runs see
identical attribution.

Prints a comparative report and writes it to ``docs/taxonomy/shadow_compiler_report.md``.
Nothing else is written; no DB touched.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from analysis.chain_compiler import CompiledChain, compile_two_sided
from analysis.names import athlete_key
from analysis.taxonomy_kind import load_inference_table
from analysis.transitions.build_graph import network_from_sequences

DEFAULT_EXPORT = Path("/home/vetor/GrapplingArc/_analytics_export.json")
REPORT_PATH = Path(__file__).resolve().parent.parent / "docs" / "taxonomy" / "shadow_compiler_report.md"


def _side_of(match: dict[str, Any]) -> Callable[[Mapping[str, Any]], str | None]:
    a_key, b_key = match.get("athlete_a_key"), match.get("athlete_b_key")

    def side_of(ev: Mapping[str, Any]) -> str | None:
        k = athlete_key(str(ev.get("actor") or ""))
        if k == a_key:
            return "a"
        if k == b_key:
            return "b"
        return None

    return side_of


def compile_corpus(matches: list[dict[str, Any]]) -> dict[str, Any]:
    table = load_inference_table()
    chains: list[CompiledChain] = []
    dropped_reasons: Counter[str] = Counter()
    matches_compiled = 0

    for m in matches:
        seq = m.get("sequence") or []
        if not seq:
            continue
        result = compile_two_sided(seq, _side_of(m), inference_table=table)
        chains.append(result["a"])
        chains.append(result["b"])
        for d in result["dropped"].dropped:
            dropped_reasons[d.reason] += 1
        for side in ("a", "b"):
            for d in result[side].dropped:
                dropped_reasons[d.reason] += 1
        matches_compiled += 1

    nodes: set[str] = set()
    edge_weight: Counter[tuple[str, str]] = Counter()
    degree: Counter[str] = Counter()
    # Phase 1: keyed on the WHOLE canonical action sequence, not just `actions[0]` (same fix as
    # `render_map_prototypes.Aggregate.add_edge`) — an edge can now carry more than one action.
    action_volume: Counter[tuple[str, ...]] = Counter()
    actions_len_dist: Counter[int] = Counter()
    total_nodes = total_edges = inferred_nodes = inferred_edges = 0
    # Fase 2: the split that matters — OBSERVED occurrences come one-for-one from the log and
    # are invariant across every phase; INFERRED ones are the rule's own output and move with it.
    observed_actions = inferred_actions = 0

    for ch in chains:
        for s in ch.states:
            nodes.add(s.node_key)
            total_nodes += 1
            if s.inferred:
                inferred_nodes += 1
        for e in ch.edges:
            total_edges += 1
            if e.inferred:
                inferred_edges += 1
            edge_weight[(e.source_key, e.target_key)] += 1
            action_volume[tuple(a.key for a in e.actions)] += 1
            observed_actions += sum(1 for a in e.actions if not a.inferred)
            inferred_actions += sum(1 for a in e.actions if a.inferred)
            actions_len_dist[len(e.actions)] += 1
            degree[e.source_key] += 1
            degree[e.target_key] += 1

    return {
        "matches_compiled": matches_compiled,
        "unique_state_nodes": len(nodes),
        "unique_transition_edges": len(edge_weight),
        "total_state_occurrences": total_nodes,
        "total_action_edges": total_edges,
        "observed_action_occurrences": observed_actions,
        "inferred_action_occurrences": inferred_actions,
        "inferred_node_pct": round(100 * inferred_nodes / total_nodes, 1) if total_nodes else 0.0,
        "inferred_edge_pct": round(100 * inferred_edges / total_edges, 1) if total_edges else 0.0,
        "top_states_by_degree": degree.most_common(10),
        "top_actions_by_volume": action_volume.most_common(10),
        # Phase 1 measurement (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md): how many transitions
        # now carry 1 / 2 / 3+ stacked actions.
        "actions_per_edge_dist": dict(sorted(actions_len_dist.items())),
        "dropped_reasons": dict(dropped_reasons),
    }


def current_derivation(matches: list[dict[str, Any]]) -> dict[str, Any]:
    sequences = []
    for m in matches:
        seq = m.get("sequence") or []
        if not seq:
            continue
        side_of = _side_of(m)
        sequences.append([{**e, "actor_id": side_of(e)} for e in seq])
    g = network_from_sequences(sequences)
    degree = Counter({n: g.in_degree(n) + g.out_degree(n) for n in g.nodes})
    edges = Counter({(u, v): d["weight"] for u, v, d in g.edges(data=True)})
    return {
        "unique_nodes": g.number_of_nodes(),
        "unique_edges": g.number_of_edges(),
        "total_edge_weight": sum(d["weight"] for _, _, d in g.edges(data=True)),
        "top_nodes_by_degree": degree.most_common(10),
        "top_edges_by_weight": edges.most_common(10),
    }


def render_report(compiler: dict[str, Any], current: dict[str, Any]) -> str:
    lines = [
        "# Shadow chain compiler report (Phase 1, actions/states migration)",
        "",
        "Generated by `scripts/shadow_chain_compiler.py`. Read-only: no DB, no persistence — a",
        "comparison between `analysis.chain_compiler.compile_two_sided` (the new action-edge /",
        "state-node structure) and `analysis.transitions.build_graph.network_from_sequences`",
        "(the current flat-node derivation), both run over the same offline dump with the same",
        "actor-id split.",
        "",
        "## Corpus",
        f"- matches compiled: {compiler['matches_compiled']}",
        f"- dropped events (by reason): {compiler['dropped_reasons']}",
        "",
        "## chain_compiler (new structure)",
        f"- unique state nodes: {compiler['unique_state_nodes']}",
        f"- unique transition edges (state→state, deduped): {compiler['unique_transition_edges']}",
        f"- total state-node occurrences (raw walk, not deduped): {compiler['total_state_occurrences']}",
        f"- total action edges: {compiler['total_action_edges']}",
        f"- action occurrences: {compiler['observed_action_occurrences']} observed "
        f"(invariant) + {compiler['inferred_action_occurrences']} inferred (the rule's output)",
        f"- inferred state nodes: {compiler['inferred_node_pct']}%",
        f"- inferred action edges: {compiler['inferred_edge_pct']}%",
        f"- actions per edge (length -> count): {compiler['actions_per_edge_dist']}",
        "",
        "### Top 10 states by degree",
    ]
    for key, deg in compiler["top_states_by_degree"]:
        lines.append(f"- {key}: {deg}")
    lines += ["", "### Top 10 actions by volume"]
    for key, n in compiler["top_actions_by_volume"]:
        lines.append(f"- {key}: {n}")

    lines += [
        "",
        "## network_from_sequences (current derivation)",
        f"- unique nodes: {current['unique_nodes']}",
        f"- unique edges: {current['unique_edges']}",
        f"- total edge weight: {current['total_edge_weight']}",
        "",
        "### Top 10 nodes by degree",
    ]
    for key, deg in current["top_nodes_by_degree"]:
        lines.append(f"- {key}: {deg}")
    lines += ["", "### Top 10 edges by weight"]
    for (a, b), w in current["top_edges_by_weight"]:
        lines.append(f"- {a} → {b}: {w}")

    lines += [
        "",
        "## Reading these numbers side by side",
        "",
        "Not apples-to-apples on node identity: `chain_compiler`'s node key space is",
        "`canonicalize(_normalize_name(label))` (this module's own convention); "
        "`network_from_sequences` keys on `clean_label` (technique-library display labels). "
        "The unique node/edge COUNTS are still comparable structurally — `chain_compiler` "
        "always has fewer or equal unique state nodes than `network_from_sequences` has unique "
        "nodes, because every ACTION label that used to be its own flat node (guard pass, "
        "takedown, submission, ...) is now an edge, not a node.",
        "",
        "`chain_compiler` was NOT fed a round/boundary filter (`reset`/`match`/`penalty`/`strike` "
        "event types have no D1 classification rule of their own — `kind_of` reads them as "
        "'state' by default, same as any unrecognised type). Events whose actor is neither "
        "fighter (referee, unattributed) are dropped via `side_of`, mirroring "
        "`network_from_sequences`'s own `actor_id is None` exclusion — so the two runs see the "
        "same attribution, not the same event-type hygiene.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", type=Path, default=DEFAULT_EXPORT)
    args = ap.parse_args()

    matches = json.loads(args.export.read_text(encoding="utf-8"))
    compiler = compile_corpus(matches)
    current = current_derivation(matches)
    report = render_report(compiler, current)

    print(report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\nwrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
