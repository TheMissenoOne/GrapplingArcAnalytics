# Proof-of-concept plans

Date: 2026-08-23. Two tracks. **E-series** = established techniques the literature has
settled that these systems should adopt — each plan ends in a measured accept/reject
decision, per the house rule (ADR-03's "spread is never a criterion" generalises: the
criterion is always held-out prediction or pre-registered stability, never
aesthetics). **X-series** = experimental methods from recent papers, run as bounded
spikes with pre-declared kill criteria.

Conventions for every PoC:
- Lives under `analysis/poc/<id>_<slug>.py` (or a notebook for X-series), with a
  pytest file; imports `stats_rigor` for every interval; seeds fixed.
- Temporal splits only (train ≤ T, evaluate T+1) — the corpus is longitudinal and a
  random split leaks.
- The write-up lands in `docs/research/poc/<id>.md`: setup, criterion (stated before
  running), result, decision. A null result is a publishable outcome.
- Nothing touches a production export until its PoC doc records an accept.

---

## E-series — established methods to implement

### PoC-E0 · The log-loss harness (prerequisite for everything rating-shaped)

**Claim being tested:** none — this builds the instrument ADR-03 already promised.
**Literature:** Coulom 2008 §experiments; Dehpanah 2021 (evaluation design);
arXiv:2604.21087 (xT-class evaluation).

Build `analysis/poc/e0_rating_eval.py`:
1. Chronological bout stream from `matches` (SUBMISSION/POINTS/DECISION outcomes,
   draws excluded — there are ~0).
2. Walk-forward evaluation: at each year boundary T, fit/replay each engine on bouts
   ≤ T, predict every T+1 bout with that engine's expected-score function, score
   log loss + Brier + accuracy. Wilson/bootstrap intervals via `stats_rigor`.
3. Engines: (a) always-0.5 baseline; (b) win-rate baseline; (c) ADCC-Elo K=40;
   (d) `athlete_elo` graph engine; (e) Glicko-2 as configured (τ=0.5);
   (f) Glicko-2 τ-sweep {0.2, 0.5, 0.8, 1.2}; later (g) WHR from PoC-E3.
4. Report per-engine, plus per-slice (bouts where both athletes have ≥5 prior bouts
   vs cold-start) — sparse-schedule behaviour is where FightMatrix's findings bite.

**Accept criterion:** the harness itself is accepted when (c) beats (a) and (b) —
sanity. Every subsequent engine claim in these repos cites its number from this
harness. **Effort:** 2–3 days. **Risk:** none (read-only).

> **STATUS 2026-08-23: BUILT AND ACCEPTED.** `analysis/poc/e0_rating_eval.py` +
> `tests/test_poc_e0.py` (11 tests). First run on the scouting corpus (689 scored
> bouts): tables in `poc/e0.md`, reading in `poc/e0_notes.md`. Findings: Elo K=40
> with multipliers wins (log loss 0.5174 / acc 0.747); the τ sweep is a four-decimal
> null (FightMatrix confirmed); yearly-period Glicko-2 trails the win-rate baseline —
> period granularity, not τ, is the lever. No production change; `--source db` run,
> per-bout-period Glicko variant, and a K sweep are the recorded next actions.

App-side twin (smaller): wire the exported-but-uncalled `expectedScore` into a
replay-based log-loss over `entry.successful` on the last 20% of sessions, per
athlete with ≥30 sessions (`churnReport`'s own floor). This is the pre-registered
ADR-13 re-measurement, finally runnable.

### PoC-E1 · Empirical-Bayes shrinkage for every small-N estimate

**Claim:** shrunken estimators rank techniques/nodes better than raw σ-thresholds
and point-estimate rankings. **Literature:** Efron & Morris 1975; Brown 2008;
Whelan & Klein 2018.

1. `analysis/shrinkage.py`: beta-binomial EB for proportions (method-of-moments
   prior over the population of nodes), normal-normal EB for ratings
   (`shrunk = μ + (1 − B)(x − μ)`, B from within/between variance). ~60 lines + tests.
2. Rewire three consumers behind flags:
   - `reward_risk_ranking` → rank by shrunken posterior mean (or switch the report
     to the existing `reward_risk_with_ci` and sort by `ci_lo`) — kills the
     n=5-above-n=64 pathology either way.
   - `deviance.py` → shrink per-node means toward the type baseline before z-scoring;
     raise `MIN_POP` to 5 and let shrinkage handle the floor.
   - App/Web signature detection → "signature = shrunken interval clears the
     athlete's mean", replacing bare ±1σ (spec here; TS port in the App repo).
3. **Accept criterion:** on a temporal split, top-k by shrunken estimate predicts
   next-period per-node success/usage better (rank correlation + top-k precision)
   than top-k by raw estimate. Also report how many current "signatures" survive —
   churn is a *finding*, not a blocker.

**Effort:** 2 days Python + 1 day TS port. **Risk:** signature churn in App UI —
gate behind the churn report.

### PoC-E2 · Leiden + resolution sweep for constellations

**Claim:** Leiden at a stability-selected resolution yields communities at least as
stable (bootstrap Jaccard, existing machinery) with zero connectivity rejections and
no resolution-limit merges on small graphs. **Literature:** Traag 2019; Fortunato &
Barthélemy 2007; Good 2010.

1. `uv add leidenalg python-igraph` (Analytics only; App decision deferred until
   measured — the golden-fixture contract means the App port follows only on accept).
2. `analysis/poc/e2_leiden.py`: run Louvain-as-shipped vs Leiden at
   γ ∈ {0.5, 0.8, 1.0, 1.5, 2.0} over (a) the 15-athlete detector-comparison set,
   (b) the two bracket divisions, (c) the corpus graph. Reuse
   `constellations/stability.py` bootstrap + `compare.py` partition metrics.
3. Report per (algorithm, γ): `rejected_rate` (should be structurally 0 for Leiden),
   mean/p10 bootstrap Jaccard, community-size distribution vs the √(2L) resolution
   bound, and — the resolution-limit probe — whether known-distinct small systems
   (from the taxonomy's curated seeds) get merged.
4. **Accept criterion (pre-registered):** Leiden accepted if stability ≥ Louvain's
   and rejected_rate = 0; the swap then goes through the golden-fixture process
   (regenerate fixtures from Python, port refinement phase to TS or accept a
   documented parity break). Consolidation rider: `athlete_systems` moves off greedy
   modularity onto the shared detector in the same change.

**Effort:** 2 days measure + 2–4 days TS port if accepted.

### PoC-E3 · Whole-History Rating for the static corpus

**Claim:** WHR beats the yearly-period Glicko-2 on PoC-E0's log loss for this
sparse, fully-stored, batch-recomputed corpus. **Literature:** Coulom 2008.

1. Implement WHR (Bradley-Terry with Wiener-process prior on each athlete's rating
   trajectory; Newton per-player as in the paper) in `analysis/poc/e3_whr.py`
   (~200 lines, no dependency) or vendor `whr` and pin.
2. Fit w² (drift variance) on train years; evaluate through PoC-E0.
3. **Accept criterion:** log-loss improvement over tuned Glicko-2 with a bootstrap
   CI excluding zero. If accepted, WHR becomes a *shadow* run first (same ADR-02
   pinned-run discipline), with the site gate (`RD ≤ 200`) re-derived from WHR's
   posterior sd impact table before any swap.
4. Even if rejected: WHR's fitted w² is the principled value for "how fast does
   grappling strength drift", replacing `athlete_elo`'s guessed 36-month half-life.

**Effort:** 3–4 days. **Risk:** none until a swap decision.

### PoC-E4 · Calibrating Path-to-Victory (VAEP-style)

**Claim:** γ and the shaping term should earn their values. **Literature:** Decroos
2019; Singh 2019; semi-Markov critique (Sci Rep 2026); Lamas 2024 as external anchor.

1. Frame the VAEP-analogue label: for each event in a bout, does a *successful
   submission by the same actor* occur within the next k=5 events?
2. Evaluate PtV-as-shipped as a predictor of that label (rank the current node's
   `v(n)`; AUC via `stats_rigor.auc`). Then sweep γ ∈ {0.6…0.95} and
   shaping ∈ {on, off}; pick by held-out AUC/log loss.
3. Memorylessness probe: first-order vs second-order transition model
   log-likelihood on held-out bouts (state = (prev, cur) pairs, pruned at min
   count 5). If second-order wins materially, PtV's kernel is the thing to revisit,
   not its γ.
4. External anchor: report our corpus's back-take→submission, takedown→submission,
   guard-pass→guard-pass probabilities beside Lamas 2024's published 0.45/0.15/0.30,
   with intervals — agreement is validation content for the-data.html;
   disagreement is either a corpus insight or an attribution-bug detector.
5. **Accept criterion:** the (γ, shaping) chosen by held-out AUC ships; if AUC's CI
   includes 0.5, PtV is demoted from site prose until it doesn't (that is ADR-03's
   rule applied to a metric).

**Effort:** 3 days. Dependency: the `perspective_reliable` gate must be applied to
the training kernel (gap #2) or the calibration inherits the attribution noise.

### PoC-E5 · "Grapple-like v2" — a defined, evaluated similarity

**Claim:** a published similarity must have a definition and a measured validity.
**Literature:** Tantardini 2019; Bagrow & Bollt 2019; Narayanan 2017; ACM SAC '22.

1. Candidates, all cheap on our graph sizes: (a) current ELO-weighted mpnet centroid
   (baseline); (b) unweighted mpnet centroid (the missing ablation); (c) graphlet
   correlation distance (orca or hand-rolled ≤4-node graphlets); (d) portrait
   divergence; (e) graph2vec.
2. Ground truth without labels: **self-recognition** — split each athlete's bouts
   into halves (odd/even by date), embed each half, and score each method by how
   often an athlete's half-A nearest neighbour is their own half-B
   (top-1/top-5 recall). Athletes with ≥6 sequence-bearing bouts only.
3. Secondary probe: same-division vs cross-division similarity distributions
   (weight classes should be somewhat separable).
4. **Accept criterion:** the winner ships with its definition and its
   self-recognition number printed on the dossier ("similarity = X; method
   identifies an athlete's own game Y% of the time"), replacing the current bare
   percentage. If (a) wins, fine — it finally has evidence and a definition.

**Effort:** 3 days.

### PoC-E6 · Archetype k and stability (run the machinery that already exists)

**Claim:** k=6 must be selected, not assumed. **Literature:** Hennig 2007.

1. Fix the two bootstrap defects (Hungarian assignment via
   `scipy.optimize.linear_sum_assignment`; dedupe resampled indices before
   Jaccard) — they bias the very stability scores the selection needs.
2. Run `optimal_k_by_stability` k ∈ 3–10 and `fit_hdbscan`; report mean/p10 Jaccard,
   ARI, and cluster-size distributions.
3. **Accept criterion:** ship the k the machinery picks (or HDBSCAN's, if its
   Jaccard dominates); archetype `FEATURE_VERSION` bumps; centroids re-backfilled.
   Shrunken deviance from PoC-E1 feeds the v5 features.

**Effort:** 1–2 days.

### PoC-E8 · Interaction graph (actor-aware states) — app-data-first

**Claim:** an actor-aware interaction graph (states tagged YOU/OPP, edges =
chronological succession across actors) carries tactical information the
within-actor ActionFlow graph structurally cannot — e.g. `YOU:Turtle → OPP:Back`
as an edge — and is the better substrate for Markov/PtV/counter analysis.
**Source:** the external PoC review (`05_EXTERNAL_POC_REVIEW.md` §1), which
measured 23 actor-switch edge types on the committed fixture and PageRank shifts
of 8–11 ranks for reaction-defined positions. **Literature:** the two-actor
version of transition-network analysis (arXiv:2411.15486); Lamas 2024's kernel is
already cross-actor at the risk term.

1. `analysis/transitions/interaction_graph.py`: nodes `(role, label)` with
   role ∈ {you, opp} (app data) or the two athletes (corpus data); edges =
   consecutive events regardless of actor, within a round/bout. Keep ActionFlow
   untouched — the two are different products, routed explicitly (the review's
   strongest point).
2. Data: the committed fixture first (`analysis/poc/fixtures.py` — app actors are
   structurally reliable), then corpus bouts **gated on
   `attribution.bout_flags(...).perspective_reliable`** (43.9% of bouts fail it;
   ungated interaction edges there measure the ingest batch).
3. Measure: (a) reproduce the fixture findings with our own code (actor-switch
   edge count, PageRank rank shifts vs ActionFlow); (b) re-run the PtV absorbing
   model on the interaction kernel and compare held-out finish-prediction AUC vs
   the ActionFlow kernel (the PoC-E4 harness decides — same criterion, two
   kernels).
4. **Accept criterion:** the interaction kernel wins the E4 AUC comparison, or
   surfaces vulnerability edges (opp-response patterns) that ActionFlow provably
   cannot represent AND that survive a stability check. Either earns it a place
   as a second, explicitly-labelled graph; neither replaces ActionFlow.

**Effort:** 2–3 days. Rider from the same review: audit that nothing presents
`network_metrics.route_to_submission` (greedy, max_steps=6) under the
"Path-to-Victory" name — the value model and the greedy walk must never share a
label (folded into PoC-E4's scope).

### PoC-E7 · Replace the tech-library effectiveness composite

**Claim:** the 0.35/0.25/0.15/0.15/0.10 composite should be replaced by a model
whose weights are fitted. **Literature:** Terner & Franks 2021 (framing);
`stats_rigor` for uncertainty.

1. Define the target the composite gestures at: P(technique attempt → finish),
   EB-shrunken (PoC-E1) across the corpus, with stage/weight-class breadth as
   *covariates in a logistic model*, not hand-weighted addends.
2. Ship `effectiveness_score = shrunken finish rate` + `breadth` as a separate
   field; the App sorts by the shrunken rate (monotone, continuous — kills the n=3
   discontinuity).
3. **Accept criterion:** temporal-split rank stability of the new score ≥ old, and
   the top-20 list survives an eyeball review against the old (documented, not
   silent). Coordinate the App-side `nodes_library` schema bump.

**Effort:** 2 days + App coordination.

---

## X-series — experimental spikes from recent papers

### PoC-X1 · Athlete PageRank on the bout graph (Radicchi 2011)

Loser→winner edges over `matches`, weighted PageRank (the `network_metrics`
implementation, reused at athlete level), compared against Glicko-2 through PoC-E0.
**Kill criterion:** if it neither beats Glicko-2 on any slice nor disagrees
informatively (disagreements not enriched in data-quality flags), archive the
notebook. **Effort:** 1 day. Cheap, and the *disagreement list* doubles as a
data-quality instrument regardless.

### PoC-X2 · Infomap on directed transition graphs

`infomap` package; two-level map equation on the directed, weighted graph;
compare against Leiden (PoC-E2) with the same stability instruments plus a
flow-coherence score (fraction of random-walk steps staying in-community — the
objective Infomap optimises and modularity ignores). **Kill criterion:** no
stability or interpretability gain over Leiden on the 15-athlete set → document
and stop. **Effort:** 1–2 days, notebook-only.

### PoC-X3 · Supervised sequence mining (Bunker 2021)

CM-SPAM/PrefixSpan (spmf via JVM, or a ~100-line PrefixSpan) over event-type/label
sequences, patterns scored by finish-within-k lift with `stats_rigor` intervals and
BH across the pattern family — the pattern-level version of the question
`decision_criteria` answered "no" at triad level. **Kill criterion:** if nothing
survives BH at q≤0.10, publish the null exactly as `decision_criteria_findings.md`
did (that is a respectable second null, and evidence the corpus is still too thin
for tactical claims — itself worth stating on the-data.html). **Effort:** 2 days.

### PoC-X4 · ST-GCN position classifier on ViCoS (Yan 2018)

Prereq: **measure the existing sklearn baseline first** — no accuracy number for
our own pipeline exists; until it does, phase 1 is unfinished. Then: ST-GCN over
2-second keypoint windows (two athletes = 34 joints or twin-stream), same
GroupKFold-by-video discipline, target: beat the measured baseline and approach the
90–92% references. **Kill criterion:** <5pt gain over the sklearn baseline at 10×
the training cost → stay sklearn, document. **Effort:** 1 week (GPU helpful).
Payoff: automated event extraction directly attacks gap #2 (the attribution
bottleneck is human tagging).

### PoC-X5 · Bracket advancement probabilities with uncertainty (Brandes 2025)

Only if the head-to-head refusal is consciously relaxed. Exact DP advancement
probabilities at n=16 with pairwise P from Glicko-2 ratings; **propagate rating
uncertainty** by sampling (r_i ~ N(rating, RD²)) into advancement *intervals* —
wide intervals ARE the refusal, now quantified. Conformal calibration
(arXiv:2208.08598) on historical brackets if any exist in-corpus. Scope label
`categoria`, `gate_refuses_when_RD>200` carried per athlete. **Effort:** 2 days
math + a BracketAnalysis panel + validate.py rules.

### PoC-X6 · GNN counter-suggestion (TacticAI-shaped, long horizon)

Guided generation over position graphs ("given this position against this archetype,
which next transition maximises PtV?") — TacticAI's receiver-prediction/
generation split maps cleanly onto (position → next-technique) prediction. Blocked
today by corpus size (~9.6k events) and gap #2; revisit when the event corpus
passes ~50k attributed events or PoC-X4 starts generating events from footage.
Until then the GCN spike (`gnn_predictor.py`) stays quarantined by its status
header — or gets its 1-day evaluation through PoC-E0's harness and lives or dies
by the number.

### PoC-S1 · Site generator statistical-communication pass (established, not experimental)

Not a model — the highest-leverage credibility item. `export/site_data.py`:
(1) route every published proportion through `est`/`gated`; (2) never print a
relative change of an index as "%": template helpers `fmt_pp` / `fmt_rel`;
(3) denominators accompany every rate ("100% (1/1)"); (4) generate a
`the-methods.html` from the code that defines Decision space, PtV, grapple-like
(post PoC-E5), ELO-weighting, Confidence — closing the "no black boxes" gap;
(5) delete the two runtime-patch functions by fixing the generated prose they
patch; (6) remove the fabricated fixture at source. Acceptance: the
`GrapplingArc/docs/RESEARCH_NOTES.md` defect list re-audited to zero. **Effort:**
3–4 days, no research risk.

---

## Sequencing

```
Week 1:  E0 harness ──► every rating claim gets a number
         E1 shrinkage (Python side) · E6 archetype k   (independent, parallel)
Week 2:  E2 Leiden ↔ X2 Infomap (same instruments, run together)
         E4 PtV calibration (needs gap #2 gate applied to its kernel)
Week 3:  E3 WHR (through E0) · E5 grapple-like v2 · X1 PageRank (1 day)
Week 4:  S1 site pass · E7 effectiveness · X3 sequence mining
Later:   X4 ST-GCN (after baseline measured) · X5 (policy decision) · X6 (corpus-gated)
```

Everything in weeks 1–3 is read-only against production exports; the first
production-touching changes (E1 rewiring, E2 swap, E7 schema) each ride behind
their PoC doc's recorded accept.
