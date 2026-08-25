# Gaps and opportunities — ranked, cross-repo

Date: 2026-08-23. Derived from the code survey + literature cross-reference in
`01_LITERATURE_REVIEW.md`. Each entry: what's wrong (or missing), where, what the
literature says, and the disposition — **FIXED** (on branch
`claude/analytics-literature-review-dexomn`), **PoC-…** (planned in
`03_POC_PLANS.md`), or **flagged** (documented, needs an owner's decision).

## Tier 1 — validity threats (results may be wrong because of these)

| # | Gap | Where | Literature | Disposition |
|---|---|---|---|---|
| 1 | **No predictive validation anywhere.** No rating engine, centrality, PtV, archetype or effectiveness score has ever been scored on held-out outcomes; ADR-03 pre-registered log loss and the harness was never built (`expectedScore` exported, zero callers). | all repos | Coulom 2008; Dehpanah 2021; arXiv:2604.21087 (evaluation methodology) | **PoC-E0** — the prerequisite for every other rating/metric decision |
| 2 | **43.9% of bouts have uninformative `actor_id`**; guard/pass convention refuted at event level. `bracket_export` gates on `perspective_reliable`; `network_metrics.build_transition_network` and `insights.py` don't. | Analytics | measurement-error-in-network-construction (Buldú 2018 discussion) | **flagged** — wiring `bout_flags` into the two ungated consumers is mechanical but changes every downstream number; needs a corpus re-export decision. The deeper fix is re-ingesting the single-actor batches. |
| 3 | **Small-N σ-thresholds without shrinkage**: `deviance.py MIN_POP=3` z-scores (feeds 8/18 archetype dims + signature threshold); App `MIN_NODES_FOR_STATS=4` where max attainable \|z\|=1.5; `detectCategoryAnchors` argmax fallback emits a "signature" unconditionally; Web population-σ on n≈5. | Analytics, App, Web | Efron & Morris 1975; Whelan & Klein 2018 | **PoC-E1** |
| 4 | **App V2 rating is self-referential** (opponent = own global ± 70·(d−5); no external anchor; binary landed/missed where `undefined`=landed) with unvalidated constants (ADR-13, self-declared), session-count RD inflation instead of time-based, and no RD cap. | App | Glickman 2001 (model assumptions); FightMatrix 2019 (τ on sparse data) | **PoC-E0** decides; RD cap + time-based periods are safe independent fixes — flagged for App owner |
| 5 | **Scale mixing in the App**: `computedElo` is V2 (~1200–1500) but `normalizeCategoryElo` still maps [100,700]→[0,1] (radar reach pinned at 1.0 for everyone — the "shape grows with skill" design is currently dead), `eloToTier` bands around 1500 misclassify, six different "default ELO" constants (250/800/1000/1200/1500) coexist. | App | — (internal consistency) | **flagged** with file:line in `GrapplingArcApp/docs/research/ANALYTICS_LITERATURE_REVIEW.md` — needs a product decision on the V1 surfaces |
| 6 | **`embed_technique_graph` had never run** (unnormalised p to `np.random.choice` → ValueError on every walkable graph); walks unseeded; `walk_based_fighter_vector` returned a global SVD component as a "per-fighter vector". | Analytics | Grover & Leskovec 2016 | **FIXED** — normalised + seeded + visit-weighted mean; regression tests added |
| 7 | **Mirror-fold inconsistency**: `_uniform_test` and `_year_series` counted athlete-perspective rows while `_ruleset_test` folded mirrors — the exact anti-correlated double-count `_distinct_bouts`'s docstring warns about. | Analytics (bracket) | — | **FIXED** + tests |
| 8 | **Radar published a naive Wilson interval beside a refusing coverage gate** ("adequate" precision + "3 sources; needs 5" in one row). | Analytics (bracket) | — | **FIXED** — `usage` now goes through `gated()`; test added |
| 8b | **Reward-risk CI denominator mismatch (P0)**: `reward_risk_with_ci` used `occ` (all appearances) as Beta trials while the point estimate's population is `denom` (successor-bearing appearances) — interval and point described different populations. Found by the external PoC review, verified in-code. | Analytics | `05_EXTERNAL_POC_REVIEW.md` §3 | **FIXED** (2026-08-24) — trials/gate on `denom`, both counts in the row; regression test |
| 8c | **Cross-graph SVD similarity coordinate-unidentifiable**: `fighter_embedding_similarity` cosined shared-node rows across independently fitted SVD spaces (sign flips swing the score through its range). Zero callers; the shipped mpnet "grapples most like" does NOT share this defect. | Analytics | `05_EXTERNAL_POC_REVIEW.md` §7 | **FIXED** (2026-08-24) — quarantined (raises with explanation); within-graph-only warnings added; sign-flip trap pinned by test |

## Tier 2 — algorithm generations behind the literature

| # | Gap | Where | Literature | Disposition |
|---|---|---|---|---|
| 9 | **Louvain, not Leiden**, with a post-hoc connectivity gate; resolution fixed at 1.0 with the resolution limit unexamined on 15–70-node graphs; three detectors coexist (shared Louvain, greedy modularity in `athlete_systems`, legacy ELO-DFS live on the App's Tendencies axis). | Analytics + App | Traag 2019; Fortunato & Barthélemy 2007; Good 2010 | **PoC-E2** |
| 10 | **Undirected projection for communities** of a directed flow graph. | Analytics + App | Rosvall & Bergstrom 2008 (Infomap) | **PoC-X2** |
| 11 | **K calibrated to target σ** (a shape statistic) instead of predictive loss; `athlete_elo` constants (36-mo half-life, 2.5× pro mult, 0.5/0.5 blend) all unfitted. | Analytics | arXiv:2512.18013; ADR-03's own criterion | **PoC-E0** scores them; **PoC-E3** (WHR) is the structural upgrade |
| 12 | ~~**PtV's γ=0.8 and shaping weights unvalidated**; first-order memorylessness untested.~~ **CLOSED 2026-08-24** — swept and measured, no production change. | Analytics | Decroos 2019; semi-Markov critique | **PoC-E4 RUN** → `docs/research/poc/e4.md`: γ non-critical over the non-saturating range, shaping a wash at γ=0.8 and harmful above it (±1 clamp), second order LOSES materially so the first-order kernel stands. |
| 12b | **The chain's STATE SPACE was never chosen either** — every Markov-shaped object in the repo (`network_from_sequences`, PtV's kernel, `reward_risk`) uses the raw `clean_label` vocabulary because that is what the events carry, not because it was compared against anything. Nor was the order question ever asked on a *small* state space (PoC-E4 closed it at label level only). | Analytics | Lamas 2024; semi-Markov critique (Sci Rep 2026) | **PoC-E9 RUN** → `docs/research/poc/e9.md`: three state spaces (226 / 8 / 12 states) on one common target, order k∈{0..3} with support coverage, an ADCC-family kernel, and absorbing terminals from `win_type`. |
| 13 | **k=6 archetypes never selected by the stability machinery the file ships**; bootstrap mapping majority-vote-only (docstring claims Hungarian); duplicate-index Jaccard inflation. | Analytics | Hennig 2007 | **PoC-E6** (run the machinery; fix the two bootstrap defects while touching it) |
| 14 | **"Grapples most like" is an undefined percentage**; ELO-weighted semantic centroid mixes incommensurables with no ablation. | Analytics + site | Tantardini 2019; ACM SAC '22 | **PoC-E5** |
| 15 | **`tech_library` effectiveness composite** (0.35/0.25/0.15/0.15/0.10, ordinal-as-ratio, discontinuous at n=3) is the App's primary technique sort key. | Analytics → App | Terner & Franks 2021 (metric-validation framing) | **PoC-E7** |
| 16 | **`benchmark.py` user-vs-pro 2×/0.5× emphasis with no interval** — the only App-facing comparison without uncertainty; baseline quartiles degenerate (escape median 0.0); self-logged training shares compared to event-coded bout shares. | Analytics → App | Wilson/Agresti-Caffo machinery already in-repo | **flagged** — small fix (route through `stats_rigor`), measurement-process mismatch needs a caveat string in the App UI |

## Tier 3 — presentation and hygiene

| # | Gap | Where | Disposition |
|---|---|---|---|
| 17 | Site generator emits percent/pp conflations, undefined metrics, n=1 headline rates, runtime-patched wrong prose, self-normalised radars, corpus-vs-career framing, raw-ELO leaks — catalogue in `GrapplingArc/docs/RESEARCH_NOTES.md`. | Analytics `export/site_data.py` → site | **PoC-S1** (route through `gated`/`est`, generated methodology page); fabricated fixture (`leandro-lo-vs-gordon-ryan-2025`) **flagged** for removal at source |
| 18 | Stale/false code headers: glicko2.ts "NOT WIRED" (it's the live rating path), detect.ts "unwired until measured", systemDetection "feeds nothing" (live for Tendencies). | App | **FIXED** (headers corrected) |
| 19 | Unresolvable citations: "Aldous 2020 / MDPI 2024", "Zhang 2022" unidentified, "Ouyang 2025" for SHAP-never-computed, "Drexler 2024" bare, PtV refs flagged "re-verify". | Analytics | **FIXED** — all resolved to real identifiers or honestly marked as this repo's own choices; `04_BIBLIOGRAPHY.md` is the registry |
| 20 | BracketAnalysis README stale count (314 vs 305 identities). | BracketAnalysis | **FIXED** |
| 21 | `stats_rigor` residuals: `significant` can't fire when either arm has k=0 (0/50 vs 30/50 → not significant); AC point estimate vs interval from different estimators; proportion-calibrated grade cuts reused for ρ [−1,1] and AUC; Fisher-z Spearman on a binary variable; BH families narrow with omnibus p's outside them; event-level serial dependence never cluster-bootstrapped. | Analytics | **flagged** — each changes published semantics of live pages; itemised for the owner in `01_LITERATURE_REVIEW.md` §7-adjacent and worth one deliberate pass rather than drive-by edits |
| 22 | App heuristic engine: `evaluatePosition` dead (with NaN on empty signal lists), `threshold_for_suggestion` dead config, live path substring-matches signal tokens (near-guaranteed firing). Decay computed and persisted but **never applied** (`loadEloDecay` zero readers). Dead exports (~half of eloService). | App | **flagged** in App research doc — product decisions |
| 23 | Site `timeline.js` prints interpolated timestamps as hard HH:MM:SS with no "estimated" marker; The Ocean percentile bars rank raw-zero values in tied distributions, suppress negative ratio suffixes, 3% bar floor. | site | **flagged** in `GrapplingArc/docs/RESEARCH_NOTES.md` |

## Opportunities that are additions, not fixes

- **External anchoring against published BJJ numbers** — Lamas 2024's transition
  probabilities and Andreato 2015's technique success rates are checkable against our
  corpus *today*; agreement/disagreement is publishable content for the-data.html.
  (Part of PoC-E4's evaluation set.)
- **Athlete-level PageRank on the bout graph** (Radicchi 2011) — a second ranking,
  methodologically independent of Glicko-2; their disagreement is itself a
  data-quality instrument. (PoC-X1.)
- **Sequence mining** (Bunker 2021) for "patterns that precede finishes" — the
  supervised-SPM frame answers the question `decision_criteria` asked and honestly
  failed to answer at triad level, at pattern level instead. (PoC-X3.)
- **Bracket advancement probabilities with uncertainty propagation** (Brandes 2025 +
  Glicko-2 RD), if the head-to-head refusal is ever consciously relaxed. (PoC-X5.)
- **TacticAI-style guided generation** — long-horizon; grappling's discrete position
  graph is arguably a *better* fit for GNN recommendation than football's continuous
  pitch, and the corpus is the blocker, not the method. (PoC-X6.)
