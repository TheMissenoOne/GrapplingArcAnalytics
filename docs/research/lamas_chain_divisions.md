# Lamas' chain, per bracket division — ADCC 2026 women's 65 kg and +65 kg

Runner: `analysis/lamas_chain.py` (mapping, chain, estimation) via `scripts/bracket_export.py`
(`markov_layer` → `data.json`'s `markov` key). Tests: `tests/test_lamas_chain.py`. Numbers
below were produced from `data/scouting/adcc_2026_women_sequences.json` on **2026-08-25**;
re-running the exporter regenerates them.

Source paper: **Lamas, L., et al. (2024). *No-gi Brazilian jiu-jitsu: a Markovian analysis of
elite-level combat dynamics.* International Journal of Sports Science & Coaching,
doi:10.1177/17479541231210979** — 93 WSFC-2019 no-gi matches; the one peer-reviewed BJJ Markov
paper and the only external transition matrix this corpus can be checked against
(`docs/research/04_BIBLIOGRAPHY.md` §D).

**This is descriptive scouting analysis, not a pre-registered PoC.** Nothing here is a
held-out prediction, nothing here selects a production value, and no site or App metric moves
on it (ADR-03). Spread between the two divisions is *not* evidence that they play differently
— with 19 and 23 bouts, most cells overlap by construction, and where they do not the
coverage gate usually says why.

---

## 1. Method

### 1.1 The state space

Twelve states, six of them an attempt/success pair. Codes and definitions are fixed in
`analysis.lamas_chain.STATES` / `STATE_DEFS` and ship inside the export, so a renderer cannot
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
   two actions is the pause between them, not a state. 92 events (65 kg) and 61 (+65 kg) were
   passed over; the top skipped labels are `guard/Half Guard`, `control/Mount`,
   `guard/Deep Half Guard`, `guard/Closed Guard`, `control/Escape to Turtle`, i.e. exactly the
   postures and dwell states the paper's action space has no code for.
2. **Chronology is array order.** Measured: 39 of the 40 scouting bouts carry `ts` on every
   event and *none* disagrees with the array; the fortieth carries no `ts` at all. Array order
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
   not trustworthy enough to build a matrix on. 135 of 180 transitions (65 kg) and 144 of 181
   (+65 kg) happen to be within-actor anyway.

### 1.4 Attempt vs success, and the bias this creates

`successful: true` → success code; **`false` OR ABSENT → attempt**. `successful` is present on
28.9% of corpus events (34.2% of the scouting subset, 166/486).

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
That drops 29 events in 7 bouts (65 kg) and 30 in 9 (+65 kg) — post-finish duplicates, the
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
every corpus bout with event data that either corner belongs to. 19 bouts in 65 kg, 23 in
+65 kg; six bouts have a rostered athlete in both corners and appear in both divisions (the
fight itself sitting in two categories, not a duplicate).

The `markov` block declares `kind: category` in `SCOPES` and ignores the uniform / ruleset /
since axes, with the measurement behind the refusal shipped in `ignored_measured`: at full
division size only 6 and 5 of twelve rows clear the gate, and across the ten non-trivial
points of the uniform × since space **170 of 240 rows are refused outright** for about 780 KB
of extra payload. The reason code is `cuts_refuse_most_rows`, deliberately *not*
`gate_refuses_every_cell` — some cuts do produce estimable rows, and a reason code that
overstates its evidence is the defect this layer exists to prevent.

---

## 2. 65 kg — 19 bouts, 199 mapped events, 92 skipped, 180 transitions

Cell = row-normalised probability (count). `n` is the row denominator, `lutas` the number of
bouts contributing to it, `gate` whether the row earns an interval at all.

| from \ to | CDP | PGD | SWPA | SWP | TKDA | TKD | GPSA | GPS | BTKA | BTK | SUBA | SUB | n | lutas | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **CDP** | — | — | — | — | — | — | — | — | 0.50 (1) | — | 0.50 (1) | — | 2 | 2 | no |
| **PGD** | — | — | — | 0.17 (1) | 0.17 (1) | — | — | — | 0.17 (1) | — | **0.50 (3)** | — | 6 | 6 | yes |
| **SWPA** | — | — | — | — | 1.00 (1) | — | — | — | — | — | — | — | 1 | 1 | no |
| **SWP** | — | 0.40 (2) | — | — | — | 0.20 (1) | 0.20 (1) | — | — | — | — | 0.20 (1) | 5 | 5 | yes |
| **TKDA** | — | 0.02 (1) | — | — | **0.34 (16)** | — | 0.02 (1) | 0.02 (1) | **0.43 (20)** | — | 0.17 (8) | — | 47 | 6 | yes |
| **TKD** | — | — | — | 0.20 (1) | — | — | — | 0.20 (1) | 0.20 (1) | — | 0.40 (2) | — | 5 | 4 | no |
| **GPSA** | — | — | — | 0.09 (1) | 0.45 (5) | — | 0.27 (3) | — | — | — | 0.18 (2) | — | 11 | 4 | no |
| **GPS** | — | — | — | — | — | — | 0.17 (1) | — | 0.17 (1) | — | 0.33 (2) | 0.33 (2) | 6 | 5 | yes |
| **BTKA** | 0.02 (1) | — | 0.02 (1) | 0.02 (1) | **0.30 (17)** | — | 0.04 (2) | — | **0.39 (22)** | 0.02 (1) | 0.18 (10) | 0.02 (1) | 56 | 10 | yes |
| **BTK** | — | — | — | — | — | — | — | — | 1.00 (1) | — | — | — | 1 | 1 | no |
| **SUBA** | — | 0.03 (1) | — | 0.05 (2) | 0.15 (6) | 0.05 (2) | 0.03 (1) | 0.08 (3) | 0.15 (6) | — | **0.41 (16)** | 0.05 (2) | 39 | 10 | yes |
| **SUB** | — | — | — | — | — | — | — | — | — | — | — | 1.00 (1) | 1 | 1 | no |

**Top cells that carry an interval.** `TKDA → BTKA` 0.43 [0.29, 0.57] is the division's
signature transition: a failed or contested takedown attempt turns into a back exposure more
often than into anything else, including another takedown attempt (0.34). `BTKA → TKDA` 0.30
runs the same loop backwards — the two states account for 105 of 199 mapped events between
them. `SUBA → SUBA` 0.41 is re-attempt behaviour, and `PGD → SUBA` 0.50 (3 of 6, wide) is the
guard-pull-to-attack pattern the +65 kg matrix shows far more strongly.

### Occupancy

| state | k | bouts | share | 95% CI |
|---|---|---|---|---|
| CDP | 2 | 2 | 0.010 | withheld (`few_clusters`) |
| PGD | 6 | 6 | 0.030 | 0.014–0.064 |
| SWPA | 1 | 1 | 0.005 | withheld (`few_clusters`) |
| SWP | 8 | 6 | 0.040 | 0.021–0.077 |
| **TKDA** | 47 | 6 | 0.236 | 0.183–0.300 |
| TKD | 5 | 4 | 0.025 | withheld (`few_clusters`) |
| GPSA | 11 | 4 | 0.055 | withheld (`few_clusters`) |
| GPS | 6 | 5 | 0.030 | 0.014–0.064 |
| **BTKA** | 58 | 10 | 0.291 | 0.233–0.358 |
| BTK | 1 | 1 | 0.005 | withheld (`few_clusters`) |
| **SUBA** | 44 | 12 | 0.221 | 0.169–0.284 |
| SUB | 10 | 9 | 0.050 | 0.028–0.090 |

Note `TKDA`'s share of 0.236 comes from **6 bouts** while `SUBA`'s 0.221 comes from 12. Both
clear the gate; they are not equally distributed evidence, and the `bouts` column is there so
that is visible rather than implied.

### Routes into a submission (3 actions, terminal `SUB` or `SUBA`)

| path | k | bouts | p (first-order) | bout rate 95% CI |
|---|---|---|---|---|
| SUBA → SUBA → SUBA | 8 | 2/19 | 0.168 | 0.03–0.31 |
| BTKA → BTKA → SUBA | 5 | 4/19 | 0.070 | 0.09–0.43 |
| TKDA → TKDA → SUBA | 5 | 4/19 | 0.058 | 0.09–0.43 |
| BTKA → SUBA → SUBA | 4 | 3/19 | 0.073 | 0.06–0.38 |
| GPSA → TKDA → SUBA | 2 | 2/19 | 0.077 | 0.03–0.31 |

Length 2 is deliberately not listed — it *is* the matrix's own `SUB`/`SUBA` column, and
printing it twice would let a reader take one table as corroboration of the other. The
terminal admits `SUBA` because §1.4 puts most real finishes there; requiring `SUB` would rank
"dominant submission pathways" off ten events.

---

## 3. +65 kg — 23 bouts, 204 mapped events, 61 skipped, 181 transitions

| from \ to | CDP | PGD | SWPA | SWP | TKDA | TKD | GPSA | GPS | BTKA | BTK | SUBA | SUB | n | lutas | gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **CDP** | — | — | — | 0.33 (2) | — | — | — | — | 0.17 (1) | — | **0.50 (3)** | — | 6 | 5 | yes |
| **PGD** | 0.12 (1) | — | — | — | 0.12 (1) | — | — | 0.12 (1) | — | — | **0.62 (5)** | — | 8 | 8 | yes |
| **SWPA** | — | — | 0.74 (14) | — | — | — | — | — | 0.16 (3) | — | 0.05 (1) | 0.05 (1) | 19 | 2 | no |
| **SWP** | 0.17 (1) | — | — | 0.17 (1) | — | — | 0.17 (1) | — | — | — | 0.33 (2) | 0.17 (1) | 6 | 4 | no |
| **TKDA** | — | 0.05 (1) | — | — | 0.20 (4) | — | 0.05 (1) | — | 0.40 (8) | — | 0.30 (6) | — | 20 | 3 | no |
| **TKD** | — | — | — | — | — | — | 0.50 (1) | — | — | — | — | 0.50 (1) | 2 | 2 | no |
| **GPSA** | — | — | — | 0.05 (1) | **0.26 (5)** | 0.05 (1) | **0.26 (5)** | — | 0.05 (1) | — | **0.26 (5)** | 0.05 (1) | 19 | 11 | yes |
| **GPS** | — | — | — | — | — | — | 0.67 (2) | — | 0.33 (1) | — | — | — | 3 | 3 | no |
| **BTKA** | 0.03 (1) | — | 0.08 (3) | — | 0.13 (5) | — | 0.05 (2) | — | **0.46 (18)** | 0.03 (1) | 0.15 (6) | 0.08 (3) | 39 | 8 | yes |
| **BTK** | — | — | — | — | — | — | — | — | 1.00 (1) | — | — | — | 1 | 1 | no |
| **SUBA** | — | 0.04 (2) | 0.02 (1) | 0.02 (1) | 0.07 (4) | — | 0.07 (4) | 0.04 (2) | 0.16 (9) | — | **0.54 (30)** | 0.05 (3) | 56 | 13 | yes |
| **SUB** | — | — | — | — | — | — | — | — | — | — | 0.50 (1) | 0.50 (1) | 2 | 2 | no |

**Top cells that carry an interval.** `PGD → SUBA` 0.62 [0.31, 0.86] over 8 bouts: in this
division a guard pull is followed by a submission attempt more often than by anything else,
and it is one of the few cells here drawn from eight distinct fights rather than two.
`SUBA → SUBA` 0.54 [0.41, 0.66] is the highest-confidence cell in either matrix.
`GPSA → {TKDA, GPSA, SUBA}` splits almost evenly at 0.26 each over 11 bouts — a passing
exchange in +65 kg has no dominant continuation.

`SWPA → SWPA` 0.74 (14 of 19) is the clearest illustration of why the gate exists: it comes
from **2 bouts**, so it is one athlete's repeated sweep attempts wearing a division's name.
The count is published; the interval is not.

### Occupancy

| state | k | bouts | share | 95% CI |
|---|---|---|---|---|
| CDP | 6 | 5 | 0.029 | 0.014–0.063 |
| PGD | 8 | 8 | 0.039 | 0.020–0.075 |
| SWPA | 19 | 2 | 0.093 | withheld (`few_clusters`) |
| SWP | 6 | 4 | 0.029 | withheld (`few_clusters`) |
| TKDA | 20 | 3 | 0.098 | withheld (`few_clusters`) |
| TKD | 3 | 2 | 0.015 | withheld (`few_clusters`) |
| **GPSA** | 21 | 12 | 0.103 | 0.068–0.152 |
| GPS | 3 | 3 | 0.015 | withheld (`few_clusters`) |
| **BTKA** | 42 | 8 | 0.206 | 0.156–0.267 |
| BTK | 1 | 1 | 0.005 | withheld (`few_clusters`) |
| **SUBA** | 61 | 15 | 0.299 | 0.240–0.365 |
| SUB | 14 | 12 | 0.069 | 0.041–0.112 |

### Routes into a submission

| path | k | bouts | p (first-order) | bout rate 95% CI |
|---|---|---|---|---|
| SUBA → SUBA → SUBA | 16 | 5/23 | 0.287 | 0.10–0.42 |
| PGD → SUBA → SUBA | 4 | 4/23 | 0.335 | 0.07–0.37 |
| BTKA → BTKA → SUBA | 4 | 2/23 | 0.071 | 0.02–0.27 |
| BTKA → SUBA → SUBA | 3 | 2/23 | 0.082 | 0.02–0.27 |
| TKDA → TKDA → SUBA | 3 | 2/23 | 0.060 | 0.02–0.27 |

`PGD → SUBA → SUBA` has the highest first-order chain probability of any route in either
division (0.335) and comes from four distinct bouts — the strongest scouting signal these two
tables carry.

---

## 4. Anchor against Lamas 2024 — and what it settles about PoC-E4

Stated at **family level** (attempt and success collapsed), because that is how the paper
states them: "back control → submission" is not a claim about a *landed* back-take, and §1.4
would otherwise put 89 of 91 back-controls on the attempt side and compare a cell the paper
never published.

Reference values are read from `analysis/poc/e4_ptv_eval.LAMAS_PUBLISHED`, so the two runners
cannot drift.

| cell | Lamas | 65 kg cross | agrees | +65 kg cross | agrees |
|---|---|---|---|---|---|
| back control → submission | 0.45 | 0.193 [0.111, 0.313] (11/57) | **NO** | 0.225 [0.123, 0.375] (9/40) | **NO** |
| takedown → submission | 0.15 | 0.192 [0.108, 0.319] (10/52) | yes | 0.318 [0.164, 0.527] (7/22) | **NO** |
| guard pass → guard pass | 0.30 | 0.235 [0.096, 0.473] (4/17) | yes | 0.318 [0.164, 0.527] (7/22) | yes |

Within-actor arm (PoC-E4's convention), same order: 0.214 / 0.194 / 0.214 in 65 kg and 0.219 /
0.316 / 0.333 in +65 kg — every cell within a few points of the cross-actor reading, and no
agreement verdict changes. Whichever convention the chain uses, the answer is the same;
that is the most useful thing the `same_actor` flag has to say.

### 4.1 The guard-pass cell: PoC-E4's structural explanation, confirmed

PoC-E4 measured these three cells on the **raw label vocabulary** and got 0.210 / 0.146 /
**0.079**, missing on two of three. Its report attributed the misses to state-space dilution:

> Lamas codes ~10 coarse states, our label vocabulary runs to hundreds of nodes, and a finer
> state space mechanically DILUTES every single transition probability […] the anchor cannot
> validate at face value **without a state-space mapping nobody has written**.

That mapping is what §1.2 is. Under it, guard pass → guard pass moves **0.079 → 0.235 and
0.318**, and both intervals now cover the published 0.30. The dilution argument was right, and
this is the measurement that shows it — on the one cell where dilution was the whole story.

### 4.2 The back-control cell: not dilution, state re-entry

Back control → submission did **not** recover: 0.19 and 0.23 against 0.45, out of interval in
both divisions and barely moved from PoC-E4's corpus-wide 0.21. So dilution is not the
explanation there, and the matrix says what is: **0.39 and 0.46 of everything leaving a
back-control goes to another back-control event.** Our corpus logs a *held* position
repeatedly; Lamas' coding occupies the state once and moves on.

`anchor[*].no_reentry` is the diagnostic that drops same-family re-entries from the
denominator — the closest thing to his coding our events can be read into:

| cell | Lamas | 65 kg no-reentry | +65 kg no-reentry |
|---|---|---|---|
| back control → submission | 0.45 | 0.333 [0.198, 0.504] — **covers 0.45** (11/33) | **0.450** [0.258, 0.658] — **covers 0.45** (9/20) |
| takedown → submission | 0.15 | 0.278 [0.158, 0.440] — no | 0.389 [0.203, 0.614] — no |

+65 kg lands on 0.450 against a published 0.45. That is a coincidence at this n and should be
read as one — but the direction is not: both divisions move from *out of interval* to *covering
the published value* once state re-entry is removed, which identifies the gap as an **event-
logging convention**, not a difference in how these athletes fight from the back.

The arm is undefined for guard pass → guard pass, because the paper's own cell there *is* the
re-entry (`reason_code: published_cell_is_the_reentry`). It is a diagnostic on the gap, never
a competing estimate, and it never enters a criterion.

### 4.3 The takedown cell

`takedown → submission` is the one cell that agrees at face value in 65 kg (0.192, covering
0.15) and disagrees in +65 kg (0.318 from **3 bouts**, gate refused). The refusal is the
finding: +65 kg's `TKDA` row is not a division estimate.

---

## 5. Caveats (these ship inside the export, `markov[div].caveats`)

1. **`successful` is present on 28.9% of corpus events; absent reads as attempt.** Every
   success rate here is a floor, and the distortion is uneven — 89 of 91 back-controls fall in
   `BTKA`.
2. **`SUB` absorbs by the bout's result, not by the flag** (§1.5). `sub_outgoing` counts the
   submissions that were locked and escaped: 1 and 2.
3. **Unmapped events are passed over** (§1.3 rule 1); `n_events_skipped` and `skipped_top` say
   how much of the stream that is — 92 of 291 and 61 of 265.
4. **`Front Headlock` is the largest unresolvable ambiguity in the mapping.** At 139 corpus
   events it is the second biggest contributor to `CDP`, and it can be standing *or* over a
   turtled opponent. The corpus does not record the distinction, so the inclusion cannot be
   settled from the data as it stands.
5. **The matrix is cross-actor** (§1.3 rule 4); the anchor reports both conventions because the
   published cells are same-athlete statements.
6. **First order, no smoothing.** A low-`n` cell has a wide interval by construction, and below
   the bout-cluster gate no interval is published at all.
7. **19 and 23 bouts.** Do not read a difference between the two matrices as a difference
   between the divisions unless the intervals say so — and most of them do not.

## 6. What this does not do

- No second-order / semi-Markov check. PoC-E4 already measured second order losing materially
  on the raw label space (Δ per-step log-likelihood −0.203 [−0.258, −0.133], bout-clustered);
  whether that survives the collapse to twelve states is unmeasured and would need its own
  pre-registered plan.
- No held-out evaluation, no stationary distribution, no absorbing-chain expected-steps
  arithmetic. All three are cheap to add once anyone needs them; none is needed to draw the
  scouting tables above.
- No production value moves. ADR-03: this is a report, not a calibration.

---

*Provenance: written 2026-08-25 against `analysis/lamas_chain.py` and
`scripts/bracket_export.py:markov_layer`. Corpus label counts come from a read-only census of
`matches.sequence` on the same date. If a number here disagrees with the exporter, the
exporter is right — regenerate and fix this file in the same push.*
