# Outcome inference — pre-registration (written before any candidate was scored)

Owner rule, binding (2026-09-12): **per-action success/failure = the manual flag on the LAST
node of a sequence; every INTERNAL action's outcome is INFERRED from the resulting transition,
and NULL when the sequence cannot resolve it — never auto-success, never auto-failure.**
`docs/research/rrb_round_rating_prereg.md` §D2/§D3b already fixed this shape and measured
today's rule (`R0` below, `scripts/research/rrb_round_rating.py:infer_entry_success`): **18.9 %**
of the owner's 640 logged entries resolve by inference (54.0 % on the action-only denominator),
and of those, **70.2 %** agree with the manual flag. This document pre-registers the attempt to
improve on that pair — higher resolution at no worse precision — before any new rule is scored.

## Ground truth

The owner's manual `successful` flags on **internal** entries (not the last own-actor node of a
round — that one is the anchor the product rule keeps a flag on by design, and scoring against
it would be circular). The App used to write a flag on every logged entry, so these rows are
real labels, not a proxy. Two sets:

* **Owner fixture** (`out/rrb_study/owner_rounds.json`, PRIVATE) — 140 rounds, 640 entries. Score
  only; no entry content leaves this run.
* **Public corpus** (`matches.sequence`, `status='final'`) — the large-N set. Per-event
  `successful` is present on a minority of rows (31.1 % measured in §D1); every row that carries
  one is a label.

Last-own-node entries are **excluded** from the internal precision/resolution numbers (they are
answered by the manual flag itself, not by inference) but reported separately as a second,
usable set — the D7 anchor rule already promises they resolve.

## Metrics

For each candidate rule (or ordered composition of rules):

1. **Resolution rate** — share of internal actions that get a non-`None` inference, on two
   denominators: all internal entries, and internal *actions* only (states are never scored).
2. **Concordance / precision vs the manual flag** — on the entries the rule resolves, agreement
   rate with `successful` (`True`/`False`, `None` on the corpus treated as "no label", never as
   a label of `False`).
3. Both reported **per action type** (`sweep`, `pass`, `takedown`, `guard`, `submission`,
   `escape`, `transition`, `control`), not only pooled — a rule that trades one type's precision
   for another's resolution should be visible.
4. Both reported on **both datasets** (owner fixture = primary; public corpus = confirmatory,
   large-N).

## Death rule

**Any rule (or composition) whose precision on resolved entries falls below today's 70.2 % is
rejected**, regardless of the resolution it buys — a wrong inference is worse than no inference,
because a manufactured `False` can silently zero out a Glicko-2 observation that actually landed.
The goal is a composition that clears **70.2 % precision** at a **higher resolution rate** than
18.9 % (entry denominator) / 54.0 % (action denominator).

## Candidate rules (composable, ordered, first non-`None` wins)

Each a pure function `infer_outcome(prev_state, action, next_state, actor) -> True | False |
None`, evaluated in the cumulative orderings `R0`, `R0+R1`, `R0+R1+R2`, `R0+R1+R2+R3`:

* **R0** — today's rule (`docs/research/rrb_round_rating_prereg.md` §D2): the target state's
  role matches the action's declared `action_exit_orientation` (from
  `data/taxonomy/inference_table.json`, read through `attribution.classify`) ⇒ landed; the chain
  returns to the source state ⇒ did not land; a terminal submission ⇒ landed; anything else ⇒
  unresolved.
* **R1** — orientation flip, sweep/reversal only: uses the WIDER 3-level stance reading
  (`taxonomy_kind.orientation_for_inference`, which also resolves through the technique
  library's canonical name and `attribution`'s curated role, not just the type-keyed exit
  table) — succeeded iff the next state's stance for the actor is `top`; failed iff it is still
  `bottom` and the state the chain started from was also `bottom` (same guard family, so no
  positional claim is invented across an axis switch).
* **R2** — type → expected next-state family: `pass`/`takedown` ⇒ landed iff the next state's
  curated role is `top`, failed iff `bottom`; `guard pull` (type `guard`) ⇒ landed iff the next
  state's role is `bottom` (the puller ended up in a guard), failed iff `top`; back take (any
  label matching `lamas_chain.BACK_TAKE_TOKENS`) ⇒ landed iff the next state's curated role is
  `controlling`, failed iff it is a plain `top`/`bottom` position instead (the back was not
  taken, something else was); `escape` ⇒ landed iff the next state's stance is `neutral` or a
  `guard` state owned by the escaping actor, failed iff still a `control` state with `bottom`/
  `controlled` role; `submission` ⇒ landed iff the next node is the `finish` anchor (or the
  chain terminates on it), unresolved otherwise (the return-to-source failure is already R0's
  job and fires first in every composition that includes R0).
* **R3** — actor consistency, gated by `actor_readable` (the same reliability gate
  `attribution.bout_flags`/`taxonomy_kind.infer_transition_actions` already use): when the
  state following an action is owned by someone other than the action's own actor, the action
  failed (the position moved to the other side). Never infers `True` from actor agreement alone
  — staying in a state you already owned is not evidence the action landed.
* **R4** — the D7 anchor rule for the last own-actor node of a sequence: **not inferred here**,
  it is already the manual flag by owner decision (§D3b) — listed for completeness, not scored
  as a candidate.

## What is reported regardless of verdict

Per rule-set: resolution (both denominators), precision, both split by action type, on both
datasets. The chosen composition is the best one clearing the death rule; if none does, R0 alone
ships unchanged and the finding is reported as a rejection, not smoothed over.
