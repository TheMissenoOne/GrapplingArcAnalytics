# Metric Value Report

_81 athletes (≥ min matches). Report only — no metric was changed._

**Method.** Pearson/Spearman vs outcomes + RandomForest importance + pairwise-|r| redundancy (Terner & Franks 2020; Ouyang 2025 SHAP). Primary outcomes (win_rate, sub_rate, rank_elo) are external; avg_ptv, positional_dominance are derived — ⚠ marks a feature whose correlation there is tautological (leakage).

**Data-volume control.** Elite athletes have more transcribed matches, so raw correlations reward metrics that just grow with graph size. `pr·` columns are **partial** correlations controlling for `n_matches` — the de-confounded signal that recommendations are based on. Compare `r·rankELO` (raw) to `pr·rankELO` (controlled) to see the confound collapse.

| Metric | pr·win | pr·rankELO | raw·rankELO | RF imp | sibling(|r|) | Rec | Why |
|---|---|---|---|---|---|---|---|
| `grown_elo` ⚠ | -0.049 | 0.217 | 0.25 | 0.187 | `mean_defender` (0.716) | **Refine** | mixed signal (|pr|=0.05, imp=0.19) |
| `turning_points` | 0.024 | 0.639 | 0.783 | 0.150 | `n_edges` (0.895) | **Keep** | predictive after control (|pr|=0.64, imp=0.15) |
| `n_edges` | 0.102 | 0.624 | 0.809 | 0.086 | `n_dilemmas` (0.94) | **Keep** | predictive after control (|pr|=0.62, imp=0.09) |
| `mean_continuity` | -0.236 | 0.223 | 0.218 | 0.066 | `mean_pagerank` (0.788) | **Keep** | predictive after control (|pr|=0.24, imp=0.07) |
| `mean_ptv` ⚠ | -0.04 | 0.172 | 0.129 | 0.061 | `mean_control` (0.916) | **Keep** | predictive after control (|pr|=0.31, imp=0.06) |
| `mean_deviance` ⚠ | -0.117 | -0.204 | -0.207 | 0.058 | `max_ptv` (0.732) | **Keep** | predictive after control (|pr|=0.34, imp=0.06) |
| `mean_control` ⚠ | -0.104 | 0.067 | 0.067 | 0.050 | `mean_ptv` (0.916) | **Merge** | |r|=0.916 with `mean_ptv` (more important) — fold in |
| `mean_defender` ⚠ | -0.217 | -0.21 | -0.204 | 0.049 | `mean_attacker` (1.0) | **Keep** | predictive after control (|pr|=0.23, imp=0.05) |
| `mean_pagerank` | 0.017 | -0.359 | -0.41 | 0.047 | `mean_continuity` (0.788) | **Keep** | predictive after control (|pr|=0.36, imp=0.05) |
| `n_matches` | nan | nan | 0.683 | 0.047 | `n_edges` (0.674) | **Keep** | predictive after control (raw |r|=0.68, imp=0.05) |
| `mean_attacker` ⚠ | 0.217 | 0.21 | 0.204 | 0.046 | `mean_defender` (1.0) | **Merge** | |r|=1.0 with `mean_defender` (more important) — fold in |
| `mean_reward_risk` | -0.021 | -0.002 | -0.07 | 0.043 | `mean_ptv` (0.605) | **Keep** | predictive after control (|pr|=0.17, imp=0.04) |
| `mean_funnel` ⚠ | -0.088 | 0.253 | 0.295 | 0.043 | `mean_control` (0.815) | **Keep** | predictive after control (|pr|=0.25, imp=0.04) |
| `n_nodes` | 0.091 | 0.533 | 0.748 | 0.035 | `n_edges` (0.94) | **Merge** | |r|=0.94 with `n_edges` (more important) — fold in |
| `n_dilemmas` | 0.099 | 0.564 | 0.759 | 0.028 | `n_edges` (0.94) | **Merge** | |r|=0.94 with `n_edges` (more important) — fold in |
| `max_ptv` ⚠ | -0.121 | 0.171 | 0.192 | 0.004 | `mean_ptv` (0.759) | **Refine** | mixed signal (|pr|=0.38, imp=0.00) |

## Notes
- **`pr·` = partial correlation controlling for `n_matches`.** Recommendations use it, not the raw `r·`. A metric whose `r·rankELO` is high but `pr·rankELO` collapses toward 0 was only tracking data volume (elite athletes have more transcribed bouts), not skill.
- ⚠ = leakage-flagged: the metric is (near-)derived from a secondary outcome, so a high correlation there is not evidence of predictive value.
- Thresholds: redundancy |r| ≥ 0.85; low-signal |partial r| < 0.15, RF imp < 0.03 (RF includes `n_matches`, so importances are volume-adjusted).
- **Merge** = keep the more-important sibling and drop this one; **Remove** = weak on every external outcome; **Refine** = keep but reformulate (noisy/partial signal).