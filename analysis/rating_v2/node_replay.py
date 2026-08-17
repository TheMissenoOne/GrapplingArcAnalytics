"""Node evidence shadow replay + ADR-03 sweep — reads the DB, writes only a JSON artefact.

    uv run python -m analysis.rating_v2.node_replay [--out DIR]

``node_key`` uses ``analysis.names._normalize_name`` + ``canonicalize`` — the SAME derivation
the rest of the repo uses for athlete graphs (``athlete_elo.py``, ``athlete_graph.py``), which
in turn must stay char-for-char with the App's ``normalizeLabel()`` (root CLAUDE.md contract).
This module never invents its own normalization.

ADR-03 fixes the acceptance order BEFORE the sweep: (1) predictive log loss out-of-sample,
temporal split train<=T / predict T+1; (2) calibration of low-RD nodes; (3) rank stability
under bootstrap; (4) fraction of nodes still essentially at the prior. Spread is never a
criterion. "No weight improves prediction" is a valid, publishable outcome (median ~1 bout
per athlete makes it plausible) — this module reports it plainly rather than picking a winner
by any other axis.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name, canonicalize
from analysis.rating_v2.config import EngineConfig
from analysis.rating_v2.glicko2 import expected_score
from analysis.rating_v2.models import RatingState
from analysis.rating_v2.node_periods import NodeBout, NodeConfig, run_node_periods
from analysis.rating_v2.periods import Bout
from analysis.rating_v2.replay import (
    MatchRow,
    _discipline_of,
    _score_a,
    build_seeds,
    load_discipline_map,
)

DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent.parent / "reports" / "rating_v2"

# ADR-03 sweep grid.
NODE_WEIGHTS = (0.10, 0.25, 0.50, 1.00)
NODE_INITIAL_RDS = (220.0, 280.0, 350.0)
NODE_TAUS = (0.3, 0.5, 0.8)

# Temporal split (2026-08-17 corpus): 2025 is the largest single eligible year (191 bouts) —
# train on every period <= TRAIN_CUTOFF, predict TRAIN_CUTOFF+1.
TRAIN_CUTOFF = 2024
HOLDOUT_YEAR = 2025

LOW_RD_THRESHOLD = 150.0  # "low RD node" for calibration (criterion 2)
# |rating - seed_rating| below this => "essentially prior" (criterion 4).
STILL_AT_PRIOR_DELTA = 10.0
N_BOOTSTRAP = 20
_EPS = 1e-9


def node_key_of(label: str) -> str:
    return canonicalize(_normalize_name(str(label)))


def _nodes_for_actor(
    sequence: list[dict[str, Any]] | None, actor_id: str
) -> tuple[frozenset[str], dict[str, int]]:
    occurrences: dict[str, int] = {}
    for event in sequence or []:
        if not isinstance(event, dict) or event.get("actor_id") != actor_id:
            continue
        label = event.get("label")
        if not label:
            continue
        key = node_key_of(label)
        occurrences[key] = occurrences.get(key, 0) + 1
    return frozenset(occurrences), occurrences


def build_node_bouts(
    matches: list[MatchRow],
    config: EngineConfig,
    events_map: dict[str, str],
    null_discipline: str,
) -> tuple[list[NodeBout], dict[str, int]]:
    """Same eligibility filter as ``replay.build_bouts`` (kept in lockstep by
    ``test_build_node_bouts_eligibility_matches_build_bouts``), plus node extraction from
    ``MatchRow.sequence``."""
    counters = {
        "total": len(matches),
        "not_final": 0,
        "self_match": 0,
        "out_of_discipline": 0,
        "no_year": 0,
        "unknown_winner": 0,
        "eligible": 0,
    }
    node_bouts: list[NodeBout] = []
    for m in matches:
        if m.status != "final":
            counters["not_final"] += 1
            continue
        if m.athlete_a_id == m.athlete_b_id:
            counters["self_match"] += 1
            continue
        discipline = _discipline_of(m.event, events_map, null_discipline)
        if discipline not in config.disciplines:
            counters["out_of_discipline"] += 1
            continue
        if m.year is None:
            counters["no_year"] += 1
            continue
        score_a = _score_a(m.winner_id, m.athlete_a_id, m.win_type)
        if score_a is None:
            counters["unknown_winner"] += 1
            continue
        counters["eligible"] += 1
        nodes_a, occ_a = _nodes_for_actor(m.sequence, m.athlete_a_id)
        nodes_b, occ_b = _nodes_for_actor(m.sequence, m.athlete_b_id)
        bout = Bout(
            period=m.year, athlete_a=m.athlete_a_id, athlete_b=m.athlete_b_id, score_a=score_a
        )
        node_bouts.append(
            NodeBout(
                bout=bout,
                nodes_a=nodes_a,
                nodes_b=nodes_b,
                occurrences_a=occ_a,
                occurrences_b=occ_b,
            )
        )
    return node_bouts, counters


def _blend(states: list[RatingState]) -> RatingState:
    """Precision-weighted (1/RD^2) combine of a global state + an athlete's node states, all
    on the same rating scale (a node is seeded FROM the global rating — doc 02). This is the
    module's own formalization of "prediction from a global-scale, node-informed estimate";
    the source docs describe the node track's evidence flow but not this exact combination
    formula, so it is spelled out here rather than assumed silently.
    """
    if not states:
        raise ValueError("need at least one state to blend")
    total_precision = sum(1.0 / (s.deviation**2) for s in states)
    rating = sum(s.rating / (s.deviation**2) for s in states) / total_precision
    deviation = (1.0 / total_precision) ** 0.5
    return RatingState(rating, deviation)


def _log_loss(predicted_p: float, score: float) -> float:
    p = min(max(predicted_p, _EPS), 1.0 - _EPS)
    return -(score * math.log(p) + (1.0 - score) * math.log(1.0 - p))


def _node_blend_for(
    athlete: str,
    global_states: dict[str, RatingState],
    node_states: dict[tuple[str, str], RatingState],
) -> RatingState | None:
    global_state = global_states.get(athlete)
    if global_state is None:
        return None
    athlete_nodes = [s for (a, _n), s in node_states.items() if a == athlete]
    return _blend([global_state, *athlete_nodes])


def _criterion_1_log_loss(
    node_bouts: list[NodeBout],
    engine_config: EngineConfig,
    node_config: NodeConfig,
    rank_elo_by_athlete: dict[str, float],
) -> dict[str, Any]:
    """Walk-forward: train on periods <= TRAIN_CUTOFF, predict HOLDOUT_YEAR bouts."""
    train_bouts = [nb for nb in node_bouts if nb.bout.period <= TRAIN_CUTOFF]
    holdout_bouts = [nb for nb in node_bouts if nb.bout.period == HOLDOUT_YEAR]

    seeds = build_seeds([nb.bout for nb in node_bouts], engine_config, rank_elo_by_athlete)
    global_states, node_states, _ = run_node_periods(seeds, train_bouts, engine_config, node_config)

    baseline_losses: list[float] = []
    candidate_losses: list[float] = []
    for nb in holdout_bouts:
        b = nb.bout
        g_a, g_b = global_states.get(b.athlete_a), global_states.get(b.athlete_b)
        if g_a is None or g_b is None:
            continue
        p_baseline = expected_score(g_a.rating, g_a.deviation, g_b.rating, g_b.deviation)
        baseline_losses.append(_log_loss(p_baseline, b.score_a))

        blend_a = _node_blend_for(b.athlete_a, global_states, node_states) or g_a
        blend_b = _node_blend_for(b.athlete_b, global_states, node_states) or g_b
        p_candidate = expected_score(
            blend_a.rating, blend_a.deviation, blend_b.rating, blend_b.deviation
        )
        candidate_losses.append(_log_loss(p_candidate, b.score_a))

    return {
        "holdout_bouts": len(baseline_losses),
        "baseline_log_loss": statistics.fmean(baseline_losses) if baseline_losses else None,
        "candidate_log_loss": statistics.fmean(candidate_losses) if candidate_losses else None,
        "improves": (
            statistics.fmean(candidate_losses) <= statistics.fmean(baseline_losses)
            if baseline_losses and candidate_losses
            else False
        ),
    }


def _criterion_2_calibration(
    node_bouts: list[NodeBout],
    engine_config: EngineConfig,
    node_config: NodeConfig,
    rank_elo_by_athlete: dict[str, float],
) -> dict[str, Any]:
    """Among low-RD nodes as of end of training, does predicted win prob (node vs opponent's
    pre-holdout global) match the observed rate in the holdout year?"""
    train_bouts = [nb for nb in node_bouts if nb.bout.period <= TRAIN_CUTOFF]
    holdout_bouts = [nb for nb in node_bouts if nb.bout.period == HOLDOUT_YEAR]

    seeds = build_seeds([nb.bout for nb in node_bouts], engine_config, rank_elo_by_athlete)
    global_states, node_states, _ = run_node_periods(seeds, train_bouts, engine_config, node_config)

    pairs: list[tuple[float, float]] = []  # (predicted, observed)
    for nb in holdout_bouts:
        b = nb.bout
        g_a, g_b = global_states.get(b.athlete_a), global_states.get(b.athlete_b)
        if g_a is None or g_b is None:
            continue
        for node_key in nb.nodes_a:
            state = node_states.get((b.athlete_a, node_key))
            if state is None or state.deviation >= LOW_RD_THRESHOLD:
                continue
            p = expected_score(state.rating, state.deviation, g_b.rating, g_b.deviation)
            pairs.append((p, b.score_a))
        for node_key in nb.nodes_b:
            state = node_states.get((b.athlete_b, node_key))
            if state is None or state.deviation >= LOW_RD_THRESHOLD:
                continue
            p = expected_score(state.rating, state.deviation, g_a.rating, g_a.deviation)
            pairs.append((p, 1.0 - b.score_a))

    if not pairs:
        return {
            "low_rd_node_observations": 0,
            "mean_predicted": None,
            "mean_observed": None,
            "abs_gap": None,
        }
    mean_p = statistics.fmean(p for p, _ in pairs)
    mean_o = statistics.fmean(o for _, o in pairs)
    return {
        "low_rd_node_observations": len(pairs),
        "mean_predicted": mean_p,
        "mean_observed": mean_o,
        "abs_gap": abs(mean_p - mean_o),
    }


def _conservative_node_ratings(
    node_bouts: list[NodeBout],
    engine_config: EngineConfig,
    node_config: NodeConfig,
    rank_elo_by_athlete: dict[str, float],
) -> dict[tuple[str, str], float]:
    seeds = build_seeds([nb.bout for nb in node_bouts], engine_config, rank_elo_by_athlete)
    _, node_states, node_meta = run_node_periods(seeds, node_bouts, engine_config, node_config)
    return {
        key: state.rating - state.deviation
        for key, state in node_states.items()
        if node_meta.get(key, None) and node_meta[key].bouts_observed >= 2
    }


def _criterion_3_bootstrap_stability(
    node_bouts: list[NodeBout],
    engine_config: EngineConfig,
    node_config: NodeConfig,
    rank_elo_by_athlete: dict[str, float],
    n_bootstrap: int = N_BOOTSTRAP,
    seed: int = 42,
) -> dict[str, Any]:
    from scipy.stats import spearmanr

    baseline = _conservative_node_ratings(
        node_bouts, engine_config, node_config, rank_elo_by_athlete
    )
    if len(baseline) < 2:
        return {"nodes_with_2plus_bouts": len(baseline), "mean_spearman": None}

    rng = random.Random(seed)
    correlations: list[float] = []
    for _ in range(n_bootstrap):
        resample = [node_bouts[rng.randrange(len(node_bouts))] for _ in range(len(node_bouts))]
        sample_ratings = _conservative_node_ratings(
            resample, engine_config, node_config, rank_elo_by_athlete
        )
        common = sorted(set(baseline) & set(sample_ratings))
        if len(common) < 2:
            continue
        rho, _ = spearmanr([baseline[k] for k in common], [sample_ratings[k] for k in common])
        if rho == rho:  # not NaN
            correlations.append(float(rho))

    return {
        "nodes_with_2plus_bouts": len(baseline),
        "n_bootstrap": len(correlations),
        "mean_spearman": statistics.fmean(correlations) if correlations else None,
    }


def _criterion_4_fraction_at_prior(
    node_bouts: list[NodeBout],
    engine_config: EngineConfig,
    node_config: NodeConfig,
    rank_elo_by_athlete: dict[str, float],
) -> dict[str, Any]:
    """Fraction of nodes whose rating barely moved from its seed (ADR-03: "fração de nós que
    continua essencialmente no prior"). Measured on the rating point estimate, not RD — RD
    shrinks with evidence *volume* even at ``node_weight`` this small, so an RD-only test
    would call a node "moved" after a single low-weight observation even though its rating
    estimate is still ~unchanged. ``STILL_AT_PRIOR_DELTA`` rating points is the threshold.
    """
    seeds = build_seeds([nb.bout for nb in node_bouts], engine_config, rank_elo_by_athlete)
    _, node_states, node_meta = run_node_periods(seeds, node_bouts, engine_config, node_config)
    if not node_states:
        return {"total_nodes": 0, "fraction_at_prior": None}
    at_prior = sum(
        1
        for key, s in node_states.items()
        if abs(s.rating - node_meta[key].seed_rating) < STILL_AT_PRIOR_DELTA
    )
    bouts_observed = [node_meta[key].bouts_observed for key in node_states]
    n_single = sum(1 for n in bouts_observed if n <= 1)
    return {
        "total_nodes": len(node_states),
        "at_prior": at_prior,
        "fraction_at_prior": at_prior / len(node_states),
        "median_bouts_observed": statistics.median(bouts_observed) if bouts_observed else 0,
        "fraction_single_bout": (n_single / len(bouts_observed)) if bouts_observed else None,
    }


def run_sweep(
    node_bouts: list[NodeBout],
    engine_config: EngineConfig,
    rank_elo_by_athlete: dict[str, float],
) -> list[dict[str, Any]]:
    results = []
    for weight in NODE_WEIGHTS:
        for initial_rd in NODE_INITIAL_RDS:
            for tau in NODE_TAUS:
                node_config = NodeConfig(
                    node_weight=weight, node_initial_rd=initial_rd, node_tau=tau
                )
                row = {
                    "node_weight": weight,
                    "node_initial_rd": initial_rd,
                    "node_tau": tau,
                    "criterion_1_log_loss": _criterion_1_log_loss(
                        node_bouts, engine_config, node_config, rank_elo_by_athlete
                    ),
                    "criterion_2_calibration": _criterion_2_calibration(
                        node_bouts, engine_config, node_config, rank_elo_by_athlete
                    ),
                    "criterion_3_bootstrap_stability": _criterion_3_bootstrap_stability(
                        node_bouts, engine_config, node_config, rank_elo_by_athlete
                    ),
                    "criterion_4_fraction_at_prior": _criterion_4_fraction_at_prior(
                        node_bouts, engine_config, node_config, rank_elo_by_athlete
                    ),
                }
                results.append(row)
    return results


def run_node_replay(engine_config: EngineConfig) -> dict[str, Any]:
    """DB-reading orchestration. Mirrors ``replay.run_replay``; the only impure function here."""
    from sqlalchemy import select

    from db.base import db_session
    from db.models import Athlete, Match

    events_map, null_discipline = load_discipline_map()

    with db_session() as session:
        rows = session.execute(
            select(
                Match.athlete_a_id, Match.athlete_b_id, Match.winner_id,
                Match.event, Match.year, Match.win_type, Match.status, Match.sequence,
            )
        ).all()
        matches = [MatchRow(*r) for r in rows]

        rank_elo_rows = session.execute(
            select(Athlete.id, Athlete.rank_elo).where(Athlete.rank_elo.is_not(None))
        ).all()
        rank_elo_by_athlete = {aid: float(elo) for aid, elo in rank_elo_rows}

    node_bouts, coverage = build_node_bouts(matches, engine_config, events_map, null_discipline)
    sweep = run_sweep(node_bouts, engine_config, rank_elo_by_athlete)

    return {
        "engine_config": engine_config.to_dict(),
        "train_cutoff": TRAIN_CUTOFF,
        "holdout_year": HOLDOUT_YEAR,
        "coverage": coverage,
        "total_node_bouts": len(node_bouts),
        "sweep": sweep,
    }


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Rating V2 node evidence sweep (read-only).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    result = run_node_replay(EngineConfig())

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / "node-sweep.json"
    out_path.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n")
    print(f"wrote {out_path}")
    print(json.dumps(result["coverage"], indent=2))


if __name__ == "__main__":
    main()
