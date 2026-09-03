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


# ── corpus → decision points ────────────────────────────────────────────────────


def _rel(actor: Any, state_actor: Any, readable: bool) -> str:
    if not readable or actor is None or state_actor is None:
        return UNK
    return OWN if actor == state_actor else OPP


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
            continue
        if kind != "action":
            continue
        if state is not None:
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

    def rel_of(self, state: str, label: str) -> tuple[str, float]:
        """Most likely relative actor for this move from this state, and its share.

        Fitted only on bouts ``attribution.bout_flags`` calls ``perspective_reliable``. An
        unseen pair answers ``("unk", 0.0)`` rather than guessing — 43.9% of the corpus cannot
        support this field at all and inventing a side there would be the exact defect
        ``analysis/attribution.py`` exists to refuse.
        """
        c = self._rel.get((state, label))
        if not c:
            return UNK, 0.0
        side, n = max(c.items(), key=lambda kv: (kv[1], kv[0]))
        return side, n / sum(c.values())


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
    return out


def markov_rank_fn(model: MarkovNextMoves) -> Any:
    """``MarkovNextMoves`` → the ``(point, k)`` callable :func:`evaluate` wants, with ``rel``."""

    def fn(p: DecisionPoint, k: int) -> list[tuple[str, float, str]]:
        return [
            (lb, pr, model.rel_of(p.state, lb)[0])
            for lb, pr in model.rank_next_moves(p.state, p.history, k)
        ]

    return fn


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
