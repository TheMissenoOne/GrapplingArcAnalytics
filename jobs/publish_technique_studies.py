"""Publish per-technique corpus digests ("technique studies") — the PUBLIC read side of a
Pro user's "study a problem" flow (``docs`` plan "ESTUDO SOB MEDIDA A PARTIR DE UM PROBLEMA").

One row per ``node_key`` (occ >= :data:`MIN_OCC` in the aggregate corpus network): frequency /
success rate / centrality / reward-risk, next-move ranking (the Markov baseline,
``analysis.next_moves``), top counter-moves (``analysis.counter_moves``), short own-actor
action chains, and up to :data:`TOP_REFS` video references ranked by the acting athlete's
ELO. Every input is a ``final`` ``matches`` row plus ``athletes`` — the PUBLIC competition
corpus, never a user session or a user graph (see root CLAUDE.md "Public vs Private Data").

No video is probed for its duration — :func:`scripts.dictionary_seed.classify_ts_origin` is
always called with ``video_duration=None``, so a match with no ``video_start_seconds`` always
classifies ``'unknown'`` (never guessed as absolute-from-zero) and its refs fall back to
``precision: 'bout'``, per AA-010.

Run manually until the App-side reader lands:
``uv run --extra postgres python -m jobs.publish_technique_studies --dry-run``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from analysis.counter_moves import counter_moves
from analysis.names import _normalize_name, canonicalize
from analysis.network_metrics import build_transition_network, node_centralities
from analysis.next_moves import MarkovNextMoves, build_vocab, corpus_points, library_actions
from analysis.technique_match import clean_label
from db.base import db_session
from db.models import Athlete, Match, TechniqueStudy
from export.match_breakdown import _final_matches, match_slug
from scripts.dictionary_seed import absolute_ts, classify_ts_origin

logger = logging.getLogger(__name__)

MIN_OCC = 3
TOP_NEXT_MOVES = 5
TOP_REFS = 8
CHAIN_LEN = 3
TOP_CHAINS = 8


def node_key_of(label: str) -> str:
    """The corpus identity for a technique label — char-for-char the same derivation as
    every other public-corpus artefact (``analysis.names``)."""
    return canonicalize(_normalize_name(label))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


# ── video reference locating (no network probing — DB evidence only) ────────────────────────

# ponytail: duplicated from ``export.site_data._video_ref`` rather than imported — that module
# pulls in the whole site-export dependency chain (narrative/jinja templates/…) for one 4-line
# regex. Upgrade path: factor both into a tiny ``analysis.video_ref`` if a third caller appears.
_YT_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|shorts/|v/))([\w-]{11})")
_YT_T_RE = re.compile(r"[?&#]t=(\d+)")


def _video_ref(url: str | None) -> tuple[str, int] | None:
    """Stored video URL -> (youtube id, start seconds from its own ``t=``), or None."""
    if not url:
        return None
    m = _YT_RE.search(url)
    if not m:
        return None
    t = _YT_T_RE.search(url)
    return m.group(1), int(t.group(1)) if t else 0


# ── the aggregate network: frequency / success / centrality / reward-risk ───────────────────


def _network_node_stats(g: Any) -> dict[str, dict[str, Any]]:
    """``node_key -> {label, occ, success_rate, pagerank, reward_risk}``, occ >= MIN_OCC only.

    ``g``'s nodes are keyed by the raw ``clean_label`` string; several of those can collapse
    onto the same ``node_key`` (the synonym-merge in ``analysis.names.SYNONYMS``) — rare, but
    when it happens every count is summed across the group and the label with the most
    occurrences is kept as the display label / next-moves query key.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for label in g.nodes:
        groups[node_key_of(label)].append(label)
    centralities = node_centralities(g)
    out: dict[str, dict[str, Any]] = {}
    for nk, labels in groups.items():
        occ = sum(g.nodes[lb].get("occ", 0) for lb in labels)
        if occ < MIN_OCC:
            continue
        ok_count = sum(g.nodes[lb].get("ok_count", 0) for lb in labels)
        reward = sum(g.nodes[lb].get("reward", 0) for lb in labels)
        risk = sum(g.nodes[lb].get("risk", 0) for lb in labels)
        denom = sum(g.nodes[lb].get("denom", 0) for lb in labels)
        primary = max(labels, key=lambda lb: g.nodes[lb].get("occ", 0))
        out[nk] = {
            "label": primary,
            "occ": occ,
            "success_rate": round(ok_count / occ, 4) if occ else 0.0,
            "pagerank": centralities.get(primary, {}).get("pagerank", 0.0),
            "reward_risk": round((reward - risk) / denom, 4) if denom else 0.0,
        }
    return out


# ── own-actor chains + per-node bout membership (one pass over the corpus) ──────────────────


@dataclass
class _OwnActorAgg:
    bout_ids: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    chain_counts: dict[str, Counter[tuple[str, ...]]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    chain_bouts: dict[str, dict[tuple[str, ...], set[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set))
    )


def _own_actor_pass(matches: Iterable[Match]) -> _OwnActorAgg:
    """One pass: which bouts each node_key appears in, and its short own-actor chains.

    ``chains`` = fixed-length (``CHAIN_LEN``) windows of an ACTOR'S OWN ordered event labels
    starting at an occurrence of the node — the "what she typically does from here, in her
    own flow" signal. Windows shorter than ``CHAIN_LEN`` (near a bout's end) are dropped
    rather than padded. ponytail: a simpler slice than ``analysis.corpus_paths`` (which
    dedupes overlapping windows into full paths) — upgrade there if a caller needs full
    path-bundling instead of raw fixed windows.
    """
    agg = _OwnActorAgg()
    for m in matches:
        by_actor: dict[str, list[str]] = defaultdict(list)
        for ev in m.sequence or []:
            if not isinstance(ev, dict):
                continue
            label = clean_label(str(ev.get("label", "")), str(ev.get("type", "")))
            actor = ev.get("actor_id")
            if not label or actor is None:
                continue
            by_actor[actor].append(label)
        for labels in by_actor.values():
            seen_here: set[str] = set()
            for i, label in enumerate(labels):
                nk = node_key_of(label)
                seen_here.add(nk)
                window = tuple(labels[i : i + CHAIN_LEN])
                if len(window) == CHAIN_LEN:
                    agg.chain_counts[nk][window] += 1
                    agg.chain_bouts[nk][window].add(m.id)
            for nk in seen_here:
                agg.bout_ids[nk].add(m.id)
    return agg


def _chains_for(nk: str, agg: _OwnActorAgg) -> list[dict[str, Any]]:
    counts = agg.chain_counts.get(nk, Counter())
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_CHAINS]
    return [
        {
            "actions": list(actions),
            "count": count,
            "bout_ids": sorted(agg.chain_bouts[nk][actions]),
        }
        for actions, count in ranked
    ]


# ── next-move ranking (fit once on the whole corpus, queried per node) ──────────────────────


def _fit_next_moves_model(matches: Sequence[Match]) -> MarkovNextMoves:
    bouts = [
        {"id": m.id, "a": m.athlete_a_id, "b": m.athlete_b_id, "sequence": m.sequence or []}
        for m in matches
    ]
    points, _stats = corpus_points(bouts)
    vocab = build_vocab(points, library_actions())
    return MarkovNextMoves(vocab).fit(points)


def _next_moves_for(label: str, model: MarkovNextMoves) -> list[dict[str, Any]]:
    return [
        {"label": lb, "key": node_key_of(lb), "p": round(p, 5), "count": model.raw_count(label, lb)}
        for lb, p in model.rank_next_moves(label, (), TOP_NEXT_MOVES)
    ]


def _counters_for(
    label: str, counters_by_label: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    # Contract requires exactly {key,label,count} (App `sanitizeCounter`) — ptv/success/leads_to
    # ride along as tolerated extras, not required fields.
    return [
        {
            "key": node_key_of(c["counter"]),
            "label": c["counter"],
            "count": c["count"],
            "ptv": c["ptv"],
            "success": c["success"],
            "leads_to": c.get("leads_to"),
        }
        for c in counters_by_label.get(label, [])
    ]


# ── video references (public corpus only, top-8 by actor ELO) ───────────────────────────────


def _refs_for(
    wanted: set[str], matches_with_video: Sequence[Match], athletes: dict[str, Athlete]
) -> dict[str, list[dict[str, Any]]]:
    """``node_key -> up to TOP_REFS refs``, one per (match, actor), ranked by (actor ELO desc,
    year desc, match_id) — the first occurrence of the label per (match, actor) wins, same
    "first sighting" convention as ``export.site_data._node_video_refs``."""
    candidates: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for m in matches_with_video:
        ref = _video_ref(m.video_url)
        if ref is None:
            continue
        vid, url_t = ref
        ts_list = [
            float(e["ts"])
            for e in (m.sequence or [])
            if isinstance(e, dict) and e.get("ts") is not None
        ]
        klass = classify_ts_origin(m.video_start_seconds, ts_list, None)
        a = athletes.get(m.athlete_a_id)
        b = athletes.get(m.athlete_b_id)
        for ev in m.sequence or []:
            if not isinstance(ev, dict):
                continue
            label = clean_label(str(ev.get("label", "")), str(ev.get("type", "")))
            actor_id = ev.get("actor_id")
            if not label or not actor_id:
                continue
            nk = node_key_of(label)
            if nk not in wanted:
                continue
            key = (m.id, actor_id)
            if key in candidates[nk]:
                continue
            t_event = (
                absolute_ts(m.video_start_seconds, klass, ev.get("ts"))
                if klass != "unknown"
                else None
            )
            if t_event is not None:
                t_secs, precision = t_event, "event"
            else:
                t_secs = m.video_start_seconds if m.video_start_seconds is not None else url_t
                precision = "bout"
            actor = athletes.get(actor_id)
            candidates[nk][key] = {
                "match_id": m.id,
                "slug": match_slug(a, b, m.year) if a is not None and b is not None else None,
                "event": m.event,
                "year": m.year,
                "athletes": {"a": a.name if a else None, "b": b.name if b else None},
                "vid": vid,
                "t_secs": int(t_secs),
                "precision": precision,
                "actor_name": actor.name if actor else None,
                "successful": ev.get("successful"),
                "ts_origin": m.ts_origin,
                "_actor_elo": actor.elo if actor else 0.0,
            }

    out: dict[str, list[dict[str, Any]]] = {}
    for nk, by_key in candidates.items():
        ranked = sorted(
            by_key.values(),
            key=lambda r: (-r["_actor_elo"], -(r["year"] or 0), r["match_id"]),
        )[:TOP_REFS]
        out[nk] = [{k: v for k, v in r.items() if k != "_actor_elo"} for r in ranked]
    return out


# ── orchestration ─────────────────────────────────────────────────────────────────────────


def build_all(session: Session, now: datetime) -> dict[str, dict[str, Any]]:
    """Every published node's payload, keyed by node_key. Pure read — no write."""
    matches = _final_matches(session)
    g = build_transition_network(session)
    node_stats = _network_node_stats(g)
    wanted = set(node_stats)
    if not wanted:
        return {}

    agg = _own_actor_pass(matches)
    model = _fit_next_moves_model(matches)
    counters_by_label = counter_moves(g)

    matches_with_video = [m for m in matches if m.video_url]
    athlete_ids = {
        aid for m in matches_with_video for aid in (m.athlete_a_id, m.athlete_b_id) if aid
    }
    athletes = (
        {
            a.id: a
            for a in session.execute(
                select(Athlete).where(Athlete.id.in_(athlete_ids))
            ).scalars()
        }
        if athlete_ids
        else {}
    )
    refs_by_node = _refs_for(wanted, matches_with_video, athletes)

    out: dict[str, dict[str, Any]] = {}
    for nk in sorted(wanted):
        stats = node_stats[nk]
        label = stats["label"]
        out[nk] = {
            # App contract (`services/techniqueStudies.ts:sanitizeTechniqueStudy`) requires the
            # literal camelCase key `schemaVersion` — everything else in this payload is
            # snake_case to match the corpus/DB convention, this one field is not.
            "schemaVersion": 1,
            "generated_at": _iso(now),
            "node_key": nk,
            "label": label,
            "stats": {
                "frequency": int(stats["occ"]),
                "success_rate": stats["success_rate"],
                "centrality": stats["pagerank"],
                "reward_risk": stats["reward_risk"],
                "bouts": len(agg.bout_ids.get(nk, ())),
            },
            "next_moves": _next_moves_for(label, model),
            "counters": _counters_for(label, counters_by_label),
            "chains": _chains_for(nk, agg),
            "refs": refs_by_node.get(nk, []),
        }
    return out


def _upsert(
    session: Session, nk: str, payload: dict[str, Any], now: datetime, *, dry_run: bool
) -> None:
    if dry_run:
        return
    existing = session.get(TechniqueStudy, nk)
    if existing is None:
        session.add(
            TechniqueStudy(node_key=nk, schema_version=1, payload=payload, generated_at=now)
        )
    else:
        existing.schema_version = 1
        existing.payload = payload
        existing.generated_at = now


def publish(
    session: Session,
    now: datetime,
    *,
    node_key: str | None = None,
    dry_run: bool = False,
    payloads: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Upsert every published node's study; a full run also deletes vanished keys.

    ``node_key`` restricts the run to one node (no deletion of anything else — a filtered run
    is never allowed to prune the rest of the table). An empty ``payloads`` result on a FULL
    run is treated as a failure, not "delete everything": a corpus-read bug producing zero
    nodes must never be able to wipe the table.
    """
    all_payloads = payloads if payloads is not None else build_all(session, now)

    if node_key is not None:
        if node_key not in all_payloads:
            logger.warning("unknown or below-threshold node_key: %s", node_key)
            return 1
        _upsert(session, node_key, all_payloads[node_key], now, dry_run=dry_run)
        return 0

    if not all_payloads:
        logger.warning("technique study build produced zero nodes — refusing to touch the table")
        return 1

    for nk, payload in all_payloads.items():
        _upsert(session, nk, payload, now, dry_run=dry_run)
    if not dry_run:
        session.execute(delete(TechniqueStudy).where(TechniqueStudy.node_key.notin_(all_payloads.keys())))
    return 0


def _print_dry_run_report(payloads: dict[str, dict[str, Any]]) -> None:
    print(f"technique_studies dry-run: {len(payloads)} node(s)")
    for nk in list(payloads)[:3]:
        text = json.dumps(payloads[nk], default=str)
        print(f"  {nk}: {text[:400]}{'...' if len(text) > 400 else ''}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--node-key")
    args = parser.parse_args(argv)
    with db_session() as session:
        now = datetime.now(UTC)
        payloads = build_all(session, now)
        if args.dry_run:
            _print_dry_run_report(payloads)
        return publish(
            session, now, node_key=args.node_key, dry_run=args.dry_run, payloads=payloads
        )


if __name__ == "__main__":
    raise SystemExit(main())
