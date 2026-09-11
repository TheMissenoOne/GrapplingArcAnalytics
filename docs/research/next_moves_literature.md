# Next-move prediction — literature pass

**Backlog item:** `literature-pass-next-moves` (the pass the round that produced
`docs/next_moves.md` never ran). **Scope:** what the field knows about predicting the next
action in an event stream, cross-referenced against what `analysis/next_moves.py` actually does.
**Privacy class: A — public competition corpus only.** A next-move ranking is shown to third
parties and it ranks techniques, so it is a competitive artefact: no `owner_kind='user'` graph,
`user_sessions` row or profile enters any model, sweep or number in this document (root
`CLAUDE.md`).

**Written 2026-09-11.** Nothing here changes code, a schema, a replay or an export.

---

## 0. What we are grounding — the system as it exists, measured

| | value | where |
|---|---|---|
| corpus | 742 bouts (`matches`, `status='final'`, cached 2026-09-02) | `data/next_moves/corpus.json` |
| decision points | 4 007 | `data/next_moves/eval_meta.json` |
| candidate vocabulary (actions) | **198** | ibid. `vocab` |
| distinct state labels | 78; top 3 hold 66.5%; `Back Control` alone 44.1% of state events | `docs/next_moves.md` §6 |
| model | Witten-Bell interpolated `(state, prev_action) → (state) → unigram`, max order 2 | `analysis/next_moves.MarkovNextMoves` |
| shipped verdict | Markov wins; both embedding sources lose by 15-16 pp top-3 | `docs/next_moves.md` §7 |
| headline number | val top-3 **27.2%** [23.1, 31.3], n = 834 points / 107 bouts | `data/next_moves/eval.csv` |
| split | 80/20 **random by bout**, `seed = 20260902` | `analysis/next_moves.split_by_bout` |
| marginal-frequency baseline | val top-3 23.7% (`markov-unigram`) | `data/next_moves/eval.csv` |
| proper scoring rule reported | **none** — `evaluate()` emits top-k + truncated MRR only | `analysis/next_moves.evaluate` |

Two facts about the corpus that every hypothesis below has to survive: the state vocabulary is
degenerate (45.5% of decision points are "you are in back control") and `current_state` is stale
by a median of 2 events, max 26, because the refiner logs positions far more rarely than actions.
`docs/next_moves.md` §6 already names this as the ceiling the experiment hit.

---

## 1. Findings

Seventeen sources. "So what" is grounded in the code above, not in general advice.

| # | Source | Claim (one line) | So what for GrapplingArc |
|---|---|---|---|
| 1 | Lamas, Heiner, Ferreira, Moura, Rangel, Fellingham & Lage (2024), *No-gi Brazilian jiu-jitsu: a Markovian analysis of elite-level combat dynamics*, **IJSSC** 19(4) 1767-1775, [doi:10.1177/17479541231210979](https://journals.sagepub.com/doi/10.1177/17479541231210979) | A 12-action / 24-state first-order chain over 93 WSFC-2019 matches estimates transitions and a reward-risk balance; the strongest within-competitor cell is pass→pass (0.30) and back-take carries the highest direct transition to submission (0.45). | The one peer-reviewed BJJ Markov paper and already the citation behind `analysis/network_metrics` and `analysis/lamas_chain`. It licenses the **model form** we ship (first-order chain over actions) and nothing beyond it: the paper's own state space is 12 actions, not 198 labels. Our pass→pass analogue is exactly the self-transition `next_moves` deliberately keeps. |
| 2 | Halverson, Heiner, Lamas & Fellingham, *Hierarchical Absorbing Markov Chain Models for Pooling Sparse Combat Sequence Data* — announced on a BYU statistics CV listing, **UNVERIFIED: not retrievable, absent from [Heiner's publication page](https://heiner.byu.edu/research) as of 2026-09-11** | The same group's stated follow-up: pool sparse per-competitor combat chains hierarchically instead of fitting each one alone. | The most on-the-nose lead in this list and the one we may **not** cite in code until it exists. Treat as a signpost: the group that wrote source 1 considers sparsity-pooling the next problem, which is the same problem our per-athlete graphs have. Re-check quarterly; do not build on it. |
| 3 | Jääskinen, Xiong, Corander & Koski (2014), *Sparse Markov chains for sequence data*, **Scand. J. Statist.** 41(3) 639-655, [link](https://onlinelibrary.wiley.com/doi/abs/10.1111/sjos.12053) | Bayesian learning that **clusters contexts into invariance classes** with a shared conditional distribution — sparsity handled by lumping histories, not by shortening them. | Directly aimed at our shape: 78 states × 198 actions is 15 444 cells fitted on 3 173 training points. Lumping `Back Control`/`Body Triangle`/`Seatbelt` into one predictive class is what the data can support, and unlike E9's frozen control-explosion map it would be **learned** rather than hand-partitioned. Candidate for H3. |
| 4 | Begleiter, El-Yaniv & Yona (2004), *On prediction using variable order Markov models*, **JAIR** 22:385-421, [arXiv:1107.0051](https://arxiv.org/abs/1107.0051) | Six VOMM algorithms compared by **average log-loss** on proteins/text/music; decomposed CTW and PPM win; the order is chosen per context, not globally. | Our max order is a fixed constant (`max_order=2`). E9 measured the *global* estimable order at label level as 2 — a VOMM says that question is per-context: `Back Control` has 1 825 points and can carry two steps, a state seen four times cannot. Also sets the metric norm: **log-loss**, which `evaluate()` does not compute. |
| 5 | Chen & Goodman (1999), *An empirical study of smoothing techniques for language modeling*, **Computer Speech & Language** 13(4) 359-394, [PDF](https://u.cs.biu.ac.il/~yogo/courses/mt2013/papers/chen-goodman-99.pdf) | Systematic comparison of Jelinek-Mercer / Katz / Witten-Bell / Kneser-Ney; **training-set size is the dominant factor**, and modified Kneser-Ney consistently wins. | Witten-Bell was chosen in `next_moves` because it needs no swept λ — defensible, and this paper is the reason it is not obviously optimal. At 3 173 training points we are deep in the small-data regime the paper says the choice matters most. A modified-KN arm is ~30 lines against the existing `_wb`; worth one arm of H3, not a project. |
| 6 | Ludewig & Jannach (2018), *Evaluation of session-based recommendation algorithms*, [arXiv:1803.09587](https://arxiv.org/pdf/1803.09587); Ludewig, Mauro, Latifi & Jannach (2021), *Empirical analysis of session-based recommendation algorithms*, **UMUAI**, [link](https://link.springer.com/article/10.1007/s11257-020-09277-1) | Across datasets, simple nearest-neighbour and co-occurrence heuristics match or beat RNN/GRU4REC session models; neural gains are "still limited". | Independent confirmation that our negative embedding result is the expected one, not a bug in our wiring. It also names the one family we have **not** tried: session-kNN (retrieve the most similar past bout prefixes, vote on their continuations). That is a counting method, offline, explainable — same class as what we ship. |
| 7 | Ferrari Dacrema, Cremonesi & Jannach (2019), *Are we really making much progress? A worrying analysis of recent neural recommendation approaches*, **RecSys'19**, [arXiv:1907.06902](https://arxiv.org/pdf/1907.06902) | 11 of 12 reproducible neural recommenders are beaten by simple baselines; weak baselines are the field's systemic failure. | Our pre-registration already does the thing this paper asks for: a marginal-frequency floor (`markov-unigram`) and an alphabetical floor are both in `eval.csv`. Keep them in every future table. It also justifies refusing a transformer arm on 4 007 points. |
| 8 | Krichene & Rendle (2020), *On sampled metrics for item recommendation*, **KDD'20** / **CACM** 2022, [PDF](https://dl.acm.org/doi/pdf/10.1145/3394486.3403226) | Top-k metrics computed against a **sampled** candidate set are inconsistent with the full-ranking version — they do not even preserve "A beats B". | We already rank the full 198-candidate vocabulary for every point. This is a "do not regress" finding: if anyone ever speeds the sweep up by sampling negatives, every published comparison in `docs/next_moves.md` becomes incomparable. Write it into the runner's docstring. |
| 9 | *Time to Split: exploring data splitting strategies for offline evaluation of sequential recommenders* (2025), [arXiv:2507.16289](https://arxiv.org/pdf/2507.16289) | For sequential prediction, the split protocol changes not just the level but the **ranking** of methods; global-temporal splits are the realistic protocol. | The direct hit on our protocol. `split_by_bout` is random-by-bout. It prevents within-bout leakage — which was the right first worry — but it lets the model train on 2026 bouts and predict 2025 ones. See §2: re-scored chronologically, the headline drops 5.6 pp. |
| 10 | Leakage-aware sports forecasting protocols — horse racing, [Front. Artif. Intell. 2026](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2026.1922250/full); football secondary markets, [ScienceDirect 2026](https://www.sciencedirect.com/science/article/pii/S2590005626003620) | Sports-prediction papers now treat chronological organisation as mandatory and report random-split optimism as a named bias. | Same conclusion from the sport side rather than the recsys side. Our `year` column is already in `corpus.json`, so the fix costs one function. |
| 11 | Simpson, Beal, Locke & Norman (2022), *Seq2Event: learning the language of soccer using transformer-based match event prediction*, **KDD'22**, [link](https://dl.acm.org/doi/10.1145/3534678.3539138) | Autoregressive transformers over possession sequences predict the next event and reveal tactical regularity; trained on multiple seasons, held out by season. | The state of the art for this exact task — and the scale that makes it work is millions of events. We have 9 592. Cited to justify a **non-goal**, and to borrow the protocol (hold out whole seasons, i.e. whole years). |
| 12 | Mendes-Neves, Meireles & Mendes-Moreira (2024), *Forecasting events in soccer matches through language* / *Towards a foundation large events model for soccer*, [arXiv:2402.06820](https://arxiv.org/pdf/2402.06820), **Mach. Learn.** [link](https://dl.acm.org/doi/abs/10.1007/s10994-024-06606-y) | LEM predicts a **chain of variables per event** (type, then precision, then attributes) rather than one flat categorical draw. | The decomposition is the transferable idea, not the model. Our target is a flat 198-way label; predicting `type` (8 classes) then `label | type` is strictly better conditioned at our sample size, and it gives a second, coarser metric that E9 already showed is the one coarse states can answer. Cheap to test on existing code. |
| 13 | *Next-event prediction in soccer: assessing the impact of team and player information*, **MLSA'25**, [PDF](https://dtai.cs.kuleuven.be/events/MLSA25/papers/MLSA25_paper_353.pdf) / [Springer](https://link.springer.com/chapter/10.1007/978-3-032-15165-0_5) | Adding team/player identity to a next-event model is measured as its own ablation rather than assumed to help. | We have per-athlete graphs and an obvious temptation to condition on the athlete. This says: treat athlete identity as an **ablation with its own number**, and expect the sparsity cost (median ~1 bout per athlete-node, ADR-03) to eat the gain unless it is pooled (sources 2, 3). |
| 14 | Yeung, Bunker, Umemoto & Fujii (2023/2025), *Transformer-based neural marked spatio-temporal point process for football match events*, [arXiv:2302.09276](https://arxiv.org/abs/2302.09276), **Applied Intelligence** 55(5) | Jointly models **inter-event time**, zone and action; most models "neglect the temporal factors"; +4% overall, +9% per component over baselines. | E9 measured the same thing on our corpus from the other direction: absorption is time-inhomogeneous, duration carries the terminal, state history does not. Our decision points throw the clock away entirely. `ts` exists on the events, and "events since the state was logged" is free — and it is exactly the staleness that §6 blames for ranking a heel hook from mount. Best cost/benefit arm in this list. |
| 15 | Yu (2010), *Hidden semi-Markov models*, **Artificial Intelligence** 174(2) 215-243, [link](https://www.sciencedirect.com/science/article/pii/S0004370209001416); applied form: *Hierarchical semi-Markov models with duration-aware dynamics for activity sequences* (2025), [arXiv:2509.18414](https://arxiv.org/pdf/2509.18414) | Semi-Markov models let sojourn time be arbitrary rather than geometric; dwell duration changes the transition probabilities. | The formal frame for source 14 and for E9's arm-5 result. If duration is added, this is the model class it belongs to — but as a **feature on the existing chain first**, not a rewrite. Ponytail: the one-line version is a duration bucket in the context tuple. |
| 16 | Gneiting & Raftery (2007), *Strictly proper scoring rules, prediction, and estimation*, **JASA** 102(477) 359-378, [PDF](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf) | Only strictly proper scoring rules (log, Brier/quadratic) reward honest probabilities; ranking metrics do not. | `evaluate()` reports top-k and MRR — neither is a scoring rule. The guidance block **prints probabilities** ("Triangle Choke 29%") into the Gemini prompt and the App export plan is `{state: [[label, p], …]}`. We ship numbers we have never scored as numbers. |
| 17 | Kull, Perelló-Nieto, Kängsepp, Song, Flach et al. (2019), *Beyond temperature scaling: Dirichlet calibration*, **NeurIPS**, [PDF](https://papers.neurips.cc/paper/9397-beyond-temperature-scaling-obtaining-well-calibrated-multi-class-probabilities-with-dirichlet-calibration.pdf); Ferrer (2024), *Evaluating posterior probabilities*, [arXiv:2408.02841](https://arxiv.org/pdf/2408.02841) | Multiclass calibration needs its own measurement and its own (low-parameter) post-hoc fit; a better Brier score does not imply better calibration, because the score mixes calibration with refinement. | Gives the exact recipe for H2: measure log-loss + ECE on held-out data, and if miscalibrated, fit **one** parameter on train (temperature on the log-prior). One parameter is affordable at n = 3 396; a full Dirichlet map at 198 classes is not, and that bound is itself the finding. |

---

## 2. What E9 already settled vs what the literature says to test next

### 2a. Settled — do not re-open

From `docs/research/poc/e9.md` (pre-registered in `e9_prereg.md`, temporal split, 429 gated bouts):

| Question | Verdict | Consequence here |
|---|---|---|
| Is the 226-label state space too sparse; do 8 event types beat it? | **REJECT.** Undominated set = all three spaces; on finish AUC the label space *wins* (0.689 vs 0.629/0.638). | Keep label-level states. Any coarsening proposal must beat this, and "it's sparse" is not evidence. |
| Does exploding `control` into five dominance states help? | **REJECT.** Δ = −0.0063 [−0.0159, +0.0043]. | The hand-written control partition is closed. Source 3's *learned* context clustering is a different question and stays open. |
| Maximum estimable Markov order | **2 for labels, 1 for the category spaces**; order 3 neutral-to-worse. | `max_order=2` is correct as a global constant. The open part is per-context order (source 4). |
| Separate ADCC kernel? | **REJECT.** Paired Δ −0.0731 [−0.1400, −0.0045] — significantly worse. Real divergence exists (`cat/control`, `cat/submission` at BH q ≤ 0.05) and is smaller than the cost of estimating it on 56 bouts. | No per-ruleset next-move model. The fix is more ADCC bouts. |
| Is terminal choice history-dependent? | **REJECT at depth 1, 2 and 3** (M1−M0 +0.0426 [−0.0245, +0.1042]). | Do not lengthen the memory to predict outcomes. |
| Is absorption time-inhomogeneous? | **CONFIRMED.** Hazard rises across interior bins; submission and points **cross over** (0-2 min: 3.4% sub vs 1.6% points; >10 min: 29.0% sub vs 66.7% points). | **Duration carries the terminal.** The open transfer is whether duration also carries the next *action* — nothing in E9 tested that. |

And from `docs/next_moves.md`: the embedding ranker is a closed negative (11-12% top-3 vs 27.2%), the hybrid's own train sweep chose α ≈ 0, and per-transition candidate texts are rejected. All three stay closed.

### 2b. Measured today — the protocol is the biggest single defect

Sources 9 and 10 say a random split over time-ordered data is optimistic. Our corpus carries `year`
and is heavily back-loaded (602 bouts ≤ 2025, 140 in 2026), so the check costs nothing. Run
2026-09-11 against the cached corpus, offline, no database:

```python
# uv run python - <<'PY'   (read-only; nothing written)
import json
from analysis.next_moves import (corpus_points, build_vocab, library_actions,
                                 MarkovNextMoves, evaluate, markov_rank_fn)
bouts = json.load(open('data/next_moves/corpus.json'))
tr = [b for b in bouts if (b.get('year') or 0) <= 2025]
va = [b for b in bouts if (b.get('year') or 0) >= 2026]
ptr, _ = corpus_points(tr); pva, _ = corpus_points(va)
vocab = build_vocab(ptr, library_actions())
m = MarkovNextMoves(vocab).fit(ptr)
print(evaluate(markov_rank_fn(m), pva, ci=True))
PY
```

| protocol | train | eval | marginal-freq top-3 | **Markov top-3** | 95% CI (cluster, by bout) | gain over marginal |
|---|---|---|---|---|---|---|
| random by bout (`seed=20260902`, shipped) | 429 bouts / 3 173 pts | 107 bouts / 834 pts | 23.7% | **27.2%** | [23.1, 31.3] | +3.5 pp |
| **chronological (train ≤ 2025, eval 2026)** | 602 bouts / 3 396 pts | 140 bouts / **667 pts** | 15.0% | **21.6%** | [18.2, 24.8] | **+6.6 pp** |

Two readings, both worth keeping:

1. **The published 27.2% is optimistic by 5.6 pp** — larger than the 5-pp margin the whole
   experiment was pre-registered to detect. Every future comparison must be chronological or it is
   measuring the split.
2. **The model looks *better*, not worse, once the split is honest.** The marginal baseline
   collapses from 23.7% to 15.0% because the target marginal itself moves year over year
   (`Foot Lock` is 7.0% of 2026 targets and outside the ≤2025 top 5; `Sweep` and `Arm Drag` fall).
   Conditioning on the state is worth roughly twice as much against a drifting marginal as against
   a stationary one. That is a *stronger* argument for shipping the Markov ranker than the one
   `docs/next_moves.md` §7 makes.

Not a confound: out-of-vocabulary targets are 1.35% and unseen states 1.2% on the 2026 slice.
**Is** a confound, and must be stated in any write-up: 2026 spans 11 distinct events against 73 in
train, so calendar time and promotion/ruleset composition are entangled — which is exactly why H1
below asks for a rolling origin rather than one cut.

### 2c. Pre-registered hypotheses

Common protocol for all of them, fixed here before any of them is run:

* **Split: chronological, never random.** Primary cut = train ≤ 2025 / eval 2026. Robustness =
  rolling origin (train ≤ 2023 → eval 2024; ≤ 2024 → 2025; ≤ 2025 → 2026), reported as three rows,
  never pooled into one. A bout is never in both sides; `split_by_bout` is retired from the
  headline and kept only to reproduce the historical table.
* **Candidate set: the full 198-label vocabulary, never sampled** (source 8), built from TRAIN only.
* **Uncertainty: 95% cluster bootstrap over bouts** (`stats_rigor.bootstrap_ci`, `groups=bout_id`) —
  already what `evaluate(ci=True)` does. Half-width on the chronological eval is ±3.3 pp.
* **Floors in every table:** alphabetical floor and marginal frequency (`max_order=0`). Source 7.
* **Public corpus only.** No user-fed row, in any arm, at any α.
* A hypothesis that is not pre-registered here does not get reported as a win.

---

**H1 — the chronological re-baseline (confirm + rolling origin).**
*Claim:* the shipped 27.2% is split-inflated, and the effect is not an artefact of one boundary year.
*Metric:* top-3 under the rolling origin, per fold.
*Threshold:* PASS (= drift is real and stable) if the chronological top-3 is below the random-split
top-3 in **≥ 2 of 3** folds AND the Markov-over-marginal gain is **positive in all 3**. FAIL if any
fold shows the gain disappearing, in which case the 2026 slice was a composition artefact and the
headline must be reported as a range, not a number.
*Already in hand:* the 2026 fold (−5.6 pp vs random; gain +6.6 pp). Two folds to run.
*Cost:* one `split_by_year` function (~8 lines) plus a loop. No DB, no network, no replay.
*Consequence if PASS:* restate `docs/next_moves.md` §3/§5 with the chronological numbers and mark
the random-split table as historical. The recommendation (ship Markov) does not change — it gets
stronger.

**H2 — the clock and the staleness counter (sources 14, 15; E9's confirmed arm 5).**
*Claim:* the next action depends on how long we have been in the current state, and on how stale
the logged state is. Both are free: `ts` is on the events and the event distance is already
computed in `decision_points`.
*Metric:* held-out **log-loss** (primary) and top-3 (secondary), chronological split.
*Threshold:* the duration-conditioned model wins if log-loss improves by **≥ 0.05 nats/step with a
95% cluster-bootstrap interval excluding 0**, or top-3 improves by ≥ 5 pp. Anything smaller is a tie.
*Ablation, reported separately, because they are different claims:* (a) staleness only —
`min(events_since_state, 3)` appended to the order-2 context; (b) elapsed-time bucket from `ts`
(with the 47-bout missing-`ts` exclusion E9 already documented, never defaulted); (c) both.
*Kill criterion:* if (a) alone is null, do not build (b) — the staleness counter is the strictly
cheaper proxy and a null on it means the clock is not reachable through this corpus's logging.
*Why first among the model arms:* it attacks the measured bottleneck (`docs/next_moves.md` §6:
median 2 events, max 26, between the state and the target) instead of the model form, and E9 already
proved duration is informative *somewhere* in this corpus.

**H3 — calibrate the number we already print (sources 16, 17).**
*Claim:* the probabilities the guidance block and the planned App export publish are not calibrated,
and nobody has ever checked.
*Metric:* held-out log-loss, multiclass Brier, and ECE with 10 equal-mass bins on the top-1
confidence, chronological split. Reliability diagram as a generated artefact.
*Threshold:* the shipped model is "fit to publish a probability" if **ECE ≤ 0.05** and its log-loss
beats the marginal baseline by **≥ 0.05 nats**. If ECE > 0.05, fit **one** parameter — a temperature
on `log_prior` — on TRAIN only, and re-measure; report both. More than one calibration parameter is
out of scope at n = 3 396 and saying so is part of the result.
*Cost:* `dist()` already returns a normalised distribution, so log-loss is three lines inside
`evaluate`. The temperature is one `scipy.optimize.minimize_scalar` on train.
*Consequence if FAIL and the temperature does not fix it:* the App export ships **ranks and counts,
not percentages**, and the guidance block drops the "29%" from the prompt. That is a product
decision the orchestrator makes, not this document.

**H4 — better smoothing / variable order under sparsity (sources 3, 4, 5).**
*Claim:* at 3 396 training points, the estimator matters; fixed-order Witten-Bell is not obviously
the best of the family.
*Arms:* (a) modified Kneser-Ney in place of Witten-Bell, same three levels; (b) PPM-C style
variable order, order chosen per context by its own support; (c) context clustering (source 3) that
lumps low-support states into a learned class instead of backing off to the unigram.
*Metric:* held-out log-loss, chronological split, paired by decision point.
*Threshold:* ≥ 0.05 nats with the cluster-bootstrap interval excluding 0, **or** ≥ 5 pp top-3.
*Kill criterion, pre-registered:* E9 already measured the global estimable order as 2 at label level
and order 3 as exactly neutral at 26% support. If arm (b)'s selected order is ≤ 2 for contexts
covering ≥ 90% of eval steps, arm (b) is closed on this corpus and the budget moves to corpus repair.
*Ranking note:* this is deliberately **below** H2 and H3. Source 5's own headline is that training
size dominates the choice of smoother — which points back at the corpus, not the estimator.

**H5 — two-stage target: type, then label (source 12); and athlete conditioning as an ablation (source 13).**
*Claim:* predicting the 8-way `type` first and `label | type` second is better conditioned than one
flat 198-way draw, and gives a coarse number that is directly comparable to E9's category target.
*Metric:* log-loss on the flat label (so the two forms are comparable), plus type-level top-1 as a
secondary.
*Threshold:* ≥ 0.05 nats, interval excluding 0.
*Athlete ablation:* condition on the athlete only with hierarchical shrinkage toward the corpus
prior; report the un-pooled version alongside so the sparsity cost is visible. Expect a null —
ADR-03 measured a median of 1 bout per athlete-node — and pre-register that expectation so a null is
publishable rather than buried.

---

## 3. Non-goals — explicit

1. **No neural sequence model.** Not a transformer, not an RNN, not an LLM generator, not Seq2Event,
   not a LEM. 4 007 decision points and 198 classes. Sources 6 and 7 say simple baselines win at this
   scale even in domains with far more data; sources 11 and 12 work at millions of events. Revisit
   only if the corpus passes ~10× its current size, and even then only after H2-H4.
2. **No embedding or hybrid ranker.** Closed, with numbers, in `docs/next_moves.md` §5. The single
   unresolved hint (gemini α=0.25 on the n=33 cold stratum) is not reopened here and needs its own
   pre-registration if the corpus grows.
3. **No per-ruleset (ADCC) next-move kernel.** E9 arm 4 rejected it and named the mechanism: 56 bouts.
4. **No hand-partitioned state space rework.** E9 arm 2 rejected the control explosion. Learned
   context clustering (H4c) is a different question and must be labelled as such.
5. **No sampled / negative-sampled evaluation.** Source 8. The full 198-candidate ranking stays.
6. **No random split in any reported headline.** Source 9, and §2b measures the cost on our own data.
7. **No private data, ever.** No `owner_kind='user'` graph, no `user_sessions`, no round-video
   analysis output (`session_video_analysis` is PRIVATE by the 0058 contract), in any arm, any
   aggregate, any α, any centroid. A next-move ranking is competitive by definition.
8. **No new dependency.** Everything above is numpy + the standard library + `analysis/stats_rigor`.
   An arm that needs a new package is a proposal, not an experiment.
9. **No replay, no prod write, no site export.** This whole line of work produces a report. The
   Markov ranker's only wired consumer is a prompt block, and the App export is still a plan.
10. **No coaching framing.** 21.6% top-3 is a ranking of corpus tendencies. The product copy says
    statistics, the same way Grappling Rating is always presented relative. That constraint is
    upstream of every hypothesis here and no measurement changes it.

---

## Provenance

Measured or read 2026-09-11 against `GrapplingArcAnalytics` at the state of that date:
`analysis/next_moves.py`, `analysis/next_moves_embed.py`, `scripts/eval_next_moves.py`,
`data/next_moves/{eval.csv,eval_meta.json,corpus.json}`, `docs/next_moves.md`,
`docs/research/poc/{e9.md,e9_prereg.md}`, `docs/rating_v2/01_DECISOES.md` (ADR-03).
The §2b table was produced by the snippet printed above, offline, from the cached corpus; re-running
it after a `--refresh-corpus` will move the numbers and should.
Source 2 is **unverified** and flagged as such; every other link was fetched or returned by search
on 2026-09-11.
