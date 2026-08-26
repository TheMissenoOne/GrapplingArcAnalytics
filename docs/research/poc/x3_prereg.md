# PoC-X3 — pre-registration

Written 2026-08-25, **before any pattern was mined against the corpus**. Fixed from
marginals only (bout counts, chain counts, base finish rate). `x3.md` reproduces this file
verbatim above its results.

Plan cell: `docs/research/03_POC_PLANS.md`, PoC-X3 ("Supervised sequence mining, Bunker
2021"). Literature: Pei et al. 2001 (PrefixSpan); Bunker & Susnjak 2021 (sequence features
for sport outcome prediction); Fournier-Viger et al. (SPMF/CM-SPAM — the variant the plan
mentions as an alternative and this cell does not need).

## What is being tested

Two questions, and they are not the same question.

1. **The plan's:** do frequent sequential patterns have a finish LIFT that survives
   multiplicity correction?
2. **The owner's, added here and made the deciding limb:** do those patterns, **as
   features**, predict held-out finishes *beyond the Markov baseline* — i.e. beyond what the
   current position already tells you?

Limb A can pass while limb B fails, and that combination has a name: a description of the
corpus that carries no out-of-sample information. It is the outcome ADR-03 exists to catch,
so limb B is the criterion and limb A is reported beside it.

## Corpus, gate, split

* Source: `matches` where `status='final' AND sequence IS NOT NULL`, via
  `analysis/poc/e9_markov.load_corpus` — same loader, same gate as PoC-E8/E9.
* Gate: `attribution.bout_flags(...)["perspective_reliable"]` AND ≥ 4 events. **MANDATORY.**
* Corpus drift against PoC-E8's published 429 gated bouts is expected, printed, and named.
* Mirroring: one chain per bout PERSPECTIVE (PoC-E8's contract), so both fighters' games
  are mined. Two perspectives on one bout are **not** two sources — every interval and gate
  in this cell clusters on the BOUT.
* **Split: temporal only** (ADR-03). PoC-E8's `temporal_split`, most recent 25% held out,
  boundary key to TRAIN. **Patterns are mined from TRAIN chains only.** A pattern mined
  from a held-out bout and then scored on it is the leak this whole apparatus exists to
  prevent.

## State space, justified rather than assumed

Mining runs on **`clean_label`** (the S-label space), not on the 8 event types. That is not
a preference — PoC-E9 measured it: on the SECONDARY criterion, which is exactly this cell's
criterion (held-out finish prediction), *the label space wins*, AUC 0.689 against 0.629
(S-cat) and 0.638 (S-v3). The 8-type space wins on per-step likelihood, which is a different
question and not the one asked here.

Consecutive repeats are **folded** (`A → A` is not a transition — PoC-E8's rule and the
graph builder's), so a mined succession means the same thing as an edge everywhere else in
this repo.

## Chains, and the truncation that makes the question honest

One chain per (bout, perspective): that fighter's own ordered, folded labels, **truncated
before their first landed submission**. A pattern that contains the finish predicts the
finish trivially; PoC-X3's question is what PRECEDES one. Chains shorter than 2 items after
truncation are dropped.

## Limb A — lift, with an interval, BH-corrected

* Mine with PrefixSpan: **gapped** subsequences (a grappler's own actions are interleaved
  with the opponent's; a contiguous-only miner asks a different and much sparser question),
  support = number of CHAINS containing the pattern, max length 4.
* **Primary min support: 10% of train chains.** {5%, 20%} are reported as sensitivity and
  are never the verdict — three support levels are three families, and picking the level
  after seeing which one produced a survivor is the failure mode BH cannot fix.
* Per pattern: `stats_rigor.compare_proportions(finishes | pattern present, finishes |
  absent)` → risk ratio with a delta-method interval and a Fisher/chi-square p.
* **Benjamini-Hochberg across EVERY mined pattern**, not a shortlist.
* **Kill criterion, as the plan wrote it:** nothing survives q ≤ 0.10 → publish the null,
  exactly as `decision_criteria_findings.md` did. A null here is a statement about the
  corpus's thickness and is publishable content in its own right.
* Every table is gated on `stats_rigor.coverage` over contributing BOUTS.

## Limb B — the criterion

The rows are **PoC-E8's `eval_rows`, unchanged**: every held-out event whose role is `you`,
labelled with PoC-E4's finish label (a landed submission by the same actor within the next
k=5 events). The same rows PoC-E4, E8 and E9 were scored on, so this number is comparable
to theirs.

Two models, fitted on TRAIN rows, scored on the SAME held-out rows:

* **baseline** — logistic regression on a one-hot of the current `clean_label` over the
  train vocabulary. This is the Markov baseline in its most generous form: everything the
  current position says, fitted.
* **patterns** — the same one-hot **plus** one indicator per mined pattern, firing when the
  pattern is a subsequence of that actor's history within the bout **and its last item is
  the current label** ("the pattern just completed here", not "it happened at some point").

`sklearn.linear_model.LogisticRegression`, L2, C=1.0, `max_iter=2000`, `random_state=20260820`
— fixed here so no hyperparameter is chosen after seeing an AUC.

* **Primary: paired ΔAUC (patterns − baseline)**, percentile bootstrap with the **BOUT** as
  the resampling unit (`stats_rigor.bootstrap_ci` via PoC-E8's `_boot_ci`), 2 000 draws,
  seed 20260820.
* **ACCEPT iff the paired Δ interval excludes 0 in the patterns' favour, with a
  non-degenerate width** (`hi > lo` — PoC-E9's amendment). Anything else is REJECT.
* Both models are supervised, on the same rows, with the same label, differing only in the
  pattern columns — so the Δ isolates the patterns and nothing else.
* **Reported, never a criterion:** production Path-to-Victory (γ=0.8, shaping off) on the
  same rows. PtV is fitted on nothing; a supervised model beating it would prove only that
  supervision helps.

## Stated limits, before the numbers

* `successful` is present on **28.9%** of events (PoC-E9's measurement); absent reads as
  `False`, so the finish label's positives are undercounted in both limbs alike.
* **43.9%** of corpus bouts are one-sided; the gate removes them but cannot repair actor
  noise inside the ones it keeps.
* The held-out window is right-censored at each bout's end, identically for both models.
* Limb A's truncation removes the finishing submission from the chain but not the events
  that led to it — a pattern ending one step before a finish is still mineable, and should
  be.
* **A null in both limbs is the most likely outcome and is not a failed run.** PoC-E4
  measured that second-order memory LOSES at label level (Δ logL/step −0.203 [−0.258,
  −0.133]); a mined subsequence is higher-order memory under another name, so the prior
  from inside this repository is that limb B fails. Registering that prior here is the
  point: it stops a null from being re-read afterwards as "we expected more".
