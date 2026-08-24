# External PoC review (GPT) — verification and assessment

Date: 2026-08-24. The repository owner had an external LLM (GPT) run seven PoC
notebooks over a privacy-reduced slice of their own app export (27 rounds / 136
events, 2026-08-11→20) and deliver a results PDF plus an interpretation memo.
This document is the independent verification: each claim checked against this
codebase and against our own review (`01`–`04`), with the resulting actions.
The slice is committed (owner-consented) as
`data/fixtures/user_export_recent_slice.json` (`analysis/poc/fixtures.py` — read
its provenance caveat).

## Overall verdict

The external analysis is **good work, honestly framed** — it repeatedly
downgrades its own findings to "demonstration, not evidence," publishes a
negative result as its most useful outcome, and two of its concrete code claims
verified against this repo as real defects. Its main weaknesses are (a) it
reconstructed "current-like" behaviour from an export rather than reading the
code, so two claims mis-describe what production actually runs, and (b) every
number rides on 27 rounds of one athlete's success-heavy (92.9%) self-logged
data, so none of the quantitative results transfer — a limit the memo itself
states.

## Claim-by-claim

| # | Claim | Verdict | Evidence / action |
|---|---|---|---|
| §3 | **P0: reward-risk CI uses `occ` while the point estimate's population is `denom`** (successor-bearing appearances) | **VERIFIED — real defect, now FIXED** | `network_metrics.reward_risk_with_ci` had `denom = occ`; `transitions/build_graph.py` counts `reward`/`risk` only over successor-bearing appearances. Fixed: trials = node `denom`, gate on `denom`, both counts in the row; regression test with an occ=5/denom=2 node (`tests/test_network_metrics.py`). |
| §7 | **Independent-SVD cross-graph similarity is coordinate-unidentifiable** (sign flips swing the score −0.85..+0.85 with within-graph geometry unchanged) | **VERIFIED — real, now QUARANTINED** | `graph_embed.fighter_embedding_similarity` cosined shared-node rows across two independently fitted SVD spaces. Zero production callers; it now raises with the explanation, and `walk_based_fighter_vector` is marked within-graph-only. Crucial nuance the memo could not know: the *shipped* "grapples most like" uses mpnet — one shared semantic space — which does **not** have this defect. The defect lived in the experimental structural path only. |
| §1 | **ActionFlow (within-actor) and Interaction (actor-aware) graphs answer different questions; adopt both** | **DIRECTIONALLY RIGHT, adopted as PoC-E8** | Our builder is deliberately within-actor (`build_graph.py:43-48`) and captures interaction as edge *metadata* (`reactions`) plus PtV's risk term — but a `YOU:Turtle → OPP:Back` edge genuinely cannot exist as topology, and it occurs 3× in this very slice. Caveat the memo missed: on the competition corpus 43.9% of bouts have uninformative actors (`attribution.py`), so the interaction graph is an **app-data-first** design; on app data actors are structurally reliable (you/partner). Plan: PoC-E8 in `03_POC_PLANS.md`. |
| §4 | **"Replace greedy Path-to-Victory with a long-horizon value model"** | **HALF-MOOT — naming, not architecture** | Production PtV (`analysis/path_to_victory.py`) *is* already a discounted absorbing/value model (γ=0.8). The greedy walk is the separate `network_metrics.route_to_submission` (max_steps=6). The live action item is the memo's rename point: never present the greedy route under the PtV name, and audit which one `insights.py`/exports surface. Folded into PoC-E4's scope. |
| §2 | **EB shrinkage is right, but the `successful` flag is not a clean Bernoulli trial** (selection into logging; success means different things per event type; 92.9% positive) | **AGREE — sharpens PoC-E1** | Matches our review (undefined-means-landed; self-logged vs event-coded measurement processes). PoC-E1 gains a precondition: define per-event-type success semantics before renaming any posterior "skill". Their Spearman 0.38 between `computedElo` and EB success ("different constructs") is over-read at n=18 shared labels (CI ≈ ±0.45) — direction fine, magnitude noise. |
| §5 | **Walk-forward: the historical base rate beat difficulty/intensity logistic models** | **CONVERGENT, but n=17** | Same shape as our PoC-E0 first run (baselines are strong; sophistication must pay rent). At 17 held-out predictions the 0.512-vs-0.60 gap is noise — the correct reading is "no evidence the covariates help," not "they don't." Their recommendation to persist pre-round `expectedScore` is exactly our App-side E0 twin. |
| §6 | **Louvain vs greedy modularity: same quality on this graph; Leiden/Infomap not run; benchmark, don't pre-decide** | **AGREE — matches PoC-E2's framing verbatim** | Our plan already treats Leiden as a measured accept/reject with pre-registered criteria, not a migration. Their seed-stability observation (mean ARI 0.95, min 0.56) is a useful instrument to add to the E2 report. Note their comparison ran on a 37-node graph — too small to expose the resolution limit either way. |
| §8 | **Sequence motifs: strong descriptively (Montada→Costas→Mata-Leão ×8), nothing survives BH (139 motifs, best q≈0.74)** | **CONVERGENT** | The pattern-level twin of our `decision_criteria` null (118 triads, 0 survive). Confirms PoC-X3's expectation: descriptive tactical identity now, statistical outcome claims only at much larger n. Their three-tier language (frequent / descriptively enriched / statistically supported) is worth adopting in any insight generator. |

## What the external analysis got right that we should keep

- **The three-tier claim language** (frequent / enriched / supported).
- **Publishing the baseline win as the headline** — methodological restraint as a
  product principle, matching ADR-03.
- **The interaction-graph framing** — the one genuinely new architectural idea
  relative to our own review, and its strongest contribution.

## Where to be skeptical

- All quantities: one athlete, 10 days, 27 rounds, 92.9% success-positive,
  transcribed by an LLM from chat text (the fixture's own caveat says so).
- Notebooks were not delivered — nothing is reproducible from the PDF (page
  images only). Both verified defects were therefore re-established from *our*
  code with *our* tests, which is the durable form of the finding.
- "Current-like" reconstructions ≠ the current code (see §4 above; also §6
  compares against greedy modularity, which is the `athlete_systems` legacy path,
  not the shared Louvain detector).

## Actions taken / planned

1. **FIXED** — reward-risk CI denominator (P0), with regression test.
2. **QUARANTINED** — `fighter_embedding_similarity` (raises; docstring explains),
   `walk_based_fighter_vector` marked within-graph-only; sign-flip trap pinned by
   test.
3. **COMMITTED** — the fixture + loader (`analysis/poc/fixtures.py`) with
   provenance and LGPD notes.
4. **PLANNED** — PoC-E8 (interaction graph, app-data-first) in `03_POC_PLANS.md`;
   PoC-E1 precondition (success-flag semantics); PoC-E4 rider (greedy-route
   naming audit); E2 gains the seed-ARI instrument.
