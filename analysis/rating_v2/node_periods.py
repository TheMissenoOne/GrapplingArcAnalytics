"""Node-level Glicko-2 observation layer — wave 5 shadow (ADR-03, plan bundle
``docs/02_ENGINE_ARCHITECTURE.md`` "Node state"). Pure — zero DB/file/network I/O.

Node observation model:

- one unique ``node_key`` used by an athlete in one bout contributes AT MOST ONE Glicko
  observation for that bout, regardless of how many times the label repeats in the
  sequence (repeats bump ``NodeMeta.occurrences``, never ``NodeMeta.bouts_observed``);
- a node first seen for an athlete starts at that athlete's CURRENT global rating (the
  pre-period global snapshot at the period it is first observed), with the sweep's node
  initial RD — never the belt floor, never re-seeded on a later sighting;
- the node's opponent in its own Glicko update is the opponent's PRE-PERIOD GLOBAL state,
  never the opponent's own node state (doc 02: node observations use "opponent's pre-period
  global state"); score mirrors the bout's actual result, at ``NodeConfig.node_weight`` < 1
  because an annotated sequence is partial evidence;
- a bout with no sequence for a side (or where a previously-seen node just doesn't recur)
  contributes NO observation for that node this period — never a negative observation. The
  node still exists and widens for the gap, exactly like an inactive athlete widens in
  ``periods.py`` (ADR-09's widening rule extends here by the same logic: widening answers
  "how much do we know about this node TODAY", not "as of its last sighting").

The global track computed alongside is bit-identical to ``periods.run_periods`` on the same
bout set — node evidence never feeds back into it (doc 02 draws global and node as separate
branches off the same evidence, not layered updates). It is duplicated here rather than
imported because ``periods.run_periods`` doesn't expose the pre-period snapshot each period
needs for node first-sight seeding; ``test_node_global_track_matches_periods_run_periods``
in ``tests/test_rating_v2.py`` is the lockstep gate against drift.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from analysis.rating_v2.config import EngineConfig
from analysis.rating_v2.glicko2 import update_period
from analysis.rating_v2.models import Observation, RatingState
from analysis.rating_v2.periods import Bout

NodeStateKey = tuple[str, str]  # (athlete_id, node_key)


@dataclass(frozen=True)
class NodeConfig:
    """Sweep parameters for the node evidence layer (ADR-03, doc 06_VALIDATION_AND_BACKTEST.md).

    Independent of ``EngineConfig.tau`` — the global track keeps whatever tau produced the
    wave-3 baseline (0.5); ``node_tau`` is the node track's own volatility smoothing.
    """

    node_weight: float = 0.25
    node_initial_rd: float = 350.0
    node_tau: float = 0.5
    node_initial_volatility: float = 0.06


@dataclass(frozen=True)
class NodeBout:
    """One eligible bout (same eligibility as ``periods.Bout``) plus which nodes each side's
    OWN sequence touched — already deduped per bout. ``occurrences_a``/``occurrences_b`` keep
    the raw repeat count per node for reporting only; they never multiply evidence weight."""

    bout: Bout
    nodes_a: frozenset[str] = frozenset()
    nodes_b: frozenset[str] = frozenset()
    occurrences_a: Mapping[str, int] = field(default_factory=dict)
    occurrences_b: Mapping[str, int] = field(default_factory=dict)


@dataclass
class NodeMeta:
    """Per-(athlete, node_key) evidence bookkeeping. ``bouts_observed`` drives the Glicko
    game count; ``occurrences`` is metadata only and never multiplies evidence.
    ``seed_rating`` is the athlete's global rating at the moment the node was first
    seeded — kept so callers can measure how far the node's rating has actually moved from
    its prior (ADR-03 criterion 4), independent of RD, which shrinks with evidence *volume*
    even when the rating point estimate barely moves."""

    bouts_observed: int = 0
    occurrences: int = 0
    seed_rating: float = 0.0


def run_node_periods(
    global_seeds: Mapping[str, RatingState],
    node_bouts: Sequence[NodeBout],
    engine_config: EngineConfig,
    node_config: NodeConfig,
) -> tuple[dict[str, RatingState], dict[NodeStateKey, RatingState], dict[NodeStateKey, NodeMeta]]:
    """Replay the global track and the node track together, period by period.

    Returns ``(global_states, node_states, node_meta)``.
    """
    if not node_bouts:
        return dict(global_seeds), {}, {}

    periods = sorted({nb.bout.period for nb in node_bouts})
    global_states: dict[str, RatingState] = dict(global_seeds)
    node_states: dict[NodeStateKey, RatingState] = {}
    node_meta: dict[NodeStateKey, NodeMeta] = defaultdict(NodeMeta)

    for period in periods:
        period_items = [nb for nb in node_bouts if nb.bout.period == period]
        pre_global = dict(global_states)  # snapshot every bout this period reads

        # ── global track — identical math to periods.run_periods ──
        global_obs: dict[str, list[Observation]] = defaultdict(list)
        for nb in period_items:
            b = nb.bout
            a_state, b_state = pre_global.get(b.athlete_a), pre_global.get(b.athlete_b)
            if a_state is None or b_state is None:
                continue
            global_obs[b.athlete_a].append(
                Observation(b_state.rating, b_state.deviation, b.score_a)
            )
            global_obs[b.athlete_b].append(
                Observation(a_state.rating, a_state.deviation, 1.0 - b.score_a)
            )
        for athlete_id, state in pre_global.items():
            global_states[athlete_id] = update_period(
                state, global_obs.get(athlete_id, []), tau=engine_config.tau
            )

        # ── node track — opponent is the opponent's PRE-PERIOD global state ──
        pre_node = dict(node_states)
        new_this_period: dict[NodeStateKey, RatingState] = {}
        node_obs: dict[NodeStateKey, list[Observation]] = defaultdict(list)

        def _touch(
            athlete: str,
            opponent_global: RatingState,
            nodes: frozenset[str],
            score: float,
            occurrences: Mapping[str, int],
        ) -> None:
            athlete_global = pre_global.get(athlete)
            if athlete_global is None:
                return
            for node_key in nodes:
                key = (athlete, node_key)
                if key not in pre_node and key not in new_this_period:
                    new_this_period[key] = RatingState(
                        athlete_global.rating,
                        node_config.node_initial_rd,
                        node_config.node_initial_volatility,
                    )
                    node_meta[key].seed_rating = athlete_global.rating
                node_obs[key].append(
                    Observation(
                        opponent_global.rating,
                        opponent_global.deviation,
                        score,
                        weight=node_config.node_weight,
                    )
                )
                node_meta[key].bouts_observed += 1
            for node_key, count in occurrences.items():
                node_meta[(athlete, node_key)].occurrences += count

        for nb in period_items:
            b = nb.bout
            a_global, b_global = pre_global.get(b.athlete_a), pre_global.get(b.athlete_b)
            if a_global is None or b_global is None:
                continue
            _touch(b.athlete_a, b_global, nb.nodes_a, b.score_a, nb.occurrences_a)
            _touch(b.athlete_b, a_global, nb.nodes_b, 1.0 - b.score_a, nb.occurrences_b)

        period_start_nodes = {**pre_node, **new_this_period}
        for key, state in period_start_nodes.items():
            node_states[key] = update_period(state, node_obs.get(key, []), tau=node_config.node_tau)

    return global_states, node_states, dict(node_meta)
