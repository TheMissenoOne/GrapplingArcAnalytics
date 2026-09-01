"""Release check: public site bundle vs canonical DB — public−canonical must be ∅.

Q1 (2026-08-24). The site is generated, but a bundle committed before a merge/synonym
fold keeps stale ids (5 stale ocean node ids after the guard pull/guard pass fold).
This check runs AFTER export.site_data and BEFORE pushing GrapplingArc: every id the
bundle exposes must exist in today's DB derivation.

Checks:
  - GA_OCEAN node ids, link endpoints, neighbour node_keys ⊆ canonicalized corpus keys
  - GA_FIGHTERS slugs ⊆ athlete_key of current athletes
  - GA_BREAKDOWNS ids ⊆ current match ids

Run (repo root, .env loaded):
    uv run python -m scripts.check_site_bundle [path-to-GrapplingArc/site]
Exit 1 on any stale id.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from sqlalchemy import text

from analysis.names import _normalize_name, athlete_key, canonicalize
from analysis.taxonomy_kind import load_inference_table
from analysis.technique_match import clean_label
from db.base import get_engine

SITE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    __file__).resolve().parents[2] / "GrapplingArc" / "site"


def _global(fname: str) -> dict | list:
    src = (SITE / fname).read_text(encoding="utf-8")
    m = re.search(r"window\.GA_\w+\s*=\s*", src)
    assert m, f"no GA_ global in {fname}"
    return json.loads(src[m.end():].split(";\nwindow.")[0].rstrip().rstrip(";"))


def main() -> int:
    eng = get_engine()
    with eng.connect() as c:
        # Same label chain the exporter's graph derivation uses (build_graph._events):
        # clean_label(label, type) → _normalize_name → canonicalize. A naive chain
        # without clean_label false-flags keys like 'neck crank' (split off a slash
        # label) or 'high crotch' (suffix-stripped) as stale.
        corpus_keys = set()
        # The path model (analysis/chain_compiler) keys on canonicalize(_normalize_name(label))
        # with NO clean_label step — contract invariant 1 of
        # docs/taxonomy/03_ARESTA_COMO_CAMINHO.md. build_graph._events DOES clean first. Two
        # legitimate derivations of the same corpus, so the raw set is kept alongside and the
        # path graph is checked against the union: the question this script asks is "is this id
        # still reachable from today's DB", and both chains answer it.
        corpus_keys_raw = set()
        for label, typ in c.execute(text("""
            select distinct e->>'label', e->>'type'
            from matches m, jsonb_array_elements(m.sequence) e
            where e->>'label' is not null""")):
            corpus_keys_raw.add(canonicalize(_normalize_name(str(label))))
            cleaned = clean_label(str(label), str(typ or ""))
            if cleaned:
                corpus_keys.add(canonicalize(_normalize_name(cleaned)))
        athletes = {athlete_key(r[0]) for r in c.execute(text("select name from athletes"))}


    bad: list[str] = []

    ocean = _global("ocean-data.js")
    seen = {n["id"] for n in ocean["nodes"]}
    seen |= {link["from"] for link in ocean["links"]} | {link["to"] for link in ocean["links"]}
    for n in ocean["nodes"]:
        seen |= {nb["node_key"] for nb in n.get("neighbours", [])}
    for k in sorted(seen - corpus_keys):
        bad.append(f"ocean: node_key '{k}' not in canonical corpus")

    # The "edge = path" map (analysis/corpus_paths.path_payload): a STATE point's `stateKey`
    # and every action key on a stroke are node_keys too, and they go stale for exactly the
    # same reason the ocean's own ids do. The frame ANCHORS and the rule's generic actions are
    # deliberately NOT corpus labels (data/taxonomy/inference_table.json invents them), so they
    # are excluded by that table rather than by a hardcoded list.
    generic = set(load_inference_table().get("generic_states", {})) \
        | set(load_inference_table().get("generic_actions", {}))
    path_keys: set[str] = set()
    for n in (ocean.get("pathGraph") or {}).get("nodes", []):
        if n.get("stateKey"):
            path_keys.add(str(n["stateKey"]).removeprefix("opp:"))
    for lk in (ocean.get("pathGraph") or {}).get("links", []):
        path_keys |= {str(a["key"]) for a in lk.get("actions", [])}
    for k in sorted(path_keys - corpus_keys - corpus_keys_raw - generic):
        bad.append(f"ocean pathGraph: node_key '{k}' not in canonical corpus")

    for f in _global("fighters-data.js"):
        name = re.sub(r"<[^>]+>", " ", f["name"])
        if athlete_key(name) not in athletes:
            bad.append(f"fighters: '{name}' ({f['slug']}) not in athletes")

    # breakdown ids are slugs, not match UUIDs — check the two athlete names instead
    for b in _global("breakdowns-data.js"):
        for side in ("a", "b"):
            if athlete_key(b[side]["name"]) not in athletes:
                bad.append(f"breakdowns: '{b[side]['name']}' ({b['id']}) not in athletes")

    if bad:
        print(f"STALE — public−canonical has {len(bad)} entries:")
        for line in bad:
            print(" ", line)
        return 1
    print("OK — public−canonical = ∅ "
          f"(ocean keys={len(seen)}, fighters={len(_global('fighters-data.js'))}, "
          f"breakdowns={len(_global('breakdowns-data.js'))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
