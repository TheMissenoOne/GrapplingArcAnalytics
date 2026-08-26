"""Ruleset-specific scoring over the Lamas action space — census first, claims second.

The question this module was built to answer is "what is the chance each action SCORES, under
each ruleset". It answers a narrower one, because the corpus cannot support the first, and the
reason is structural rather than a matter of sample size. Stated here, before any number, so it
travels with the code that produces them (``docs/research/ruleset_scoring.md`` carries the
measurements):

    P(a scores | ruleset r) = P(a lands) · 1[points_r(a) > 0] · P(the moment is scoreable)

Three factors, three different problems.

1. ``points_r`` is a LOOKUP, not a probability. Over the seven symbols this corpus can
   resolve, the IBJJF and ADCC tables differ on exactly one value (BTK 4 vs 3). Everything the
   two rule books genuinely disagree about — advantages, knee-on-belly, mount, ADCC's 4-point
   takedown-that-passes, ADCC's negative phase — has no symbol in the Lamas vocabulary at all.
   So the ruleset can barely enter the number even in principle.

2. ``P(the moment is scoreable)`` needs ADCC's match clock and phase, and the corpus has
   neither together. Measured (2026-08-26, 185 adcc-family bouts): 22 carry a ``stage``, which
   is what picks the qualifying/final/overtime profile; 45 carry a usable clock; and **zero
   carry both** — ``ts_origin`` is ``bout_relative`` on no bout at all. The intersection of
   "knows which ADCC profile applies" and "knows when in the bout this happened" is EMPTY. This
   factor is therefore fixed at 1 and the negative window is never applied;
   ``ADCC_NEGATIVE_WINDOW`` exists as an off-by-default switch so the refusal is visible rather
   than silent, and ``adcc_clock_feasibility`` recomputes the number instead of trusting it.

3. ``P(a lands)`` is the one empirical term, and it reads the ``successful`` flag, which
   ``lamas_chain`` rule 3 sends to the ATTEMPT state when absent. Presence measured per family:
   ibjjf 59.8%, adcc 53.5%, cji 37.4%, other 24.1%. Those are four annotation batches, not four
   ways of grappling, so a point estimate of the landing rate is comparable WITHIN a family and
   refused ACROSS them — the same verdict, for the same reason, that
   ``scripts/bracket_export._adcc_annotation`` already reaches for the ADCC cycle.

Every figure in this docstring is a corpus MEASUREMENT and the corpus is live — four ADCC bouts
landed mid-study and moved the adcc coverage from 52.6% to 53.5%.
``docs/research/ruleset_scoring.md`` carries the read date and the full tables;
``scripts/ruleset_scoring_report.py`` regenerates them.

What this module publishes instead of a point estimate is a **bound**. Missing annotation is
treated as missing rather than as failure, so the landing rate ships as a Manski-style envelope
— ``[missing counted as failure, missing counted as success]`` — with the complete-case rate
between them. The envelope's WIDTH is the missing rate, which turns the annotation problem from
a caveat into a reported quantity, and an envelope stays honest across families where a point
estimate does not.

Everything downstream inherits the bound: the expected-points layer is evaluated at both ends,
so it publishes an interval rather than a number.

Privacy class **A, public competition data**: every input is a ``matches`` row from published
footage. Nothing here reads a user graph or a session.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

from analysis.lamas_chain import FAMILY, STATES, chain_of
from analysis.stats_rigor import (
    Coverage,
    benjamini_hochberg,
    compare_proportions,
    coverage,
    wilson,
)

DEFAULT_EVENT_RULESETS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "scouting" / "event_rulesets.json"
)

# The seven-symbol collapse of ``lamas_chain.STATES``: each attempt/success pair folded to one
# symbol, CDP and PGD (which have no pair) carried through. This is the resolution at which the
# corpus can be read WITHOUT the annotation batch deciding the answer, and it is therefore the
# honest primary — the same move `bracket_export` makes when it calls the family-level `anchor`
# the one cross-corpus reading its block supports.
SYMBOLS: tuple[str, ...] = ("CDP", "PGD", "SWP", "TKD", "GPS", "BTK", "SUB")
SYMBOL_INDEX = {s: i for i, s in enumerate(SYMBOLS)}

# state -> symbol. Built from `lamas_chain.FAMILY` rather than written out, so a change to the
# state space cannot leave this table behind.
SYMBOL_OF: dict[str, str] = {"CDP": "CDP", "PGD": "PGD"}
for _attempt, _success in FAMILY.values():
    SYMBOL_OF[_attempt] = SYMBOL_OF[_success] = _success
assert set(SYMBOL_OF) == set(STATES), "SYMBOL_OF must cover the Lamas state space exactly"
assert set(SYMBOL_OF.values()) == set(SYMBOLS)

# The success half of each symbol — the state that means "this action LANDED". CDP and PGD have
# no attempt/success split in the Lamas space and are their own success, which is why neither
# carries points in either table: nothing about achieving them is a score.
SUCCESS_STATE: dict[str, str] = {s: s for s in ("CDP", "PGD")}
for _a, _s in FAMILY.values():
    SUCCESS_STATE[_s] = _s

#: Pre-registered accumulation horizon for :func:`expected_points`. Three transitions, chosen
#: before any number was read, to match ``lamas_chain.PATHWAY_LENGTH``'s two-transitions-into-a
#: -submission reading plus one — long enough that a takedown can reach a pass and then the
#: back, short enough that the first-order chain is not being extrapolated past its evidence.
HORIZON = 3

#: False-discovery rate the contrast declares, fixed before the corpus was read at this
#: resolution. One number, one report, so a reader is never shown a q-value against a threshold
#: that was chosen after seeing it.
BH_ALPHA = 0.05

#: Off. See the module docstring, factor 2— the corpus cannot place an action inside or
#: outside ADCC's negative phase, so applying the penalty would be inventing the fact that
#: decides it. Flipping this to True is a research switch, never a publication setting.
ADCC_NEGATIVE_WINDOW = False


class RulesetError(ValueError):
    pass


@lru_cache(maxsize=4)
def load_event_rulesets(path: str | None = None) -> dict[str, Any]:
    """The versioned event -> ruleset-family map, generated by
    ``scripts/build_event_rulesets.py``."""
    p = Path(path) if path else DEFAULT_EVENT_RULESETS_PATH
    try:
        doc: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RulesetError(f"não foi possível ler {p}: {exc}") from exc
    for key in ("events", "points", "null_event", "families"):
        if key not in doc:
            raise RulesetError(f"{p} sem a chave obrigatória {key!r}")
    return doc


def family_of(event: str | None, doc: Mapping[str, Any] | None = None) -> str:
    """Ruleset family of one bout's ``event`` tag. Unknown names fall to ``other``, never to a
    ruleset — an unrecognised event is an event nobody classified, and reading it as ADCC
    because it is not obviously IBJJF is how a family quietly acquires bouts it never had."""
    doc = doc or load_event_rulesets()
    if event is None:
        return str(doc["null_event"]["family"])
    entry = doc["events"].get(event)
    return str(entry["family"]) if entry else "other"


def points_for(
    symbol: str, family: str, doc: Mapping[str, Any] | None = None,
    negative_window: bool = ADCC_NEGATIVE_WINDOW,
) -> int | None:
    """Points the family's rule book awards for ACHIEVING ``symbol``.

    ``None`` where the family has no per-action point table at all (cji, other, unknown) —
    which is a different fact from zero and must not be summed as one. ``0`` means the ruleset
    scores this action at nothing.
    """
    doc = doc or load_event_rulesets()
    table = doc["points"].get(family)
    if table is None:
        return None
    if symbol not in table:
        raise RulesetError(f"símbolo desconhecido: {symbol}")
    if symbol == "PGD" and family == "adcc" and negative_window:
        return int(doc.get("adcc_guard_pull_penalty", -1))
    return int(table[symbol])


def scoring_symbols(family: str, doc: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Symbols worth positive points under this family. Empty when the family has no table."""
    doc = doc or load_event_rulesets()
    if doc["points"].get(family) is None:
        return ()
    return tuple(s for s in SYMBOLS if (points_for(s, family, doc) or 0) > 0)


# ── annotation coverage: measured BEFORE anything is compared ──────────────────

class Annotation(NamedTuple):
    events: int
    present: int
    landed: int

    @property
    def present_pct(self) -> float:
        return round(100 * self.present / self.events, 1) if self.events else 0.0

    @property
    def landed_pct(self) -> float:
        return round(100 * self.landed / self.events, 1) if self.events else 0.0


def annotation_coverage(bouts: Sequence[Mapping[str, Any]]) -> Annotation:
    """How much of this bout set carries a ``successful`` flag at all.

    Same measurement, same purpose, as ``bracket_export._adcc_annotation``: the attempt/success
    split of the state space follows this number, so it decides which comparisons the rest of
    the module is allowed to make.
    """
    evs = [e for b in bouts for e in (b.get("seq") or [])]
    return Annotation(
        len(evs),
        sum(1 for e in evs if "successful" in e),
        sum(1 for e in evs if e.get("successful") is True),
    )


def comparability(by_family: Mapping[str, Annotation]) -> dict[str, Any]:
    """The cross-family verdict, as numbers a renderer can see rather than as a sentence."""
    pcts = {f: a.present_pct for f, a in by_family.items() if a.events}
    spread = (max(pcts.values()) - min(pcts.values())) if pcts else 0.0
    return {
        "by_family": {f: a._asdict() | {"present_pct": a.present_pct,
                                        "landed_pct": a.landed_pct}
                      for f, a in by_family.items()},
        "present_pct_spread": round(spread, 1),
        # False, always, and not as a precaution: the spread below is measured, and rule 3 of
        # `lamas_chain` sends an absent flag to the ATTEMPT state, so the split tracks the
        # ingest batch. Point estimates of a landing rate are comparable within a family only.
        "landing_rate_cross_family_comparable": False,
        "envelope_cross_family_comparable": True,
        "comparable": ["symbol occupancy", "landing-rate ENVELOPE (bounds, not the point)"],
        "comparable_within_family": ["landing rate (complete-case)", "expected points"],
        "warning": (
            "⚠️ AS FAMÍLIAS NÃO FORAM ANOTADAS DO MESMO JEITO. `successful` aparece em "
            + ", ".join(f"{f} {p}%" for f, p in sorted(pcts.items(), key=lambda x: -x[1]))
            + f" (amplitude {spread:.1f} pontos). Como um `successful` ausente é lido como "
            "TENTATIVA, a taxa de acerto pontual acompanha o LOTE DE ANOTAÇÃO, não o "
            "jiu-jitsu. Só o ENVELOPE (limite inferior = ausente conta como falha, superior "
            "= ausente conta como acerto) atravessa as famílias, porque ele não decide o "
            "que a anotação não disse."
        ),
    }


# ── the landing envelope, per symbol ───────────────────────────────────────────

class Mark(NamedTuple):
    """One Lamas action, at seven-symbol resolution, with the two facts about it the
    twelve-state code cannot carry: whether the ``successful`` flag was PRESENT at all, and
    which bout it came from.

    ``lamas_chain.Step`` deliberately holds only ``(state, actor_id)`` — absence of the flag is
    folded into the attempt state there, which is exactly the fold this module needs to undo in
    order to bound it. So the flag is read back off the events, paired with the steps
    ``chain_of`` produced. Nothing in ``lamas_chain`` is changed or re-implemented: the mapping,
    the skip rule and the absorbing truncation all stay its decisions.
    """

    bout_id: str
    symbol: str
    landed: bool
    annotated: bool
    actor_id: Any


def marks_of(bout: Mapping[str, Any]) -> list[Mark]:
    """``chain_of``'s steps, re-paired with the events they were mapped from.

    The pairing is positional and safe: ``chain_of`` walks ``seq`` in array order and appends
    one step per mapped event, so the nth step is the nth event ``lamas_state`` accepted. When
    the absorbing rule truncates the chain the steps simply run out first, and ``zip`` stops
    there — the tail events are dropped from both streams together.
    """
    from analysis.lamas_chain import lamas_state

    ch = chain_of(bout)
    mapped = [e for e in (bout.get("seq") or []) if lamas_state(e) is not None]
    out: list[Mark] = []
    for step, ev in zip(ch.steps, mapped):
        sym = SYMBOL_OF[step.state]
        out.append(Mark(ch.bout_id, sym, step.state == SUCCESS_STATE[sym],
                        "successful" in ev, step.actor_id))
    return out


class Envelope(NamedTuple):
    """Manski-style bounds on a landing rate under missing annotation.

    ``lo`` counts every unannotated appearance as a failure (``lamas_chain`` rule 3's own
    reading, which is why it is the LOWER bound and why every success rate in that module is
    described as a floor). ``hi`` counts every one as a success. ``cc`` is the complete-case
    rate — the estimate you get by dropping the unannotated, valid only if the annotation is
    missing at random, which across four ingest batches it demonstrably is not.
    """

    n: int
    landed: int
    annotated: int
    lo: float | None
    cc: float | None
    hi: float | None

    @property
    def width(self) -> float | None:
        return None if self.lo is None or self.hi is None else round(self.hi - self.lo, 4)


def _envelope(n: int, landed: int, annotated: int) -> Envelope:
    if n <= 0:
        return Envelope(0, 0, 0, None, None, None)
    missing = n - annotated
    return Envelope(
        n, landed, annotated,
        round(landed / n, 4),
        round(landed / annotated, 4) if annotated else None,
        round((landed + missing) / n, 4),
    )


def _symbol_tally(marks: Sequence[Sequence[Mark]]) -> dict[str, dict[str, Any]]:
    """Per symbol: appearances, landings, annotated appearances, and the bouts behind them.

    ``m.landed or m.annotated`` is not belt-and-braces. CDP and PGD have no attempt state, so
    ``Mark.landed`` is True for every one of their appearances by construction while the
    ``successful`` flag may well be absent — without the ``or`` their ``landed`` would exceed
    their ``annotated`` and the envelope's upper bound would run past 1. The consequence is that
    those two symbols report a landing rate of 1.000 with zero width, which is a DEFINITION and
    not a measurement; the doc says so beside the table, and neither symbol scores under either
    rule book, so nothing downstream reads it as evidence.
    """
    out: dict[str, dict[str, Any]] = {
        s: {"n": 0, "landed": 0, "annotated": 0, "bouts": {}} for s in SYMBOLS
    }
    for bout_marks in marks:
        for m in bout_marks:
            row = out[m.symbol]
            row["n"] += 1
            row["bouts"][m.bout_id] = row["bouts"].get(m.bout_id, 0) + 1
            row["landed"] += int(m.landed)
            row["annotated"] += int(m.landed or m.annotated)
    return out


def landing_envelopes(marks: Sequence[Sequence[Mark]]) -> dict[str, dict[str, Any]]:
    """Per symbol: the landing envelope, its Wilson interval at each bound, and the bout gate.

    The gate is ``stats_rigor.coverage`` on the per-BOUT appearance counts, identical to
    ``lamas_chain._cell``: the observed counts are a fact about the corpus and survive, the
    interval is a claim about a population and is withheld below the cluster cut.
    """
    tally = _symbol_tally(marks)
    out: dict[str, dict[str, Any]] = {}
    for sym in SYMBOLS:
        row = tally[sym]
        cov: Coverage = coverage(list(row["bouts"].values()))
        env = _envelope(row["n"], row["landed"], row["annotated"])
        entry: dict[str, Any] = {
            "symbol": sym, "envelope": env._asdict(), "width": env.width,
            "clusters": cov.clusters, "effective_n": round(cov.effective_n, 2),
            "coverage": cov.grade, "estimable": cov.estimable,
        }
        if cov.estimable:
            entry["ci_lo_bound"] = wilson(row["landed"], row["n"]).to_dict()
            entry["ci_hi_bound"] = wilson(
                row["landed"] + row["n"] - row["annotated"], row["n"]).to_dict()
        else:
            entry["reason_code"] = cov.reason_code
        out[sym] = entry
    return out


def scoring_chance(
    marks: Sequence[Sequence[Mark]], family: str, doc: Mapping[str, Any] | None = None,
    negative_window: bool = ADCC_NEGATIVE_WINDOW,
) -> dict[str, dict[str, Any]]:
    """P(this action scores | family), as an interval — the module's headline answer.

    ``points`` is ``None`` for a family with no point table, and then ``chance`` is ``None``
    too: a ruleset that does not score actions has no per-action scoring chance, and reporting
    zero would read as "these actions never score" instead of "this question does not apply".
    """
    doc = doc or load_event_rulesets()
    envs = landing_envelopes(marks)
    out: dict[str, dict[str, Any]] = {}
    for sym in SYMBOLS:
        pts = points_for(sym, family, doc, negative_window)
        env = envs[sym]
        e = env["envelope"]
        if pts is None:
            chance: dict[str, Any] | None = None
        elif pts <= 0:
            # Deterministic: this ruleset awards nothing for this action, however well it lands.
            chance = {"lo": 0.0, "cc": 0.0, "hi": 0.0, "deterministic": True}
        else:
            chance = {"lo": e["lo"], "cc": e["cc"], "hi": e["hi"], "deterministic": False}
        out[sym] = {**env, "points": pts, "chance": chance}
    return out


# ── the Markov layer: expected ruleset points following an action ──────────────

def symbol_matrix(marks: Sequence[Sequence[Mark]]) -> list[list[float]]:
    """Row-normalised first-order transition matrix over ``SYMBOLS``, cross-actor.

    Same chain, same chronology and the same self-loop policy as ``lamas_chain._matrix`` —
    this only reads it at the seven-symbol resolution instead of twelve. A row with no
    outgoing transitions stays all-zero rather than being made uniform: an absorbing or
    never-continued symbol has no successor distribution, and inventing one would put expected
    points on a state the corpus never leaves.
    """
    counts = [[0.0] * len(SYMBOLS) for _ in SYMBOLS]
    for bout_marks in marks:
        for a, b in zip(bout_marks, bout_marks[1:]):
            counts[SYMBOL_INDEX[a.symbol]][SYMBOL_INDEX[b.symbol]] += 1
    return [[c / total if (total := sum(row)) else 0.0 for c in row] for row in counts]


def expected_points(
    marks: Sequence[Sequence[Mark]], family: str, doc: Mapping[str, Any] | None = None,
    horizon: int = HORIZON, negative_window: bool = ADCC_NEGATIVE_WINDOW,
) -> dict[str, dict[str, Any]] | None:
    """Expected ruleset points accrued in the ``horizon`` actions AFTER each symbol.

    **Pre-registered accumulation design** (fixed before any corpus number was read):

        E_k(s) = Σ_{t=1..k} (P^t · r)_s ,    r_j = points_r(j) · P(j lands)

    Undiscounted, finite horizon ``k = HORIZON``, on the empirical row-normalised matrix.
    Undiscounted because a discount factor is a second unfitted constant and this module has no
    held-out target to fit one against (``path_to_victory``'s γ was at least swept by PoC-E4);
    finite because the chain is not ergodic here — SUB absorbs — and an infinite-horizon solve
    would be dominated by whether a row happens to be all-zero. The reward lands on ENTERING a
    symbol, never on occupying it, so a self-loop pays again and a dwell does not.

    Evaluated at both ends of the landing envelope, so the result is an interval whose width is
    inherited from the annotation gap. ``None`` when the family has no point table.

    **It is points scored by EITHER athlete, not by her.** ``symbol_matrix`` is cross-actor, the
    same reading ``lamas_chain`` gives its matrix and for the same measured reason —
    ``docs/match_event_model.md`` records 307 of 700 corpus bouts filing every event under one
    athlete, so a within-actor chain would be describing the attribution more than the grappling.
    So this answers "how many ruleset points does the next passage of the fight put on the
    board", never "how many does SHE score". A signed version needs an actor field this corpus
    does not have.

    ``estimable`` is carried over from the STARTING symbol's bout-cluster coverage. That is a
    floor and not a certificate: the reward vector reads every symbol's landing rate, so a gated
    row can still be standing on an ungated cell three transitions away. The gate says the
    starting state has enough fights behind it, and nothing more.
    """
    doc = doc or load_event_rulesets()
    if doc["points"].get(family) is None:
        return None
    p = symbol_matrix(marks)
    envs = landing_envelopes(marks)
    out: dict[str, dict[str, Any]] = {}
    for bound in ("lo", "hi"):
        r = [
            (points_for(s, family, doc, negative_window) or 0)
            * (envs[s]["envelope"][bound] or 0.0)
            for s in SYMBOLS
        ]
        # v accumulates Σ_t P^t r by carrying the t-step reward vector forward one step at a
        # time -- no matrix powers, no numpy, k multiplications of a 7-vector by a 7x7.
        step = list(r)
        total = [0.0] * len(SYMBOLS)
        for _ in range(horizon):
            step = [sum(p[i][j] * step[j] for j in range(len(SYMBOLS)))
                    for i in range(len(SYMBOLS))]
            total = [t + s for t, s in zip(total, step)]
        for i, sym in enumerate(SYMBOLS):
            out.setdefault(sym, {"symbol": sym, "horizon": horizon,
                                 "estimable": envs[sym]["estimable"],
                                 "clusters": envs[sym]["clusters"]})[bound] = round(total[i], 4)
    return out


# ── the one falsifiable check: does the inferred score agree with the result? ──

def winner_agreement(
    bouts: Sequence[Mapping[str, Any]], family: str,
    doc: Mapping[str, Any] | None = None, negative_window: bool = ADCC_NEGATIVE_WINDOW,
) -> dict[str, Any]:
    """Of the bouts DECIDED on points, how often does the inferred point leader actually win?

    This is the only claim in the module that can be wrong against evidence the module did not
    produce. Everything else is a description of the event stream; this compares a score
    inferred from that stream against ``winner_id``, a field no annotation batch touches.
    Submission and draw finishes are excluded — a submission win says nothing about who was
    ahead on points, and counting it as agreement would inflate the rate with bouts the
    scoreboard never decided.

    ``strict`` requires the ``successful`` flag (the lower bound on landings); ``lenient``
    credits every appearance of a scoring symbol (the upper bound). The two rates bracket what
    the corpus can support, exactly as the landing envelope does.
    """
    doc = doc or load_event_rulesets()
    if doc["points"].get(family) is None:
        return {"family": family, "applicable": False,
                "reason_code": "family_has_no_point_table"}
    counted = {"strict": [0, 0], "lenient": [0, 0]}
    ties = {"strict": 0, "lenient": 0}
    skipped: dict[str, int] = {}
    for b in bouts:
        wt = str(b.get("win_type") or "").strip().upper()
        winner = b.get("winner")
        a_id, b_id = b.get("a_id"), b.get("b_id")
        if wt in {"SUBMISSION", "DRAW"} or not winner or a_id is None or b_id is None:
            skipped[wt or "<none>"] = skipped.get(wt or "<none>", 0) + 1
            continue
        tally = {"strict": {str(a_id): 0, str(b_id): 0},
                 "lenient": {str(a_id): 0, str(b_id): 0}}
        for m in marks_of(b):
            actor = str(m.actor_id) if m.actor_id is not None else None
            if actor not in tally["strict"]:
                continue
            pts = points_for(m.symbol, family, doc, negative_window) or 0
            if pts <= 0:
                continue
            tally["lenient"][actor] += pts
            if m.landed:
                tally["strict"][actor] += pts
        for mode in ("strict", "lenient"):
            hi = max(tally[mode].values())
            if hi == 0 or list(tally[mode].values()).count(hi) > 1:
                ties[mode] += 1
                continue
            leader = max(tally[mode], key=lambda k: tally[mode][k])
            counted[mode][1] += 1
            counted[mode][0] += int(leader == str(winner))
    return {
        "family": family, "applicable": True,
        **{mode: {**wilson(k, n).to_dict(), "ties_or_scoreless": ties[mode]}
           for mode, (k, n) in counted.items()},
        "bouts_skipped": skipped,
    }


# ── the census ────────────────────────────────────────────────────────────────

#: Bucket order. First match wins, so the list IS the precedence and a reader can check the
#: classification without reading the loop.
CENSUS_BUCKETS: tuple[str, ...] = (
    "has_event_points", "has_declared_score", "partial_tally_possible", "unrecoverable",
)


def census(bouts: Sequence[Mapping[str, Any]], doc: Mapping[str, Any] | None = None
           ) -> dict[str, Any]:
    """What scoring information each ruleset family actually has. One row per family.

    Four buckets on the SCORE axis, mutually exclusive, in ``CENSUS_BUCKETS`` precedence, each
    named for what it can support:

    ``has_event_points``        at least one event carries a ``points`` field — the only place
                                in this corpus where an actual point VALUE is stored. Checked on
                                ``timeline`` and on ``seq`` together: ``dump_import`` writes the
                                field into both and requiring one of them would make the bucket
                                depend on which importer ran.
    ``has_declared_score``      no stored value, but the bout's own ``win_type`` is ``POINTS`` —
                                the corpus stating that a scoreboard decided this fight without
                                recording what the scoreboard said. It is a weaker fact than the
                                bucket above, which is why it sits below it.
    ``partial_tally_possible``  no stored score at all, but the sequence contains at least one
                                action the family's point table values — a PARTIAL tally can be
                                inferred, never a scoreline, because an unannotated attempt
                                cannot be told from a failed one. Structurally empty for a family
                                with no point table (cji, other, unknown, non_grappling): those
                                bouts fall through to ``unrecoverable``, which is the honest
                                reading — there is no per-action point value to recover.
    ``unrecoverable``           none of the above: nothing in the row from which a point can be
                                derived.

    And a FIFTH count that is deliberately NOT a bucket, because it is a different axis and
    making it one would hide it for every bout that also has a partial tally:

    ``footage``                 the bout carries a ``video_url``, so the score is recoverable by
                                RE-READING the footage — the ``scripts/build_refinement_manifest``
                                → ``scripts/frame_pdf`` path. This is feasibility, never a score:
                                it says the evidence still exists outside the database, and it is
                                reported per bucket (``footage_by_bucket``) so a reader sees which
                                of the three deficient buckets is actually rescuable.
    """
    doc = doc or load_event_rulesets()
    rows: dict[str, dict[str, Any]] = {}
    for b in bouts:
        fam = family_of(b.get("event"), doc)
        row = rows.setdefault(fam, {
            "family": fam, "bouts": 0, "with_sequence": 0, "events": 0,
            **{k: 0 for k in CENSUS_BUCKETS},
            "footage": 0, "footage_with_start": 0,
            "footage_by_bucket": {k: 0 for k in CENSUS_BUCKETS},
            "has_point_table": doc["points"].get(fam) is not None,
        })
        row["bouts"] += 1
        seq = b.get("seq") or []
        row["events"] += len(seq)
        if seq:
            row["with_sequence"] += 1
        wanted = set(scoring_symbols(fam, doc))
        if any(e.get("points") is not None for e in [*(b.get("timeline") or []), *seq]):
            bucket = "has_event_points"
        elif str(b.get("win_type") or "").strip().upper() == "POINTS":
            bucket = "has_declared_score"
        elif wanted and any(m.symbol in wanted for m in marks_of(b)):
            bucket = "partial_tally_possible"
        else:
            bucket = "unrecoverable"
        row[bucket] += 1
        if b.get("video_url"):
            row["footage"] += 1
            row["footage_by_bucket"][bucket] += 1
            if b.get("video_start_seconds") is not None:
                row["footage_with_start"] += 1
    return {"families": rows, "total_bouts": len(bouts), "buckets": list(CENSUS_BUCKETS)}


def adcc_clock_feasibility(bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Can an ADCC scoring WINDOW be applied to any bout in this set? Measured, not assumed.

    ``analysis/scouting_rulesets.project_adcc_events`` needs two things that live in different
    columns, and it needs BOTH at once:

    ``stage``      picks which profile of the ADCC preset applies — ``qualifying``, ``final`` or
                   ``overtime`` — and those profiles carry DIFFERENT window boundaries. Without
                   it there is no window table to look an action up in.
    a clock        ``ts_origin`` says which clock ``sequence[].ts`` runs on. ``bout_relative`` is
                   directly usable; ``video_absolute`` needs ``video_start_seconds`` to become
                   bout-relative; NULL means nobody established which, and
                   ``db/models.Match.ts_origin``'s own comment says a reader must treat that as
                   "cannot locate", never as a default of either (AA-010 placed frames in a
                   different fight exactly that way).

    ``both`` is the number of bouts where a window could be resolved at all. It is the number
    that decides whether ``ADCC_NEGATIVE_WINDOW`` may ever be switched on for this corpus.
    """
    clocked = 0
    staged = 0
    both = 0
    origins: dict[str, int] = {}
    for b in bouts:
        origin = str(b.get("ts_origin") or "none")
        origins[origin] = origins.get(origin, 0) + 1
        has_ts = any(e.get("ts") is not None for e in (b.get("seq") or []))
        clock = has_ts and (
            origin == "bout_relative"
            or (origin == "video_absolute" and b.get("video_start_seconds") is not None)
        )
        stage = bool(b.get("stage"))
        clocked += int(clock)
        staged += int(stage)
        both += int(clock and stage)
    return {
        "bouts": len(bouts), "with_stage": staged, "with_usable_clock": clocked, "both": both,
        "ts_origin": dict(sorted(origins.items())),
        "window_applicable": both > 0,
        "reason_code": None if both else "no_bout_has_both_stage_and_clock",
    }


def bout_concentration(bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How evenly a family's EVENTS are spread across its bouts.

    The row gate counts bout CLUSTERS, which answers "how many fights" and cannot answer "how
    much of this is one fight". The two come apart hard here: measured on the corpus, one bout
    supplies 43% of the entire `ibjjf` arm's events while the arm still shows 21 clusters and
    passes every gate above.

    So the same `coverage` grading is applied a third time, with the bout as the source and its
    event count as the weight. `effective_n` is then "this family is worth N equally-sized
    fights", and `top_share` names the largest single one.
    """
    counts = [len(b.get("seq") or []) for b in bouts if b.get("seq")]
    return {"bouts_with_events": len(counts), "events": sum(counts),
            **coverage(counts).to_dict()}


def athlete_coverage(bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How many distinct ATHLETES a family's events come from, graded on the same gate.

    The bout-cluster gate on every row above asks whether an estimate rests on enough fights.
    It cannot ask whether those fights rest on enough people, and the two come apart badly here:
    a family can clear five bouts while being four athletes' game, in which case the row is
    describing them and wearing the rule book's name. `stats_rigor`' own docstring names this
    exact failure — "a hundred events from one athlete will earn a narrow interval while
    describing one person wearing the category's name" — so the second unit is graded and
    published rather than assumed away.

    Both corners are credited with the bout's events, because the chain is cross-actor: an event
    belongs to the fight, and the fight belongs to two people.
    """
    per: dict[str, int] = {}
    unattributed = 0
    for b in bouts:
        n = len(b.get("seq") or [])
        sides = [s for s in (b.get("a_id"), b.get("b_id")) if s is not None]
        if not sides:
            unattributed += n
            continue
        for s in sides:
            per[str(s)] = per.get(str(s), 0) + n
    cov = coverage(list(per.values()))
    return {"athletes": len(per), "events_unattributed": unattributed, **cov.to_dict()}


def truncation(bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The ONE channel through which the annotation batch can touch the 7-symbol occupancy.

    Folding each attempt/success pair to one symbol makes the occupancy blind to ``successful``
    — a takedown is a ``TKD`` whether or not anyone marked it. Blind in the mapping, that is;
    ``lamas_chain`` rule 4 still truncates a chain at its first ``SUB``, and reaching ``SUB``
    requires ``successful is True``. So in a submission-won bout with an unflagged finish the
    tail events survive into the chain and inflate the occupancy of whatever they are.

    ``events_after_finish`` is the size of that channel and ``unflagged_finishes`` is how often
    it fired the other way. Published rather than argued about: the doc's honest-primary claim
    rests on the occupancy being annotation-blind, and this is the exception that has to be
    small for the claim to hold.
    """
    chains = [chain_of(b) for b in bouts]
    sub_won = [b for b in bouts
               if str(b.get("win_type") or "").strip().upper() == "SUBMISSION"]
    truncated = sum(1 for c in chains if c.truncated)
    return {
        "bouts": len(bouts), "won_by_submission": len(sub_won), "bouts_truncated": truncated,
        "unflagged_finishes": len(sub_won) - truncated,
        "events_after_finish": sum(c.after_finish for c in chains),
        "events_mapped": sum(c.mapped for c in chains),
        "events_skipped": sum(c.skipped for c in chains),
    }


# ── the cross-ruleset contrast ─────────────────────────────────────────────────
# PRE-REGISTERED before the corpus was read at this resolution. Two arms, and which of them is
# the primary was fixed in advance rather than picked from the results:
#
# PRIMARY   symbol OCCUPANCY — the share of a family's actions that are this symbol. It is the
#           one quantity here the annotation batch cannot reach (see `truncation` for the single
#           exception and its measured size), which is the same reason `lamas_chain`'s
#           family-level `anchor` is the only thing §7.2 of docs/research/lamas_chain_divisions.md
#           lets cross two corpora. Agresti-Caffo difference + Wilson arms + Fisher/chi-square,
#           via `stats_rigor.compare_proportions`, gated on bout clusters per side.
# SECONDARY landing-rate ENVELOPE. A point estimate of a landing rate follows the annotation
#           batch and is refused across families by `comparability`. The BOUNDS do not, so the
#           contrast survives on one condition, stated before the numbers: the two envelopes must
#           be DISJOINT. Overlapping envelopes are reported as `separated: false` and mean the
#           corpus cannot tell the two families apart on that action, whatever the point
#           estimates look like.
#
# No third arm. Expected points is a function of both plus the transition matrix, so contrasting
# it would be contrasting these two again with a chain's worth of extra variance on top.

def per_action_contrast(
    marks_a: Sequence[Sequence[Mark]], marks_b: Sequence[Sequence[Mark]],
    family_a: str, family_b: str, doc: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """``family_a`` against ``family_b``, one row per symbol, both arms, with both gates."""
    doc = doc or load_event_rulesets()
    ta, tb = _symbol_tally(marks_a), _symbol_tally(marks_b)
    na = sum(r["n"] for r in ta.values())
    nb = sum(r["n"] for r in tb.values())
    ea, eb = landing_envelopes(marks_a), landing_envelopes(marks_b)
    rows: list[dict[str, Any]] = []
    for sym in SYMBOLS:
        pa, pb = points_for(sym, family_a, doc), points_for(sym, family_b, doc)
        va, vb = ea[sym]["envelope"], eb[sym]["envelope"]
        gated = bool(ea[sym]["estimable"] and eb[sym]["estimable"])
        separated = bool(
            va["lo"] is not None and vb["lo"] is not None
            and (va["lo"] > vb["hi"] or vb["lo"] > va["hi"])
        )
        rows.append({
            "symbol": sym,
            "points_a": pa, "points_b": pb,
            "points_differ": pa != pb,
            "occupancy": compare_proportions(ta[sym]["n"], na, tb[sym]["n"], nb).to_dict(),
            "occupancy_gated": gated,
            "occupancy_reason_code": None if gated else "; ".join(
                filter(None, (ea[sym].get("reason_code"), eb[sym].get("reason_code")))) or None,
            "landing_a": va, "landing_b": vb,
            "landing_separated": separated,
            "landing_verdict": ("envelopes disjoint" if separated
                                else "envelopes overlap — no claim"),
        })
    # Seven symbols means seven tests off one pair of corpora, and at alpha 0.05 that produces a
    # "finding" roughly a third of the time from nothing at all. `stats_rigor`'s own docstring
    # puts multiplicity here rather than on the reader, so the family of tests carries its FDR.
    # The family is the GATED rows only: an ungated row's p-value is not a result being
    # corrected, it is a result that was never published, and including it would inflate `m` and
    # make the surviving rows look better than they are.
    tested = [r for r in rows if r["occupancy_gated"] and r["occupancy"]["p_value"] is not None]
    qs = benjamini_hochberg([r["occupancy"]["p_value"] for r in tested])
    for r, q in zip(tested, qs):
        r["occupancy_q"] = round(q, 4)
        r["occupancy_survives_bh"] = q <= BH_ALPHA
    for r in rows:
        r.setdefault("occupancy_q", None)
        r.setdefault("occupancy_survives_bh", False)
    return {
        "families": [family_a, family_b],
        "appearances": {family_a: na, family_b: nb},
        "primary": "occupancy",
        "secondary": "landing envelope (claim only when disjoint)",
        "rows": rows,
        "multiplicity": {"tests": len(tested), "alpha": BH_ALPHA, "method": "benjamini-hochberg",
                         "family": "gated occupancy rows only"},
        "points_table_differs_on": [s for s in SYMBOLS
                                    if points_for(s, family_a, doc)
                                    != points_for(s, family_b, doc)],
    }


# ── one call that produces everything the doc reports ──────────────────────────

def family_report(bouts: Sequence[Mapping[str, Any]], doc: Mapping[str, Any] | None = None,
                  contrast_families: tuple[str, str] = ("ibjjf", "adcc"),
                  horizon: int = HORIZON) -> dict[str, Any]:
    """Census, comparability, per-family scoring layer, and the one cross-ruleset contrast.

    ``bouts`` is whatever bout set the caller assembled; this module never selects fights, the
    same rule ``lamas_chain.markov_block`` follows, so the census and the results below it can
    never describe different universes.

    Every family gets its block, including the ones with no point table — a family whose
    ``scoring_chance`` is all ``None`` is a RESULT (the ruleset does not score actions), and
    dropping it would turn that result into an absence.
    """
    doc = doc or load_event_rulesets()
    by_family: dict[str, list[Mapping[str, Any]]] = {}
    for b in bouts:
        by_family.setdefault(family_of(b.get("event"), doc), []).append(b)
    marks = {f: [marks_of(b) for b in bs] for f, bs in by_family.items()}
    ann = {f: annotation_coverage(bs) for f, bs in by_family.items()}
    families = {
        f: {
            "family": f,
            "bouts": len(bs),
            "with_sequence": sum(1 for b in bs if b.get("seq")),
            "annotation": ann[f]._asdict() | {"present_pct": ann[f].present_pct,
                                              "landed_pct": ann[f].landed_pct},
            "athlete_coverage": athlete_coverage(bs),
            "bout_concentration": bout_concentration(bs),
            "truncation": truncation(bs),
            "scoring_chance": scoring_chance(marks[f], f, doc),
            "expected_points": expected_points(marks[f], f, doc, horizon),
            "winner_agreement": winner_agreement(bs, f, doc),
        }
        for f, bs in sorted(by_family.items())
    }
    fam_a, fam_b = contrast_families
    return {
        "census": census(bouts, doc),
        "comparability": comparability(ann),
        "families": families,
        "adcc_clock": adcc_clock_feasibility(by_family.get("adcc", [])),
        "contrast": (per_action_contrast(marks[fam_a], marks[fam_b], fam_a, fam_b, doc)
                     if fam_a in marks and fam_b in marks else None),
        # Do the two arms describe the same people? Published because the contrast is a
        # between-population comparison the moment they do not, and no gate above would say so:
        # the bout-cluster gate counts fights and the annotation flag counts labels, and neither
        # can see that one arm is a handful of athletes the other arm never met.
        "contrast_arms": _arm_overlap(by_family.get(fam_a, []), by_family.get(fam_b, [])),
        "horizon": horizon,
        "negative_window_applied": ADCC_NEGATIVE_WINDOW,
    }


def _arm_overlap(a: Sequence[Mapping[str, Any]], b: Sequence[Mapping[str, Any]]
                 ) -> dict[str, Any]:
    def ids(bouts: Sequence[Mapping[str, Any]]) -> set[str]:
        return {str(s) for x in bouts if x.get("seq")
                for s in (x.get("a_id"), x.get("b_id")) if s is not None}

    ia, ib = ids(a), ids(b)
    return {"athletes_a": len(ia), "athletes_b": len(ib), "shared": len(ia & ib),
            "shared_share_of_a": round(len(ia & ib) / len(ia), 3) if ia else None}


def _demo() -> None:
    """One runnable check of the non-trivial logic, no framework, no DB."""
    doc = load_event_rulesets()

    # The finding the whole doc rests on: at this resolution the two tables differ once.
    diff = [s for s in SYMBOLS if points_for(s, "ibjjf", doc) != points_for(s, "adcc", doc)]
    assert diff == ["BTK"], diff
    assert points_for("SUB", "cji", doc) is None
    assert points_for("PGD", "adcc", doc) == 0
    assert points_for("PGD", "adcc", doc, negative_window=True) == -1

    def ev(t: str, label: str, actor: str, ok: bool | None = None) -> dict[str, Any]:
        e: dict[str, Any] = {"type": t, "label": label, "actor_id": actor}
        if ok is not None:
            e["successful"] = ok
        return e

    # Four takedowns: 1 landed, 1 explicitly failed, 2 unannotated.
    bout = {"id": "b", "event": "IBJJF Worlds 2023", "win_type": "DECISION", "winner": "X",
            "a_id": "X", "b_id": "Y", "seq": [
                ev("takedown", "Single Leg", "X", True),
                ev("takedown", "Double Leg", "X", False),
                ev("takedown", "Ankle Pick", "Y"),
                ev("takedown", "Body Lock", "Y"),
            ]}
    envs = landing_envelopes([marks_of(bout)])
    e = envs["TKD"]["envelope"]
    assert (e["n"], e["landed"], e["annotated"]) == (4, 1, 2), e
    assert (e["lo"], e["cc"], e["hi"]) == (0.25, 0.5, 0.75), e   # 1/4, 1/2, (1+2)/4
    assert envs["TKD"]["width"] == 0.5

    chance = scoring_chance([marks_of(bout)], "ibjjf", doc)
    assert chance["TKD"]["points"] == 2 and chance["TKD"]["chance"]["cc"] == 0.5
    assert chance["CDP"]["chance"] == {"lo": 0.0, "cc": 0.0, "hi": 0.0, "deterministic": True}
    assert scoring_chance([marks_of(bout)], "cji", doc)["TKD"]["chance"] is None

    # Hand-computable chain arithmetic: TKD -> GPS -> BTK, one bout, every action landed.
    line = {"id": "c", "event": "IBJJF Worlds 2023", "win_type": "DECISION", "winner": "X",
            "a_id": "X", "b_id": "Y", "seq": [
                ev("takedown", "Single Leg", "X", True),
                ev("pass", "Knee Cut", "X", True),
                ev("control", "Back Control", "X", True),
            ]}
    ch = [marks_of(line)]
    m = symbol_matrix(ch)
    assert m[SYMBOL_INDEX["TKD"]][SYMBOL_INDEX["GPS"]] == 1.0
    assert m[SYMBOL_INDEX["GPS"]][SYMBOL_INDEX["BTK"]] == 1.0
    assert sum(m[SYMBOL_INDEX["BTK"]]) == 0.0          # nothing follows; row stays empty
    exp = expected_points(ch, "ibjjf", doc, horizon=3)
    assert exp is not None
    # From TKD: step 1 -> GPS (3), step 2 -> BTK (4), step 3 -> nowhere. Landing rates are 1.0.
    assert exp["TKD"]["lo"] == 7.0 and exp["TKD"]["hi"] == 7.0, exp["TKD"]
    assert exp["GPS"]["lo"] == 4.0 and exp["BTK"]["lo"] == 0.0
    assert expected_points(ch, "cji", doc) is None

    # Winner agreement: X scores 2+3+4 = 9 under IBJJF, Y nothing, and X won.
    agree = winner_agreement([line], "ibjjf", doc)
    assert agree["strict"]["k"] == 1 and agree["strict"]["n"] == 1, agree
    assert winner_agreement([line], "cji", doc)["applicable"] is False

    c = census([line, {"id": "d", "event": "CJI", "seq": [ev("guard", "Guard Pull", "X")],
                       "video_url": "https://x"}], doc)
    assert c["families"]["ibjjf"]["partial_tally_possible"] == 1
    assert c["families"]["cji"]["has_point_table"] is False
    assert c["families"]["cji"]["unrecoverable"] == 1
    assert c["families"]["cji"]["footage_by_bucket"]["unrecoverable"] == 1
    assert c["families"]["ibjjf"]["footage"] == 0

    # A window needs BOTH a stage and a clock; either alone resolves nothing.
    assert adcc_clock_feasibility([
        {"stage": "F", "ts_origin": None, "seq": [{"ts": 1}]},
        {"stage": None, "ts_origin": "bout_relative", "seq": [{"ts": 1}]},
    ])["both"] == 0
    assert adcc_clock_feasibility(
        [{"stage": "F", "ts_origin": "bout_relative", "seq": [{"ts": 1}]}])["both"] == 1

    print("ruleset_scoring demo ok")


if __name__ == "__main__":
    _demo()
