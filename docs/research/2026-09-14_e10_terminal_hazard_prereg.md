# PoC-E10 — a time-inhomogeneous POINTS terminal

**STATUS: PRE-REGISTRATION ONLY. No E10 held-out number existed when this file was written.**
`scripts/research/e10_terminal_hazard.py` re-emits every line of this file verbatim above its
results, so the criterion travels with the numbers it judged. The corpus *shape* below (bout
counts, duration quantiles, ts-integrity counts) was measured read-only before writing, exactly
as PoC-E9 measured its own shape before registering; **no model, no loss and no held-out
comparison was computed.**

Backlog cell: `poc-e10-terminal-hazard`. Parent: `docs/research/poc/e9.md` arm 5 (T1/T2/T3).
Literature: Lamas 2024 (doi:10.1177/17479541231210979 — the one peer-reviewed BJJ Markov
paper, already cited in `analysis/network_metrics.py`); the semi-Markov critique
(Sci Rep 2026, s41598-026-52938-1) that E9 arm 5 was built to test; van Houwelingen 2007
(doi:10.1111/j.1467-9469.2006.00529.x, *Dynamic prediction by landmarking*) and Nicolaie &
van Houwelingen 2013 for landmarking under **competing risks**, which is the evaluation
design used here; piecewise-constant cause-specific hazards as the estimator (standard
competing-risks form; arXiv:2408.03602 for why an *estimated* cut point needs its own
penalty — this cell does not estimate one, it takes E9's).

---

## 1. What is being tested, and what is NOT

E9 arm 5 measured two things and they point opposite ways:

* **T1 — null.** Which terminal a bout absorbs into is **not** predictable from the last 1–3
  states (`M1 − M0 = +0.0426 [−0.0245, +0.1042]`, 107 eval bouts). Memory does not help.
* **T3 — large.** The two cause-specific hazards **cross over on the clock**: in the first two
  minutes submission outruns points (3.4% vs 1.6%); past ten minutes points outruns submission
  hard (66.7% vs 29.0%). Both causes share the same denominator in every bin.

Production `analysis/path_to_victory.py` has **one** absorbing terminal and **no clock**:
`_terminal_rate` returns a non-zero absorption probability only for nodes typed `submission`
(their observed success rate), and `path_to_victory` iterates a single time-invariant value
`v(n)`. A bout at minute 1 and the same bout at minute 12 are valued identically.

**The hypothesis.** Elapsed time carries held-out information about *which* terminal a bout
reaches, beyond what a time-homogeneous competing-risks chain carries — enough to justify
giving `path_to_victory` a second, clock-dependent terminal.

**What this cell deliberately does NOT do** (`ponytail`): it does not build a time-indexed PtV,
does not touch `analysis/path_to_victory.py`, does not re-fit γ or the shaping weights, and does
not run a replay. It tests the ONE empirical premise every such rework rests on, with the
smallest object that can falsify it. If the premise fails, the rework is never designed. If it
passes, §8 names the exact place the hazard would enter and what it would move.

## 2. Corpus, gate, integrity filter, split

* Source: `matches` where `status='final' AND sequence IS NOT NULL`, loaded through
  **`analysis.poc.e9_markov.load_corpus`** — the same function, not a restatement, so the gate
  cannot drift from E9's.
* Gate: `attribution.bout_flags(...)["perspective_reliable"]` AND ≥ 4 events.
  **The corpus has moved since E9 published.** Measured read-only 2026-09-14: **911** matches
  (E9: 864), **465** gated (E9: 429). E9's `verify_gate` asserts 429 and would now fail; this
  cell therefore asserts nothing against 429 and reports both counts. Every number here is about
  a **different, larger corpus** than `e9.md`'s and is not directly comparable to it.
* Chain: **C-bout** — actor-free. A terminal is a property of the bout, not of one fighter
  (E9's own reason for using only C-bout in arm 5). The `perspective_reliable` gate is therefore
  not logically required here; it is kept anyway so the corpus is E9's, and the un-gated
  sensitivity is **deliberately not run** — comparability with the parent cell is worth more
  than ~30% more bouts on a criterion whose effect size T3 already showed to be large.
* Terminal alphabet (`analysis.poc.e9_markov.BoutRow.terminal`, unchanged):
  `END/submission` ← `SUBMISSION`; `END/points` ← `DECISION ∪ POINTS`; `END/draw` ← `DRAW`.
  `win_type` NULL → dropped, never defaulted. **Draws are dropped from the primary** (the
  criterion is "submission vs points") and counted; a sensitivity merges them into points.
* Clock: `elapsed = ts − min(ts)` **within the bout** — AA-010 safe, invariant to an unknown
  `ts_origin`, no missing `ts` ever defaulted. A bout is used only if **every** event carries a
  `ts`. Measured: **417 of 465** gated bouts qualify.
* **ts-integrity filter, registered here because it is new.** Measured span quantiles over those
  417 bouts: p50 480 s, p75 755 s, p90 1153 s, p95 1491 s, p97.5 2510 s, **p99 19 527 s**, max
  **22 841 s**. A grappling bout is not six hours long; the tail is a timestamp defect, not a
  long match. Bouts with span **> 2400 s (40 min)** are excluded as a ts-integrity violation —
  **12 bouts**, counted in the report. 2400 s is chosen as a rule-based ceiling (ADCC's longest
  format is 20 min regulation plus overtime; 40 min doubles it) and **not** by looking at any
  loss. A no-cap sensitivity is reported. Four bouts carry non-monotonic `ts`; their events are
  sorted by `ts` for the clock and the count is reported.
* Split: **temporal only (ADR-03)** — `e9_markov.split_rows`, the most recent 25% of gated bouts
  by `(year, created_at, id)`, boundary key to TRAIN, never random. This is a **single temporal
  holdout**, which is what E9 used; it is stated as such and is not a rolling walk-forward.
* LGPD: athlete corpus only (`matches`). No `graphs` read, so `owner_kind` cannot leak by
  construction. E9 used **no scouting corpus** in arm 5, so neither does this cell.
* Read-only. No write, no replay, no export, no production file touched.

## 3. The two models

Both are discrete-time competing-risks chains on a **Δ = 30 s** grid, horizon 2400 s
(= the integrity cap, so the grid and the filter agree). Cell *j* covers `[jΔ, (j+1)Δ)`. A bout
with duration *T* is at risk in cells `0 … ⌊T/Δ⌋` and absorbs into its observed cause in cell
`⌊T/Δ⌋`. Cause-specific hazards are estimated per **piece** by pooling cells:

    ĥ_τ(piece) = (events of cause τ in piece + ½) / (at-risk cell-observations in piece + 1)

(Jeffreys smoothing; with thousands of cell-observations per piece it is a guard against a
zero cell, not a fit.)

* **M_hom — one piece.** A single `(ĥ_points, ĥ_sub)` for all time. This is the *fair* homogeneous
  comparator: it is what production's chain becomes if a points terminal is added **without** the
  clock, so the contrast isolates the time-inhomogeneity and nothing else. Registered identity,
  asserted by test: with one piece, `P(points | T > t) = ĥ_p/(ĥ_p+ĥ_s)` for **every** *t*, which
  is exactly the train marginal over terminal types. Any time-homogeneous competing-risks chain
  is the train marginal on this criterion; there is nothing weaker to compare against and nothing
  stronger hiding inside "homogeneous".
* **M_piece — two pieces, cut at 600 s.** `(ĥ_p1, ĥ_s1)` for cells starting before 600 s,
  `(ĥ_p2, ĥ_s2)` from 600 s on. **The cut is E9's own bin boundary and is fixed before any E10
  number exists** — it is not estimated, not tuned, not chosen by a loss. One extra pair of
  parameters, nothing else.

Prediction, both models identically:

    P(points | T > t) = Σ_{j ≥ t/Δ} S_j · ĥ_p(j)  ÷  Σ_{j ≥ t/Δ} S_j · (ĥ_p(j) + ĥ_s(j))

with `S_j` the survival within the forward recursion from the landmark. Dividing by the total
absorbed mass renormalises the probability that survives past the horizon proportionally across
the two causes; that is the only horizon convention and it is the same for both models.

## 4. Primary criterion — held-out terminal type at landmarks

Landmarking (van Houwelingen 2007; competing-risks form, Nicolaie & van Houwelingen 2013),
because it is the only design under which the two models can differ at all: integrated from
t = 0 both models return a marginal, so a prospective "at the first bell" comparison is a null
**by construction** and would be a vacuous test.

* Landmarks `L = {120, 240, 360, 480, 600, 720} s` — multiples of Δ, fixed in advance.
* Eval bout *i* contributes at landmark *t* iff `T_i > t` ("still running"). A bout with no
  contributing landmark (`T_i ≤ 120`) is dropped from the primary and **counted**.
* `loss_i(M) = mean over its contributing landmarks of −log p_M(y_i | T > t)`, with
  `y_i ∈ {points, submission}`. **One value per bout**, so the bootstrap over bouts IS the
  cluster bootstrap and no bout is counted twice.
* `Δ_i = loss_i(M_hom) − loss_i(M_piece)`. Positive ⇒ the clock helps.
* Interval: `stats_rigor.bootstrap_ci(deltas, mean, n_boot=5000, seed=20260820)`.
* **Brier** is reported with the identical structure, as a second, proper, bounded score.

### PASS / FAIL, and nothing else decides it

**PASS** iff **all four** hold:

1. mean `Δ_logloss > 0`;
2. its 95% percentile CI **excludes 0**;
3. `hi > lo` — a degenerate interval is never a win (E9 amendment 1: a zero-width interval is
   the signature of two models differing by a constant offset, not by what they predict);
4. the **permutation null** (§5) returns `p ≤ 0.05` (see amendment A1 — this condition was
   written as "covers 0" and was WRONG; it was corrected from a synthetic fixture, before any
   database number existed).

Anything else is **FAIL**. Brier agreeing or disagreeing is a reading, not a vote.

## 5. Registered null control

The harness is structurally asymmetric — one model may use *t*, the other may not — so it must be
shown that the asymmetry alone wins nothing. Terminal labels are permuted **jointly across train
and eval** (seed 20260914, 200 draws; durations, the temporal split and the landmark structure
all untouched), both models are **re-fitted** on the permuted train labels, and the primary Δ is
recomputed. Under permutation the clock carries no information about the type, so the extra pair
of parameters can only buy noise.

**Criterion:** `p = (1 + #{draws with Δ_perm ≥ Δ_obs}) / (1 + n_draws) ≤ 0.05`.
**This is a PASS condition, not a reading.**

### Amendment A1 — the null control, corrected before any database number

Written first as "the permutation interval must cover 0", and **refuted by the synthetic fixture
in `scripts/research/e10_terminal_hazard.py:self_check`, before the runner was pointed at the
database** — the same discipline as PoC-E9's amendments 1 and 2, which were also found by
synthetic corpora with a known answer.

On a planted corpus where the clock IS the terminal, permuting only the **eval** labels returned
`Δ = −0.352 [−0.407, −0.284]` — far from covering 0, and **correctly so**: a model fitted on true
time structure and then scored against random labels must lose to a model that only ever predicts
the marginal. "Covers 0" would therefore have failed a working harness. Two things are fixed:

1. The permutation is **joint over train and eval with a re-fit**, which is the null the control
   is actually for — "if terminal type were unrelated to duration, could this harness still
   produce a positive Δ?" — rather than "what happens to a good model given random labels".
2. The criterion is a **one-sided permutation p-value**, not an interval. Δ_perm being *negative*
   is the expected sign and is not evidence of anything; what must be shown is that Δ_obs sits in
   the upper tail of the permutation distribution.

The amendment **tightens** the criterion (a p-value in the upper tail is strictly harder than an
interval covering 0, which a small negative Δ_perm would have satisfied by accident). It relaxes
nothing. The permutation Δ distribution is reported whatever the verdict.

## 6. Readings — reported, never a criterion

1. **Cut sweep** `∈ {300, 420, 600, 780, 900} s`, primary Δ at each. If the pre-registered 600 s
   is not distinguished, the finding is "duration matters", not "ten minutes matters", and §8
   must say so.
2. **Draw merged into points** ("went the distance"), re-run end to end.
3. **No integrity cap** (`MAX_SPAN = ∞`), re-run end to end.
4. **Secondary — time-to-terminal.** Held-out log-likelihood of the observed `(duration, cause)`
   pair, per bout, M_hom vs M_piece, same bootstrap. **Explicitly non-decisive**, because its
   target *is* the artefact of §7.1: the quantity being predicted is the time of the last
   *recorded* event, not the bell.
5. **Calibration at each landmark** — predicted mean `P(points)` against the observed eval rate,
   with a Wilson interval on the observed rate. A production change needs the number to be right,
   not only better ordered.
6. Held-out **per-landmark** loss for both models, so a win concentrated in one landmark cannot
   hide inside the per-bout average.

## 7. Threats that bound every number here — stated before the numbers

1. **`T` is the last RECORDED event, not the final bell** (E9 says so of its own T3). If a
   submission's last event coincides with the bout ending while a decision's last recorded event
   falls well before the bell, the "still running at *t*" indicator is contaminated *in the
   direction of the finding*. **Pre-registered reading:** a bout that reaches the time limit must
   end on points/decision/draw **by rule**, so the *sign* of the effect has an external mechanism
   that the recording artefact cannot manufacture; the artefact can inflate the **magnitude** and
   shift **where** the crossover sits. Therefore a PASS licenses "the clock carries terminal-type
   information", and does **not** license shipping `ĥ_p2` as a calibrated production constant.
2. **The >10 min bin is open-ended.** E9 flagged that its hazard is inflated by construction. The
   primary criterion is a *conditional distribution over the two causes*, which is invariant to
   that inflation (it scales both causes' mass in the same cells); the secondary time-to-terminal
   criterion is **not**, which is the second reason it cannot decide anything.
3. **Direction is anticipated.** T3 already measured a large association on the whole gated
   corpus. E10 is therefore a *confirmatory* test of whether that association survives a strict
   temporal holdout with a paired interval and a permutation control — it is not a discovery, and
   the report must not present a PASS as one.
4. **107 → ~100 eval bouts is thin.** Every interval here says so.
5. `successful` is present on ~29% of corpus events — irrelevant to this cell (no `successful` is
   read), noted so the absence is deliberate.
6. `ts` present on ~95% of events, `ts_origin` NULL on most matches — which is exactly why only
   within-bout differences are used and why 48 gated bouts are excluded rather than patched.

## 8. What changes in production

**Nothing, unless PASS.** On FAIL the cell closes the backlog item: `path_to_victory` keeps one
terminal and no clock, and the E9 crossover stays a descriptive finding.

On **PASS**, the change and its one blocker, named in advance so a PASS cannot be read as a
licence to improvise:

* **Where.** `analysis/path_to_victory.py`. `_terminal_rate(g, n)` is a *node* property; a points
  terminal is a *clock* property and cannot live in that function. The minimal shape is one new
  keyword on `path_to_victory(g, ..., points_hazard: float = 0.0)`, folded into the existing
  `stay` term — `stay = max(0, 1 − p_r − p_k − p_t − points_hazard)` — with production calling it
  twice, `points_hazard=ĥ_p1` (early) and `ĥ_p2` (late). Two scalars, one keyword, no new module,
  no time index on the returned dict.
* **The blocker — a points terminal has no signed value in this model.** PtV is one-sided
  ("value to the actor") and carries no score state, so it cannot know whether time expiring is a
  win or a loss. Inventing a sign would be a score model smuggled in as a constant. The only
  honest minimal form is an **absorption with value 0**: it truncates the discounted future
  without paying a reward, so slow control positions are worth less late in a bout and submission
  chains are worth relatively more. That is a real, defensible change and it is the *whole* of
  what a PASS licenses.
* **What moves on the site.** `path_to_victory` → `analysis/counter_moves.py` (`edge_ptv`) and
  `path_to_victory.dilemmas` → `export/site_data.py:_forks_block` → the per-fighter dossier's
  **dilemmas / counter-moves / defense** block, and `analysis/systems.py` →
  `export/ontology.py` + `export/narrative.py`. Every PtV-derived number on the public site moves,
  which means a full `export/site_data.py` regeneration and a commit to the `GrapplingArc` repo.
* **Gate.** Two pieces fitted on ~350 train bouts is a research constant, not a calibrated one;
  §7.1 says so. Shipping requires re-fitting on the whole corpus, a `ponytail:` comment naming
  the ceiling, and the owner's decision — a subagent never runs the replay or the regeneration.
