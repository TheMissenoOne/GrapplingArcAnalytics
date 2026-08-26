# System path strength — design note

**Module:** `analysis/systems_path_strength.py` · **Base graph:** the directed ActionFlow graph
(`analysis/transitions/build_graph.network_from_sequences`) · **Consumer:** `analysis/insights.py`
(→ `docs/insights/`, internal only — nothing here reaches `site/`).

Answers "how strong is the path to X inside this system", where a **system** is a member set of
the transition graph and **X** is that system's desired node. It is the multi-step
*route* question, next to `path_to_victory.py`'s multi-step *position* question.

## Definitions

| Term | Definition |
|---|---|
| **system** | Any `list[str]` of graph nodes. `constellations.detect(...).constellations[i].members` (canonical, ADR-08) and `network_metrics.detect_communities(...)` both feed it directly — this module picks no detector. |
| **desired node** | The system's goal. Default: its highest-`occ` `type == "submission"` member; no submission in the set → the weighted-degree hub (same tie-break rule as `constellations.detect._hub`). Any node may be named instead — the default is a convenience, not a claim. |
| **DESIRED** | Absorbing state, reward 1. |
| **EXIT** | Absorbing state, reward 0 — left the system, hit a dead end, or was finished by the opponent. |
| **p_desired(n)** | P(absorbed at DESIRED before EXIT \| started at `n`). |
| **expected_steps(n)** | Expected steps to absorption in *either* state. `None` where it diverges. |
| **usage(n)** | `occ(n) / Σ occ(transient members)` — this entry's share of the system's volume. Sums to 1. |
| **direction(n)** | Kernel-mass-weighted mean of `n`'s out-edge direction factors. |
| **prize** | `(1 + PtV(desired)) / 2 ∈ [0,1]` — the goal's own Path-to-Victory value, rescaled. |

## The chain

For a transient member `n`:

```
p_risk(n) = risk(n) / denom(n)                          → EXIT
(1 − p_risk(n)) · P(n → j)                              → j     (j inside the system)
                                                        → EXIT  (j outside, or n has no out-edge)
```

`P(n → j)` is the row-normalised empirical kernel over `n`'s **whole** out-neighbourhood — the
same kernel as `path_to_victory._kernel` and `weighted_pagerank` at τ=1. Mass leaving the system
is counted as leaving, never renormalised away: that is the entire reason EXIT exists as a
second absorbing state rather than the system being treated as closed.

**Risk is injected, reward is not.** `network_from_sequences` edges are *within-actor*, so the
opponent finishing you is never an out-edge of your own node — without `p_risk` the kernel
silently assumes the fighter always got another turn. Your own finish, by contrast, already is
an out-edge (`n → <submission>`), so adding `reward` on top would charge the same events twice.
`p_risk` is read via `path_to_victory._rates`, not re-derived, so the Lamas rates have one
definition across both models.

Solved by the same in-place iteration `path_to_victory.path_to_victory` uses, **not** by
inverting `(I − Q)`: a system can contain a closed cycle with no leak, which makes `I − Q`
singular. The iteration converges to the minimal non-negative solution and reports
`expected_steps = None` for the trapped nodes instead of raising.

## The composite

```
strength(n) = p_desired(n) · usage(n) · direction(n) · prize          (all four in [0,1])

system_strength = Σ strength(n) = prize · E_usage[ p_desired · direction ]
```

`Σ usage = 1`, so **the system's strength is exactly the sum of its nodes'** — the row carrying
the system is readable straight off the table, and a high-probability entry nobody uses cannot
inflate it.

For one concrete route `π`, the chained probability replaces `p_desired` and the weakest step
supplies the direction:

```
strength(π) = p_chain(π) · usage(π₀) · min_step_direction(π) · prize
```

`p_chain(π) ≤ p_desired(π₀)` by construction — one simple route can never be worth more than
every route from the same start, cycles included. Routes are enumerated as *simple* paths
(`nx.all_simple_paths`, cutoff `MAX_PATH_LEN = 6`, the `route_to_submission` horizon); the
cyclic mass is not lost, it is what `p_desired` already counted.

### Why PtV is the Score

`reward_risk` (Lamas 2024) is the one-step special case of PtV (`docs/path_to_victory.md`), and
the chain above is already a multi-step object — multiplying by a one-step balance would
re-charge the same events. PtV is the repo's established multi-step node value and is what the
graph's other consumers use.

### Why directionality never enters the probability

`edge_arrow` is a **rendering** contract, shared char-for-char with the App
(`GrapplingArcApp/src/services/directedEdges.ts` — `MIN_EDGE_ARROW`, `TWO_WAY_RATIO`). Its
constants are imported here, never redefined and never touched. An absorption probability has
to remain a measurement of what the corpus did, so directionality applies only in the composite:

| `edge_arrow(f, r)` | reading | factor |
|---|---|---|
| false | undirected — too sparse to call, or a genuine two-way exchange | 1.0 (neutral) |
| true, `f ≥ r` | the majority direction, the arrow the data draws | 1.0 |
| true, `f < r` | the *minority* direction of an edge the data calls directed the other way | `COUNTERFLOW_FACTOR` |

## Constants

| Name | Value | Provenance |
|---|---|---|
| `MIN_MEMBERS` | 2 | "a lone node is not a system" — `analysis/systems.propose_from_network` |
| `MIN_DESIRED_OCC` | 5 | `network_metrics.reward_risk_ranking` / `reward_risk_with_ci` `min_occ`. Read on `occ`, **not** `denom`: a landed finish ends the bout, so a submission node's `denom` is near-zero by construction and would gate out precisely the nodes a system aims at. |
| `MAX_PATH_LEN` | 6 | `route_to_submission.max_steps`; PtV's γ=0.8 has decayed to 0.26 by then |
| `COUNTERFLOW_FACTOR` | 0.5 | **unfitted.** `ponytail:` half credit, a keyword knob on every entry point. Ceiling = sweeping it on held-out finish prediction the way PoC-E4 swept γ (`docs/research/poc/e4.md`) |
| `_MAX_ITER` / `_TOL` | 200 / 1e-6 | same budget as `path_to_victory.path_to_victory` |
| `_MAX_PATHS_PER_ENTRY` | 2000 | Guardrail against exponential simple-path enumeration. **Per entry point, not global.** A global budget is spent entirely by whichever member sorts first — measured 2026-08-25 on the corpus's 48-member `Armbar` community, where every reported route began at `50/50 Guard` because the digit sorts before every letter. That was a ranking artefact, not a finding. |

## Gates

A gate is a **refusal to narrate, not to measure** — same convention as
`category_constellations.gate_text`. `gated` / `gate_reason` ship on the row and the numbers
ship with them.

- `system_too_small` — fewer than `MIN_MEMBERS` distinct members.
- `desired_below_occ_floor` — the goal node is seen fewer than `MIN_DESIRED_OCC` times.

## Determinism

Every iteration is over `sorted(...)`, the fixed-point solves are in-place Gauss-Seidel in that
same sorted order, and every output list has a total sort key. Same graph + same member set →
byte-identical output, in any member order
(`tests/test_systems_path_strength.py::test_output_is_deterministic_and_member_order_independent`).

## Caveats

- **Descriptive, not predictive.** No held-out evaluation. ADR-03 applies: this is a report,
  not a calibration, and no production value moves off it.
- **First-order.** The kernel is the same order-1 kernel PoC-E4 measured against order 2 on the
  raw label space; nothing here re-opens that.
- **Coarse systems produce meaningless routes, and cost the most.** Measured 2026-08-25 on the
  corpus graph: the `Armbar` community carries 48 members, its best simple route chains to
  `p_chain = 0.0069`, and it dominates the section's 5.2 s runtime over six families. A route
  through a 48-node community is not a route. Athlete-level systems (5–15 members) are where the
  path numbers say something; the corpus-level section in `insights.py` is the cheap surface,
  not the intended one.
- **Share-weighted graphs neutralise direction.** `edge_arrow`'s `MIN_EDGE_ARROW = 2` is a count
  floor, so on `transitions.normalize.athlete_balanced_category_graph` (every weight < 1) every
  edge reads "undirected" and `direction ≡ 1.0`. Honest — a share carries no sample size — but
  uninformative. Run on the unnormalised graph if direction is meant to say anything.
- **The default desired node is `occ`-based.** In Gordon Ryan's back-attack constellation that
  picks `Triangle Choke` over `Rear Naked Choke`; both are members. `occ` was chosen over
  `ok_count` because `successful` is present on only ~29% of corpus events
  (`analysis/lamas_chain.py` rule 3). Name the node explicitly when the default reads wrong.
- **`p_risk` is only as good as `actor_id`.** `docs/match_event_model.md` records 307 of 700
  corpus bouts filing every event under one athlete; in those bouts the opponent's finish is
  not attributable and `build_graph` (correctly) never charges it, so `p_risk` is a **lower
  bound** on the real leak, and `p_desired` an upper bound.

## Worked example (corpus, read-only, 2026-08-25 — 909 final matches)

Gordon Ryan's own ActionFlow graph (15 bouts with attributable own events, 45 nodes / 112
edges), `constellations.detect`, back-attack constellation (hub `Back Control`, 14 members),
desired node auto-selected as `Triangle Choke`:

```
system strength 0.4260 · prize 0.8115 · direction 0.9526 · gate —

  Back Control      usage 0.337  p 0.564  steps 2.61  dir 0.972  strength 0.1499
  Mount             usage 0.293  p 0.402  steps 2.40  dir 0.929  strength 0.0889
  Body Triangle     usage 0.087  p 0.813  steps 2.55  dir 0.929  strength 0.0533
  Rear Naked Choke  usage 0.065  p 0.564  steps 3.61  dir 1.000  strength 0.0298

  Back Control → Triangle Choke                  p_chain 0.174  strength 0.0476
  Body Triangle → Triangle Choke                 p_chain 0.571  strength 0.0403
  Back Control → Body Triangle → Triangle Choke  p_chain 0.124  strength 0.0340
  Mount → Back Control → Triangle Choke          p_chain 0.079  strength 0.0188
```

The decomposition is the point: `Body Triangle` is the *surest* entry (p 0.813) and `Back
Control` the *strongest* one (0.1499), because strength is absorption weighted by how often the
athlete is actually there. A metric that reported only `p_desired` would have named the wrong
node.

## References

Absorbing-chain machinery (absorption probabilities, expected steps, the fundamental matrix
this module deliberately does **not** invert) is textbook — Kemeny, J. G. & Snell, J. L. (1976),
*Finite Markov Chains*, Springer, ch. III. The modelling choices are the repo's own precedent:

- `docs/path_to_victory.md` — the discounted absorbing valuation this extends, and its
  xT / VAEP / EPV / hockey-Q lineage (Rudd 2011; Singh 2019; Decroos et al., KDD 2019;
  Cervone et al. 2014; Routley & Schulte, UAI 2015).
- Lamas et al. (2024), *No-gi Brazilian jiu-jitsu: a Markovian analysis of elite-level combat
  dynamics*, IJSSC, doi:10.1177/17479541231210979 — the one-step reward-risk rates `p_risk`
  reads.
- `docs/research/lamas_chain_divisions.md` §8 names "no absorbing-chain expected-steps
  arithmetic" as a declared gap. This closes it at the **systems/ActionFlow** layer; the
  twelve-state Lamas layer (`analysis/lamas_chain.py`) is a separate state space and is
  untouched.
