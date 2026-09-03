# N2 composite-label reprocess — replay runbook (owner-runnable)

Code is done and merged (`analysis/composite_labels.py`, `data/taxonomy/composite_labels.json`,
`db.repository.expand_sequence` wired into `register_match`/`register_matches_bulk`/
`update_match`, `state_orientation.json` 39 → 93 lines, `guard recovery` fix). Nothing below has
been run — every write here touches prod. Contract: `docs/taxonomy/04_ONTOLOGIA_CANONICA.md` §9.

## Why a reprocess, not just the code

The expansion is wired at every WRITE path, so any match imported/pasted/edited from today
onward gets it automatically. But `matches.sequence` for bouts already in prod still carries the
composite labels as-logged — the code fix does not touch a row unless something writes it again.
This reprocess re-writes every affected row's `sequence` through the SAME
`analysis.composite_labels.expand_sequence` the write paths use, doing exactly what a fresh
import would have done.

**Operate on `matches.sequence` in the DATABASE, never on the root dumps
(`scripts/dumps/*_data.py`)** — the `dumps-diverged-from-db` scar: a prior repair (AA-011) fixed
the DB and never made it back into the dumps, so the dumps are already stale relative to prod by
one events count. Re-deriving from a dump here would re-introduce that exact drift, or worse,
overwrite the AA-011 fix. This script reads/writes `Match.sequence` through `db.repository`
only.

## Estimate (read-only)

```bash
cd GrapplingArcAnalytics
set -a; source .env; set +a
uv run python3 -c "
from db.base import db_session
from db.models import Match
from analysis.composite_labels import _load_table
from analysis.names import _normalize_name

table = _load_table()
with db_session() as s:
    matches = list(s.query(Match).all())

affected_matches = affected_events = 0
for m in matches:
    hits = sum(1 for e in (m.sequence or [])
               if isinstance(e, dict) and _normalize_name(str(e.get('label', ''))) in table)
    if hits:
        affected_matches += 1
        affected_events += hits
print(f'{len(matches)} matches total, {affected_matches} affected, {affected_events} composite events to expand')
"
```

Measured against prod 2026-09-04: **912 matches, 142 affected, 248 composite events** decompose
into 1-2 events each (net new events: roughly +150 to +250, most composites are 2-way splits,
some `{action,to}` rows with a generic `to` drop to 1 event). This is lower than the
orchestrator's earlier estimate of 336 (a different SELECT, scope not reproduced here) — treat
248 as the number this exact script measures, and re-run it right before the write step below to
catch any corpus change between now and then.

## Reprocess (write)

One `update_match` call per affected bout — reuses the exact function every admin edit already
goes through (expands `sequence`, re-registers the split labels into the shared technique
library when the match is `status == 'final'`), rather than writing `Match.sequence` directly.

```bash
uv run python3 -c "
from db.base import db_session
from db.models import Match
from db.repository import update_match
from analysis.composite_labels import _load_table
from analysis.names import _normalize_name

table = _load_table()

def needs_expansion(seq):
    return any(isinstance(e, dict) and _normalize_name(str(e.get('label', ''))) in table
               for e in (seq or []))

with db_session() as s:
    matches = [m for m in s.query(Match).all() if needs_expansion(m.sequence)]
    print(f'{len(matches)} matches to reprocess')
    for m in matches:
        update_match(
            m.id,
            athlete_a_id=m.athlete_a_id, athlete_b_id=m.athlete_b_id,
            winner_id=m.winner_id, win_type=m.win_type, submission=m.submission,
            event=m.event, year=m.year, weight_class=m.weight_class, stage=m.stage,
            sequence=m.sequence, session=s, video_url=m.video_url,
        )
    s.commit()
print('done')
"
```

`update_match` expands idempotently (an already-atomic label is a no-op through
`expand_composite`), so running this script twice is safe — the second pass reprocesses nothing.

## Checks after, before the N1 replay

```bash
uv run python -m scripts.audit_ontology --check   # rc=0, composites stays at 11 (the _skipped set)
uv run python3 -c "
from db.base import db_session
from db.models import Match
from analysis.composite_labels import _load_table
from analysis.names import _normalize_name
table = _load_table()
with db_session() as s:
    remaining = sum(
        1 for m in s.query(Match).all() for e in (m.sequence or [])
        if isinstance(e, dict) and _normalize_name(str(e.get('label', ''))) in table
    )
print('remaining composite events in matches.sequence:', remaining)   # expect 0
"
```

## Then: joins the N1 replay — do not replay twice

This reprocess changes `matches.sequence` (new `node_key`s for the split halves — e.g. `Guard
Pass to Mount` never existed as a node, `Guard Pass` and `Mount` do). Every derived artefact
keyed by `node_key` (`computed_elo`, `graph_edges.elo`, `graphs.user_elo`, `athletes.elo`,
`elo_series`, the shared technique library, the site) needs the SAME full replay N1 already
requires for its alias merges — run this reprocess BEFORE step 1 of
`docs/repairs/2026-09-04_n1_alias_replay.md`'s runbook (seed → replay → backfill → baselines →
site regen), then continue that runbook unchanged from its "Runbook — order, all writes prod"
section. One replay, N1 + N2 together — never `scripts.reprocess_all`
(`docs/rating_v2/08_ESTADO_DO_CUTOVER.md`).
