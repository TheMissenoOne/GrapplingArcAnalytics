"""Per-node Glicko-2 for ATHLETE graphs — the production node-rating track (ADR-16).

This is the Analytics half of the cutover the App already made: ``computed_elo`` on an
athlete's graph nodes stops being V1's per-match delta split and becomes a per-node Glicko-2
rating, projected at the PRODUCER so every downstream consumer moves in one step
(``deviance``, edge ELO, ``athlete_systems``, ``export/site_data``, ``analysis/ocean``,
``export/ontology``). The App's equivalent is
``GrapplingArcApp/src/services/rating/ratingV2Projection.ts``.

Not to be confused with :mod:`analysis.rating_v2.node_periods` /
:mod:`analysis.rating_v2.node_replay`. Those are the wave-5 SHADOW study that ADR-03 stands
on: one observation per node per bout, scored by the BOUT's result. They are frozen evidence
for a published decision and are deliberately left untouched — re-pointing them at this
module's observation model would silently invalidate the sweep the ADR quotes.

## The observation model, and the one measurement that shaped it

An observation is one *event* in the athlete's own side of a bout's ``sequence``:

- **score** comes from the event's ``successful`` flag — 1.0 landed, 0.0 missed;
- **an event with ``successful`` NULL produces NO observation.** Measured on the 2026-08-26
  corpus: 6818 of 10075 own-actor labelled events (67.7%) carry no flag. Reading NULL as
  landed would score 82.5% of the corpus as a success; reading it as missed (Python's
  historical default) would score 85% as a failure. Both fabricate evidence. This is ADR-06
  one level down — a missing outcome is lost coverage, never a manufactured result — and it
  is the same reason that ADR refuses to turn 271 winner-less decisions into draws;
- **the observation's "opponent" is the athlete's OWN pre-period global state**, from the same
  corpus-wide replay that produces the published athlete rating
  (``periods.run_periods_with_snapshots``). A node is seeded AT that rating, so its first
  expected score is exactly 0.5: the question a node rating answers is "does this technique
  land more often than a coin flip, for me", and the node then drifts above or below my own
  level by how reliably it works.

  **This was measured, not assumed.** The first implementation anchored each observation on
  the OPPONENT's global state — measure the technique against the person it was used on. On
  the 2026-08-26 corpus that produced ``corr(athlete global, mean node offset) = −0.790``
  over the 271 athletes with ≥3 evidenced nodes: athletes below 1700 sat +84 above their own
  global, athletes above 1950 sat −152 below. The cause is that "the technique landed" and
  "the bout was won" are different Bernoulli variables — a dominant athlete wins ~90% of
  bouts and lands 46% of annotated attempts, so anchoring on the opponent made every one of
  their techniques read as below their own level, and every EVIDENCED node fall below every
  SEEDED one. That inverts any within-athlete "best technique" reading. Own-anchor removes
  the coupling to athlete strength and is what the App already does (``ratingV2Evidence.ts``
  centres its virtual partner on the athlete's current global), so the two engines now answer
  the same question. Opponent strength has not been discarded — it is exactly what the
  GLOBAL track measures, and the node is expressed relative to it;
- **weight** is ``NODE_EVENT_WEIGHT × n × share``, where ``share`` is
  ``markov_weights.relative_shares`` over the bout side's scored events and ``n`` is how many
  there are — a MEAN-1 vector. See "Weight semantics" below;
- **a node first seen for an athlete is seeded at that athlete's pre-period global rating**,
  never at a belt floor — and is RE-ANCHORED on the athlete's final global at the end, keeping
  the evidence term it earned (``run_node_ratings``' ``global_final``). Seeding fixes the
  anchor at first sighting; re-anchoring stops that anchor going stale as the athlete's own
  rating moves, and makes an evidenced node comparable to a never-seen one, which
  ``project_onto_graph`` places at the final global. Partial coverage on a rating field is not
  a smaller version of full coverage, it is two units in one distribution: the App measured
  σ 354 / 0 signatures with a
  raw projection against σ 31.9 / 1 signature once unseen nodes were seeded at the global.
  Everything reading ``computed_elo`` compares a node to its neighbours, so the neighbours
  have to be on one scale.

## Weight semantics — why a weighted observation is still Glicko-2

Glicko-2 has no notion of a fractional game, but the ``weight`` field in
:func:`analysis.rating_v2.glicko2.update_period` multiplies BOTH accumulators identically —
the information term ``g²·E·(1−E)`` and the score residual ``g·(s−E)``. For an integer
weight that is exactly the same arithmetic as repeating the observation that many times, so
the fractional case is the continuous extension of repeat-count expansion: weight *w* means
"*w* games' worth of this result". That is Glickman's own device for partial and time-decayed
evidence, and it is the identical mechanism the App applies to
``RATING_V2_CALIBRATION.ATTEMPT_WEIGHT`` in ``glicko2.ts`` — same field, same reading, both
sides.

The Markov vector is normalised to **mean 1**, not to sum 1. Mean-1 leaves the TOTAL
information a bout contributes at ``NODE_EVENT_WEIGHT × n`` — unchanged from a flat-weight
run — and moves only the SPLIT across actions, which is precisely the invariant the
``Markov action weights`` row of the root ``CLAUDE.md`` already declares for the V1 split.
Sum-1 was rejected: it would make a six-event bout carry the same information as a one-event
bout, a magnitude change nobody asked for. Raw block weights were rejected for the same
reason in reverse — total information would then wander with which actions happened to
appear.

Family selection follows :mod:`analysis.markov_weights` (owner decision, 2026-08-26): the
``adcc`` block for a bout whose ``ruleset_scoring.family_of`` says ADCC, ``global``
otherwise. The App bundles ``global`` only, because a training round is run under no rule
book.

Privacy class **A, public competition data**. Every athlete-side query here filters
``owner_kind='athlete'`` at its call site (``db.repository``); nothing in this module reads a
user graph, and user node ratings stay on the user's device by construction.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from analysis.lamas_chain import lamas_state
from analysis.markov_weights import relative_shares
from analysis.names import _normalize_name, canonicalize
from analysis.rating_v2.config import EngineConfig
from analysis.rating_v2.glicko2 import update_period
from analysis.rating_v2.models import Observation, RatingState
from analysis.rating_v2.periods import Bout, run_periods_with_snapshots

# ── Calibration constants (ADR-13: named ONCE, all UNVALIDATED) ─────────────────────────
#: Evidence weight of ONE scored own-side competition event, before the Markov split. A
#: single annotated technique is not a whole bout's worth of information about the node, so
#: it counts for a fraction of a Glicko "game". 0.25 is the wave-5 sweep's own default
#: (``node_periods.NodeConfig.node_weight``) and the conservative end of the grid ADR-03
#: measured. UNVALIDATED — recalibrate by ADR-03's criterion (out-of-sample log loss first,
#: spread never), not by how the numbers look.
NODE_EVENT_WEIGHT = 0.25

#: RD a node starts at the first time an athlete is seen using it. Deliberately the same
#: number as the App's ``RATING_V2_CALIBRATION.NODE_RD_SEED`` — the App reused the Analytics
#: athlete-node seed rather than invent a second value for the same concept, and keeping them
#: equal is what makes "a node the model has barely seen" mean the same thing on both sides.
#: The ADR-03 grid marginally preferred 220 on log loss, under a DIFFERENT observation model
#: (bout-scored, not event-scored); that measurement does not transfer. UNVALIDATED.
NODE_INITIAL_RD = 350.0

#: Volatility smoothing for the node track, independent of ``EngineConfig.tau`` (which the
#: global track owns). ADR-03 measured tau NON-IDENTIFIABLE on this corpus — 0.3/0.5/0.8 gave
#: identical results to four decimal places — so this is 0.5 by inheritance, not by choice.
NODE_TAU = 0.5

#: Standard Glicko-2 starting volatility. Not a product calibration candidate.
NODE_INITIAL_VOLATILITY = 0.06

NodeKey = tuple[str, str]  # (athlete_id, node_key)


def node_key_of(label: str) -> str:
    """The canonical ``node_key`` for a sequence label.

    ``canonicalize(_normalize_name(...))`` — the SAME derivation ``athlete_elo``,
    ``athlete_graph`` and ``node_replay`` use, and therefore the same key space the persisted
    graph edges live in. ``_normalize_name`` is the char-for-char twin of the App's
    ``normalizeLabel()`` (root CLAUDE.md contract). This module never invents its own.
    """
    return canonicalize(_normalize_name(str(label)))


@dataclass(frozen=True)
class NodeObservation:
    """One scored event on one side of one bout."""

    node_key: str
    score: float
    weight: float


@dataclass(frozen=True)
class NodeEvidenceBout:
    """An eligible bout plus each side's scored node observations."""

    bout: Bout
    observations_a: tuple[NodeObservation, ...] = ()
    observations_b: tuple[NodeObservation, ...] = ()


@dataclass(frozen=True)
class NodeRating:
    """A node's final Glicko-2 state plus the evidence behind it.

    ``observations`` and ``bouts`` are carried because a rating with no evidence behind it
    reads exactly like one with plenty, and every honest consumer of this field needs to be
    able to tell them apart.
    """

    rating: float
    deviation: float
    volatility: float
    observations: int
    bouts: int
    #: The athlete's global rating at the period this node was FIRST seen — the anchor the
    #: Glicko replay ran against.
    seed_rating: float
    #: What the evidence actually earned: the replayed rating minus that anchor. This is the
    #: strength-neutral part (measured corr with athlete rating: +0.109), and ``rating`` above
    #: is it re-expressed against the athlete's CURRENT global.
    offset: float = 0.0


def observations_for_side(
    sequence: Iterable[Any] | None,
    actor_id: str,
    block: Mapping[str, float] | None,
) -> tuple[NodeObservation, ...]:
    """One bout side's scored events → weighted node observations.

    Events without a label, without ``actor_id == actor_id``, or with ``successful`` NULL
    produce nothing at all (see the module docstring). The Markov codes are read from the RAW
    corpus event, so ``lamas_state`` sees the ``type``/``label``/``successful`` it was
    derived under — including its own rule that only ``successful is True`` earns a success
    code, which is why the code lookup and the score read the same field differently and
    correctly.
    """
    scored: list[tuple[str, float, Any]] = []
    for event in sequence or []:
        if not isinstance(event, dict) or event.get("actor_id") != actor_id:
            continue
        label = event.get("label")
        successful = event.get("successful")
        if not label or successful is None:
            continue
        key = node_key_of(label)
        if not key:
            continue
        scored.append((key, 1.0 if successful else 0.0, event))

    if not scored:
        return ()
    shares = relative_shares([lamas_state(event) for _k, _s, event in scored], block)
    n = len(scored)
    return tuple(
        NodeObservation(key, score, NODE_EVENT_WEIGHT * n * share)
        for (key, score, _event), share in zip(scored, shares)
    )


def run_node_ratings(
    global_snapshots: Mapping[int, Mapping[str, RatingState]],
    evidence: Sequence[NodeEvidenceBout],
    global_final: Mapping[str, RatingState] | None = None,
) -> dict[NodeKey, NodeRating]:
    """Replay the node track over the periods the global track already replayed.

    ``global_snapshots`` is ``periods.run_periods_with_snapshots``' second return — the
    PRE-period global state of every athlete in every period. This function never advances
    the global track itself; the two must not disagree, and the cheapest guarantee of that is
    having exactly one implementation.

    Every known node gets a Glicko period in every period, so a node nobody used this year
    widens for inactivity — the same reading ADR-09 fixed for athletes ("how much do we know
    about this TODAY"), applied one level down.

    ``global_final`` RE-ANCHORS each node on the athlete's current rating, keeping the
    evidence term it earned: ``rating = final + (replayed − seed)``. Without it a node carries
    the global its athlete held the period it was FIRST seen, which is a stale anchor and was
    measured to be the whole of the residual bias — ``corr(athlete rating, seed − final) =
    −0.855``, versus ``+0.109`` for the evidence term itself. An improving athlete's every
    technique would otherwise read below their current level for no reason but the passage of
    time. It also makes evidenced nodes agree with ``project_onto_graph``, which already seeds
    NEVER-seen nodes at the final global — two anchors in one graph is the mixed-unit state
    this whole layer exists to avoid. RD is untouched: this is a change of anchor, not of
    information. Omit it (tests) to get the raw replayed ratings.
    """
    by_period: dict[int, list[NodeEvidenceBout]] = defaultdict(list)
    for item in evidence:
        by_period[item.bout.period].append(item)

    states: dict[NodeKey, RatingState] = {}
    obs_count: dict[NodeKey, int] = defaultdict(int)
    bout_count: dict[NodeKey, int] = defaultdict(int)
    seed_rating: dict[NodeKey, float] = {}

    for period in sorted(global_snapshots):
        pre_global = global_snapshots[period]
        fresh: dict[NodeKey, RatingState] = {}
        period_obs: dict[NodeKey, list[Observation]] = defaultdict(list)

        for item in by_period.get(period, ()):
            b = item.bout
            a_global, b_global = pre_global.get(b.athlete_a), pre_global.get(b.athlete_b)
            if a_global is None or b_global is None:
                continue
            for athlete, own_global, side in (
                (b.athlete_a, a_global, item.observations_a),
                (b.athlete_b, b_global, item.observations_b),
            ):
                touched: set[str] = set()
                for obs in side:
                    key = (athlete, obs.node_key)
                    if key not in states and key not in fresh:
                        fresh[key] = RatingState(
                            own_global.rating, NODE_INITIAL_RD, NODE_INITIAL_VOLATILITY
                        )
                        seed_rating[key] = own_global.rating
                    # The node is scored against its OWN athlete's pre-period global — see the
                    # module docstring. A node starts life AT that rating, so its first
                    # expected score is exactly 0.5 and "did it land" is measured against a
                    # coin flip rather than against the bout's win probability.
                    period_obs[key].append(
                        Observation(
                            own_global.rating,
                            own_global.deviation,
                            obs.score,
                            weight=obs.weight,
                        )
                    )
                    obs_count[key] += 1
                    touched.add(obs.node_key)
                for node in touched:
                    bout_count[(athlete, node)] += 1

        for key, state in {**states, **fresh}.items():
            states[key] = update_period(state, period_obs.get(key, []), tau=NODE_TAU)

    out: dict[NodeKey, NodeRating] = {}
    for key, state in states.items():
        seed = seed_rating.get(key, state.rating)
        offset = state.rating - seed
        anchor = (global_final or {}).get(key[0])
        out[key] = NodeRating(
            rating=(anchor.rating + offset) if anchor is not None else state.rating,
            deviation=state.deviation,
            volatility=state.volatility,
            observations=obs_count[key],
            bouts=bout_count[key],
            seed_rating=seed,
            offset=offset,
        )
    return out


@dataclass(frozen=True)
class ProjectionResult:
    """What a projection did, so a caller can log its coverage."""

    evidenced: int
    seeded: int


def project_onto_graph(
    graph: Any,
    athlete_id: str,
    node_ratings: Mapping[NodeKey, NodeRating],
    global_rating: float | None,
) -> ProjectionResult:
    """Overwrite an ``AthleteGraph``'s node/edge/user ELO from the node track. Mutates.

    Returns ``(evidenced, seeded)``. A node the replay never saw takes ``global_rating`` —
    the seeding rule the module docstring justifies. ``global_rating`` NULL (an athlete with
    no state in the V2 run at all: out-of-discipline by ADR-05, or every bout excluded by
    ADR-06) means there is nothing to project onto and the V1 numbers are left ALONE. Half a
    projection is the one state that must never be persisted.
    """
    if global_rating is None:
        return ProjectionResult(0, 0)

    evidenced = seeded = 0
    for key, node in graph.nodes.items():
        rated = node_ratings.get((athlete_id, key))
        if rated is None:
            node.computed_elo = global_rating
            seeded += 1
        else:
            node.computed_elo = rated.rating
            evidenced += 1

    # Edge ELO is the mean of its endpoints — the same derivation ``athlete_elo`` uses. It has
    # to be re-derived HERE because it is the only per-node number that survives to the DB
    # (``graph_edges.elo``); leaving it on the V1 scale under V2 nodes is the mixed-unit state
    # every consumer downstream reconstructs nodes from.
    for (src, tgt), edge in graph.edges.items():
        elos = [
            n.computed_elo
            for n in (graph.nodes.get(src), graph.nodes.get(tgt))
            if n is not None and n.computed_elo is not None
        ]
        if elos:
            edge.elo = sum(elos) / len(elos)

    # ``user_elo`` takes the athlete's GLOBAL V2 rating rather than a re-average of the
    # projected nodes, for the reason the App gives at the same line: that IS the engine's
    # answer to "how good is this athlete", weighted by evidence, and a plain mean of nodes
    # (55% of which are seeds) is not. It also makes ``athletes.elo`` and the published
    # Grappling ELO the same number instead of two answers to one question.
    graph.user_elo = global_rating
    return ProjectionResult(evidenced, seeded)


# ── DB-reading orchestration — the only impure part of this module ──────────────────────


@dataclass(frozen=True)
class CorpusNodeRatings:
    """One corpus-wide node replay, reusable across every athlete in a batch.

    Built once and passed down rather than memoised: the corpus moves under this process
    (a match is approved, a sequence is corrected, a replay batch follows), and a cache with
    no invalidation key is how a replay quietly rates yesterday's corpus.
    """

    node_ratings: dict[NodeKey, NodeRating]
    global_final: dict[str, RatingState]
    #: period -> athlete -> state AFTER that period, for the per-match ELO series.
    global_after: dict[int, dict[str, RatingState]]
    coverage: dict[str, int]
    #: ``replay.bouts_hash`` of the corpus this was replayed over. Compared against the
    #: PINNED run's ``source_hash`` at build time (see ``build_corpus_node_ratings``): equal
    #: means ``athletes.elo`` and the published Grappling ELO are the same number, which is
    #: the only state ADR-02 tolerates.
    input_hash: str = ""

    def rating_for(self, athlete_id: str) -> float | None:
        state = self.global_final.get(athlete_id)
        return state.rating if state is not None else None

    def series_for(self, athlete_id: str, years: Sequence[int | None]) -> list[float]:
        """The athlete's global rating after each of ``years``' periods, in order.

        ``athlete.elo_series`` has to live on the same scale as ``athlete.elo`` or the row
        carries two units (ADR-02). A year with no state yet — or a bout before the athlete's
        first eligible period — falls back to the final rating rather than to a V1 number.
        """
        final = self.rating_for(athlete_id)
        if final is None:
            return []
        out: list[float] = []
        periods = sorted(self.global_after)
        for year in years:
            usable = [p for p in periods if year is not None and p <= year]
            state = self.global_after[usable[-1]].get(athlete_id) if usable else None
            out.append(state.rating if state is not None else final)
        return out


def build_corpus_node_ratings(
    session: Any, config: EngineConfig | None = None
) -> CorpusNodeRatings:
    """Replay the whole athlete corpus once: global track + node track.

    Eligibility is ``replay.build_bouts``' — final, in-discipline, known year, known winner,
    no self-match — and ``test_node_evidence_eligibility_matches_build_bouts`` is the lockstep
    gate that keeps this loop and that one saying the same thing. The duplication is
    deliberate: ``build_bouts`` throws away the link back to the ``Match`` row, and this track
    needs the sequence and the event tag that row carries.
    """
    from sqlalchemy import select

    from analysis.markov_weights import block_for_family, load_markov_weights
    from analysis.rating_v2.replay import (
        _discipline_of,
        _score_a,
        bouts_hash,
        build_seeds,
        load_discipline_map,
    )
    from analysis.ruleset_scoring import family_of
    from db.models import Athlete, Match

    config = config or EngineConfig()
    events_map, null_discipline = load_discipline_map()
    weights_doc = load_markov_weights()

    rows = session.execute(
        select(
            Match.athlete_a_id, Match.athlete_b_id, Match.winner_id, Match.event,
            Match.year, Match.win_type, Match.status, Match.sequence,
        )
    ).all()
    rank_elo_by_athlete = {
        aid: float(elo)
        for aid, elo in session.execute(
            select(Athlete.id, Athlete.rank_elo).where(Athlete.rank_elo.is_not(None))
        ).all()
    }

    coverage = {"total": len(rows), "eligible": 0, "with_evidence": 0}
    evidence: list[NodeEvidenceBout] = []
    for m in rows:
        if m.status != "final" or m.athlete_a_id == m.athlete_b_id:
            continue
        if _discipline_of(m.event, events_map, null_discipline) not in config.disciplines:
            continue
        if m.year is None:
            continue
        score_a = _score_a(m.winner_id, m.athlete_a_id, m.win_type)
        if score_a is None:
            continue
        coverage["eligible"] += 1
        block = block_for_family(family_of(m.event), weights_doc)
        obs_a = observations_for_side(m.sequence, m.athlete_a_id, block)
        obs_b = observations_for_side(m.sequence, m.athlete_b_id, block)
        if obs_a or obs_b:
            coverage["with_evidence"] += 1
        evidence.append(
            NodeEvidenceBout(
                bout=Bout(
                    period=m.year,
                    athlete_a=m.athlete_a_id,
                    athlete_b=m.athlete_b_id,
                    score_a=score_a,
                ),
                observations_a=obs_a,
                observations_b=obs_b,
            )
        )

    bouts = [item.bout for item in evidence]
    seeds = build_seeds(bouts, config, rank_elo_by_athlete)
    final, snapshots = run_periods_with_snapshots(seeds, bouts, config)

    # PRE-period snapshots shifted by one period give the state AFTER each period; the last
    # period's "after" is the final state.
    ordered = sorted(snapshots)
    global_after: dict[int, dict[str, RatingState]] = {}
    for i, period in enumerate(ordered):
        nxt = ordered[i + 1] if i + 1 < len(ordered) else None
        global_after[period] = dict(snapshots[nxt]) if nxt is not None else dict(final)

    node_ratings = run_node_ratings(snapshots, evidence, final)
    coverage["nodes"] = len(node_ratings)
    input_hash = bouts_hash(bouts)
    _warn_if_corpus_drifted(session, input_hash)
    return CorpusNodeRatings(
        node_ratings=node_ratings,
        global_final=dict(final),
        global_after=global_after,
        coverage=coverage,
        input_hash=input_hash,
    )


def _warn_if_corpus_drifted(session: Any, input_hash: str) -> None:
    """Log when this replay's corpus is not the one the public rating is pinned to.

    ADR-02 makes ``run_id`` a required read key, and ADR-16 makes ``athletes.elo`` the V2
    global rating — so if this replay reads a corpus the pinned run never saw, the graph and
    the published board start answering the same question differently. It is a WARNING and
    not a failure because the divergence is a normal intermediate state (ingest, then replay,
    then re-pin, then regenerate the site); what must never happen is nobody noticing.
    Verified 2026-08-26: recomputed states matched the pinned run for all 675 athletes to
    0.000000, so equality here is achievable, not aspirational.
    """
    import logging

    from analysis.rating_v2.config import SITE_RATING_RUN_ID
    from db.models import RatingEngineRun

    if SITE_RATING_RUN_ID is None:
        return
    from sqlalchemy import select

    pinned = session.execute(
        select(RatingEngineRun.source_hash).where(RatingEngineRun.id == SITE_RATING_RUN_ID)
    ).scalar_one_or_none()
    if pinned is not None and pinned != input_hash:
        logging.getLogger(__name__).warning(
            "rating_v2 node replay reads corpus %s but SITE_RATING_RUN_ID is pinned to %s — "
            "athletes.elo and the published Grappling ELO will disagree until the run is "
            "re-pinned and the site regenerated",
            input_hash[:8],
            pinned[:8],
        )
