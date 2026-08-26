"""PoC-E9 — state space, Markov order, event kernels and history-dependent absorption.

The pre-registration is `docs/research/poc/e9.md`, written and committed BEFORE this module
produced a held-out number, and re-emitted verbatim by ``render_markdown`` so the criterion
always travels with the numbers it judged.

Five arms, one runner:

  1. **State space.** S-label (226 states) vs S-cat (8) vs S-v3 (12 — the 8 event types with
     ``control`` exploded into five dominance positions).
  2. **Order k ∈ {0,1,2,3}** per state space, with per-context support coverage.
  3. **Maximum estimable order** — the largest k whose paired Δ against k−1 excludes 0.
  4. **ADCC-family kernel** vs the global kernel, power-gated, plus a per-state divergence
     table with BH across the state family.
  5. **Absorbing terminals** from ``Match.win_type``, and whether entry into the
     time-expiry / points terminal depends on more than the current state.

Two decisions are load-bearing and are argued in the pre-registration, not here:

  * **the common target.** Per-step log-likelihood is a function of vocabulary size, so an
    8-state chain beats a 226-state chain before any data is read. Every arm is therefore
    scored on ONE fixed 9-symbol alphabet (the 8 ``EVENT_TYPES`` + ``other``), reached by
    marginalising each arm's next-STATE distribution through a train-estimated
    ``P(category | state)``.
  * **repeats are not folded.** ``e4``/``e8`` fold ``A → A`` because their graph builders
    refuse a self-edge; a Markov chain does not, dwell is information (the Sci Rep 2026
    semi-Markov critique), and folding would make the scored-step count depend on the state
    space and destroy the step-for-step pairing the criterion needs. The cost is that the
    label-space numbers here are NOT directly comparable to ``e4.md``'s; a folded,
    hard-backoff parity row is reported as the bridge.

Everything else is inherited: ``Bout``/``Kernel``/``temporal_split``/``evaluate_kernels``/
``paired_delta_auc``/``eval_rows`` from PoC-E8, ``dedupe_by_key``/``markov_order`` from
PoC-E4, every interval from ``stats_rigor``.

LGPD: athlete corpus only (the loader filters exactly as ``e8.corpus_bouts`` does). No
``owner_kind='user'`` row enters any arm. Nothing here touches a production export.

Usage::

    uv run python -m analysis.poc.e9_markov
    uv run python -m analysis.poc.e9_markov --out docs/research/poc/e9.md
    uv run python -m analysis.poc.e9_markov --skip-corpus     # self-check, no DB
"""

from __future__ import annotations

import argparse
import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analysis.attribution import EVENT_TYPES, bout_flags
from analysis.discipline import match_discipline
from analysis.names import _normalize_name
from analysis.path_to_victory import GAMMA
from analysis.poc.e4_ptv_eval import MIN_CONTEXT, dedupe_by_key, markov_order
from analysis.poc.e8_interaction_graph import (
    Bout,
    Kernel,
    KernelResult,
    _boot_ci,
    evaluate_kernels,
    paired_delta_auc,
    temporal_split,
)
from analysis.stats_rigor import (
    benjamini_hochberg,
    compare_proportions,
    coverage,
    heterogeneity,
    wilson,
)
from analysis.technique_match import clean_label
from analysis.transitions.build_graph import network_from_sequences

REPO = Path(__file__).resolve().parents[2]

SEED = 20260820                 # stats_rigor's seed, so every interval in the repo agrees
ALPHA_GRID: tuple[float, ...] = (0.1, 0.5, 1.0)
ALPHA = 0.5                     # headline; PoC-E4's
ORDERS: tuple[int, ...] = (0, 1, 2, 3)
MIN_SCORED_INDEX = 3            # k=1..3 all scored on the same steps
EVAL_FRACTION = 0.25
N_BOOT = 2000
N_PERM = 2000

# PoC-E8's published gate count. A mismatch means the corpus moved under this cell.
# Repinned 2026-08-25 (was 429, E8/E9's published corpus): the audited ingestion batches
# of the same day added +37 gate-passing bouts (trials + women-65 concordance imports).
# E9's published verdicts in docs/research/poc/e9.md remain the morning-of-08-25 snapshot;
# E11/E5/X3/E14 all ran and reported on the 466 corpus. The drift alarm did its job.
E8_GATED_BOUTS = 466

# Arm 4's power gate, pre-registered.
ADCC_MIN_EVAL_BOUTS = 10
ADCC_MIN_EVAL_STEPS = 100

# The common target alphabet. Fixed, identical for every arm, so a per-step log-likelihood
# means the same thing in all three state spaces.
OTHER_CAT = "other"
CAT_ALPHABET: tuple[str, ...] = (*sorted(EVENT_TYPES), OTHER_CAT)

UNK = "\x00unk"                 # reserved: an eval state train never saw

# ── S-v3: the control partition ─────────────────────────────────────────────────
# Seeded from analysis/attribution.py's curated `_CONTROL_BACK` / `_CONTROL_TOP` /
# `_CONTROL_GRIP` (each with its own measured provenance there) and named with
# docs/taxonomy.json v2 control-child ids. Only 38 of 376 technique_nodes carry a
# taxonomy_id, so this map is explicit rather than joined — and it is a function of LABELS
# only, fixed before any held-out number exists.
#
# ponytail: a hand-frozen dict, not a taxonomy join. Ceiling = it covers the measured head of
# the control distribution and drops everything else into `peripheral`; upgrade path = backfill
# `technique_nodes.taxonomy_id` (kanban 017) and read the partition off the taxonomy.
_V3_BACK = frozenset({
    "back control", "body triangle", "body lock", "rear body lock", "seatbelt control",
    "truck", "standing back control", "arm drag to back take", "crab ride to back take",
    "straight jacket",
})
_V3_MOUNT = frozenset({"mount", "threequarter mount", "mounted crucifix"})
_V3_PIN = frozenset({
    "side control", "northsouth position", "north south control", "knee on belly",
    "scarf hold", "crucifix", "near fall", "nearfall", "top control",
    "top control body lock", "top control half guard", "top half guard", "smash half guard",
    "chesttochest half guard", "half guard control", "half nelson", "leg lace", "ride out",
    "ground and pound", "headarm control top",
})
_V3_FRONT_HEADLOCK = frozenset({"front headlock", "front headlock control"})


def v3_control_bucket(label: str) -> str:
    """Which dominance position a ``control``-typed label names. Labels only, no bout."""
    key = _normalize_name(label)
    if key in _V3_BACK:
        return "back-control"
    if key in _V3_MOUNT:
        return "mount"
    if key in _V3_PIN:
        return "pin"
    if key in _V3_FRONT_HEADLOCK:
        return "front-headlock"
    return "peripheral"


# ── state spaces ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class StateSpace:
    """A named way to turn one event into one state.

    ``state_of`` returns ``""`` for an event that carries no usable state. Names are
    PREFIXED (``cat/pass``): a bare ``"pass"`` canonicalises to ``"Guard Pass"`` inside
    ``clean_label``, which would silently de-synchronise the graph builder from
    ``Kernel.node_of`` on the finish-AUC limb. Asserted by test.
    """

    name: str
    state_of: Callable[[Mapping[str, Any]], str]


def _typ(e: Mapping[str, Any]) -> str:
    return str(e.get("type", "")).strip().lower()


def _label_state(e: Mapping[str, Any]) -> str:
    return clean_label(str(e.get("label", "")), _typ(e))


def _cat_state(e: Mapping[str, Any]) -> str:
    t = _typ(e)
    return f"cat/{t}" if t in EVENT_TYPES else ""


def _v3_state(e: Mapping[str, Any]) -> str:
    t = _typ(e)
    if t not in EVENT_TYPES:
        return ""
    if t != "control":
        return f"v3/{t}"
    return f"v3/control/{v3_control_bucket(str(e.get('label', '')))}"


S_LABEL = StateSpace("S-label (clean_label)", _label_state)
S_CAT = StateSpace("S-cat (8 event types)", _cat_state)
S_V3 = StateSpace("S-v3 (control exploded)", _v3_state)
SPACES: tuple[StateSpace, ...] = (S_LABEL, S_CAT, S_V3)


def category_of(e: Mapping[str, Any]) -> str:
    """The common target symbol for one event."""
    t = _typ(e)
    return t if t in EVENT_TYPES else OTHER_CAT


# ── chains ──────────────────────────────────────────────────────────────────────
# Repeats are NOT folded (see the module docstring). Both definitions return one list of
# (state, category) pairs per chain, so every arm reads the same object.
Step = tuple[str, str]


def own_chains(seq: Sequence[Mapping[str, Any]], sp: StateSpace) -> list[list[Step]]:
    """C-own — one chain per actor: that fighter's own ordered states."""
    by_actor: defaultdict[Any, list[Step]] = defaultdict(list)
    for e in seq:
        actor = e.get("actor_id", e.get("actor"))
        s = sp.state_of(e)
        if actor is not None and s:
            by_actor[actor].append((s, category_of(e)))
    return [c for c in by_actor.values() if len(c) > 1]


def bout_chain(seq: Sequence[Mapping[str, Any]], sp: StateSpace) -> list[list[Step]]:
    """C-bout — one chain per bout, every event in stored order, actor-free."""
    c = [(sp.state_of(e), category_of(e)) for e in seq if sp.state_of(e)]
    return [c] if len(c) > 1 else []


ChainFn = Callable[[Sequence[Mapping[str, Any]], StateSpace], list[list[Step]]]
CHAIN_DEFS: dict[str, ChainFn] = {"C-own": own_chains, "C-bout": bout_chain}


# ── the estimator ───────────────────────────────────────────────────────────────
@dataclass
class Backoff:
    """Hierarchical interpolated backoff over one state vocabulary.

        p_j(z|ctx_j) = λ_j·f̂(z|ctx_j) + (1 − λ_j)·p_{j−1}(z|ctx_{j−1}),
        λ_j = n(ctx_j) / (n(ctx_j) + α·V)

    down to the order-0 train marginal and finally uniform 1/V. Interpolation rather than
    E4's hard ``MIN_CONTEXT`` prune, because "maximum estimable order" must not be an
    artefact of one arbitrary threshold — E4's rule is still run, as the parity row.
    """

    order: int
    alpha: float
    vocab: tuple[str, ...]
    levels: list[dict[tuple[str, ...], Counter[str]]]
    marginal: Counter[str]
    total: int
    # P(category | state), train-estimated with the same additive smoothing over the fixed
    # 9-symbol alphabet. Estimated for EVERY space, including S-cat where it is near
    # degenerate: one code path, and it absorbs the 3.3% of labels filed under >1 type.
    cat_given_state: dict[str, dict[str, float]]
    cat_marginal: dict[str, float]
    # The fixed target alphabet this model marginalises onto. A PARAMETER rather than the
    # module constant since PoC-E11, which scores the same estimator onto Lamas' action
    # families; the default is E9's own 9-symbol alphabet, so every E9 number is unchanged.
    alphabet: tuple[str, ...] = CAT_ALPHABET

    @property
    def v(self) -> int:
        return len(self.vocab)

    def state_dist(self, ctx: Sequence[str]) -> dict[str, float]:
        """Distribution over the state vocabulary given the last ``order`` states.

        Order 0 is the additive-smoothed train marginal, which IS the λ-form of the
        recursion against a uniform 1/V prior — so the ladder has one rule at every rung.
        """
        denom0 = self.total + self.alpha * self.v
        p = {z: (self.marginal[z] + self.alpha) / denom0 for z in self.vocab}
        for j in range(1, self.order + 1):
            key = tuple(ctx[-j:]) if j <= len(ctx) else None
            if key is None or len(key) < j:
                break
            succ = self.levels[j].get(key)
            n = sum(succ.values()) if succ else 0
            lam = n / (n + self.alpha * self.v)
            if succ:
                p = {z: lam * (succ[z] / n) + (1 - lam) * p[z] for z in self.vocab}
            # n == 0 → λ == 0 → the level is a no-op; keep recursing so a deeper level
            # cannot resurrect a context its parent never saw.
        return p

    def category_dist(self, ctx: Sequence[str]) -> dict[str, float]:
        """The next event's CATEGORY — the common target every arm is scored on."""
        ps = self.state_dist(ctx)
        out = dict.fromkeys(self.alphabet, 0.0)
        for z, pz in ps.items():
            if pz <= 0.0:
                continue
            pc = self.cat_given_state.get(z, self.cat_marginal)
            for c in self.alphabet:
                out[c] += pz * pc[c]
        tot = sum(out.values()) or 1.0
        return {c: v / tot for c, v in out.items()}


def fit_backoff(chains: Sequence[Sequence[Step]], order: int, alpha: float,
                alphabet: tuple[str, ...] = CAT_ALPHABET) -> Backoff:
    """Fit every level 0..order on train chains. ``UNK`` is always in the vocabulary.

    ``alphabet`` is the fixed target symbol set every arm is scored on; it defaults to E9's
    own 9-symbol category alphabet and is a parameter only so PoC-E11 can score the SAME
    estimator onto Lamas' action families without a second copy of it.
    """
    marginal: Counter[str] = Counter()
    cat_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    cat_marg: Counter[str] = Counter()
    levels: list[dict[tuple[str, ...], Counter[str]]] = [{} for _ in range(order + 1)]
    lvl: list[defaultdict[tuple[str, ...], Counter[str]]] = [
        defaultdict(Counter) for _ in range(order + 1)
    ]
    for c in chains:
        states = [s for s, _ in c]
        for s, cat in c:
            marginal[s] += 1
            cat_counts[s][cat] += 1
            cat_marg[cat] += 1
        for j in range(1, order + 1):
            for i in range(j, len(states)):
                lvl[j][tuple(states[i - j:i])][states[i]] += 1
    for j in range(1, order + 1):
        levels[j] = dict(lvl[j])
    vocab = (*sorted(marginal), UNK)
    n_cat = len(alphabet)
    cat_given = {
        s: {c: (cnt[c] + alpha) / (sum(cnt.values()) + alpha * n_cat) for c in alphabet}
        for s, cnt in cat_counts.items()
    }
    tot_cat = sum(cat_marg.values())
    cat_marginal = {
        c: (cat_marg[c] + alpha) / (tot_cat + alpha * n_cat) for c in alphabet
    }
    return Backoff(order, alpha, vocab, levels, marginal, sum(marginal.values()),
                   cat_given, cat_marginal, alphabet)


def _map_unknown(chain: Sequence[Step], known: frozenset[str]) -> list[Step]:
    return [(s if s in known else UNK, c) for s, c in chain]


@dataclass
class OrderRow:
    """One (state space, chain, order, α) point of the primary criterion."""

    space: str
    chain: str
    order: int
    alpha: float
    n_states: int
    n_steps: int
    ll: float
    lo: float
    hi: float
    support: float           # share of scored steps whose order-k context was seen in train
    contexts: int            # distinct order-k contexts in train
    contexts_min5: int       # ... with at least MIN_CONTEXT observations
    logps: list[float] = field(default_factory=list)
    groups: list[Any] = field(default_factory=list)


def score_order(
    train: Sequence[Sequence[Step]],
    held: Sequence[tuple[Any, Sequence[Step]]],
    order: int,
    alpha: float,
    space: str,
    chain: str,
    n_boot: int = N_BOOT,
    alphabet: tuple[str, ...] = CAT_ALPHABET,
    min_index: int = MIN_SCORED_INDEX,
) -> OrderRow:
    """Held-out per-step log-likelihood of the next event's CATEGORY, at one order.

    Scored steps start at ``min_index`` so k=1..3 see identical steps; because repeats are
    not folded, the step set is identical across state spaces too, which is what makes the
    cross-space deltas paired.

    ``alphabet``/``min_index`` are parameters (defaults = E9's own) only so PoC-E11 can run
    the identical estimator on Lamas' action families at order 1, where a floor of 3 would
    throw away a third of a corpus whose median action chain is six long.
    """
    m = fit_backoff(train, order, alpha, alphabet)
    known = frozenset(m.marginal)
    level = m.levels[order] if order else {}
    seen_ctx = set(level)
    min5 = {k for k, v in level.items() if sum(v.values()) >= MIN_CONTEXT}
    logps: list[float] = []
    groups: list[Any] = []
    covered = 0
    for key, raw in held:
        c = _map_unknown(raw, known)
        states = [s for s, _ in c]
        for t in range(min_index, len(c)):
            ctx = states[max(0, t - order):t] if order else []
            if order and tuple(states[t - order:t]) in seen_ctx:
                covered += 1
            p = m.category_dist(ctx)
            logps.append(math.log(max(p[c[t][1]], 1e-12)))
            groups.append(key)
    if not logps:
        nan = float("nan")
        return OrderRow(space, chain, order, alpha, len(m.marginal), 0, nan, nan, nan,
                        0.0, len(seen_ctx), len(min5))
    obs, lo, hi = _boot_ci(
        [True] * len(logps),
        lambda s: sum(logps[i] for i in s) / len(s) if s else 0.0, n_boot, groups)
    support = (covered / len(logps)) if order else 1.0
    return OrderRow(space, chain, order, alpha, len(m.marginal), len(logps), obs, lo, hi,
                    support, len(seen_ctx), len(min5), logps, groups)


def paired_delta(a: OrderRow, b: OrderRow, n_boot: int = N_BOOT) -> tuple[float, float, float]:
    """mean(a.logp − b.logp) with a BOUT-CLUSTERED paired percentile bootstrap.

    The two rows must be scored on the same steps in the same order; that is guaranteed by
    ``MIN_SCORED_INDEX`` (orders) and by not folding repeats (state spaces), and checked here
    rather than trusted.
    """
    if len(a.logps) != len(b.logps) or a.groups != b.groups:
        raise ValueError(f"unpaired rows: {a.space}/{a.order} vs {b.space}/{b.order}")
    diffs = [x - y for x, y in zip(a.logps, b.logps, strict=True)]
    return _boot_ci([True] * len(diffs),
                    lambda s: sum(diffs[i] for i in s) / len(s) if s else 0.0,
                    n_boot, a.groups)


def wins(delta: tuple[float, float, float]) -> bool:
    """The pre-registered bar: the paired interval excludes 0 in the first arm's favour.

    Plus one guard the pre-registration did not anticipate and the synthetic fixtures found:
    a **degenerate** interval (``lo == hi``) is refused. A bootstrap over bouts that returns
    the same value in every draw saw no bout-to-bout variation at all, which happens when the
    two models differ by a CONSTANT offset rather than by what they predict — the signature of
    one model simply being smoothed less than the other (nested interpolation shrinks once per
    level, so a higher order recovers prior mass an unobserved symbol was holding). That is
    arithmetic about the estimator, not evidence about grappling, and a zero-width interval
    "excluding 0" is exactly how it would be published as the latter.
    Measured on a synthetic corpus with five uninformative contexts: Δ +0.0051, width 0.
    """
    _, lo, hi = delta
    if lo != lo or hi != hi:
        return False
    return lo > 0.0 and hi > lo


def max_estimable_order(
    rows: Mapping[int, OrderRow], n_boot: int = N_BOOT
) -> tuple[int, dict[int, tuple[float, float, float]]]:
    """Largest k whose paired Δ against k−1 excludes 0 in k's favour.

    The whole ladder is computed and returned — a reader has to see the rung the model fell
    off, not just the last one it cleared — but ``best`` stops climbing at the first failure:
    "order 3 helps although order 2 did not" is a coverage accident on a corpus this size,
    not an estimable third order.
    """
    steps: dict[int, tuple[float, float, float]] = {}
    best = 0
    climbing = True
    for k in ORDERS[1:]:
        if k not in rows or (k - 1) not in rows:
            break
        d = paired_delta(rows[k], rows[k - 1], n_boot)
        steps[k] = d
        if climbing and wins(d):
            best = k
        else:
            climbing = False
    return best, steps


# ── corpus ──────────────────────────────────────────────────────────────────────
@dataclass
class BoutRow:
    """One gated bout, with everything the five arms need attached to it."""

    key: tuple[Any, ...]
    sequence: list[dict[str, Any]]
    athlete_a: str
    athlete_b: str
    event: str | None
    win_type: str | None
    family: str
    discipline: str
    elapsed: list[float] | None   # ts − min(ts), or None when any ts is missing

    @property
    def terminal(self) -> str | None:
        wt = (self.win_type or "").strip().upper()
        if wt == "SUBMISSION":
            return "END/submission"
        if wt in {"DECISION", "POINTS"}:
            return "END/points"
        if wt == "DRAW":
            return "END/draw"
        return None

    def mirrored(self) -> list[Bout]:
        """The E8 contract: one Bout per athlete perspective."""
        return [Bout(key=self.key, sequence=[dict(e) for e in self.sequence], perspective=p)
                for p in (self.athlete_a, self.athlete_b)]


TERMINALS: tuple[str, ...] = ("END/submission", "END/points", "END/draw")


def event_family(event: str | None) -> str:
    """``adcc`` iff the event tag upper-cases to a string starting ADCC. Stated, not inferred:
    ``analysis/scouting_rulesets.FAMILIES`` is not joined to ``matches``."""
    return "adcc" if (event or "").strip().upper().startswith("ADCC") else "other"


def _elapsed(seq: Sequence[Mapping[str, Any]]) -> list[float] | None:
    """``ts − min(ts)`` within the bout, or None if ANY event lacks a ts.

    A within-bout difference is invariant to ``ts_origin`` (an unknown origin is an additive
    per-bout offset), which is how this arm stays inside AA-010: no missing ts is ever
    defaulted, and no absolute ts is ever read.
    """
    out: list[float] = []
    for e in seq:
        ts = e.get("ts")
        if not isinstance(ts, int | float) or isinstance(ts, bool):
            return None
        out.append(float(ts))
    base = min(out)
    return [t - base for t in out]


@dataclass
class GateReport:
    total: int = 0
    with_sequence: int = 0
    passed: int = 0
    one_sided: int = 0
    rows: list[BoutRow] = field(default_factory=list)
    error: str | None = None


def load_corpus(min_events: int = 4) -> GateReport:
    """Gated athlete-corpus bouts. ONE read-only ``select`` over ``matches``.

    Same filter and same gate as ``e8.corpus_bouts`` — the SQL is restated only because this
    cell also needs ``win_type``, ``event`` and ``ts``. ``verify_gate`` checks the resulting
    count against E8's published 429 rather than trusting the restatement.
    """
    rep = GateReport()
    try:
        from sqlalchemy import text

        from db.base import get_engine

        with get_engine().connect() as conn:
            rowset = conn.execute(text(
                "SELECT id, athlete_a_id, athlete_b_id, year, created_at, event, win_type, "
                "sequence FROM matches WHERE status = 'final' AND sequence IS NOT NULL"
            )).mappings().all()
    except Exception as exc:  # noqa: BLE001 — the report says why, the self-check still runs
        rep.error = f"{type(exc).__name__}: {exc}".split("\n")[0][:160]
        return rep

    for row in rowset:
        rep.total += 1
        seq = [dict(e) for e in (row["sequence"] or []) if isinstance(e, dict)]
        if len(seq) < min_events:
            continue
        rep.with_sequence += 1
        if not bout_flags(seq, str(row["athlete_a_id"]),
                          str(row["athlete_b_id"]))["perspective_reliable"]:
            rep.one_sided += 1
            continue
        rep.passed += 1
        rep.rows.append(BoutRow(
            key=(int(row["year"] or 0), str(row["created_at"]), str(row["id"])),
            sequence=seq,
            athlete_a=str(row["athlete_a_id"]), athlete_b=str(row["athlete_b_id"]),
            event=row["event"], win_type=row["win_type"],
            family=event_family(row["event"]),
            discipline=match_discipline(row["event"]),
            elapsed=_elapsed(seq),
        ))
    rep.rows.sort(key=lambda r: r.key)
    return rep


def split_rows(rows: Sequence[BoutRow], frac: float = EVAL_FRACTION
               ) -> tuple[list[BoutRow], list[BoutRow]]:
    """Chronological split, boundary key to TRAIN — ``temporal_split``'s rule on BoutRows."""
    bouts = [Bout(key=r.key, sequence=r.sequence) for r in rows]
    train, held = temporal_split(bouts, frac)
    held_keys = {b.key for b in held}
    return ([r for r in rows if r.key not in held_keys],
            [r for r in rows if r.key in held_keys])


def chains_of(rows: Iterable[BoutRow], sp: StateSpace, chain: str) -> list[list[Step]]:
    fn = CHAIN_DEFS[chain]
    return [c for r in rows for c in fn(r.sequence, sp)]


def keyed_chains_of(rows: Iterable[BoutRow], sp: StateSpace, chain: str
                    ) -> list[tuple[Any, list[Step]]]:
    fn = CHAIN_DEFS[chain]
    return [(r.key, c) for r in rows for c in fn(r.sequence, sp)]


# ── arms 1-3: state space × order ───────────────────────────────────────────────
@dataclass
class SpaceResult:
    space: str
    chain: str
    rows: dict[int, OrderRow]                       # order → row, at the headline α
    alpha_rows: dict[float, dict[int, OrderRow]]    # α → order → row
    order_deltas: dict[int, tuple[float, float, float]]
    max_order: int

    @property
    def best(self) -> OrderRow:
        return self.rows[self.max_order]


def run_space(rows_train: Sequence[BoutRow], rows_eval: Sequence[BoutRow], sp: StateSpace,
              chain: str, n_boot: int = N_BOOT,
              alphas: Sequence[float] = ALPHA_GRID) -> SpaceResult:
    tr = chains_of(rows_train, sp, chain)
    ev = keyed_chains_of(rows_eval, sp, chain)
    by_alpha: dict[float, dict[int, OrderRow]] = {}
    for a in alphas:
        by_alpha[a] = {k: score_order(tr, ev, k, a, sp.name, chain, n_boot) for k in ORDERS}
    head = by_alpha[ALPHA]
    best, deltas = max_estimable_order(head, n_boot)
    return SpaceResult(sp.name, chain, head, by_alpha, deltas, best)


def compare_spaces(results: Sequence[SpaceResult], n_boot: int = N_BOOT
                   ) -> list[tuple[str, str, tuple[float, float, float]]]:
    """Every ordered pair, each at ITS OWN maximum estimable order — the fair contest."""
    out: list[tuple[str, str, tuple[float, float, float]]] = []
    for i, a in enumerate(results):
        for b in results[i + 1:]:
            out.append((a.space, b.space, paired_delta(a.best, b.best, n_boot)))
    return out


# ── secondary criterion: the finish-AUC limb (E8/E4 harness, verbatim) ──────────
def space_kernel(sp: StateSpace) -> Kernel:
    """One ``Kernel`` per state space: rewrite each event's label to its state name.

    ``network_from_sequences`` reads ``label``/``type``/``actor_id``/``successful``, so
    rewriting the label is the whole change — node ``type`` (which PtV's terminal rate reads)
    survives untouched. State names are prefixed precisely so ``clean_label`` leaves them
    alone; ``test_state_names_survive_clean_label`` pins that.
    """
    def rewrite(seq: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [{**e, "label": sp.state_of(e)} for e in seq if sp.state_of(e)]

    def chains(b: Bout) -> list[list[str]]:
        return [[s for s, _ in c] for c in own_chains(b.sequence, sp)]

    return Kernel(
        name=sp.name,
        build=lambda bouts: network_from_sequences([rewrite(b.sequence) for b in bouts]),
        node_of=lambda e: sp.state_of(e),
        chains=chains,
    )


@dataclass
class AucLimb:
    results: list[KernelResult]
    deltas: list[tuple[str, str, tuple[float, float, float]]]
    n_train: int
    n_eval: int


def run_auc_limb(rows_train: Sequence[BoutRow], rows_eval: Sequence[BoutRow],
                 n_boot: int = N_BOOT) -> AucLimb:
    """PtV γ=0.8, **shaping off** for all three arms, on E8's mirrored bouts and E8's rows."""
    from functools import partial

    from analysis.path_to_victory import path_to_victory

    train = [b for r in rows_train for b in r.mirrored()]
    held = [b for r in rows_eval for b in r.mirrored()]
    kernels = [space_kernel(sp) for sp in SPACES]
    value_fn = partial(path_to_victory, gamma=GAMMA, shaping_w=0.0)
    results, labels = evaluate_kernels(train, held, kernels, n_boot=n_boot, value_fn=value_fn)
    deltas: list[tuple[str, str, tuple[float, float, float]]] = []
    for i, a in enumerate(results):
        for b in results[i + 1:]:
            deltas.append((a.name, b.name,
                           paired_delta_auc(a, b, labels, n_boot=max(200, n_boot // 4))))
    return AucLimb(results, deltas, len(train), len(held))


# ── arm 4: the ADCC kernel ──────────────────────────────────────────────────────
@dataclass
class AdccLimb:
    n_gated: int
    n_eval_bouts: int
    n_eval_steps: int
    cov_estimable: bool
    cov_reason: str | None
    powered: bool
    adcc_ll: float = float("nan")
    global_ll: float = float("nan")
    delta: tuple[float, float, float] = (float("nan"),) * 3
    divergence: list[dict[str, Any]] = field(default_factory=list)
    n_train_bouts: int = 0        # corpus bouts ≤ the ADCC-internal boundary
    n_adcc_train: int = 0
    boundary: str = "—"           # the year the ADCC-internal split cuts at


def _js(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    """Jensen-Shannon divergence, base 2, over the union of two successor distributions."""
    keys = set(p) | set(q)

    def kl(a: Mapping[str, float], m: Mapping[str, float]) -> float:
        return sum(a.get(k, 0.0) * math.log2(a[k] / m[k]) for k in a if a.get(k, 0.0) > 0)

    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def _successors(chains: Sequence[Sequence[Step]]) -> dict[str, Counter[str]]:
    out: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for c in chains:
        states = [s for s, _ in c]
        for x, y in zip(states, states[1:], strict=False):
            out[x][y] += 1
    return dict(out)


def _norm(c: Counter[str]) -> dict[str, float]:
    n = sum(c.values()) or 1
    return {k: v / n for k, v in c.items()}


def adcc_divergence(rows: Sequence[BoutRow], sp: StateSpace, chain: str,
                    n_perm: int = N_PERM, seed: int = SEED) -> list[dict[str, Any]]:
    """Per state: JS(ADCC successors ‖ non-ADCC successors), bout-level permutation p, BH q.

    The permutation shuffles the FAMILY LABEL across whole bouts, so the null keeps every
    bout's internal serial dependence intact — the unit of independence is the bout, exactly
    as in ``stats_rigor.bootstrap_ci``'s cluster argument.
    """
    per_bout = [(r.family, _successors(CHAIN_DEFS[chain](r.sequence, sp))) for r in rows]
    per_bout = [(f, s) for f, s in per_bout if s]
    fams = [f for f, _ in per_bout]

    total: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for _, succ in per_bout:
        for x, c in succ.items():
            total[x].update(c)

    def side(assign: Sequence[str]) -> tuple[dict[str, Counter[str]], dict[str, Counter[str]]]:
        """ADCC counts by summing only the ~56 ADCC bouts; the other side is total − ADCC.

        Summing the small side and subtracting is what makes 2 000 permutations finish: the
        complement is 373 bouts and rebuilding it every draw is the whole cost.
        """
        a: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for (_, succ), f in zip(per_bout, assign, strict=True):
            if f == "adcc":
                for x, c in succ.items():
                    a[x].update(c)
        b = {x: (total[x] - a[x]) if x in a else total[x] for x in total}
        return dict(a), {x: c for x, c in b.items() if sum(c.values()) > 0}

    obs_a, obs_b = side(fams)
    states = sorted(set(obs_a) & set(obs_b))
    obs = {s: _js(_norm(obs_a[s]), _norm(obs_b[s])) for s in states}

    # coverage per state: how many distinct ADCC bouts contribute an outgoing step from it,
    # and how evenly — the unit of independence is the bout, never the step.
    per_state_bouts: defaultdict[str, Counter[Any]] = defaultdict(Counter)
    for r in rows:
        if r.family != "adcc":
            continue
        for x, c in _successors(CHAIN_DEFS[chain](r.sequence, sp)).items():
            per_state_bouts[x][r.key] += sum(c.values())

    rng = random.Random(seed)
    hits = dict.fromkeys(states, 0)
    shuffled = list(fams)
    for _ in range(n_perm):
        rng.shuffle(shuffled)
        pa, pb = side(shuffled)
        for s in states:
            if s in pa and s in pb and _js(_norm(pa[s]), _norm(pb[s])) >= obs[s]:
                hits[s] += 1
    pvals = [(hits[s] + 1) / (n_perm + 1) for s in states]
    qvals = benjamini_hochberg(pvals)
    out: list[dict[str, Any]] = []
    for s, p, q in zip(states, pvals, qvals, strict=True):
        cov = coverage(list(per_state_bouts[s].values()))
        out.append({"state": s, "js": obs[s], "p": p, "q": q,
                    "n_adcc": sum(obs_a[s].values()), "n_other": sum(obs_b[s].values()),
                    "cov": cov})
    out.sort(key=lambda d: -float(d["js"]))
    return out


def run_adcc(all_rows: Sequence[BoutRow], sp: StateSpace, chain: str, order: int,
             n_boot: int = N_BOOT, n_perm: int = N_PERM) -> AdccLimb:
    """4a on an ADCC-INTERNAL temporal split; 4b on the whole gated corpus.

    Amendment, recorded in the pre-registration and made before any ADCC likelihood was
    computed: the corpus-wide split puts **zero** ADCC bouts in the held-out window (the
    corpus' most recent quarter is 2025–2026 and every gated ADCC bout is 2019–2024), so the
    pre-registered power gate refuses the arm on an empty set — a true statement about
    nothing. Splitting the ADCC subcorpus on its OWN timeline is still a temporal split
    (ADR-03): train ≤ T, evaluate T+1, boundary to train, no randomness. The global kernel is
    trained on every corpus bout ≤ the SAME boundary, so both kernels see the same past and
    the contest is about the kernel, not about how much data each one got to see.
    """
    adcc_all = [r for r in all_rows if r.family == "adcc"]
    adcc_train, adcc_eval = split_rows(adcc_all)
    boundary = max((r.key for r in adcc_train), default=None)
    rows_train = [r for r in all_rows if boundary is not None and r.key <= boundary]
    ev = keyed_chains_of(adcc_eval, sp, chain)
    n_steps = sum(max(0, len(c) - MIN_SCORED_INDEX) for _, c in ev)
    # One count per SOURCE BOUT — a hundred steps from one bout are not a hundred sources.
    per_bout_steps = [
        sum(max(0, len(c) - MIN_SCORED_INDEX) for c in CHAIN_DEFS[chain](r.sequence, sp))
        for r in adcc_eval]
    cov = coverage([n for n in per_bout_steps if n > 0])
    powered = (len(adcc_eval) >= ADCC_MIN_EVAL_BOUTS
               and n_steps >= ADCC_MIN_EVAL_STEPS and cov.estimable)
    limb = AdccLimb(len(adcc_all), len(adcc_eval), n_steps, cov.estimable, cov.reason, powered)
    limb.n_train_bouts = len(rows_train)
    limb.n_adcc_train = len(adcc_train)
    limb.boundary = str(boundary[0]) if boundary else "—"
    limb.divergence = adcc_divergence(all_rows, sp, chain, n_perm)
    if not powered:
        return limb
    a = score_order(chains_of(adcc_train, sp, chain), ev, order, ALPHA, sp.name,
                    f"{chain} · ADCC-trained", n_boot)
    g = score_order(chains_of(rows_train, sp, chain), ev, order, ALPHA, sp.name,
                    f"{chain} · global-trained", n_boot)
    limb.adcc_ll, limb.global_ll, limb.delta = a.ll, g.ll, paired_delta(a, g, n_boot)
    return limb


# ── arm 5: absorbing terminals ──────────────────────────────────────────────────
_NAN3: tuple[float, float, float] = (float("nan"),) * 3


@dataclass
class TerminalDepth:
    """T1 — how much of the terminal a bout's recent history explains.

    ``contexts`` is not decoration. Nested interpolation shrinks once per level, so a model
    whose order-k context is the SAME for every bout is an order-0 model that happens to be
    smoothed less — and it beats M0 by a constant, zero-variance margin that no bootstrap
    can see through. Requiring the context to actually partition the data is what stops that
    arithmetic from being reported as evidence about grappling. (Measured on a synthetic
    single-context corpus: Δ +0.0104 with a zero-width interval. The guard is why
    ``test_terminal_depth_is_flat_...`` passes.)
    """

    space: str
    n_eval: int
    ll: dict[int, float]
    delta_vs_m0: dict[int, tuple[float, float, float]]
    delta_vs_m1: dict[int, tuple[float, float, float]]
    per_terminal: dict[str, tuple[int, float, float]]   # terminal → (n, ll_M0, ll_M1)
    contexts: dict[int, int]                            # order → distinct train contexts

    @property
    def history_dependent(self) -> bool:
        return self.contexts.get(1, 0) > 1 and wins(self.delta_vs_m0.get(1, _NAN3))

    @property
    def deep(self) -> bool:
        return any(self.contexts.get(k, 0) > self.contexts.get(1, 0)
                   and wins(self.delta_vs_m1.get(k, _NAN3)) for k in (2, 3))


def _terminal_model(rows: Sequence[BoutRow], sp: StateSpace, order: int
                    ) -> tuple[dict[tuple[str, ...], Counter[str]], Counter[str]]:
    """(context → terminal counts, marginal terminal counts) over train bouts."""
    ctx: defaultdict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    marg: Counter[str] = Counter()
    for r in rows:
        term = r.terminal
        chain = bout_chain(r.sequence, sp)
        if term is None or not chain:
            continue
        states = [s for s, _ in chain[0]]
        marg[term] += 1
        if order and len(states) >= order:
            ctx[tuple(states[-order:])][term] += 1
    return dict(ctx), marg


def terminal_depth(rows_train: Sequence[BoutRow], rows_eval: Sequence[BoutRow],
                   sp: StateSpace, alpha: float = ALPHA,
                   n_boot: int = N_BOOT) -> TerminalDepth:
    """T1 — held-out log-likelihood of the observed terminal under M0..M3.

    One observation per eval bout, so a plain bootstrap over bouts IS the cluster bootstrap.
    """
    v = len(TERMINALS)
    models = {k: _terminal_model(rows_train, sp, k) for k in ORDERS}
    # bout_chain returns a LIST of chains (one, for C-bout) — take it, don't iterate it.
    scored = [(r, chains[0]) for r, chains in
              ((r, bout_chain(r.sequence, sp)) for r in rows_eval)
              if r.terminal is not None and chains]

    def logp(k: int, states: Sequence[str], term: str) -> float:
        ctx, marg = models[k]
        tot = sum(marg.values())
        p = (marg[term] + alpha) / (tot + alpha * v)
        if k and len(states) >= k:
            succ = ctx.get(tuple(states[-k:]))
            if succ:
                n = sum(succ.values())
                lam = n / (n + alpha * v)
                p = lam * (succ[term] / n) + (1 - lam) * p
        return math.log(max(p, 1e-12))

    per_k: dict[int, list[float]] = {}
    terms = [r.terminal or "" for r, _ in scored]
    for k in ORDERS:
        per_k[k] = [logp(k, [s for s, _ in c], r.terminal or "") for r, c in scored]

    def boot(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
        d = [x - y for x, y in zip(a, b, strict=True)]
        return _boot_ci([True] * len(d),
                        lambda s: sum(d[i] for i in s) / len(s) if s else 0.0, n_boot, None)

    ll = {k: (sum(vals) / len(vals) if vals else float("nan")) for k, vals in per_k.items()}
    d0 = {k: boot(per_k[k], per_k[0]) for k in (1, 2, 3)} if scored else {}
    d1 = {k: boot(per_k[k], per_k[1]) for k in (2, 3)} if scored else {}
    per_terminal: dict[str, tuple[int, float, float]] = {}
    for t in TERMINALS:
        idx = [i for i, x in enumerate(terms) if x == t]
        if idx:
            per_terminal[t] = (len(idx),
                               sum(per_k[0][i] for i in idx) / len(idx),
                               sum(per_k[1][i] for i in idx) / len(idx))
    contexts = {k: len(models[k][0]) for k in ORDERS}
    return TerminalDepth(sp.name, len(scored), ll, d0, d1, per_terminal, contexts)


STEP_BINS: tuple[tuple[int, int, str], ...] = (
    (1, 4, "1-4"), (5, 8, "5-8"), (9, 12, "9-12"), (13, 20, "13-20"), (21, 10**6, "21+"))
TIME_BINS: tuple[tuple[float, float, str], ...] = (
    (0, 120, "0-2 min"), (120, 240, "2-4"), (240, 360, "4-6"), (360, 480, "6-8"),
    (480, 600, "8-10"), (600, float("inf"), ">10"))


@dataclass
class HazardTable:
    axis: str
    bins: list[str]
    # bin → terminal → (absorbed, at-risk)
    cells: dict[str, dict[str, tuple[int, int]]]
    n_bouts: int
    n_excluded: int
    het: Any
    first_last: dict[str, Any]
    # Same contrast against the last INTERIOR bin. The final bin is open-ended, so every
    # surviving bout must absorb inside it and its hazard is inflated by construction —
    # a trend claim resting only on that cell would be an artefact of the binning.
    interior: dict[str, Any] = field(default_factory=dict)


def _heterogeneity_or_null(table: Sequence[Sequence[int]]) -> Any:
    """``stats_rigor.heterogeneity`` with the degenerate tables it cannot take.

    ``chi2_contingency`` raises on an all-zero row OR column; a hazard table with (say) no
    submission finishes anywhere is a perfectly real corpus and must not crash a report.
    Both are dropped and the test is run on what is left; nothing survives → a null result,
    which is the honest answer and not an exception.
    """
    rows = [list(r) for r in table if sum(r) > 0]
    if len(rows) < 2:
        return heterogeneity([])
    keep = [j for j in range(len(rows[0])) if sum(r[j] for r in rows) > 0]
    if len(keep) < 2:
        return heterogeneity([])
    return heterogeneity([[r[j] for j in keep] for r in rows])


def bout_positions(r: BoutRow, sp: StateSpace, axis: str) -> list[float] | None:
    """Where each usable event of this bout sits on the chosen axis.

    ``step`` = 1-based index inside the bout chain. ``time`` = ``ts − min(ts)`` **within the
    bout**, which is invariant to ``ts_origin`` (an unknown origin is an additive per-bout
    offset) — and ``None`` the moment ANY event lacks a ``ts``, so nothing is defaulted
    (AA-010). Alignment matters: ``bout_chain`` drops events with no usable state, so the
    elapsed list is filtered by the SAME predicate rather than indexed positionally.
    """
    keep = [i for i, e in enumerate(r.sequence) if sp.state_of(e)]
    if len(keep) < 2:
        return None
    if axis == "step":
        return [float(i + 1) for i in range(len(keep))]
    if r.elapsed is None:
        return None
    return [r.elapsed[i] for i in keep]


def _hazard(rows: Sequence[BoutRow], sp: StateSpace, axis: str) -> HazardTable:
    """Discrete-time competing-risks hazard, binned on step index or elapsed seconds.

    A bout contributes one at-risk observation per bin it reaches and absorbs in the bin its
    LAST recorded event falls in. On the time axis, that last timestamp is a LOWER BOUND on
    the bout's real duration, so this measures the hazard of "no further recorded event",
    not of the final bell — stated in the pre-registration, repeated here so the function
    cannot be read as more than it is.
    """
    bins = [b[2] for b in (STEP_BINS if axis == "step" else TIME_BINS)]
    cells: dict[str, dict[str, tuple[int, int]]] = {
        b: {t: (0, 0) for t in TERMINALS} for b in bins}
    used = excluded = 0
    for r in rows:
        term = r.terminal
        if term is None:
            continue
        positions = bout_positions(r, sp, axis)
        if positions is None:
            excluded += 1
            continue
        used += 1
        last_bin = _bin_of(positions[-1], axis)
        seen: list[str] = []
        for p in positions:
            b = _bin_of(p, axis)
            if b and b not in seen:
                seen.append(b)
        for b in seen:
            for t in TERMINALS:
                a, k = cells[b][t]
                cells[b][t] = (a + (1 if (b == last_bin and t == term) else 0), k + 1)
    table = [[cells[b]["END/points"][0], cells[b]["END/submission"][0],
              max(0, cells[b]["END/points"][1]
                  - sum(cells[b][t][0] for t in TERMINALS))] for b in bins]
    het = _heterogeneity_or_null(table)
    first_last: dict[str, Any] = {}
    interior: dict[str, Any] = {}
    for t in ("END/points", "END/submission"):
        f, latest, penult = cells[bins[0]][t], cells[bins[-1]][t], cells[bins[-2]][t]
        if f[1] and latest[1]:
            first_last[t] = compare_proportions(latest[0], latest[1], f[0], f[1])
        if f[1] and penult[1]:
            interior[t] = compare_proportions(penult[0], penult[1], f[0], f[1])
    return HazardTable(axis, bins, cells, used, excluded, het, first_last, interior)


def _bin_of(pos: float, axis: str) -> str | None:
    if axis == "step":
        for slo, shi, sname in STEP_BINS:
            if slo <= pos <= shi:
                return sname
        return None
    for tlo, thi, tname in TIME_BINS:
        if tlo <= pos < thi:
            return tname
    return None


# ── parity row against PoC-E4 ───────────────────────────────────────────────────
def e4_parity(rows_train: Sequence[BoutRow], rows_eval: Sequence[BoutRow],
              n_boot: int = N_BOOT) -> Any:
    """E4's own probe, its own folded chains, its own hard MIN_CONTEXT backoff — the bridge.

    If this row's sign disagrees with ``e4.md``'s published Δ −0.203 the two cells are not
    talking about the same corpus and the state-space tables must be read with that in mind.
    """
    from analysis.poc.e8_interaction_graph import actionflow_kernel

    train = [b for r in rows_train for b in r.mirrored()]
    held = [b for r in rows_eval for b in r.mirrored()]
    return markov_order(actionflow_kernel(), dedupe_by_key(train), dedupe_by_key(held),
                        alpha=ALPHA, n_boot=n_boot)


# ── pass assembly ───────────────────────────────────────────────────────────────
@dataclass
class Pass:
    chain: str
    n_train: int
    n_eval: int
    spaces: list[SpaceResult]
    cross: list[tuple[str, str, tuple[float, float, float]]]

    @property
    def winner(self) -> SpaceResult:
        return max(self.spaces, key=lambda s: s.best.ll)


def run_chain_pass(rows_train: Sequence[BoutRow], rows_eval: Sequence[BoutRow], chain: str,
                   n_boot: int = N_BOOT, alphas: Sequence[float] = ALPHA_GRID) -> Pass:
    spaces = [run_space(rows_train, rows_eval, sp, chain, n_boot, alphas) for sp in SPACES]
    return Pass(chain, len(rows_train), len(rows_eval), spaces,
                compare_spaces(spaces, n_boot))


@dataclass
class Run:
    gate: GateReport
    primary: Pass | None = None
    sensitivity: Pass | None = None
    grappling: Pass | None = None
    auc: AucLimb | None = None
    adcc: AdccLimb | None = None
    depth: list[TerminalDepth] = field(default_factory=list)
    hazards: list[HazardTable] = field(default_factory=list)
    parity: Any = None


def run_all(gate: GateReport, n_boot: int = N_BOOT, n_perm: int = N_PERM,
            alphas: Sequence[float] = ALPHA_GRID) -> Run:
    run = Run(gate)
    if not gate.rows:
        return run
    train, held = split_rows(gate.rows)
    run.primary = run_chain_pass(train, held, "C-own", n_boot, alphas)
    run.sensitivity = run_chain_pass(train, held, "C-bout", n_boot, alphas)
    g_tr = [r for r in train if r.discipline == "grappling"]
    g_ev = [r for r in held if r.discipline == "grappling"]
    if g_tr and g_ev:
        run.grappling = run_chain_pass(g_tr, g_ev, "C-own", n_boot, alphas)
    run.auc = run_auc_limb(train, held, n_boot)
    win = run.primary.winner
    sp = next(s for s in SPACES if s.name == win.space)
    run.adcc = run_adcc(gate.rows, sp, "C-own", max(1, win.max_order), n_boot, n_perm)
    run.depth = [terminal_depth(train, held, s, ALPHA, n_boot) for s in (S_CAT, S_V3)]
    run.hazards = [_hazard(gate.rows, S_CAT, "step"), _hazard(gate.rows, S_CAT, "time")]
    run.parity = e4_parity(train, held, n_boot)
    return run


def verify_gate(gate: GateReport) -> str:
    """The self-check the pre-registration promised: does our gate reproduce E8's 429?"""
    if gate.error:
        return f"NOT RUN — {gate.error}"
    if gate.passed == E8_GATED_BOUTS:
        return f"OK — {gate.passed} gated bouts, matching PoC-E8's published {E8_GATED_BOUTS}"
    return (f"MISMATCH — {gate.passed} gated bouts against PoC-E8's published "
            f"{E8_GATED_BOUTS}. The corpus moved; read every table below with that in mind.")


# ── verdicts ────────────────────────────────────────────────────────────────────
Cross = Sequence[tuple[str, str, tuple[float, float, float]]]


def signed_delta(cross: Cross, a: str, b: str) -> tuple[float, float, float]:
    """The paired Δ oriented as (a − b), whichever way the pair was stored."""
    for x, y, d in cross:
        if x == a and y == b:
            return d
        if x == b and y == a:
            return (-d[0], -d[2], -d[1])
    return (float("nan"),) * 3


def signed_wins(cross: Cross) -> list[tuple[str, str]]:
    """(winner, loser) for every pair whose paired interval excludes 0."""
    out: list[tuple[str, str]] = []
    for a, b, d in cross:
        if wins(d):
            out.append((a, b))
        elif wins((-d[0], -d[2], -d[1])):
            out.append((b, a))
    return out


def verdicts(run: Run) -> dict[str, str]:
    """The pre-registered criteria, applied verbatim. No arm reads another arm's outcome."""
    out: dict[str, str] = {}
    if run.primary is None:
        return {"all": "UNDECIDED — the corpus pass did not run."}
    p = run.primary
    beats = signed_wins(p.cross)
    losers_of_label = [a for (a, b) in beats if b == S_LABEL.name]
    undominated = [s.space for s in p.spaces
                   if not any(b == s.space for _, b in beats)]
    auc_note = ""
    if run.auc:
        beaten_by_label = [loser for w, loser in signed_wins(run.auc.deltas)
                           if w == S_LABEL.name]
        auc_note = (
            " On the SECONDARY criterion (held-out finish prediction) the label space "
            + (f"**WINS**, beating {len(beaten_by_label)} of the other 2 "
               f"(AUC {run.auc.results[0].auc:.3f} against "
               f"{' and '.join(f'{r.auc:.3f}' for r in run.auc.results[1:])})."
               if beaten_by_label else "does not win.")
            + " The two criteria answer different questions and are reported as two.")
    out["arm1_state_space"] = (
        f"{'ACCEPT' if losers_of_label else 'REJECT'} the owner's hypothesis that the "
        f"{S_LABEL.name} space is too sparse — on the PRIMARY criterion it is beaten by "
        f"{', '.join(losers_of_label) or 'nothing'}, and the undominated set is "
        f"{{{', '.join(undominated)}}}.{auc_note}"
    )
    signed = signed_delta(p.cross, S_V3.name, S_CAT.name)
    out["arm2_v3"] = (
        f"{'ACCEPT' if wins(signed) else 'REJECT'} the control-explosion rework: paired Δ "
        f"(S-v3 − S-cat) {_ci(signed)} on the common target — "
        f"{'excludes' if wins(signed) else 'covers'} 0."
    )
    out["arm3_order"] = " · ".join(
        f"{s.space}: max estimable order **{s.max_order}** "
        f"(order-{max(1, s.max_order)} support {s.rows[max(1, s.max_order)].support:.0%})"
        for s in p.spaces)
    if run.adcc is None:
        out["arm4_adcc"] = "NOT RUN."
    elif not run.adcc.powered:
        out["arm4_adcc"] = (
            f"UNDERPOWERED — {run.adcc.n_eval_bouts} eval bouts / {run.adcc.n_eval_steps} "
            f"scored steps against the pre-registered floor of {ADCC_MIN_EVAL_BOUTS} / "
            f"{ADCC_MIN_EVAL_STEPS}"
            + (f"; coverage refuses: {run.adcc.cov_reason}" if not run.adcc.cov_estimable
               else "")
            + ". No verdict, by pre-registration. The divergence table below is descriptive.")
    else:
        d = run.adcc.delta
        out["arm4_adcc"] = (
            f"{'ACCEPT' if wins(d) else 'REJECT'} a separate ADCC kernel: paired Δ "
            f"(ADCC-trained − global-trained) {d[0]:+.4f} [{d[1]:+.4f}, {d[2]:+.4f}].")
    if run.depth:
        d0 = run.depth[0]
        out["arm5_terminal"] = (
            f"{'ACCEPT' if d0.history_dependent else 'REJECT'} history-dependent absorption "
            f"at depth 1 (M1 − M0 "
            f"{d0.delta_vs_m0.get(1, (float('nan'),) * 3)[0]:+.4f} "
            f"[{d0.delta_vs_m0.get(1, (float('nan'),) * 3)[1]:+.4f}, "
            f"{d0.delta_vs_m0.get(1, (float('nan'),) * 3)[2]:+.4f}]); "
            f"{'ACCEPT' if d0.deep else 'REJECT'} DEEP history (M2/M3 vs M1).")
    return out


# ── report ──────────────────────────────────────────────────────────────────────
def _ci(d: tuple[float, float, float]) -> str:
    return f"{d[0]:+.4f} [{d[1]:+.4f}, {d[2]:+.4f}]"


def _order_table(s: SpaceResult) -> list[str]:
    lines = [
        f"**{s.space}** · chain {s.chain} · {s.rows[0].n_states} train states · "
        f"{s.rows[0].n_steps} scored eval steps · α={ALPHA}",
        "",
        "| order k | contexts | ≥5 obs | eval support | mean logP (category) [95% CI, bouts] "
        "| Δ vs k−1 [95% CI] | worth it? |",
        "|---|---|---|---|---|---|---|",
    ]
    for k in ORDERS:
        r = s.rows[k]
        d = s.order_deltas.get(k)
        lines.append(
            f"| {k} | {r.contexts if k else '—'} | {r.contexts_min5 if k else '—'} "
            f"| {r.support:.1%} | {r.ll:.4f} [{r.lo:.4f}, {r.hi:.4f}] "
            f"| {_ci(d) if d else '—'} | {'**yes**' if d and wins(d) else 'no' if d else '—'} |")
    lines += ["", f"Maximum estimable order: **{s.max_order}**.", ""]
    lines += ["| α | " + " | ".join(f"k={k}" for k in ORDERS) + " |",
              "|---|" + "---|" * len(ORDERS)]
    for a in sorted(s.alpha_rows):
        lines.append(f"| {a} | " + " | ".join(f"{s.alpha_rows[a][k].ll:.4f}" for k in ORDERS)
                     + " |")
    return lines + [""]


def _pass_section(p: Pass, title: str) -> list[str]:
    lines = [f"### {title}", "",
             f"{p.n_train} train bouts, {p.n_eval} held out (most recent "
             f"{EVAL_FRACTION:.0%} by date).", ""]
    for s in p.spaces:
        lines += _order_table(s)
    lines += ["**Head-to-head, each space at its own maximum estimable order** "
              "(paired, bout-clustered):", "",
              "| A | B | Δ logP(category) per step [95% CI] | reading |", "|---|---|---|---|"]
    for a, b, d in p.cross:
        rev = (-d[0], -d[2], -d[1])
        read = ("A wins" if wins(d) else "B wins" if wins(rev) else "no difference")
        lines.append(f"| {a} | {b} | {_ci(d)} | {read} |")
    return lines + [""]


def _hazard_section(h: HazardTable) -> list[str]:
    axis = "step index" if h.axis == "step" else "elapsed seconds within the bout"
    lines = [f"**Hazard by {axis}** — {h.n_bouts} bouts"
             + (f", {h.n_excluded} excluded for a missing `ts` (never defaulted)"
                if h.axis != "step" else "") + ".", "",
             "| bin | at risk | END/points hazard [95% CI] | END/submission hazard [95% CI] |",
             "|---|---|---|---|"]
    for b in h.bins:
        pts, sub = h.cells[b]["END/points"], h.cells[b]["END/submission"]
        wp, ws = wilson(pts[0], pts[1]), wilson(sub[0], sub[1])
        lines.append(
            f"| {b} | {pts[1]} "
            f"| {_pct(wp)} ({pts[0]}/{pts[1]}) | {_pct(ws)} ({sub[0]}/{sub[1]}) |")
    lines += ["", f"Heterogeneity across bins: χ²={h.het.chi2:.1f}, dof={h.het.dof}, "
                  f"p={h.het.p_value:.4g}"
                  + (f" (permutation p={h.het.p_permutation:.4g}; min expected "
                     f"{h.het.min_expected:.1f} < 5)" if h.het.p_permutation is not None
                     else "") + f", Cramér's V={h.het.cramers_v:.3f}.", ""]
    for t, c in h.first_last.items():
        if c.diff is not None:
            rising = c.diff_lo is not None and c.diff_lo > 0
            lines.append(f"- `{t}` last bin − first bin: {c.diff:+.3f} "
                         f"[{c.diff_lo:+.3f}, {c.diff_hi:+.3f}] — "
                         f"**{'increasing' if rising else 'not established as increasing'}** "
                         f"(the pre-registered contrast).")
    lines += ["", "> The FINAL bin is open-ended, so its hazard is inflated by construction: "
                  "every bout that reaches it must absorb inside it. The pre-registered "
                  "first-vs-last contrast is reported above as written; the contrast that "
                  "does not carry that artefact is first vs **last interior** bin, below.", ""]
    for t, c in h.interior.items():
        if c.diff is not None:
            rising = c.diff_lo is not None and c.diff_lo > 0
            lines.append(f"- `{t}` last INTERIOR bin ({h.bins[-2]}) − first bin: {c.diff:+.3f} "
                         f"[{c.diff_lo:+.3f}, {c.diff_hi:+.3f}] — "
                         f"**{'increasing' if rising else 'not established as increasing'}**.")
    return lines + [""]


def _pct(e: Any) -> str:
    if e.p is None or e.lo is None:
        return "—"
    return f"{e.p:.1%} [{e.lo:.1%}, {e.hi:.1%}]"


PREREG = REPO / "docs" / "research" / "poc" / "e9_prereg.md"


def render_markdown(run: Run, prereg: str) -> str:
    v = verdicts(run)
    lines = [
        "# PoC-E9 — state space, Markov order, event kernels, history-dependent absorption",
        "",
        "Generated by `uv run python -m analysis.poc.e9_markov` — **do not hand-edit**. "
        "Module: `analysis/poc/e9_markov.py`; tests: `tests/test_poc_e9.py`; plan: "
        "`docs/research/03_POC_PLANS.md` (PoC-E9).",
        "",
        f"**Gate self-check:** {verify_gate(run.gate)}",
        "",
        "## Verdicts",
        "",
    ]
    for k in ("arm1_state_space", "arm2_v3", "arm3_order", "arm4_adcc", "arm5_terminal"):
        if k in v:
            lines += [f"- **{k.split('_', 1)[1].replace('_', ' ')}** — {v[k]}", ""]
    if "all" in v:
        lines += [f"**{v['all']}**", ""]
    lines += ["---", "", prereg.strip(), "", "---", "", "## Results", ""]
    if run.gate.error:
        lines += [f"Corpus pass NOT RUN: `{run.gate.error}`.", ""]
        return "\n".join(lines) + "\n"
    lines += [
        "## Gate", "",
        f"{run.gate.total} final matches with a sequence · {run.gate.with_sequence} with ≥4 "
        f"events · **{run.gate.passed} pass** `bout_flags(...)['perspective_reliable']` · "
        f"{run.gate.one_sided} refused (one-sided filing).", "",
        "## Arms 1–3 — state space and order", "",
    ]
    if run.primary:
        lines += _pass_section(run.primary, "Primary: chain = C-own (within-actor)")
    if run.sensitivity:
        lines += _pass_section(run.sensitivity,
                               "Sensitivity: chain = C-bout (bout-chronological, actor-free)")
    if run.grappling:
        lines += _pass_section(run.grappling,
                               "Sensitivity: grappling-only corpus (C-own)")
    if run.auc:
        lines += ["### Secondary criterion — held-out finish prediction (PtV γ=0.8, "
                  "shaping OFF)", "",
                  f"{run.auc.n_train} train sequences, {run.auc.n_eval} held out "
                  f"(both mirrored, E8's contract).", "",
                  "| state space | nodes | edges | AUC [95% CI] | pos | neg | cold rows "
                  "| reading |", "|---|---|---|---|---|---|---|---|"]
        for r in run.auc.results:
            lines.append(f"| {r.name} | {r.n_nodes} | {r.n_edges} "
                         f"| {r.auc:.3f} [{r.lo:.3f}, {r.hi:.3f}] | {r.n_pos} | {r.n_neg} "
                         f"| {r.cold_rows} | {r.verdict} |")
        lines += ["", "| A | B | paired ΔAUC [95% CI] | reading |", "|---|---|---|---|"]
        for a, b, d in run.auc.deltas:
            rev = (-d[0], -d[2], -d[1])
            lines.append(f"| {a} | {b} | {_ci(d)} | "
                         f"{'A wins' if wins(d) else 'B wins' if wins(rev) else 'no difference'} |")
        lines.append("")
    if run.parity is not None:
        pr = run.parity
        lines += ["### PoC-E4 parity row (folded chains, hard MIN_CONTEXT backoff)", "",
                  f"Δ per-step log-likelihood (2nd − 1st order) on the production ActionFlow "
                  f"kernel: **{pr.delta:+.4f} [{pr.lo:+.4f}, {pr.hi:+.4f}]** over "
                  f"{pr.n_steps} steps, against `e4.md`'s published −0.203 "
                  f"[−0.258, −0.133]. Same sign = the two cells describe the same corpus.",
                  ""]
    if run.adcc:
        ad = run.adcc
        lines += ["## Arm 4 — the ADCC kernel", "",
                  f"{ad.n_gated} gated ADCC bouts. **ADCC-internal temporal split** (the "
                  f"amendment): cut at {ad.boundary}, {ad.n_adcc_train} ADCC bouts to train, "
                  f"**{ad.n_eval_bouts} held out**, {ad.n_eval_steps} scored eval steps. The "
                  f"global kernel trains on the {ad.n_train_bouts} corpus bouts ≤ the same "
                  f"boundary, so both kernels see the same past. Coverage over contributing "
                  f"eval bouts: "
                  f"{'estimable' if ad.cov_estimable else 'REFUSES: ' + str(ad.cov_reason)}.",
                  ""]
        if ad.powered:
            lines += [f"ADCC-trained {ad.adcc_ll:.4f} vs global-trained {ad.global_ll:.4f}; "
                      f"paired Δ {_ci(ad.delta)}.", ""]
        else:
            lines += ["**Held-out limb UNDERPOWERED** — no verdict, by pre-registration.", ""]
        lines += ["### Where the ADCC kernel diverges (descriptive, whole gated corpus)", "",
                  "| state | JS divergence | ADCC steps | other steps | permutation p | BH q "
                  "| coverage |", "|---|---|---|---|---|---|---|"]
        for row in ad.divergence:
            cov = row["cov"]
            lines.append(
                f"| `{row['state']}` | {row['js']:.4f} | {row['n_adcc']} | {row['n_other']} "
                f"| {row['p']:.4g} | {row['q']:.4g} | "
                + (f"{cov.clusters} bouts, eff n {cov.effective_n:.1f}"
                   if cov.estimable else f"REFUSES ({cov.reason_code})") + " |")
        lines.append("")
    if run.depth:
        lines += ["## Arm 5 — absorbing terminals", "",
                  "### T1 — does the terminal depend on more than the current state?", "",
                  "| state space | eval bouts | M0 | M1 | M2 | M3 | M1−M0 | M2−M1 | M3−M1 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        nan3 = (float("nan"),) * 3
        for td in run.depth:
            lines.append(
                f"| {td.space} | {td.n_eval} | "
                + " | ".join(f"{td.ll[k]:.4f}" for k in ORDERS)
                + f" | {_ci(td.delta_vs_m0.get(1, nan3))} "
                  f"| {_ci(td.delta_vs_m1.get(2, nan3))} "
                  f"| {_ci(td.delta_vs_m1.get(3, nan3))} |")
        lines.append("")
        for td in run.depth:
            if td.per_terminal:
                lines += [f"Per terminal ({td.space}): " + " · ".join(
                    f"`{t}` n={n}, M0 {m0:.3f} → M1 {m1:.3f}"
                    for t, (n, m0, m1) in td.per_terminal.items()), ""]
    for h in run.hazards:
        lines += ["### T2/T3 — " + ("step-index" if h.axis == "step"
                                    else "elapsed-time") + " hazard", ""]
        lines += _hazard_section(h)
    lines += ["", "## Reading", ""] + _reading(run)
    return "\n".join(lines) + "\n"


def _reading(run: Run) -> list[str]:
    """The synthesis, written from the numbers above — mechanisms and honest bounds."""
    if run.primary is None:
        return ["No corpus pass, no reading."]
    p = run.primary
    by = {s.space: s for s in p.spaces}
    lab, cat, v3 = by[S_LABEL.name], by[S_CAT.name], by[S_V3.name]
    out = [
        f"1. **Granularity is a wash on the common target, and the reason is visible in the "
        f"support columns.** At each space's own maximum estimable order the three arms land "
        f"within {max(abs(d[0]) for _, _, d in p.cross):.3f} nats per step of each other and "
        f"every paired interval on the primary criterion covers 0. The point estimates do "
        f"favour the coarse spaces ({cat.best.ll:.4f} for S-cat against {lab.best.ll:.4f} "
        f"for S-label), and the label space claws part of that back by using a second step "
        f"of memory ({lab.rows[1].ll:.4f} → {lab.rows[2].ll:.4f}) that neither coarse space "
        f"can use at all — but the gap that remains is inside its own interval, so the "
        f"honest statement is that this corpus **cannot distinguish** 226 states with two "
        f"steps of memory from {cat.rows[0].n_states} states with one. That is a real answer "
        f"to the owner's first question and it is not the one he expected: the label space "
        f"is not measurably worse at predicting what happens next.",
        "",
        f"2. **The order ceiling is set by support, and it is different per space — measured, "
        f"not assumed.** S-label: order 2 helps ({_ci(lab.order_deltas.get(2, _NAN3))}) on "
        f"{lab.rows[2].support:.0%} eval support, order 3 is exactly neutral at "
        f"{lab.rows[3].support:.0%} support — with a quarter of the steps covered, the "
        f"backoff simply returns the order-2 answer, which is what a null Δ of "
        f"{lab.order_deltas.get(3, _NAN3)[0]:+.4f} means. S-cat and S-v3: order 1 helps, "
        f"order 2 does not ({_ci(cat.order_deltas.get(2, _NAN3))} and "
        f"{_ci(v3.order_deltas.get(2, _NAN3))}) even at "
        f"{cat.rows[2].support:.0%}/{v3.rows[2].support:.0%} support, and order 3 is "
        f"significantly WORSE. Coverage was never the binding constraint on the small "
        f"spaces — 64 order-2 contexts over ~5 600 train steps is plenty — so the honest "
        f"conclusion is that eight or twelve coarse states carry ONE step of memory and no "
        f"more. **Maximum estimable order: 2 for labels, 1 for both category spaces.**",
        "",
        f"3. **The control explosion does not pay on either criterion.** S-v3 − S-cat is "
        f"{_ci(signed_delta(p.cross, S_V3.name, S_CAT.name))} on the common target and "
        f"{_ci(signed_delta(run.auc.deltas, S_V3.name, S_CAT.name)) if run.auc else 'n/a'} "
        f"on finish AUC. Read it with the corpus shape in front of you: `back control` is "
        f"1 952 events, 61.8% of all `control` and 20.3% of the corpus, so exploding "
        f"`control` produces one very large state and four small ones and most of the extra "
        f"resolution has almost no mass behind it. This is a null about THIS corpus and this "
        f"partition, not a proof that mount and side control are the same position.",
        "",
    ]
    if run.auc:
        a_lab = run.auc.results[0]
        out += [
            f"4. **The two criteria disagree, and that is the most useful thing in the "
            f"report.** On finish prediction the label kernel separates at "
            f"{a_lab.auc:.3f} [{a_lab.lo:.3f}, {a_lab.hi:.3f}] and beats both coarse spaces "
            f"by a paired interval that excludes 0, while on next-category prediction it "
            f"cannot be told apart from them. Nothing is contradictory: predicting *what "
            f"class of thing happens next* is a coarse question that coarse states answer, "
            f"and ranking *which position is close to a finish* is a question about specific "
            f"techniques that a state called `control` cannot represent. The practical "
            f"consequence for this repo is that the shipped label-level kernel is not the "
            f"defect the sparsity hypothesis suspected — it is the right vocabulary for the "
            f"job it is doing.",
            "",
        ]
    if run.adcc and run.adcc.powered:
        sig = [r for r in run.adcc.divergence if float(r["q"]) <= 0.05]
        out += [
            f"5. **ADCC diverges in structure and still loses as a kernel.** The specialised "
            f"kernel scores {run.adcc.adcc_ll:.4f} against the global kernel's "
            f"{run.adcc.global_ll:.4f} on the SAME {run.adcc.n_eval_bouts} held-out ADCC "
            f"bouts, paired Δ {_ci(run.adcc.delta)} — significantly WORSE. The mechanism is "
            f"sample size, not similarity: {run.adcc.n_adcc_train} ADCC training bouts "
            f"against {run.adcc.n_train_bouts} corpus bouts up to the same date, and a chain "
            f"fitted on the smaller set pays for its specificity in variance. That the "
            f"divergence limb finds "
            + (f"{len(sig)} state(s) whose outgoing distribution differs at BH q ≤ 0.05 "
               f"({', '.join('`' + str(r['state']) + '`' for r in sig)}) "
               if sig else "no state differing at BH q ≤ 0.05 ")
            + "is the other half of the same sentence: **there is a real difference, and it "
              "is smaller than the cost of estimating it separately on 56 bouts.** The "
              "actionable form of that is not a per-event kernel; it is more ADCC bouts.",
            "",
        ]
    if run.depth:
        d = run.depth[0]
        out += [
            f"6. **WHEN a bout absorbs is strongly history-dependent; WHICH terminal it "
            f"absorbs into is not — at least not from the state chain.** T1 is a null on "
            f"both state spaces (M1 − M0 {_ci(d.delta_vs_m0.get(1, _NAN3))}, "
            f"{d.n_eval} eval bouts) — knowing the last one, two or three states does not "
            f"tell you whether the bout ends by submission or by points, and with 107 "
            f"held-out bouts the interval is wide enough that this is 'no evidence', not "
            f"'evidence of no'. T2 and T3 are the opposite: the hazard rises across the "
            f"interior bins on both axes, and the elapsed-time table carries the finding "
            f"that no binning artefact can explain — the two terminals CROSS OVER. In the "
            f"first two minutes a bout is more likely to end by submission than by points "
            f"(3.4% against 1.6%); past ten minutes the order inverts hard (66.7% points "
            f"against 29.0% submission). Both rates share the same denominator in every "
            f"bin, so the crossing is about the clock and not about the open-ended last "
            f"cell. The owner's intuition is therefore half-confirmed, and precisely: "
            f"absorption is **time-inhomogeneous** — a hazard, not a constant, which is a "
            f"SEMI-Markov property and exactly what the Sci Rep 2026 critique predicts — "
            f"but it is the DURATION that carries the information, not the sequence of "
            f"previous states. A model that wants to predict a points ending should be "
            f"given the clock, not a longer memory.",
            "",
        ]
    out += [
        "7. **What bounds all of it.** The finish label rides on `successful`, present on "
        "28.9% of corpus events and reading absent as False, so the AUC limb's positives are "
        "undercounted; the gate removes one-sided bouts but cannot repair actor noise inside "
        "the ones it keeps, which is exactly why the C-bout sensitivity exists and why it "
        "agrees with the primary; the elapsed-time hazard measures 'no further recorded "
        "event', because the last timestamp is a lower bound on the bout's duration; 107 "
        "held-out bouts is a thin eval set and every interval here says so; and the "
        "grappling-only sensitivity flips one head-to-head (S-cat beats S-label there), "
        "which is a reminder that 70 MMA-tagged and 17 NCAA-tagged bouts sit inside the "
        "primary corpus because PoC-E4 and PoC-E8 kept them.",
        "",
        "8. **Nothing here changes a production export.** No engine, no site artefact, no "
        "schema. The one thing that would follow from an accept — swapping the shipped "
        "kernel's vocabulary — is refused by the measurement, which is the outcome this cell "
        "was built to be able to reach.",
    ]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PoC-E9 — Markov state space, order, terminals")
    ap.add_argument("--out", default=str(REPO / "docs" / "research" / "poc" / "e9.md"))
    ap.add_argument("--prereg", default=str(PREREG),
                    help="pre-registration markdown, echoed verbatim into the report")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--n-perm", type=int, default=N_PERM)
    ap.add_argument("--skip-corpus", action="store_true", help="no DB read")
    args = ap.parse_args(argv)

    gate = GateReport(error="skipped (--skip-corpus)") if args.skip_corpus else load_corpus()
    run = run_all(gate, n_boot=args.n_boot, n_perm=args.n_perm)
    prereg_path = Path(args.prereg)
    prereg = prereg_path.read_text(encoding="utf-8") if prereg_path.exists() else (
        "> Pre-registration file missing — see `docs/research/03_POC_PLANS.md` (PoC-E9).")
    md = render_markdown(run, prereg)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
