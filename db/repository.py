"""DB repository — upsert helpers for graphs, athletes, archetypes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from analysis.names import _normalize_name
from db.models import (
    Archetype,
    Athlete,
    AthleteRatingStateV2,
    Graph,
    GraphEdge,
    GraphEdgeBout,
    GraphNode,
    Match,
    TechniqueNode,
    UserSession,
    UserSyncMeta,
)

if TYPE_CHECKING:  # import cycle: analysis.rating_v2 reads db.models
    from analysis.rating_v2.node_rating import CorpusNodeRatings


@dataclass
class DerivedNode:
    """A graph node reconstructed from edges + the shared technique library.

    Per-user node stats are no longer persisted (graph_nodes is dropped), so
    ``computed_elo`` is derived as the strongest incident edge ELO. Exposes the
    attributes ``analysis.archetype.graph_feature_vector`` reads."""

    node_key: str
    node_type: str
    computed_elo: float | None


def _register_techniques(techs: dict[str, dict[str, str]], session: Session) -> None:
    """Batch insert-if-absent into the shared technique library (one statement).

    Empty node_keys are skipped (a punctuation/whitespace-only label normalizes to
    '' and must not become a junk library row / FK target). Never clobbers an existing
    row (do-nothing on conflict), so a curated entry keeps its provenance.

    Callers set ``source`` themselves. It used to be hard-coded to ``'user'`` here, which
    is why 215 rows of ordinary competition vocabulary — "Jab", "Riding Time", "Stalling
    Warning" — carried user provenance in production: the ATHLETE ingestion path wrote
    through this function too. ``source`` means "where this vocabulary came from", and
    everything the athlete corpus produces came from published footage.
    """
    rows = [t for key, t in techs.items() if key]
    if not rows:
        return
    session.execute(
        pg_insert(TechniqueNode).values(rows).on_conflict_do_nothing(index_elements=["node_key"])
    )


def _register_graph_nodes(
    graph_id: str, nodes: dict[str, dict[str, str]], session: Session
) -> None:
    """Write a graph's OWN node identity (alembic 0037), before its edges.

    ``graph_edges`` endpoints carry composite foreign keys into ``graph_nodes``, so an
    edge cannot be written until its endpoints exist here. Nothing in this module wrote
    this table when 0037 landed, which left every server-side graph writer unable to
    persist an edge at all.

    ``canonical_node_key`` points at the curated library when the key is known there. The
    direction is the point: a graph node may name public vocabulary; public vocabulary
    never learns anything about a private node.
    """
    rows = [
        {
            "graph_id": graph_id,
            "node_key": key,
            "label": node.get("label") or key,
            "type": node.get("type") or "technique",
            "node_type": node.get("node_type") or "",
            "canonical_node_key": key,
        }
        for key, node in nodes.items()
        if key
    ]
    if not rows:
        return
    stmt = pg_insert(GraphNode).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["graph_id", "node_key"],
            set_={
                "label": stmt.excluded.label,
                "type": stmt.excluded.type,
                "node_type": stmt.excluded.node_type,
                "canonical_node_key": stmt.excluded.canonical_node_key,
            },
        )
    )


def incident_edge_elos(edges: Iterable[GraphEdge]) -> dict[str, list[float]]:
    """Map each node_key to the ELOs of its incident edges (graph_nodes is gone, so
    the node set + per-node stats are reconstructed from edges). Shared by clustering
    and the athlete export so the derivation has a single definition."""
    incident: dict[str, list[float]] = {}
    for e in edges:
        incident.setdefault(e.source_key, []).append(e.elo)
        incident.setdefault(e.target_key, []).append(e.elo)
    return incident


def upsert_graph_from_athlete_graph(
    athlete_graph: Any, athlete_id: str, session: Session
) -> str:
    """Persist an AthleteGraph (from analysis/athlete_graph.py) into the DB."""
    user_elo = getattr(athlete_graph, "user_elo", None)
    stmt = (
        pg_insert(Graph)
        .values(
            owner_kind="athlete",
            owner_id=athlete_id,
            user_elo=user_elo,
            synced_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["owner_kind", "owner_id"],
            set_={"user_elo": user_elo, "synced_at": datetime.now(UTC)},
        )
        .returning(Graph.id)
    )
    graph_id: str = session.execute(stmt).scalar_one()

    # Register techniques (nodes + edge endpoints) in one batch before edges (FK).
    techs: dict[str, dict[str, str]] = {}
    for node_key, node in athlete_graph.nodes.items():
        if node_key:
            techs.setdefault(
                node_key,
                {"node_key": node_key, "label": node.label, "type": "technique",
                 "node_type": node.type, "source": "library"},
            )
    for (src, tgt), _edge in athlete_graph.edges.items():
        for key in (src, tgt):
            if key:
                techs.setdefault(
                    key,
                    {"node_key": key, "label": key, "type": "technique",
                     "node_type": "", "source": "library"},
                )
    _register_techniques(techs, session)
    # The graph's own nodes, before its edges — the endpoints are a composite FK target.
    _register_graph_nodes(graph_id, techs, session)

    # One bulk upsert for ALL edges — a per-edge session.execute is a remote round-trip
    # each (~200 edges × ~130ms = the whole replay cost). edge_key is unique within a graph
    # (keyed by (src,tgt)), so no intra-statement ON CONFLICT collision.
    # Prefer the grown edge ELO; fall back to the raw count for count-only callers.
    edge_rows = [
        {"graph_id": graph_id, "edge_key": f"{src}→{tgt}",
         "source_key": src, "target_key": tgt,
         "elo": edge.elo if edge.elo is not None else float(edge.count)}
        for (src, tgt), edge in athlete_graph.edges.items()
        if src and tgt
    ]
    if edge_rows:
        edge_stmt = pg_insert(GraphEdge).values(edge_rows)
        edge_stmt = edge_stmt.on_conflict_do_update(
            index_elements=["graph_id", "edge_key"],
            set_={"elo": edge_stmt.excluded.elo},
        )
        session.execute(edge_stmt)

    # Prune stale edges: replay derives the FULL current graph, so any edge_key from a
    # prior persist that's no longer in this athlete_graph (technique dropped, renamed,
    # re-derivation shrank the game) must go — upsert above only adds/updates, never
    # deletes. Scoped to THIS graph_id and only edge_keys absent from the fresh set.
    keep_keys = [r["edge_key"] for r in edge_rows]
    stale_stmt = delete(GraphEdge).where(GraphEdge.graph_id == graph_id)
    if keep_keys:
        stale_stmt = stale_stmt.where(GraphEdge.edge_key.notin_(keep_keys))
    session.execute(stale_stmt)

    # Bout provenance (alembic 0046) — one row per (edge, bout), written AFTER the prune so it
    # never references an edge the prune just removed.
    #
    # Replace rather than merge, for the same reason the prune exists: a replay derives the FULL
    # current provenance, so a bout that no longer contributes to an edge (technique renamed,
    # sequence corrected) has to lose the row that says it does. Merging would accumulate claims
    # that were true once and are not now, and a bootstrap would resample them.
    #
    # Skipped entirely when no edge carries provenance, so a caller that builds a graph without
    # walking matches (`build_athlete_graph`) leaves what is stored alone instead of erasing it.
    bout_rows = [
        {"graph_id": graph_id, "source_key": src, "target_key": tgt, "match_id": bout_id}
        for (src, tgt), edge in athlete_graph.edges.items()
        if src and tgt
        for bout_id in sorted(edge.bout_ids)
    ]
    if bout_rows:
        session.execute(delete(GraphEdgeBout).where(GraphEdgeBout.graph_id == graph_id))
        session.execute(pg_insert(GraphEdgeBout).values(bout_rows).on_conflict_do_nothing())

    return graph_id


def _techniques_from_sequence(
    sequence: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Every technique in a match sequence → a shared-library row (both actors).

    The athlete *graph* only holds the athlete's own moves, but the technique
    *library* should record every technique seen in any entered match. Keyed by
    the normalized label (skips empty keys); ``source='user'``."""
    techs: dict[str, dict[str, str]] = {}
    for entry in sequence:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip()
        key = _normalize_name(label)
        if not key:
            continue
        techs.setdefault(
            key,
            {"node_key": key, "label": label, "type": "technique",
             "node_type": str(entry.get("type", "")), "source": "user"},
        )
    return techs


def register_match(
    athlete_a_id: str,
    athlete_b_id: str,
    *,
    winner_id: str | None,
    win_type: str | None,
    submission: str | None,
    event: str | None,
    year: int | None,
    weight_class: str | None,
    stage: str | None,
    sequence: list[dict[str, Any]],
    created_by: str | None,
    session: Session,
    status: str = "final",
    video_url: str | None = None,
    timeline: list[dict[str, Any]] | None = None,
) -> str:
    """Store one GLOBAL match between two athletes. ``sequence`` events carry
    ``actor_id`` (one of the two athlete ids). ``winner_id`` is None for a draw.
    ``timeline`` = the full event list (actor 'a'/'b'/None) for the breakdown UI; optional."""
    match = Match(
        athlete_a_id=athlete_a_id,
        athlete_b_id=athlete_b_id,
        winner_id=winner_id,
        win_type=win_type,
        submission=submission,
        event=event,
        year=year,
        weight_class=weight_class,
        stage=stage,
        sequence=sequence,
        created_by=created_by,
        status=status,
        video_url=video_url,
        timeline=timeline,
    )
    session.add(match)
    session.flush()
    # Every technique in a FINAL match enters the shared library (app + analytics).
    # Draft (scraped, unreviewed) matches hold coarse labels, so they don't register
    # until approved — keeps the library clean.
    if status == "final":
        _register_techniques(_techniques_from_sequence(sequence), session)
    return match.id


def register_matches_bulk(rows: list[dict[str, Any]], session: Session) -> None:
    """Insert many GLOBAL final matches in one round trip.

    Same field shape as ``register_match``'s kwargs (minus ``session``/``status``,
    always 'final') and same technique-registration behavior, but batched: the dump
    importer previously called ``register_match`` once per bout (delete + insert each,
    ~2 round trips/bout dominating reprocess cost). Caller still issues its own
    delete(s) beforehand; this only inserts + registers techniques, once."""
    if not rows:
        return
    from sqlalchemy import insert

    session.execute(insert(Match), [{**r, "status": "final"} for r in rows])
    techs: dict[str, dict[str, str]] = {}
    for r in rows:
        techs.update(_techniques_from_sequence(r.get("sequence") or []))
    _register_techniques(techs, session)


def get_match(match_id: str, session: Session) -> Match | None:
    return session.get(Match, match_id)


def update_match(
    match_id: str,
    *,
    athlete_a_id: str,
    athlete_b_id: str,
    winner_id: str | None,
    win_type: str | None,
    submission: str | None,
    event: str | None,
    year: int | None,
    weight_class: str | None,
    stage: str | None,
    sequence: list[dict[str, Any]],
    session: Session,
    video_url: str | None = None,
) -> None:
    """Edit a stored global match in place (a/b are symmetric, so the caller may pass
    them in the page-athlete's perspective). The caller re-runs ``replay_participants``
    to rebuild both graphs. Techniques re-register only when the match is final."""
    match = session.get(Match, match_id)
    if match is None:
        raise ValueError(f"Match {match_id} not found")
    match.athlete_a_id = athlete_a_id
    match.athlete_b_id = athlete_b_id
    match.winner_id = winner_id
    match.win_type = win_type
    match.submission = submission
    match.event = event
    match.year = year
    match.weight_class = weight_class
    match.stage = stage
    match.video_url = video_url
    match.sequence = sequence
    session.flush()
    if match.status == "final":
        _register_techniques(_techniques_from_sequence(sequence), session)


def upsert_athlete(
    name: str,
    nickname: str | None = None,
    team: str | None = None,
    weight_class: str | None = None,
    belt: str | None = None,
    source: str = "manual",
    session: Session = None,  # type: ignore[assignment]
) -> str:
    athlete = Athlete(
        name=name,
        nickname=nickname,
        team=team,
        weight_class=weight_class,
        belt=belt,
        source=source,
    )
    session.add(athlete)
    session.flush()
    return athlete.id


def upsert_user_session(
    owner_id: str,
    session_id: str,
    data: dict[str, Any] | None,
    updated_at: datetime,
    session: Session,
    *,
    deleted_at: datetime | None = None,
) -> None:
    """Upsert one raw ``SessionState`` row by ``id`` (device-generated). Pass
    ``deleted_at`` to write a tombstone (delete propagation, alembic 0019); ``data`` may be
    None for a tombstone whose session was never pushed live. The ON CONFLICT arm relies on
    the DB ``trg_user_sessions_stale_write`` guard to drop a write whose ``updated_at`` is
    older than the server row — the app can't be trusted to order concurrent pushes, so
    last-write-wins is enforced on ``updated_at`` in Postgres."""
    stmt = (
        pg_insert(UserSession)
        .values(
            id=session_id, owner_id=owner_id, data=data,
            updated_at=updated_at, deleted_at=deleted_at,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"data": data, "updated_at": updated_at, "deleted_at": deleted_at},
        )
    )
    session.execute(stmt)


def get_user_sessions_since(
    owner_id: str, since: datetime | None, session: Session
) -> list[UserSession]:
    """All sessions for ``owner_id`` with ``updated_at`` on or after ``since`` (all, if None).

    Boundary is inclusive (``>=``) to match the app's own cursor query — a row sharing the
    exact cursor timestamp with an already-synced row must not be skipped; the merge's strict
    ``remote.updated_at > local`` comparison is what dedupes/no-ops it, not this query.

    Tombstones (``deleted_at`` set) are intentionally included — the caller needs them to
    propagate deletions to other devices. Do NOT add a ``deleted_at IS NULL`` filter."""
    stmt = select(UserSession).where(UserSession.owner_id == owner_id)
    if since is not None:
        stmt = stmt.where(UserSession.updated_at >= since)
    return list(session.execute(stmt).scalars().all())


def get_sync_meta(owner_id: str, session: Session) -> UserSyncMeta | None:
    return session.get(UserSyncMeta, owner_id)


def upsert_sync_meta(owner_id: str, session: Session, **fields: Any) -> UserSyncMeta:
    """Create or update the sync-progress row for ``owner_id``. ``fields`` may set any
    of ``big_sync_completed_at``/``last_sync_at``/``session_count``."""
    meta = session.get(UserSyncMeta, owner_id)
    if meta is None:
        meta = UserSyncMeta(owner_id=owner_id, **fields)
        session.add(meta)
    else:
        for key, value in fields.items():
            setattr(meta, key, value)
    session.flush()
    return meta


# ponytail: process-wide read cache for the read-only site export, where
# get_matches_for_athlete is called ~5×/fighter (qualifies, style_profile, disciplines,
# defense) — each a remote round-trip. prime once → serve from memory. None = live query
# (the default everywhere else; replay/import paths mutate and must NOT use it).
_MATCH_INDEX: dict[str, list[Match]] | None = None


def prime_match_cache(session: Session) -> None:
    """Load every match once and index by both participants (deterministic order), so the
    export's per-athlete lookups hit memory instead of one remote query each. Call
    clear_match_cache() when done."""
    global _MATCH_INDEX
    idx: dict[str, list[Match]] = {}
    for m in session.execute(
        select(Match).order_by(Match.year, Match.created_at, Match.id)
    ).scalars():
        idx.setdefault(m.athlete_a_id, []).append(m)
        idx.setdefault(m.athlete_b_id, []).append(m)
    _MATCH_INDEX = idx


def clear_match_cache() -> None:
    global _MATCH_INDEX
    _MATCH_INDEX = None


def get_matches_for_athlete(athlete_id: str, session: Session) -> list[Match]:
    """Every global match the athlete participates in (either side), in a deterministic
    order (year, created_at, id). Without the explicit ORDER BY the DB return order is
    arbitrary, so same-year matches would replay in a nondeterministic order."""
    if _MATCH_INDEX is not None:
        return list(_MATCH_INDEX.get(athlete_id, ()))
    return list(
        session.execute(
            select(Match)
            .where(
                or_(Match.athlete_a_id == athlete_id, Match.athlete_b_id == athlete_id)
            )
            .order_by(Match.year, Match.created_at, Match.id)
        ).scalars()
    )


def opponent_input_elo(match: Match, athlete_id: str, session: Session) -> float:
    """Input rating for the OTHER athlete when replaying ``athlete_id``'s side: the
    other athlete's ADCC rank_elo, else the black-belt floor (unranked → 800)."""
    from analysis.athlete_elo import base_elo_for_belt

    other_id = match.athlete_b_id if match.athlete_a_id == athlete_id else match.athlete_a_id
    other = session.get(Athlete, other_id)
    if other is not None and other.rank_elo is not None:
        return float(other.rank_elo)
    return base_elo_for_belt("black")


@dataclass
class _PerspectiveMatch:
    """A global ``Match`` viewed from one athlete's side — the duck-typed shape
    ``analysis.athlete_elo.replay_matches`` consumes (``.id``, ``.sequence`` with actor
    'you'/'opponent', ``.won``, ``.win_type``, ``.year``, ``.created_at``).

    ``id`` is part of that shape and not decoration: ``replay_matches`` records it as the bout
    each edge was observed in (alembic 0046), which is condition (a) of ADR-08. It was missing
    here at first, and the failure was silent — the replay reads it through a ``getattr`` default
    and simply recorded nothing, so ``graph_edge_bouts`` stayed empty across a corpus of 865
    matches with no error anywhere. ``tests/test_edge_bout_provenance.py`` now pins the field.
    """

    id: str
    sequence: list[dict[str, Any]]
    won: bool
    win_type: str | None
    year: int | None
    created_at: Any


def _perspective_view(match: Match, athlete_id: str) -> _PerspectiveMatch:
    """Remap a global match's actor_id sequence to 'you'/'opponent' for ``athlete_id``.

    The shared perspective conversion lives in ``analysis.perspective_sequence``
    (``perspective_events``); this shim keeps the historical dict shape
    ``{label, type, actor, successful?}`` the replay/ELO/style consumers expect.
    Both actors are preserved here — the execution graph and ELO filter
    ``actor == 'you'`` downstream.
    """
    from analysis.perspective_sequence import perspective_events

    seq: list[dict[str, Any]] = []
    for pe in perspective_events(match, athlete_id):
        item: dict[str, Any] = {
            "label": pe.label,
            "type": pe.event_type,
            "actor": "you" if pe.actor == "you" else "opponent",
        }
        if pe.successful is not None:
            item["successful"] = pe.successful
        seq.append(item)
    # No winner (draw OR un-inferred winner, e.g. unreviewed scraped match) → neutral
    # score for BOTH sides, not a loss for both. Force the view's win_type to DRAW so
    # score_from_match returns 0.5 instead of the loss fallback.
    win_type = match.win_type
    if match.winner_id is None and (win_type or "").upper() != "DRAW":
        win_type = "DRAW"
    return _PerspectiveMatch(
        # `str` because the column is a UUID and the provenance table keys on text; the edge and
        # its bout have to join, and a `UUID` object and its string form do not.
        id=str(match.id),
        sequence=seq,
        won=match.winner_id == athlete_id,
        win_type=win_type,
        year=match.year,
        created_at=match.created_at,
    )


def replay_and_persist_athlete(
    athlete: Athlete,
    session: Session,
    node_ratings: CorpusNodeRatings | None = None,
) -> list[float]:
    """Replay every FINAL match this athlete participates in, FROM THEIR SIDE; persist
    the grown graph + ``athlete.elo`` + ``athlete.elo_series``. Returns the snapshots.
    Draft matches are held out until approved.

    ``node_ratings`` (ADR-16) is one corpus-wide Glicko-2 node replay. V1 still runs — it is
    what derives the graph's nodes, edges, counts and bout provenance — and its per-node ELO
    is then OVERWRITTEN by the projection, exactly as the App overwrites ``computedElo`` at
    its two producers. Omitting the argument makes this function build the corpus replay
    itself, which is correct but pays for a full-corpus pass per athlete; every caller that
    loops over athletes should build it once with
    ``analysis.rating_v2.node_rating.build_corpus_node_ratings(session)`` and pass it down.
    """
    from analysis.athlete_elo import COMPETITIVE_K_MULT, replay_matches  # local: avoid import cycle
    from analysis.markov_weights import block_for_family, load_markov_weights
    from analysis.rating_v2.node_rating import build_corpus_node_ratings, project_onto_graph
    from analysis.ruleset_scoring import family_of

    target = rank_elo_for_athlete(athlete.name)
    is_competitive = target is not None or athlete.source == "leaderboard"
    if target is None:
        target = athlete.rank_elo if athlete.rank_elo is not None else 1000.0
    comp_mult = COMPETITIVE_K_MULT if is_competitive else 1.0
    # get_matches_for_athlete already orders by (year, created_at, id); re-sort only to
    # coalesce NULL years to the front and keep that deterministic id tiebreak.
    final = sorted(
        (m for m in get_matches_for_athlete(athlete.id, session) if m.status == "final"),
        key=lambda m: (m.year or 0, m.created_at, m.id),
    )
    views = [_perspective_view(m, athlete.id) for m in final]
    opp_elos = [opponent_input_elo(m, athlete.id, session) for m in final]
    # Per-match Markov action-weight block. Resolved HERE and not inside the replay because
    # it needs the match's own `event` tag plus the versioned ruleset map, and `athlete_elo`
    # is pure by contract. `None` everywhere while the artefact is absent, which is exactly
    # the pre-Markov uniform split — nothing moves until the file ships and a full replay is
    # run (see `analysis/markov_weights.py` rule 3).
    weights_doc = load_markov_weights()
    action_weights = [
        block_for_family(family_of(m.event), weights_doc) for m in final
    ]
    graph, snapshots = replay_matches(
        athlete.name, views, target, opp_elos, belt=athlete.belt or "black",
        competitive_mult=comp_mult, action_weights=action_weights,
    )

    # ── ADR-16 cutover: per-node Glicko-2 replaces V1's delta split ──────────────────────
    # Projected onto the SAME ``computed_elo`` field so node ELO, edge ELO, ``user_elo`` and
    # every consumer that reconstructs a node from an edge move in one step. An athlete with
    # no state in the V2 run (out-of-discipline per ADR-05, or every bout excluded by ADR-06)
    # is left on V1 — there is nothing to project, and a half-projected graph is the one
    # thing that must never reach the DB.
    corpus = node_ratings if node_ratings is not None else build_corpus_node_ratings(session)
    projection = project_onto_graph(
        graph, athlete.id, corpus.node_ratings, corpus.rating_for(athlete.id)
    )
    if projection.evidenced or projection.seeded:
        # The series has to move with ``athlete.elo`` or the row carries two scales (ADR-02).
        snapshots = corpus.series_for(athlete.id, [m.year for m in final])

    upsert_graph_from_athlete_graph(graph, athlete.id, session)
    if graph.user_elo is not None:
        athlete.elo = graph.user_elo
    athlete.elo_series = snapshots
    return snapshots


def replay_participants(match: Match, session: Session) -> None:
    """Rebuild BOTH athletes' graphs after a match changes — the double pass."""
    from analysis.rating_v2.node_rating import build_corpus_node_ratings

    # One corpus node replay for both sides. Built here, after the match change is visible to
    # this session, so the two athletes are rated against the same corpus as each other.
    corpus = build_corpus_node_ratings(session)
    for aid in (match.athlete_a_id, match.athlete_b_id):
        athlete = session.get(Athlete, aid)
        if athlete is not None:
            replay_and_persist_athlete(athlete, session, node_ratings=corpus)


def approve_match(match_id: str, session: Session) -> Match:
    """Promote a draft match to 'final' and register its (now-reviewed) techniques.
    The caller runs ``replay_participants`` to fold it into both graphs."""
    match = session.get(Match, match_id)
    if match is None:
        raise ValueError(f"Match {match_id} not found")
    match.status = "final"
    session.flush()
    _register_techniques(_techniques_from_sequence(match.sequence or []), session)
    return match


def delete_match(match_id: str, session: Session) -> None:
    match = session.get(Match, match_id)
    if match is not None:
        session.delete(match)
        session.flush()


def rated_athlete_graph_ids(session: Session, run_id: str | None) -> set[str]:
    """``graph_id`` of every athlete graph whose owner has a state in rating_v2 ``run_id``.

    The population filter ADR-16 needs. Only these graphs carry V2-scale ``computed_elo``
    (``replay_and_persist_athlete`` leaves an athlete the run does not cover on V1), so any
    BASELINE built without this filter mixes two units inside one ``node_key`` and every
    z-score in ``deviance.node_population_stats`` starts measuring which corpus an athlete is
    in rather than how strong the node is. Measured 2026-08-26: 451 athlete graphs with edges
    are covered by the pinned run and 86 are not (Chimaev, Khabib, St-Pierre, Prochazka… —
    MMA, correctly out of the grappling corpus by ADR-05), and the two sides share 104
    node_keys.

    ``None`` run_id → empty set, and every caller treats "no filter available" as "do not
    filter" rather than "exclude everything".
    """
    if run_id is None:
        return set()
    rated = select(AthleteRatingStateV2.athlete_id).where(AthleteRatingStateV2.run_id == run_id)
    return {
        gid
        for gid in session.execute(
            select(Graph.id).where(Graph.owner_kind == "athlete", Graph.owner_id.in_(rated))
        ).scalars()
    }


def graphs_for_clustering(
    session: Session, owner_kind: str | None = None
) -> list[tuple[str, list[DerivedNode]]]:
    """Return [(graph_id, [DerivedNode, ...])] for graphs (optionally one ``owner_kind``).

    Nodes are reconstructed from each graph's edges joined to the shared
    ``technique_nodes`` library (graph_nodes is dropped): the node set is the
    edge endpoints, ``node_type`` comes from the library, and ``computed_elo``
    is derived as the strongest incident edge ELO. Pass ``owner_kind='athlete'`` to
    restrict to pro-athlete graphs (the archetype population).

    Baselines computed over the athlete population must additionally be narrowed by
    ``rated_athlete_graph_ids`` — see that function for why."""
    node_types: dict[str, str] = {
        row[0]: row[1]
        for row in session.execute(
            select(TechniqueNode.node_key, TechniqueNode.node_type)
        ).all()
    }
    graph_q = select(Graph.id)
    if owner_kind is not None:
        graph_q = graph_q.where(Graph.owner_kind == owner_kind)
    graph_ids = list(session.execute(graph_q).scalars())
    result = []
    for graph_id in graph_ids:
        edges = session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars()
        incident = incident_edge_elos(edges)
        nodes = [
            DerivedNode(
                node_key=key,
                node_type=node_types.get(key, ""),
                computed_elo=max(elos) if elos else None,
            )
            for key, elos in incident.items()
        ]
        result.append((graph_id, nodes))
    return result


def save_archetypes(
    centroids: list[list[float]],
    names: list[str],
    keys: list[str],
    signature_types: list[list[str]],
    feature_version: str,
    session: Session,
) -> list[int]:
    """Persist computed (emergent) archetypes. Returns the new row ids in input order."""
    ids = []
    for name, key, sig, centroid in zip(names, keys, signature_types, centroids):
        a = Archetype(
            name=name,
            key=key,
            kind="emergent",
            signature_types=sig,
            centroid={"vector": centroid},
            feature_version=feature_version,
        )
        session.add(a)
        session.flush()
        ids.append(a.id)
    return ids


def assign_archetype_to_graph(graph_id: str, archetype_id: int, session: Session) -> None:
    graph = session.get(Graph, graph_id)
    if graph:
        graph.archetype_id = archetype_id


def archetype_refs(session: Session) -> list[Any]:
    """Load archetypes as match-ready ``ArchetypeRef``s (18-d feature centroid +
    optional 768-d embedding). Rows without a stored feature centroid are skipped
    (can't be compared structurally)."""
    import numpy as np

    from analysis.archetype import ArchetypeRef

    refs: list[Any] = []
    for a in session.execute(select(Archetype)).scalars():
        centroid = (a.centroid or {}).get("vector") if a.centroid else None
        if not centroid:
            continue
        refs.append(ArchetypeRef(
            id=a.id,
            name=a.name,
            signature_types=list(a.signature_types or []),
            centroid_vec=np.asarray(centroid, dtype=np.float64),
            embedding=(
                np.asarray(a.embedding, dtype=np.float64) if a.embedding is not None else None
            ),
        ))
    return refs


def assign_user_archetype_to_graph(
    graph_id: str, archetype_id: int, report: dict[str, Any], session: Session
) -> None:
    """Persist a user graph's matched archetype + its structural similar/differ report."""
    graph = session.get(Graph, graph_id)
    if graph:
        graph.archetype_id = archetype_id
        graph.archetype_report = report


def clear_archetypes(session: Session) -> int:
    """Null graph refs to EMERGENT archetypes and delete those rows before a recompute.

    Scoped to kind=='emergent' so author-defined TARGET archetypes (RF01) survive recompute.
    Returns rows deleted.
    """
    emergent_ids = [
        r[0] for r in session.execute(
            select(Archetype.id).where(Archetype.kind == "emergent")
        ).all()
    ]
    if not emergent_ids:
        return 0
    session.execute(
        update(Graph).values(archetype_id=None).where(Graph.archetype_id.in_(emergent_ids))
    )
    res = session.execute(delete(Archetype).where(Archetype.id.in_(emergent_ids)))
    session.flush()
    return getattr(res, "rowcount", 0) or 0


def publish_athlete(athlete_id: str, session: Session) -> None:
    athlete = session.get(Athlete, athlete_id)
    if athlete:
        athlete.is_published = True


def _load_leaderboard() -> list[dict[str, Any]]:
    """Load the ADCC ELO leaderboard, regenerating the JSON if it's missing."""
    import json

    from pipelines.etl import PROCESSED_DIR

    path = PROCESSED_DIR / "adcc_elo_table.json"
    if not path.exists():
        from export.adcc_elo_table import export_adcc_elo_table

        export_adcc_elo_table()
    with open(path) as f:
        data: list[dict[str, Any]] = json.load(f)
    return data


def rank_elo_for_athlete(name: str) -> float | None:
    """Look up an athlete's ADCC rank ELO by normalized name, or None."""
    target = _normalize_name(name)
    for rec in _load_leaderboard():
        if _normalize_name(str(rec.get("fighter", ""))) == target:
            return float(rec["elo"])
    return None


def seed_athletes_from_leaderboard(session: Session) -> int:
    """Create Athlete rows from the leaderboard with ``rank_elo`` set.

    Skips fighters whose (normalized) name already exists. Returns count created.
    """
    existing = {
        _normalize_name(a.name)
        for a in session.execute(select(Athlete)).scalars()
    }
    created = 0
    for rec in _load_leaderboard():
        name = str(rec.get("fighter", "")).strip()
        if not name or _normalize_name(name) in existing:
            continue
        session.add(
            Athlete(
                name=name,
                belt="black",
                weight_class=str(rec.get("weight_class") or "") or None,
                source="leaderboard",
                rank_elo=float(rec["elo"]),
            )
        )
        existing.add(_normalize_name(name))
        created += 1
    return created


class AthleteRemovalReason(StrEnum):
    """Why an athlete is being removed — and therefore what happens to their graph.

    The two are not variants of one operation. They differ in what they believe about the data,
    and that belief is what decides the graph's fate, so the caller has to state it.
    """

    #: The athlete was never real, or the data behind them is wrong — a duplicate, a phantom from
    #: a bad name mapping, an audit finding. The graph is derived from the same wrong data, so it
    #: goes with them.
    INVALID_DATA = "invalid_data"

    #: The person is real and so are the bouts; what must stop is the data identifying them
    #: (LGPD Art. 18). Nothing was wrong, so the graph is anonymised and KEPT.
    RIGHTS_REQUEST = "rights_request"


#: Everything on an athlete row that names a person. `id` is not here: it is a pseudonymous UUID
#: carrying no identity of its own, and keeping it is what lets the graph keep a valid owner.
_IDENTIFYING_COLUMNS = ("nickname", "team", "weight_class")

#: What the name becomes. A fixed token rather than a blank, so a row that went through this is
#: visibly distinct from one where the name was simply never filled in.
ANONYMIZED_NAME = "[anonymized]"


def remove_athlete(athlete: Athlete, session: Session, *, reason: AthleteRemovalReason) -> None:
    """Remove an athlete for ``reason``, and do the right thing with their graph.

    **The invariant this exists to protect:** every graph with ``owner_kind='athlete'`` has a row
    in ``athletes``, with no exceptions. An orphaned athlete graph is therefore always a defect,
    and nobody has to ask whether a particular one was intentional.

    That is why the rights-request path does not delete the row. Deleting it and marking the
    graph instead would create a second, legitimate kind of orphan, and the guard would weaken to
    "an orphan without a marker" — the same ambiguity that let seven of them accumulate in
    production unnoticed until 2026-08-19.

    A database cascade cannot do this job. ``graphs.owner_id`` is polymorphic, so Postgres cannot
    carry a foreign key on it at all; and a cascade fires on every delete regardless of why,
    which would destroy exactly the graph a rights request says to keep.
    """
    if reason is AthleteRemovalReason.RIGHTS_REQUEST:
        # In place: the row survives, the person does not. `elo`, `elo_series` and `archetype_id`
        # stay — they are derived from published bouts and identify nobody once the name is gone,
        # and they are what keeps the graph useful to aggregate work over the athlete corpus.
        athlete.name = ANONYMIZED_NAME
        for column in _IDENTIFYING_COLUMNS:
            setattr(athlete, column, None)
        # No individual page for someone who asked not to be identified.
        athlete.is_published = False
        athlete.anonymized_at = datetime.now(UTC)
        session.flush()
        return

    # INVALID_DATA: the graph is derived from the same data that was wrong.
    session.execute(
        delete(Graph).where(Graph.owner_kind == "athlete", Graph.owner_id == athlete.id)
    )
    session.execute(delete(Athlete).where(Athlete.id == athlete.id))
    session.flush()
