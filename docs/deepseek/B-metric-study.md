# Deepseek QA — sanity-check the metric value study

`analysis/metric_evaluation.py` correlates every generated metric against outcomes,
controls for the data-volume confound (`n_matches`) via partial correlation, ranks by
RandomForest importance, and recommends Keep/Refine/Merge/Remove → `docs/metric_value_report.md`.

Your job: confirm the study is methodologically sound and the verdicts follow from the numbers.

## Inputs (attach)
- `analysis/metric_evaluation.py`
- `docs/metric_value_report.md`

## Checks
1. **No hidden leakage.** Every feature marked ⚠ must be genuinely derived from the
   secondary outcome it's flagged against (PtV family → avg_ptv; attacker/defender →
   positional_dominance; grown_elo → rank_elo). Flag any UNflagged feature that is
   actually derived from an outcome (would inflate its correlation).
2. **Partial correlation is real.** Confirm `pr·` is a true partial correlation
   (residualize feature and outcome on `n_matches`, then correlate) — not just the raw
   number. Confirm recommendations use `pr·`, not `r·`.
3. **Confound direction.** For the graph-size metrics (`n_edges`, `n_nodes`,
   `n_dilemmas`, `turning_points`), `pr·rankELO` must be ≤ `raw·rankELO` (controlling
   for volume can only remove shared variance). Flag any where partial > raw.
4. **Merge calls justified.** Each **Merge** must have |r| ≥ 0.85 with a sibling that has
   higher RF importance. Spot-check `mean_defender`→`mean_attacker` (|r|=1.0),
   `mean_control`→`mean_ptv`, `n_nodes`/`n_dilemmas`→`n_edges`.
5. **Remove call.** `mean_reward_risk` is Removed on all-NaN correlations — confirm the
   aggregate is genuinely degenerate (no variance across athletes), not a bug in how it's
   pulled from `node_centralities`.

## Output
`<check#>: PASS|FAIL — reason`. Flag any verdict the numbers don't support, and any
metric that should be Keep/Remove but isn't (or vice versa).
