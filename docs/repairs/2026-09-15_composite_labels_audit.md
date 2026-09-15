# 2026-09-15 — Composite labels audit (full sweep, all 3 sources)

Owner found `Crucifix / Omoplata`, `Omoplata / Triangle`, `Omoplata (Shoulder Lock)` outside the
curated N2 table and asked: is there any OTHER instance of a composite label in the corpus?
**Read-only against prod.** No write to `matches`, no `scripts.reprocess_all`, nothing committed.
Contract: `docs/taxonomy/04_ONTOLOGIA_CANONICA.md` §9 (N2); prior work: `data/taxonomy/
composite_labels.json`, `analysis/composite_labels.py`, `docs/repairs/
2026-09-04_n2_composite_reprocess.md`.

## Method

Three sources, 9 separator/suffix/prefix patterns (broader than N2's own 2-pattern detector in
`scripts/audit_ontology.py`, which only looks for `" to "` / `" / "`):

| pattern | example |
|---|---|
| `" / "` | `Omoplata / Triangle` |
| `" to "` | `Escape to Turtle` |
| `->` / `→` | (none found) |
| `+` | (none found in a label — only `Pin & Cut` variant hit `&`) |
| `" & "` | `Pin & Cut` (library variant) |
| `(...)` parenthetical | `Omoplata (Shoulder Lock)` |
| `:` | (none found) |
| `" - "` dash (spaces both sides — excludes `X-Guard`/`50-50`) | (none found) |
| `,` | (none found) |
| suffix word `attempt(ed/ing)\|failed\|defense\|defence\|defended` | `Takedown Defense` |
| prefix `top\|bottom` (perspective) | `Top Half Guard` |
| bare outcome word (`tap`/`finish`/`submission`/`escape`/`win`/`loss`/…) | `Escape` |

Excluded false positives (given + `COMPOSITE_FALSE_POSITIVES` in `scripts/audit_ontology.py`):
`Shin to Shin`, `Chest-to-Chest`, `50/50 Guard`, `X-Guard`.

Sources read:
1. **`matches.sequence[].label`** — all 911 rows in `matches` (741 with a non-empty `sequence`,
   10 016+ events), grouped by `source_batch`.
2. **`technique_nodes.node_key`** where `source='library'` (451 rows — every distinct label ever
   registered by a `status='final'` write, App-facing shared vocabulary).
3. **`analysis/data/technique_library.json`** (210 curated entries) — `en`, `pt`, `variants`.

## Totals

| source | distinct composite labels | events / rows | bouts affected |
|---|---|---|---|
| `matches.sequence` (live) | **37** | **656** | **347** (of 741 with sequence) |
| `technique_nodes` (source=library) | 82 | — (node rows, not events) | — |
| `technique_library.json` | 47 hits (`en`/`pt`/`variant`) | — | — |

Of the 82 `technique_nodes` hits, **47 are stale** — the literal label no longer appears in
`matches.sequence` at all (already expanded by the 2026-09-04 N2 reprocess, or superseded by a
manual rename/edit — this is where `Crucifix / Omoplata` lives: 0 occurrences in current
`matches.sequence`, only `Crucifix` and `Mounted Crucifix` remain). These are orphaned rows in
`technique_nodes`, a DB-hygiene question, **not** a composite-label decision — noted below, no
action taken (no write performed). The remaining 35 match one of the 37 live labels 1:1 (13 are
already curated/skipped in `composite_labels.json`, 22 are the "live+new" set this audit reviews).

`technique_library.json`'s 47 hits are **all** the dictionary's own deliberate `en`/`pt`/variant
forms (`Bridge (Upa Escape)`, `armbar attempt`, `Top Control`, …) — every one is the curated name
itself, never a stray un-curated composite. **Verdict: keep, all 47** (they define the vocabulary
other labels resolve against; nothing to decompose here by construction).

**Top batches by composite-event count** (of the 656 live events, 56 distinct batches):
`(null)` 161, `PGF2026-W3` 49, `PGF2026-W4` 43, `PGF2026-W2` 34, `PGF2026-W5` 32, `UFC matches` 18,
`ADCC2022+99kg` 18, `UFC card` 16, `ADCC2022-ABS` 14, `ADCC2022-88kg` 13, `CJI` 13. Driven almost
entirely by the 4 bare outcome words below (451 of 656 events) — not a batch-quality problem the
way the actor-ownership gap in `docs/match_event_model.md` was; it is one generic verb reused
corpus-wide, e.g. `Finish`/`Tap`/`Submission` cluster in the `PGF2026-*` batches specifically.

## Full table, sorted by event count (all 37 live labels)

Full detail + `why` per row: `data/taxonomy/composite_labels.proposed.json`. Summary:

| events | bouts | label | verdict | note |
|---:|---:|---|---|---|
| 184 | 111 | `Escape` | **reject** | bare outcome word, not composite |
| 112 | 66 | `Finish` | **reject** | bare outcome word |
| 87 | 72 | `Tap` | **reject** | bare outcome word |
| 68 | 59 | `Submission` | **reject** | bare outcome word |
| 49 | 37 | `Bridge (Upa Escape)` | keep | curated `en`, paren = alt-name |
| 35 | 25 | `Saddle (Inside Sankaku)` | keep | curated `en`, paren = alt-name |
| 29 | 24 | `Takedown Defense` | keep | curated `en`, standalone technique |
| 18 | 17 | `Top Control` | keep | curated `en`, standalone technique |
| 16 | 16 | `Americana (Keylock)` | keep | curated `en`, paren = alt-name |
| 11 | 9 | `Omoplata (Shoulder Lock)` | keep | curated `en`, paren = alt-name |
| 8 | 7 | `Top Control (Half Guard)` | keep | already N2-curated (perspective) |
| 7 | 7 | `Top Half Guard` | keep | already N2-curated (perspective) |
| 2 | 2 | `Body Triangle (Bottom)` | keep | already N2-curated (perspective) |
| 2 | 2 | `Head‑Arm Control (Top)` | keep | already N2-curated (perspective) |
| 2 | 2 | `Shrimp (Hip Escape)` | keep | curated `en`, paren = alt-name |
| 2 | 1 | `Leg Entanglement / 50/50` | keep | already `_skipped` (both sides are states) |
| 2 | 2 | `Knockdown (right hand)` | keep | MMA strike descriptor, dictionary gap |
| 2 | 2 | `Takedown (Back Exposure)` | **reject** | `Back Exposure` is a **dictionary gap** — nothing to point `to` at |
| 1 | 1 | `Ankle Pick Takedown (SV)` | keep | wrestling period tag ("Sudden Victory"), dictionary gap |
| 1 | 1 | `Knockdown (punches)` | keep | MMA strike descriptor, dictionary gap |
| 1 | 1 | `Omoplata / Triangle` | keep | already `_skipped` (ambiguous combo/dup-name, n=1) |
| **1** | **1** | **`Top Control (Body Lock)`** | **decompose** | **gap in the already-curated perspective set — see below** |
| 1 | 1 | `Katagatame / Gift Wrap` | keep | already `_skipped` (ambiguous, n=1) |
| 1 | 1 | `Armbar / Choi Bar` | keep | already `_skipped` (alias, same submission two names) |
| 1 | 1 | `Arm‑Triangle / Head‑Arm Strangle` | keep | already `_skipped` (alias) |
| **1** | **1** | **`Pull Guard / Sit Guard`** | **decompose** | **N1 blocker from `_skipped` is already resolved — see below** |
| 1 | 1 | `Neck Crank / Rear Naked Choke` | keep | already `_skipped` (ambiguous, n=1) |
| 1 | 1 | `Leg Entry (50/50)` | keep | both sides resolve to `guard`-family states, no `{action,to}` fit |
| 1 | 1 | `Knockdown (head kick)` | keep | MMA strike descriptor, dictionary gap |
| 1 | 1 | `Half Guard (flat)` | keep | style qualifier, dictionary gap |
| 1 | 1 | `Rear Naked Choke (One-Arm)` | keep | variant qualifier, dictionary gap |
| 1 | 1 | `Quick Stand‑up Escape (TB)` | keep | wrestling period tag ("Tie Breaker"), dictionary gap |
| 1 | 1 | `Knee Crush / Calf Slicer` | keep | already `_skipped` (near-synonym) |
| 1 | 1 | `Katagatame / Darce` | keep | already `_skipped` (alias) |
| 1 | 1 | `Armbar / Triangle` | keep | already `_skipped` (ambiguous combo, n=1) |
| 1 | 1 | `Guard Pass (Tripod)` | keep | style qualifier, dictionary gap |

**Verdict counts: 30 keep, 5 reject, 2 decompose.**

### The 2 decompose candidates

```json
"Top Control (Body Lock)": {"state": "Body Lock", "perspective": {"actor": "top"}}
"Pull Guard / Sit Guard": {"action": "Guard Pull", "to": "Seated Guard"}
```

- **`Top Control (Body Lock)`** — exact same shape as the 4 perspective pairs already live
  (`top half guard`, `top control half guard`, `body triangle bottom`, `headarm control top`).
  `Body Lock` is a curated `state` (N0 reclassified `control/Body Lock`, 68 events in the main
  corpus). This is a coverage gap in the existing perspective set, not a new kind of decision —
  1 event only, so low urgency, but mechanically identical to what's already curated.
- **`Pull Guard / Sit Guard`** — `composite_labels.json._skipped` deferred this exact label with
  "target might be `seated guard` already curated under another spelling, decision is N1, not
  N2." That block is resolved now: `technique_library.json`'s `Seated Guard` entry already lists
  `sit guard` as a variant (confirmed via `analysis.technique_match._index`). `Pull Guard`
  resolves to `Guard Pull` (transition). Both sides are curated → fits `{action, to}`. n=1
  (`WNO31`) — recommend confirming against the source transcript before promoting to the live
  table, same caution the original `_skipped` note asked for.

Both candidates pass `tests/test_composite_labels_audit.py::test_every_referenced_key_resolves_or_is_null`.

## Dictionary gaps (referenced key has no curated entry)

| label | missing piece | why it blocks decomposition |
|---|---|---|
| `Takedown (Back Exposure)` | `Back Exposure` | no curated node to point `to` — may be a synonym of `Back Take`/`Back Control`, unconfirmed |
| `Knockdown (right hand)` / `(punches)` / `(head kick)` | the whole label | MMA striking descriptors (UFC-batch bouts), never meant to be BJJ-library entries — `ADR-05`, MMA/wrestling athletes rated outside the Glicko-2 node track |
| `Ankle Pick Takedown (SV)` / `Quick Stand‑up Escape (TB)` | the whole label | `(SV)`/`(TB)` are **wrestling period tags** (Sudden Victory / Tie Breaker, `2025NCAA` batch), not a second technique — the parenthetical is match-context metadata, out of the N2 state/action/perspective shapes entirely |
| `Guard Pass (Tripod)` | style qualifier | plausible future library variant (`Tripod` is a recognized pass style), not a compound event |
| `Rear Naked Choke (One-Arm)` | style qualifier | same — recognized RNC variant, not a compound event |
| `Half Guard (flat)` | style qualifier | same — recognized half-guard variant, not a compound event |

None of these are N2 decompositions (they never had two grappling nodes baked into one label to
begin with) — they're candidates for a future **N1 library-expansion** pass (new `variants`
entries in `technique_library.json`), a different, smaller kind of change with no replay
implication (adding a variant doesn't move an existing `node_key`).

## Compostos legítimos (parenthetical alt-name, never decompose)

Confirmed by the label being the curated `en` field itself in `technique_library.json`:
`Bridge (Upa Escape)`, `Saddle (Inside Sankaku)`, `Americana (Keylock)`,
`Omoplata (Shoulder Lock)`, `Shrimp (Hip Escape)` — same pattern as the already-excluded
`50/50 Guard`/`X-Guard`/`Shin to Shin`/`Chest-to-Chest`: the parenthetical names a **synonym**,
not a second position. Plus the given false positives themselves (not re-litigated here).

Ambiguous "A / B" pairs already reviewed and explicitly kept in `composite_labels.json._skipped`
(re-confirmed present, still correct): `Leg Entanglement / 50/50`, `Reset / Stalemate`,
`Armbar / Choi Bar`, `Arm‑Triangle / Head‑Arm Strangle`, `Katagatame / Darce`,
`Katagatame / Gift Wrap`, `Knee Crush / Calf Slicer`, `Armbar / Triangle`,
`Neck Crank / Rear Naked Choke`, `Omoplata / Triangle` — same finish/position named two ways
(or a genuinely ambiguous n=1 with no transcript to disambiguate combo-vs-alt-name). Decomposing
any of these would duplicate one occurrence into two nodes, not describe it better.

## Bare outcome words — a different defect, out of N2 scope

`Escape` (184), `Finish` (112), `Tap` (87), `Submission` (68) — **451 of 656 flagged events**,
the overwhelming majority of this audit's raw count. These are **not** composite labels: there is
no `B` to split from `A`, they're a single vague verb standing in for a real, unspecified
technique. `Escape` logs `type='transition'`/`'escape'`, `Finish`/`Tap`/`Submission` all log
`type='submission'` — a real submission happened and the source didn't name which one. This is a
label-**specificity** gap (transcription/ingestion quality), not something N2's `{action,to}` /
`{state,action}` / `{state,perspective}` shapes can express — decomposing a single vague verb
produces nothing more informative. Flagged and rejected per the task's own instruction to review
outcome-word labels; no further action proposed here.

## `technique_nodes` orphans (informational, no write performed)

47 of the 82 `technique_nodes(source='library')` composite rows have **zero** occurrences in
current `matches.sequence` — remnants of labels already expanded by the 2026-09-04 N2 reprocess
(`Guard Pass to Mount`, `Escape to Turtle`, `Leg Drag to Straddle`, …) or superseded by a manual
rename (`Crucifix / Omoplata`, `Drag‑to‑Single Leg Takedown (TB)`). They're harmless — nothing
reads `technique_nodes` rows with zero graph edges — but they're dead weight in the shared
vocabulary table. Not fixed here (no DB write in this task's scope); candidate for a follow-up
`scripts/prune_orphan_technique_nodes.py`-style cleanup, same shape as
`scripts/prune_orphan_athlete_graphs.py`.

## The plan to apply (N2, unchanged from 2026-09-04 — cited, not re-run)

Same mechanism as the existing 29 curated entries, nothing new to build:

1. Add the 2 decompose rows above to `data/taxonomy/composite_labels.json` (human review first,
   especially the n=1 `Pull Guard / Sit Guard`).
2. `analysis.composite_labels.expand_composite` picks them up automatically — already wired into
   every write path (`db.repository.register_match`/`register_matches_bulk`/`update_match`) via
   `expand_sequence`, so any match imported/pasted/edited after step 1 lands split.
3. **Reprocess the 2 already-live rows** (`Top Control (Body Lock)` in `WNO31`,
   `Pull Guard / Sit Guard` in `WNO31`) the same way `docs/repairs/
   2026-09-04_n2_composite_reprocess.md` describes: `update_match` re-run against
   `matches.sequence` **in the database**, never against `scripts/dumps/*_data.py` — that's the
   `dumps-diverged-from-db` scar (`docs/dumps-diverged-from-db.md` / MEMORY.md): a prior repair
   (AA-011) fixed the DB and never made it back into the dumps, so re-deriving from a dump here
   would reintroduce that exact drift. `analysis.composite_labels.expand_composite` is idempotent
   (an already-atomic label is a no-op), so this is safe to run alongside any pending N1 batch.
4. **Replay implication**: splitting these 2 labels creates new `node_key`s (`Body Lock`,
   `Guard Pull`, `Seated Guard` all already exist elsewhere in the corpus, so no NEW key is truly
   novel — but the 2 specific `matches.sequence` rows change shape), which is the same class of
   change N1/N2 already require a full replay for
   (`docs/rating_v2/08_ESTADO_DO_CUTOVER.md`) — `computed_elo`, `graph_edges.elo`,
   `graphs.user_elo`, `athletes.elo`, `elo_series`, the site export. Given the blast radius here
   is 2 events in 1 bout (`WNO31`), the pragmatic move is to fold this into whichever N1/N2
   replay batch is next (`docs/repairs/2026-09-14_n1_alias_replay_batch2.md` is the most recent),
   not to trigger a standalone replay for 2 events.
5. The dictionary gaps and orphan `technique_nodes` rows above are **not** part of this plan —
   separate, smaller pieces of future work, noted so they aren't re-discovered from scratch.

## Files

- `data/taxonomy/composite_labels.proposed.json` — proposal (NOT the live table), all 37 live
  labels with `{state, action, to, perspective, alias_of, verdict, why, events, bouts}`.
- `tests/test_composite_labels_audit.py` — every referenced key resolves against
  `analysis.technique_match._index` (or is null); no known-legitimate composite got
  `verdict=decompose`; every `decompose` row matches one of `expand_composite`'s 3 shapes.
- This report.
