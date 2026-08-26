"""PoC-E13 — GraphSAGE inductive link prediction. Tiny synthetic graphs, no database.

Three things these tests exist to protect, in order of how badly a break would hurt:

1. **Split leakage.** An eval athlete's graph, their bouts, and their held-out edges must
   never reach a training input. This is the whole inductive claim; if it leaks, every
   number in `docs/research/poc/e13.md` is about memorisation.
2. **Determinism.** A fixed seed must give the identical forward pass and the identical
   candidate rows — an interval that moves between runs is not reproducible.
3. **The instrument.** The vectorised AUC must equal the pure-Python one PoC-E8 published,
   or the two cells are not comparable.
"""
from __future__ import annotations

import random
from typing import Any

import numpy as np
import pytest

from analysis.poc.e8_interaction_graph import rank_auc
from analysis.poc.e13_graphsage import (
    BASELINES,
    MIN_EDGES,
    ArmResult,
    BoutRow,
    Sample,
    _decide,
    adjacency,
    athlete_bouts,
    auc_np,
    baseline_scores,
    beats,
    corpus_prior,
    edge_list,
    features,
    load_text_vectors,
    quarantine,
    run_arm,
    sample_arm_a,
    sample_arm_b,
    split_athletes,
)

torch = pytest.importorskip("torch")

from analysis.poc.e13_graphsage import (  # noqa: E402  (after the torch guard)
    build_model,
    model_scores,
    to_tensors,
    train_model,
)

CHAIN = ["Closed Guard", "Armbar", "Mount", "Back Control", "Rear Naked Choke",
         "Half Guard", "Knee Cut", "Side Control", "Kimura", "Triangle Choke",
         "De La Riva", "Berimbolo", "Single Leg", "Double Leg", "Heel Hook"]


def _event(label: str, actor: str, typ: str = "control") -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": False}


def _bout(idx: int, a: str, b: str, labels_a: list[str], labels_b: list[str]) -> BoutRow:
    """One synthetic bout: A's own chain interleaved with B's, so within-actor edges are
    exactly ``labels_a`` for A and ``labels_b`` for B."""
    seq: list[dict[str, Any]] = []
    for x, y in zip(labels_a, labels_b, strict=False):
        seq.append(_event(x, a))
        seq.append(_event(y, b))
    return BoutRow(key=(2020 + idx, f"2020-01-{idx + 1:02d}", f"m{idx}"),
                   athlete_a=a, athlete_b=b, sequence=seq)


def _rows(n_athletes: int = 12, bouts_each: int = 3) -> list[BoutRow]:
    """A corpus where each bout walks its own seeded permutation of the label chain.

    Permutations rather than rotations on purpose: rotating a chain changes exactly one edge
    (the seam), so a rotation corpus gives an athlete's later bouts nothing new to predict and
    Arm B degenerates to zero positives.
    """
    rows: list[BoutRow] = []
    idx = 0
    for a in range(n_athletes):
        for b in range(bouts_each):
            rng = random.Random(f"{a}:{b}")
            rows.append(_bout(idx, f"ath{a:02d}", f"ath{(a + 1) % n_athletes:02d}",
                              rng.sample(CHAIN, len(CHAIN)), rng.sample(CHAIN, len(CHAIN))))
            idx += 1
    return rows


def _sample(nodes: int = 6, positives: tuple[tuple[int, int], ...] = ((0, 3),)) -> Sample:
    obs = tuple((i, i + 1, 1.0) for i in range(nodes - 1))
    return Sample("ath00", (2020, "d", "m"), tuple(CHAIN[:nodes]), obs,
                  frozenset(positives), 0, 2)


# ── the instrument ──────────────────────────────────────────────────────────────
def test_auc_np_matches_e8_rank_auc() -> None:
    rng = np.random.default_rng(7)
    for _ in range(20):
        scores = rng.integers(0, 5, size=60).astype(float)  # heavy ties on purpose
        labels = rng.random(60) < 0.3
        assert auc_np(scores, labels) == pytest.approx(
            rank_auc(list(scores), list(labels)), abs=1e-12)


def test_auc_np_degenerate_class_is_chance() -> None:
    assert auc_np(np.array([1.0, 2.0]), np.array([True, True])) == 0.5


def test_beats_requires_the_interval_to_exclude_zero() -> None:
    assert beats((0.05, 0.01, 0.09))
    assert not beats((0.05, -0.01, 0.09))   # covers 0 → a null, not a win
    assert not beats((-0.05, -0.09, -0.01))


# ── candidate construction ──────────────────────────────────────────────────────
def test_candidates_exclude_observed_edges_and_include_positives() -> None:
    s = _sample()
    pairs = s.candidates()
    observed = {(i, j) for i, j, _ in s.obs}
    assert not (observed & {(i, j) for i, j, _ in pairs})
    assert {(i, j) for i, j, y in pairs if y} == set(s.positives)
    assert len(pairs) == s.n_nodes * (s.n_nodes - 1) - len(observed)


def test_candidates_are_deterministic() -> None:
    assert _sample().candidates() == _sample().candidates()


def test_features_are_771_wide_and_derive_only_from_observed_edges() -> None:
    s = _sample()
    text = {label: np.ones(768) / np.sqrt(768) for label in s.nodes}
    x = features(s, text)
    assert x.shape == (s.n_nodes, 771)
    # node 0 has one outgoing observed edge and no incoming one
    assert x[0, 769] == pytest.approx(np.log1p(1.0))
    assert x[0, 768] == pytest.approx(0.0)


def test_adjacency_rows_are_means_not_sums() -> None:
    s = Sample("a", (0, "", ""), ("x", "y", "z"), ((0, 1, 5.0), (0, 2, 1.0)),
               frozenset({(1, 2)}), 0, 1)
    a_out, a_in = adjacency(s)
    assert a_out[0].tolist() == [0.0, 0.5, 0.5]      # unweighted mean aggregator
    assert a_in[1].tolist() == [1.0, 0.0, 0.0]


# ── samplers ────────────────────────────────────────────────────────────────────
def test_arm_a_observed_graph_never_contains_a_positive() -> None:
    by_athlete = athlete_bouts(_rows())
    made = 0
    for ab in by_athlete.values():
        s = sample_arm_a(ab)
        if s is None:
            continue
        made += 1
        assert not ({(i, j) for i, j, _ in s.obs} & set(s.positives))
        assert len(s.obs) >= 1 and s.positives
    assert made >= 8


def test_arm_a_is_seed_deterministic() -> None:
    ab = next(iter(athlete_bouts(_rows()).values()))
    assert sample_arm_a(ab) == sample_arm_a(ab)


def test_arm_a_respects_the_min_edges_floor() -> None:
    rows = [_bout(0, "solo", "other", ["Mount", "Armbar"], ["Closed Guard", "Sweep"])]
    ab = athlete_bouts(rows)["solo"]
    assert len(edge_list(ab.graph())) < MIN_EDGES
    assert sample_arm_a(ab) is None


def test_arm_b_observed_graph_uses_only_the_earlier_bouts() -> None:
    """The held-out bouts' transitions must be absent from the observed edge set — that is the
    only thing making Arm B a forecast rather than a reconstruction."""
    by_athlete = athlete_bouts(_rows(n_athletes=8, bouts_each=4))
    checked = 0
    for ab in by_athlete.values():
        s = sample_arm_b(ab)
        if s is None:
            continue
        checked += 1
        observed = {(s.nodes[i], s.nodes[j]) for i, j, _ in s.obs}
        early = {(u, v) for u, v, _ in edge_list(
            ab.graph([k for k, _ in ab.bouts][:max(1, int(round(len(ab.bouts) * 0.7)))]))}
        assert observed == early
        assert not (observed & {(s.nodes[i], s.nodes[j]) for i, j in s.positives})
    assert checked >= 1


def test_arm_b_needs_two_bouts() -> None:
    rows = [_bout(0, "solo", "x", CHAIN, list(reversed(CHAIN)))]
    assert sample_arm_b(athlete_bouts(rows)["solo"]) is None


# ── split leakage guards ────────────────────────────────────────────────────────
def test_split_is_athlete_disjoint_and_ordered_by_debut() -> None:
    samples = [Sample(f"a{i}", (2000 + i, "", ""), ("x", "y", "z"), ((0, 1, 1.0),),
                      frozenset({(1, 2)}), 0, 1) for i in range(20)]
    train, val, held = split_athletes(samples)
    ids = [{s.athlete for s in group} for group in (train, val, held)]
    assert not (ids[0] & ids[1]) and not (ids[0] & ids[2]) and not (ids[1] & ids[2])
    assert len(train) + len(val) + len(held) == 20
    assert max(s.debut for s in train + val) < min(s.debut for s in held)


def test_quarantine_removes_every_bout_an_eval_athlete_touched() -> None:
    rows = _rows()
    blocked = {"ath03", "ath07"}
    kept = quarantine(rows, blocked)
    assert kept and len(kept) < len(rows)
    assert all(not (r.has(a)) for r in kept for a in blocked)


def test_corpus_prior_cannot_see_a_quarantined_bout() -> None:
    """The prior baseline is fitted on train bouts; a transition that exists ONLY in a
    quarantined bout must be absent from it."""
    rows = _rows(n_athletes=4, bouts_each=1)
    rows.append(BoutRow(key=(2099, "z", "secret"), athlete_a="evalguy", athlete_b="ath00",
                        sequence=[_event("Ezekiel Choke", "evalguy"),
                                  _event("Wristlock", "evalguy")]))
    trans_all, _ = corpus_prior(rows)
    trans_train, _ = corpus_prior(quarantine(rows, {"evalguy"}))
    assert ("Ezekiel Choke", "Wristlock") in trans_all
    assert ("Ezekiel Choke", "Wristlock") not in trans_train


def test_run_arm_never_trains_on_an_eval_athlete(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end guard: capture what the trainer was handed and assert no eval athlete and no
    quarantined bout is in it."""
    rows = _rows(n_athletes=14, bouts_each=3)
    by_athlete = athlete_bouts(rows)
    text = {label: np.eye(768)[i % 768] for i, label in enumerate(CHAIN)}
    seen: dict[str, Any] = {}

    import analysis.poc.e13_graphsage as mod

    real = mod.train_model

    def spy(train: Any, val: Any, in_dim: int, layers: int = 2, **kw: Any) -> Any:
        seen.setdefault("train", set()).update(g.sample.athlete for g in train)
        seen.setdefault("val", set()).update(g.sample.athlete for g in val)
        return real(train, val, in_dim, layers, **kw)

    monkeypatch.setattr(mod, "train_model", spy)
    arm = run_arm("A (test)", rows, by_athlete, sample_arm_a, text,
                  n_boot=60, max_epochs=3, n_boot_paired=40)
    assert arm.n_eval >= 1 and arm.n_train >= 1

    samples = [s for s in (sample_arm_a(ab) for ab in by_athlete.values()) if s is not None]
    eval_ids = {s.athlete for s in split_athletes(samples)[2]}
    assert eval_ids
    # THE guard: no eval athlete's graph ever reached the trainer, nor the validation set.
    assert not (seen["train"] & eval_ids)
    assert not (seen["val"] & eval_ids)
    assert not (seen["train"] & seen["val"])
    # every method scored exactly the same rows — the comparison is paired
    assert len({len(r.scores) for r in arm.results.values()}) == 1
    assert set(arm.deltas) == set(BASELINES)


# ── the model ───────────────────────────────────────────────────────────────────
def _tensors(n: int = 10) -> list[Any]:
    text = {label: np.eye(768)[i] for i, label in enumerate(CHAIN)}
    by_athlete = athlete_bouts(_rows(n_athletes=n, bouts_each=3))
    samples = [s for s in (sample_arm_a(ab) for ab in by_athlete.values()) if s is not None]
    return [to_tensors(s, text) for s in samples]


def test_forward_is_deterministic_for_a_fixed_seed() -> None:
    graphs = _tensors(4)
    a = build_model(int(graphs[0].x.shape[1]), 2, seed=7)
    b = build_model(int(graphs[0].x.shape[1]), 2, seed=7)
    assert model_scores(a, graphs) == model_scores(b, graphs)
    c = build_model(int(graphs[0].x.shape[1]), 2, seed=8)
    assert model_scores(a, graphs) != model_scores(c, graphs)


def test_embeddings_are_l2_normalised() -> None:
    graphs = _tensors(3)
    model = build_model(int(graphs[0].x.shape[1]), 2, seed=7)
    with torch.no_grad():
        z = model.encode(graphs[0].x, graphs[0].a_out, graphs[0].a_in)
    assert torch.allclose(z.norm(dim=1), torch.ones(z.shape[0]), atol=1e-5)


def test_backward_smoke_loss_decreases() -> None:
    """The gradient actually flows: same seed, more epochs → lower training loss."""
    graphs = _tensors(6)
    in_dim = int(graphs[0].x.shape[1])
    _, short = train_model(graphs, [], in_dim, 2, seed=11, max_epochs=1)
    _, long = train_model(graphs, [], in_dim, 2, seed=11, max_epochs=25)
    assert long.final_loss < short.final_loss
    assert long.epochs_run == 25


def test_zero_layer_ablation_ignores_the_graph() -> None:
    """The `mlp` arm must be blind to message passing — scrambling the adjacency may not move
    a single score, or the ablation is not an ablation."""
    graphs = _tensors(3)
    model = build_model(int(graphs[0].x.shape[1]), 0, seed=3)
    before = model_scores(model, graphs)
    scrambled = [type(g)(x=g.x, a_out=torch.zeros_like(g.a_out), a_in=torch.zeros_like(g.a_in),
                         src=g.src, dst=g.dst, y=g.y, sample=g.sample) for g in graphs]
    assert model_scores(model, scrambled) == before

    sage = build_model(int(graphs[0].x.shape[1]), 2, seed=3)
    assert model_scores(sage, scrambled) != model_scores(sage, graphs)


# ── baselines ───────────────────────────────────────────────────────────────────
def test_baselines_score_every_row() -> None:
    s = _sample()
    pairs = s.candidates()
    text = {label: np.eye(768)[i] for i, label in enumerate(s.nodes)}
    trans = {(s.nodes[0], s.nodes[3]): 4}
    occ = {s.nodes[3]: 9}
    for name in ("prior", "popularity", "text", "adamic_adar", "pref_attach"):
        scores = baseline_scores(name, s, pairs, trans, occ, text)
        assert len(scores) == len(pairs)
        assert all(isinstance(x, float) for x in scores)


def test_prior_baseline_reads_the_transition_table() -> None:
    s = _sample()
    pairs = s.candidates()
    trans = {(s.nodes[0], s.nodes[3]): 7}
    scores = baseline_scores("prior", s, pairs, trans, {}, {})
    hit = [sc for (i, j, _), sc in zip(pairs, scores, strict=True) if (i, j) == (0, 3)]
    assert hit == [7.0]


def test_unknown_baseline_raises() -> None:
    with pytest.raises(ValueError, match="unknown baseline"):
        baseline_scores("nope", _sample(), [], {}, {}, {})


def test_missing_embeddings_degrade_to_one_neutral_vector_and_say_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the first smoke run reported 0/270 embedding hits because a raw SELECT
    returns the pgvector column as a STRING and the exception was swallowed. The fallback is
    legitimate; the SILENCE was not. The hit count must be reported, and it must be 0 here."""
    import analysis.embeddings as emb

    def boom(_session: Any) -> Any:
        raise RuntimeError("no database")

    monkeypatch.setattr(emb, "load_matrix", boom)
    vectors, hits = load_text_vectors(["Mount", "Armbar"])
    assert hits == 0
    assert np.array_equal(vectors["Mount"], vectors["Armbar"])  # every label is the same vector


# ── the verdict lattice ─────────────────────────────────────────────────────────
def _armed(deltas: dict[str, tuple[float, float, float]]) -> ArmResult:
    return ArmResult("A", n_eval=20, n_pos=200, coverage_ok=True, deltas=deltas)


def test_verdict_accept_requires_every_comparator() -> None:
    arm = _armed({b: (0.1, 0.05, 0.15) for b in BASELINES})
    _decide(arm)
    assert arm.verdict == "ACCEPT"


def test_verdict_partial_when_the_two_decisive_ones_are_beaten() -> None:
    deltas = {b: (0.0, -0.05, 0.05) for b in BASELINES}
    deltas["prior"] = deltas["mlp"] = (0.1, 0.05, 0.15)
    arm = _armed(deltas)
    _decide(arm)
    assert arm.verdict == "PARTIAL"


def test_verdict_reject_when_the_prior_is_not_beaten() -> None:
    deltas = {b: (0.1, 0.05, 0.15) for b in BASELINES}
    deltas["prior"] = (0.0, -0.02, 0.02)
    arm = _armed(deltas)
    _decide(arm)
    assert arm.verdict == "REJECT"
    assert "`prior`" in arm.verdict_why


def test_power_gate_precedes_every_verdict() -> None:
    arm = ArmResult("A", n_eval=2, n_pos=3, coverage_ok=False,
                    deltas={b: (0.9, 0.8, 1.0) for b in BASELINES})
    _decide(arm)
    assert arm.verdict == "UNDERPOWERED"
