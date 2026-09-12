# Inferência de resultado por ação — o contrato `infer_outcome` (D7, internal half)

Owner rule, binding (2026-09-12): **per-action success/failure = the manual flag on the LAST
node of a sequence; every INTERNAL action's outcome is INFERRED from the resulting transition,
and `None` when the sequence cannot resolve it — never auto-success, never auto-failure.** This
document is the contract for the INTERNAL half — `analysis.outcome_inference` — pre-registered
at `docs/research/outcome_inference_prereg.md` before any rule below was scored, evidence in §3.
The last-node anchor half (D7 proper) is already shipped as `last_flag_only` in
`scripts/research/rrb_round_rating.py` (prereg §D3b) — not this module's job.

## 1. The shape

```python
def infer_outcome(
    prev_state: StateRef | None, action: ActionRef, next_state: StateRef | None,
    actor: str | None, *, rules=DEFAULT_RULES, terminal: bool = False,
    actor_readable: bool = True,
) -> bool | None
```

`StateRef(label, type, actor=None)` / `ActionRef(label, type)`. `terminal` mirrors
`analysis.chain_compiler.ChainEdge.terminal` (this action closes the chain with nothing
observed after it) — the caller's own signal, never re-derived here. `actor_readable` is the
same reliability gate `analysis.attribution.bout_flags` / `taxonomy_kind.infer_transition_actions`
already require before trusting an actor difference as evidence.

Each rule is `Rule = Callable[..., bool | None]`, called in order with `(prev_state, action,
next_state, actor)` plus `table`/`actor_readable`/`terminal` as keywords every rule accepts
(and most ignore). **The first rule that answers wins — composition never overrides an earlier
non-`None` answer**, and a rule that returns `None` is a refusal, not a "no opinion worth
recording": it means the sequence itself gives that rule nothing to read.

## 2. The rules, in shipped order (`DEFAULT_RULES`)

### R0 — today's rule (unchanged, `scripts/research/rrb_round_rating.py:infer_entry_success`)

1. The chain returns to its own source (`next_state.label == prev_state.label`, canonically) ⇒
   **failed**.
2. The target's curated role (`analysis.attribution.classify`) matches the action type's
   declared `action_exit_orientation` (`data/taxonomy/inference_table.json`) ⇒ **landed**.
3. A terminal submission with no next state (`terminal=True`, `next_state is None`) ⇒
   **landed**.
4. Anything else — including a `neutral` declared exit, which is a declared NO-CLAIM, never a
   guess — ⇒ **unresolved**.

Owner's own worked examples, pinned as tests: `Half Guard → Sweep → Mount ⇒ True` (English),
`Meia Guarda → Raspagem → Montada ⇒ True` (pt-BR); `Back Control → RNC → Back Control ⇒ False`,
`Costas → Mata Leão → Costas ⇒ False` (pt-BR).

### R1 — orientation flip, sweep/reversal only

Reuses `taxonomy_kind.orientation_for_inference` — the WIDER 3-level stance reading (declared
`state_orientation.json`, then the library's canonical name, then `attribution`'s curated role)
that resolves labels R0's type-keyed exit table cannot (`docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`
§8.2 measured 52/74 curated labels reading `neutral` through the narrow table alone). Landed iff
the next state's stance for the actor is `top`; failed only when it is STILL `bottom` **and**
the chain started `bottom` too (same guard family — never a claim across the topology/control
axis split `attribution._AXES` keeps separate).

### R2 — type → expected next-state family

One curated expectation per action type, off the SAME `attribution.classify` role table:

| action type | landed | failed |
|---|---|---|
| `pass` / `takedown` | next role `top` | next role `bottom` |
| `guard` (a guard pull) | next role `bottom` (ended up in a guard) | next role `top` |
| back take (label matches `lamas_chain.BACK_TAKE_TOKENS`) | next role `controlling` | next role `top`/`bottom` (control taken, back not) |
| `escape` | next stance `neutral`, or a `guard` state with stance `bottom` | a `control` state still reading `bottom` |
| `submission` | the chain closes on the `finish` anchor, or this action IS the chain's own terminal action | *(the return-to-source failure is already R0's job — fires first, so this rule is never asked to invent it)* |

Everything else ⇒ unresolved. `pass`/`takedown`/`guard` are measured **redundant with R0** on
the owner's data (R0's exit-orientation table already answers those three types the same way);
the back-take/escape-family/finish-anchor branches are the ones R0 cannot reach at all.

### R3 — actor consistency, gated by reliability — **defined, NOT shipped in `DEFAULT_RULES`**

When `actor_readable` and the state after the action belongs to someone OTHER than the action's
own actor, the action **failed** — the position moved to the other side. Never infers `True`
from actor agreement: staying in a state you already owned is not evidence an action landed,
only that nothing disproved it. **Measured below today's precision floor (§3) — death rule 14
fires, and it is excluded from `DEFAULT_RULES`.** Kept in the source, tested, and in `RULE_SETS`
for the record: correct on the cases it touches in isolation, just not a net win on this
dataset's mix of action types.

### R4 — the D7 anchor rule (last node) — not part of this module

The manual flag on the LAST own-actor node of a sequence. Already shipped as
`last_flag_only` in `scripts/research/rrb_round_rating.py` (prereg §D3b) — listed here only so
the full rule (R0–R4) reads as one story.

## 3. Evidence (prereg §D2/§D3, `docs/research/outcome_inference_prereg.md`)

Scored on the owner's private fixture (`out/rrb_study/owner_rounds.json`, 140 rounds, 640
entries, 224 actions — score only, no entry content leaves this run). **The public-corpus arm
was pre-registered but not run in this session** (no database credentials in this sandbox) —
open item, not silently dropped; run `matches.sequence` through the same harness when DB access
is available.

Two denominators, because the primary test set for this task (internal actions, excluding the
one the D7 anchor already answers by manual flag) is NOT the same population the study's
headline 18.9 %/70.2 % were measured on (which pools every entry, internal and last-node
alike). Both are reported rather than picking the one that reads better:

| rule set | resolved / 224 (all, incl. last node) | precision | resolved / 160 (internal only) | precision |
|---|---|---|---|---|
| **R0** (today, reproduces the study's headline exactly) | 121 (54.0 %) | **70.2 %** | 76 (47.5 %) | 65.8 % |
| **R0+R1** | 122 (54.5 %) | **70.5 %** | 77 (48.1 %) | 66.2 % |
| **R0+R1+R2 — shipped `DEFAULT_RULES`** | 122 (54.5 %) | 70.5 % | 77 (48.1 %) | 66.2 % |
| R0+R1+R2+R3 (rejected) | 146 (65.2 %) | 63.0 % | 99 (61.9 %) | 56.6 % |

**R1 strictly dominates R0** on the owner fixture — equal-or-higher resolution AND
equal-or-higher precision on BOTH denominators, never worse. The gain is small (+1 resolved
entry, entirely `sweep`/`reversal` cases R0's narrow exit table missed and the wider 3-level
stance reading catches) but it is a real, measured improvement with no trade-off, which is what
the death rule is built to allow through.

**R2 is measured NEUTRAL on this dataset** — identical resolved/precision to R0+R1. Its
`pass`/`takedown`/`guard` branches are redundant with R0 (same exit-orientation table, same
answer); its back-take, escape-to-neutral and finish-anchor branches are designed for the
**compiled `ChainEdge` world** (`chain_compiler.terminal`, the `finish` generic anchor node,
`terminal=True` threaded from the compiler) — this study's harness walks RAW un-compiled
sequence entries, which never carry a literal `finish` state and rarely place a real state
immediately after an `escape`/back-take action. R2 ships anyway: it is provably correct on its
own cases (`tests/test_outcome_inference.py`), harmless where it does not apply (never resolves
where it shouldn't), and its intended activation is the wiring step this task explicitly defers.

**R3 fails death rule 14 on both denominators** — it buys real resolution (+24 to +25 entries)
by reading "the state after an action belongs to the other athlete" as failure, but roughly a
third of those reads disagree with the manual flag (mostly `takedown`/`pass` cases where control
changed hands for a reason other than the logged action failing — a scramble, an unrelated
follow-up). **Rejected, not shipped.**

## 4. What always stays `None`, and why

* An action whose type carries no curated exit orientation and does not match any R1/R2 branch
  (`control`, `transition` with no positional claim) — the taxonomy genuinely has no opinion.
  Measured: `control`/`transition` actions resolve almost entirely via R3 alone (which is not
  shipped), so today's DEFAULT_RULES leaves most of them `None`.
* A `neutral` declared exit orientation (`escape`, `submission` mid-chain outside R2's finish
  case, bare `transition`, `control`) — `neutral` is a declared NO-CLAIM in
  `action_exit_orientation`, and every rule here honours "exit orientation may only SUPPRESS an
  inference, never create one" (`docs/taxonomy/03_ARESTA_COMO_CAMINHO.md` §8.3).
* No next state at all, and `terminal=False` — the caller has not told this module the chain
  actually ends here, so a missing observation is never read as "it must have landed."
* `actor_readable=False` disables R3 entirely (moot while R3 is unshipped, load-bearing the day
  it is reconsidered).

## 5. Not wired in yet

`chain_compiler`/rating are untouched by this module (owner instruction, 2026-09-12). Wiring
`infer_outcome` into the App's rating path — passing real `ChainEdge`/`ChainAction` state
instead of hand-built `StateRef`/`ActionRef`, and threading `ChainEdge.terminal` through — is a
later step, not this one. `data/fixtures/outcomeInferenceGolden.json`
(`scripts/export_outcome_inference_fixtures.py --check`) exists so the App can mirror the
shipped composition byte-identically whenever that wiring happens.
