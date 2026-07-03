# Path-to-Victory (PtV) — design note

**Module:** `analysis/path_to_victory.py` · **Base graph:** `network_metrics.network_from_sequences`
(within-actor transition network, Lamas et al. 2024 reward-risk attrs).

## Model

Discounted Markov-reward value iteration over the empirical transition kernel:

```
v(n) = clamp₋₁¹( shaping(n) + p_reward(n)·(+1) + p_risk(n)·(−1)
                 + γ · (1 − p_reward − p_risk) · Σⱼ P(n→j) · v(j) )
```

- `p_reward` / `p_risk` = the node's Lamas one-step finish/get-finished rates
  (`reward/denom`, `risk/denom`). **γ = 0 recovers `reward_risk` exactly** — PtV is its
  multi-step generalization, per plan.
- `P(n→j)` = row-normalized within-actor edge weights (the empirical Markov kernel,
  pure-weighted; same kernel as `weighted_pagerank` at τ=1).
- Terminals: a finish absorbs at **±1** (finisher/victim) — the absorbing-state reward of
  xT (Singh 2019) and the symmetric `ΔP(score) − ΔP(concede)` of VAEP (Decroos et al.,
  KDD 2019). Submission-type nodes additionally absorb at their observed success rate
  (`ok_count/occ`): a landed finish IS the terminal, and without it edges *into* a finish
  would look worthless (the Lamas reward credit sits on the predecessor node).
- `shaping(n)` = small positional prior so positions with thin downstream data still order
  sensibly: `0.1·points(n)/4 + 0.1·(attacker_ds − defender_ds)`, reusing
  `athlete_elo._points_for_entry` (IBJJF point map, max 4) and
  `decision_space.position_decision_space` — the existing point/win logic, not a fork.
  Bounded |shaping| ≤ 0.175 so terminals always dominate (cf. potential-based reward
  shaping, Ng et al. 1999).

## γ = 0.8 (fixed)

- Guarantees convergence: the update is a γ-contraction, and the corpus graph is cyclic
  (guard ⇄ pass), so xT's undiscounted absorbing-chain iteration is not safe here;
  discounted MDP valuation follows Routley & Schulte (UAI 2015, hockey action Q-values).
- Horizon matches the game: at 0.8, a finish 3 own-steps out is worth 0.51, 6 steps out
  0.26 — consistent with `route_to_submission`'s max_steps=6 horizon and with xT's
  effective ~5-iteration lookahead.
- `ponytail:` fixed γ + fixed shaping weights; ceiling = calibrating γ against held-out
  finish prediction (VAEP-style). Both are explicit keyword args.

## Edge PtV & derived metrics

`edge_ptv(A→B) = γ·v(B)` — the signed downstream victory value bought by taking that
branch. Derived (each names its brick): continuity (out-kernel × risk half), dilemma
(≥2 out-edges with edge-PtV ≥ θ, + high-PtV subtree), momentum (cumulative signed PtV
over `Match.sequence`, 0–1 share-shaped for the existing `momentum_series` canvas),
countering (`decision_space.turning_points` credited to the flipping label),
decision funnel (out-mass share on high-PtV branches), control (mean − std of out-edge
PtV).

## ELO deviance (CF8)

`node_elo_deviance`: occ-weighted centering of v over the corpus → signed offset
`round(clamp(±150, 200·(v − v̄)))`, keyed by `node_key` (`_normalize_name`). ±150 on the
calibrated /400 logistic ≈ 70% expected score vs baseline — a strong prior for a pure
finisher, still overridable by ~4 K=40 updates. `ponytail:` scale/clamp are constants
with keyword knobs; ceiling = fitting scale to observed per-node ELO spreads.

## References

Singh (2019) *Expected Threat (xT)*; Decroos, Bransen, Van Haaren, Davis (KDD 2019)
*Actions Speak Louder than Goals (VAEP)*; Cervone, D'Amour, Bornn, Goldsberry (2014)
*EPV / POINTWISE*; Routley & Schulte (UAI 2015) *Markov Game Model for Valuing Player
Actions in Ice Hockey*; Ng, Harada, Russell (ICML 1999) *reward shaping*; Lamas et al.
(2024) *No-gi BJJ: a Markovian analysis* (already the repo's reward-risk base).
(scientific-papers MCP was down — arxiv/openalex/core all timed out 2026-07-02; citations
from established literature, re-verify IDs when the MCP is back.)
