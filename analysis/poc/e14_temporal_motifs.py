"""PoC-E14 — δ-temporal motifs: does TIMING carry information beyond ORDER?

    uv run python -m analysis.poc.e14_temporal_motifs            # → docs/research/poc/e14.md
    uv run python -m analysis.poc.e14_temporal_motifs --dry-run  # ts slice + window sizing

Every graph in this repository is built from event ORDER. `ts` is on 94.7% of events and is
read by exactly one thing (`e9`'s elapsed-time hazard). This cell asks whether that is a
waste: do time-respecting motif counts — Paranjape, Benson & Leskovec's δ-temporal motifs —
predict held-out finishes better than the SAME motif counter run over an index window?

The design is one paired difference and nothing else. Same edges, same motif alphabet, same
counter, same rows, same model, same bootstrap. The only thing that changes is whether the
window is measured in seconds or in events. If the two tie, timing carries nothing beyond
order on this corpus — which would retroactively justify every ts-free structure here, and
is a result worth having either way.

**AA-010 is absolute.** A missing `ts` is never defaulted. Only bouts carrying `ts` on
EVERY event enter this cell, the excluded ones are counted in the report, and every time is
a WITHIN-BOUT difference (`ts − min(ts)`), which is invariant to the unknown `ts_origin`.

Literature: Paranjape, Benson & Leskovec (2017), *Motifs in Temporal Networks*, WSDM '17
601–610, arXiv:1612.09259. Harness: PoC-E8's `temporal_split` / `eval_rows` / `rank_auc` /
bout-clustered paired bootstrap, the same instrument PoC-E4, E9 and X3 were scored on.
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter
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
from analysis.poc.e9_markov import BoutRow, GateReport, load_corpus
from analysis.poc.signatures import TEdge, counter_vector, index_motif_counts, motif_counts
from analysis.poc.x3_sequence_mining import _own_labels
from analysis.technique_match import clean_label

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "research" / "poc" / "e14.md"
PREREG = REPO / "docs" / "research" / "poc" / "e14_prereg.md"

SEED = 20260820
N_BOOT = 2000
# δ chosen from the corpus's inter-edge-gap marginal, measured read-only BEFORE any arm ran:
# own-actor edges are 24 s apart at the median and 135 s at p90, and the share of positions
# whose δ window holds the 3 edges a motif needs is 24% at 30 s, 42% at 60 s, 58% at 120 s.
# 120 s is the smallest window on this grid where a majority of rows can carry a motif at all.
DELTA_PRIMARY = 120.0           # seconds; {60, 240} reported as sensitivity
DELTA_SWEEP = (60.0, 120.0, 240.0)
MOTIF_K = 3                     # Paranjape's 3-edge motifs
MOTIF_MAX_NODES = 3
MIN_MOTIF_SUPPORT = 20          # a motif id must appear in ≥ this many TRAIN rows to be a feature

# Power gate, pre-registered. Below either floor the cell reports UNDERPOWERED and no
# verdict — an interval computed on a handful of motif-bearing rows would be measuring the
# bootstrap, not the corpus.
MIN_EVAL_ROWS_WITH_MOTIF = 200
MIN_MOTIF_IDS = 20


# ── temporal edges ──────────────────────────────────────────────────────────────
def own_temporal_edges(bout: Bout, elapsed: Sequence[float]) -> list[TEdge]:
    """The YOU actor's own within-actor successions, as timestamped directed edges.

    Same edge definition as ``transitions/build_graph.network_from_sequences`` (own next
    action, self-loops refused), with the target event's WITHIN-BOUT elapsed time attached.
    That is what makes this a temporal network in Paranjape's sense rather than a graph with
    a clock bolted on.
    """
    own = _own_labels(bout)
    out: list[TEdge] = []
    for (a, _ia, _), (b, ib, _) in zip(own, own[1:], strict=False):
        if a != b:
            out.append(TEdge(a, b, float(elapsed[ib]) if ib < len(elapsed) else float(ib)))
    return out


def _window(edges: Sequence[TEdge], j: int, delta: float) -> list[TEdge]:
    """Edges up to and including ``j`` whose time is within ``delta`` of edge ``j``."""
    t = edges[j].t
    start = j
    while start > 0 and t - edges[start - 1].t <= delta:
        start -= 1
    return list(edges[start:j + 1])


def motifs_ending_at(edges: Sequence[TEdge], j: int, delta: float,
                     by_index: bool) -> Counter[str]:
    """Motifs in the window ending at edge ``j`` that INVOLVE edge ``j``.

    Computed as ``count(window) − count(window minus its last edge)``. Both terms enumerate
    ordered k-subsets, and every motif not involving the last edge appears identically in
    both, so the difference is exactly the motifs that complete here. Two calls to the
    shared counter rather than a second, subtly different walk of the same recursion.
    """
    if j < MOTIF_K - 1:
        return Counter()
    win = (list(edges[max(0, j - int(delta) + 1):j + 1]) if by_index
           else _window(edges, j, delta))
    if len(win) < MOTIF_K:
        return Counter()
    count = index_motif_counts if by_index else motif_counts
    span: Any = int(delta) if by_index else delta
    full = count(win, span, MOTIF_K, MOTIF_MAX_NODES)
    full.subtract(count(win[:-1], span, MOTIF_K, MOTIF_MAX_NODES))
    return Counter({k: v for k, v in full.items() if v > 0})


def mean_window_edges(bouts: Sequence[tuple[Bout, list[float]]], delta: float) -> int:
    """How many edges a ``delta``-second window holds on average, on these bouts.

    This is what the index-window control is set to, and it is MEASURED on TRAIN rather than
    picked: an index window of a different width would be comparing widths, not comparing
    seconds against events.

    Matching on the MEAN rather than the median is deliberate — the window-size distribution
    is heavily right-skewed (median 3 edges, max 20 at δ=120 s), and matching the median
    would give the index arm a systematically narrower window than the δ arm actually uses.

    The match is still not perfect and the residual runs AGAINST the hypothesis: a
    fixed-width index window covers every position with enough history, while the δ window
    covers only the positions whose events happened close enough together. The index arm
    therefore gets MORE chances to carry a motif, which makes a δ win harder, not easier.
    Both arms' motif coverage is printed so the asymmetry is visible rather than argued.
    """
    sizes: list[int] = []
    for b, elapsed in bouts:
        edges = own_temporal_edges(b, elapsed)
        sizes.extend(len(_window(edges, j, delta)) for j in range(len(edges)))
    if not sizes:
        return MOTIF_K
    return max(MOTIF_K, int(round(float(np.mean(sizes)))))


# ── rows ────────────────────────────────────────────────────────────────────────
@dataclass
class Rows:
    events: list[dict[str, Any]] = field(default_factory=list)
    labels: list[bool] = field(default_factory=list)
    groups: list[Any] = field(default_factory=list)
    delta_motifs: list[Counter[str]] = field(default_factory=list)
    index_motifs: list[Counter[str]] = field(default_factory=list)


def build_rows(bouts: Sequence[tuple[Bout, list[float]]], delta: float,
               span: int, k: int = FINISH_WINDOW) -> Rows:
    """PoC-E8's ``eval_rows`` plus, per row, the δ-window and index-window motif counts.

    The rows come from ``eval_rows`` unchanged. The alignment between a row and its own
    event index is checked, not assumed — the same guard PoC-X3 carries, for the same
    reason.
    """
    out = Rows()
    for b, elapsed in bouts:
        rows = eval_rows([b], k)
        own = _own_labels(b)
        if len(rows) != len(own):
            raise ValueError(
                f"row/event misalignment on bout {b.key}: {len(rows)} rows, {len(own)} own "
                f"events — node_key and clean_label have diverged"
            )
        edges = own_temporal_edges(b, elapsed)
        # own event p (p ≥ 1) closes edge index p−1 when it is not a repeat; map each row to
        # the last edge that exists at or before it.
        edge_at: list[int] = []
        cursor = -1
        for p, (label, _, _) in enumerate(own):
            if p > 0 and own[p - 1][0] != label:
                cursor += 1
            edge_at.append(cursor)
        for (e, y), j in zip(rows, edge_at, strict=True):
            out.events.append(e)
            out.labels.append(y)
            out.groups.append(b.key)
            if j < 0:
                out.delta_motifs.append(Counter())
                out.index_motifs.append(Counter())
                continue
            out.delta_motifs.append(motifs_ending_at(edges, j, delta, by_index=False))
            out.index_motifs.append(motifs_ending_at(edges, j, float(span), by_index=True))
    return out


def motif_vocabulary(rows: Rows) -> list[str]:
    """Motif ids seen often enough in TRAIN to be a feature — the SAME alphabet for both
    windows, so the two arms differ in their counts and never in their feature space."""
    seen: Counter[str] = Counter()
    for a, b in zip(rows.delta_motifs, rows.index_motifs, strict=True):
        seen.update(set(a) | set(b))
    return sorted(k for k, v in seen.items() if v >= MIN_MOTIF_SUPPORT)


def features(rows: Rows, vocab: Sequence[str], motifs: Sequence[str] | None,
             which: str) -> np.ndarray:
    """State one-hot, optionally concatenated with one motif-count block."""
    idx = {lb: i for i, lb in enumerate(vocab)}
    extra = len(motifs) if motifs else 0
    x = np.zeros((len(rows.events), len(vocab) + extra), dtype=np.float64)
    counters = rows.delta_motifs if which == "delta" else rows.index_motifs
    for r, e in enumerate(rows.events):
        j = idx.get(clean_label(str(e.get("label", "")), str(e.get("type", ""))))
        if j is not None:
            x[r, j] = 1.0
        if motifs:
            x[r, len(vocab):] = counter_vector(counters[r], motifs)
    return x


# ── models ──────────────────────────────────────────────────────────────────────
@dataclass
class Model:
    name: str
    auc: float
    lo: float
    hi: float
    n_features: int
    scores: list[float] = field(default_factory=list)


@dataclass
class Pass:
    delta: float
    span: int
    n_train_rows: int
    n_eval_rows: int
    n_pos: int
    n_motifs: int
    # Motif COVERAGE per arm: how many held-out rows carry at least one motif. The δ arm's
    # coverage is what the power gate reads, and the gap between the two is the residual
    # asymmetry `mean_window_edges` documents.
    cover_delta: int = 0
    cover_index: int = 0
    models: list[Model] = field(default_factory=list)
    delta_vs_index: tuple[float, float, float] = (float("nan"),) * 3
    delta_vs_state: tuple[float, float, float] = (float("nan"),) * 3
    error: str | None = None
    underpowered: str | None = None

    def model(self, name: str) -> Model | None:
        return next((m for m in self.models if m.name == name), None)


ARM_STATE = "state one-hot (baseline)"
ARM_DELTA = "state + δ-temporal motifs (seconds)"
ARM_INDEX = "state + index-window motifs (order only)"


def run_pass(train: Sequence[tuple[Bout, list[float]]],
             held: Sequence[tuple[Bout, list[float]]],
             delta: float, n_boot: int = N_BOOT) -> Pass:
    from sklearn.linear_model import LogisticRegression

    span = mean_window_edges(train, delta)
    tr = build_rows(train, delta, span)
    ev = build_rows(held, delta, span)
    n_pos = sum(1 for y in ev.labels if y)
    motifs = motif_vocabulary(tr)
    p = Pass(delta, span, len(tr.events), len(ev.events), n_pos, len(motifs),
             cover_delta=sum(1 for c in ev.delta_motifs if c),
             cover_index=sum(1 for c in ev.index_motifs if c))
    if p.cover_delta < MIN_EVAL_ROWS_WITH_MOTIF or len(motifs) < MIN_MOTIF_IDS:
        p.underpowered = (
            f"{p.cover_delta} held-out rows carry a δ-motif (floor "
            f"{MIN_EVAL_ROWS_WITH_MOTIF}) and {len(motifs)} motif ids clear the support "
            f"floor (floor {MIN_MOTIF_IDS}) — below the pre-registered power gate, so this "
            f"pass reports counts and NO verdict"
        )
    if not tr.events or not ev.events or n_pos in (0, len(ev.labels)):
        p.error = "one class empty on the held-out rows — nothing to separate"
        return p
    if len(set(tr.labels)) < 2:
        p.error = "training rows carry one class only"
        return p

    vocab = sorted({clean_label(str(e.get("label", "")), str(e.get("type", "")))
                    for e in tr.events} - {""})
    for name, mots, which in ((ARM_STATE, None, "delta"),
                              (ARM_DELTA, motifs, "delta"),
                              (ARM_INDEX, motifs, "index")):
        clf = LogisticRegression(max_iter=2000, C=1.0, random_state=SEED)
        clf.fit(features(tr, vocab, mots, which), tr.labels)
        scores = [float(s) for s in
                  clf.predict_proba(features(ev, vocab, mots, which))[:, 1]]

        def stat(s: Sequence[int], sc: list[float] = scores) -> float:
            return rank_auc([sc[i] for i in s], [ev.labels[i] for i in s])

        obs, lo, hi = _boot_ci(ev.labels, stat, n_boot, ev.groups)
        p.models.append(Model(name, obs, lo, hi,
                              len(vocab) + (len(mots) if mots else 0), scores))

    def paired(a: str, b: str) -> tuple[float, float, float]:
        ma, mb = p.model(a), p.model(b)
        if ma is None or mb is None:
            return (float("nan"),) * 3
        return _boot_ci(ev.labels, lambda s: (
            rank_auc([ma.scores[i] for i in s], [ev.labels[i] for i in s])
            - rank_auc([mb.scores[i] for i in s], [ev.labels[i] for i in s])),
            n_boot, ev.groups)

    p.delta_vs_index = paired(ARM_DELTA, ARM_INDEX)
    p.delta_vs_state = paired(ARM_DELTA, ARM_STATE)
    return p


def wins(d: tuple[float, float, float]) -> bool:
    """PoC-E9's rule: interval strictly above 0, with a non-degenerate width."""
    _, lo, hi = d
    return bool(np.isfinite(lo) and np.isfinite(hi) and hi > lo and lo > 0.0)


# ── run ─────────────────────────────────────────────────────────────────────────
@dataclass
class Run:
    gate_note: str
    ts_note: str
    n_train: int
    n_eval: int
    passes: list[Pass] = field(default_factory=list)

    def primary(self) -> Pass | None:
        return next((p for p in self.passes if p.delta == DELTA_PRIMARY), None)


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


def ts_slice(rows: Sequence[BoutRow]) -> tuple[list[tuple[Bout, list[float]]], str]:
    """Bouts carrying ``ts`` on EVERY event, mirrored per perspective. AA-010: no default.

    ``BoutRow.elapsed`` is ``None`` the moment one event lacks a timestamp, and such a bout
    is DROPPED, never repaired. The count of what was dropped is part of the report — a
    usable slice quoted without its denominator is the failure this rule exists to prevent.
    """
    usable: list[tuple[Bout, list[float]]] = []
    for r in rows:
        if r.elapsed is None:
            continue
        for b in r.mirrored():
            usable.append((b, list(r.elapsed)))
    kept = sum(1 for r in rows if r.elapsed is not None)
    note = (f"{kept} of {len(rows)} gated bouts carry `ts` on every event "
            f"({kept / max(len(rows), 1):.1%}); the other {len(rows) - kept} are dropped, "
            f"never defaulted (AA-010). Mirrored → {len(usable)} bout perspectives.")
    return usable, note


def run_all(gate: GateReport, n_boot: int = N_BOOT) -> Run:
    usable, note = ts_slice(gate.rows)
    # `Bout` is frozen but carries a list, so it is unhashable; identity is the key.
    # `temporal_split` filters the list it is given and returns the very same objects.
    by_key = {id(b): el for b, el in usable}
    train_b, eval_b = temporal_split([b for b, _ in usable])
    train = [(b, by_key[id(b)]) for b in train_b]
    held = [(b, by_key[id(b)]) for b in eval_b]
    run = Run(gate_note(gate), note, len(train), len(held))
    for delta in DELTA_SWEEP:
        run.passes.append(run_pass(train, held, delta, n_boot))
    return run


def verdicts(run: Run) -> dict[str, str]:
    p = run.primary()
    if p is None or p.error:
        return {"cell": f"NOT RUN — {p.error if p else 'no pass'}"}
    if p.underpowered:
        return {"power gate": f"UNDERPOWERED — {p.underpowered}",
                "cell": "NO VERDICT — the pre-registered power gate refuses one"}
    out: dict[str, str] = {}
    d, lo, hi = p.delta_vs_index
    won = wins(p.delta_vs_index)
    out["timing beyond order"] = (
        f"{'ACCEPT' if won else 'REJECT'} — δ-temporal motifs "
        f"{'beat' if won else 'do not beat'} the same motifs counted over an index window "
        f"matched on mean width ({p.span} edges): paired ΔAUC {d:+.4f} [{lo:+.4f}, "
        f"{hi:+.4f}], bout-clustered, {p.n_eval_rows} held-out rows ({p.n_pos} positive)"
    )
    ds, ls, hs = p.delta_vs_state
    out["motifs at all"] = (
        f"{'ACCEPT' if wins(p.delta_vs_state) else 'REJECT'} — the motif family "
        f"{'adds' if wins(p.delta_vs_state) else 'adds nothing'} beyond the current state: "
        f"paired ΔAUC {ds:+.4f} [{ls:+.4f}, {hs:+.4f}] over {p.n_motifs} motif features"
    )
    out["cell"] = ("ACCEPT" if won else
                   "REJECT — on this corpus the clock carries no information the event "
                   "order does not already carry. Every ts-free structure in this "
                   "repository is, by this measurement, not leaving anything on the table.")
    return out


# ── report ──────────────────────────────────────────────────────────────────────
def _ci(d: tuple[float, float, float]) -> str:
    o, lo, hi = d
    return "—" if not np.isfinite(o) else f"{o:+.4f} [{lo:+.4f}, {hi:+.4f}]"


def _pass_section(p: Pass, primary: bool) -> list[str]:
    tag = " — **PRIMARY**" if primary else " (sensitivity)"
    out = [f"### δ = {p.delta:.0f} s{tag}", ""]
    if p.error:
        return [*out, f"NOT RUN — {p.error}", ""]
    if p.underpowered:
        out += [f"⚠️ **UNDERPOWERED** — {p.underpowered}.", ""]
    out += [f"Index-window control width, measured on train: **{p.span} edges** (the mean "
            f"number of own-actor edges inside a {p.delta:.0f} s window). "
            f"{p.n_motifs} motif ids clear the ≥{MIN_MOTIF_SUPPORT}-row support floor; both "
            f"arms use that same alphabet.", "",
            f"{p.n_train_rows} train rows, {p.n_eval_rows} held-out rows ({p.n_pos} "
            f"positive). Motif coverage on the held-out rows: **{p.cover_delta} δ-window** "
            f"({p.cover_delta / max(p.n_eval_rows, 1):.0%}) against **{p.cover_index} "
            f"index-window** ({p.cover_index / max(p.n_eval_rows, 1):.0%}) — the residual "
            f"asymmetry, which runs against the δ arm.", "",
            "| model | features | held-out AUC [95% CI, bout-clustered] |", "|---|---|---|"]
    for m in p.models:
        out.append(f"| {m.name} | {m.n_features} | {m.auc:.4f} [{m.lo:.4f}, {m.hi:.4f}] |")
    out += ["", f"**Paired Δ (δ-window − index-window): {_ci(p.delta_vs_index)}** — the "
                f"criterion.",
            f"Paired Δ (δ-window − state only): {_ci(p.delta_vs_state)} — does the motif "
            f"family add anything at all.", ""]
    return out


def render_markdown(run: Run, prereg: str) -> str:
    v = verdicts(run)
    lines = [
        "# PoC-E14 — δ-temporal motifs: does timing carry information beyond order?", "",
        "Generated by `uv run python -m analysis.poc.e14_temporal_motifs` — "
        "**do not hand-edit**. Module: `analysis/poc/e14_temporal_motifs.py`; counter: "
        "`analysis/poc/signatures.motif_counts`; tests: `tests/test_poc_e14.py`, "
        "`tests/test_poc_signatures.py`; pre-registration: "
        "`docs/research/poc/e14_prereg.md` (reproduced verbatim below).", "",
        f"**Corpus gate:** {run.gate_note}", "",
        f"**Timestamp slice (AA-010):** {run.ts_note}", "",
        f"**Split:** {run.n_train} train / {run.n_eval} held-out bout perspectives, "
        f"chronological (PoC-E8's `temporal_split`).", "",
        "## Verdicts", "",
    ]
    lines += [f"{i}. **{k}** — {val}" for i, (k, val) in enumerate(v.items(), start=1)]
    lines += ["", "---", "", "## Pre-registration (verbatim)", "", prereg.strip(), "",
              "---", "", "## Results", ""]
    for p in run.passes:
        lines += _pass_section(p, p.delta == DELTA_PRIMARY)
    lines += _reading(run, v)
    return "\n".join(lines) + "\n"


def _reading(run: Run, v: dict[str, str]) -> list[str]:
    p = run.primary()
    if p is None or p.error:
        return ["## Reading", "", "Not run.", ""]
    out = ["## Reading", ""]
    dm, ix = p.model(ARM_DELTA), p.model(ARM_INDEX)
    st = p.model(ARM_STATE)
    if dm and ix and st:
        out.append(
            f"The two motif arms are the same counter over the same edges with the same "
            f"alphabet; the only difference is the window's unit. They land at "
            f"{dm.auc:.4f} (seconds) and {ix.auc:.4f} (events), against {st.auc:.4f} for "
            f"the state alone. The paired difference is {_ci(p.delta_vs_index)}."
        )
    out.append(
        "This is the cleanest form the question could take, and that is deliberate: an "
        "unpaired comparison of a temporal model against an order model confounds the clock "
        "with everything else that differs between two feature sets. Holding the counter, "
        "the alphabet, the rows, the model and the seed fixed leaves the unit of the window "
        "as the only free variable."
    )
    if not wins(p.delta_vs_index):
        out.append(
            "The null has a use. `ts` is present on 94.7% of events, `ts_origin` is NULL on "
            "most matches, and every timestamp in this corpus is a human reading off a "
            "video clock — a measurement whose error is plausibly of the same order as the "
            "60-second window being tested. This result does not prove grappling is "
            "timeless; it bounds what THIS corpus's timestamps can support, and says the "
            "order-only structures the repository already ships are not the weak link."
        )
    out += ["", f"**Decision:** {v.get('cell', '')}", "",
            "Nothing here touches production. The counter and the motif alphabet live in "
            "`analysis/poc/signatures.py` and are reusable by any later cell that gets a "
            "denser corpus or machine-read timestamps.", ""]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="PoC-E14 — δ-temporal motifs")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    args = ap.parse_args(argv)

    gate = load_corpus()
    logger.info("gate: %s", gate_note(gate))
    if gate.error:
        return 1
    if args.dry_run:
        usable, note = ts_slice(gate.rows)
        logger.info("ts slice: %s", note)
        tr, _ev = temporal_split([b for b, _ in usable])
        by_key = {id(b): el for b, el in usable}
        pairs = [(b, by_key[id(b)]) for b in tr]
        for delta in DELTA_SWEEP:
            logger.info("  δ=%.0fs → index-window control %d edges", delta,
                        mean_window_edges(pairs, delta))
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
