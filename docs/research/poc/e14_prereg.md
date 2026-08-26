# PoC-E14 — pre-registration

Written 2026-08-25, **before any arm was scored**. Fixed against read-only marginals only:
the timestamp slice, the own-actor inter-edge gap distribution, and how many edges a window
of each width holds. None of those say anything about which arm wins. `e14.md` reproduces
this file verbatim above its results.

New cell, registered in `docs/research/03_POC_PLANS.md` as **PoC-E14** (E10–E13 were claimed
by concurrent runs).

## The question

Every graph in this repository is built from event ORDER. `ts` is present on 94.7% of events
and is read by exactly one thing — PoC-E9's elapsed-time hazard. This cell asks whether that
is a waste.

**Do δ-temporal motifs (Paranjape, Benson & Leskovec 2017) predict held-out finishes better
than the SAME motif counter run over an index window?**

The design is one paired difference and nothing else: same edges, same motif alphabet, same
counter, same rows, same model, same seed, same bootstrap. The only free variable is whether
the window is measured in **seconds** or in **events**. An unpaired "temporal model vs order
model" comparison would confound the clock with every other difference between two feature
sets; this one cannot.

## Temporal network

* Nodes = `clean_label` positions. Edges = the actor's own within-actor successions
  (`a → b`, `a ≠ b`) — the same edge definition
  `transitions/build_graph.network_from_sequences` uses everywhere else in this repo, so a
  motif here is made of the same edges the shipped graphs are.
* Each edge is stamped with the target event's **within-bout elapsed time**
  (`ts − min(ts)`), which is invariant to `ts_origin` (an unknown origin is an additive
  per-bout offset). `ts_origin` is NULL for most matches, which is exactly why nothing
  absolute is ever read.
* **AA-010, absolute:** a bout enters this cell only if EVERY event carries a `ts`. No
  missing timestamp is ever defaulted. Measured: **418 of 466 gated bouts (89.7%)** qualify;
  the excluded 48 are counted in the report.
* Mirroring: one perspective per athlete (PoC-E8's contract) → 836 bout perspectives. Every
  interval clusters on the BOUT, never the perspective.

## Motifs, and the deviation from the paper — stated, not buried

Motif identity = the ordered triple of edges after **first-appearance node relabelling**
(`a→b, b→c, c→a` and `x→y, y→z, z→x` are one motif). Those are Paranjape's equivalence
classes — their 6×6 grid drawn — under our own numbering, because nothing here
cross-references their figure.

**These are NOT induced temporal motifs.** Paranjape requires the k edges to be consecutive
among all edges touching the motif's nodes; this counts every ordered k-tuple inside the
window, which is a superset. That is acceptable HERE and only here: the counts are used as
predictive features and are always compared against themselves under a different window, so
the same superset sits on both sides of a paired difference and cancels. It would not be
acceptable in a published motif census, and this cell publishes none.

k = 3 edges, ≤ 3 nodes (the paper's 3-node/3-edge family). Per row, only the motifs that
**complete at the current event** are counted — computed as `count(window) − count(window
minus its last edge)`, which is exactly the motifs involving the last edge.

## δ, chosen from the marginal before the run

Measured read-only on the ts slice: own-actor edges are **24 s apart at the median**, 62 s at
p75, 135 s at p90. The share of positions whose δ window holds the 3 edges a motif needs is
**24% at δ=30 s, 42% at 60 s, 58% at 120 s, 69% at 240 s**.

* **Primary: δ = 120 s** — the smallest window on the grid where a majority of rows can carry
  a motif at all. Picked from the coverage marginal, not from any AUC.
* Sensitivity: δ ∈ {60, 240} s, reported, never the verdict.

## The index-window control

Width = **the mean number of own-actor edges inside a δ-second window, measured on TRAIN**.
Matching on the mean rather than the median is deliberate: the window-size distribution is
heavily right-skewed (median 3, max 20 at δ=120 s), and matching the median would hand the
control a systematically narrower window than the δ arm actually uses.

**The match is imperfect and the residual runs AGAINST the hypothesis.** A fixed-width index
window covers every position with enough history; the δ window covers only positions whose
events happened close enough together. The index arm therefore gets MORE chances to carry a
motif, which makes a δ win harder, not easier. Both arms' motif coverage is printed so the
asymmetry is visible rather than argued about afterwards.

## Rows, models, criterion

Rows are **PoC-E8's `eval_rows`, unchanged** — every held-out event whose role is `you`,
labelled with PoC-E4's finish label (a landed submission by the same actor within the next
k=5 events). The same rows PoC-E4, E8, E9 and X3 were scored on. Row-to-event alignment is
checked with a raising guard, not assumed.

Split: **temporal only** (ADR-03), PoC-E8's `temporal_split`, most recent 25% held out,
boundary key to TRAIN. Motif vocabulary is built on TRAIN rows only.

Three models, `sklearn.linear_model.LogisticRegression` (L2, C=1.0, `max_iter=2000`,
`random_state=20260820` — fixed here so nothing is tuned after seeing an AUC):

1. **state one-hot** — the baseline, identical in construction to PoC-X3's.
2. **state + δ-temporal motif counts.**
3. **state + index-window motif counts** — the same alphabet, the same counter, an event
   window instead of a second window.

* **PRIMARY: paired ΔAUC (arm 2 − arm 3)**, percentile bootstrap with the **BOUT** as the
  resampling unit, 2 000 draws, seed 20260820.
  **ACCEPT iff the interval excludes 0 in the δ arm's favour with a non-degenerate width**
  (`hi > lo` — PoC-E9's amendment). Anything else is REJECT.
* **Secondary, reported:** paired ΔAUC (arm 2 − arm 1) — does the motif family add anything
  at all beyond the current position?

## Power gate, pre-registered

A pass reports **UNDERPOWERED and NO verdict** unless BOTH hold:

* ≥ **200 held-out rows carry at least one δ-motif**, and
* ≥ **20 distinct motif ids** clear the ≥20-train-row support floor.

Below either, an interval would be measuring the bootstrap rather than the corpus. This is
PoC-E9's ADCC-arm discipline, applied to the arm that can actually run out of data here: the
median bout perspective yields only **3 own-actor edges**, which is the bare minimum a
3-edge motif needs.

## Stated limits, before the numbers

* Every timestamp in this corpus is a **human reading a video clock**. Its error is
  plausibly of the same order as the shortest windows tested. A null therefore bounds what
  THIS corpus's timestamps can support; it does not prove grappling is timeless.
* The last event's `ts` is a lower bound on the bout's duration (PoC-E9's stated limit,
  inherited) — irrelevant here, because nothing in this cell reads a bout's end.
* `successful` is present on 28.9% of events; absent reads as `False`, so the finish label's
  positives are undercounted — identically for all three arms.
* **A null is the expected outcome**, registered as such: PoC-E4 measured that second-order
  memory LOSES at label level, PoC-E9 that the first-order kernel is not the defect, and
  PoC-X3 that mined subsequences add nothing. A motif is a richer object than a subsequence,
  but the prior from inside this repository is that it fails too. Saying so in advance is
  what stops the null from being re-read afterwards as a disappointment.
