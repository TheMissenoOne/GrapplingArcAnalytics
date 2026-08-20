"""Retire technique_nodes rows whose key is now a SYNONYMS alias. DRY-RUN BY DEFAULT.

**Read this before running it.** A node-level merge was tried once already and did not hold:
commit 277821f (2026-07-14) records why — "node-level merges don't survive replay/export (both
re-derive node keys from raw Match event labels)". Merging rows in `technique_nodes` fixes
what is in the table today and nothing about tomorrow, because the next replay reads
`matches.sequence[].label` again and mints the alias node back.

So this script is NOT the fix. `analysis/names.py:SYNONYMS` is the fix: it collapses at
derivation, which is the only layer re-derivation passes through. This script is the cleanup
that follows it -- it removes rows that SYNONYMS has just made unreachable, so today's DB and
today's site stop showing one action as two. Run it only AFTER the alias exists in SYNONYMS,
or you are re-fighting a settled battle.

Two things get repointed, and both matter for the same reason scar #2 in `failure-archaeology`
matters: an FK repointed without the denormalised copy leaves the merge half-done.

  * `graph_nodes(graph_id, node_key)` -- since 0037 the edge endpoints FK into THIS, per graph,
    not into `technique_nodes`. A graph cannot hold an edge on the canonical key until it has a
    node row for it, so one is created where missing.
  * `graph_edges.source_key` / `.target_key` -- the endpoint keys themselves.
  * `graph_edges.edge_key` -- a denormalised "source→target" string (the separator is U+2192,
    NOT ">") that would otherwise still name the retired key. Rewritten only for rows this
    script actually repoints: a `like '%pass%'` match hits 811 rows, almost none of which
    involve the bare `pass` node, and rewriting those with a guessed separator would have
    corrupted every one of them.

Raw `matches.sequence[].label` is deliberately NOT rewritten. That string is the record of what
the source actually said, and SYNONYMS already makes every derivation agree on where it lands.

    uv run python scripts/merge_synonym_nodes.py            # dry run
    uv run python scripts/merge_synonym_nodes.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from sqlalchemy import text  # noqa: E402

from analysis.names import SYNONYMS, canonical_label  # noqa: E402
from db.base import get_engine  # noqa: E402

BACKUP = REPO / "runs" / "node_merge"
# Read off the live table, not assumed: edge_key joins its two keys with U+2192, and writing
# ">" instead would rename every row it touched into something nothing can resolve.
SEP = "\u2192"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    eng = get_engine()
    plan: list[dict[str, str | int]] = []
    with eng.connect() as c:
        present = {r[0] for r in c.execute(text("select node_key from technique_nodes"))}
        for alias, canon in SYNONYMS.items():
            if alias not in present:
                continue
            if canon not in present:
                # Retiring an alias whose target does not exist would orphan every edge on it.
                print(f"  SKIP {alias!r}: canonical {canon!r} is not in technique_nodes")
                continue
            src = c.execute(text("select count(*) from graph_edges where source_key=:k"),
                            {"k": alias}).scalar()
            tgt = c.execute(text("select count(*) from graph_edges where target_key=:k"),
                            {"k": alias}).scalar()
            # only rows this script will actually repoint -- never a substring match
            ek = c.execute(text("select count(*) from graph_edges "
                                "where source_key=:k or target_key=:k"), {"k": alias}).scalar()
            usr = c.execute(text("select count(*) from graph_edges where owner_kind<>'athlete' "
                                 "and (source_key=:k or target_key=:k)"), {"k": alias}).scalar()
            plan.append({"alias": alias, "canonical": canon, "source_edges": int(src or 0),
                         "target_edges": int(tgt or 0), "edges_touched": int(ek or 0),
                         "non_athlete_edges": int(usr or 0),
                         "label": str(canonical_label(canon, canon))})

    if not plan:
        print("nothing to retire — every SYNONYMS alias is already absent from technique_nodes")
        return 0
    print(f"aliases still present as their own node: {len(plan)}\n")
    for p in plan:
        print(f"  {p['alias']!r} -> {p['canonical']!r} ({p['label']})")
        print(f"     graph_edges: {p['source_edges']} as source, {p['target_edges']} as target, "
              f"{p['edges_touched']} rows repointed"
              + (f"  ** {p['non_athlete_edges']} NOT owner_kind='athlete' **"
                 if p["non_athlete_edges"] else ""))
    private = sum(int(p["non_athlete_edges"]) for p in plan)
    if private:
        # Retiring a node a USER graph points at is a different decision from cleaning the
        # public corpus, and this script is not authorised to make it.
        print(f"\nREFUSING: {private} affected edges are not owner_kind='athlete'. "
              "A user graph is private data; retiring a node it references needs its own call.")
        return 2
    if not a.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    restored: list[dict[str, str]] = []
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    BACKUP.mkdir(parents=True, exist_ok=True)
    with eng.begin() as c:
        keys: list[str] = [str(p["alias"]) for p in plan]
        before = [dict(r._mapping) for r in c.execute(text(
            "select * from graph_edges where source_key = any(:k) or target_key = any(:k)"),
            {"k": keys})]
        nodes = [dict(r._mapping) for r in c.execute(text(
            "select node_key, label, node_type from technique_nodes where node_key = any(:k)"),
            {"k": keys})]
        (BACKUP / f"backup_{stamp}.json").write_text(
            json.dumps({"plan": plan, "graph_edges": before, "technique_nodes": nodes},
                       indent=1, default=str), encoding="utf-8")

        for p in plan:
            alias, canon = str(p["alias"]), str(p["canonical"])

            # ORDER IS FORCED by a non-deferrable composite FK. `graph_edge_bouts` references
            # (graph_id, source_key, target_key) on graph_edges, so the edge cannot be
            # repointed while a provenance row still names the old key. The rows are lifted
            # out, the edge is moved, and they go back on the canonical key -- losing them
            # would throw away which bout each edge came from, which is the unit the
            # bootstrap resamples.
            lifted = [dict(r._mapping) for r in c.execute(text(
                "select graph_id::text, source_key, target_key, match_id::text "
                "from graph_edge_bouts where source_key=:a or target_key=:a"), {"a": alias})]
            c.execute(text("delete from graph_edge_bouts "
                           "where source_key=:a or target_key=:a"), {"a": alias})

            # `graph_nodes` is NOT leftover from before 0007 — 0007 did drop it, and 0037
            # brought it back with a different job: each graph's OWN node identity, private by
            # construction, optionally linked to the curated library. `graph_edges` endpoints
            # FK into it, which is the privacy fix itself (0005's FK into the world-readable
            # `technique_nodes` was what forced the App to write private labels there).
            #
            # So an edge can only be repointed to the canonical key once THIS GRAPH has a node
            # row for it. Create it where missing, carrying the library link.
            # `label` is NOT NULL: a graph node exists to say what THIS graph calls the node,
            # so it cannot be created without a name. The curated canonical label is the right
            # one — copying the alias's display text would put "Pull Guard" on a "guard pull"
            # node and undo the collapse at the only place a reader ever sees it.
            c.execute(text("""
                insert into graph_nodes (graph_id, node_key, label, canonical_node_key)
                select distinct e.graph_id, :c, :lab, :c from graph_edges e
                 where (e.source_key = :a or e.target_key = :a)
                   and e.owner_kind = 'athlete'
                   and not exists (select 1 from graph_nodes n
                                    where n.graph_id = e.graph_id and n.node_key = :c)
                on conflict do nothing"""),
                {"a": alias, "c": canon, "lab": str(p["label"])})
            c.execute(text("update graph_nodes set canonical_node_key=:c "
                           "where canonical_node_key=:a"), {"a": alias, "c": canon})

            touched = [r[0] for r in c.execute(text(
                "select id::text from graph_edges where owner_kind='athlete' "
                "and (source_key=:a or target_key=:a)"), {"a": alias})]
            c.execute(text("update graph_edges set source_key=:c "
                           "where source_key=:a and owner_kind='athlete'"),
                      {"a": alias, "c": canon})
            c.execute(text("update graph_edges set target_key=:c "
                           "where target_key=:a and owner_kind='athlete'"),
                      {"a": alias, "c": canon})
            if touched:
                c.execute(text("update graph_edges set edge_key = source_key || :sep || "
                               "target_key where id::text = any(:ids)"),
                          {"sep": SEP, "ids": touched})
            # the per-graph alias node goes only once nothing points at it any more
            c.execute(text("""
                delete from graph_nodes n where n.node_key = :a
                  and not exists (select 1 from graph_edges e where e.graph_id = n.graph_id
                                  and (e.source_key = :a or e.target_key = :a))"""), {"a": alias})
            c.execute(text("delete from technique_nodes where node_key=:a"), {"a": alias})
            restored.extend(
                {**r, "source_key": canon if r["source_key"] == alias else r["source_key"],
                 "target_key": canon if r["target_key"] == alias else r["target_key"]}
                for r in lifted)

        # A merge can collide two edges onto one (source, target) inside one graph. Keep the
        # higher-elo row: an edge that scored is worth more than a duplicate that did not.
        dropped = c.execute(text("""
            delete from graph_edges e using graph_edges k
             where e.graph_id = k.graph_id and e.source_key = k.source_key
               and e.target_key = k.target_key
               and (coalesce(e.elo, 0), e.id::text) < (coalesce(k.elo, 0), k.id::text)
            returning e.id""")).rowcount

        # Back on the canonical key. A provenance row whose edge did not survive the collapse
        # is dropped rather than forced: it would point at nothing. ON CONFLICT because a
        # merge can map two provenance rows onto one edge.
        put_back = 0
        for r in restored:
            put_back += c.execute(text("""
                insert into graph_edge_bouts (graph_id, source_key, target_key, match_id)
                select cast(:g as uuid), :s, :t, cast(:m as uuid)
                 where exists (select 1 from graph_edges e where e.graph_id = cast(:g as uuid)
                               and e.source_key = :s and e.target_key = :t)
                on conflict do nothing"""),
                {"g": r["graph_id"], "s": r["source_key"], "t": r["target_key"],
                 "m": r["match_id"]}).rowcount

    with eng.connect() as c:
        left = c.execute(text("select count(*) from technique_nodes where node_key = any(:k)"),
                         {"k": [p["alias"] for p in plan]}).scalar()
        # against graph_nodes, which is what the endpoints actually FK into since 0037.
        # Checking technique_nodes reports every private user label as an orphan -- 177 of them
        # here -- and none of those are broken.
        orphan = c.execute(text("""
            select count(*) from graph_edges e where not exists
              (select 1 from graph_nodes n
                where n.graph_id = e.graph_id and n.node_key = e.source_key)
               or not exists
              (select 1 from graph_nodes n
                where n.graph_id = e.graph_id and n.node_key = e.target_key)""")).scalar()
    print(f"\nAPPLIED. alias nodes remaining = {left} (want 0); "
          f"duplicate edges collapsed = {dropped}; orphan edges = {orphan} (want 0); "
          f"provenance rows lifted {len(restored)} and re-attached {put_back}")
    print(f"backup: {BACKUP / f'backup_{stamp}.json'}")
    print("Next: re-export the site so the generated bundle stops showing the retired keys.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
