"""RRB read as a PROGRESSION: where a bout's chain moves an athlete, not only where it ends.

``analysis/lamas_chain.rrb`` scores each of Lamas' twelve actions by *who eventually finishes*
from it. That is a value per STATE. This module does the one thing that value makes possible and
that nothing in the report does yet: it walks a single bout's chain, reads the value of each
action **from one athlete's side**, and asks how that athlete's standing MOVED.

Two questions come out of the same walk:

* **progression** — the per-transition change in the athlete's RRB position, summed. Signed by
  side, so an opponent's action that raises *their* standing is the athlete's regression.
* **offensive / defensive cycles** — the runs in which the athlete sits on positive-RRB
  (offensive) or negative-RRB (defensive) ground: how many, how long in actions, and how often a
  defensive run is followed straight back by an offensive one (**recovery**).

Everything below is PRE-REGISTERED, before any corpus number, because each choice is a place a
different reading gives a different answer.

--------------------------------------------------------------------------------------------
1. THE VALUE FUNCTION, and why it is the SHARE and not the balance
--------------------------------------------------------------------------------------------

    V(s) = 2 · sub_share(s) − 1 ,   sub_share(s) = p_sub_own(s) / (p_sub_own(s) + p_sub_opp(s))

so ``V ∈ [−1, +1]``, zero exactly where the two absorption probabilities are equal, +1 where
every finish reachable from ``s`` is hers.

``balance = p_sub_own − p_sub_opp`` is `rrb`'s PRIMARY relation and it is **rejected here**, for
the reason §8.1 of ``docs/research/lamas_chain_divisions.md`` already gives for shipping the
share at all: the difference cannot separate "nothing happens here" from "something one-sided
happens here". Measured on the full corpus (913 final bouts, read-only 2026-08-26), every
non-``SUB`` balance sits inside ``[−0.018, +0.075]`` — §8.6's flattening — so a trajectory drawn
in balance space is a flat line with one spike at ``SUB`` and says nothing about the fight. The
share is balance's own bounded monotone transform (``r / (1 + r)`` of the odds), so **the sign of
V and the sign of balance are identical by construction** and only the magnitudes become
legible. Nothing is re-derived: the share is read straight off `rrb`'s published rows.

That single choice also fixes the weights artifact at the bottom of this module —
``w(s) = (V(s) + 1) / 2 = sub_share(s)`` — so the two deliverables are one quantity in two
presentations and can never disagree in sign.

--------------------------------------------------------------------------------------------
2. THE FALLBACK CHAIN, in order, and why the fallback is CENTRED
--------------------------------------------------------------------------------------------

``value_table`` resolves each state through exactly three tiers and records which one fired:

``rrb_sub_share``           the corpus's absorption is estimable AND this row cleared its own
                            bout-cluster gate. ``V = 2·sub_share − 1``.
``reward_risk_centered``    the row is refused above, but ``reward_risk`` has a gated score for
                            it. ``V = clip(score(s) − pooled_retention, −1, +1)``.
``none``                    neither. ``V`` is ``None`` — a refusal, not a zero.

**The centring is not cosmetic; without it the fallback is wrong.** ``reward_risk.score`` is
``P(next action is hers) − P(next action is the opponent's)``, and its zero means "the next
action is a coin flip", NOT "neither athlete is ahead". Measured pooled retention over every
scored appearance: **+0.412** on the full corpus, **+0.321** on the ADCC family, **+0.170** on
the IBJJF family — the same athlete simply tends to act again. Substituting the raw score would
put ELEVEN of twelve states on the positive side of the corpus and classify almost every step as
"offensive". Subtracting the corpus's own pooled score moves zero to "this action retains the
initiative no better than a typical action of this corpus", which is the sign the RRB share
carries. The clip to ``[−1, +1]`` matters: ``score − pooled`` can leave the range (measured
``TKD`` at −1.170 in the IBJJF family) and the value function is defined on ``[−1, +1]``.

**A mixed table is flagged and its magnitudes are not comparable to a pure one.** The two tiers
answer different questions at different horizons and, measured, at different dispersions —
tier-1 values spread over ``[−0.050, +0.195]`` outside ``SUB`` while tier-2 values spread over
roughly ``[−0.34, +0.25]``, about five times wider, because §8.6's propagation flattening does
not apply to a one-step reading. ``value_table`` therefore returns ``mixed_source`` and the doc
says plainly: **signs stay comparable, magnitudes do not.** On the corpus this was designed
against the fallback fires ZERO times globally and zero times for the ADCC family (all twelve
rows gate in both); it exists so a thin slice degrades in a named way instead of silently.

--------------------------------------------------------------------------------------------
3. POSITION, and how the side enters
--------------------------------------------------------------------------------------------

For a reference athlete ``R`` and step ``i`` of the chain::

    pos_R(i) = +V(state_i)   when the step's actor IS R
               −V(state_i)   when the step's actor is the opponent
               None          when the actor is unknown, is neither side, or V is None

The sign flip is exact rather than an approximation: `rrb` lifts the state space by side and the
two rows of a state are exact mirrors of each other (§8.2, asserted in
``tests/test_lamas_chain.py``), so ``−V`` IS the opponent-side row's value.

--------------------------------------------------------------------------------------------
4. PROGRESSION
--------------------------------------------------------------------------------------------

    Δ_i        = pos_R(i+1) − pos_R(i)        for every transition where BOTH ends are valued
    net        = Σ Δ_i
    gained     = Σ max(Δ_i, 0)                two disjoint arms on one denominator, the same
    lost       = Σ min(Δ_i, 0)                shape `reward_risk` uses; gained + lost == net
    per_action = net / (number of valued transitions)

A transition with an unvalued end is **excluded and counted** (``unvalued_transitions``), never
scored as zero. That is `analysis/transitions/build_graph.py`'s convention as `lamas_chain`
inherits it — an unknown is never charged — carried to a signed difference, where "charge it
zero" is not neutral: it would drag a valued neighbour's Δ toward the missing step.

``net`` telescopes to ``pos(last) − pos(first)`` across any contiguous valued stretch, which is
why ``gained``/``lost`` ship beside it: the pair is what distinguishes a fight that climbed
steadily from one that swung and landed in the same place. ``per_action`` is the rate, so bouts
of different length compare.

**Per exchange.** An EXCHANGE is a maximal run of consecutive steps by the same actor — the
chain's own unit of "whose turn it is", and the only place ``pos`` can change sign for a reason
other than the action itself. ``exchanges`` reports one row each with its endpoints' positions
and its Δ, and ``net_per_exchange`` normalises by their count.

--------------------------------------------------------------------------------------------
5. CYCLES
--------------------------------------------------------------------------------------------

    phase(i) = "off"       pos_R(i) > 0
               "def"       pos_R(i) < 0
               "neutral"   pos_R(i) == 0 exactly
               "unvalued"  pos_R(i) is None

A **cycle** is a maximal run of consecutive steps in one phase. Durations are counted in
ACTIONS, which is the chain's only clock — ``ts`` is absent or unusable on most of the corpus
(``ruleset_scoring.adcc_clock_feasibility``: zero bouts carry both a stage and a usable clock),
so a duration in seconds would be a fact about the annotation batch.

``unvalued`` is its own phase and is NEVER merged away. Bridging over it would let one missing
actor splice two offensive runs into a single long one, which is the one way this table could
invent a dominance streak that did not happen.

    recovery = a "def" run IMMEDIATELY followed by an "off" run
    collapse = an "off" run immediately followed by a "def" run

Adjacency is required: ``def → neutral → off`` is **not** a recovery, because the stretch in
between is ground on which nothing was measured and crediting it to the athlete would be reading
the gap as progress. Denominators drop the last run of a chain, which has no successor — again
`build_graph`'s rule, and the same reason ``lamas_chain`` gives for it.

⚠️ **The recovery RATE is 1.00 by construction whenever only ``off`` and ``def`` occur**, because
two-phase runs alternate by definition. That is the ORDINARY case on this corpus — no state's
value is exactly zero and the actor gate has already removed the unknown actors — so the rate is
published with ``recovery_degenerate`` beside it rather than quietly presented as a finding, and
the quantity that carries information is the CYCLE LENGTH: how many actions she spends underwater
each time she goes under (``def_mean_len`` / ``mean_def_cycle_len``) and how often she goes
(``def_cycles``). The rate becomes informative only where neutral or unvalued ground exists —
a thin corpus, or a mixed value table.

--------------------------------------------------------------------------------------------
6. WHAT IS GATED
--------------------------------------------------------------------------------------------

A single bout's trajectory is a DESCRIPTION of that chain and is always computed; it carries
``actor_reliable`` through so a renderer can refuse it. The per-athlete aggregation is a CLAIM
and therefore consumes only chains that clear ``lamas_chain._actor_reliability`` — the same
refusal `reward_risk` and `rrb` already apply, and it matters more here, not less: every number
in this module is read through ``actor_id``, and a bout that files every event under one athlete
would show a monotone climb by construction. Intervals are the pair the rest of the report uses
— Wilson over steps for the shares, bout-clustered percentile bootstrap for ``per_action`` —
gated on ``stats_rigor.coverage`` over per-BOUT contributions, and withheld together below it.

Privacy class **A, public competition data**: every input is a ``matches`` row from published
footage. Nothing here reads a user graph or a session.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from analysis.lamas_chain import N_BOOT, SEED, STATES, Chain
from analysis.stats_rigor import bootstrap_ci, coverage, wilson

#: The fallback chain of section 2, in the order it is tried. Published so a consumer reads the
#: precedence off the artifact instead of off this file.
VALUE_SOURCES: tuple[str, ...] = ("rrb_sub_share", "reward_risk_centered", "none")

#: Phase vocabulary of section 5, fixed so a renderer never invents a fifth.
PHASES: tuple[str, ...] = ("off", "def", "neutral", "unvalued")

#: The share a state with NO usable value is given when a non-negative weight is required
#: (``weights_from_value_table``). 0.5 is the only honest substitution: in share space it means
#: "as likely to end in her finish as in the opponent's", i.e. the action carries no preference
#: and renormalisation makes it interchangeable with any other unvalued action.
NEUTRAL_SHARE = 0.5

#: Strictly-positive floor on a published weight. It exists so ``sum(weights) > 0`` is a
#: STRUCTURAL invariant of the artifact rather than a lucky property of today's corpus — a
#: consumer normalising over a subset of actions must never divide by zero. It does not bind on
#: any measured value (the minimum observed share is 0.451).
#: ponytail: a constant floor, not a smoothing prior — if a future corpus ever produces a share
#: near zero, replace it with an explicit shrinkage toward NEUTRAL_SHARE and say so in the PR.
WEIGHT_FLOOR = 0.01

#: Everything shipped in the weights artifact is rounded here. Determinism across machines: the
#: absorption solve goes through ``numpy.linalg.inv``, whose last bits are BLAS-dependent, and a
#: byte-identical artifact is a contract this repo actually checks (``--check``).
WEIGHT_PLACES = 4


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def pooled_retention(rr_block: Mapping[str, Any]) -> float | None:
    """The corpus's own base rate of "the same athlete acts next", as a `reward_risk` score.

    ``(Σ reward_k − Σ risk_k) / Σ n`` over every state — one measured constant, not a fit, and
    the zero point the tier-2 fallback is centred on (section 2). ``None`` when nothing scored.
    """
    rows = rr_block.get("rows") or []
    n = sum(int(r["n"]) for r in rows)
    if not n:
        return None
    k = sum(int(r["reward"]["k"]) - int(r["risk"]["k"]) for r in rows)
    return k / n


def value_table(rrb_block: Mapping[str, Any], rr_block: Mapping[str, Any]) -> dict[str, Any]:
    """The pre-registered value function over ``lamas_chain.STATES``, with its fallback chain.

    ``rrb_block`` is ``lamas_chain.rrb``'s output and ``rr_block`` is ``lamas_chain.reward_risk``'s,
    both computed on the SAME chains by the caller. This module never selects bouts — the same
    rule ``lamas_chain.markov_block`` follows, so a trajectory and the value it is read against
    can never describe different universes.

    Each state resolves through ``VALUE_SOURCES`` in order (section 2). The returned rows carry
    the provenance a consumer needs to refuse a value: which tier fired, the appearance and bout
    counts behind it, and ``n_terminal`` — the appearances that ARE the chain's last step, which
    is what makes ``SUB``'s value partly circular (§8.6).
    """
    corpus_ok = bool((rrb_block.get("absorption") or {}).get("estimable"))
    rrb_rows = {r["state"]: r for r in rrb_block.get("rows") or []}
    rr_rows = {r["state"]: r for r in rr_block.get("rows") or []}
    pooled = pooled_retention(rr_block)

    states: dict[str, dict[str, Any]] = {}
    fired: Counter[str] = Counter()
    for s in STATES:
        a = rrb_rows.get(s) or {}
        b = rr_rows.get(s) or {}
        share = a.get("sub_share")
        value: float | None
        if corpus_ok and a.get("gated") and share is not None:
            source, value = "rrb_sub_share", _clip(2.0 * float(share) - 1.0)
        elif b.get("gated") and b.get("score") is not None and pooled is not None:
            source, value = "reward_risk_centered", _clip(float(b["score"]) - pooled)
        else:
            source, value = "none", None
        fired[source] += 1
        states[s] = {
            "state": s, "value": value, "source": source,
            "sub_share": share, "balance": a.get("balance"),
            "n": int(a.get("n") or 0), "bouts": int(a.get("bouts") or 0),
            "n_terminal": int(a.get("n_terminal") or 0),
            "rr_score": b.get("score"), "rr_n": int(b.get("n") or 0),
            "reason_code": None if source == "rrb_sub_share" else (
                a.get("reason_code") or b.get("coverage", {}).get("reason_code")),
        }

    return {
        "states": states,
        "order": list(STATES),
        "sources": list(VALUE_SOURCES),
        "n_by_source": {k: fired[k] for k in VALUE_SOURCES},
        "mixed_source": fired["reward_risk_centered"] > 0,
        "pooled_retention": None if pooled is None else round(pooled, 4),
        "corpus_estimable": corpus_ok,
        "corpus_reason_code": (rrb_block.get("absorption") or {}).get("reason_code"),
        "absorbing_bouts": int((rrb_block.get("absorption") or {}).get("absorbing_bouts") or 0),
        "definition": (
            "V(s) = 2·sub_share(s) − 1, com sub_share vindo de `lamas_chain.rrb`. O `balance` "
            "foi REJEITADO como função de valor: é a relação primária do §8.1, mas fora de SUB "
            "ele cabe todo em [−0,018, +0,075] no corpus inteiro (o achatamento do §8.6), então "
            "uma trajetória desenhada nele é uma linha reta. A share é a transformada monótona "
            "limitada do próprio balance, logo o SINAL é idêntico por construção e só a "
            "magnitude fica legível. Cadeia de recuo: rrb_sub_share → reward_risk CENTRADO na "
            "retenção agregada do corpus (o zero do `reward_risk` é 'cara ou coroa', não "
            "'ninguém na frente' — medido +0,412 no corpus inteiro) → nenhum valor, que é uma "
            "RECUSA e não um zero."),
    }


def _value_of(values: Mapping[str, Any], state: str) -> float | None:
    row = (values.get("states") or {}).get(state)
    return None if row is None else row.get("value")


class Position(NamedTuple):
    """One step of the chain, read from the reference athlete's side."""

    i: int
    state: str
    is_ref: bool | None      # None when the actor is unknown or is neither corner
    value: float | None      # V(state), before the side flip
    pos: float | None        # +V when hers, −V when the opponent's
    phase: str


def positions(chain: Chain, ref: Any, values: Mapping[str, Any]) -> list[Position]:
    """The reference athlete's signed RRB standing at every step (section 3)."""
    ref_s = None if ref is None else str(ref)
    out: list[Position] = []
    for i, step in enumerate(chain.steps):
        actor = None if step.actor_id is None else str(step.actor_id)
        is_ref = None if (actor is None or ref_s is None) else (actor == ref_s)
        v = _value_of(values, step.state)
        pos = None if (is_ref is None or v is None) else (v if is_ref else -v)
        if pos is None:
            phase = "unvalued"
        elif pos > 0:
            phase = "off"
        elif pos < 0:
            phase = "def"
        else:
            phase = "neutral"
        out.append(Position(i, step.state, is_ref, v, pos, phase))
    return out


class Run(NamedTuple):
    start: int
    end: int      # inclusive
    key: Any

    @property
    def n_steps(self) -> int:
        return self.end - self.start + 1


def _runs(keys: Sequence[Any]) -> list[Run]:
    """Maximal runs of equal key. One helper for both cycles (phase) and exchanges (actor)."""
    out: list[Run] = []
    for i, k in enumerate(keys):
        if out and out[-1].key == k:
            out[-1] = Run(out[-1].start, i, k)
        else:
            out.append(Run(i, i, k))
    return out


def _cycles(pos: Sequence[Position]) -> dict[str, Any]:
    """Offensive/defensive cycles, their durations in actions, and recovery (section 5)."""
    runs = _runs([p.phase for p in pos])
    by_phase: dict[str, list[int]] = {p: [] for p in PHASES}
    for r in runs:
        by_phase[str(r.key)].append(r.n_steps)

    # A run that ENDS the chain has no successor and leaves both denominators — `build_graph`'s
    # rule, the same one `lamas_chain` inherits for appearances.
    trans = [(str(a.key), str(b.key)) for a, b in zip(runs, runs[1:])]
    def_with_succ = sum(1 for a, _ in trans if a == "def")
    off_with_succ = sum(1 for a, _ in trans if a == "off")
    recoveries = sum(1 for a, b in trans if a == "def" and b == "off")
    collapses = sum(1 for a, b in trans if a == "off" and b == "def")

    valued = sum(sum(by_phase[p]) for p in ("off", "def", "neutral"))
    present = sorted({str(r.key) for r in runs})
    out: dict[str, Any] = {
        "runs": [{"phase": str(r.key), "start": r.start, "end": r.end, "n_steps": r.n_steps}
                 for r in runs],
        "valued_steps": valued,
        "recoveries": recoveries, "def_runs_with_successor": def_with_succ,
        "collapses": collapses, "off_runs_with_successor": off_with_succ,
        "phases_present": present,
        # MEASURED AND PUBLISHED, not hidden: with only `off` and `def` present, runs alternate
        # by definition, so every defensive run that has a successor is followed by an offensive
        # one and `recoveries / def_runs_with_successor` is 1.00 BY CONSTRUCTION. That is the
        # ordinary case on this corpus — no state's value is exactly zero and the actor gate
        # removes the unknown actors — so the informative quantity is how LONG she stays under
        # (`def_mean_len`) and how often she goes there (`def_cycles`), never the rate.
        "recovery_degenerate": set(present) <= {"off", "def"},
    }
    for p in PHASES:
        lens = by_phase[p]
        out[f"{p}_cycles"] = len(lens)
        out[f"{p}_steps"] = sum(lens)
        out[f"{p}_mean_len"] = round(sum(lens) / len(lens), 4) if lens else None
        out[f"{p}_max_len"] = max(lens) if lens else 0
        out[f"{p}_share"] = round(sum(lens) / valued, 4) if valued else None
    return out


def trajectory(chain: Chain, ref: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """One bout, one athlete: the whole walk — positions, Δs, exchanges and cycles.

    Descriptive of THIS chain, so it is computed for any chain; ``actor_reliable`` and
    ``actor_refusal`` travel through untouched so a renderer or an aggregator can refuse it
    (section 6). Nothing here is gated: a single fight is not a sample of anything.
    """
    pos = positions(chain, ref, values)
    deltas: list[float | None] = []
    for a, b in zip(pos, pos[1:]):
        deltas.append(None if (a.pos is None or b.pos is None) else round(b.pos - a.pos, 6))
    scored = [d for d in deltas if d is not None]
    net = round(sum(scored), 6)
    valued_pos = [p for p in pos if p.pos is not None]

    ex_runs = _runs([p.is_ref for p in pos])
    exchanges: list[dict[str, Any]] = []
    for r in ex_runs:
        a, b = pos[r.start], pos[r.end]
        exchanges.append({
            "start": r.start, "end": r.end, "n_steps": r.n_steps, "is_ref": r.key,
            "pos_start": a.pos, "pos_end": b.pos,
            "delta": (None if (a.pos is None or b.pos is None)
                      else round(b.pos - a.pos, 6)),
        })

    return {
        "bout_id": chain.bout_id,
        "athlete": None if ref is None else str(ref),
        "actor_reliable": chain.actor_reliable,
        "actor_refusal": chain.actor_refusal,
        "absorbs": chain.absorbs,
        "steps": [p._asdict() for p in pos],
        "deltas": deltas,
        "n_steps": len(pos),
        "n_valued_steps": len(valued_pos),
        "n_transitions": len(deltas),
        "n_valued_transitions": len(scored),
        "unvalued_transitions": len(deltas) - len(scored),
        "net": net,
        "gained": round(sum(d for d in scored if d > 0), 6),
        "lost": round(sum(d for d in scored if d < 0), 6),
        "per_action": round(net / len(scored), 6) if scored else None,
        "start_pos": valued_pos[0].pos if valued_pos else None,
        "end_pos": valued_pos[-1].pos if valued_pos else None,
        "exchanges": exchanges,
        "n_exchanges": len(exchanges),
        "net_per_exchange": round(net / len(exchanges), 6) if exchanges else None,
        "cycles": _cycles(pos),
        "value_source_mixed": bool(values.get("mixed_source")),
    }


def _estimate(k: int, n: int, cov: Any) -> dict[str, Any]:
    """A Wilson cell, or the count with the interval withheld — `lamas_chain._cell`'s rule.

    Re-stated rather than imported because that one is private to its module; the rule it
    encodes is the report's, not one module's: the observed proportion is a fact about the
    corpus and survives, the INTERVAL is a claim about a population and goes below the gate.
    """
    d = wilson(k, n).to_dict()
    if cov.estimable:
        return {**d, "estimable": True, "coverage": cov.grade}
    return {**d, "lo": None, "hi": None, "half": None, "grade": "none", "estimable": False,
            "coverage": cov.grade, "reason_code": cov.reason_code}


def athlete_progression(pairs: Sequence[tuple[Any, Chain]], values: Mapping[str, Any],
                        n_boot: int = N_BOOT) -> dict[str, Any]:
    """Per-athlete aggregation of the trajectories, with the report's usual gates and intervals.

    ``pairs`` is ``(athlete_id, chain)`` — the caller decides which corner of which bout it is
    asking about, because this module never selects fights. Only chains that clear
    ``lamas_chain``'s actor refusal enter (section 6); the refusals are counted, not dropped
    silently.

    Three claims per athlete, each gated on the same unit — the number of BOUTS behind the
    number, weighted by what each bout contributed:

    ``per_action``    the mean per-transition Δ. No closed form, so a bout-clustered percentile
                      bootstrap (``stats_rigor.bootstrap_ci``, seeded), withheld below the gate.
    ``off_share``     offensive steps over valued steps, Wilson.
    ``recovery_rate`` recoveries over defensive runs that HAVE a successor run, Wilson.

    Rows are ordered by valued transitions descending, then by athlete id, so a rebuild cannot
    reshuffle ties.
    """
    from statistics import fmean

    usable: list[tuple[str, Chain]] = []
    refused: Counter[str] = Counter()
    for ref, ch in pairs:
        if ch.actor_reliable and ref is not None:
            usable.append((str(ref), ch))
        else:
            refused[ch.actor_refusal or ("no_reference_athlete" if ref is None
                                         else "unknown")] += 1

    deltas: dict[str, list[float]] = defaultdict(list)
    groups: dict[str, list[str]] = defaultdict(list)
    steps_by_bout: dict[str, Counter[str]] = defaultdict(Counter)
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    for ref, ch in usable:
        t = trajectory(ch, ref, values)
        for d in t["deltas"]:
            if d is not None:
                deltas[ref].append(float(d))
                groups[ref].append(t["bout_id"])
        c = t["cycles"]
        steps_by_bout[ref][t["bout_id"]] += int(t["n_valued_steps"])
        acc = tally[ref]
        acc["bouts"] += 1
        acc["valued_steps"] += int(t["n_valued_steps"])
        acc["degenerate_bouts"] += int(bool(c["recovery_degenerate"]))
        for k in ("off_steps", "def_steps", "neutral_steps", "unvalued_steps",
                  "off_cycles", "def_cycles", "recoveries", "collapses",
                  "def_runs_with_successor", "off_runs_with_successor"):
            acc[k] += int(c[k])

    rows: list[dict[str, Any]] = []
    for ref, acc in tally.items():
        vals, gl = deltas[ref], groups[ref]
        cov = coverage(list(steps_by_bout[ref].values()))
        lo: float | None = None
        hi: float | None = None
        mean = round(fmean(vals), 6) if vals else None
        if cov.estimable and vals and n_boot:
            _, b_lo, b_hi = bootstrap_ci(vals, fmean, n_boot=n_boot, groups=gl, seed=SEED)
            lo, hi = round(b_lo, 6), round(b_hi, 6)
        rows.append({
            "athlete": ref,
            "bouts": acc["bouts"],
            "n_valued_transitions": len(vals),
            "net_total": round(sum(vals), 6) if vals else None,
            "per_action": mean, "per_action_lo": lo, "per_action_hi": hi,
            "off_share": _estimate(acc["off_steps"], acc["valued_steps"], cov),
            "def_share": _estimate(acc["def_steps"], acc["valued_steps"], cov),
            "recovery_rate": _estimate(acc["recoveries"], acc["def_runs_with_successor"], cov),
            "collapse_rate": _estimate(acc["collapses"], acc["off_runs_with_successor"], cov),
            # True when every bout of hers had only `off` and `def` ground, in which case the
            # two rates above are 1.00 by construction and must not be read as a finding. The
            # informative pair is the CYCLE LENGTHS beside them: how long she stays under, and
            # how long she stays on top.
            "recovery_degenerate": acc["degenerate_bouts"] == acc["bouts"],
            "off_cycles": acc["off_cycles"], "def_cycles": acc["def_cycles"],
            "mean_off_cycle_len": (round(acc["off_steps"] / acc["off_cycles"], 4)
                                   if acc["off_cycles"] else None),
            "mean_def_cycle_len": (round(acc["def_steps"] / acc["def_cycles"], 4)
                                   if acc["def_cycles"] else None),
            "valued_steps": acc["valued_steps"], "unvalued_steps": acc["unvalued_steps"],
            "gated": bool(cov.estimable and vals),
            "coverage": cov.to_dict(),
        })
    rows.sort(key=lambda r: (-int(r["n_valued_transitions"]), str(r["athlete"])))

    return {
        "rows": rows,
        "bouts_used": len(usable),
        "bouts_refused": dict(refused),
        "value_source_mixed": bool(values.get("mixed_source")),
        "boot": {"n": n_boot, "unit": "bout", "kind": "cluster percentile", "seed": SEED},
        "method": (
            "Progressão = soma dos Δ por transição da posição RRB da atleta de referência "
            "(pos = +V quando a ação é dela, −V quando é da adversária). Transição com ponta "
            "sem valor fica FORA e é contada (`unvalued_transitions`) — convenção do "
            "build_graph: desconhecido nunca é cobrado. Ciclos = corridas máximas de fase "
            "(off/def/neutral/unvalued), duração em AÇÕES, e recuperação = corrida `def` "
            "seguida IMEDIATAMENTE de uma `off`. Só cadeias que passam pela recusa de "
            "atribuição do `lamas_chain` entram nesta agregação."),
        "caveats": list(PROGRESSION_CAVEATS),
    }


PROGRESSION_CAVEATS: tuple[str, ...] = (
    "A função de valor é a SHARE, não o `balance`. Fora de SUB, todo `balance` do corpus cabe "
    "em [−0,018, +0,075] (§8.6): uma trajetória desenhada nele seria uma linha reta com um pico "
    "em SUB. A share é a transformada monótona limitada do mesmo par de probabilidades, então o "
    "SINAL é idêntico e só a magnitude fica legível — nenhum sinal novo foi criado.",
    "O valor de SUB é parcialmente CIRCULAR: boa parte das aparições de SUB é o passo terminal "
    "que absorve (`n_terminal` viaja em cada linha da tabela de valores). Uma trajetória que "
    "termina em SUB sobe porque a luta acabou ali, não porque a ação prevê algo.",
    "TUDO aqui é lido através do `actor_id`, mais ainda que o `reward_risk`: o lado de cada "
    "passo decide o sinal da posição. Uma luta que arquiva todos os eventos sob uma atleta "
    "mostraria uma subida monótona por construção, e é por isso que a agregação por atleta só "
    "aceita cadeias que passam pela recusa (`one_sided` + `single_actor`).",
    "Tabela de valores MISTA (`mixed_source`) mistura dois horizontes: o nível 1 é a absorção "
    "propagada e o nível 2 é a iniciativa de um passo, cuja dispersão medida é ~5× maior porque "
    "o achatamento do §8.6 não se aplica a uma leitura de um passo. Os SINAIS continuam "
    "comparáveis; as MAGNITUDES não.",
    "A duração do ciclo é contada em AÇÕES, nunca em segundos: nenhuma luta do corpus carrega "
    "`stage` e relógio utilizável ao mesmo tempo (`ruleset_scoring.adcc_clock_feasibility`), "
    "então uma duração em tempo seria um fato sobre o lote de anotação.",
    "A fase `unvalued` NUNCA é costurada: unir duas corridas ofensivas por cima de um passo sem "
    "atribuição inventaria uma sequência de domínio que não aconteceu.",
    "⚠️ A TAXA DE RECUPERAÇÃO É 1,00 POR CONSTRUÇÃO quando a luta só tem terreno `off` e `def` "
    "— corridas de duas fases se alternam por definição. É o caso ORDINÁRIO neste corpus "
    "(nenhum estado vale exatamente zero e o portão de atribuição já tirou os atores "
    "desconhecidos), e por isso `recovery_degenerate` viaja em cada linha. O que carrega "
    "informação são os COMPRIMENTOS: quantas ações ela passa embaixo de cada vez "
    "(`mean_def_cycle_len`) e quantas vezes ela vai parar lá (`def_cycles`).",
)


# ── the canonical action-weights artifact ──────────────────────────────────────

def weights_from_value_table(values: Mapping[str, Any]) -> dict[str, Any]:
    """The same value function, presented NON-NEGATIVE, for ELO-delta redistribution.

    The transform is pre-registered and is the value function's own inverse map::

        w(s) = max( (V(s) + 1) / 2 , WEIGHT_FLOOR )    = max( sub_share(s), WEIGHT_FLOOR )
        w(s) = NEUTRAL_SHARE                            when V(s) is None

    so a weight IS the share of the finishes reachable from ``s`` that are HERS, in ``[0, 1]``.

    **Why the share and not a shifted balance.** ``(balance + 1) / 2`` is also non-negative and
    bounded, and it is rejected: the measured balances outside ``SUB`` span 0.09 of a range
    centred on 0.5, so every action would weigh 0.5 ± 0.04 and the redistribution would be
    uniform in all but name. The share is a monotone function of the same evidence with a
    legible spread (measured on the full corpus: 0.475 to 0.807), and it is bounded by
    construction, which a raw ratio is not (§8.1 rejects the ratio for exactly that).

    **Weights are NOT pre-normalised.** A consumer distributing an ELO delta renormalises over
    the actions actually present in the sequence it is scoring; publishing a sum-to-one vector
    over twelve states would be wrong for every subset. ``WEIGHT_FLOOR`` is what makes that
    renormalisation safe as an invariant rather than as an observation.

    **The spread is small and that is the measurement, not a defect.** §8.6: propagation flattens
    the signal because the chain's mixing time is shorter than its absorption time. This artifact
    publishes what was measured. A consumer that needs sharper differentiation is applying a
    transform of its own and must justify it in its own PR.
    """
    out: dict[str, Any] = {}
    for s in STATES:
        row = (values.get("states") or {}).get(s) or {}
        v = row.get("value")
        w = NEUTRAL_SHARE if v is None else max((float(v) + 1.0) / 2.0, WEIGHT_FLOOR)
        out[s] = {
            "weight": round(w, WEIGHT_PLACES),
            "source": row.get("source", "none"),
            "n": row.get("n", 0), "bouts": row.get("bouts", 0),
            "n_terminal": row.get("n_terminal", 0),
            "balance": row.get("balance"), "sub_share": row.get("sub_share"),
            "reason_code": row.get("reason_code"),
        }
    return out


def _demo() -> None:
    """One runnable check of the non-trivial logic, no framework, no DB."""
    from analysis.lamas_chain import chain_of, reward_risk, rrb

    # A hand-made value table: BTK is hers-favouring, PGD is against, GPSA is dead neutral,
    # SWPA has no value at all.
    vt: dict[str, Any] = {"states": {s: {"state": s, "value": None, "source": "none", "n": 0,
                                         "bouts": 0, "n_terminal": 0, "balance": None,
                                         "sub_share": None, "reason_code": "synthetic"}
                                     for s in STATES},
                          "mixed_source": False}
    for s, v in (("CDP", 0.1), ("TKDA", -0.2), ("BTK", 0.5), ("PGD", -0.4), ("GPSA", 0.0)):
        vt["states"][s].update(value=v, source="rrb_sub_share", sub_share=(v + 1) / 2)

    def ev(t: str, label: str, actor: str, ok: bool | None = None) -> dict[str, Any]:
        e: dict[str, Any] = {"type": t, "label": label, "actor_id": actor}
        if ok is not None:
            e["successful"] = ok
        return e

    # X clinches (+0.1), Y clinches (−0.1), X takes the back (+0.5), Y pulls guard (+0.4 for X,
    # because a guard pull is worth −0.4 to whoever does it), X attempts a takedown (−0.2).
    bout = {"id": "t", "a_id": "X", "b_id": "Y", "seq": [
        ev("control", "Collar Tie", "X"),
        ev("control", "Collar Tie", "Y"),
        ev("control", "Back Control", "X", True),
        ev("guard", "Guard Pull", "Y"),
        ev("takedown", "Single Leg", "X"),
    ]}
    ch = chain_of(bout)
    assert [s.state for s in ch.steps] == ["CDP", "CDP", "BTK", "PGD", "TKDA"]
    t = trajectory(ch, "X", vt)
    assert [round(float(p["pos"] or 0), 4) for p in t["steps"]] == [0.1, -0.1, 0.5, 0.4, -0.2]
    assert t["deltas"] == [-0.2, 0.6, -0.1, -0.6]
    assert t["net"] == -0.3 and t["gained"] == 0.6 and round(t["lost"], 6) == -0.9
    assert t["net"] == round(t["gained"] + t["lost"], 6)
    # Telescoping: with no gaps, the sum of Δ IS end − start.
    assert round(float(t["end_pos"]) - float(t["start_pos"]), 6) == t["net"]
    c = t["cycles"]
    assert [r["phase"] for r in c["runs"]] == ["off", "def", "off", "def"]
    assert (c["off_cycles"], c["def_cycles"]) == (2, 2)
    assert c["off_steps"] == 3 and c["def_steps"] == 2      # steps 0,2,3 vs 1,4
    assert c["off_max_len"] == 2 and c["def_max_len"] == 1
    # Runs: off | def | off off | def. The trailing `def` ends the chain, so it is out of the
    # recovery denominator; both `off` runs have a successor and both collapse.
    assert c["recoveries"] == 1 and c["def_runs_with_successor"] == 1
    assert c["collapses"] == 2 and c["off_runs_with_successor"] == 2
    # Exchanges are actor runs: X | Y | X | Y | X — five of them, one step each.
    assert t["n_exchanges"] == 5 and all(e["n_steps"] == 1 for e in t["exchanges"])
    # The opponent's walk is the exact mirror.
    ty = trajectory(ch, "Y", vt)
    assert ty["net"] == -t["net"] and ty["cycles"]["off_steps"] == c["def_steps"]

    # An unvalued state breaks the chain instead of scoring zero, and is never bridged over.
    gap = {"id": "g", "a_id": "X", "b_id": "Y", "seq": [
        ev("control", "Collar Tie", "X"),          # CDP  +0.1  off
        ev("sweep", "Sweep", "X"),                 # SWPA  no value -> unvalued
        ev("control", "Collar Tie", "X"),          # CDP  +0.1  off
    ]}
    tg = trajectory(chain_of(gap), "X", vt)
    assert tg["deltas"] == [None, None]
    assert tg["n_valued_transitions"] == 0 and tg["unvalued_transitions"] == 2
    assert [r["phase"] for r in tg["cycles"]["runs"]] == ["off", "unvalued", "off"]
    assert tg["cycles"]["off_cycles"] == 2      # NOT spliced into one
    assert tg["per_action"] is None

    # The value table's fallback chain, on a corpus where nothing absorbs: tier 1 is impossible,
    # so a gated reward_risk row falls to tier 2 CENTRED, and the rest to tier 3.
    won = {"id": "w", "win_type": "DECISION", "winner": "X", "a_id": "X", "b_id": "Y", "seq": [
        ev("control", "Collar Tie", "X"), ev("takedown", "Trip", "X"),
        ev("pass", "Knee Cut", "Y"), ev("control", "Back Control", "Y"),
    ]}
    chains = [chain_of(won)]
    vt2 = value_table(rrb(chains, n_boot=0), reward_risk(chains, n_boot=0))
    assert vt2["corpus_estimable"] is False
    assert vt2["n_by_source"]["rrb_sub_share"] == 0
    assert vt2["mixed_source"] == (vt2["n_by_source"]["reward_risk_centered"] > 0)
    # Every state resolves to exactly one declared source, and only tier 3 has a null value.
    for s in STATES:
        r = vt2["states"][s]
        assert r["source"] in VALUE_SOURCES
        assert (r["value"] is None) == (r["source"] == "none")
        assert r["value"] is None or -1.0 <= r["value"] <= 1.0

    # Weights: non-negative, floored, and a refused state lands on the neutral share.
    w = weights_from_value_table(vt)
    assert w["BTK"]["weight"] == 0.75 and w["PGD"]["weight"] == 0.3
    assert w["GPSA"]["weight"] == 0.5 and w["SWPA"]["weight"] == NEUTRAL_SHARE
    assert w["SWPA"]["source"] == "none"
    assert all(x["weight"] >= WEIGHT_FLOOR for x in w.values())
    assert sum(x["weight"] for x in w.values()) > 0

    print("rrb_progression self-check ok")


if __name__ == "__main__":
    _demo()
