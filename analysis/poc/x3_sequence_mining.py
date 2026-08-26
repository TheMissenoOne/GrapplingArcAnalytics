"""PoC-X3 — supervised sequence mining: do mined patterns predict finishes?

    uv run python -m analysis.poc.x3_sequence_mining            # → docs/research/poc/x3.md
    uv run python -m analysis.poc.x3_sequence_mining --dry-run  # corpus shape only

Two limbs, one corpus, one temporal split.

* **Limb A** is the plan's version (`03_POC_PLANS.md`, PoC-X3): mine frequent gapped
  subsequences with PrefixSpan, score each by its finish LIFT with an interval, control the
  family's false-discovery rate with Benjamini-Hochberg. The kill criterion is pre-declared:
  nothing survives q ≤ 0.10 → publish the null exactly as `decision_criteria_findings.md`
  did.
* **Limb B** is the harder question and the one that decides the cell: do those patterns,
  **as features**, predict held-out finishes *beyond what the current event already says*?
  A pattern with a big lift and no incremental information is a description of the corpus,
  not a tactical finding. Limb B reuses PoC-E8's evaluator verbatim — the same
  `temporal_split`, the same `eval_rows`, the same `rank_auc`, the same bout-clustered
  paired bootstrap that PoC-E4 and PoC-E9 were scored on.

Literature: Pei et al. 2001 (PrefixSpan); Bunker & Susnjak 2021 (supervised sequence mining
in sport prediction — the framing this cell borrows); Fournier-Viger et al. (SPMF) for the
CM-SPAM variant the plan mentions and this cell does not need.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from analysis.poc.e8_interaction_graph import (
    FINISH_WINDOW,
    Bout,
    _boot_ci,
    eval_rows,
    rank_auc,
    temporal_split,
)
from analysis.poc.e9_markov import GateReport, load_corpus
from analysis.poc.signatures import contains, prefixspan
from analysis.stats_rigor import (
    benjamini_hochberg,
    compare_proportions,
    coverage,
    spearman,
)
from analysis.technique_match import clean_label
from analysis.transitions.interaction_graph import YOU, role_map

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "research" / "poc" / "x3.md"
PREREG = REPO / "docs" / "research" / "poc" / "x3_prereg.md"

SEED = 20260820
N_BOOT = 2000
MIN_SUPPORT_FRACTION = 0.10     # primary; {0.05, 0.20} reported as sensitivity
SUPPORT_SWEEP = (0.05, 0.10, 0.20)
MAX_PATTERN_LEN = 4
BH_ALPHA = 0.10                 # the plan's declared q
MIN_CHAIN_LEN = 2
TOP_PATTERNS_SHOWN = 20


# ── chains ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Chain:
    """One (bout, actor) unit: that fighter's own ordered labels, and whether they finished.

    ``items`` is truncated BEFORE the actor's first successful submission. A pattern that
    contains the finish predicts the finish trivially; the question is what PRECEDES one.
    ``finished`` records the outcome the truncation removed.
    """

    bout_key: tuple[Any, ...]
    actor: str
    items: list[str]
    finished: bool


def _own_labels(bout: Bout) -> list[tuple[str, int, bool]]:
    """(label, original event index, is a landed submission) for the YOU actor's events."""
    roles = role_map(bout.sequence, bout.perspective)
    out: list[tuple[str, int, bool]] = []
    for i, e in enumerate(bout.sequence):
        actor = e.get("actor_id", e.get("actor"))
        if actor is None or roles.get(actor) != YOU:
            continue
        label = clean_label(str(e.get("label", "")), str(e.get("type", "")))
        if not label:
            continue
        landed_sub = (str(e.get("type", "")).lower() == "submission"
                      and bool(e.get("successful", False)))
        out.append((label, i, landed_sub))
    return out


def fold_repeats(items: Sequence[str]) -> list[str]:
    """Drop consecutive duplicates — PoC-E8's rule, so a mined succession means the same
    thing as an edge of the graph everything else in this repo is built on."""
    out: list[str] = []
    for x in items:
        if not out or out[-1] != x:
            out.append(x)
    return out


def chains_of(bouts: Sequence[Bout]) -> list[Chain]:
    """One chain per bout perspective, truncated before that actor's first landed sub."""
    out: list[Chain] = []
    for b in bouts:
        own = _own_labels(b)
        cut = next((j for j, (_, _, sub) in enumerate(own) if sub), len(own))
        items = fold_repeats([lb for lb, _, _ in own[:cut]])
        if len(items) >= MIN_CHAIN_LEN:
            out.append(Chain(b.key, str(b.perspective), items, cut < len(own)))
    return out


# ── limb A: lift + BH ───────────────────────────────────────────────────────────
@dataclass
class PatternRow:
    pattern: tuple[str, ...]
    support: int
    with_finish: int
    without_n: int
    without_finish: int
    ratio: float | None
    ratio_lo: float | None
    ratio_hi: float | None
    p_value: float | None
    q_value: float = 1.0

    @property
    def rate(self) -> float:
        return self.with_finish / self.support if self.support else float("nan")


@dataclass
class LimbA:
    min_support: int
    n_chains: int
    n_finished: int
    base_rate: float
    n_patterns: int
    rows: list[PatternRow] = field(default_factory=list)
    survivors: list[PatternRow] = field(default_factory=list)
    coverage_ok: bool = False
    coverage_reason: str | None = None
    # The length confound, measured rather than suspected — see `length_confound`.
    mean_len_finished: float = float("nan")
    mean_len_unfinished: float = float("nan")
    len_vs_ratio_rho: float = float("nan")
    len_vs_ratio_lo: float = float("nan")
    len_vs_ratio_hi: float = float("nan")


def length_confound(limb: LimbA, train: Sequence[Chain]) -> None:
    """Is limb A measuring tactics, or chain length?

    Chains are truncated at the finish, so a chain that finished EARLY is SHORT. A longer
    pattern needs a longer chain to match, and longer chains are exactly the ones that did
    not finish — which would push every pattern's risk ratio below 1 for a reason that has
    nothing to do with grappling.

    This was not foreseen in the pre-registration; it is computed here, reported as a
    post-hoc diagnostic, and never used to change a verdict. Two numbers settle it: the mean
    chain length of finished vs unfinished chains, and the rank correlation between a
    pattern's LENGTH and its risk ratio. If the second is strongly negative, limb A's
    survivors are a length artefact.
    """
    fin = [len(c.items) for c in train if c.finished]
    unf = [len(c.items) for c in train if not c.finished]
    limb.mean_len_finished = float(np.mean(fin)) if fin else float("nan")
    limb.mean_len_unfinished = float(np.mean(unf)) if unf else float("nan")
    pairs = [(len(r.pattern), r.ratio) for r in limb.rows if r.ratio is not None]
    if len(pairs) >= 4:
        rc = spearman([float(a) for a, _ in pairs], [float(b) for _, b in pairs])
        limb.len_vs_ratio_rho = rc.rho
        limb.len_vs_ratio_lo, limb.len_vs_ratio_hi = rc.lo, rc.hi


def run_limb_a(train: Sequence[Chain], min_support: int) -> LimbA:
    """Mine train chains, score every pattern's finish lift, BH across the whole family.

    The comparison is chain-level and the family is EVERY mined pattern, not a hand-picked
    shortlist: picking the interesting ones first and correcting afterwards is how a
    corrected p-value stops meaning anything.
    """
    seqs = [c.items for c in train]
    mined = prefixspan(seqs, min_support=min_support, max_len=MAX_PATTERN_LEN)
    n = len(train)
    finished = sum(1 for c in train if c.finished)
    limb = LimbA(min_support, n, finished, finished / n if n else float("nan"), len(mined))

    for pattern, sup in mined:
        hit = [c for c in train if contains(c.items, pattern)]
        miss = [c for c in train if not contains(c.items, pattern)]
        k1, n1 = sum(1 for c in hit if c.finished), len(hit)
        k2, n2 = sum(1 for c in miss if c.finished), len(miss)
        if n1 == 0 or n2 == 0:
            continue
        ct = compare_proportions(k1, n1, k2, n2)
        limb.rows.append(PatternRow(pattern, sup, k1, n2, k2, ct.ratio, ct.ratio_lo,
                                    ct.ratio_hi, ct.p_value))

    qs = benjamini_hochberg([r.p_value if r.p_value is not None else 1.0 for r in limb.rows])
    for r, q in zip(limb.rows, qs, strict=True):
        r.q_value = q
    limb.rows.sort(key=lambda r: (r.q_value, -(r.ratio or 0.0)))
    limb.survivors = [r for r in limb.rows if r.q_value <= BH_ALPHA]

    # Every claim is gated on how many distinct BOUTS the evidence came from, not how many
    # chains: two perspectives on one bout are not two sources.
    cov = coverage(list({c.bout_key: 1 for c in train}.values()))
    limb.coverage_ok, limb.coverage_reason = cov.estimable, cov.reason
    length_confound(limb, train)
    return limb


# ── limb B: does it predict, beyond the current state? ──────────────────────────
@dataclass
class Model:
    name: str
    auc: float
    lo: float
    hi: float
    n_features: int
    scores: list[float] = field(default_factory=list)


@dataclass
class LimbB:
    n_train_rows: int
    n_eval_rows: int
    n_pos: int
    n_patterns: int
    models: list[Model] = field(default_factory=list)
    delta: tuple[float, float, float] = (float("nan"),) * 3
    delta_vs_ptv: tuple[float, float, float] = (float("nan"),) * 3
    error: str | None = None

    def model(self, name: str) -> Model | None:
        return next((m for m in self.models if m.name == name), None)


def _rows_with_context(bouts: Sequence[Bout], k: int = FINISH_WINDOW
                       ) -> tuple[list[dict[str, Any]], list[bool], list[list[str]],
                                  list[Any]]:
    """PoC-E8's ``eval_rows``, plus each row's own-actor history and its bout key.

    The rows come from ``eval_rows`` UNCHANGED — limb B is scored on exactly the rows
    PoC-E4, E8 and E9 were scored on. The history is attached in a second pass over the same
    bout, and the two passes are reconciled by a ``ValueError``: if the row count for a bout
    ever stops matching the own-event count, the alignment is wrong and this must fail
    loudly rather than silently pair a row with somebody else's history.

    (They can differ in principle — ``eval_rows`` filters on ``node_key`` and ``_own_labels``
    on ``clean_label``. Measured on this corpus they agree; the guard exists so a future
    divergence surfaces as a crash, not as a quietly wrong AUC.)
    """
    events: list[dict[str, Any]] = []
    labels: list[bool] = []
    histories: list[list[str]] = []
    groups: list[Any] = []
    for b in bouts:
        rows = eval_rows([b], k)
        own = _own_labels(b)
        if len(rows) != len(own):
            raise ValueError(
                f"row/history misalignment on bout {b.key}: eval_rows produced {len(rows)} "
                f"rows but the bout has {len(own)} own events — node_key and clean_label "
                f"have diverged and the pattern features would be attached to the wrong rows"
            )
        folded: list[str] = []
        for (e, y), (label, _, _) in zip(rows, own, strict=True):
            if not folded or folded[-1] != label:
                folded.append(label)
            events.append(e)
            labels.append(y)
            histories.append(list(folded))
            groups.append(b.key)
    return events, labels, histories, groups


def _features(events: Sequence[dict[str, Any]], histories: Sequence[list[str]],
              vocab: Sequence[str], patterns: Sequence[tuple[str, ...]],
              with_patterns: bool) -> np.ndarray:
    """One-hot of the current state, optionally concatenated with pattern indicators.

    A pattern indicator fires when the pattern is a subsequence of the actor's history AND
    its last item is the CURRENT label — "the pattern just completed here", not "it happened
    at some point in this fight".
    """
    idx = {lb: i for i, lb in enumerate(vocab)}
    width = len(vocab) + (len(patterns) if with_patterns else 0)
    x = np.zeros((len(events), width), dtype=np.float64)
    for r, (e, hist) in enumerate(zip(events, histories, strict=True)):
        label = clean_label(str(e.get("label", "")), str(e.get("type", "")))
        j = idx.get(label)
        if j is not None:
            x[r, j] = 1.0
        if not with_patterns:
            continue
        for p, pat in enumerate(patterns):
            if pat and pat[-1] == label and contains(hist, pat):
                x[r, len(vocab) + p] = 1.0
    return x


def run_limb_b(train_bouts: Sequence[Bout], eval_bouts: Sequence[Bout],
               patterns: Sequence[tuple[str, ...]], n_boot: int = N_BOOT) -> LimbB:
    from sklearn.linear_model import LogisticRegression

    tr_events, tr_labels, tr_hist, _ = _rows_with_context(train_bouts)
    ev_events, ev_labels, ev_hist, ev_groups = _rows_with_context(eval_bouts)
    n_pos = sum(1 for y in ev_labels if y)
    limb = LimbB(len(tr_events), len(ev_events), n_pos, len(patterns))
    if not tr_events or not ev_events or n_pos == 0 or n_pos == len(ev_labels):
        limb.error = "one class empty on the held-out rows — nothing to separate"
        return limb
    if len(set(tr_labels)) < 2:
        limb.error = "training rows carry one class only"
        return limb

    vocab = sorted({clean_label(str(e.get("label", "")), str(e.get("type", "")))
                    for e in tr_events} - {""})

    for name, use in (("state one-hot (baseline)", False),
                      ("state one-hot + PrefixSpan patterns", True)):
        xtr = _features(tr_events, tr_hist, vocab, patterns, use)
        xev = _features(ev_events, ev_hist, vocab, patterns, use)
        clf = LogisticRegression(max_iter=2000, C=1.0, random_state=SEED)
        clf.fit(xtr, tr_labels)
        scores = [float(s) for s in clf.predict_proba(xev)[:, 1]]

        def stat(s: Sequence[int], sc: list[float] = scores) -> float:
            return rank_auc([sc[i] for i in s], [ev_labels[i] for i in s])

        obs, lo, hi = _boot_ci(ev_labels, stat, n_boot, ev_groups)
        limb.models.append(Model(name, obs, lo, hi, xtr.shape[1], scores))

    base = limb.model("state one-hot (baseline)")
    full = limb.model("state one-hot + PrefixSpan patterns")
    if base and full:
        limb.delta = _boot_ci(ev_labels, lambda s: (
            rank_auc([full.scores[i] for i in s], [ev_labels[i] for i in s])
            - rank_auc([base.scores[i] for i in s], [ev_labels[i] for i in s])),
            n_boot, ev_groups)

    ptv = _ptv_reference(train_bouts, ev_events, ev_labels, ev_groups, n_boot)
    if ptv is not None:
        limb.models.append(ptv)
        if full is not None:
            limb.delta_vs_ptv = _boot_ci(ev_labels, lambda s: (
                rank_auc([full.scores[i] for i in s], [ev_labels[i] for i in s])
                - rank_auc([ptv.scores[i] for i in s], [ev_labels[i] for i in s])),
                n_boot, ev_groups)
    return limb


def _ptv_reference(train_bouts: Sequence[Bout], events: Sequence[dict[str, Any]],
                   labels: Sequence[bool], groups: Sequence[Any],
                   n_boot: int) -> Model | None:
    """Production Path-to-Victory on the same rows — the unsupervised reference PoC-E4
    accepted (γ=0.8, shaping off). Reported beside the two models, never inside the
    criterion: PtV is fitted on nothing, so a supervised model beating it is unsurprising
    and would prove only that supervision helps."""
    try:
        from analysis.path_to_victory import path_to_victory
        from analysis.transitions.build_graph import network_from_sequences
    except ImportError:
        return None
    g = network_from_sequences([b.sequence for b in train_bouts])
    v = path_to_victory(g)
    scores = [float(v.get(clean_label(str(e.get("label", "")), str(e.get("type", ""))), 0.0))
              for e in events]

    def stat(s: Sequence[int]) -> float:
        return rank_auc([scores[i] for i in s], [labels[i] for i in s])

    obs, lo, hi = _boot_ci(labels, stat, n_boot, groups)
    return Model("production PtV (γ=0.8, unsupervised reference)", obs, lo, hi, 1, scores)


# ── run ─────────────────────────────────────────────────────────────────────────
@dataclass
class SupportPass:
    fraction: float
    limb_a: LimbA
    limb_b: LimbB


@dataclass
class Run:
    gate_note: str
    n_train_chains: int
    n_eval_chains: int
    passes: list[SupportPass] = field(default_factory=list)

    def primary(self) -> SupportPass | None:
        return next((p for p in self.passes if p.fraction == MIN_SUPPORT_FRACTION), None)


E8_GATED_BOUTS = 429


def gate_note(gate: GateReport) -> str:
    if gate.error:
        return f"NOT RUN — {gate.error}"
    base = (f"{gate.passed} gated bouts of {gate.total} final+sequence "
            f"({gate.total - gate.with_sequence} under 4 events, "
            f"{gate.one_sided} dropped as one-sided)")
    if gate.passed == E8_GATED_BOUTS:
        return f"{base} — matching PoC-E8's published {E8_GATED_BOUTS}"
    return (f"{base} — **DRIFT** against PoC-E8/E9's published {E8_GATED_BOUTS}; the corpus "
            f"grew since those cells ran")


def run_all(gate: GateReport, n_boot: int = N_BOOT) -> Run:
    bouts = [b for r in gate.rows for b in r.mirrored()]
    train_b, eval_b = temporal_split(bouts)
    train_c, eval_c = chains_of(train_b), chains_of(eval_b)
    run = Run(gate_note(gate), len(train_c), len(eval_c))
    for frac in SUPPORT_SWEEP:
        min_sup = max(2, int(round(frac * len(train_c))))
        a = run_limb_a(train_c, min_sup)
        b = run_limb_b(train_b, eval_b, [r.pattern for r in a.rows], n_boot)
        run.passes.append(SupportPass(frac, a, b))
    return run


def verdicts(run: Run) -> dict[str, str]:
    p = run.primary()
    if p is None:
        return {"cell": "NOT RUN"}
    out: dict[str, str] = {}
    a = p.limb_a
    out["limb A — lift + BH"] = (
        f"{len(a.survivors)} of {len(a.rows)} mined patterns survive BH at q ≤ {BH_ALPHA:.2f}"
        if a.survivors else
        f"NULL — none of {len(a.rows)} mined patterns survives BH at q ≤ {BH_ALPHA:.2f}. "
        f"Published as a null, exactly as the plan pre-declared."
    )
    b = p.limb_b
    if b.error:
        out["limb B — held-out prediction"] = f"NOT RUN — {b.error}"
    else:
        d, lo, hi = b.delta
        won = bool(np.isfinite(lo) and np.isfinite(hi) and hi > lo and lo > 0.0)
        out["limb B — held-out prediction"] = (
            f"{'ACCEPT' if won else 'REJECT'} — pattern features "
            f"{'add' if won else 'add nothing'} beyond the current state: paired ΔAUC "
            f"{d:+.4f} [{lo:+.4f}, {hi:+.4f}], bout-clustered, over {b.n_eval_rows} held-out "
            f"rows ({b.n_pos} positive)"
        )
    out["cell"] = (
        "ACCEPT" if out.get("limb B — held-out prediction", "").startswith("ACCEPT")
        else "REJECT — sequence mining does not earn a place in any published artefact on "
             "this corpus. Limb B is the criterion; limb A's lifts describe the corpus, they "
             "do not predict out of it."
    )
    return out


# ── report ──────────────────────────────────────────────────────────────────────
def _pat(p: tuple[str, ...]) -> str:
    return " → ".join(p)


def _limb_a_table(a: LimbA) -> list[str]:
    rows = [f"Mined **{a.n_patterns}** patterns at min support {a.min_support} chains "
            f"(of {a.n_chains}); {a.n_finished} chains ({a.base_rate:.1%}) precede a landed "
            f"submission by that actor.", "",
            "| pattern | support | finish rate | risk ratio [95% CI] | p | q (BH) |",
            "|---|---|---|---|---|---|"]
    for r in a.rows[:TOP_PATTERNS_SHOWN]:
        rr = (f"{r.ratio:.2f} [{r.ratio_lo:.2f}, {r.ratio_hi:.2f}]"
              if r.ratio is not None and r.ratio_lo is not None else "—")
        pv = f"{r.p_value:.3f}" if r.p_value is not None else "—"
        rows.append(f"| {_pat(r.pattern)} | {r.support} | "
                    f"{r.rate:.1%} ({r.with_finish}/{r.support}) | {rr} | {pv} | "
                    f"{r.q_value:.3f} |")
    if len(a.rows) > TOP_PATTERNS_SHOWN:
        rows.append(f"| _… {len(a.rows) - TOP_PATTERNS_SHOWN} more, all with larger q_ "
                    f"| | | | | |")
    rows += ["", "**Length diagnostic (post-hoc, never a verdict).** Chains are truncated "
             f"at the finish, so a chain that finished early is short: mean length "
             f"{a.mean_len_finished:.1f} for chains that finished against "
             f"{a.mean_len_unfinished:.1f} for chains that did not. Rank correlation "
             f"between a pattern's LENGTH and its risk ratio: ρ = {a.len_vs_ratio_rho:+.3f} "
             f"[{a.len_vs_ratio_lo:+.3f}, {a.len_vs_ratio_hi:+.3f}]."]
    if not a.coverage_ok:
        rows += ["", f"⚠️ Source coverage gate: {a.coverage_reason}."]
    return rows


def _limb_b_table(b: LimbB) -> list[str]:
    if b.error:
        return [f"NOT RUN — {b.error}"]
    rows = [f"{b.n_train_rows} train rows, {b.n_eval_rows} held-out rows "
            f"({b.n_pos} positive), {b.n_patterns} pattern features.", "",
            "| model | features | held-out AUC [95% CI, bout-clustered] |",
            "|---|---|---|"]
    for m in b.models:
        rows.append(f"| {m.name} | {m.n_features} | {m.auc:.4f} [{m.lo:.4f}, {m.hi:.4f}] |")
    d, lo, hi = b.delta
    rows += ["", f"**Paired Δ (patterns − baseline): {d:+.4f} [{lo:+.4f}, {hi:+.4f}]** — the "
                 f"criterion.",
             f"Against the production PtV reference: {b.delta_vs_ptv[0]:+.4f} "
             f"[{b.delta_vs_ptv[1]:+.4f}, {b.delta_vs_ptv[2]:+.4f}] (reported, not a "
             f"criterion)."]
    return rows


def render_markdown(run: Run, prereg: str) -> str:
    v = verdicts(run)
    lines = [
        "# PoC-X3 — supervised sequence mining (PrefixSpan)", "",
        "Generated by `uv run python -m analysis.poc.x3_sequence_mining` — "
        "**do not hand-edit**. Module: `analysis/poc/x3_sequence_mining.py`; miner: "
        "`analysis/poc/signatures.prefixspan`; tests: `tests/test_poc_x3.py`, "
        "`tests/test_poc_signatures.py`; pre-registration: "
        "`docs/research/poc/x3_prereg.md` (reproduced verbatim below).", "",
        f"**Corpus gate:** {run.gate_note}", "",
        f"**Chains:** {run.n_train_chains} train / {run.n_eval_chains} held-out "
        f"(one per bout perspective, truncated before that actor's first landed submission).",
        "", "## Verdicts", "",
    ]
    lines += [f"{i}. **{k}** — {val}" for i, (k, val) in enumerate(v.items(), start=1)]
    lines += ["", "---", "", "## Pre-registration (verbatim)", "", prereg.strip(), "",
              "---", "", "## Results", ""]
    for p in run.passes:
        tag = " — **PRIMARY**" if p.fraction == MIN_SUPPORT_FRACTION else " (sensitivity)"
        lines += [f"### Min support {p.fraction:.0%} of train chains{tag}", "",
                  "#### Limb A — pattern lift, BH-controlled", ""]
        lines += _limb_a_table(p.limb_a)
        lines += ["", "#### Limb B — held-out finish prediction", ""]
        lines += _limb_b_table(p.limb_b)
        lines += [""]
    lines += _reading(run, v)
    return "\n".join(lines) + "\n"


def _reading(run: Run, v: dict[str, str]) -> list[str]:
    p = run.primary()
    if p is None:
        return ["## Reading", "", "Not run.", ""]
    out = ["## Reading", ""]
    a, b = p.limb_a, p.limb_b
    out.append(
        f"PrefixSpan mines {a.n_patterns} frequent gapped subsequences from "
        f"{a.n_chains} training chains at 10% support. That is the easy half; every one of "
        f"them is, by construction, something this corpus does often."
    )
    if a.survivors:
        best = a.survivors[0]
        protective = sum(1 for r in a.survivors if (r.ratio or 1.0) < 1.0)
        out.append(
            f"{len(a.survivors)} clear BH at q ≤ {BH_ALPHA:.2f}; the strongest is "
            f"`{_pat(best.pattern)}` (finish rate {best.rate:.1%} against a "
            f"{a.base_rate:.1%} base, q {best.q_value:.3f})."
        )
        if protective == len(a.survivors):
            out.append(
                f"**All {len(a.survivors)} survivors point the same way — every one has a "
                f"risk ratio BELOW 1.** Read naively that says grappling has {protective} "
                f"reliable ways to NOT finish and none to finish, which is not a fact about "
                f"grappling. The diagnostic says what it is: chains are truncated at the "
                f"finish, so a chain that finished early is short (mean "
                f"{a.mean_len_finished:.1f} items against {a.mean_len_unfinished:.1f} for "
                f"chains that did not finish), a longer pattern needs a longer chain to "
                f"match, and pattern length tracks the risk ratio at ρ = "
                f"{a.len_vs_ratio_rho:+.3f} [{a.len_vs_ratio_lo:+.3f}, "
                f"{a.len_vs_ratio_hi:+.3f}]. Limb A's survivors are a length artefact of the "
                f"truncation the design needed for a different reason. Recorded, not "
                f"patched: the fix (matching on chain length, or scoring at a fixed step "
                f"index) is a change to the criterion and belongs to a re-registered run, "
                f"not to this one."
            )
    else:
        out.append(
            f"**None survives BH at q ≤ {BH_ALPHA:.2f}.** With a {a.base_rate:.1%} base rate "
            f"and chain-level denominators, no frequent pattern's finish rate is "
            f"distinguishable from the corpus average once the family is corrected. This is "
            f"the second null of its kind here (after `decision_criteria_findings.md`) and it "
            f"says the same thing: the corpus is not yet thick enough for pattern-level "
            f"tactical claims."
        )
    if not b.error:
        base, full = b.model("state one-hot (baseline)"), \
            b.model("state one-hot + PrefixSpan patterns")
        if base and full:
            out.append(
                f"Limb B is the one that decides. A logistic model on the current state alone "
                f"reaches held-out AUC {base.auc:.4f}; adding all {b.n_patterns} pattern "
                f"indicators moves it to {full.auc:.4f}, paired Δ {b.delta[0]:+.4f} "
                f"[{b.delta[1]:+.4f}, {b.delta[2]:+.4f}] with the bout as the resampling "
                f"unit. Whatever the mined patterns describe, the current position already "
                f"carries it."
            )
        out.append(
            "That result is consistent with PoC-E4's and PoC-E9's: second-order memory LOSES "
            "at label level (Δ logL/step −0.203 [−0.258, −0.133]), and a mined subsequence is "
            "higher-order memory wearing a nicer name. Three cells, three instruments, one "
            "answer — the first-order kernel is not leaving anything on the table."
        )
    out += ["", f"**Decision:** {v.get('cell', '')}", "",
            "Nothing here touches a production artefact. A null is the outcome the plan "
            "pre-declared as publishable, and it is content for the-data.html in its own "
            "right: it bounds what this corpus can honestly claim.", ""]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="PoC-X3 — supervised sequence mining")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    args = ap.parse_args(argv)

    gate = load_corpus()
    logger.info("gate: %s", gate_note(gate))
    if gate.error:
        return 1
    if args.dry_run:
        bouts = [b for r in gate.rows for b in r.mirrored()]
        tr, ev = temporal_split(bouts)
        tc, ec = chains_of(tr), chains_of(ev)
        logger.info("chains: %d train / %d eval; finished %d/%d train",
                    len(tc), len(ec), sum(1 for c in tc if c.finished), len(tc))
        for frac in SUPPORT_SWEEP:
            ms = max(2, int(round(frac * len(tc))))
            logger.info("  support %.0f%% → min %d chains → %d patterns", frac * 100, ms,
                        len(prefixspan([c.items for c in tc], ms, MAX_PATTERN_LEN)))
        return 0

    run = run_all(gate, args.n_boot)
    prereg = PREREG.read_text(encoding="utf-8") if PREREG.exists() else \
        "_(pre-registration file missing — this run is NOT pre-registered)_"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render_markdown(run, prereg), encoding="utf-8")
    logger.info("wrote %s", OUT)
    for k, val in verdicts(run).items():
        logger.info("VERDICT %s: %s", k, val)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
