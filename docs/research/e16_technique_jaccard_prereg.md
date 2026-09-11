# PoC-E16 — plain technique-label Jaccard as "grapples most like": pre-registration

Written 2026-09-11, **before any arm was scored**. Everything below was fixed against
read-only MARGINALS — bout counts, cohort sizes, which columns `matches` carries, how the
corpus clusters in ingestion time, how many athletes use each label. None of those say which
method wins. Results are appended to this same file, below the pre-registration, by
`uv run python -m scripts.research.e16_technique_jaccard`.

**Owner decision being tested (2026-09-11):** *E16 is OPEN with plain technique-label
Jaccard (E15's control `c1`) as the pre-registered candidate.*

This cell exists because PoC-E15 ended with a control winning. `c1` — `|A∩B| / |A∪B|` over
technique labels, no communities, no embeddings, no weighting — was registered there as an
ABLATION, scored the highest MRR in every pass, and was the first arm in that series to
clear the size null. E15 could not accept it: promoting a control after seeing the table is
the exact error pre-registration exists to prevent. Its §8 named the four things the next
cell has to register first. This is that cell, and §§3–6 below are those four things.

---

## 1. Roles, corrected

E15's table, with every role re-assigned **before** this run:

| id | arm | role HERE | role in E15 |
|---|---|---|---|
| **`c1`** | **whole-graph technique-label Jaccard** | **HYPOTHESIS (H1)** — the only arm with a verdict | ablation |
| `n1` | size descriptor — `(log n, log m, mean degree)` | **null (H0)** | null (H0) |
| `n2` | degree histogram | **null** | null |
| `a0` | production `athlete_systems.system_similarity`, 12-node | **reference** | reference |
| `r1` | flat occurrence-weighted mpnet centroid | **reference**, no verdict | reference |
| `v2` / `v3` / `v4` | `c1` over labels used by ≥2 / ≥3 / ≥4 cohort athletes | **sweep** (§5), no verdict | `p1` was the ≥3 point only, post-hoc |

`c2` (system member Jaccard) and `c3` (per-system centroid) are **not re-run**. E15 rejected
both against their pre-registered criteria; re-running a rejected arm on nearly the same
corpus adds a comparison and no information.

`r1` still carries no verdict for the reason E15 gave: it is PoC-E5's strongest arm re-run
on nearly the same data, so its number here is that same measurement ± corpus drift, not an
independent confirmation. It is reported so "the simplest arm beats the embedding one" is a
statement with a number under it.

## 2. What is reused verbatim (imported, never re-typed)

Numbers must stay comparable to E5 and E15, so every loader, gate, cohort builder, retrieval
scorer and bootstrap is **imported**, not copied:

| Thing | Source |
|---|---|
| corpus + gate | `analysis.poc.e9_markov.load_corpus` (`status='final' AND sequence IS NOT NULL`, ≥4 events, `attribution.bout_flags(...)['perspective_reliable']`) |
| athlete bouts, halves, cohort gate | `analysis.poc.e5_grapple_like.{athlete_bouts,split_halves,build_cohort}` (≥3 nodes, ≥2 edges per half) |
| graph builder | `analysis.transitions.build_graph.network_from_sequences`, own events only |
| retrieval scoring | `analysis.poc.signatures.retrieval` (pessimistic ties, NaN last) |
| paired bootstrap | `e5_grapple_like._paired_ci` → `stats_rigor.bootstrap_ci`, **seed 20260820, 4 000 draws**, resampling unit = ATHLETE |
| win rule | `e5_grapple_like.wins` — interval strictly above 0 AND non-degenerate (`hi > lo`) |
| nulls | `signatures.size_signature`, `signatures.degree_signature` |
| embeddings | `e5_grapple_like.load_embeddings` (`technique_nodes.embedding`, all-mpnet-base-v2, 768-dim) |
| the candidate itself | `scripts.research.e15_systems_similarity._node_jaccard_distance` + `shared_vocabulary` + `PASSES` + the arm-name constants |

**New code in this cell, and only this:** the ingestion-batch assignment (§3), the two
provenance sub-cohorts it builds, the sweep loop, and the runner. No production module is
edited. Nothing is written to the database.

## 3. The annotation-provenance control — E15 §8 item 2

### 3a. The handle that actually exists (measured, not assumed)

`matches` carries: `id, athlete_a_id, athlete_b_id, winner_id, event, year, weight_class,
win_type, stage, submission, sequence, status, created_by, created_at, video_url, timeline,
video_start_seconds, ts_origin`.

* There is **no `bundle_imports` table and no import/dump/batch table of any kind** —
  `information_schema` returns nothing matching `%import%`, `%dump%` or `%batch%`.
* **`created_by` is NULL for all 911** final+sequence rows. It is not a handle.
* `created_at` is. The corpus was built in discrete import runs and the row-insert timestamp
  is the only surviving trace of them.

**Batch := an ingestion SESSION.** Order the gated bouts by `(created_at, id)` — a total
order — and start a new batch whenever the gap to the previous bout exceeds **30 minutes**.
Measured on the 465 gated bouts: **19 sessions**, the largest holding **298**. A plain
calendar-day grouping gives 13 groups with the largest at 315; the two agree on the dominant
mass, so the finer definition is used and the coarser is not run as a second arm.

> `ponytail:` `created_at` is a row-insert timestamp, not a dump-file id. Ceiling — it cannot
> separate two annotators working in one session, and it cannot re-join a bout that was
> re-imported later to its original dump. Upgrade path = a real `source_batch` column written
> by `scripts/dump_import`; until that exists this is the strongest provenance signal the
> schema carries, and saying so is part of the result.

### 3b. Why a batch is a confound at all

`c1` scores a pair by shared technique LABELS. Labels come from transcripts read by a model
or a human in one run. If one run prefers "back take" and another "taking the back", then two
halves of the same athlete ingested in the same run share vocabulary **because of the
annotator**, and `c1` retrieves the annotator, not the game. E15's `p1` probe removed
idiosyncratic labels; it could not remove systematically different choices among the SHARED
labels. That residual is what this section is designed against.

### 3c. The two sub-tests, and the marginals that make them runnable

A half's batch is its **modal** session (the session most of its bouts came from; ties by
lowest session index — a total order). Measured on the primary cohort (chronological, ≥4):

| | chrono ≥4 | odd/even ≥4 | chrono ≥6 | odd/even ≥6 |
|---|---|---|---|---|
| cohort n | 36 | 35 | 14 | 14 |
| **cross-batch** queries (the two halves' modal sessions DIFFER) | **15** | 12 | 5 | 4 |
| within-batch queries | 21 | 23 | 9 | 10 |
| biggest same-session pool | **16** | 18 | 6 | 7 |
| that pool's chance MRR | **0.211** | 0.194 | 0.408 | 0.370 |
| pool purity (share of its athletes' bouts inside the session) | 84% | 79% | 78% | 76% |

> Marginal correction, disclosed: an earlier scratch count of this table read 16 / 20 for the
> primary column because it broke modal-batch ties by `Counter` insertion order. The rule
> registered above and implemented in the runner breaks them by **lowest session index** — a
> total order, failure-archaeology #10 — which moves one athlete from cross to within. Still
> a marginal; no arm had been scored; **both chance floors the gate reads (0.116 and 0.211)
> are unchanged**, and so is the gate rule in §3d.

**(i) Cross-batch subgroup.** Full 36-candidate ranking, MRR read over the 15 queries whose
own two halves came from DIFFERENT ingestion sessions. A shared batch cannot be why those 15
are retrieved. Reported beside the 21 within-batch queries, so "a pair sharing a batch is not
advantaged" is a number and not a claim.

**(ii) Batch-stratified pool.** Restrict the cohort to the 16 athletes whose BOTH halves are
modal to the same single session (measured: all 16 fall in the 07-07 bulk run), and re-rank
inside that pool alone. **Every candidate now shares the target's ingestion batch**, so batch
membership carries zero discriminative power by construction. Chance MRR 0.211.

### 3d. The gate rule — fixed now

**PROVENANCE GATE = PASS** iff BOTH hold:

1. *cross-batch subgroup*: `c1`'s MRR bootstrap interval over those 15 queries has its lower
   bound **strictly above the full cohort's chance floor (0.116)**, and the paired
   Δ(`c1` − `n1`) over those same 15 athletes has a **positive point estimate**;
2. *stratified pool*: `c1`'s MRR interval inside the 16-athlete same-session pool has its
   lower bound **strictly above that pool's chance floor (0.211)**, and the paired
   Δ(`c1` − `n1`) inside the pool has a **positive point estimate**.

**FAIL** otherwise. There is no third state here, deliberately, and the weakness is stated
rather than hidden: at n ≈ 15 a strict "paired interval excludes 0" requirement would be
decided by interval WIDTH rather than by evidence, so the interval test is spent where it
buys the most — on "still above chance with the batch held constant" — and the paired delta
is required only directionally. A gate that cannot be passed is not a control, it is a veto.

**If the gate FAILS, this cell stops.** No wiring plan is written, `c1` is not accepted
whatever its MRR, and the finding is that technique-label overlap cannot be separated from
who annotated the tape on this corpus.

## 4. Metric, thresholds, verdict — decided before the numbers

**Primary metric: MRR**, per-athlete reciprocal ranks retained so every comparison is a
**paired bootstrap over athletes**. **top-1 / top-3 / top-5 reported beside it, never the
criterion** — with 36 queries a hit/miss rate has a far wider paired interval than a
reciprocal rank. Ties rank pessimistically. Chance floor `H_n / n` printed in every table.

**Primary pass: chronological split, ≥4-bout floor** (36 athletes). This is E15's primary and
the reason is unchanged and was written before both runs: odd/even halves share events,
opponents *and annotation batches*, and a descriptor that only recognises the batch is
worthless for a dossier sentence. **Odd/even ≥4 is reported as a sensitivity pass**, together
with both ≥6 passes, so this cell cannot be accused of reporting only the flattering split.

A **win** = the paired Δ interval lies strictly above 0 AND is non-degenerate (`hi > lo`).

| | Verdict rule, fixed before the run |
|---|---|
| **H1** — plain technique-label Jaccard is a better "grapples most like" than what ships and than volume | **ACCEPT** iff, on the PRIMARY pass, `c1` wins against `n1` (H0, size) **and** against `n2` (degree) **and** against `a0` (production), **and** the §3d provenance gate PASSES. Anything less is REJECT. |
| **H0** | `n1`, the three-number size descriptor. E5 measured it out-scoring the shipped method; E15 measured the shipped method at chance. It is the thing to beat. |

* **One hypothesis, conjunctive criterion, plus a gate.** Four intervals must fall the same
  way for an ACCEPT. That is a stricter multiplicity control than any α-correction on a
  single comparison, so no correction is applied — stated, not assumed.
* **An inconclusive interval is reported as inconclusive**, never as evidence of equivalence
  (ADR-03).
* **The sweep (§5) and the two provenance sub-tests beyond the gate carry no verdict.** They
  are readings.

## 5. The shared-vocabulary sweep — E15 §8 item 3

E15 ran ONE threshold (≥3 athletes) post-hoc. This cell runs the sweep and registers the
reading first. Measured marginal on the primary cohort: **170 labels**, of which **88** are
used by ≥2 athletes, **68** by ≥3, **49** by ≥4.

`v2` / `v3` / `v4` = `c1` computed over those restricted vocabularies. Let
Δ_k = paired Δ(`v_k` − `n1`), with Δ_1 = Δ(`c1` − `n1`) the unrestricted point.

**The reading, both branches written now:**

* **STYLE** — Δ_k *wins* (interval strictly above 0) at every k ∈ {2, 3, 4}. The advantage
  survives even when only the vocabulary many athletes share is left, so what `c1` recognises
  is which techniques an athlete uses, not which words their annotator picked.
* **VOCABULARY** — Δ_2 fails to win. The first restriction — dropping the 82 labels only one
  athlete uses — is the one that removes a label which can appear in that athlete's OWN
  intersection and in no cross-athlete intersection at all. If the advantage needs those, it
  is a self-recognition cheat by construction.
* **THIN** — Δ_2 wins but Δ_3 or Δ_4 does not. Reported as decay, and **not** read as
  evidence of a confound: at ≥4 only 49 of 170 labels remain and the half-graphs are a median
  ~10 nodes, so a failure there is as consistent with running out of vocabulary as with
  running out of signal. Which one it is, this corpus cannot say, and the honest output is to
  say that.

Monotonicity (does Δ decay smoothly across k, or fall off a cliff at k = 2) is reported as
the shape of the sweep. It informs the reading; the branch above is what decides it.

## 6. Ranking vs explanation — two decisions, E15 §8 item 4

They are separated now so neither can be smuggled in on the other's evidence.

**D1 — the RANKING.** Order a dossier's analogues by `c1` distance instead of
`aggregate_similarity`. **Requires H1 ACCEPT (which includes the provenance gate).** A
ranking is a claim about who is most similar; it needs a measurement.

**D2 — the EXPLANATION.** The shared-systems presentation already shipped in `fb9823f`
(dossier names the shared systems + up to 4 shared techniques instead of a bare percent).
**Requires nothing from this cell.** It is justified by a fact E15 already settled: on the
primary split the shipped percentage scored MRR 0.113 against a chance floor of 0.116, so the
bare number was not defensible and naming checkable evidence is strictly better than printing
it. D2 is unaffected by any verdict here.

**The asymmetry is deliberate and registered:** D2 may ship without D1 (it already has).
**D1 may never ship without D2** — a re-ordered list with no visible evidence is exactly the
unfalsifiable number that got this series started.

**No user-facing number, either way.** If D1 ships, the dossier shows the ORDER and the
shared-technique evidence. A percentage may return only when some cell has a self-recognition
MRR to print beside it (PoC-E5's standing acceptance condition), and `c1`'s MRR is a
*cohort-level* property, not a per-pair one — it cannot be rendered as "63%" next to a name.

## 7. Stated limits, before the numbers

* **36 queries** on the primary pass; **15** (cross-batch) and **16** (stratified pool) in the provenance sub-tests. Intervals will be
  wide. An effect smaller than roughly ±0.13 (E5/E15's paired width on this cohort) cannot be
  resolved and will be reported as unresolved, not as a tie.
* **The stratified pool is one session.** All 16 of its athletes come from the 07-07 bulk run,
  so the gate tests provenance for THAT run's annotation and no other. A second run large
  enough to stratify does not exist in this corpus.
* **Modal, not pure.** A half's batch is where most of its bouts came from; halves can mix
  sessions. The runner reports the pool's purity (share of pool bouts inside the modal
  session) as a marginal so the reading is not made on an assumed purity.
* **`created_at` is a proxy** for the annotation run, not a record of it (§3a ceiling).
* **Undirected/unordered by construction.** `c1` reads the NODE SET only: it throws away
  direction, edge weight, order and frequency. That it still wins would be a statement about
  how little of the graph is carrying the signal, not a claim that the rest is worthless.
* **Self-recognition is not "grapples like".** Every cell in this series measures whether a
  descriptor can find the same athlete twice. That is a necessary condition for a similarity
  metric and not a sufficient one; nothing here validates that the athlete ranked #2 is
  meaningfully similar to a reader.
* **Corpus drift.** Gate re-measured at **465 of 911**, identical to E15's, so this cell's
  numbers ARE comparable to that one's. They remain NOT byte-comparable to PoC-E5's 466 or
  E8/E9's 429.
* **Nothing here touches production.** No DB write, no replay, no site export, no edit to
  `export/site_data.py` or `analysis/athlete_systems.py`.

## 8. Determinism and data class

**Determinism.** failure-archaeology #10 is this exact output: analogue lists reshuffled on
~43/77 dossiers per export because `PYTHONHASHSEED` broke ties in hub selection and community
ordering. Every ranking, grouping and greedy selection in this cell carries a total order —
batch assignment sorts by `(created_at, id)`, sub-cohorts preserve the cohort's own athlete
order, vocabulary sets are membership-tested and never iterated into an output.
`--digest` hashes every arm's distance matrix plus both sub-cohorts' membership and the three
sweep vocabularies; it is run under three `PYTHONHASHSEED`s and the digests must be
identical. `--self-check` asserts the batch segmentation, the modal-batch rule and the
sub-cohort construction with no DB.

**Data class — athlete corpus only.** Every read is `matches` and `technique_nodes`, both
PUBLIC. This cell **never queries the `graphs` table**, so `owner_kind` cannot leak by
construction: there is no query that could return a user-owned row. No `user_sessions`, no
`profiles`, no app-fed data enters any arm or any distance.
`repository.rated_athlete_graph_ids` is deliberately not applied, for E15's reason: that
filter exists to stop a baseline mixing the Glicko-2 and V1 rating scales inside one
`node_key`, and this cell reads **no rating at all** (`computed_elo` is 0 in every arm, which
zeroes `system_similarity`'s 0.15 ELO term for every pair alike). With no rating read there is
no scale to mix.

## 9. Wiring plan for D1 — written AFTER the verdict, wired by nobody in this cell

**Status.** Not pre-registered; this section is the deliverable §6 promised *conditional on*
H1 ACCEPT + provenance-gate PASS, and both landed. It is a plan, not a change: no file below
is edited by this cell, and the orchestrator owns the decision to build it.

**What it changes: exactly one ordering.** The dossier's analogue LIST — which five athletes
appear under "Grapples most like", and in what order. Nothing else. The presentation those
five rows use (hub + shared techniques, `fb9823f`) is untouched and keeps working, because it
is computed per pair from `match_systems`, independently of what ordered the pairs.

### 9a. The path today (traced, not assumed)

```
export/site_data.py:976    career = _career_graphview(athlete, profile, session, 12, _net())   # TRUNCATED to 12
export/site_data.py:1004   ag = from_career_graphview(athlete.name, career)
export/site_data.py:1005   system_profile = build_system_profile(athlete.name, ag)      → system_profiles[slug]
export/site_data.py:1044   nearest = compare_profiles(sp, all_profiles, k=5)            → profile_dict["analogues"]
  analysis/athlete_systems.py:388  results.sort(key=lambda x: -x["aggregate_similarity"])   ← THE ORDERING
export/site_data.py:2780   profile["_analogues"] = d.get("analogues") or []
export/site_data.py:1826   _ana_row(...) renders name + shared systems + shared techniques (no percent)
```

`analysis/athlete_systems.py:388` is the one line whose output D1 replaces.

### 9b. Two substitutions between the measured arm and production — name them or ship a different metric

| | E16 measured | production today |
|---|---|---|
| node set | the athlete's **untruncated** label set | `_career_graphview(..., limit=12)` — the **12 busiest** |
| substrate | `matches.sequence` → `network_from_sequences` | persisted `graph_edges` via `export_fighter_graph` |

**The truncation is the load-bearing one.** `c1` is a set-overlap score; capping both sides at
12 labels caps `|A∩B|` at 12 and throws away exactly the long tail that distinguishes two
athletes with the same busy nodes. Ordering by Jaccard over the 12-node cut is **not the arm
this cell accepted** and must not be presented as such.

Fix, and it costs no extra query: `_career_graphview` already calls
`export_fighter_graph(athlete, session)` in full and only then calls `_truncate_graph(..., 12)`.
Capture the full id set at that point.

* `export/site_data.py:_career_graphview` — before truncating, keep
  `gv["_all_node_ids"] = sorted(n["id"] for n in <untruncated gv>["nodes"])` (the
  `signature_transitions` fallback branch is already untruncated; set the same key there from
  its own `nodes`). `sorted` is the total order, not incidental dict order.
* `DOSSIER_VERSION` **must go 7 → 8** (`export/site_data.py:898`). The `:c` cache item gains a
  key; a stale hit would silently order some dossiers on the old payload and not others. This
  is the same rule the existing 5→6 comment states ("the cache has to miss, not merely gain a
  key").
* `analysis/athlete_systems.from_career_graphview` — carry `_all_node_ids` onto the returned
  `AthleteGraph` (a plain attribute; `AthleteGraph` is this repo's own dataclass), falling
  back to `graph.nodes.keys()` when absent so every other caller keeps working unchanged.
* `analysis/athlete_systems.AthleteSystemProfile` — one new field
  `labels: frozenset[str] = frozenset()`, filled by `build_system_profile` from that
  attribute. `profile_to_dict` lists its fields explicitly, so the exported `_systems` payload
  does **not** change shape and no site global moves.

The substrate difference (`graph_edges` vs `matches.sequence`) is NOT worth closing: E5
declared it, E15 ran on it, and both are the same athlete's own technique labels. It is a
stated risk, and §9d's check is what bounds it.

### 9c. The ordering itself, and the tiebreak that is not optional

In `compare_profiles`, replace the sort key:

```python
# was: results.sort(key=lambda x: -x["aggregate_similarity"])
results.sort(key=lambda x: (-x["label_overlap"], x["athlete"]))
```

with `label_overlap = jaccard(set(query.labels), set(target.labels))`
(`analysis.constellations.compare.jaccard`, already imported elsewhere in this repo — do not
write a second one).

**The explicit `athlete` tiebreak is load-bearing, more so than it was before.**
`aggregate_similarity` is a weighted sum of four floats and ties almost never; Jaccard is a
ratio of small integers over ~10-node sets and ties constantly. Today's sort has **no**
tiebreaker at all (`athlete_systems.py:388`) and is deterministic only because
`list(system_profiles.values())` happens to preserve insertion order. Swapping in a
tie-heavy key without a total order re-opens failure-archaeology #10 — the 2026-07-07 bug
where these exact analogue lists reshuffled on ~43/77 dossiers per export. Add the tiebreak
in the same diff as the key, never after.

Keep `aggregate_similarity` in the returned payload. It stops being the ORDER; it is still
what `_shared_systems` weights its evidence rows by, and dropping it would change the
presentation this plan promises not to touch.

### 9d. Verification, before anything is pushed

1. `uv run pytest tests/test_athlete_systems.py tests/test_dossier_systems.py` —
   `test_athlete_systems.py` asserts `compare_profiles`' ordering; it will need updating, and
   **the update must be to assert the new order, not to delete the assertion.**
2. **Determinism, the specific regression this reopens.** Export twice in fresh processes
   under different `PYTHONHASHSEED`s and diff the analogue rows across all dossiers.
   Zero differing rows, or the tiebreak is wrong. This is the exact check `8fa5c82` was
   written for; `site-checker` can run it.
3. **Bound the substrate substitution.** For the ~36 cohort athletes, compare the label set
   `export_fighter_graph` yields against the one `matches.sequence` yields and report the
   Jaccard between them. A median well above 0.9 means E16's number transfers; anything lower
   is a finding that belongs in this doc before the swap ships, not after.
4. Full `uv run pytest` + `uv run ruff check .` + `uv run mypy .` **after `uv sync --all-extras`**
   (CI installs every extra; a plain sync resolves optional deps to `Any` and can be green
   locally while CI is red).

### 9e. Regeneration, and what it does NOT touch

Dossier analogue rows are generated output, so the change only reaches readers through a
re-export: `uv run python -m export.site_data` over the whole corpus (~10–12 min, an N+1 and
**not** a hang — failure-archaeology #3, do not kill it), then commit + push `GrapplingArc`
`main`, which is what deploys. The `DOSSIER_VERSION` bump means every dossier misses cache on
that run, so budget the full time, not the incremental.

Nothing here is a replay: no ELO math, no K factor, no score function, no `graph_edges` write.
`athletes.elo`, `elo_series` and every rating artefact are untouched. Nothing here is a
migration: no table, column, RLS policy or view changes. Nothing here reads a `graphs` row of
`owner_kind='user'`.

### 9f. No user-facing number — the constraint that survives the ACCEPT

`c1` may set the ORDER and nothing else. **No Jaccard percentage is rendered**, on the chip,
the row, a tooltip or a data attribute. The reason is not squeamishness: 0.366 is a
*cohort-level* self-recognition MRR, a property of the ranker over 36 athletes, and there is
no measurement that licenses printing a per-pair `|A∩B|/|A∪B|` as "63% similar". The evidence
a reader gets stays what `fb9823f` already ships — the named shared systems and the shared
technique list, which is checkable. PoC-E5's standing condition is unchanged: a percentage
returns only when some cell has a number that justifies *that* number.

### 9g. What would invalidate this plan

* **Truncation shipped by accident.** If `_all_node_ids` is skipped and the sort runs on the
  12-node profile, the shipped metric is not the accepted one. That is the single most likely
  way this goes wrong quietly.
* **The substrate check (§9d.3) comes back low.** Then E16's number was measured on a
  different node set than production would rank on, and the swap waits.
* **The residual in the Reading.** `c1` scores 0.433 on within-batch queries against 0.273 on
  cross-batch ones while the size null sits flat (0.196 / 0.203). The gate establishes that
  annotation provenance is not the WHOLE effect; it does not establish the share. If the
  corpus ever gains a real `source_batch` column (§3a), re-run this cell before trusting the
  ordering further than "better than what shipped, which was at chance".

---

<!-- E16:RESULTS -->

## Results

Generated by `uv run python -m scripts.research.e16_technique_jaccard` — **do not hand-edit**; a re-run replaces this block. Script: `scripts/research/e16_technique_jaccard.py`.

**Corpus gate:** 465 gated bouts of 911 final+sequence (310 under 4 events, 136 dropped as one-sided) — matching PoC-E15's published 465, so this cell's numbers ARE comparable to that one's

**Embeddings:** 217 of 253 cohort labels carry a `technique_nodes.embedding` (86%); the mpnet arms run

**Ingestion batches:** 19 ingestion sessions over the gated corpus (`created_at`, new session after a 30-minute gap; largest holds 298 of 465 bouts). `matches` carries no import/dump/batch table and `created_by` is NULL for every row, so this is the only provenance handle in the schema. On the primary cohort: 15 athletes' halves span different sessions, 21 do not; the largest same-session pool is session 9 with 16 athletes (84% of their bouts actually inside it)

### PRIMARY — chronological split, ≥4-bout floor

Cohort: **36 athletes**. Split: `chronological`, ≥4-bout floor. Median half-graph: 10 nodes / 12 edges (≥3 nodes / ≥2 edges gated). Chance MRR: 0.116. Vocabulary: ≥2 athletes → 88, ≥3 athletes → 68, ≥4 athletes → 49 of 170 labels.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| c1 node Jaccard, whole graph (no systems) | **H1 (candidate)** | 0.366 [0.246, 0.489] | 22% | 44% | 47% | +0.168 [+0.046, +0.291] | +0.190 [+0.031, +0.340] | +0.254 [+0.141, +0.370] |
| n1 null: size only (H0) | null (H0) | 0.199 [0.131, 0.280] | 6% | 19% | 33% | — | — | — |
| n2 null: degree histogram | null | 0.176 [0.100, 0.271] | 8% | 14% | 19% | — | — | — |
| a0 production method (athlete_systems, 12-node) | reference | 0.113 [0.082, 0.148] | 0% | 6% | 19% | -0.086 [-0.168, -0.012] | — | — |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.304 [0.199, 0.415] | 14% | 39% | 44% | +0.105 [-0.006, +0.222] | — | — |
| v2 c1 over labels used by ≥2 cohort athletes | sweep (no verdict) | 0.363 [0.244, 0.484] | 22% | 42% | 50% | +0.164 [+0.042, +0.286] | — | — |
| v3 c1 over labels used by ≥3 cohort athletes | sweep (no verdict) | 0.339 [0.225, 0.458] | 19% | 39% | 53% | +0.141 [+0.028, +0.256] | — | — |
| v4 c1 over labels used by ≥4 cohort athletes | sweep (no verdict) | 0.329 [0.216, 0.448] | 19% | 36% | 50% | +0.130 [+0.014, +0.249] | — | — |
| _chance (random ranking)_ | — | 0.116 | 3% | 8% | 14% | — | — | — |

## Provenance control (§3) — the gate

### Provenance A — WITHIN-batch queries (same ingestion session both halves)

Cohort: **21 athletes**. Split: `chronological`, ≥4-bout floor. Median half-graph: 10 nodes / 12 edges (≥3 nodes / ≥2 edges gated). Chance MRR: 0.116.

Same ranking and same candidate pool as the primary pass; only the queries the mean is taken over change. These are the pairs a batch confound COULD advantage.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| c1 node Jaccard, whole graph (no systems) | **H1 (candidate)** | 0.433 [0.257, 0.620] | 33% | 43% | 43% | +0.237 [+0.077, +0.417] | +0.291 [+0.068, +0.511] | +0.341 [+0.177, +0.517] |
| n1 null: size only (H0) | null (H0) | 0.196 [0.119, 0.298] | 5% | 14% | 38% | — | — | — |
| n2 null: degree histogram | null | 0.142 [0.070, 0.247] | 5% | 10% | 10% | — | — | — |
| a0 production method (athlete_systems, 12-node) | reference | 0.092 [0.067, 0.123] | 0% | 0% | 14% | -0.103 [-0.213, -0.019] | — | — |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.345 [0.196, 0.509] | 19% | 43% | 43% | +0.149 [+0.004, +0.307] | — | — |
| v2 c1 over labels used by ≥2 cohort athletes | sweep (no verdict) | 0.432 [0.258, 0.618] | 33% | 43% | 48% | +0.236 [+0.076, +0.415] | — | — |
| v3 c1 over labels used by ≥3 cohort athletes | sweep (no verdict) | 0.398 [0.233, 0.578] | 29% | 43% | 52% | +0.202 [+0.056, +0.366] | — | — |
| v4 c1 over labels used by ≥4 cohort athletes | sweep (no verdict) | 0.387 [0.221, 0.568] | 29% | 38% | 48% | +0.191 [+0.045, +0.353] | — | — |
| _chance (random ranking)_ | — | 0.116 | 5% | 14% | 24% | — | — | — |

### Provenance B — CROSS-batch queries (halves from different sessions)

Cohort: **15 athletes**. Split: `chronological`, ≥4-bout floor. Median half-graph: 10 nodes / 12 edges (≥3 nodes / ≥2 edges gated). Chance MRR: 0.116.

Same ranking and same candidate pool. A shared ingestion batch cannot be why these are retrieved. Gate leg (i).

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| c1 node Jaccard, whole graph (no systems) | **H1 (candidate)** | 0.273 [0.150, 0.418] | 7% | 47% | 53% | +0.070 [-0.089, +0.227] | +0.049 [-0.155, +0.235] | +0.132 [+0.039, +0.275] |
| n1 null: size only (H0) | null (H0) | 0.203 [0.099, 0.350] | 7% | 27% | 27% | — | — | — |
| n2 null: degree histogram | null | 0.224 [0.087, 0.404] | 13% | 20% | 33% | — | — | — |
| a0 production method (athlete_systems, 12-node) | reference | 0.141 [0.083, 0.210] | 0% | 13% | 27% | -0.062 [-0.199, +0.062] | — | — |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.247 [0.126, 0.390] | 7% | 33% | 47% | +0.044 [-0.112, +0.217] | — | — |
| v2 c1 over labels used by ≥2 cohort athletes | sweep (no verdict) | 0.266 [0.144, 0.411] | 7% | 40% | 53% | +0.063 [-0.094, +0.220] | — | — |
| v3 c1 over labels used by ≥3 cohort athletes | sweep (no verdict) | 0.258 [0.136, 0.403] | 7% | 33% | 53% | +0.055 [-0.100, +0.208] | — | — |
| v4 c1 over labels used by ≥4 cohort athletes | sweep (no verdict) | 0.247 [0.134, 0.389] | 7% | 33% | 53% | +0.045 [-0.129, +0.211] | — | — |
| _chance (random ranking)_ | — | 0.116 | 7% | 20% | 33% | — | — | — |

### Provenance C — batch-stratified pool (every candidate from ONE session)

Cohort: **16 athletes**. Split: `chronological`, ≥4-bout floor. Median half-graph: 11 nodes / 13 edges (≥3 nodes / ≥2 edges gated). Chance MRR: 0.211.

Re-ranked inside the pool alone, so batch membership carries zero discriminative power by construction. Session 9; 84% of these athletes' bouts sit inside it. Gate leg (ii).

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| c1 node Jaccard, whole graph (no systems) | **H1 (candidate)** | 0.526 [0.322, 0.739] | 44% | 44% | 62% | +0.275 [+0.087, +0.477] | +0.271 [+0.019, +0.508] | +0.355 [+0.163, +0.563] |
| n1 null: size only (H0) | null (H0) | 0.251 [0.171, 0.331] | 0% | 38% | 56% | — | — | — |
| n2 null: degree histogram | null | 0.255 [0.153, 0.386] | 6% | 31% | 44% | — | — | — |
| a0 production method (athlete_systems, 12-node) | reference | 0.170 [0.120, 0.233] | 0% | 19% | 25% | -0.080 [-0.166, +0.009] | — | — |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.484 [0.291, 0.692] | 38% | 44% | 56% | +0.233 [+0.055, +0.426] | — | — |
| _chance (random ranking)_ | — | 0.211 | 6% | 19% | 31% | — | — | — |

### Sensitivity — odd/even split, ≥4-bout floor (PoC-E5's primary split)

Cohort: **35 athletes**. Split: `odd_even`, ≥4-bout floor. Median half-graph: 11 nodes / 11 edges (≥3 nodes / ≥2 edges gated). Chance MRR: 0.118. Vocabulary: ≥2 athletes → 89, ≥3 athletes → 64, ≥4 athletes → 49 of 168 labels.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| c1 node Jaccard, whole graph (no systems) | **H1 (candidate)** | 0.501 [0.364, 0.641] | 40% | 51% | 57% | +0.269 [+0.118, +0.419] | +0.306 [+0.116, +0.485] | +0.253 [+0.119, +0.389] |
| n1 null: size only (H0) | null (H0) | 0.232 [0.151, 0.327] | 9% | 23% | 40% | — | — | — |
| n2 null: degree histogram | null | 0.195 [0.109, 0.299] | 11% | 17% | 23% | — | — | — |
| a0 production method (athlete_systems, 12-node) | reference | 0.248 [0.148, 0.368] | 14% | 23% | 31% | +0.016 [-0.110, +0.151] | — | — |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.371 [0.250, 0.503] | 26% | 40% | 49% | +0.139 [-0.010, +0.291] | — | — |
| v2 c1 over labels used by ≥2 cohort athletes | sweep (no verdict) | 0.448 [0.318, 0.581] | 31% | 51% | 54% | +0.216 [+0.076, +0.357] | — | — |
| v3 c1 over labels used by ≥3 cohort athletes | sweep (no verdict) | 0.442 [0.308, 0.584] | 34% | 43% | 49% | +0.210 [+0.067, +0.360] | — | — |
| v4 c1 over labels used by ≥4 cohort athletes | sweep (no verdict) | 0.431 [0.298, 0.571] | 31% | 43% | 49% | +0.199 [+0.058, +0.342] | — | — |
| _chance (random ranking)_ | — | 0.118 | 3% | 9% | 14% | — | — | — |

### Sensitivity — chronological split, ≥6-bout floor

Cohort: **14 athletes**. Split: `chronological`, ≥6-bout floor. Median half-graph: 16 nodes / 19 edges (≥3 nodes / ≥2 edges gated). Chance MRR: 0.232. Vocabulary: ≥2 athletes → 57, ≥3 athletes → 40, ≥4 athletes → 29 of 134 labels.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| c1 node Jaccard, whole graph (no systems) | **H1 (candidate)** | 0.717 [0.533, 0.893] | 57% | 79% | 86% | +0.245 [+0.031, +0.455] | +0.423 [+0.121, +0.692] | +0.448 [+0.262, +0.635] |
| n1 null: size only (H0) | null (H0) | 0.472 [0.295, 0.660] | 29% | 57% | 71% | — | — | — |
| n2 null: degree histogram | null | 0.294 [0.160, 0.466] | 14% | 21% | 57% | — | — | — |
| a0 production method (athlete_systems, 12-node) | reference | 0.270 [0.170, 0.411] | 7% | 29% | 57% | -0.203 [-0.392, -0.033] | — | — |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.481 [0.276, 0.699] | 36% | 43% | 57% | +0.009 [-0.177, +0.203] | — | — |
| v2 c1 over labels used by ≥2 cohort athletes | sweep (no verdict) | 0.675 [0.496, 0.853] | 50% | 79% | 93% | +0.202 [+0.004, +0.387] | — | — |
| v3 c1 over labels used by ≥3 cohort athletes | sweep (no verdict) | 0.704 [0.512, 0.882] | 57% | 79% | 86% | +0.231 [-0.001, +0.453] | — | — |
| v4 c1 over labels used by ≥4 cohort athletes | sweep (no verdict) | 0.692 [0.490, 0.882] | 57% | 79% | 86% | +0.219 [-0.021, +0.465] | — | — |
| _chance (random ranking)_ | — | 0.232 | 7% | 21% | 36% | — | — | — |

### Sensitivity — odd/even split, ≥6-bout floor

Cohort: **14 athletes**. Split: `odd_even`, ≥6-bout floor. Median half-graph: 14 nodes / 18 edges (≥3 nodes / ≥2 edges gated). Chance MRR: 0.232. Vocabulary: ≥2 athletes → 57, ≥3 athletes → 40, ≥4 athletes → 29 of 134 labels.

| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) | Δ vs degree | Δ vs production |
|---|---|---|---|---|---|---|---|---|
| c1 node Jaccard, whole graph (no systems) | **H1 (candidate)** | 0.829 [0.645, 1.000] | 79% | 86% | 86% | +0.415 [+0.168, +0.646] | +0.484 [+0.155, +0.762] | +0.441 [+0.261, +0.624] |
| n1 null: size only (H0) | null (H0) | 0.413 [0.254, 0.602] | 21% | 50% | 71% | — | — | — |
| n2 null: degree histogram | null | 0.345 [0.179, 0.538] | 21% | 36% | 57% | — | — | — |
| a0 production method (athlete_systems, 12-node) | reference | 0.387 [0.219, 0.583] | 21% | 43% | 64% | -0.026 [-0.266, +0.189] | — | — |
| r1 mpnet centroid, occurrence-weighted (flat) | reference | 0.659 [0.450, 0.851] | 57% | 71% | 79% | +0.246 [-0.006, +0.482] | — | — |
| v2 c1 over labels used by ≥2 cohort athletes | sweep (no verdict) | 0.668 [0.467, 0.870] | 57% | 71% | 86% | +0.255 [-0.004, +0.511] | — | — |
| v3 c1 over labels used by ≥3 cohort athletes | sweep (no verdict) | 0.592 [0.405, 0.786] | 43% | 64% | 86% | +0.179 [-0.059, +0.420] | — | — |
| v4 c1 over labels used by ≥4 cohort athletes | sweep (no verdict) | 0.503 [0.334, 0.687] | 29% | 71% | 79% | +0.089 [-0.162, +0.340] | — | — |
| _chance (random ranking)_ | — | 0.232 | 7% | 21% | 36% | — | — | — |

## Reading

**H1, the candidate.** Over 36 candidates on the chronological split, plain technique-label Jaccard scores MRR 0.366 [0.246, 0.489] (top-3 44%) against a chance floor of 0.116, the three-number size descriptor's 0.199 and the shipped ranking's 0.113. Paired: Δ vs H0/size +0.168 [+0.046, +0.291], Δ vs degree +0.190 [+0.031, +0.340], Δ vs production +0.254 [+0.141, +0.370]. Those three intervals are the whole of the pre-registered metric criterion.
**Against the embedding reference.** The 768-dim occurrence-weighted mpnet centroid scores 0.304; the set-intersection arm that ignores direction, weight, order and frequency scores 0.366, paired Δ +0.062 [-0.029, +0.158]. `r1` carries no verdict (it is PoC-E5's arm re-run on nearly the same data), so this is context, not a win.
**The provenance control, which is what this cell was commissioned for.** Split by whether an athlete's two halves came from the SAME ingestion session: within-batch queries (n=21) score 0.433 [0.257, 0.620], cross-batch queries (n=15) score 0.273 [0.150, 0.418], both against the same 36-candidate pool and the same chance floor 0.116. Inside the batch-stratified pool — 16 athletes who all share one ingestion session, so batch membership cannot discriminate at all — `c1` scores 0.526 [0.322, 0.739] against that pool's chance floor 0.211. **Gate: PASS** — cross-batch subgroup (n=15): c1 MRR 0.273 [0.150, 0.418] vs chance 0.116 → above; Δ vs size +0.070 [-0.089, +0.227] → positive · batch-stratified pool (n=16): c1 MRR 0.526 [0.322, 0.739] vs chance 0.211 → above; Δ vs size +0.275 [+0.087, +0.477] → positive.
That is the one thing E15 could not do. It removes the mechanism, not just the idiosyncratic labels: `p1` there stripped rare words, this holds the annotation run itself constant across every candidate.
**The residual the gate does NOT clear, stated rather than left in the table.** `c1` scores 0.433 on within-batch queries and 0.273 on cross-batch ones, while the size null barely moves between the same two subgroups (0.196 → 0.203). The nulls do not care which session a pair came from; the label-based arms do. That is consistent with PART of `c1`'s edge being shared annotation vocabulary — and it is equally consistent with cross-batch athletes simply being harder (their two halves are further apart in career time, which is what put them in different import runs). This subgroup contrast is DESCRIPTIVE: §3d registered the gate, not a two-sample test between subgroups, and with 15 vs 21 athletes no such test would resolve a gap this size. What the gate does establish is the harder half of the question: with the batch held constant for every candidate (leg ii), `c1` still beats the size null by an interval that excludes 0, so annotation provenance cannot be the WHOLE effect. Separating the remaining share needs the `source_batch` column §3a names, not more statistics on this corpus.
**The shared-vocabulary sweep, read as §5 registered it — STYLE.** the advantage survives at every threshold, so what `c1` recognises is which techniques an athlete uses, not which words their annotator picked — ≥2 (88/170 labels) +0.164 [+0.042, +0.286]; ≥3 (68/170 labels) +0.141 [+0.028, +0.256]; ≥4 (49/170 labels) +0.130 [+0.014, +0.249].
Two cross-checks worth printing because they cost nothing: `c1`'s primary MRR here reproduces PoC-E15's to three decimals on the same 465-bout gate (same arm, same cohort, independently re-run), and the sweep's own `≥3` point reproduces E15's post-hoc `p1`. The corpus has not moved and neither has the arm.

### Verdicts

1. **PROVENANCE GATE (§3d) — a pair sharing an ingestion batch must not be advantaged** — PASS — cross-batch subgroup (n=15): c1 MRR 0.273 [0.150, 0.418] vs chance 0.116 → above; Δ vs size +0.070 [-0.089, +0.227] → positive · batch-stratified pool (n=16): c1 MRR 0.526 [0.322, 0.739] vs chance 0.211 → above; Δ vs size +0.275 [+0.087, +0.477] → positive
2. **H1 — plain technique-label Jaccard beats volume, degree and the shipped ranking** — ACCEPT — MRR 0.366 [0.246, 0.489] vs chance 0.116; Δ vs H0/size +0.168 [+0.046, +0.291], Δ vs degree +0.190 [+0.031, +0.340], Δ vs production +0.254 [+0.141, +0.370]
3. **shared-vocabulary sweep (§5, reading, no verdict)** — STYLE — the advantage survives at every threshold, so what `c1` recognises is which techniques an athlete uses, not which words their annotator picked — ≥2 (88/170 labels) +0.164 [+0.042, +0.286]; ≥3 (68/170 labels) +0.141 [+0.028, +0.256]; ≥4 (49/170 labels) +0.130 [+0.014, +0.249]
4. **D1 — RANKING: order dossier analogues by c1** — WIRE — H1 accepted and the provenance gate passed; the plan is in the results block
5. **D2 — EXPLANATION: the shared-systems dossier line (fb9823f)** — UNAFFECTED — it needs no arm to win (§6); it already shipped and stands either way

Nothing in this cell touches production: no DB write, no replay, no site export, no edit to `export/site_data.py` or `analysis/athlete_systems.py`.

<!-- /E16:RESULTS -->

## §9d.3 — transfer check (2026-09-11, orquestrador)
Mediana do Jaccard entre o conjunto de rótulos de `export_fighter_graph` e o de `matches.sequence` (carreira inteira) na coorte primária (36 atletas): **0,924** (média 0,900; faixa 0,714–1,000). Passa o critério "> 0,9". Fiado em `analysis/athlete_systems.py` (ordem `(-label_overlap, athlete)`), `DOSSIER_VERSION` 7→8.
