"""Next-move ranking — the empirical corpus baseline every other ranker must beat.

Question: *given the position on the mat right now, what does this corpus say happens next?*
Answer here is a smoothed Markov distribution over the canonical ACTION vocabulary, fitted on
the public competition corpus only. It is the baseline in the sense the research skill means:
it ships before any model, and a model that does not beat it does not ship.

Privacy class **A, public competition data**. Every input is a ``matches`` row from published
footage (``owner_kind='athlete'`` side of the corpus). Nothing here reads a user graph, a
session, or a ``owner_kind='user'`` row, and nothing here may be fitted on one — a next-move
prior is a competitive artefact by definition (it is shown to third parties and it ranks
techniques), so the private half of the database is out of bounds by the root rule, not by
preference.

## What a decision point is

The corpus stores a bout as a flat event list. This module reads it through the SAME
state/action classifier the taxonomy migration uses (``taxonomy_kind.kind_of_entry``, which
resolves the label through the App technique library first, so a stale logged ``type`` cannot
misread an action as a state) and walks it:

* an event classified ``state`` updates ``current_state`` (and remembers whose it is);
* an event classified ``action`` is a **decision point**: the state we are in, the last N
  action labels, → this action's label as the target.

Two exclusions, both deliberate and both counted:

1. **Actions before the bout's first state are dropped** (1 578 of 5 585 corpus actions,
   28.3%). They are real — a bout opens standing, with no position logged yet — but the query
   this ranker answers always CARRIES a state (the App knows the user's position; the vision
   model reads it off the frame), so scoring on contexts the product never issues would
   flatter the numbers. Reported as ``n_actions_without_state``.
2. **Self-transitions are kept.** ``network_from_sequences`` drops A→A and ``normalize_chain``
   folds consecutive repeats; both would delete a genuinely predictive cell (the repeated
   pass attempt is the modal continuation of a passing sequence). Same reasoning as
   ``lamas_chain``'s own "self-loops SURVIVE".

## The actor is a SECOND-CLASS field here, and that is measured

``docs/match_event_model.md`` records that 307 of 700 corpus bouts file every event under one
athlete: ``actor_id`` carries no information on those. So the headline target is the action
**label**, cross-actor — the bout's flow, the same choice ``lamas_chain.chain_of`` made for the
same reason. Relative actor (own / opponent, relative to whoever owns the current state) is
carried alongside as ``rel``, is ``"unk"`` on every bout ``attribution.bout_flags`` refuses
(``perspective_reliable`` false), and is evaluated as a SEPARATE, smaller number
(:func:`evaluate` reports the joint ``(label, rel)`` score on the gated subset only). Reporting
one 4 000-point number that silently mixes a reliable field with an unreliable one is the
failure this split exists to avoid.

## Smoothing: Witten-Bell, no tuned hyperparameter

Witten, I. H. & Bell, T. C. (1991). *The zero-frequency problem: estimating the probabilities of
novel events in adaptive text compression.* IEEE Trans. Inf. Theory 37(4), 1085-1094 — method C.
The grappling side of the model form is the same one ``analysis/lamas_chain`` runs on this corpus
(Lamas et al. 2024, *No-gi Brazilian jiu-jitsu: a Markovian analysis of elite-level combat
dynamics*, IJSSC, doi:10.1177/17479541231210979): a first-order chain over grappling actions is
the one peer-reviewed form this domain has, and this module is that chain conditioned on the
position rather than marginalised over it.

Three levels — ``(state, prev_action)`` → ``(state)`` → unigram — interpolated by
Witten-Bell::

    P_n(a | c) = (count(c, a) + T(c) · P_{n-1}(a)) / (N(c) + T(c))

where ``T(c)`` is the number of DISTINCT continuations seen after ``c`` and ``N(c)`` their
total count. A context seen once with one continuation is trusted half; a context seen 200
times with 3 continuations is trusted almost fully. The base level is Lidstone add-one over the
fixed vocabulary, which is what makes every distribution strictly positive and sum to exactly 1
(asserted in ``tests/test_next_moves.py``). Witten-Bell rather than a swept λ because the sweep
would need its own held-out fold and this baseline has to be reproducible without one.

## Splitting

``split_by_bout`` splits **by bout id**, never by decision point. Two decision points from the
same bout share a state vocabulary, an athlete pair and a referee — putting one in train and
one in validation leaks. Deterministic: sorted ids, seeded shuffle.

``split_by_year`` + ``rolling_origin_folds`` are the chronological protocol
``docs/research/next_moves_literature.md`` §H1 confirmed random-by-bout is optimistic on this
corpus (calendar drift, not just within-bout leakage). Random split is kept only to reproduce
the historical table in ``docs/next_moves.md``; every new headline number must be chronological.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from analysis.attribution import bout_flags
from analysis.taxonomy_kind import kind_of_entry
from analysis.technique_match import clean_label

#: How many previous actions a decision point remembers. Order-2 context uses the last ONE
#: (``history[-1]``); the rest travel for the embedding query text and the guidance block,
#: which read them as prose rather than as a lookup key.
HISTORY_N = 3

#: Seed + fraction for the one canonical split. Fixed so every variant is scored on exactly the
#: same bouts — a variant evaluated on a different split is not a comparison.
SPLIT_SEED = 20260902
VAL_FRAC = 0.2

OWN, OPP, UNK = "own", "opp", "unk"

_LIBRARY_PATH = Path(__file__).resolve().parent / "data" / "technique_library.json"

#: Library ``type`` values that are ACTIONS. ``concept`` entries are not events at all
#: (``docs/match_event_model.md``); guard/control are positions, i.e. states.
ACTION_TYPES: frozenset[str] = frozenset(
    {"takedown", "sweep", "pass", "submission", "escape", "transition"}
)


class DecisionPoint(NamedTuple):
    """One "what happens next" question, with its recorded answer."""

    bout_id: str
    state: str  # canonical label of the position we are in
    state_type: str  # the state event's own ``type`` (guard / control / ...)
    history: tuple[tuple[str, str], ...]  # last HISTORY_N (action label, rel) before the target
    target: str  # canonical label of the action that actually came next
    target_rel: str  # own / opp / unk — relative to the actor owning ``state``
    rel_readable: bool  # False ⇒ every rel on this point is "unk"; excluded from actor scoring
    # Position of the target event in the bout's sequence (provenance). NOT ``index`` — that
    # name would shadow ``tuple.index`` on a NamedTuple.
    event_index: int
    # H2 (docs/research/next_moves_literature.md): the clock. ``staleness`` = events since the
    # state event fired (>= 1, always known). ``elapsed_ts`` = seconds since that same event's
    # ``ts``, or ``None`` when either event lacks a numeric ``ts`` — NEVER defaulted to 0, same
    # convention E9 already used for its 47 missing-``ts`` bouts.
    staleness: int
    elapsed_ts: float | None


# ── corpus → decision points ────────────────────────────────────────────────────


def _rel(actor: Any, state_actor: Any, readable: bool) -> str:
    if not readable or actor is None or state_actor is None:
        return UNK
    return OWN if actor == state_actor else OPP


def _numeric_ts(v: Any) -> float | None:
    """``ts`` is seconds into the bout (``analysis.attribution.CONTRADICTION_WINDOW`` is 10 of
    the same unit). ``bool`` is an ``int`` subclass in Python and is not a timestamp."""
    if isinstance(v, int | float) and not isinstance(v, bool):
        return float(v)
    return None


def decision_points(
    sequence: Sequence[Mapping[str, Any]],
    bout_id: str,
    *,
    rel_readable: bool = True,
    history_n: int = HISTORY_N,
) -> list[DecisionPoint]:
    """One bout's events → its decision points, in array order.

    ``rel_readable`` is the caller's verdict on this bout's ``actor_id`` field — pass
    ``bout_flags(...)["perspective_reliable"]``. False makes every ``rel`` on every point
    ``"unk"``; it does NOT drop the points, because the label target does not depend on the
    actor field at all.

    Array order is the chronology, same as ``lamas_chain`` (measured: 39 of 40 scouting bouts
    carry ``ts`` on every event and none disagrees with the array).
    """
    points: list[DecisionPoint] = []
    state: str | None = None
    state_type = ""
    state_actor: Any = None
    state_index = -1
    state_ts: float | None = None
    hist: list[tuple[str, Any]] = []  # (label, actor) — rel is computed at emit time

    for i, ev in enumerate(sequence or []):
        etype = str(ev.get("type", ""))
        label = clean_label(str(ev.get("label", "")), etype)
        if not label:
            continue
        kind = kind_of_entry(label, etype)
        actor = ev.get("actor_id")
        if kind == "state":
            state, state_type, state_actor = label, etype, actor
            state_index = i
            state_ts = _numeric_ts(ev.get("ts"))
            continue
        if kind != "action":
            continue
        if state is not None:
            ev_ts = _numeric_ts(ev.get("ts"))
            points.append(
                DecisionPoint(
                    bout_id=bout_id,
                    state=state,
                    state_type=state_type,
                    history=tuple(
                        (lb, _rel(ac, state_actor, rel_readable)) for lb, ac in hist[-history_n:]
                    ),
                    target=label,
                    target_rel=_rel(actor, state_actor, rel_readable),
                    rel_readable=rel_readable,
                    event_index=i,
                    staleness=i - state_index,
                    elapsed_ts=(
                        (ev_ts - state_ts) if ev_ts is not None and state_ts is not None else None
                    ),
                )
            )
        hist.append((label, actor))
    return points


def corpus_points(
    bouts: Iterable[Mapping[str, Any]], *, history_n: int = HISTORY_N
) -> tuple[list[DecisionPoint], dict[str, int]]:
    """Every bout → every decision point, plus the counts this module publishes as caveats.

    ``bouts`` are dicts shaped like a ``matches`` row: ``{id, a, b, sequence}``. Gating is done
    here rather than by the caller so no path can forget it.
    """
    points: list[DecisionPoint] = []
    stats = Counter[str]()
    for b in bouts:
        seq = b.get("sequence") or []
        flags = bout_flags(seq, str(b.get("a") or ""), str(b.get("b") or ""))
        readable = bool(flags["perspective_reliable"])
        stats["bouts"] += 1
        stats["bouts_rel_readable"] += int(readable)
        pts = decision_points(seq, str(b["id"]), rel_readable=readable, history_n=history_n)
        points.extend(pts)
        # actions the walk saw but could not ask a question about (no state yet)
        n_actions = sum(
            1
            for e in seq
            if (lb := clean_label(str(e.get("label", "")), str(e.get("type", ""))))
            and kind_of_entry(lb, str(e.get("type", ""))) == "action"
        )
        stats["actions"] += n_actions
        stats["actions_without_state"] += n_actions - len(pts)
    return points, dict(stats)


def split_by_bout(
    points: Sequence[DecisionPoint], *, val_frac: float = VAL_FRAC, seed: int = SPLIT_SEED
) -> tuple[list[DecisionPoint], list[DecisionPoint]]:
    """80/20 **by bout**, deterministic. No bout appears on both sides — that is the whole job."""
    ids = sorted({p.bout_id for p in points})
    rng = random.Random(seed)
    rng.shuffle(ids)
    n_val = max(1, round(len(ids) * val_frac)) if ids else 0
    val_ids = set(ids[:n_val])
    train = [p for p in points if p.bout_id not in val_ids]
    val = [p for p in points if p.bout_id in val_ids]
    return train, val


def split_by_year(
    bouts: Iterable[Mapping[str, Any]], cutoff: int
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Chronological split, **by bout**: train ``year <= cutoff``, test ``year > cutoff``.

    Deterministic — a filter, no shuffle — and returned sorted by bout id so re-running is
    byte-identical. ``docs/research/next_moves_literature.md`` §2b/§H1: random-by-bout
    (``split_by_bout``) lets the model train on 2026 bouts and predict 2025 ones; this is the
    honest protocol every future headline number must use. A bout with no/zero year sorts into
    train (``<= cutoff`` is true for ``year=0``) rather than silently vanishing.
    """
    train = sorted(
        (b for b in bouts if int(b.get("year") or 0) <= cutoff), key=lambda b: str(b["id"])
    )
    test = sorted(
        (b for b in bouts if int(b.get("year") or 0) > cutoff), key=lambda b: str(b["id"])
    )
    return train, test


# ── vocabulary ──────────────────────────────────────────────────────────────────


def library_actions(path: Path | None = None) -> list[dict[str, Any]]:
    """The App technique library's ACTION entries — the canonical candidate set.

    Positions (``guard``/``control``) and ``concept`` rows are excluded: they are states and
    non-events respectively, and neither is a legal answer to "what does she do next".
    """
    import json

    entries: list[dict[str, Any]] = json.loads(
        (path or _LIBRARY_PATH).read_text(encoding="utf-8")
    )
    return [e for e in entries if str(e.get("type", "")) in ACTION_TYPES]  # noqa: RET504


def build_vocab(
    train: Sequence[DecisionPoint], library: Sequence[Mapping[str, Any]] | None = None
) -> list[str]:
    """Candidate labels: every action seen in TRAIN ∪ every library action entry.

    Fixed before evaluation and identical for every variant — a ranker scored against a
    different candidate set is not being compared. Built from TRAIN only (never validation),
    so a label that exists solely in the held-out bouts stays out-of-vocabulary and is counted
    as the miss it is.
    """
    lib = library if library is not None else library_actions()
    vocab = {p.target for p in train} | {str(e["en"]) for e in lib}
    return sorted(vocab)


# ── the model ───────────────────────────────────────────────────────────────────


class MarkovNextMoves:
    """Witten-Bell interpolated P(next action | state, [previous action]).

    Fit on decision points, ranks over a FIXED vocabulary, and every distribution it produces
    is strictly positive and sums to 1 (``tests/test_next_moves.py``). Pure: no DB, no network,
    no global state.
    """

    def __init__(self, vocab: Sequence[str], max_order: int = 2) -> None:
        #: 0 = unigram only, 1 = condition on the state, 2 = state + previous action. Lower
        #: orders exist so the report can show what each conditioning level actually buys —
        #: an ablation, not a tuning knob.
        self.max_order = max_order
        self.vocab: list[str] = list(vocab)
        self._index = {lb: i for i, lb in enumerate(self.vocab)}
        self._c0: Counter[str] = Counter()
        self._c1: dict[str, Counter[str]] = defaultdict(Counter)
        self._c2: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        # P(rel | state, label) on gated points only — the actor half, kept apart on purpose.
        self._rel: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self.n_fitted = 0
        self.n_oov = 0

    # -- fitting ---------------------------------------------------------------

    def fit(self, points: Iterable[DecisionPoint]) -> MarkovNextMoves:
        for p in points:
            if p.target not in self._index:
                self.n_oov += 1
                continue
            self.n_fitted += 1
            self._c0[p.target] += 1
            self._c1[p.state][p.target] += 1
            prev = p.history[-1][0] if p.history else ""
            self._c2[(p.state, prev)][p.target] += 1
            if p.rel_readable and p.target_rel != UNK:
                self._rel[(p.state, p.target)][p.target_rel] += 1
        return self

    # -- probability -----------------------------------------------------------

    def _p0(self, label: str) -> float:
        """Lidstone add-one unigram over the fixed vocabulary. Strictly positive by construction."""
        return (self._c0[label] + 1.0) / (self.n_fitted + len(self.vocab))

    @staticmethod
    def _wb(counts: Counter[str], label: str, backoff: float) -> float:
        n = sum(counts.values())
        if n == 0:
            return backoff
        t = len(counts)
        return (counts[label] + t * backoff) / (n + t)

    def prob(self, state: str, history: Sequence[Any], label: str) -> float:
        """P(label | state, previous action) — the interpolated three-level estimate."""
        p = self._p0(label)
        if self.max_order < 1:
            return p
        p = self._wb(self._c1.get(state, Counter()), label, p)
        if self.max_order < 2:
            return p
        prev = _prev_label(history)
        return self._wb(self._c2.get((state, prev), Counter()), label, p)

    def dist(self, state: str, history: Sequence[Any] = ()) -> dict[str, float]:
        """Full distribution over the vocabulary. Sums to 1 (up to float error)."""
        return {lb: self.prob(state, history, lb) for lb in self.vocab}

    def rank_next_moves(
        self, state: str, history: Sequence[Any] = (), k: int = 5
    ) -> list[tuple[str, float]]:
        """Top-``k`` ``[(label, p)]``, highest first.

        Ties broken on the label so the ranking is a total order — the ``PYTHONHASHSEED``
        lesson from ``docs/insights`` applies to every ranking in this repo.
        """
        d = self.dist(state, history)
        ordered = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
        return ordered[: max(0, k)]

    def raw_count(self, state: str, label: str) -> int:
        """Raw, unsmoothed count of LABEL immediately following STATE in the training corpus.

        The "seen M times from this position" figure `guidance_block` prints (H3,
        `docs/next_moves.md` §5c) — deliberately NOT `prob()`'s Witten-Bell estimate, which is
        smoothed and conditions on the previous action too. This is the plain tally a reader can
        check against the corpus themselves.
        """
        return self._c1.get(state, Counter())[label]

    def rel_counts(self, state: str, label: str) -> tuple[str, int, int]:
        """Most likely relative actor for this move from this state, as raw counts.

        ``(side, n_for_side, n_total)`` — `rel_of` is this divided into a share. Fitted only on
        bouts ``attribution.bout_flags`` calls ``perspective_reliable``. An unseen pair answers
        ``(UNK, 0, 0)`` rather than guessing — 43.9% of the corpus cannot support this field at
        all and inventing a side there would be the exact defect ``analysis/attribution.py``
        exists to refuse.
        """
        c = self._rel.get((state, label))
        if not c:
            return UNK, 0, 0
        side, n = max(c.items(), key=lambda kv: (kv[1], kv[0]))
        return side, n, sum(c.values())

    def rel_of(self, state: str, label: str) -> tuple[str, float]:
        """Most likely relative actor for this move from this state, and its share.

        See `rel_counts` for the raw counts this is derived from.
        """
        side, n, total = self.rel_counts(state, label)
        if total == 0:
            return UNK, 0.0
        return side, n / total


def _prev_label(history: Sequence[Any]) -> str:
    """Last action label out of a history that may be ``[(label, rel)]`` or plain ``[label]``."""
    if not history:
        return ""
    last = history[-1]
    if isinstance(last, str):
        return last
    return str(last[0])


# ── evaluation ──────────────────────────────────────────────────────────────────


class RankFn:
    """Structural type for anything scorable: ``(point, k) -> [(label, score)]``."""


def evaluate(
    rank: Any,
    points: Sequence[DecisionPoint],
    ks: Sequence[int] = (1, 3, 5),
    *,
    ci: bool = False,
    dist_fn: Any = None,
    ece_bins: int = 10,
) -> dict[str, Any]:
    """Top-k accuracy + MRR over decision points.

    ``rank(point, k)`` returns ``[(label, score)]`` for ONE point. MRR uses the rank of the true
    label within the top ``max(ks)``; a target ranked below that (or out of vocabulary)
    contributes 0, which is the standard truncated MRR and is stated rather than hidden.

    ``joint_top3`` is the actor-aware number and is computed on the gated subset ONLY: the
    prediction must get both the label and the relative actor right. ``rank`` may return
    ``(label, score, rel)`` triples to be scored on it; two-tuples score ``rel`` as a miss.

    ``ci=True`` adds ``top3_lo``/``top3_hi``, a 95% **cluster** bootstrap over BOUTS
    (``stats_rigor.bootstrap_ci``, ``groups=bout_id``). The cluster is the bout because two
    decision points from the same bout are not two independent observations — a naive interval
    on 834 points would read about ±3.0 pp and understate the real uncertainty, which is
    exactly the quantity the pre-registered 5-point win margin has to be compared against.

    ``dist_fn(point) -> {label: p}`` (H3, docs/research/next_moves_literature.md §H3/§17), when
    given, adds ``log_loss`` (mean ``-log P(target)``), ``brier`` (multiclass, ``sum_c p_c^2 -
    2*p_target + 1``) and ``ece`` — expected calibration error over the TOP-1 confidence, in
    ``ece_bins`` EQUAL-MASS bins (quantiles of confidence, not equal-width: this corpus's
    confidence distribution is heavy-tailed and equal-width bins leave the high-confidence ones
    near-empty). A point whose target is outside ``dist_fn``'s vocabulary cannot be scored as a
    probability (``log(0)``) and is excluded, counted in ``calib_n_oov`` rather than silently
    dropped from the denominator.
    """
    kmax = max(ks) if ks else 5
    hits = {k: 0 for k in ks}
    hit3: list[float] = []
    bouts: list[str] = []
    rr = 0.0
    joint_hits = 0
    joint_n = 0
    n = 0
    for p in points:
        n += 1
        ranked = rank(p, kmax)
        labels = [str(r[0]) for r in ranked]
        for k in ks:
            if p.target in labels[:k]:
                hits[k] += 1
        hit3.append(1.0 if p.target in labels[:3] else 0.0)
        bouts.append(p.bout_id)
        if p.target in labels:
            rr += 1.0 / (labels.index(p.target) + 1)
        if p.rel_readable and p.target_rel != UNK:
            joint_n += 1
            for r in ranked[:3]:
                if str(r[0]) == p.target and len(r) > 2 and str(r[2]) == p.target_rel:
                    joint_hits += 1
                    break
    out: dict[str, Any] = {
        "n": n,
        **{f"top{k}": (hits[k] / n if n else 0.0) for k in ks},
        "mrr": rr / n if n else 0.0,
        "joint_n": joint_n,
        "joint_top3": joint_hits / joint_n if joint_n else 0.0,
    }
    if ci and hit3:
        from analysis.stats_rigor import bootstrap_ci

        _, lo, hi = bootstrap_ci(hit3, lambda v: sum(v) / len(v), n_boot=2000, groups=bouts)
        out["top3_lo"], out["top3_hi"] = lo, hi
    if dist_fn is not None:
        out.update(calibration_metrics(dist_fn, points, n_bins=ece_bins))
    return out


def calibration_metrics(
    dist_fn: Any, points: Sequence[DecisionPoint], *, n_bins: int = 10
) -> dict[str, Any]:
    """Log-loss, multiclass Brier and top-1 ECE for a full-distribution ranker (H3).

    ``dist_fn(point) -> {label: p}`` must be a normalised distribution (``MarkovNextMoves.dist``
    is: Lidstone-smoothed, strictly positive, sums to 1). Prefixed ``calib_`` because this is
    called from :func:`evaluate`, where ``n`` already names the rank-accuracy denominator and a
    dist-scored point can be a different count (OOV targets excluded, see below).
    """
    ll_sum = 0.0
    brier_sum = 0.0
    n = n_oov = 0
    confs: list[float] = []
    corrects: list[float] = []
    for p in points:
        d = dist_fn(p)
        if p.target not in d:
            n_oov += 1
            continue
        n += 1
        pt = d[p.target]
        ll_sum += -math.log(pt)
        brier_sum += sum(v * v for v in d.values()) - 2.0 * pt + 1.0
        top_label, top_p = max(d.items(), key=lambda kv: (kv[1], kv[0]))
        confs.append(top_p)
        corrects.append(1.0 if top_label == p.target else 0.0)
    return {
        "calib_n": n,
        "calib_n_oov": n_oov,
        "log_loss": ll_sum / n if n else float("nan"),
        "brier": brier_sum / n if n else float("nan"),
        "ece": _ece(confs, corrects, n_bins) if n else float("nan"),
    }


def _ece(confs: Sequence[float], corrects: Sequence[float], n_bins: int) -> float:
    """Expected calibration error, EQUAL-MASS bins (quantiles of confidence).

    Kull, Perelló-Nieto et al. 2019 / Ferrer 2024 (source 17,
    ``docs/research/next_moves_literature.md``): a better score does not imply calibration, and
    calibration needs measuring on its own terms. Equal-mass rather than equal-width so a
    heavy-tailed confidence distribution (this corpus's) does not leave the top bins empty.
    """
    n = len(confs)
    if n == 0:
        return float("nan")
    order = sorted(range(n), key=lambda i: confs[i])
    bounds = [round(i * n / n_bins) for i in range(n_bins + 1)]
    ece = 0.0
    for lo, hi in zip(bounds, bounds[1:]):
        if hi <= lo:
            continue
        idx = order[lo:hi]
        bin_conf = sum(confs[i] for i in idx) / len(idx)
        bin_acc = sum(corrects[i] for i in idx) / len(idx)
        ece += (len(idx) / n) * abs(bin_conf - bin_acc)
    return ece


def temperature_scaled_dist(
    model: MarkovNextMoves, state: str, history: Sequence[Any], temperature: float
) -> dict[str, float]:
    """``model``'s distribution rescaled by ONE scalar temperature on ``log_prior`` (H3).

    ``P_T(y) = softmax(log P(y) / T)`` — the standard multiclass temperature scaling (Guo et al.
    2017; Kull et al. 2019, source 17), applied to the count model's own log-probabilities
    rather than to NN logits, which is the same operation: it sharpens (``T<1``) or flattens
    (``T>1``) the distribution without changing its ranking.
    """
    lp = log_prior(model, state, history)
    m = max(lp.values())
    exps = {lb: math.exp((v - m) / temperature) for lb, v in lp.items()}
    z = sum(exps.values())
    return {lb: v / z for lb, v in exps.items()}


def fit_temperature(
    model: MarkovNextMoves,
    points: Sequence[DecisionPoint],
    *,
    bounds: tuple[float, float] = (0.05, 20.0),
) -> float:
    """ONE scalar temperature minimising log-loss on ``points`` — TRAIN only, never validation.

    ``scipy.optimize.minimize_scalar``, bounded. More than one calibration parameter is out of
    scope at this corpus's size (H3 pre-registration) — that bound is itself the finding if a
    single scalar cannot fix the calibration.
    """
    from scipy.optimize import minimize_scalar

    def loss(t: float) -> float:
        total = 0.0
        n = 0
        for p in points:
            d = temperature_scaled_dist(model, p.state, p.history, t)
            if p.target not in d:
                continue
            total += -math.log(d[p.target])
            n += 1
        return total / n if n else float("inf")

    res = minimize_scalar(loss, bounds=bounds, method="bounded")
    return float(res.x)


def markov_rank_fn(model: MarkovNextMoves) -> Any:
    """``MarkovNextMoves`` → the ``(point, k)`` callable :func:`evaluate` wants, with ``rel``."""

    def fn(p: DecisionPoint, k: int) -> list[tuple[str, float, str]]:
        return [
            (lb, pr, model.rel_of(p.state, lb)[0])
            for lb, pr in model.rank_next_moves(p.state, p.history, k)
        ]

    return fn


def rolling_origin_folds(
    bouts: Sequence[Mapping[str, Any]],
    cutoffs: Sequence[int] = (2023, 2024, 2025),
    *,
    library: Sequence[Mapping[str, Any]] | None = None,
    ks: Sequence[int] = (1, 3, 5),
    ci: bool = True,
    history_n: int = HISTORY_N,
) -> list[dict[str, Any]]:
    """H1's rolling-origin evaluation — one row per cutoff year, never pooled.

    Fold for ``cutoff`` trains on every bout with ``year <= cutoff`` (:func:`split_by_year`) and
    tests on the single FOLLOWING year only (``cutoff + 1``), matching the three folds
    pre-registered in ``docs/research/next_moves_literature.md`` §H1 (≤2023→2024, ≤2024→2025,
    ≤2025→2026) — a rolling origin, not one growing test tail. Reuses :func:`evaluate` for both
    the Markov ranker (``max_order=2``) and the marginal-frequency floor (``max_order=0``) so
    the gain-over-marginal each row reports is computed the same way as every other table in
    this module. A cutoff with no train or no test bouts is reported ``skipped`` rather than
    raising — a thin corpus year is a fact about the data, not a bug.
    """
    lib = library if library is not None else library_actions()
    rows: list[dict[str, Any]] = []
    for cutoff in cutoffs:
        train_bouts, _ = split_by_year(bouts, cutoff)
        test_bouts = [b for b in bouts if int(b.get("year") or 0) == cutoff + 1]
        if not train_bouts or not test_bouts:
            rows.append(
                {
                    "cutoff": cutoff,
                    "test_year": cutoff + 1,
                    "skipped": "no bouts in train or in the test year",
                }
            )
            continue
        ptr, _ = corpus_points(train_bouts, history_n=history_n)
        pte, _ = corpus_points(test_bouts, history_n=history_n)
        vocab = build_vocab(ptr, lib)
        markov = MarkovNextMoves(vocab, max_order=2).fit(ptr)
        marginal = MarkovNextMoves(vocab, max_order=0).fit(ptr)
        m_eval = evaluate(markov_rank_fn(markov), pte, ks=ks, ci=ci)
        b_eval = evaluate(markov_rank_fn(marginal), pte, ks=ks, ci=False)
        row = {
            "cutoff": cutoff,
            "test_year": cutoff + 1,
            "train_bouts": len(train_bouts),
            "train_points": len(ptr),
            "test_bouts": len(test_bouts),
            "test_points": len(pte),
            "marginal_top3": b_eval["top3"],
            "markov_top3": m_eval["top3"],
            "gain_top3": m_eval["top3"] - b_eval["top3"],
        }
        if ci:
            row["markov_top3_lo"] = m_eval.get("top3_lo")
            row["markov_top3_hi"] = m_eval.get("top3_hi")
        rows.append(row)
    return rows


# ── H2: the clock (staleness / elapsed-time ablation) ──────────────────────────


def staleness_bucket(staleness: int) -> int:
    """Arm (a)'s bucket: ``min(events since state, 3)`` — pre-registered in
    ``docs/research/next_moves_literature.md`` §H2."""
    return min(max(staleness, 0), 3)


def elapsed_bucket(elapsed_ts: float | None, *, bucket_s: float = 15.0) -> int:
    """Arm (b)'s bucket: 15-second-wide buckets, capped at 3 (same shape as
    :func:`staleness_bucket` so the two arms are directly comparable). ``None`` (no numeric
    ``ts`` on one of the two events) has no bucket here — callers must exclude those points
    rather than default them into bucket 0, same convention E9 used for its 47 missing-``ts``
    bouts (docs/research/next_moves_literature.md §H2)."""
    if elapsed_ts is None:
        raise ValueError("elapsed_ts is None — exclude the point, do not bucket it")
    return min(int(max(elapsed_ts, 0.0) // bucket_s), 3)


class StalenessMarkovNextMoves(MarkovNextMoves):
    """H2 ablation: the level-2 context is ``(state, prev_action, bucket)`` instead of
    ``(state, prev_action)``. Same Witten-Bell cascade, same backoff to ``(state)`` then the
    unigram (``MarkovNextMoves._wb``/``_p0``) — this is a different KEY on the existing count
    model, not a new model class (docs/research/next_moves_literature.md §H2: "no new model
    class").

    ``feature``: ``"staleness"`` (arm a), ``"elapsed"`` (arm b, drops points with no
    ``elapsed_ts`` from BOTH fit and scoring) or ``"both"`` (arm c).
    """

    def __init__(
        self, vocab: Sequence[str], *, feature: str = "staleness", max_order: int = 2
    ) -> None:
        if feature not in ("staleness", "elapsed", "both"):
            raise ValueError(f"feature must be staleness/elapsed/both, got {feature!r}")
        super().__init__(vocab, max_order=max_order)
        self.feature = feature
        self._c2b: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)

    def _usable(self, p: DecisionPoint) -> bool:
        return self.feature == "staleness" or p.elapsed_ts is not None

    def _bucket(self, p: DecisionPoint) -> tuple[int, ...]:
        if self.feature == "staleness":
            return (staleness_bucket(p.staleness),)
        if self.feature == "elapsed":
            return (elapsed_bucket(p.elapsed_ts),)
        return (staleness_bucket(p.staleness), elapsed_bucket(p.elapsed_ts))

    def fit(self, points: Iterable[DecisionPoint]) -> StalenessMarkovNextMoves:
        for p in points:
            if p.target not in self._index:
                self.n_oov += 1
                continue
            if not self._usable(p):
                continue  # feature="elapsed"/"both" and this point has no ts — excluded, not OOV
            self.n_fitted += 1
            self._c0[p.target] += 1
            self._c1[p.state][p.target] += 1
            prev = p.history[-1][0] if p.history else ""
            key = (p.state, prev, *self._bucket(p))
            self._c2b[key][p.target] += 1
            if p.rel_readable and p.target_rel != UNK:
                self._rel[(p.state, p.target)][p.target_rel] += 1
        return self

    def dist_for_point(self, p: DecisionPoint) -> dict[str, float]:
        """Full distribution conditioned on this exact point's state/history/bucket."""
        prev = p.history[-1][0] if p.history else ""
        key = (p.state, prev, *self._bucket(p))
        c1 = self._c1.get(p.state, Counter())
        c2 = self._c2b.get(key, Counter())
        out = {}
        for lb in self.vocab:
            v = self._p0(lb)
            if self.max_order >= 1:
                v = self._wb(c1, lb, v)
            if self.max_order >= 2:
                v = self._wb(c2, lb, v)
            out[lb] = v
        return out

    def rank_for_point(self, p: DecisionPoint, k: int) -> list[tuple[str, float, str]]:
        d = self.dist_for_point(p)
        ordered = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[: max(0, k)]
        return [(lb, pr, self.rel_of(p.state, lb)[0]) for lb, pr in ordered]


def staleness_ablation(
    bouts: Sequence[Mapping[str, Any]],
    cutoffs: Sequence[int] = (2023, 2024, 2025),
    *,
    library: Sequence[Mapping[str, Any]] | None = None,
    features: Sequence[str] = ("staleness",),
    history_n: int = HISTORY_N,
) -> list[dict[str, Any]]:
    """H2 — one row per (fold, feature): does the clock beat plain ``(state, prev_action)``?

    Kill rule (pre-registered, docs/research/next_moves_literature.md §H2): if ``"staleness"``
    is null, do not evaluate ``"elapsed"``/``"both"`` — pass ``features=("staleness",)`` until
    that row is read, exactly like :func:`rolling_origin_folds` reuses :func:`evaluate` so every
    row in this table is computed the same way as every other table in this module.
    """
    from analysis.stats_rigor import bootstrap_ci

    lib = library if library is not None else library_actions()
    rows: list[dict[str, Any]] = []
    for cutoff in cutoffs:
        train_bouts, _ = split_by_year(bouts, cutoff)
        test_bouts = [b for b in bouts if int(b.get("year") or 0) == cutoff + 1]
        if not train_bouts or not test_bouts:
            for feature in features:
                rows.append(
                    {
                        "cutoff": cutoff,
                        "test_year": cutoff + 1,
                        "feature": feature,
                        "skipped": "no bouts in train or in the test year",
                    }
                )
            continue
        ptr, _ = corpus_points(train_bouts, history_n=history_n)
        pte, _ = corpus_points(test_bouts, history_n=history_n)
        vocab = build_vocab(ptr, lib)
        base = MarkovNextMoves(vocab, max_order=2).fit(ptr)

        def base_dist(p: DecisionPoint, base: MarkovNextMoves = base) -> dict[str, float]:
            return base.dist(p.state, p.history)

        for feature in features:
            eval_pts = (
                pte if feature == "staleness" else [p for p in pte if p.elapsed_ts is not None]
            )
            arm = StalenessMarkovNextMoves(vocab, feature=feature).fit(ptr)

            def arm_dist(
                p: DecisionPoint, arm: StalenessMarkovNextMoves = arm
            ) -> dict[str, float]:
                return arm.dist_for_point(p)

            def arm_rank(
                p: DecisionPoint, k: int, arm: StalenessMarkovNextMoves = arm
            ) -> list[tuple[str, float, str]]:
                return arm.rank_for_point(p, k)

            diffs: list[float] = []
            groups: list[str] = []
            for p in eval_pts:
                bd, ad = base_dist(p), arm_dist(p)
                if p.target not in bd or p.target not in ad:
                    continue
                diffs.append(-math.log(bd[p.target]) - (-math.log(ad[p.target])))
                groups.append(p.bout_id)
            gain, lo, hi = (
                bootstrap_ci(diffs, lambda v: sum(v) / len(v), groups=groups)
                if diffs
                else (float("nan"), float("nan"), float("nan"))
            )
            m_eval = evaluate(markov_rank_fn(base), eval_pts, ks=(1, 3, 5))
            a_eval = evaluate(arm_rank, eval_pts, ks=(1, 3, 5))
            rows.append(
                {
                    "cutoff": cutoff,
                    "test_year": cutoff + 1,
                    "feature": feature,
                    "n": len(diffs),
                    "n_excluded_no_ts": len(pte) - len(eval_pts),
                    "logloss_gain": gain,
                    "logloss_gain_lo": lo,
                    "logloss_gain_hi": hi,
                    "base_top3": m_eval["top3"],
                    "arm_top3": a_eval["top3"],
                    "top3_gain_pp": (a_eval["top3"] - m_eval["top3"]) * 100,
                }
            )
    return rows


def log_prior(model: MarkovNextMoves, state: str, history: Sequence[Any]) -> dict[str, float]:
    """``{label: log P}`` — the term the hybrid scorer blends with cosine similarity.

    Log, not raw probability: the corpus prior is heavy-tailed (the modal continuation of a
    passing sequence outweighs the tenth by two orders of magnitude), so a linear blend would
    be the prior alone at every α below ~0.99. Strictly finite because :meth:`prob` is strictly
    positive.
    """
    return {lb: math.log(p) for lb, p in model.dist(state, history).items()}


def _demo() -> None:
    """Self-check — runnable without a database or a network."""
    seq = [
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Armbar", "type": "submission", "actor_id": "A"},
        {"label": "Guard Pass", "type": "pass", "actor_id": "B"},
        {"label": "Mount", "type": "control", "actor_id": "B"},
        {"label": "Armbar", "type": "submission", "actor_id": "B"},
    ]
    pts = decision_points(seq, "bout-1")
    assert [p.target for p in pts] == ["Armbar", "Guard Pass", "Armbar"], pts
    assert pts[0].state == "Closed Guard" and pts[0].target_rel == "own"
    assert pts[1].state == "Closed Guard" and pts[1].target_rel == "opp"
    assert pts[2].state == "Mount" and pts[2].target_rel == "own"

    m = MarkovNextMoves(["Armbar", "Guard Pass", "Heel Hook"]).fit(pts)
    d = m.dist("Closed Guard")
    assert abs(sum(d.values()) - 1.0) < 1e-9, sum(d.values())
    assert all(v > 0 for v in d.values())
    assert m.rank_next_moves("Closed Guard", (), 1)[0][0] == "Armbar"
    assert m.rel_of("Mount", "Armbar") == ("own", 1.0)

    tr, va = split_by_bout(pts + [p._replace(bout_id="bout-2") for p in pts])
    assert not ({p.bout_id for p in tr} & {p.bout_id for p in va})
    print("next_moves demo ok")


if __name__ == "__main__":
    _demo()
