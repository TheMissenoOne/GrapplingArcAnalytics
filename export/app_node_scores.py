#!/usr/bin/env python
"""Build `data/processed/app_node_scores.json` — the App's per-technique corpus scores.

The App's `nodeCorpusScores.ts` + the "undertrained move with potential" suggestion
(`insightThunksLocal.ts`) read two fields off each `@grapplingarch:nodes_library` entry:
`rrb` and `eloPercentile`. No producer existed for either — this is it. This module writes
a standalone lookup (`{normalized_name: {rrb?, eloPercentile?}}`); wiring it INTO
`export/tech_library.py`'s `NodeLibraryItem` output so it actually reaches the App's
AsyncStorage payload is a separate step, not done here.

    uv run python -m export.app_node_scores            # write
    uv run python -m export.app_node_scores --check    # rebuild and diff, write nothing
    uv run python -m export.app_node_scores --stdout   # print, write nothing

## `rrb` — per-technique reward-risk balance

Each library entry's `type` (and, for `control`/`transition`/`guard`, its ONE canonical
label — `translations.en`, else `name`; see `canonical_label`) is mapped through
`analysis.lamas_chain.lamas_state` to one of the twelve Lamas action codes — the same
mapping the App ports in `markovActionWeights.ts`. No `successful` flag is passed: a
library entry is a general technique, not a landed/missed event, so `lamas_state` naturally
resolves to the ATTEMPT-side code (`landed` defaults False). That code's `sub_share` from
`analysis.lamas_chain.rrb()` over the full public
corpus (`matches` table, `n_boot=0` — cheap, deterministic, no bootstrap) is the value:
`p_sub_own / (p_sub_own + p_sub_opp)`, the absorbing-chain estimate of "does a bout passing
through this state end in the acting athlete's OWN submission". `global` block only — this
artifact serves the App, which has no ruleset (ADCC/IBJJF) to pick a family by.

A technique whose type/label maps to no Lamas state, or whose state's row does not clear
`rrb()`'s absorbing-bout / coverage gates (`row["gated"]`), gets no `rrb` field. Never a
fabricated value.

## `eloPercentile` — per-technique corpus ELO percentile

`analysis.deviance.node_population_stats` over the athlete-graph population, filtered
`owner_kind='athlete'` AND `db.repository.rated_athlete_graph_ids(session,
SITE_RATING_RUN_ID)` (ADR-16 — a population baseline that mixes V1 and V2 `computed_elo`
scales inside one `node_key` is a defect, not noise). Percentile = the fraction of corpus
`node_key`s whose mean `computed_elo` is STRICTLY below this technique's, times 100,
rounded to the nearest int. A technique with no matching `node_key` in the corpus gets no
`eloPercentile` field.

## Key matching

Both layers land on the App's `normalizeLabel(label)` == `analysis.names._normalize_name`
contract (char-for-char, root CLAUDE.md) — NOT `_deaccent` first (that is the separate
`lamas_chain._key` contract the Lamas mapping itself uses internally). Every name variant a
library entry carries (`name`, each `translations` value, each `variations` entry) is
normalized and emitted as its own key pointing at the SAME score object, mirroring exactly
how `nodeCorpusScores.warmCorpusScoresIndex` builds its own in-memory index from the same
fields — so a consumer keying off any variant finds the technique. A key two different
techniques both normalize to is a real collision (not expected in the shipped library);
the first technique wins and every collision is listed in `meta.key_collisions` rather than
silently overwritten.

Privacy class A, public competition data (rrb) + athlete-graph population (eloPercentile) —
never a user graph or session (root CLAUDE.md, "Public vs Private Data").

READ-ONLY against prod. Uses `db.base.db_session` (commits on clean exit, but this issues no
writes, so the commit is a no-op).
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.deviance import Stats, grappling_nodes, node_population_stats  # noqa: E402
from analysis.lamas_chain import chain_of, lamas_state, rrb  # noqa: E402
from analysis.names import _normalize_name  # noqa: E402

OUT = REPO / "data" / "processed" / "app_node_scores.json"
APP_NODES_PATH = REPO.parent / "GrapplingArcApp" / "src" / "data" / "grappling-arch.nodes.json"

#: Same node-count gate `analysis/ocean.py:elo_distribution` uses for its own athlete-corpus
#: baseline — a graph with fewer grappling nodes than this is too thin to trust for a
#: population mean.
MIN_GRAPH_NODES = 3


# ── rrb: technique -> Lamas action -> sub_share ─────────────────────────────────
def rrb_by_state(bouts: list[dict[str, Any]]) -> dict[str, float]:
    """Per-Lamas-state `sub_share`, gated rows only. `n_boot=0` — no bootstrap, deterministic."""
    chains = [chain_of(b) for b in bouts]
    result = rrb(chains, n_boot=0)
    return {
        row["state"]: row["sub_share"]
        for row in result["rows"]
        if row["gated"] and row["sub_share"] is not None
    }


def _name_variants(node: dict[str, Any]) -> list[str]:
    """Every raw label a library entry carries, in a fixed order (name first)."""
    texts: list[str] = []
    name = node.get("name")
    if isinstance(name, str) and name.strip():
        texts.append(name)
    for v in (node.get("translations") or {}).values():
        if isinstance(v, str) and v.strip():
            texts.append(v)
    for v in node.get("variations") or []:
        if isinstance(v, str) and v.strip():
            texts.append(v)
    seen: set[str] = set()
    out = []
    for t in texts:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def canonical_label(node: dict[str, Any]) -> str | None:
    """The ONE label a node maps through — `translations.en`, else `name`.

    Not every raw variant: a technique's English translation and its Portuguese
    variations can trip DIFFERENT label tokens (e.g. "Body Lock das Costas" carries
    "Rear Body Lock" among its variations, which reads BTKA, while its own English
    translation "Body Lock from Back" reads CDP via the plain "body lock" token) — trying
    several and keeping the first non-None would make the mapping depend on array order
    instead of on which label is canonical.
    """
    translations = node.get("translations") or {}
    en = translations.get("en")
    if isinstance(en, str) and en.strip():
        return en
    name = node.get("name")
    if isinstance(name, str) and name.strip():
        return name
    return None


def lamas_code_for_node(node: dict[str, Any]) -> str | None:
    """The library entry's Lamas action code (type wins for the four type-first families
    regardless of the label), or None when its canonical label carries no Lamas action."""
    label = canonical_label(node)
    if label is None:
        return None
    return lamas_state({"type": node.get("type"), "label": label})


# ── eloPercentile: technique -> node_key -> corpus percentile ───────────────────
def rated_athlete_rows(
    graphs: list[tuple[str, Any]], rated: set[str]
) -> list[tuple[str, Any]]:
    """The population-baseline row filter: ADR-16 rated-only + `MIN_GRAPH_NODES`/graph.

    `graphs` is already restricted to `owner_kind='athlete'` by the caller's query — a user
    graph never reaches this function. `rated` empty means "no filter available", per
    `rated_athlete_graph_ids`' own contract (never "exclude everything").
    """
    return [
        (gid, gn)
        for gid, raw in graphs
        if (not rated or gid in rated) and len(gn := grappling_nodes(raw)) >= MIN_GRAPH_NODES
    ]


def elo_baseline(session: Any) -> tuple[Stats, list[float]]:
    """Per-`node_key` corpus ELO stats + the sorted list of means, for percentile lookup."""
    from analysis.rating_v2.config import SITE_RATING_RUN_ID
    from db.repository import graphs_for_clustering, rated_athlete_graph_ids

    graphs = graphs_for_clustering(session, owner_kind="athlete")
    rated = rated_athlete_graph_ids(session, SITE_RATING_RUN_ID)
    rows = rated_athlete_rows(graphs, rated)
    by_key, _by_type = node_population_stats(rows)
    return by_key, sorted(mean for mean, _std, _n in by_key.values())


def percentile_rank(sorted_means: list[float], value: float) -> int:
    """% of `sorted_means` strictly below `value`, rounded to the nearest int."""
    n = len(sorted_means)
    if n == 0:
        return 0
    lt = bisect.bisect_left(sorted_means, value)
    return round(lt / n * 100)


# ── assembly ─────────────────────────────────────────────────────────────────────
def build_scores(
    nodes: list[dict[str, Any]],
    rrb_states: dict[str, float],
    by_key: Stats,
    sorted_means: list[float],
) -> tuple[dict[str, dict[str, float]], dict[str, list[str]]]:
    """One `{rrb?, eloPercentile?}` entry per technique, keyed by every normalized name
    variant it carries. Returns `(scores, key_collisions)`.

    Ownership of a key is claimed by the FIRST technique (file order) to carry it, whether
    or not that technique ends up with any data — a technique whose own entry is empty (no
    rrb, no eloPercentile) still occupies its keys, so a later, unrelated technique sharing
    one of those keys is still a recorded collision rather than a silent win. First wins,
    never overwritten; every collision is listed regardless of which side had data.
    """
    scores: dict[str, dict[str, float]] = {}
    key_owner: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for node in nodes:
        texts = _name_variants(node)
        if not texts:
            continue
        own_name = str(node.get("name"))
        entry: dict[str, float] = {}

        code = lamas_code_for_node(node)
        if code is not None and code in rrb_states:
            entry["rrb"] = rrb_states[code]

        keys = [k for k in (_normalize_name(t) for t in texts) if k]
        matched_key = next((k for k in keys if k in by_key), None)
        if matched_key is not None:
            mean = by_key[matched_key][0]
            entry["eloPercentile"] = float(percentile_rank(sorted_means, mean))

        for key in dict.fromkeys(keys):  # dedup, keep first-seen order
            owner = key_owner.get(key)
            if owner is None:
                key_owner[key] = own_name
            elif owner != own_name:
                collisions.setdefault(key, []).append(own_name)
                continue
            if entry and key not in scores:
                scores[key] = entry
    return scores, collisions


def _fetch_final_bouts(session: Any) -> list[dict[str, Any]]:
    """Every FINAL bout — same shape `scripts/build_markov_action_weights.fetch_bouts` reads,
    minus the unused `event` column (no per-family split here, `global` block only)."""
    from sqlalchemy import text

    rows = session.execute(
        text(
            """
            select m.id::text, m.win_type, m.winner_id::text,
                   m.athlete_a_id::text, m.athlete_b_id::text, m.sequence
              from matches m
             where m.status = 'final'
             order by m.id
            """
        )
    ).fetchall()
    return [
        {"id": r[0], "win_type": r[1], "winner": r[2], "a_id": r[3], "b_id": r[4],
         "seq": list(r[5] or [])}
        for r in rows
    ]


def build(session: Any, nodes: list[dict[str, Any]], generated_date: str) -> dict[str, Any]:
    """The whole artifact. Deterministic given the corpus + library — nothing random, nothing
    from a clock except `version`'s date suffix, which `--check` ignores for that reason."""
    bouts = _fetch_final_bouts(session)
    rrb_states = rrb_by_state(bouts)
    by_key, sorted_means = elo_baseline(session)

    scores, collisions = build_scores(nodes, rrb_states, by_key, sorted_means)
    with_rrb = sum(1 for v in scores.values() if "rrb" in v)
    with_elo = sum(1 for v in scores.values() if "eloPercentile" in v)

    return {
        "version": f"app_node_scores@{generated_date}",
        "scores": dict(sorted(scores.items())),
        "meta": {
            "techniques_in_library": len(nodes),
            "keys_emitted": len(scores),
            "with_rrb": with_rrb,
            "with_elo_percentile": with_elo,
            "corpus_bouts": len(bouts),
            "corpus_node_keys": len(by_key),
            "filters": {
                "rrb": "analysis.lamas_chain.rrb() global block, matches.status='final', "
                       "n_boot=0, row must clear the absorbing-bout + coverage gates",
                "eloPercentile": "owner_kind='athlete' graphs AND "
                                  "db.repository.rated_athlete_graph_ids(SITE_RATING_RUN_ID) "
                                  f"(ADR-16), >= {MIN_GRAPH_NODES} grappling nodes per graph "
                                  "(analysis.ocean.elo_distribution's own gate)",
            },
            "definitions": {
                "rrb": "sub_share of the technique's Lamas action state: "
                       "p_sub_own / (p_sub_own + p_sub_opp), the absorbing-chain propagated "
                       "estimate of a bout passing through that state ending in the acting "
                       "athlete's OWN submission (analysis.lamas_chain.rrb). The library "
                       "carries no per-instance success flag, so the ATTEMPT-side Lamas code "
                       "is used throughout. Omitted when the type/label maps to no Lamas "
                       "state, or the state's row does not clear rrb()'s gates.",
                "eloPercentile": "round(100 * count(other corpus node_keys with a strictly "
                                  "lower mean computed_elo) / total corpus node_keys). "
                                  "Omitted when no name variant of the technique matches a "
                                  "corpus node_key.",
            },
            "key_collisions": collisions,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true",
                     help="rebuild and diff against the committed file (ignoring `version`'s date)")
    ap.add_argument("--stdout", action="store_true", help="print, write nothing")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    from db.base import db_session

    nodes = json.loads(APP_NODES_PATH.read_text(encoding="utf-8"))
    with db_session() as session:
        doc = build(session, nodes, datetime.now(UTC).strftime("%Y-%m-%d"))

    text_out = json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    if args.stdout:
        print(text_out, end="")
        return 0
    if args.check:
        if not args.out.exists():
            print(f"MISSING {args.out}")
            return 1
        old = json.loads(args.out.read_text(encoding="utf-8"))
        new = json.loads(text_out)
        old.pop("version", None)
        new.pop("version", None)
        if old == new:
            print(f"ok — {args.out.relative_to(REPO)} matches the corpus and the library")
            return 0
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                print(f"DIFF {key}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text_out, encoding="utf-8")
    print(f"wrote {args.out.relative_to(REPO)} — {doc['meta']['keys_emitted']} keys, "
          f"{doc['meta']['with_rrb']} rrb, {doc['meta']['with_elo_percentile']} eloPercentile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
