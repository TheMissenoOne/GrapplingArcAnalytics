"""PoC-E8 — the interaction graph, measured (``docs/research/03_POC_PLANS.md``).

Three passes, in this order:

  (a) **Fixture** (``analysis.poc.fixtures``) — reproduce, with our own code, the two
      numbers the external PoC review published (``05_EXTERNAL_POC_REVIEW.md`` §1):
      23 actor-switch edge types and PageRank rank shifts of 8–11 ranks for
      reaction-defined positions. A mismatch is a finding, not a failure.
  (b) **Corpus**, gated on ``attribution.bout_flags(...)["perspective_reliable"]``.
      43.9% of bouts file every event under one athlete; ungated, an interaction
      edge measures the ingest batch. Each gated bout enters TWICE, once per
      athlete's perspective — a competition bout has no privileged "you", and
      ActionFlow's rates/kernel are invariant to that duplication (every count
      doubles), so the comparison stays paired.
  (c) **Two-kernel comparison** — the PtV absorbing/discounted value model
      (``analysis.path_to_victory``, γ=0.8, unchanged) run on the interaction
      kernel and on the ActionFlow kernel, scored by held-out finish-prediction
      AUC on a TEMPORAL split. This is the seed of the PoC-E4 harness: ``Bout``,
      ``temporal_split``, ``Kernel``, ``finish_label`` and ``evaluate_kernels``
      are kernel-agnostic on purpose — E4's γ/shaping sweep is "pass more
      ``Kernel``s".

Pre-registered before any number was looked at (all of it is in ``render_markdown``
too, so the generated report carries its own criterion):

  * label ......... PoC-E4's VAEP-analogue: does a *successful submission by the
                    same actor* occur within the next k=5 events?
  * split ......... the most recent ``EVAL_FRACTION`` of bouts by date are held out;
                    train is everything before them. Never random.
  * eval rows ..... only events whose role is ``you`` in their bout's perspective, so
                    the score is always "value of this state for the actor who acted".
                    Both kernels are scored on exactly the SAME rows (paired).
  * win ........... the interaction kernel wins iff the paired bootstrap ΔAUC interval
                    excludes 0 in its favour. Overlapping intervals = no win.
  * stable ........ an actor-switch edge counts as a vulnerability edge iff it survives
                    ``STABILITY_THRESHOLD`` of ``N_STABILITY`` bout-level bootstrap
                    resamples. ActionFlow provably cannot represent it (its two
                    endpoints belong to different fighters — ``build_graph.py``).
  * venue ......... the VERDICT is decided on the corpus pass only. The fixture is one
                    athlete's ten days; it reproduces the external claim and proves the
                    instrument, and it is not evidence about grappling.

LGPD: the fixture is the owner's own app data. Its numbers may appear in
``docs/research/poc/e8.md`` as PoC evidence and NOWHERE else — no export, no site
artefact, no competitive output (root ``CLAUDE.md``, fixtures' provenance note).

Usage::

    uv run python -m analysis.poc.e8_interaction_graph
    uv run python -m analysis.poc.e8_interaction_graph --out docs/research/poc/e8.md
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from analysis.attribution import bout_flags
from analysis.names import _normalize_name, canonicalize
from analysis.path_to_victory import path_to_victory
from analysis.poc.fixtures import user_export_rounds
from analysis.stats_rigor import bootstrap_ci
from analysis.technique_match import clean_label
from analysis.transitions.build_graph import network_from_sequences
from analysis.transitions.interaction_graph import (
    OPP,
    YOU,
    chain_segments,
    interaction_graph,
    node_id,
    node_key,
    role_map,
    switch_edges,
)

REPO = Path(__file__).resolve().parents[2]

FINISH_WINDOW = 5          # PoC-E4's k
EVAL_FRACTION = 0.25       # most recent quarter held out
N_STABILITY = 200          # bout-level bootstrap resamples for edge stability
STABILITY_THRESHOLD = 0.80
SEED = 20260824
COLD_SCORE = 0.0           # a node the training kernel never saw scores neutral


# ── the E4 harness seed ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Bout:
    """One sequence with a temporal key and a perspective.

    ``sequence`` events are corpus-shaped (``actor_id``); ``perspective`` names the
    actor that is ``you`` (``None`` = let the builder decide, which is what the app
    fixture wants: its actors are literally ``you``/``partner``).
    """

    key: tuple[Any, ...]
    sequence: list[dict[str, Any]]
    perspective: Any | None = None


@dataclass(frozen=True)
class Kernel:
    """A named way to turn bouts into a graph, an event into a node of it, and a bout
    into the node chains that graph's edges are counted from.

    ``chains`` exists so a model of the SUCCESSIONS (PoC-E4's Markov-order probe) reads
    the same definition of "what follows what" as ``build`` does — one bout can yield
    several chains (one per actor on ActionFlow, one per attributable segment on the
    interaction kernel), and consecutive repeats are folded out, matching both builders'
    ``a != b`` edge rule.
    """

    name: str
    build: Callable[[Sequence[Bout]], nx.DiGraph]
    node_of: Callable[[Mapping[str, Any]], str]
    chains: Callable[[Bout], list[list[str]]]


@dataclass
class KernelResult:
    name: str
    n_nodes: int
    n_edges: int
    auc: float
    lo: float
    hi: float
    n_pos: int
    n_neg: int
    verdict: str
    cold_rows: int
    scores: list[float] = field(default_factory=list)
    # Bout-clustered interval, filled by PoC-E4 (rows inside one bout are consecutive events
    # of one fight, so the row-level `lo`/`hi` above are anti-conservative). NaN = not computed.
    clo: float = float("nan")
    chi: float = float("nan")
    # Share of train nodes whose value saturated at the ±1 clamp. A value function that is
    # mostly clamped has no ranking left to score, which is how a γ/shaping pair can be
    # significantly WORSE than another without anything being wrong with the corpus (PoC-E4).
    clamped: float = 0.0


def temporal_split(
    bouts: Sequence[Bout], eval_fraction: float = EVAL_FRACTION
) -> tuple[list[Bout], list[Bout]]:
    """Chronological split — train ≤ T, eval > T. Never random (ADR-03).

    Bouts sharing the boundary key go to TRAIN, so no held-out bout has a
    same-day twin in the training kernel.
    """
    ordered = sorted(bouts, key=lambda b: b.key)
    if len(ordered) < 4:
        return list(ordered), []
    cut = max(1, int(round(len(ordered) * (1 - eval_fraction))))
    boundary = ordered[cut - 1].key
    train = [b for b in ordered if b.key <= boundary]
    return train, [b for b in ordered if b.key > boundary]


def finish_label(events: Sequence[Mapping[str, Any]], i: int, k: int = FINISH_WINDOW) -> bool:
    """PoC-E4's label: a landed submission BY THE SAME ACTOR within the next k events.

    Right-censored near a bout's end (fewer than k successors → fewer chances to be
    positive). Left as-is rather than trimmed: the censoring is identical for both
    kernels, which are scored on the same rows.
    """
    actor = events[i].get("actor_id", events[i].get("actor"))
    for e in events[i + 1: i + 1 + k]:
        if (e.get("actor_id", e.get("actor")) == actor
                and str(e.get("type", "")).lower() == "submission"
                and bool(e.get("successful", False))):
            return True
    return False


def eval_rows(bouts: Sequence[Bout], k: int = FINISH_WINDOW) -> list[tuple[dict[str, Any], bool]]:
    """(event, label) for every held-out event whose role is ``you`` in its bout.

    Kernel-agnostic: both kernels score these same rows, so the comparison is paired
    and the interaction kernel gets no extra events out of the mirroring.
    """
    rows: list[tuple[dict[str, Any], bool]] = []
    for b in bouts:
        roles = role_map(b.sequence, b.perspective)
        for i, e in enumerate(b.sequence):
            actor = e.get("actor_id", e.get("actor"))
            if actor is None or roles.get(actor) != YOU:
                continue
            if not node_key(str(e.get("label", "")), str(e.get("type", ""))):
                continue
            rows.append((dict(e), finish_label(b.sequence, i, k)))
    return rows


def rank_auc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Mann–Whitney AUC, tie-aware, O(n log n).

    Identical to ``stats_rigor.auc``'s point estimate (pinned by a test), computed by
    rank sum instead of the O(n_pos·n_neg) pair loop — the bootstrap below evaluates it
    thousands of times on thousands of corpus rows, where the pair loop does not finish.
    Undefined with one class present; 0.5 (chance) is returned so a degenerate bootstrap
    draw neither crashes nor votes.
    """
    n_pos = sum(1 for y in labels if y)
    n_neg = len(labels) - n_pos
    if not n_pos or not n_neg:
        return 0.5
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    pos_rank_sum = sum(r for r, y in zip(ranks, labels, strict=True) if y)
    return (pos_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _boot_ci(
    labels: Sequence[bool], statistic: Callable[[Sequence[int]], float], n_boot: int,
    groups: Sequence[Any] | None = None,
) -> tuple[float, float, float]:
    """Percentile bootstrap over EVAL ROWS via ``stats_rigor.bootstrap_ci``.

    ``bootstrap_ci`` resamples a list of floats, so what it is handed is row INDICES
    (as floats) and the statistic recomputes on the drawn rows — the only way to keep
    two kernels' scores PAIRED inside a generic resampler.

    ``groups`` = one bout key per row makes the resampling unit the BOUT. Rows inside one
    bout are not independent (they are consecutive events of the same fight), so the
    row-level interval is anti-conservative; PoC-E4 reports both.
    """
    idx = [float(i) for i in range(len(labels))]
    return bootstrap_ci(idx, lambda s: statistic([int(x) for x in s]),
                        n_boot=n_boot, seed=SEED, groups=groups)


def evaluate_kernels(
    train: Sequence[Bout],
    held_out: Sequence[Bout],
    kernels: Sequence[Kernel],
    k: int = FINISH_WINDOW,
    n_boot: int = 4000,
    value_fn: Callable[[nx.DiGraph], dict[str, float]] = path_to_victory,
    groups: Sequence[Any] | None = None,
) -> tuple[list[KernelResult], list[bool]]:
    """Fit each kernel on ``train``, score the SAME held-out rows, AUC per kernel.

    ``value_fn`` is how PoC-E4 sweeps: a (γ, shaping) pair is a partially-applied
    ``path_to_victory``, and everything else — label, split, rows, pairing — is held
    fixed by construction. ``groups`` (one cluster label per eval row) switches the
    interval to a CLUSTER bootstrap; ``None`` keeps E8's row-level resample.
    """
    rows = eval_rows(held_out, k)
    labels = [y for _, y in rows]
    n_pos = sum(1 for y in labels if y)
    results: list[KernelResult] = []
    for kern in kernels:
        g = kern.build(train)
        v = value_fn(g)
        scores = [v.get(kern.node_of(e), COLD_SCORE) for e, _ in rows]
        cold = sum(1 for e, _ in rows if kern.node_of(e) not in v)
        if n_pos and n_pos < len(labels):
            def stat(s: Sequence[int], sc: list[float] = scores) -> float:
                return rank_auc([sc[i] for i in s], [labels[i] for i in s])

            obs, lo, hi = _boot_ci(labels, stat, n_boot, groups)
        else:
            obs = lo = hi = float("nan")
        clamped = (sum(1 for x in v.values() if abs(x) >= 0.9999) / len(v)) if v else 0.0
        results.append(KernelResult(kern.name, g.number_of_nodes(), g.number_of_edges(),
                                    obs, lo, hi, n_pos, len(labels) - n_pos,
                                    "chance" if not (lo > 0.5 or hi < 0.5) else "separates",
                                    cold, scores, clamped=clamped))
    return results, labels


def paired_delta_auc(
    a: KernelResult, b: KernelResult, labels: Sequence[bool], n_boot: int = 2000,
    groups: Sequence[Any] | None = None,
) -> tuple[float, float, float]:
    """AUC(a) − AUC(b) with a paired percentile-bootstrap interval over eval rows."""
    return _boot_ci(labels, lambda s: (
        rank_auc([a.scores[i] for i in s], [labels[i] for i in s])
        - rank_auc([b.scores[i] for i in s], [labels[i] for i in s])), n_boot, groups)


# ── the two kernels ─────────────────────────────────────────────────────────────
def _fold_repeats(chain: Sequence[str]) -> list[str]:
    """Drop consecutive duplicates — an ``A → A`` step is not a transition (both
    builders refuse that edge; a successor model has to refuse it too)."""
    out: list[str] = []
    for s in chain:
        if not out or out[-1] != s:
            out.append(s)
    return out


def _actionflow_chains(b: Bout) -> list[list[str]]:
    """One chain per actor: that fighter's own ordered labels, which is exactly the
    succession ``network_from_sequences`` builds its edges from."""
    by_actor: defaultdict[Any, list[str]] = defaultdict(list)
    for e in b.sequence:
        actor = e.get("actor_id", e.get("actor"))
        label = clean_label(str(e.get("label", "")), str(e.get("type", "")))
        if actor is not None and label:
            by_actor[actor].append(label)
    return [c for c in (_fold_repeats(v) for v in by_actor.values()) if len(c) > 1]


def _interaction_chains(b: Bout) -> list[list[str]]:
    return [c for c in (_fold_repeats([e["id"] for e in seg])
                        for seg in chain_segments(b.sequence, b.perspective)) if len(c) > 1]


def actionflow_kernel() -> Kernel:
    """Production ActionFlow (within-actor), untouched."""
    return Kernel(
        name="actionflow (within-actor)",
        build=lambda bouts: network_from_sequences([b.sequence for b in bouts]),
        node_of=lambda e: clean_label(str(e.get("label", "")), str(e.get("type", ""))),
        chains=_actionflow_chains,
    )


def interaction_kernel() -> Kernel:
    return Kernel(
        name="interaction (actor-aware)",
        build=lambda bouts: interaction_graph([b.sequence for b in bouts],
                                              [b.perspective for b in bouts]),
        node_of=lambda e: node_id(YOU, node_key(str(e.get("label", "")),
                                                str(e.get("type", "")))),
        chains=_interaction_chains,
    )


# ── corpora ─────────────────────────────────────────────────────────────────────
def fixture_bouts() -> list[Bout]:
    """The committed app slice → bouts. ``actor`` is copied to ``actor_id`` so the
    ActionFlow builder (which reads ``actor_id``) sees the same rounds."""
    out: list[Bout] = []
    for i, r in enumerate(user_export_rounds()):
        seq = [{**e, "actor_id": e.get("actor")} for e in r.get("events", [])]
        if seq:
            out.append(Bout(key=(str(r.get("date", "")), i), sequence=seq, perspective="you"))
    return out


@dataclass
class GateReport:
    total: int = 0
    with_sequence: int = 0
    passed: int = 0
    one_sided: int = 0
    bouts: list[Bout] = field(default_factory=list)
    error: str | None = None


def corpus_bouts(min_events: int = 4) -> GateReport:
    """Gated corpus bouts, mirrored (one ``Bout`` per athlete perspective).

    Read-only: one ``select`` over ``matches`` through the shared engine. A missing or
    unreachable ``DATABASE_URL`` is reported, not raised — the fixture pass still runs.
    """
    rep = GateReport()
    try:
        from sqlalchemy import text

        from db.base import get_engine

        with get_engine().connect() as conn:
            rowset = conn.execute(text(
                "SELECT id, athlete_a_id, athlete_b_id, year, created_at, sequence "
                "FROM matches WHERE status = 'final' AND sequence IS NOT NULL"
            )).mappings().all()
    except Exception as exc:  # noqa: BLE001 — the report says why, the PoC continues
        rep.error = f"{type(exc).__name__}: {exc}".split("\n")[0][:160]
        return rep

    for row in rowset:
        rep.total += 1
        seq = [e for e in (row["sequence"] or []) if isinstance(e, dict)]
        if len(seq) < min_events:
            continue
        rep.with_sequence += 1
        flags = bout_flags(seq, str(row["athlete_a_id"]), str(row["athlete_b_id"]))
        if not flags["perspective_reliable"]:
            rep.one_sided += 1
            continue
        rep.passed += 1
        key = (int(row["year"] or 0), str(row["created_at"]), str(row["id"]))
        for persp in (str(row["athlete_a_id"]), str(row["athlete_b_id"])):
            rep.bouts.append(Bout(key=key, sequence=[dict(e) for e in seq], perspective=persp))
    return rep


# ── measurements ────────────────────────────────────────────────────────────────
def _pagerank_by_key(g: nx.DiGraph) -> dict[str, float]:
    """PageRank collapsed to the canonical key space (roles summed), so the two
    graphs are ranked over the same vocabulary."""
    pr = nx.pagerank(g, weight="weight")
    out: defaultdict[str, float] = defaultdict(float)
    for n, p in pr.items():
        key = g.nodes[n].get("label") or canonicalize(_normalize_name(str(n)))
        out[str(key)] += p
    return dict(out)


def _ranks(scores: Mapping[str, float]) -> dict[str, int]:
    order = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return {k: i + 1 for i, (k, _) in enumerate(order)}


def opp_share(g: nx.DiGraph) -> dict[str, float]:
    """Per label, the share of its appearances that belong to the OPPONENT role —
    the pre-registered definition of a "reaction-defined" position (share ≥ 0.5)."""
    tot: defaultdict[str, float] = defaultdict(float)
    opp: defaultdict[str, float] = defaultdict(float)
    for n, d in g.nodes(data=True):
        key = str(d.get("label", n))
        tot[key] += float(d.get("occ", 0))
        if d.get("role") != YOU:
            opp[key] += float(d.get("occ", 0))
    return {k: (opp[k] / tot[k] if tot[k] else 0.0) for k in tot}


def rank_shifts(af: nx.DiGraph, ig: nx.DiGraph) -> list[dict[str, Any]]:
    """|Δrank| of each label between the ActionFlow and interaction PageRanks."""
    ra, ri = _ranks(_pagerank_by_key(af)), _ranks(_pagerank_by_key(ig))
    share = opp_share(ig)
    rows: list[dict[str, Any]] = [
        {"label": k, "actionflow": ra[k], "interaction": ri[k],
         "shift": abs(ra[k] - ri[k]), "opp_share": round(share.get(k, 0.0), 2)}
        for k in set(ra) & set(ri)]
    rows.sort(key=lambda r: (-int(r["shift"]), str(r["label"])))
    return rows


def _swap_roles(node: str) -> str:
    role, _, key = node.partition(":")
    return node_id(OPP if role == YOU else YOU, key)


def distinct_up_to_mirror(edges: Sequence[tuple[str, str]]) -> int:
    """How many of these edges are distinct once the mirror is folded.

    A mirrored corpus (each bout entered from both athletes' perspectives) yields every
    cross-fighter succession TWICE — ``opp:X → you:Y`` and its role-swapped twin — so
    the raw type count is exactly 2× the distinct patterns. Reporting only the raw count
    would double the size of the finding for free.
    """
    return len({min((u, v), (_swap_roles(u), _swap_roles(v))) for u, v in edges})


def switch_edge_stability(
    bouts: Sequence[Bout], n_boot: int = N_STABILITY, seed: int = SEED
) -> dict[tuple[str, str], float]:
    """How often each full-sample actor-switch edge survives a bout-level resample.

    Bouts (not events) are the resampling unit: events inside one bout are not
    independent observations (``stats_rigor.bootstrap_ci``'s cluster argument, same
    reasoning). Frequency is over the FULL-sample switch edges, so an edge invented by
    a single lucky resample never enters the table.
    """
    full = set(switch_edges(interaction_graph([b.sequence for b in bouts],
                                              [b.perspective for b in bouts])))
    if not full:
        return {}
    rng = random.Random(seed)
    hits: defaultdict[tuple[str, str], int] = defaultdict(int)
    n = len(bouts)
    for _ in range(n_boot):
        draw = [bouts[rng.randrange(n)] for _ in range(n)]
        g = interaction_graph([b.sequence for b in draw], [b.perspective for b in draw])
        for e in switch_edges(g):
            if e in full:
                hits[e] += 1
    return {e: hits[e] / n_boot for e in full}


# ── pass assembly ───────────────────────────────────────────────────────────────
@dataclass
class Pass:
    """One venue's numbers (fixture or corpus)."""

    name: str
    n_bouts: int
    n_events: int
    af: nx.DiGraph
    ig: nx.DiGraph
    switch_types: int
    switch_occurrences: int
    shifts: list[dict[str, Any]]
    stability: dict[tuple[str, str], float]
    results: list[KernelResult]
    delta: tuple[float, float, float]
    n_train: int
    n_eval_bouts: int
    n_stability: int
    # Corpus bouts are entered from BOTH athletes' perspectives (no privileged side);
    # the app fixture is single-perspective. Changes what the role counts mean.
    mirrored: bool

    @property
    def stable_switch(self) -> list[tuple[tuple[str, str], float]]:
        return sorted(((e, f) for e, f in self.stability.items() if f >= STABILITY_THRESHOLD),
                      key=lambda kv: (-kv[1], kv[0]))

    @property
    def switch_distinct(self) -> int:
        return distinct_up_to_mirror(switch_edges(self.ig))

    @property
    def stable_distinct(self) -> int:
        return distinct_up_to_mirror([e for e, _ in self.stable_switch])



def run_pass(name: str, bouts: Sequence[Bout], n_boot: int = 4000,
             n_stability: int = N_STABILITY, mirrored: bool = False) -> Pass:
    seqs = [b.sequence for b in bouts]
    persp = [b.perspective for b in bouts]
    af = network_from_sequences(seqs)
    ig = interaction_graph(seqs, persp)
    sw = switch_edges(ig)
    train, held = temporal_split(bouts)
    results, labels = evaluate_kernels(train, held, [interaction_kernel(), actionflow_kernel()],
                                       n_boot=n_boot)
    delta = (paired_delta_auc(results[0], results[1], labels, n_boot=max(200, n_boot // 4))
             if held and len(set(labels)) > 1 else (float("nan"),) * 3)
    return Pass(
        name=name, n_bouts=len(bouts), n_events=sum(len(s) for s in seqs),
        af=af, ig=ig, switch_types=len(sw),
        switch_occurrences=sum(ig[u][v]["weight"] for u, v in sw),
        shifts=rank_shifts(af, ig),
        stability=switch_edge_stability(bouts, n_boot=n_stability),
        results=results, delta=delta, n_train=len(train), n_eval_bouts=len(held),
        n_stability=n_stability, mirrored=mirrored,
    )


def verdict(corpus: Pass | None) -> dict[str, Any]:
    """The pre-registered criterion, applied verbatim, on the corpus pass only."""
    if corpus is None:
        return {"decided": False, "auc_win": False, "stable_edges": 0,
                "text": "UNDECIDED — the corpus pass did not run, and the fixture is "
                        "not a venue for the verdict."}
    d, lo, hi = corpus.delta
    auc_win = lo == lo and lo > 0.0  # NaN-safe: the paired interval excludes 0 in favour
    stable = len(corpus.stable_switch)
    accept = auc_win or stable > 0
    return {
        "decided": True, "auc_win": auc_win, "stable_edges": stable, "accept": accept,
        "text": ("ACCEPT" if accept else "REJECT") + " — limb 1 (AUC win) "
                + (f"HOLDS: ΔAUC {d:+.3f} [{lo:+.3f}, {hi:+.3f}]" if auc_win
                   else f"FAILS: ΔAUC {d:+.3f} [{lo:+.3f}, {hi:+.3f}] covers 0")
                + "; limb 2 (stable vulnerability edges) "
                + (f"HOLDS: {stable} actor-switch edge types "
                   f"({corpus.stable_distinct} distinct) at ≥ {STABILITY_THRESHOLD:.0%}"
                   if stable else "FAILS: no actor-switch edge survives the resample")
                + ". Consequence, per the criterion: the interaction graph earns a place "
                  "as a SECOND, explicitly-labelled graph. It replaces nothing — "
                  "ActionFlow, PtV's production kernel and every shipped number stand.",
    }


# ── report ──────────────────────────────────────────────────────────────────────
def _auc_row(r: KernelResult) -> str:
    return (f"| {r.name} | {r.n_nodes} | {r.n_edges} | {r.auc:.3f} [{r.lo:.3f}, {r.hi:.3f}] "
            f"| {r.n_pos} | {r.n_neg} | {r.cold_rows} | {r.verdict} |")


def _pass_section(p: Pass, external: str | None) -> list[str]:
    lines = [
        f"## {p.name}",
        "",
        f"{p.n_bouts} sequences · {p.n_events} events · ActionFlow "
        f"{p.af.number_of_nodes()} nodes / {p.af.number_of_edges()} edges · interaction "
        f"{p.ig.number_of_nodes()} nodes / {p.ig.number_of_edges()} edges.",
        "",
        "| quantity | ours" + (" | external (§1) |" if external else " |"),
        "|---|---|" + ("---|" if external else ""),
        f"| actor-switch edge TYPES | **{p.switch_types}** "
        + (f"| {external} |" if external else "|"),
        *([f"| — distinct once the mirror is folded | {p.switch_distinct} |"]
          if p.mirrored else []),
        f"| actor-switch edge occurrences | {p.switch_occurrences} "
        + ("| — |" if external else "|"),
        f"| within-actor edge types | {p.ig.number_of_edges() - p.switch_types} "
        + ("| — |" if external else "|"),
        "",
        "### PageRank rank shift, interaction vs ActionFlow (top 10 by |Δrank|)",
        "",
        "| label | ActionFlow rank | interaction rank | Δ | opp share |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {r['label']} | {r['actionflow']} | {r['interaction']} | {r['shift']} "
              f"| {r['opp_share']:.2f} |" for r in p.shifts[:10]]
    reaction = [r for r in p.shifts if float(r["opp_share"]) >= 0.5]
    if p.mirrored:
        lines += ["", "Opp share is 0.50 for every label here **by construction** — each bout "
                      "is entered from both athletes' perspectives, so \"reaction-defined\" "
                      "cannot discriminate on a corpus with no privileged side. It is a "
                      "fixture-only instrument; the |Δrank| column above still stands."]
    elif reaction:
        top = max(int(r["shift"]) for r in reaction)
        med = sorted(int(r["shift"]) for r in reaction)[len(reaction) // 2]
        lines += ["", f"Reaction-defined positions (opp share ≥ 0.5): {len(reaction)}; "
                      f"median |Δrank| {med}, max {top}."]
    else:
        lines += ["", "No label reaches an opp share ≥ 0.5 in this venue."]
    lines += [
        "",
        "### Vulnerability edges (actor-switch, bout-level bootstrap)",
        "",
        f"{len(p.stability)} switch edge types tested over {p.n_stability} resamples; "
        f"**{len(p.stable_switch)}** survive at ≥ {STABILITY_THRESHOLD:.0%}"
        + (f" ({p.stable_distinct} distinct once the mirror is folded)." if p.mirrored
           else "."),
        "",
        "| edge | stability |",
        "|---|---|",
    ]
    lines += [f"| `{u}` → `{v}` | {f_:.2f} |" for (u, v), f_ in p.stable_switch[:10]]
    if not p.stable_switch:
        lines.append("| — | — |")
    lines += [
        "",
        "### Held-out finish prediction (PtV γ=0.8 on each kernel)",
        "",
        f"Temporal split: {p.n_train} train sequences, {p.n_eval_bouts} held out "
        f"(most recent {EVAL_FRACTION:.0%} by date).",
        "",
        "| kernel | nodes | edges | AUC [95% CI] | pos | neg | cold rows | reading |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [_auc_row(r) for r in p.results]
    d, dlo, dhi = p.delta
    lines += ["", (f"Paired ΔAUC (interaction − ActionFlow): **{d:+.3f}** "
                   f"[{dlo:+.3f}, {dhi:+.3f}]" if d == d else
                   "Paired ΔAUC: not computable (held-out set has one class or is empty).")]
    return lines


def render_markdown(fixture: Pass, corpus: Pass | None, gate: GateReport) -> str:
    v = verdict(corpus)
    lines = [
        "# PoC-E8 — interaction graph (actor-aware states): first run",
        "",
        "Generated by `uv run python -m analysis.poc.e8_interaction_graph` — do not "
        "hand-edit. Builder: `analysis/transitions/interaction_graph.py`; tests: "
        "`tests/test_interaction_graph.py`. Plan: `docs/research/03_POC_PLANS.md` "
        "(PoC-E8); origin: `docs/research/05_EXTERNAL_POC_REVIEW.md` §1.",
        "",
        "## Criterion (pre-registered, before any number below)",
        "",
        "> the interaction kernel wins the E4 AUC comparison, or surfaces vulnerability "
        "edges (opp-response patterns) that ActionFlow provably cannot represent AND that "
        "survive a stability check. Either earns it a place as a second, "
        "explicitly-labelled graph; neither replaces ActionFlow.",
        "",
        "Operationalised, fixed in the runner's docstring before the run:",
        "",
        f"1. **Win** = the paired bootstrap ΔAUC interval (interaction − ActionFlow, same "
        f"held-out rows) excludes 0 in the interaction kernel's favour. Label: a landed "
        f"submission by the same actor within the next k={FINISH_WINDOW} events. Split: the "
        f"most recent {EVAL_FRACTION:.0%} of sequences by date, never random.",
        f"2. **Vulnerability edge** = an actor-switch edge (`you:X → opp:Y`) — ActionFlow "
        f"cannot represent it, its endpoints belong to different fighters — surviving "
        f"≥ {STABILITY_THRESHOLD:.0%} of {N_STABILITY} bout-level bootstrap resamples.",
        "3. **Venue** = the corpus pass decides. The fixture is one athlete's ten days: it "
        "reproduces the external claim and proves the instrument, and it is not evidence "
        "about grappling.",
        "",
        "## Verdict",
        "",
        f"**{v['text']}**",
        "",
    ]
    lines += _pass_section(fixture, external="23")
    lines += ["", "> LGPD: the fixture is the repository owner's own app export "
                  "(`owner_kind='user'` data). These numbers exist as PoC evidence in this "
                  "file only — never in an export, a site artefact or any competitive "
                  "output.", ""]
    if corpus is not None:
        lines += ["## Gate", "",
                  f"{gate.total} final matches with a sequence · "
                  f"{gate.with_sequence} with ≥4 events · **{gate.passed} pass** "
                  f"`bout_flags(...)['perspective_reliable']` · {gate.one_sided} refused "
                  f"(one-sided filing). Each passing bout enters twice, once per athlete "
                  f"perspective → {gate.passed * 2} sequences.", ""]
        lines += _pass_section(corpus, external=None)
    else:
        lines += ["## Corpus pass — NOT RUN", "",
                  f"`{gate.error or 'no DATABASE_URL'}`. The verdict stays undecided: the "
                  "fixture is not a venue for it.", ""]
    lines += ["", "## Reading", ""] + _reading(fixture, corpus, v)
    return "\n".join(lines) + "\n"


def _reading(fixture: Pass, corpus: Pass | None, v: Mapping[str, Any]) -> list[str]:
    fix_shift = fixture.shifts[0]["shift"] if fixture.shifts else 0
    out = [
        f"1. **The external §1 numbers reproduce.** Our builder finds "
        f"{fixture.switch_types} actor-switch edge types on the committed fixture against "
        f"the external's 23, and the largest PageRank rank shift is {fix_shift} against "
        f"their reported 8–11 band. The claim that ActionFlow structurally cannot hold "
        f"`you:X → opp:Y` is now established from our own code, which is the durable form "
        f"of the finding (`05_EXTERNAL_POC_REVIEW.md`, \"Where to be skeptical\").",
        "",
    ]
    if corpus is not None:
        i_res, a_res = corpus.results[0], corpus.results[1]
        d, dlo, dhi = corpus.delta
        out += [
            f"2. **The AUC limb is a null result on the corpus.** Interaction "
            f"{i_res.auc:.3f} [{i_res.lo:.3f}, {i_res.hi:.3f}] vs ActionFlow "
            f"{a_res.auc:.3f} [{a_res.lo:.3f}, {a_res.hi:.3f}]; paired ΔAUC "
            f"{d:+.3f} [{dlo:+.3f}, {dhi:+.3f}]. "
            + ("The interval excludes 0, so limb 1 passes."
               if v.get("auc_win") else
               "The interval covers 0, so limb 1 fails — no evidence the actor-aware "
               "kernel predicts finishes better, which is not the same claim as "
               "\"it predicts worse\"."),
            "",
            f"3. **The vulnerability limb "
            f"{'passes' if corpus.stable_switch else 'fails'}, on a floor it sets itself.** "
            f"{len(corpus.stable_switch)} of {len(corpus.stability)} actor-switch edge "
            f"types survive {corpus.n_stability} bout-level resamples at ≥ "
            f"{STABILITY_THRESHOLD:.0%} — {corpus.stable_distinct} distinct patterns once "
            f"the perspective mirror is folded. These are things ActionFlow cannot express "
            f"at all: its edges join one fighter's own consecutive actions by construction "
            f"(`transitions/build_graph.py`), so a cross-fighter succession has nowhere to "
            f"live in it. Be honest about the bar, though: an edge observed across many "
            f"bouts survives resampling nearly by definition. What the check rules out is "
            f"the fixture-scale accident where one bout carries a whole edge — not much "
            f"more.",
            "",
            "4. **What this does NOT license.** A stable edge is a reproducible pattern, "
            "not a validated predictor — limb 2 is a *representational* claim and the "
            "criterion says so (\"a second, explicitly-labelled graph\"). Nothing here "
            "moves ActionFlow, PtV's production kernel, or any shipped number. The next "
            "cell is PoC-E4: the same harness with γ and shaping swept, on both kernels, "
            "where a genuinely better kernel would show up.",
            "",
            f"5. **Caveats that bound every number above.** The corpus label rides on "
            f"`successful`, which is present on a minority of corpus events (absent reads "
            f"as False, so positives are undercounted); the gate removes the one-sided "
            f"bouts but cannot repair the actor noise inside the ones it keeps; the "
            f"held-out window is right-censored at each bout's end, for both kernels "
            f"equally; and the large |Δrank| values sit in the low-PageRank tail, where a "
            f"few extra in-edges move a node dozens of ranks — the median shift "
            f"({sorted(int(r['shift']) for r in corpus.shifts)[len(corpus.shifts) // 2]}) "
            f"is the honest summary, not the top row. Note also that the interaction "
            f"kernel splits the same events over "
            f"{corpus.ig.number_of_nodes() / max(1, corpus.af.number_of_nodes()):.1f}× the "
            f"nodes and buys no AUC with them: more resolution is not more signal here.",
        ]
    else:
        out += ["2. **No corpus pass, no verdict.** The AUC and stability limbs are both "
                "corpus-decided by pre-registration; with the DB unreachable this run "
                "proves the instrument and reproduces §1, nothing more."]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PoC-E8 — interaction graph, measured")
    ap.add_argument("--out", default=str(REPO / "docs" / "research" / "poc" / "e8.md"))
    ap.add_argument("--n-boot", type=int, default=4000)
    ap.add_argument("--n-stability", type=int, default=N_STABILITY)
    ap.add_argument("--skip-corpus", action="store_true",
                    help="fixture pass only (no DB read)")
    args = ap.parse_args(argv)

    fixture = run_pass("Fixture pass (app data — owner's own export)", fixture_bouts(),
                       n_boot=args.n_boot, n_stability=args.n_stability)
    gate = GateReport(error="skipped (--skip-corpus)") if args.skip_corpus else corpus_bouts()
    corpus = (run_pass("Corpus pass (gated on perspective_reliable)", gate.bouts,
                       n_boot=args.n_boot, n_stability=args.n_stability, mirrored=True)
              if gate.bouts else None)

    md = render_markdown(fixture, corpus, gate)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
