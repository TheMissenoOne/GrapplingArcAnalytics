# docs/research — analytics literature cross-reference

Produced 2026-08-23 on branch `claude/analytics-literature-review-dexomn`: a survey of
every data-analysis/statistics technique implemented across the five repos
(GrapplingArcAnalytics, GrapplingArcApp, BracketAnalysis, the GrapplingArc public
site, GrapplingArcWeb), cross-referenced against the academic literature, with ranked
gaps and concrete PoC plans.

| Doc | What |
|---|---|
| `01_LITERATURE_REVIEW.md` | Per-domain review: what we implement, what the field knows, verdicts. Start here. |
| `02_GAPS_AND_OPPORTUNITIES.md` | Ranked gap list (validity threats → algorithm upgrades → presentation), each with a disposition: FIXED on this branch, PoC-planned, or flagged for an owner decision. |
| `03_POC_PLANS.md` | Implementation plans. E-series = established methods (log-loss harness, shrinkage, Leiden, WHR, PtV calibration, similarity v2, archetype k, effectiveness). X-series = experimental spikes (athlete PageRank, Infomap, sequence mining, ST-GCN, bracket DP, GNN suggestion) with kill criteria. |
| `04_BIBLIOGRAPHY.md` | Every paper, with links and which are already cited in code. |
| `05_EXTERNAL_POC_REVIEW.md` | Verification of the owner-commissioned external (GPT) PoC analysis: two claims verified as real defects and fixed (reward-risk CI denominator; cross-graph SVD similarity), one adopted as PoC-E8 (interaction graph), the rest cross-checked claim by claim. |
| `poc/` | Executed PoC results: `e0.md` (generated tables) + `e0_notes.md` (reading). |

Sibling-repo docs written in the same pass (each points back here):
`GrapplingArcApp/docs/research/ANALYTICS_LITERATURE_REVIEW.md` ·
`BracketAnalysis/docs/RESEARCH_NOTES.md` · `GrapplingArc/docs/RESEARCH_NOTES.md` ·
`GrapplingArcWeb/docs/RESEARCH_NOTES.md`.

Code changes that landed with this review (same branch): mirror-fold consistency in
`_uniform_test`/`_year_series`, the radar usage interval routed through the coverage
gate (`scripts/bracket_export.py` + tests), `analysis/graph_embed.py` walk path
repaired/seeded and the fighter vector made real (+ tests), and every unresolvable
citation in `analysis/` replaced with verified identifiers or honestly re-labelled as
this repo's own choice.
