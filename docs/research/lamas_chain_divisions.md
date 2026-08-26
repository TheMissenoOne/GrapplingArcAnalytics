# Lamas' chain — ADCC 2026 women's brackets, and the ADCC 2023-24 cycle

Runner: `analysis/lamas_chain.py` (mapping, chain, estimation) via `scripts/bracket_export.py`
— `markov_layer` → `data.json`'s `markov` key (§§2–6, the two women's bracket divisions) and
`adcc_layer` → the `adcc` key (§7, one whole ADCC qualifying cycle). Tests:
`tests/test_lamas_chain.py` and `tests/test_bracket_export.py`.

Four layers stand on one chain, and they answer four different questions off it:

| § | block | question |
|---|---|---|
| §§2–4 | `markov[div]` / `adcc.corpora[*]` — matrix, occupancy, routes, anchor | where does the action go next |
| §5 | `reward_risk` | who ACTS next — retention of the initiative |
| **§8** | **`rrb`** | who FINISHES, propagated to the end of the chain |
| **§9** | **`chain_factor`** | does the action start a RUN of her own actions |

§§8–9 are reported for all five corpora, so they sit after §7 rather than beside §5.

§§2–6 come from `data/scouting/adcc_2026_women_sequences.json` (58 bouts, 54 with events); §7
reads `matches` directly. Both regenerated **2026-08-25**; re-running the exporter reproduces
every number below.

> **Numbers move when the corpus does.** §§2–6 were first written against a 40-bout scouting
> corpus and every table in them changed when it grew to 58. What did NOT change is every
> conclusion: guard-pass still recovers under the collapsed state space (§4.1), back-control is
> still explained by re-entry (§4.2), and still nothing separates the two divisions (§5.6). One
> decimal that was flagged in advance as a coincidence duly stopped being one — see the note in
> §4.2. That is the intended failure mode of stating which digits are load-bearing.

Source paper: **Lamas, L., et al. (2024). *No-gi Brazilian jiu-jitsu: a Markovian analysis of
elite-level combat dynamics.* International Journal of Sports Science & Coaching,
doi:10.1177/17479541231210979** — 93 WSFC-2019 no-gi matches; the one peer-reviewed BJJ Markov
paper and the only external transition matrix this corpus can be checked against
(`docs/research/04_BIBLIOGRAPHY.md` §D).

**This is descriptive scouting analysis, not a pre-registered PoC.** Nothing here is a
held-out prediction, nothing here selects a production value, and no site or App metric moves
on it (ADR-03). Spread between the two divisions is *not* evidence that they play differently
— with 29 and 32 bouts, most cells overlap by construction, and where they do not the
coverage gate usually says why.

---

## 1. Method

### 1.1 The state space

Twelve states, six of them an attempt/success pair. Codes and definitions are fixed in
`analysis.lamas_chain.STATES` / `STATE_DEFS` and ship inside the export (identically in
`markov` and in `adcc`), so a renderer cannot
carry a glossary that disagrees with the mapping that produced the number.

| code | definition |
|---|---|
| `CDP` | clinch dispute — both standing, seeking the takedown or the dominant position |
| `PGD` | pull guard — sits or lies down pulling the opponent into guard |
| `SWPA` / `SWP` | sweep attempt / sweep (the bottom player inverts to top control) |
| `TKDA` / `TKD` | takedown attempt / takedown |
| `GPSA` / `GPS` | guard-pass attempt / guard pass (to side control / north-south) |
| `BTKA` / `BTK` | back-take attempt / back-take |
| `SUBA` / `SUB` | submission attempt / submission (the match ends) |

### 1.2 The mapping table, verbatim

**Type first.** The corpus's own `type` decides the four action families; only events typed
`control`, `transition` or `guard` are read at the label level, for the three states the type
vocabulary has no word for. `escape` is deliberately never label-read — admitting it would let
`escape/Back Escape` match the back-take vocabulary on the word "back".

| corpus `type` | state (absent/false → attempt) | state (`successful: true`) |
|---|---|---|
| `takedown` | `TKDA` | `TKD` |
| `sweep` | `SWPA` | `SWP` |
| `pass` | `GPSA` | `GPS` |
| `submission` | `SUBA` | `SUB` |

| state | matched on `control` / `transition` / `guard` labels containing | corpus labels it claims (whole-corpus counts) |
|---|---|---|
| `BTK` / `BTKA` | `back take`, `back control`, `hooks in`, `body triangle`, `rear body lock` | Back Control (1964), Body Triangle (87), Hooks In (18), Back Take (17), Rear Body Lock (3), Arm Drag to Back Take (2), Crab Ride to Back Take (1), Standing Back Control (1) |
| `PGD` | `guard pull`, `pull guard`, `pull half guard`, `pull closed guard` | Guard Pull (99 + 87), Pull Guard / Inversion (24 + 2), Pull Guard (4), Double Guard Pull (3), Pull Half Guard (2), Pull Closed Guard (1), Pull Guard / Sit Guard (1), Pull Guard / Inside Triangle (1) |
| `CDP` | `collar tie`, `body lock`, `front headlock`, `clinch`, `russian tie`, `twoonone`, `underhook`, `overhook`, `wrist control`, `arm drag`, `snapdown`, `snap down`, `duck under` | Front Headlock (139), Body Lock (68), Collar Tie (41), Underhook (10), Arm Drag (10), Duck Under (8), Two-on-One (7), Double Underhooks (5), Clinch (4), Russian Tie (3), Snapdown (2), Clinch Knees (1) |

Tokens are matched against the repo's own normaliser (`analysis.names._normalize_name` after
`_deaccent`) — de-accented, lower-cased, punctuation stripped, which is why `twoonone` and
`snapdown` appear in that form. **The lists were built by enumerating the corpus read-only on
2026-08-25 (339 distinct `type`/`label` pairs, 9,985 events); every entry is a label that
exists.** Ordering inside the label branch is back-take → guard-pull → clinch, so
`Arm Drag to Back Take` is a `BTKA` and not a `CDP`.

**Two measured overrides**, same device as `scripts/bracket_export.METHOD_FAMILY` — an explicit
entry beats the token list, and the token list stays for labels the table has not seen:
`control/Top Control (Body Lock)` (1) is unmapped, because a body lock held from the top on the
ground is not a standing clinch dispute; `control/Body Triangle (Bottom)` (2) is unmapped,
because it names the person *under* the body triangle, the opposite of a back-take. Both were
found by reading the census, not by a failure in production.

**Deliberate non-members**, listed so the omission is a decision and not an oversight:
Crucifix / Mounted Crucifix (34) — back-adjacent but not a back *take*; Kimura Grip (4) — a
grip that may be standing or on the ground, and `CDP` is defined as *both standing*;
`control`-typed Escape to Turtle / Turtle Position / Turtle Control (68) and every guard
posture — dwell states, see rule 1 of §1.3.

**Type-over-label collisions**, i.e. where the type rule deliberately contradicts the label's
own semantics: `takedown/Snapdown|Snap Down` (86), `takedown/Arm Drag` (203),
`takedown/Duck Under` (22), `sweep/Sweep · Back Take` (1),
`takedown/Takedown to Back Take` (1), `takedown/Takedown (Back Exposure)` (2). The corpus typed
them; the type wins.

### 1.3 Chain definition

Four rules, pre-declared in the module docstring before any number was read.

1. **Unmapped events are passed over, not broken into.** The chain links the *surviving*
   actions in bout order — the paper's chains are action-to-action, so a guard posture between
   two actions is the pause between them, not a state. 108 events (65 kg) and 76 (+65 kg) were
   passed over; the top skipped labels are `guard/Half Guard`, `control/Mount`,
   `guard/Deep Half Guard`, `guard/Closed Guard`, `control/Escape to Turtle`, i.e. exactly the
   postures and dwell states the paper's action space has no code for.
2. **Chronology is array order.** Measured: 57 of the 58 scouting bouts carry `ts` on every
   event and *none* disagrees with the array; the fifty-eighth carries no `ts` at all. Array order
   is also what `analysis/attribution.py` reads (`rule_code: consecutive_only_array_order`), so
   the two layers cannot drift apart.
3. **Self-loops survive.** `network_from_sequences` drops the A → A edge and `normalize_chain`
   folds consecutive repeats; either would delete the exact cell Lamas publishes (guard pass →
   guard pass, 0.30). Nothing is folded here. This matters — see §4.
4. **Cross-actor.** The chain is the *match's* action flow, not one athlete's. `CDP` is dyadic
   by definition and the dominance states carry their actor implicitly. The within-actor
   reading — PoC-E4's `own_transitions` convention — is kept on every transition
   (`same_actor`) and reported in the anchor, but it is not the spine: `docs/match_event_model.md`
   records **307 of 700** corpus bouts filing every event under one athlete, so `actor_id` is
   not trustworthy enough to build a matrix on. 169 of 232 transitions (65 kg) and 175 of 226
   (+65 kg) happen to be within-actor anyway.

### 1.4 Attempt vs success, and the bias this creates

`successful: true` → success code; **`false` OR ABSENT → attempt**. `successful` is present on
28.9% of corpus events (43.7% of the scouting subset, 251/574).

**Every success rate below is a LOWER BOUND**, and the distortion is not spread evenly.
`control/Back Control` carries 77 absent, 12 false and 2 true in the scouting subset, so **89
of 91 back-controls land in `BTKA`** — a position that by its own name has already been taken.
The alternative convention (reading a control *state* label as success by definition) is
defensible; it is not the one specified, and applying it per-state would make the matrix
unreadable. The caveat travels inside the export instead (`markov[div].caveats`).

### 1.5 SUB is absorbing — by the bout's result, not by the flag

The naive rule (truncate at the first `successful` submission) is **wrong on this corpus**, and
the counter-example is not hypothetical: a bout Amy Campo *lost on DECISION* carries
`submission/Knee Bar successful=true` at ts 8951 and runs for seventeen more events.
`successful` on a submission means the lock was applied, not that anyone tapped. Truncating on
the flag would have cut that bout at event 0.

So: **the chain truncates at the first `SUB` only when the bout's `win_type` is SUBMISSION.**
That drops 2 events in 6 bouts (65 kg) and 15 in 10 (+65 kg) — post-finish duplicates, the
corpus routinely logging one finish as `Tap` → `Submission` → `Triangle Choke`. A `SUB` in a
bout that ended any other way keeps its outgoing transitions; there is 1 such transition in
65 kg and 2 in +65 kg (`absorbed.sub_outgoing`), reported rather than hidden.

### 1.6 Estimation

First-order transition counts, row-normalised. Wilson 95% per cell (`analysis/stats_rigor`),
with the row's denominator. **The interval is gated on bout-cluster coverage**
(`stats_rigor.coverage`, ≥5 clusters and effective-n ≥2): a row whose transitions come from two
fights is describing two fights, so the count survives and the interval is withheld with its
`reason_code`. This is the same rule every other block in `data.json` uses
(`scripts/bracket_export.gated`).

*Why no separate cluster bootstrap on the cells.* `bootstrap_ci`'s own docstring says to gate
on `coverage` first and that with a handful of clusters it is unstable in both directions —
and only 6 of 12 rows (65 kg) and 5 of 12 (+65 kg) clear the gate at all. Where the bout is
genuinely the unit being counted, it *is* the unit: `pathways_to_sub[*].bout_rate` is
bouts-containing-the-route over bouts, which is bout-clustered by construction rather than by
resampling.

### 1.7 Bout selection, and why the block ignores the reader's filters

The bout sets are **exactly** the ones the sequence layer already uses —
`scripts/bracket_export.division_bouts` was factored out of `sequence_layer` and is now the
single definition, so the two blocks cannot describe different universes under one heading:
every corpus bout with event data that either corner belongs to. 29 bouts in 65 kg, 32 in
+65 kg; six bouts have a rostered athlete in both corners and appear in both divisions (the
fight itself sitting in two categories, not a duplicate).

The `markov` block declares `kind: category` in `SCOPES` and ignores the uniform / ruleset /
since axes, with the measurement behind the refusal shipped in `ignored_measured`: at full
division size only 8 and 7 of twelve rows clear the gate, and across the ten non-trivial
points of the uniform × since space **164 of 240 rows are refused outright** for about 980 KB
of extra payload. The reason code is `cuts_refuse_most_rows`, deliberately *not*
`gate_refuses_every_cell` — some cuts do produce estimable rows, and a reason code that
overstates its evidence is the defect this layer exists to prevent.

---

## 2. 65 kg — 29 bouts, 261 mapped events, 108 skipped, 232 transitions

Cell = row-normalised probability (count). `n` is the row denominator, `lutas` the number of
bouts contributing to it, `gate` whether the row earns an interval at all.

| from \ to | CDP | PGD | SWPA | SWP | TKDA | TKD | GPSA | GPS | BTKA | BTK | SUBA | SUB | n | lutas | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **CDP** | 0.14 (1) | — | — | — | 0.14 (1) | 0.14 (1) | — | — | **0.29** (2) | — | **0.29** (2) | — | 7 | 4 | no |
| **PGD** | 0.06 (1) | 0.06 (1) | — | **0.44** (7) | 0.06 (1) | — | — | 0.06 (1) | 0.12 (2) | — | 0.19 (3) | — | 16 | 14 | yes |
| **SWPA** | — | — | — | — | **1.00** (1) | — | — | — | — | — | — | — | 1 | 1 | no |
| **SWP** | 0.11 (1) | **0.22** (2) | — | — | — | **0.22** (2) | 0.11 (1) | — | — | 0.11 (1) | — | **0.22** (2) | 9 | 9 | yes |
| **TKDA** | 0.04 (2) | 0.02 (1) | — | 0.02 (1) | **0.31** (17) | — | 0.02 (1) | 0.02 (1) | **0.42** (23) | 0.02 (1) | 0.15 (8) | — | 55 | 7 | yes |
| **TKD** | — | — | — | 0.17 (1) | — | — | — | **0.33** (2) | 0.17 (1) | — | **0.33** (2) | — | 6 | 5 | yes |
| **GPSA** | — | — | — | 0.08 (1) | **0.38** (5) | — | **0.23** (3) | — | 0.15 (2) | — | 0.15 (2) | — | 13 | 4 | no |
| **GPS** | — | — | — | — | — | — | 0.14 (1) | — | 0.14 (1) | — | **0.43** (3) | **0.29** (2) | 7 | 6 | yes |
| **BTKA** | 0.01 (1) | — | 0.01 (1) | 0.01 (1) | **0.32** (22) | — | 0.06 (4) | 0.01 (1) | **0.37** (25) | 0.01 (1) | 0.16 (11) | 0.01 (1) | 68 | 10 | yes |
| **BTK** | — | — | — | — | — | — | — | — | **0.67** (2) | — | — | **0.33** (1) | 3 | 3 | no |
| **SUBA** | — | 0.05 (2) | — | 0.05 (2) | 0.17 (7) | 0.05 (2) | 0.02 (1) | 0.05 (2) | 0.17 (7) | — | **0.39** (16) | 0.05 (2) | 41 | 11 | yes |
| **SUB** | 0.17 (1) | — | — | — | — | — | — | — | 0.17 (1) | — | — | **0.67** (4) | 6 | 5 | yes |

**Top cells that carry an interval.** `TKDA → BTKA` 0.42 (23) is the division's signature
transition: a contested takedown attempt turns into a back exposure more often than into
anything else, including another takedown attempt (0.31). `BTKA → TKDA` 0.32 runs the same loop
backwards — the two states hold 125 of 261 mapped events between them. `SUBA → SUBA` 0.39 is
re-attempt behaviour, and `PGD → SWP` 0.44 (7 of 16, over 14 distinct bouts) is the cleanest new
cell in this division: a guard pull here leads to a sweep more often than to anything else.

### Occupancy

| state | k | bouts | share | 95% CI |
|---|---|---|---|---|
| CDP | 7 | 4 | 0.027 | withheld (`few_clusters`) |
| **PGD** | 16 | 14 | 0.061 | 0.038–0.097 |
| SWPA | 1 | 1 | 0.004 | withheld (`few_clusters`) |
| **SWP** | 16 | 14 | 0.061 | 0.038–0.097 |
| **TKDA** | 55 | 7 | 0.211 | 0.166–0.264 |
| **TKD** | 8 | 7 | 0.031 | 0.016–0.059 |
| GPSA | 13 | 4 | 0.050 | withheld (`few_clusters`) |
| **GPS** | 8 | 7 | 0.031 | 0.016–0.059 |
| **BTKA** | 70 | 10 | 0.268 | 0.218–0.325 |
| BTK | 3 | 3 | 0.011 | withheld (`few_clusters`) |
| **SUBA** | 47 | 13 | 0.180 | 0.138–0.231 |
| **SUB** | 17 | 13 | 0.065 | 0.041–0.102 |

Note `TKDA`'s share of 0.211 comes from **7 bouts** while `SUBA`'s 0.180 comes from 13. Both
clear the gate; they are not equally distributed evidence, and the `bouts` column is there so
that is visible rather than implied.

### Routes into a submission (3 actions, terminal `SUB` or `SUBA`)

| path | k | bouts | p (first-order) | bout rate 95% CI |
|---|---|---|---|---|
| SUBA → SUBA → SUBA | 8 | 2/29 | 0.152 | 0.02–0.22 |
| TKDA → TKDA → SUBA | 5 | 4/29 | 0.045 | 0.05–0.31 |
| BTKA → SUBA → SUBA | 4 | 3/29 | 0.063 | 0.04–0.26 |
| BTKA → BTKA → SUBA | 4 | 3/29 | 0.059 | 0.04–0.26 |
| TKDA → BTKA → SUBA | 2 | 1/29 | 0.068 | 0.01–0.17 |

Length 2 is deliberately not listed — it *is* the matrix's own `SUB`/`SUBA` column, and
printing it twice would let a reader take one table as corroboration of the other. The
terminal admits `SUBA` because §1.4 puts most real finishes there; requiring `SUB` would rank
"dominant submission pathways" off ten events.

---

## 3. +65 kg — 32 bouts, 258 mapped events, 76 skipped, 226 transitions

| from \ to | CDP | PGD | SWPA | SWP | TKDA | TKD | GPSA | GPS | BTKA | BTK | SUBA | SUB | n | lutas | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **CDP** | 0.08 (1) | 0.08 (1) | — | 0.17 (2) | 0.08 (1) | 0.08 (1) | — | — | 0.17 (2) | — | **0.33** (4) | — | 12 | 8 | yes |
| **PGD** | 0.14 (2) | — | — | 0.07 (1) | 0.07 (1) | 0.07 (1) | — | 0.14 (2) | — | — | **0.36** (5) | 0.14 (2) | 14 | 14 | yes |
| **SWPA** | — | — | **0.74** (14) | — | — | — | — | — | 0.16 (3) | — | 0.05 (1) | 0.05 (1) | 19 | 2 | no |
| **SWP** | **0.25** (2) | — | — | 0.12 (1) | — | 0.12 (1) | 0.12 (1) | — | — | — | **0.25** (2) | 0.12 (1) | 8 | 6 | yes |
| **TKDA** | 0.07 (2) | 0.04 (1) | — | 0.04 (1) | 0.18 (5) | — | 0.04 (1) | — | **0.39** (11) | 0.04 (1) | **0.21** (6) | — | 28 | 4 | no |
| **TKD** | — | — | — | — | — | — | **0.33** (1) | **0.33** (1) | — | — | — | **0.33** (1) | 3 | 3 | no |
| **GPSA** | — | — | — | 0.05 (1) | **0.24** (5) | 0.05 (1) | **0.24** (5) | — | 0.14 (3) | — | **0.24** (5) | 0.05 (1) | 21 | 11 | yes |
| **GPS** | — | — | — | — | — | — | **0.50** (2) | **0.25** (1) | **0.25** (1) | — | — | — | 4 | 4 | no |
| **BTKA** | 0.02 (1) | — | 0.06 (3) | — | 0.20 (10) | — | 0.08 (4) | — | **0.41** (21) | 0.02 (1) | 0.16 (8) | 0.06 (3) | 51 | 8 | yes |
| **BTK** | — | — | — | — | — | — | — | — | **1.00** (2) | — | — | — | 2 | 2 | no |
| **SUBA** | — | 0.03 (2) | 0.02 (1) | 0.02 (1) | 0.09 (5) | — | 0.07 (4) | 0.03 (2) | 0.17 (10) | — | **0.52** (30) | 0.05 (3) | 58 | 14 | yes |
| **SUB** | 0.17 (1) | — | — | — | — | — | — | — | 0.17 (1) | — | 0.17 (1) | **0.50** (3) | 6 | 5 | yes |

**Top cells that carry an interval.** `SUBA → SUBA` 0.52 (30 over 14 bouts) is the
highest-confidence cell in either matrix. `BTKA → BTKA` 0.41 and `PGD → SUBA` 0.36 (5 of 14,
over 14 distinct bouts) follow. `GPSA → {TKDA, GPSA, SUBA}` splits evenly at 0.24 each over 11
bouts — a passing exchange in +65 kg has no dominant continuation.

`SWPA → SWPA` 0.74 (14 of 19) is the clearest illustration of why the gate exists: it comes
from **2 bouts**, so it is one athlete's repeated sweep attempts wearing a division's name.
The count is published; the interval is not — and note the cell did not move at all when the
corpus grew from 40 bouts to 58. It is still exactly those two fights.

### Occupancy

| state | k | bouts | share | 95% CI |
|---|---|---|---|---|
| **CDP** | 12 | 8 | 0.047 | 0.027–0.080 |
| **PGD** | 15 | 15 | 0.058 | 0.036–0.094 |
| SWPA | 19 | 2 | 0.074 | withheld (`few_clusters`) |
| **SWP** | 8 | 6 | 0.031 | 0.016–0.060 |
| TKDA | 28 | 4 | 0.109 | withheld (`few_clusters`) |
| **TKD** | 7 | 6 | 0.027 | 0.013–0.055 |
| **GPSA** | 23 | 12 | 0.089 | 0.060–0.130 |
| **GPS** | 6 | 5 | 0.023 | 0.011–0.050 |
| **BTKA** | 54 | 8 | 0.209 | 0.164–0.263 |
| BTK | 2 | 2 | 0.008 | withheld (`few_clusters`) |
| **SUBA** | 64 | 16 | 0.248 | 0.199–0.304 |
| **SUB** | 20 | 16 | 0.078 | 0.051–0.117 |

### Routes into a submission

| path | k | bouts | p (first-order) | bout rate 95% CI |
|---|---|---|---|---|
| SUBA → SUBA → SUBA | 16 | 5/32 | 0.267 | 0.07–0.32 |
| PGD → SUBA → SUBA | 4 | 4/32 | 0.185 | 0.05–0.28 |
| BTKA → BTKA → SUBA | 4 | 2/32 | 0.065 | 0.02–0.20 |
| BTKA → SUBA → SUBA | 3 | 2/32 | 0.081 | 0.02–0.20 |
| TKDA → TKDA → SUBA | 3 | 2/32 | 0.038 | 0.02–0.20 |

`PGD → SUBA → SUBA` remains the highest-probability non-degenerate route in either division
(0.185) over four distinct bouts — one of the few readings that survived the corpus growing by
45%, which is most of the reason to trust it.

---

## 4. Anchor against Lamas 2024 — and what it settles about PoC-E4

Stated at **family level** (attempt and success collapsed), because that is how the paper
states them: "back control → submission" is not a claim about a *landed* back-take, and §1.4
would otherwise put 89 of 91 back-controls on the attempt side and compare a cell the paper
never published.

Reference values are read from `analysis/poc/e4_ptv_eval.LAMAS_PUBLISHED`, so the two runners
cannot drift.

**65 kg**

| cell | Lamas | cross | agrees | within | no-reentry |
|---|---|---|---|---|---|
| back control → submission | 0.45 | 0.183 [0.110, 0.288] (13/71) | **NO** | 0.200 | 0.302 [0.186, 0.451] **covers** |
| takedown → submission | 0.15 | 0.164 [0.092, 0.276] (10/61) | yes | 0.159 | 0.227 [0.128, 0.370] **covers** |
| guard pass → guard pass | 0.30 | 0.200 [0.081, 0.416] (4/20) | yes | 0.188 | n/a |

**+65 kg**

| cell | Lamas | cross | agrees | within | no-reentry |
|---|---|---|---|---|---|
| back control → submission | 0.45 | 0.208 [0.120, 0.335] (11/53) | **NO** | 0.205 | 0.379 [0.227, 0.560] **covers** |
| takedown → submission | 0.15 | 0.226 [0.114, 0.398] (7/31) | yes | 0.222 | 0.269 [0.137, 0.461] **covers** |
| guard pass → guard pass | 0.30 | 0.320 [0.172, 0.516] (8/25) | yes | 0.350 | n/a |

The within-actor arm (PoC-E4's convention) sits within a few points of the cross-actor reading
in every cell of both divisions, and **no agreement verdict changes between the two**.
Whichever convention the chain uses, the answer is the same; that is the most useful thing the
`same_actor` flag has to say.

### 4.1 The guard-pass cell: PoC-E4's structural explanation, confirmed

PoC-E4 measured these three cells on the **raw label vocabulary** and got 0.210 / 0.146 /
**0.079**, missing on two of three. Its report attributed the misses to state-space dilution:

> Lamas codes ~10 coarse states, our label vocabulary runs to hundreds of nodes, and a finer
> state space mechanically DILUTES every single transition probability […] the anchor cannot
> validate at face value **without a state-space mapping nobody has written**.

That mapping is what §1.2 is. Under it, guard pass → guard pass moves **0.079 → 0.200 and
0.320**, and both intervals cover the published 0.30. The dilution argument was right, and this
is the measurement that shows it — on the one cell where dilution was the whole story.

### 4.2 The back-control cell: not dilution, state re-entry

Back control → submission did **not** recover: 0.183 and 0.208 against 0.45, out of interval in
both divisions and barely moved from PoC-E4's corpus-wide 0.21. So dilution is not the
explanation there, and the matrix says what is: **0.37 and 0.41 of everything leaving a
back-control goes to another back-control event.** Our corpus logs a *held* position
repeatedly; Lamas' coding occupies the state once and moves on.

`anchor[*].no_reentry` is the diagnostic that drops same-family re-entries from the
denominator — the closest thing to his coding our events can be read into:

| cell | Lamas | 65 kg no-reentry | +65 kg no-reentry |
|---|---|---|---|
| back control → submission | 0.45 | 0.302 [0.186, 0.451] — **covers** (13/43) | 0.379 [0.227, 0.560] — **covers** (11/29) |
| takedown → submission | 0.15 | 0.227 [0.128, 0.370] — **covers** (10/44) | 0.269 [0.137, 0.461] — **covers** (7/26) |

Both divisions move from *out of interval* to *covering the published value* once state
re-entry is removed, which identifies the gap as an **event-logging convention**, not a
difference in how these athletes fight from the back.

> This section previously reported 0.333 and **0.450** on the 40-bout corpus, and read the
> +65 kg figure landing exactly on 0.45 as "a coincidence at this n". The corpus has since grown
> to 58 bouts and the figure moved to 0.379 — so it *was* a coincidence, and saying so in
> advance is the only reason that is legible now rather than embarrassing. The direction is what
> replicated; the decimal was never the finding.

The arm is undefined for guard pass → guard pass, because the paper's own cell there *is* the
re-entry (`reason_code: published_cell_is_the_reentry`). It is a diagnostic on the gap, never
a competing estimate, and it never enters a criterion.

### 4.3 The takedown cell

`takedown → submission` now agrees at face value in **both** divisions (0.164 and 0.226, both
covering 0.15) — on the 40-bout corpus +65 kg missed at 0.318 off three bouts. That row is
still gate-refused in +65 kg (4 bouts), so the agreement is not yet a division estimate; what
changed is that the disagreement did not survive more evidence, which is what a three-bout cell
should be expected to do in either direction.

---

## 5. Reward-risk per state

### 5.1 What is inherited, and what is translated

The repo already has a reward-risk convention and this does not invent a second one.
`analysis/transitions/build_graph.py` scores each node (citing Lamas et al. 2024) and
`network_metrics.reward_risk_ranking` orders by the result. Four properties, kept verbatim:

| property | `build_graph` | here |
|---|---|---|
| denominator | appearances **that have a successor** — a node that ends the sequence is out | same |
| arms | two **disjoint** rates on that one denominator | same |
| unknown attribution | "left neutral, **never charged**" — stays in the denominator, scores neither | same |
| composite | `(reward − risk) / denom`, a **difference of rates** | same |

What changes is the *event class*, not the structure. `build_graph` anchors both arms on a
**finished submission**: reward = the fighter's own next action is a landed sub, risk = the very
next event is the *opponent's* landed sub. That anchor does not survive the move to this state
space. §1.4 puts almost every real finish in `SUBA`, leaving **17 and 20 `SUB` events** per
division, so a submission-anchored numerator would sit at 0–3 for nearly every state and the
table would be measuring the corpus's `successful` coverage rather than the grappling.

Every action that survives into a Lamas chain is an *attacking* action by construction (rule 1
of §1.3 skips escapes, guard postures and dwell states). So "did the exchange advance the
acting athlete" reduces to **who acts next**:

```
reward(s) = P(next action is by the SAME athlete | appearance of s that has a successor)
risk(s)   = P(next action is by the OPPONENT     | same denominator)
score(s)  = reward(s) − risk(s)        ≡ (reward_k − risk_k) / denom
```

**Read this as retention of the initiative, not as points scored.** A state with a high score
is one the acting athlete keeps working from; a negative one hands the exchange over. That is
the Lamas reward-risk question asked in the only currency this corpus has enough of.

### 5.2 Actor noise is handled by refusal, not by a footnote

This is the **only** layer in the block that depends on `actor_id` — the matrix is cross-actor
and never reads it (§1.3 rule 4), precisely because `docs/match_event_model.md` measures the
field as uninformative in 307 of 700 corpus bouts. A bout filing every event under one athlete
would score `reward = 1.00`, `risk = 0.00` for every state in it. So bouts are refused before
they enter:

| refusal | rule | 65 kg | +65 kg |
|---|---|---|---|
| `one_sided` | `attribution.bout_flags(...)["perspective_reliable"]` — the corpus's own verdict | 1 | 3 |
| `single_actor` | the mapped chain names fewer than two athletes | **11** | **11** |
| **usable** | | **17 / 29** | **18 / 32** |

The second rule is not redundant with the first, and the numbers say why: `bout_flags` only
calls a bout one-sided at ≥6 events, so a *short* bout filed under one name passes it while
scoring reward 1.00 by construction. It catches eleven bouts in each division against the first
rule's one and three — the bigger hole by far, and it stayed the bigger hole as the corpus grew.

**The residual error this cannot fix.** The corpus's ownership convention is `actor` = the
athlete whose *game* the node belongs to, not who is winning the exchange
(`docs/match_event_model.md`): a guard node belongs to the guard player, the pass to the
passer. Every event filed against that convention flips a reward into a risk here. It is a
documented convention that entry paths have violated before, it is the single largest source of
error in these tables, and it is **not correctable from the events alone**.

### 5.3 Intervals and gating

Wilson on each arm (both are binomial over one denominator). The composite is a difference of
two rates with no closed form, so it takes `stats_rigor.bootstrap_ci` over per-appearance
values of +1 / −1 / 0 — whose mean *is* the composite — **clustered on the bout**, 2000 draws,
seeded. Everything is gated on the same bout-cluster `coverage` as the matrix cells, and below
the gate the counts survive while **every** interval is withheld, the arm's and the composite's
alike (`bootstrap_ci`'s own docstring: gate first, it is not a rescue).

Rows are ranked **estimable-first, then by score, then by the fixed state order** — a state with
denom 1 scoring +1.000 would otherwise top a table the gate exists to keep it off.

### 5.4 65 kg — 17 usable bouts, 216 scored appearances

| # | state | n | bouts | reward | risk | score | score 95% CI (bout-clustered) | gate |
|---|---|---|---|---|---|---|---|---|
| 1 | `SWP` | 8 | 8 | 1.00 [0.68, 1.00] | 0.00 [0.00, 0.32] | **+1.000** | [+1.000, +1.000] | yes |
| 2 | `BTKA` | 65 | 8 | 0.75 [0.64, 0.84] | 0.25 [0.16, 0.36] | **+0.508** | [+0.355, +0.704] | yes |
| 3 | `SUBA` | 38 | 10 | 0.74 [0.58, 0.85] | 0.26 [0.15, 0.42] | **+0.474** | [+0.067, +0.800] | yes |
| 4 | `TKDA` | 55 | 7 | 0.73 [0.60, 0.83] | 0.27 [0.17, 0.40] | **+0.455** | [+0.062, +0.773] | yes |
| 5 | `PGD` | 12 | 10 | 0.25 [0.09, 0.53] | 0.75 [0.47, 0.91] | **−0.500** | [−1.000, +0.000] | yes |
| 6 | `SUB` | 6 | 5 | 0.17 [0.03, 0.56] | 0.83 [0.44, 0.97] | **−0.667** | [−1.000, +0.200] | yes |
| 7 | `SWPA` | 1 | 1 | 1.00 | 0.00 | +1.000 | withheld | no |
| 8 | `GPS` | 5 | 4 | 1.00 | 0.00 | +1.000 | withheld | no |
| 9 | `BTK` | 3 | 3 | 1.00 | 0.00 | +1.000 | withheld | no |
| 10 | `CDP` | 7 | 4 | 0.71 | 0.29 | +0.429 | withheld | no |
| 11 | `GPSA` | 12 | 3 | 0.67 | 0.33 | +0.333 | withheld | no |
| 12 | `TKD` | 4 | 3 | 0.50 | 0.50 | +0.000 | withheld | no |

`SWP` at +1.000 over 8 bouts is the one row where the interval is degenerate for a real reason
rather than a thin one: every single sweep in this division is followed by the sweeper's own
next action. Eight bouts clears the gate, so the interval is published — but a bootstrap of a
constant is a constant, and `[+1.000, +1.000]` should be read as "no counter-example yet", not
as precision.

**The genuinely new rows are the negative ones.** `PGD` −0.500 and `SUB` −0.667 both clear the
gate now and both were refused or absent on the 40-bout corpus. A guard pull in 65 kg hands the
exchange to the opponent three times in four — which is the same fact the matrix states as
`PGD → SWP` 0.44, since the sweep that follows a pull belongs to the guard player's *opponent*
only when the pull failed. The two tables agree, and they agree because they are two views of
one thing (§5.6).

### 5.5 +65 kg — 18 usable bouts, 189 scored appearances

| # | state | n | bouts | reward | risk | score | score 95% CI (bout-clustered) | gate |
|---|---|---|---|---|---|---|---|---|
| 1 | `BTKA` | 49 | 7 | 0.82 [0.69, 0.90] | 0.18 [0.10, 0.31] | **+0.633** | [+0.490, +0.771] | yes |
| 2 | `GPSA` | 17 | 7 | 0.71 [0.47, 0.87] | 0.29 [0.13, 0.53] | **+0.412** | [+0.067, +0.750] | yes |
| 3 | `SUBA` | 49 | 10 | 0.67 [0.53, 0.79] | 0.33 [0.21, 0.47] | **+0.347** | [−0.189, +0.688] | yes |
| 4 | `CDP` | 9 | 6 | 0.56 [0.27, 0.81] | 0.44 [0.19, 0.73] | **+0.111** | [−0.333, +0.667] | yes |
| 5 | `PGD` | 10 | 10 | 0.40 [0.17, 0.69] | 0.60 [0.31, 0.83] | **−0.200** | [−0.800, +0.400] | yes |
| 6 | `SWP` | 3 | 3 | 1.00 | 0.00 | +1.000 | withheld | no |
| 7 | `GPS` | 3 | 3 | 1.00 | 0.00 | +1.000 | withheld | no |
| 8 | `BTK` | 2 | 2 | 1.00 | 0.00 | +1.000 | withheld | no |
| 9 | `TKDA` | 28 | 4 | 0.86 | 0.14 | +0.714 | withheld | no |
| 10 | `SWPA` | 14 | 1 | 0.79 | 0.21 | +0.571 | withheld | no |
| 11 | `SUB` | 5 | 4 | 0.20 | 0.80 | −0.600 | withheld | no |
| 12 | `TKD` | 0 | 0 | — | — | — | withheld | no |

`SWPA` at +0.571 over **1 bout** is the sweep-attempt spam already visible as the matrix's
`SWPA → SWPA` 0.74 — the same single athlete seen through a second statistic, which is exactly
why a reader must not treat the two tables as independent corroboration. `BTKA` is the
division's strongest row and the narrowest interval in the document.

### 5.6 The comparison

`d65` = 65 kg, `d65p` = +65 kg (`comparison_sides` names them in the export). The `contrast` is
Agresti-Caffo on the **reward arm**, the only genuine proportion in the row, computed only when
both divisions clear the gate.

| state | 65 kg | +65 kg | delta | both estimable | reward diff (AC 95%) | p |
|---|---|---|---|---|---|---|
| `CDP` | +0.429 (n=7, 4b) | +0.111 (n=9, 6b) | +0.318 | no | — | — |
| `PGD` | −0.500 (n=12, 10b) | −0.200 (n=10, 10b) | −0.300 | **yes** | −0.150 [−0.497, +0.235] | 0.652 |
| `SWPA` | +1.000 (n=1, 1b) | +0.571 (n=14, 1b) | +0.429 | no | — | — |
| `SWP` | +1.000 (n=8, 8b) | +1.000 (n=3, 3b) | +0.000 | no | — | — |
| `TKDA` | +0.455 (n=55, 7b) | +0.714 (n=28, 4b) | −0.260 | no | — | — |
| `TKD` | +0.000 (n=4, 3b) | — | — | no | — | — |
| `GPSA` | +0.333 (n=12, 3b) | +0.412 (n=17, 7b) | −0.079 | no | — | — |
| `GPS` | +1.000 (n=5, 4b) | +1.000 (n=3, 3b) | +0.000 | no | — | — |
| `BTKA` | +0.508 (n=65, 8b) | +0.633 (n=49, 7b) | −0.125 | **yes** | −0.062 [−0.208, +0.093] | 0.569 |
| `BTK` | +1.000 (n=3, 3b) | +1.000 (n=2, 2b) | +0.000 | no | — | — |
| `SUBA` | +0.474 (n=38, 10b) | +0.347 (n=49, 10b) | +0.127 | **yes** | +0.063 [−0.131, +0.248] | 0.686 |
| `SUB` | −0.667 (n=6, 5b) | −0.600 (n=5, 4b) | −0.067 | no | — | — |

**The reading: nothing separates the two divisions.** Three states now clear the gate on both
sides — one more than on the 40-bout corpus — and all three contrasts cover zero comfortably
(p = 0.652, 0.569, 0.686). The eye-catching deltas (`SWPA` +0.429, `CDP` +0.318, `TKDA` −0.260)
all sit on refused cells. Per §0, spread between the two matrices is not evidence that the
divisions play differently, and this is the clearest illustration in the document: 45% more
evidence bought a third comparable state and moved no verdict.

**What does survive, in both divisions and with an interval:** every gated state is positive.
Attacking actions in this corpus are followed by the *same* athlete's next action roughly 70–78%
of the time. `BTKA` is the most reliable of them (+0.434 [+0.333, +0.615] and +0.568 [+0.467,
+0.750], the two narrowest intervals in either table) — once someone gets to the back, they keep
working there. That is consistent with the matrix's `BTKA → BTKA` 0.37 / 0.41 and with §4.2's
finding, and it is the same underlying fact seen three ways, not three findings.

The one thing that is NOT uniformly positive any more, and this is new evidence rather than a
rewording: **`PGD` is negative in both divisions** (−0.500 and −0.200), and gated in both. A
guard pull hands the exchange over more often than it keeps it. On the 40-bout corpus `PGD` was
+0.000 and +0.333 with only one side gated, so the sign flipped when the evidence grew — which
is exactly the kind of row that should not have been read before the gate passed, and was not.

---

## 6. Caveats (these ship inside the export, `markov[div].caveats`)

The `adcc` corpora carry all of these plus three of their own — see §7.1–7.2 and
`adcc.corpora[*].caveats`.

1. **`successful` is present on 28.9% of corpus events; absent reads as attempt.** Every
   success rate here is a floor, and the distortion is uneven — 89 of 91 back-controls fall in
   `BTKA`.
2. **`SUB` absorbs by the bout's result, not by the flag** (§1.5). `sub_outgoing` counts the
   submissions that were locked and escaped: 6 in each division.
3. **Unmapped events are passed over** (§1.3 rule 1); `n_events_skipped` and `skipped_top` say
   how much of the stream that is — 108 of 369 and 76 of 334.
4. **`Front Headlock` is the largest unresolvable ambiguity in the mapping.** At 139 corpus
   events it is the second biggest contributor to `CDP`, and it can be standing *or* over a
   turtled opponent. The corpus does not record the distinction, so the inclusion cannot be
   settled from the data as it stands.
5. **The matrix is cross-actor** (§1.3 rule 4); the anchor reports both conventions because the
   published cells are same-athlete statements.
6. **First order, no smoothing.** A low-`n` cell has a wide interval by construction, and below
   the bout-cluster gate no interval is published at all.
7. **29 and 32 bouts.** Do not read a difference between the two matrices as a difference
   between the divisions unless the intervals say so — and most of them do not.

Reward-risk carries five more of its own (`markov[div].reward_risk.caveats`), all of §5: it is
the layer standing most directly on `actor_id`; the guard/pass ownership convention is its
largest uncorrectable error; a terminal appearance is out of the denominator; unknown
attribution is neutral and never charged; the composite's interval is a bout-clustered bootstrap
and is withheld entirely below the gate.

`rrb` carries seven of its own (`…rrb.caveats`, all of §8) and `chain_factor` five
(`…chain_factor.caveats`, §9) — including the two that govern how either may be read at all:
the absorbing evidence is four to six bouts per corpus and zero in one of them (§8.4), and
propagation flattens the balance to near zero in eleven of twelve states because the chain mixes
faster than it absorbs (§8.6).

## 7. The ADCC 2023-24 cycle — the same machinery on a different corpus

Everything above is scoped to sixteen women across two bracket divisions. This section runs the
identical code — `markov_block` and `reward_risk_comparison`, unchanged — over one whole ADCC
qualifying cycle, read straight from `matches`: **every division, both sexes, absolute
included.** Exporter: `scripts/bracket_export.py:adcc_layer` → `data.json`'s `adcc` key.

**It is a different population, not a cut of the one above.** `SCOPES["adcc"]` says `global`
where `markov` says `category`. Comparing `BTKA` here against `BTKA` in §2 compares 86 bouts of
mixed-division ADCC against 29 bouts of one women's bracket.

### 7.1 Corpus selection

Three corpora, from the tags in `matches.event`. The SQL is deliberately dumb
(`event ilike '%adcc%'`); every decision lives in the pure, tested `adcc_corpus_of`.

| corpus | bouts w/ events | tags |
|---|---|---|
| **Trials 2023-24** | 53 | EC-2023 (16), WC-2024 (8), EU-2024 (6), SA2-2024 (6), Asia-2024 (5), SA1-2024 (5), Asia-2023 (4), EU-2023 (3) |
| **ADCC 2024** (Worlds) | 33 | `ADCC 2024` (30), `ADCC World Championship` (2), `ADCC` (1) |
| **Ciclo completo** | 86 | the two above, pooled |

`ADCC Trials 2023 East Coast` covers finals **and** semis — one tag, both dumps, so the semis
are included by construction rather than by a second rule that could drift.

**The ambiguous `ADCC` tag: decided, not assumed.** It holds 18 bouts spanning 2017–2024, of
which exactly **one** in-cycle row carries a sequence — Gordon Ryan × Felipe Pena 2024, the
Worlds superfight, 22 events. `ADCC World Championship` holds three, of which two are 2024
(Ethan Crelinsten, 7 and 5 events). Both tags *name the World Championship*, so they resolve to
the Worlds bucket, admitted **only** when the row's own `year` is 2023 or 2024. Checked for
double-counting against `ADCC 2024` by canonical athlete pair: **zero duplicates**, so these
three bouts are additions rather than copies. `ADCC WC Trials` is deliberately absent from the
undated list — it names a *trials*, so admitting it by year would file a qualifier under the
World Championship (its one row is 2017 with no events, so this costs nothing today and is
correct by name rather than by luck).

**Excluded, and counted** (`adcc.excluded`): `ADCC 2022` (53 bouts with events),
`ADCC Trials 2022 South America` (6), `ADCC` at 2017/2019/2022 (4),
`ADCC World Championship` 2022 (1). That is the previous cycle, refused by the year gate.

### 7.2 ⚠️ The two corpora were not annotated the same way

**This governs how everything below may be read, so it ships as numbers** (`adcc.annotation`),
not as a footnote.

| corpus | events | `successful` present | `successful: true` |
|---|---|---|---|
| Trials 2023-24 | 466 | **100.0%** | **79.8%** |
| ADCC 2024 | 457 | 34.8% | 12.0% |
| Ciclo completo | 923 | 67.7% | 46.3% |

Per type it is starker. The trials dumps mark `guard` 68/68 and `escape` 17/17 successful; the
ADCC 2024 dumps mark `guard` 0/70, `pass` 0/17, `transition` 0/20, `control` 3/139. Per tag,
every trials event runs 70–100% `true` while `ADCC 2024` runs 13% and `ADCC` runs 0%. These are
two annotation conventions in one table, not two ways of grappling.

**What it invalidates.** Rule 3 (§1.4) sends `successful is True` to the success state and
everything else to the attempt state, so the attempt/success split *tracks the dump batch*:

| | Trials | Worlds |
|---|---|---|
| takedowns | TKD 40 / TKDA 24 | TKDA 75 / TKD 7 |
| back-takes | BTK 26 / BTKA 4 | BTKA 93 / BTK 2 |
| sweeps | SWPA **0** | GPS **0** |

Reporting that as "qualifiers land more takedowns than the Worlds" would be reporting the dump
batch. `annotation.state_split_comparable` is **false**, in the export, where a renderer can
see it.

**What only *looks* like it survives.** `reward_risk` is immune in its *question* (who acts
next) but **not in its partition**: its rows are keyed by state, and the annotation decides
which events land in which state. `TKDA` in the trials corpus means "a takedown explicitly
marked unsuccessful" (21 appearances beside 16 `TKD`); in the Worlds corpus it means "a
takedown, mostly unmarked" (70 beside 3). Those are different subsets under one label. The
comparison duly finds `TKDA` −0.143 vs +0.486 excluding zero (p=0.015) and `SUBA` +0.143 vs
+0.818 (p=0.029) — **which is exactly what the annotation gap predicts**, since a takedown
*known* to have failed should hand the exchange over more often than an unmarked one. Those two
p-values are evidence about the dumps. `annotation.reward_risk_cross_corpus_comparable` is
**false** too.

**What genuinely survives across corpora:** the family-level `anchor`, which collapses attempt
and success and therefore cannot see the annotation at all. Within a single corpus, everything
is comparable as usual.

### 7.3 Ciclo completo — 86 bouts, 593 mapped, 270 skipped, 508 transitions

The headline corpus, and the only one where **all twelve states clear the bout-cluster gate**.

| from \ to | CDP | PGD | SWPA | SWP | TKDA | TKD | GPSA | GPS | BTKA | BTK | SUBA | SUB | n | lutas | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **CDP** | 0.08 (5) | 0.20 (12) | 0.02 (1) | — | **0.23** (14) | **0.26** (16) | — | — | 0.05 (3) | — | 0.15 (9) | 0.02 (1) | 61 | 39 | yes |
| **PGD** | 0.18 (4) | — | 0.05 (1) | 0.05 (1) | 0.18 (4) | 0.05 (1) | 0.05 (1) | 0.09 (2) | 0.05 (1) | 0.09 (2) | 0.14 (3) | 0.09 (2) | 22 | 20 | yes |
| **SWPA** | **0.23** (3) | — | — | — | **0.23** (3) | — | 0.08 (1) | — | 0.15 (2) | — | 0.08 (1) | **0.23** (3) | 13 | 9 | yes |
| **SWP** | 0.17 (2) | — | — | — | — | **0.25** (3) | 0.17 (2) | 0.08 (1) | — | — | — | **0.33** (4) | 12 | 12 | yes |
| **TKDA** | 0.06 (6) | 0.04 (4) | 0.02 (2) | 0.01 (1) | **0.37** (36) | 0.06 (6) | 0.02 (2) | 0.01 (1) | **0.30** (29) | 0.01 (1) | 0.10 (10) | — | 98 | 24 | yes |
| **TKD** | 0.09 (3) | 0.03 (1) | — | 0.03 (1) | — | 0.18 (6) | 0.09 (3) | 0.12 (4) | 0.06 (2) | 0.12 (4) | 0.12 (4) | 0.18 (6) | 34 | 26 | yes |
| **GPSA** | — | 0.04 (1) | — | 0.08 (2) | 0.15 (4) | 0.08 (2) | 0.04 (1) | 0.04 (1) | 0.12 (3) | — | 0.08 (2) | **0.38** (10) | 26 | 21 | yes |
| **GPS** | 0.12 (1) | 0.12 (1) | — | — | — | 0.12 (1) | — | 0.12 (1) | — | 0.12 (1) | **0.25** (2) | 0.12 (1) | 8 | 7 | yes |
| **BTKA** | 0.02 (2) | 0.01 (1) | 0.04 (4) | — | **0.30** (28) | — | 0.05 (5) | — | **0.34** (32) | 0.02 (2) | 0.16 (15) | 0.04 (4) | 93 | 18 | yes |
| **BTK** | — | 0.04 (1) | — | — | — | 0.04 (1) | — | — | 0.04 (1) | **0.62** (15) | — | **0.25** (6) | 24 | 11 | yes |
| **SUBA** | 0.06 (4) | — | 0.01 (1) | 0.03 (2) | 0.11 (8) | — | 0.03 (2) | 0.04 (3) | 0.17 (12) | 0.04 (3) | **0.43** (30) | 0.07 (5) | 70 | 26 | yes |
| **SUB** | 0.09 (4) | 0.02 (1) | 0.09 (4) | 0.02 (1) | — | 0.13 (6) | 0.09 (4) | — | 0.04 (2) | — | — | **0.53** (25) | 47 | 19 | yes |

**Top cells.** `BTK → BTK` 0.62 and `SUB → SUB` 0.53 are re-entry/duplicate-logging, not
grappling (§4.2, §1.5). The substantive ones: `TKDA → TKDA` 0.37 and `TKDA → BTKA` 0.30 — the
same takedown↔back loop the 65 kg division shows, now over 24 bouts; `GPSA → SUB` 0.38 over 21
bouts, the strongest pass-to-finish cell anywhere in this document; `CDP → TKD` 0.26 and
`CDP → TKDA` 0.23 over 39 bouts, i.e. a clinch dispute resolves into a takedown roughly half
the time.

**Occupancy** (all twelve gated):

| state | k | bouts | share | 95% CI |
|---|---|---|---|---|
| CDP | 63 | 40 | 0.106 | 0.084–0.134 |
| PGD | 25 | 21 | 0.042 | 0.029–0.061 |
| SWPA | 15 | 11 | 0.025 | 0.015–0.041 |
| SWP | 14 | 14 | 0.024 | 0.014–0.039 |
| **TKDA** | 99 | 24 | 0.167 | 0.139–0.199 |
| TKD | 47 | 32 | 0.079 | 0.060–0.104 |
| GPSA | 27 | 22 | 0.046 | 0.031–0.065 |
| GPS | 13 | 12 | 0.022 | 0.013–0.037 |
| **BTKA** | 97 | 18 | 0.164 | 0.136–0.195 |
| BTK | 28 | 12 | 0.047 | 0.033–0.067 |
| **SUBA** | 80 | 29 | 0.135 | 0.110–0.165 |
| **SUB** | 85 | 49 | 0.143 | 0.117–0.174 |

**Routes into a submission:**

| path | k | bouts | p (first-order) | bout rate 95% CI |
|---|---|---|---|---|
| SUBA → SUBA → SUBA | 21 | 5/86 | 0.184 | 0.03–0.13 |
| SUB → SUB → SUB | 7 | 5/86 | 0.283 | 0.03–0.13 |
| BTK → BTK → SUB | 6 | 6/86 | 0.156 | 0.03–0.14 |
| BTKA → BTKA → SUBA | 6 | 5/86 | 0.056 | 0.03–0.13 |
| TKDA → TKDA → SUBA | 6 | 5/86 | 0.037 | 0.03–0.13 |

Four of five are re-entry chains. `BTK → BTK → SUB` (6 bouts) is the only one naming two
distinct actions, and it is the cycle's signature finishing route: take the back, hold it,
finish.

### 7.4 The three corpora against Lamas 2024

The one cross-corpus reading the annotation permits.

| corpus | back control → sub (0.45) | takedown → sub (0.15) | guard pass → guard pass (0.30) |
|---|---|---|---|
| Trials 2023-24 | 0.259 [0.132, 0.447] — no | **0.212 [0.122, 0.340] — yes** | **0.111 [0.031, 0.328] — yes** |
| ADCC 2024 | 0.200 [0.130, 0.294] — no | **0.113 [0.060, 0.200] — yes** | 0.062 [0.011, 0.283] — no |
| **Ciclo completo** | 0.214 [0.149, 0.296] — no | **0.152 [0.100, 0.222] — yes** | 0.088 [0.030, 0.230] — no |
| 65 kg (§4) | 0.183 — no | 0.164 — yes | 0.200 — yes |
| +65 kg (§4) | 0.208 — no | 0.226 — yes | 0.320 — yes |

**`takedown → submission` agrees in all five corpora**, and the 86-bout cycle lands on
**0.152** against a published 0.15 with the tightest interval any corpus here has produced
([0.100, 0.222], 20/132). That is the strongest external agreement in this document.

`back control → submission` misses in all five and misses the same way, and the `no_reentry`
diagnostic explains it the same way everywhere: 0.636 (Trials), 0.321 (Worlds), **0.373**
(cycle) — the cycle's interval [0.267, 0.493] covers 0.45. §4.2's conclusion replicates on a
corpus three times the size and of a different population, which is the best evidence in this
document that it is a logging convention rather than a quirk of one bracket.

`guard pass → guard pass` is the interesting split: it agrees in the women's divisions (0.200,
0.320) and in the Trials (0.111), and **misses in the Worlds corpus (0.062, 1/16)**. With
n=16 that is a thin cell, and the `GPS` state has **zero** occupancy in the Worlds corpus
because no pass there is marked successful — so this cell is annotation-bound too, and should
not be read as ADCC-level passing being less repeatable.

### 7.5 Sample size — the E9 context

PoC-E9's ADCC kernel lost for **sample** reasons at 42 train bouts. This corpus is 86 bouts
with events, and the difference is visible in exactly the place sample size should show up:

| corpus | bouts | states clearing the bout-cluster gate | reward-risk states gated |
|---|---|---|---|
| 65 kg (§2) | 29 | 8 / 12 | 6 / 12 |
| +65 kg (§3) | 32 | 7 / 12 | 5 / 12 |
| Trials 2023-24 | 53 | 10 / 12 | 10 / 12 |
| ADCC 2024 | 33 | 7 / 12 | 5 / 12 |
| **Ciclo completo** | **86** | **12 / 12** | **12 / 12** |

At 86 bouts the coverage gate stops refusing: every state earns an interval, on both the matrix
rows and the reward-risk table. That is a statement about *estimability*, not about a verdict —
E9's kernel is not re-run here and nothing above re-opens it. What this says is that the corpus
has passed the size where the honest answer to most questions was "not enough independent
fights", which is the precondition E9 lacked, not a result.

### 7.6 Reward-risk, Ciclo completo

Within-corpus, so the annotation caveat does not bite. All twelve gated.

| # | state | n | bouts | reward | risk | score | score 95% CI (bout-clustered) |
|---|---|---|---|---|---|---|---|
| 1 | `BTK` | 19 | 9 | 0.95 [0.75, 0.99] | 0.05 [0.01, 0.25] | **+0.895** | [+0.727, +1.000] |
| 2 | `GPS` | 6 | 5 | 0.83 [0.44, 0.97] | 0.17 [0.03, 0.56] | **+0.667** | [−0.200, +1.000] |
| 3 | `SUBA` | 43 | 20 | 0.74 [0.60, 0.85] | 0.26 [0.15, 0.40] | **+0.488** | [+0.158, +0.762] |
| 4 | `BTKA` | 78 | 11 | 0.73 [0.62, 0.82] | 0.27 [0.18, 0.38] | **+0.462** | [+0.255, +0.609] |
| 5 | `SWPA` | 10 | 6 | 0.70 [0.40, 0.89] | 0.30 [0.11, 0.60] | **+0.400** | [+0.111, +1.000] |
| 6 | `TKD` | 19 | 13 | 0.68 [0.46, 0.85] | 0.32 [0.15, 0.54] | **+0.368** | [−0.067, +0.765] |
| 7 | `TKDA` | 91 | 18 | 0.67 [0.57, 0.76] | 0.33 [0.24, 0.43] | **+0.341** | [−0.100, +0.605] |
| 8 | `SWP` | 6 | 6 | 0.67 [0.30, 0.90] | 0.33 [0.10, 0.70] | **+0.333** | [−0.333, +1.000] |
| 9 | `GPSA` | 19 | 14 | 0.58 [0.36, 0.77] | 0.42 [0.23, 0.64] | **+0.158** | [−0.429, +0.571] |
| 10 | `SUB` | 33 | 15 | 0.55 [0.38, 0.70] | 0.45 [0.30, 0.62] | **+0.091** | [−0.391, +0.442] |
| 11 | `CDP` | 48 | 27 | 0.54 [0.40, 0.67] | 0.46 [0.33, 0.60] | **+0.083** | [−0.200, +0.333] |
| 12 | `PGD` | 18 | 16 | 0.44 [0.25, 0.66] | 0.56 [0.34, 0.75] | **−0.111** | [−0.625, +0.400] |

Only **four** rows have intervals excluding zero: `BTK` +0.895, `SUBA` +0.488, `BTKA` +0.462,
`SWPA` +0.400. Everything else is compatible with a coin flip once the bout is the resampling
unit — including `CDP` (+0.083 over 27 bouts), which says a clinch dispute is genuinely
even-money for who acts next. That is the most defensible single sentence in this section.

**`PGD` is negative here too** (−0.111), as in both women's divisions (−0.500, −0.200). The
cycle's interval covers zero, so it is agreement in sign rather than a confirmed effect — but
three independent corpora putting guard-pull on the negative side is worth a look at footage.

Bouts refused by the actor gate: 33 `single_actor` + 13 `one_sided` = 40 of 86 usable. That
refusal rate (53%) is worse than the divisions' (17/29, 18/32) and is the main thing limiting
this table.

---

## 8. RRB — reward-risk balance by two-sided submission absorption

`reward_risk` (§5) asks **who acts next**. This asks **who finishes**, and asks it of the whole
chain rather than of one cell. Runner: `analysis/lamas_chain.rrb`, exported at
`markov[div].rrb` and `adcc.corpora[*].rrb` — the same sub-block in both, like everything else
in this document.

### 8.1 Pre-registered definitions, before any number below

Per state `s`, taken as performed by a reference athlete:

```
p_sub_own(s) = P(the bout ends in a submission BY HER          | an appearance of s)
p_sub_opp(s) = P(the bout ends in a submission BY THE OPPONENT | an appearance of s)
balance(s)   = p_sub_own(s) − p_sub_opp(s)                 ← PRIMARY relation
sub_share(s) = p_sub_own(s) / (p_sub_own(s) + p_sub_opp(s)) ← SECONDARY relation
by_next_mover(s).own / .opp = the same two probabilities, conditioned on whether the NEXT
                              action is hers or the opponent's
```

A third absorbing outcome, **no-sub end**, takes everything else, so the three sum to exactly 1
per state. It is not published as a column — it is `1 − own − opp`, and printing it invites a
reader to check arithmetic instead of reading the finding.

**Why `balance` is primary, stated before the numbers.** Not because a difference is better
statistics than a ratio, but because §5's composite is already a difference (`build_graph`'s
convention, kept verbatim), and two composites on the same page composing two arms two
different ways would make the tables silently incomparable.

**Why `sub_share` at all.** The difference cannot separate "nothing happens here" from
"something one-sided happens here": at `own = 0.05, opp = 0.01` the difference is +0.04, which
reads as nothing, and the share is 0.83, which reads as five-to-one. Both ship.

**The plain ratio is rejected** — unbounded, and undefined wherever `p_sub_opp` is zero, which
is the ordinary case in this data. The share is its bounded monotone transform `r / (1 + r)`, so
nothing is lost but the blow-up.

### 8.2 The state space, lifted by side

Lamas' twelve states carry no owner, so "her submission" has no meaning in them. Each state is
split into **(state, side)** relative to the athlete who performed the appearance we start
from — 24 transient states. A transition **keeps** the side when it is the same athlete's and
**flips** it otherwise, which is the only thing the side coordinate ever does; the sided kernel
is therefore completely determined by the 12 × 12 × 2 array of (from, to, same/switch) counts
the matrix already implies. Reading the same evidence from the opponent's side is the same
kernel mirrored, so the two rows of a state are exact mirrors of each other — asserted in
`tests/test_lamas_chain.py`, not assumed.

Every appearance contributes exactly one unit of row mass: a transition when it has a
successor, an **absorbing** transition when it is the chain's last step. That is what makes the
rows honest.

Solved as a fundamental matrix, `B = (I − Q)⁻¹R`, not iterated. `path_to_victory`'s value
iteration is the right tool when a discount makes the operator a contraction; here there is no
discount and absorption takes 5–14 actions, so iterating to a useful tolerance would cost
hundreds of sweeps per bootstrap draw for what one linear solve gives exactly.

> **Why this is a second solver and not a call into `analysis/systems_path_strength.absorption`**
> — the repo's other absorbing chain, and the first thing checked here. That one is built for
> exactly *two* absorbing states (one named `desired` at 1, one implicit EXIT taking the
> remainder), so three outcomes would mean two passes with EXIT reinterpreted on each; and it is
> a pure-Python Gauss-Seidel iteration, right for the handful of solves a systems report needs
> and wrong for the **ten thousand** this layer runs (2000 draws × 5 corpora). They agree on the
> mathematics and on the honesty rule — a third state for *did not get there* — and they should
> stay two functions.

> **`I − Q` cannot be singular here, and the reason is structural.** Every state that appears at
> all appears inside some chain, and every chain ENDS — its last step carries an absorbing
> transition by construction. So from any appearing state there is a positive-probability route,
> along the very chain the appearance sits in, to an absorbing column, and a closed recurrent
> class among the transient states is impossible. A state that does *not* appear has an all-zero
> row, which leaves `I − Q`'s row as the identity's: still non-singular, answer zero, and never
> read because such a state is refused for having no appearances.

### 8.3 The absorbing rule: the bout's result, never the finishing event

§1.5 already refused the naive rule for the *matrix*. This layer needs one thing more — not just
*whether* a submission finished the bout but *whose* it was — and the same principle answers it:

> **A bout absorbs into a submission iff `win_type` is SUBMISSION. The absorbing transition
> hangs on the chain's LAST step, and the side comes from `winner`.**

One sentence, two consequences, both measured (read-only, 2026-08-25):

| | measured |
|---|---|
| Finishing events filed under the **loser**, among the ADCC cycle's 24 truncated chains | **7** (6 of them in the Trials corpus) |
| …of those 7, how many are in a bout the actor gate lets through | **0** — the gate refuses exactly those bouts |
| Bouts won by submission whose chain never reached a flagged `SUB` | 2 in 65 kg, 1 in +65 kg, 2 in the Worlds corpus, 0 in the Trials |
| …how many of those the actor gate lets through | **1 in 65 kg, 1 in +65 kg** |

So the two arms of the rule do different work, and it is worth being exact about which:

- **The side arm changes no number today.** Among bouts that clear the actor gate, `winner` and
  the finishing event's `actor_id` agree in **100%** of cases, in all five corpora. The rule is
  a guarantee against the gate ever being loosened, not a repair of a current figure — and
  it is also a second, independent piece of evidence that the actor gate is catching the right
  bouts.
- **The unflagged-finish arm moves the tables.** It is what takes +65 kg from **4 to 5**
  absorbing bouts, i.e. from the refused side of the coverage gate to the estimable side. One
  bout decides whether that division publishes an interval at all.

A submission win with no recorded `winner`, or a chain whose last step has no actor, does **not**
absorb. A missing fact is never read as a finish.

### 8.4 Estimation, and the third gate

Two gates already exist in this document. RRB adds one, and it is the binding one.

| gate | unit | what it refuses |
|---|---|---|
| row coverage (§1.6) | bouts contributing **appearances** of `s` | the row's interval |
| **absorbing-bout coverage** | bouts that actually **absorb into a submission** | **the whole corpus's intervals** |
| zero absorbing bouts | — | **the point estimates too** |

The middle one is the layer's own, and it exists because nothing else in the report would have
caught the failure. A row can rest on ten bouts' worth of appearances while every gram of its
absorbing mass traces back to the same four finishes; the row-coverage gate sees ten clusters
and says "estimable". Each bout contributes exactly one ending, so the absorbing counts are ones
and `effective_n` is the bout count — the honest shape of that evidence.

The bottom one matters because **zero is not a measurement**. Twelve rows of `0.000` read as "we
measured no submission risk here"; the truth is "we measured nothing". Where there are no
absorbing bouts every probability ships as `null` with `reason_code: no_absorbing_bouts`.

Intervals are a **bout-clustered percentile bootstrap**: whole bouts are resampled with
replacement, the kernel and the absorbing block are rebuilt from the resample, and the chain is
re-solved — 2000 draws, seeded (`random.Random(20260820)`, stdlib rather than numpy's Generator,
which does not promise a stable stream across versions). Draws in which a state never appears
carry no information about it and are dropped; below 100 surviving draws the interval is
withheld like any other ungated one.

**Where each corpus lands:**

| corpus | usable bouts | absorbing bouts | verdict |
|---|---|---|---|
| 65 kg | 17 / 29 | **4** | **REFUSED** — `few_absorbing_bouts`; point estimates only |
| +65 kg | 18 / 32 | **5** | estimable (by one bout, and by the unflagged-finish arm of §8.3) |
| Trials 2023-24 | 28 / 53 | **6** | estimable |
| ADCC 2024 | 12 / 33 | **0** | **NO ESTIMATE** — `no_absorbing_bouts`; every probability `null` |
| Ciclo completo | 40 / 86 | **6** | estimable |

That ADCC 2024 row is not a thin cell, it is an empty one: the actor gate removes 21 of 33
bouts, and none of the twelve that survive was won by submission. It is the single most
important thing this section has to say about that corpus.

### 8.5 Ciclo completo — the only corpus where all twelve rows are estimable

`n` is appearances, `lutas` the bouts behind them, `term` how many of those appearances are the
chain's last step. Intervals are bout-clustered.

| state | n | lutas | term | p_sub_own | p_sub_opp | balance | balance 95% CI | share | ações até absorver |
|---|---|---|---|---|---|---|---|---|---|
| CDP | 49 | 27 | 1 | 0.061 | 0.063 | −0.002 | [−0.009, +0.007] | 0.493 | 12.09 |
| PGD | 19 | 16 | 1 | 0.063 | 0.068 | −0.005 | [−0.037, +0.013] | 0.482 | 11.14 |
| SWPA | 10 | 6 | 0 | 0.088 | 0.064 | +0.024 | [−0.001, +0.083] | 0.578 | 12.04 |
| SWP | 7 | 7 | 1 | 0.076 | 0.076 | −0.001 | [−0.063, +0.068] | 0.498 | 9.63 |
| TKDA | 92 | 18 | 1 | 0.060 | 0.060 | +0.000 | [−0.005, +0.004] | 0.502 | 12.97 |
| TKD | 24 | 15 | 5 | 0.065 | 0.053 | +0.012 | [−0.020, +0.040] | 0.549 | 8.74 |
| GPSA | 20 | 15 | 1 | 0.072 | 0.074 | −0.002 | [−0.031, +0.039] | 0.493 | 10.85 |
| GPS | 8 | 7 | 2 | 0.068 | 0.042 | +0.026 | [−0.000, +0.080] | 0.619 | 8.12 |
| BTKA | 82 | 11 | 4 | 0.061 | 0.058 | +0.003 | [−0.000, +0.013] | 0.511 | 12.61 |
| **BTK** | 22 | 9 | 3 | **0.096** | 0.046 | **+0.049** | **[+0.005, +0.140]** | **0.674** | 8.35 |
| SUBA | 49 | 21 | 6 | 0.063 | 0.057 | +0.006 | [−0.006, +0.030] | 0.526 | 10.58 |
| **SUB** | 48 | 23 | 15 | **0.191** | 0.072 | **+0.119** | **[+0.036, +0.242]** | **0.726** | 7.25 |

**+65 kg** (5 absorbing bouts, 6 of 12 rows gated): `SUB` +0.287 [+0.059, +0.727], `BTKA` +0.023
[+0.000, +0.123], `SUBA` +0.029 [−0.019, +0.072], `GPSA` +0.006 [−0.005, +0.020], `CDP` +0.011
[−0.003, +0.055], `PGD` −0.048 [−0.172, +0.006].

**Trials 2023-24** (6 absorbing, 10 of 12 gated): `SUB` +0.162 [+0.048, +0.335], `BTK` +0.073
[+0.008, +0.202], `GPS` +0.033 [−0.007, +0.103], everything else covering zero, and `SWP`
−0.027, `PGD` −0.011, `TKDA` −0.003, `CDP` −0.002 on the negative side.

**65 kg** publishes twelve point estimates and no interval at all. For the record, its ordering
is the same shape: `SUB` +0.187, `BTK` +0.069, `GPS` +0.065, `SUBA` +0.043, `SWP` +0.042, down to
`PGD` −0.029 and `TKD` −0.005. **Nothing in that list may be read as a division estimate.**

### 8.6 The reading, and the honest disappointment in it

**Propagation flattens the signal, and that IS the finding.** Outside `SUB`, **no state's
balance exceeds ±0.074 in any corpus** — the extreme is `BTK` at +0.073 in the Trials — and 7 to
10 of the eleven remaining states in each corpus sit inside ±0.03. This is not an implementation
defect and it is not noise to be filtered: it is what an absorbing chain must produce when its
**mixing time is shorter than its absorption time**. The side flips on 22%–47% of transitions,
and among the rows that clear the gate absorption takes **4.6 to 13.9 further actions**
(`expected_actions`, published per row for exactly this reason). After five or six changes of
hands the chain has forgotten whose initiative it started on, so `p_sub_own` and `p_sub_opp`
both converge on the corpus's own base finish rate and their difference goes to zero.

Read plainly: **in this corpus, the action you are in tells you almost nothing about who
eventually taps.** That is a real answer to the owner's question, and it is one the sparse
immediate cell could not have given — a submission-anchored one-step numerator would have been
0–3 for nearly every state (§5.1) and would have looked like noise rather than like mixing.

**Two states survive the flattening, and they are the same story twice.** `BTK` — a *landed*
back-take — is the **only non-SUB state whose interval excludes zero**, and it does so in both
corpora where it is estimable: +0.073 [+0.008, +0.202] in the Trials and +0.049 [+0.005, +0.140]
in the cycle, share 0.68 and 0.67. Getting to the back is the one place in this state space
where the chain does not forget. That is §4.2's finding and Lamas' published back-control →
submission 0.45 arriving through a third, independent statistic. `SUB` is the other, and it is
**circular by construction**: 15 of its 48 cycle appearances ARE the terminal step, so its value
mostly restates §1.5's truncation rule. `n_terminal` is in the export so a reader can see that
rather than take it for a finding.

**`by_next_mover` is where the one-step signal still lives**, and it behaves with almost boring
consistency: `balance | next mover is hers` sits above `balance | next mover is the opponent's`
in **12 of 12 states on the cycle, 8 of 8 in each division, and 10 of 11 in the Trials**,
counting every row where both arms exist. On the cycle: `SUBA` +0.017
[+0.002, +0.053] against −0.022 [−0.056, −0.003], `BTK` +0.060 [+0.007, +0.156] against +0.005,
`SUB` +0.050 [+0.009, +0.119] against −0.080 [−0.172, −0.024]. That is the sign of §5 restated
one step further out, and it brackets the whole layer: the conditional arms are the k = 1
answer, `balance` is the k = ∞ answer, and `expected_actions` says how far apart those two
horizons are.

**`PGD` is negative in all four corpora that produce an estimate** (−0.029, −0.048, −0.011,
−0.005). Every interval covers zero, so this is agreement in sign, not a confirmed effect — but
it is the third statistic in this document to put guard-pull on the negative side (§5.6, §7.6),
and three of them agreeing is worth a look at footage.

### 8.7 The alternative that was measured and NOT shipped

The **empirical forward-looking** version of the same question — credit every appearance in a
bout with that bout's own ending, no Markov assumption — was computed during the design and
discriminates far more sharply. 65 kg `PGD` reads −0.500 there against this table's −0.029;
`GPS` +0.600; `SUBA` +0.372.

It is not published, and the reason is a denominator, not a preference. Its effective sample is
the number of absorbing **bouts** — four to six — while it wears an appearance count of up to
sixty-seven as its `n`, because every appearance inside one fight carries that fight's identical
outcome. Apply this document's own gate to the right unit and every row of it is refused, in
every corpus. The gap between it and §8.5 is therefore not evidence that first order is losing
signal; it is what a bout-level statistic looks like when it is printed at appearance
resolution. Naming it here makes the omission a decision.

---

## 9. The chain factor — does an action start a combination?

Runner: `analysis/lamas_chain.chain_factor`, exported at `markov[div].chain_factor` and
`adcc.corpora[*].chain_factor`.

### 9.1 The pre-registered definition, and the two that were rejected

```
chain_factor(s) = P( the TWO actions following an appearance of s are BOTH by the athlete
                     who performed s | that appearance has two following actions )
```

**Rejected: depth one.** "The next action is by the same athlete" is already published — it is
literally `reward_risk`'s `reward` arm (§5.1). A chain factor that stopped at one step would be
that number wearing a second name, and the two tables would sit on the same page pretending to
be independent. Depth two is the shortest window that says something the initiative table does
not: whether the exchange **keeps going**. The two are meant to be read together, and the gap
between them is the point — on the whole cycle, `CDP` retains at 0.54 for one step and chains at
0.32 for two, while `BTK` retains at 0.95 and chains at 0.64.

**Rejected: expected run length.** The intuitive "how long a run does this start" is an
expectation over a heavy right tail, where one bout's run of eight moves a state's number more
than every other bout together — and the bout is exactly the unit this corpus cannot spare. The
joint probability is bounded, is a proportion, and drops straight into the gating and interval
machinery every other cell in this block already uses. The run length is one accumulator away
if anyone ever needs a magnitude rather than a rate.

### 9.2 The denominator, and what leaves it

| leaves the denominator | rule | measured |
|---|---|---|
| appearances with fewer than two following actions | `n_short` — `build_graph`'s "an appearance without a successor is not scored", carried to depth two | 0–23 per state on the cycle; `SUB` is the extreme (23 of its 48 appearances), as it should be |
| windows containing an unknown actor | `n_unknown_actor` | **0 in all five corpora** |
| every bout the actor gate refuses | `one_sided` + `single_actor`, §5.2, unchanged | 12/29, 14/32, 25/53, 21/33, 46/86 |

The unknown-actor rule differs from `reward_risk`'s on purpose. That layer can leave an unknown
at 0 because its score is *signed* and 0 is genuinely neutral; a two-valued proportion has no
neutral outcome, and scoring an unknown as a failure would turn the factor into a measure of
annotation coverage. It is dropped and counted instead. That the count is zero everywhere is a
consequence of the actor gate running first, not an assurance.

The bias `n_short` creates is named rather than corrected: **chains that end quickly are
disproportionately the ones a submission ended**, so the factor describes the flow that
survived. That is the same trade §5 makes and it is made the same way.

Intervals are the pair §5.3 publishes — Wilson over appearances for the cell, bout-clustered
percentile bootstrap over the 0/1 values (whose mean *is* the factor) — and both are withheld
together below the bout-cluster gate.

### 9.3 Results

⚠️ **Do not read across corpora.** The rows are keyed by state and §7.2's annotation split
decides which events land in which state. `adcc.annotation.chain_factor_cross_corpus_comparable`
is `false` in the export.

**Ciclo completo** — 40 usable bouts, 350 windows, 11 of 12 rows gated.

| state | k / n | lutas | fator | Wilson 95% | bootstrap 95% (luta) | n_short |
|---|---|---|---|---|---|---|
| **BTK** | 9 / 14 | 7 | **0.643** | [0.388, 0.837] | [0.500, 0.818] | 8 |
| SWPA | 6 / 10 | 6 | 0.600 | [0.313, 0.832] | [0.375, 1.000] | 0 |
| **SUBA** | 23 / 39 | 18 | 0.590 | [0.434, 0.729] | [0.382, 0.750] | 10 |
| **BTKA** | 44 / 75 | 11 | 0.587 | [0.474, 0.691] | [0.426, 0.718] | 7 |
| **TKDA** | 49 / 86 | 16 | 0.570 | [0.464, 0.669] | [0.278, 0.745] | 6 |
| TKD | 9 / 16 | 12 | 0.562 | [0.332, 0.769] | [0.250, 0.800] | 8 |
| GPSA | 8 / 15 | 10 | 0.533 | [0.301, 0.752] | [0.200, 0.733] | 5 |
| PGD | 6 / 13 | 11 | 0.462 | [0.232, 0.709] | [0.091, 0.737] | 6 |
| SUB | 11 / 25 | 12 | 0.440 | [0.267, 0.629] | [0.217, 0.632] | 23 |
| SWP | 2 / 5 | 5 | 0.400 | [0.118, 0.769] | [0.000, 0.800] | 2 |
| **CDP** | 15 / 47 | 27 | **0.319** | [0.204, 0.462] | [0.195, 0.463] | 2 |
| GPS | 2 / 5 | 4 | 0.400 | withheld (`few_clusters`) | withheld | 3 |

**65 kg** — 17 usable bouts, 199 windows, 5 rows gated: `SWP` 0.833 [0.500, 1.000] (5/6, 6
bouts), `SUBA` 0.622 [0.452, 0.778] (23/37, 10), `BTKA` 0.617 [0.429, 0.773] (37/60, 7), `TKDA`
0.604 [0.364, 0.827] (32/53, 7), `PGD` 0.182 [0.000, 0.455] (2/11, 9).

**+65 kg** — 18 usable, 171 windows, 5 rows gated: `BTKA` 0.674 [0.474, 0.829] (29/43, 5),
`SUBA` 0.609 [0.357, 0.809] (28/46, 10), `GPSA` 0.467 [0.111, 0.643] (7/15, 6), `CDP` 0.429
[0.091, 1.000] (3/7, 5), `PGD` 0.222 [0.000, 0.556] (2/9, 9). `TKDA` 0.786 is **refused** — 4
bouts, the same concentration §3 flags.

**Trials 2023-24** — 28 usable, 143 windows, 9 rows gated: `BTK` 0.692 [0.579, 0.875] (9/13, 6),
`TKD` 0.643 [0.333, 0.882] (9/14, 10), then a long tail near a third — `SWP` 0.400, `CDP` 0.333
(13/39, 24 bouts, the tightest row in the corpus), `GPSA` 0.333, `SUBA` 0.333, `SUB` 0.231,
`TKDA` 0.211 [0.053, 0.381], `PGD` 0.125 [0.000, 0.375].

**ADCC 2024** — 12 usable, 207 windows, 4 rows gated: `SUBA` 0.810 [0.625, 0.938] (17/21, 9),
`TKDA` 0.672 [0.345, 0.848] (45/67, 8), `SWPA` 0.600 [0.375, 1.000] (6/10, 6), `BTKA` 0.597
[0.426, 0.742] (43/72, 9).

### 9.4 The reading

**Within the cycle, the spread is ordered the way a coach would guess.** A *landed* back-take
chains at 0.643 and a clinch dispute at 0.319 — the widest gap in the table, and the one worth
naming: **a clinch exchange in this corpus does not start combinations; a back-take does.**
`CDP` is also the corpus's most evidence-rich row (27 bouts, the widest cluster base here), so
the low end is the well-measured end, which is unusual in this document.

**It is a difference worth naming and not a difference that has been established, and the
distinction is not pedantry.** The bout-clustered intervals do not overlap ([0.500, 0.818]
against [0.195, 0.463]); the Wilson ones *just* do (0.388 against 0.462). Where the two
disagree, the clustered one is not automatically the better witness — `stats_rigor.bootstrap_ci`
warns in its own docstring that with a handful of clusters it can come back NARROWER than the
naive interval because it is resampling few things, and `BTK` here has seven bouts. Reading the
non-overlap as a result would be using exactly the interval that warning is about.

**The attacking middle is flat.** `SUBA`, `BTKA`, `TKDA`, `TKD`, `GPSA` all sit between 0.53 and
0.59 with heavily overlapping intervals. Nothing separates them, and reading an order into those
five would be reading noise.

**`PGD` is the low end in both divisions** (0.182 and 0.222, both gated, both intervals touching
zero at the bottom) and mid-pack in the cycle (0.462). A guard pull, in the women's bracket
corpus, does not begin a run of her own actions — the fourth statistic in this document to put
guard-pull on the unfavourable side, after §5.4, §5.6 and §8.6.

**What the `SUB` row is.** 23 of its 48 cycle appearances have no two-action future, which is
exactly what a terminal state should look like; the 0.440 describes the quarter of submission
events that were locked, escaped and followed by more grappling. It is not a statement about
finishing.

---

## 10. What this does not do

- No second-order / semi-Markov check. PoC-E4 already measured second order losing materially
  on the raw label space (Δ per-step log-likelihood −0.203 [−0.258, −0.133], bout-clustered);
  whether that survives the collapse to twelve states is unmeasured and would need its own
  pre-registered plan.
- No held-out evaluation and no stationary distribution. Both are cheap to add once anyone needs
  them; neither is needed to draw the tables above. The absorbing-chain arithmetic that used to
  be listed here alongside them **is** now done — §8, including the expected actions to
  absorption, which is what makes §8.6's flatness legible.
- **No empirical forward-looking absorption** (§8.7). Measured, sharper, and refused by this
  document's own gate in every corpus once the gate is applied to the bout rather than to the
  appearance.
- **No expected same-actor run length** (§9.1). Rejected for the tail, not forgotten.
- **RRB is not published for two of the five corpora.** 65 kg carries point estimates and no
  interval (4 absorbing bouts); ADCC 2024 carries nothing at all (0). The remedy is corpus size
  or a looser actor gate, and the second is not available — this layer depends on `actor_id`
  more heavily than §5 does, not less.
- Reward-risk is not weighted. `build_graph` takes an optional `weight_fn` so a corpus can be
  confidence-weighted per athlete (`analysis/confidence_weight.py`); this does not, because
  with 17 and 18 usable bouts the weighting would be estimated from fewer athletes than it
  reweights. Add it when the usable set outgrows the gate, not before.
- Reward-risk does not distinguish `TKDA → TKD` from `TKDA → TKDA`. Both are the same athlete
  acting again, and both count as reward. A landed-only refinement is one predicate away, and
  it is deliberately not taken: §1.4 means "landed" is a statement about `successful` coverage
  as much as about the grappling.
- No production value moves. ADR-03: this is a report, not a calibration.
- **It does not repair the annotation split found in §7.2.** The right fix is upstream — one
  documented convention for `successful` across every dump batch, and a re-refine of the ADCC
  2024 events under it. Until then the attempt/success axis of any cross-batch comparison is
  uninterpretable, and no amount of statistics downstream can recover it. That is a data
  ticket, not an analysis one.
- No per-division read of the ADCC cycle. The corpus holds every weight class and both sexes;
  slicing it by division is possible and was not done, because §7.5's whole point is that 86
  bouts is the first size at which the gate stops refusing — cutting it back into eights
  would undo exactly that.

---

*Provenance: written 2026-08-25 against `analysis/lamas_chain.py`,
`scripts/bracket_export.py:markov_layer` (§§2–6) and `:adcc_layer` (§7); §§8–9 added the same
day, with every definition in §8.1 and §9.1 written before the corpus numbers under them were
read. Corpus label counts and
the ADCC event-tag census come from read-only queries against `matches` on the same date. If a
number here disagrees with the exporter, the exporter is right — regenerate and fix this file in
the same push. §§2–6 have already been through that once: they were rewritten when the scouting
corpus went from 40 bouts to 58, and the header note records what moved.*
