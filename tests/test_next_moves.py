"""Contract tests for the next-move rankers — all offline, no DB, no network, no model load."""

from __future__ import annotations

import numpy as np
import pytest

from analysis.next_moves import (
    OPP,
    OWN,
    UNK,
    MarkovNextMoves,
    build_vocab,
    corpus_points,
    decision_points,
    evaluate,
    log_prior,
    markov_rank_fn,
    split_by_bout,
)
from analysis.next_moves_embed import (
    EmbedRanker,
    blend,
    build_candidate_texts,
    candidate_text,
    guidance_block,
    query_text,
)

SEQ = [
    {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
    {"label": "Armbar", "type": "submission", "actor_id": "A"},
    {"label": "Guard Pass", "type": "pass", "actor_id": "B"},
    {"label": "Mount", "type": "control", "actor_id": "B"},
    {"label": "Armbar", "type": "submission", "actor_id": "B"},
]


def _bout(bid: str, seq=SEQ, a="A", b="B"):
    return {"id": bid, "a": a, "b": b, "sequence": seq}


# ── decision points ─────────────────────────────────────────────────────────────


def test_decision_points_track_state_and_relative_actor():
    pts = decision_points(SEQ, "b1")
    assert [(p.state, p.target, p.target_rel) for p in pts] == [
        ("Closed Guard", "Armbar", OWN),
        ("Closed Guard", "Guard Pass", OPP),
        ("Mount", "Armbar", OWN),
    ]


def test_actions_before_the_first_state_are_dropped():
    seq = [
        {"label": "Double Leg Takedown", "type": "takedown", "actor_id": "A"},
        {"label": "Closed Guard", "type": "guard", "actor_id": "B"},
        {"label": "Armbar", "type": "submission", "actor_id": "B"},
    ]
    pts = decision_points(seq, "b1")
    assert [p.target for p in pts] == ["Armbar"]


def test_unreadable_actor_field_makes_every_rel_unknown():
    pts = decision_points(SEQ, "b1", rel_readable=False)
    assert {p.target_rel for p in pts} == {UNK}
    assert all(not p.rel_readable for p in pts)
    # the label target is unaffected — that is the whole point of separating the two
    assert [p.target for p in pts] == [p.target for p in decision_points(SEQ, "b1")]


def test_history_carries_previous_actions_relative_to_the_current_state():
    pts = decision_points(SEQ, "b1")
    assert pts[0].history == ()
    assert pts[1].history == (("Armbar", OWN),)
    # the Mount point's state belongs to B, so A's earlier armbar reads as the opponent's
    assert pts[2].history == (("Armbar", OPP), ("Guard Pass", OWN))


def test_corpus_points_gates_one_sided_bouts():
    one_sided = [{**e, "actor_id": "A"} for e in SEQ] + [
        {"label": "Heel Hook", "type": "submission", "actor_id": "A"}
    ]
    pts, stats = corpus_points([_bout("b1"), _bout("b2", one_sided)])
    assert stats["bouts"] == 2
    assert stats["bouts_rel_readable"] == 1
    assert {p.target_rel for p in pts if p.bout_id == "b2"} == {UNK}


# ── split ───────────────────────────────────────────────────────────────────────


def test_split_never_puts_a_bout_on_both_sides():
    pts = [p for i in range(30) for p in decision_points(SEQ, f"b{i:02d}")]
    train, val = split_by_bout(pts)
    assert train and val
    assert not ({p.bout_id for p in train} & {p.bout_id for p in val})
    assert len(train) + len(val) == len(pts)


def test_split_is_deterministic():
    pts = [p for i in range(30) for p in decision_points(SEQ, f"b{i:02d}")]
    assert split_by_bout(pts) == split_by_bout(pts)
    assert split_by_bout(pts)[1] != split_by_bout(pts, seed=1)[1]


def test_vocab_is_built_from_train_only():
    train = decision_points(SEQ, "b1")
    vocab = build_vocab(train, library=[])
    assert set(vocab) == {"Armbar", "Guard Pass"}
    assert "Heel Hook" not in vocab  # a val-only label must stay out of vocabulary


# ── the Markov model ────────────────────────────────────────────────────────────


@pytest.fixture
def fitted():
    vocab = ["Armbar", "Guard Pass", "Heel Hook", "Berimbolo"]
    return MarkovNextMoves(vocab).fit(decision_points(SEQ, "b1"))


def test_distribution_sums_to_one_and_is_strictly_positive(fitted):
    for state in ("Closed Guard", "Mount", "A State Nobody Logged"):
        d = fitted.dist(state, [("Armbar", OWN)])
        assert abs(sum(d.values()) - 1.0) < 1e-9
        assert all(v > 0 for v in d.values())
        assert set(d) == set(fitted.vocab)


def test_every_backoff_order_still_sums_to_one():
    pts = decision_points(SEQ, "b1")
    for order in (0, 1, 2):
        m = MarkovNextMoves(["Armbar", "Guard Pass", "Heel Hook"], max_order=order).fit(pts)
        assert abs(sum(m.dist("Closed Guard").values()) - 1.0) < 1e-9


def test_order_zero_ignores_the_state():
    pts = decision_points(SEQ, "b1")
    m = MarkovNextMoves(["Armbar", "Guard Pass", "Heel Hook"], max_order=0).fit(pts)
    assert m.dist("Closed Guard") == m.dist("Mount")


def test_rank_returns_k_items_sorted_by_probability(fitted):
    top = fitted.rank_next_moves("Closed Guard", (), 3)
    assert len(top) == 3
    assert [p for _, p in top] == sorted((p for _, p in top), reverse=True)
    assert top[0][0] == "Armbar"
    assert fitted.rank_next_moves("Closed Guard", (), 0) == []
    assert len(fitted.rank_next_moves("Closed Guard", (), 99)) == len(fitted.vocab)


def test_ties_break_on_the_label_not_on_dict_order(fitted):
    # Two labels nobody ever played from this state have identical probability.
    top = fitted.rank_next_moves("Nowhere", (), 4)
    tied = [lb for lb, p in top if abs(p - top[-1][1]) < 1e-12]
    assert tied == sorted(tied)


def test_previous_action_changes_the_answer():
    seq = [
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Armbar", "type": "submission", "actor_id": "A"},
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Triangle Choke", "type": "submission", "actor_id": "A"},
    ]
    m = MarkovNextMoves(["Armbar", "Triangle Choke"]).fit(decision_points(seq, "b1"))
    assert m.rank_next_moves("Closed Guard", [("Armbar", OWN)], 1)[0][0] == "Triangle Choke"
    assert m.rank_next_moves("Closed Guard", (), 1)[0][0] == "Armbar"


def test_relative_actor_refuses_to_guess_on_an_unseen_pair(fitted):
    assert fitted.rel_of("Mount", "Armbar") == (OWN, 1.0)
    assert fitted.rel_of("Mount", "Heel Hook") == (UNK, 0.0)


def test_log_prior_is_finite_everywhere(fitted):
    lp = log_prior(fitted, "Nowhere", ())
    assert set(lp) == set(fitted.vocab)
    assert all(np.isfinite(v) for v in lp.values())


# ── evaluation ──────────────────────────────────────────────────────────────────


def test_evaluate_counts_what_it_says_it_counts():
    pts = decision_points(SEQ, "b1")  # targets: Armbar, Guard Pass, Armbar

    def always_armbar(_p, k):
        return [("Armbar", 1.0, OWN), ("Heel Hook", 0.0, UNK), ("Berimbolo", 0.0, UNK)][:k]

    res = evaluate(always_armbar, pts)
    assert res["n"] == 3
    assert res["top1"] == pytest.approx(2 / 3)
    assert res["top3"] == pytest.approx(2 / 3)
    assert res["mrr"] == pytest.approx(2 / 3)
    # joint: both Armbar targets are "own" and the ranker says "own" → 2 of 3 gated points
    assert res["joint_n"] == 3
    assert res["joint_top3"] == pytest.approx(2 / 3)


def test_evaluate_scores_a_two_tuple_ranker_as_an_actor_miss():
    pts = decision_points(SEQ, "b1")
    res = evaluate(lambda _p, k: [("Armbar", 1.0)][:k], pts)
    assert res["top1"] == pytest.approx(2 / 3)
    assert res["joint_top3"] == 0.0


def test_evaluate_is_zero_on_an_out_of_vocabulary_target(fitted):
    seq = [
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Heel Hook", "type": "submission", "actor_id": "A"},
    ]
    m = MarkovNextMoves(["Armbar", "Guard Pass"]).fit([])
    res = evaluate(markov_rank_fn(m), decision_points(seq, "b1"))
    assert res["top5"] == 0.0 and res["mrr"] == 0.0


def test_cluster_ci_brackets_the_point_estimate():
    pts = [p for i in range(20) for p in decision_points(SEQ, f"b{i:02d}")]
    res = evaluate(lambda _p, k: [("Armbar", 1.0, OWN)][:k], pts, ci=True)
    assert res["top3_lo"] <= res["top3"] <= res["top3_hi"]


# ── embedding layer ─────────────────────────────────────────────────────────────


def test_candidate_and_query_texts_carry_the_fields_they_promise():
    txt = candidate_text(
        {"en": "Heel Hook", "pt": "Chave de Calcanhar", "type": "submission",
         "variants": ["heel hook", "chave de pé"]}
    )
    assert "Heel Hook" in txt and "Chave de Calcanhar" in txt
    assert "chave de pé" in txt and "finalização" in txt

    q = query_text("Closed Guard", [("Armbar", OWN)], OWN, state_type="guard")
    assert "Closed Guard" in q and "Armbar" in q and "guarda" in q
    assert "qualquer uma das duas" in query_text("Closed Guard", (), UNK)


def test_uncurated_label_falls_back_to_the_bare_label():
    texts = build_candidate_texts(["Not In The Library"], library=[])
    assert texts == ["Not In The Library"]


def test_ranker_refuses_an_unnormalised_candidate_matrix():
    with pytest.raises(ValueError, match="L2-normalised"):
        EmbedRanker(["a", "b"], np.array([[3.0, 0.0], [0.0, 1.0]]))
    with pytest.raises(ValueError, match="does not match"):
        EmbedRanker(["a", "b"], np.eye(3))


def test_alpha_zero_is_the_prior_and_alpha_one_is_the_embedding(fitted):
    vocab = fitted.vocab
    eye = np.eye(len(vocab))
    r = EmbedRanker(vocab, eye)
    lp = log_prior(fitted, "Closed Guard", ())
    assert [lb for lb, _ in r.rank(eye[0], lp, alpha=0.0, k=len(vocab))] == [
        lb for lb, _ in fitted.rank_next_moves("Closed Guard", (), len(vocab))
    ]
    last = vocab[-1]
    assert r.rank(eye[-1], lp, alpha=1.0, k=1)[0][0] == last


def test_blend_standardisation_is_scale_free():
    cos = np.array([0.10, 0.11, 0.12])
    logp = np.array([-9.0, -3.0, -6.0])
    z = blend(cos, logp, 0.5)
    raw = blend(cos, logp, 0.5, standardize=False)
    # standardised: cosine actually competes; raw: the prior's 6-nat spread swamps 0.02 cosine
    assert int(np.argmax(z)) == 2 or int(np.argmax(z)) == 1
    assert int(np.argmax(raw)) == 1
    assert np.allclose(blend(np.zeros(3), logp, 1.0), np.zeros(3))


# ── guidance block ──────────────────────────────────────────────────────────────


def test_guidance_block_works_with_no_embedding_at_all(fitted):
    txt = guidance_block("Closed Guard", [("Armbar", OWN)], OWN, k=3, model=fitted)
    assert "corpus statistics" in txt.lower()
    assert "not a constraint" in txt.lower()
    assert "Closed Guard" in txt
    assert txt.count("\n- ") == 3
    assert "%" in txt


def test_guidance_block_prints_the_markov_probability_even_when_the_order_is_hybrid(fitted):
    eye = np.eye(len(fitted.vocab))
    r = EmbedRanker(fitted.vocab, eye)
    hybrid = guidance_block("Closed Guard", (), OWN, k=2, model=fitted, ranker=r,
                            qvec=eye[-1], alpha=1.0)
    # α=1 puts the last vocab entry first, but its printed number is still its corpus share
    first = hybrid.split("\n- ")[1]
    assert fitted.vocab[-1] in first
    share = fitted.dist("Closed Guard")[fitted.vocab[-1]]
    assert f"{share:.1%}" in first


def test_guidance_block_is_empty_without_a_model():
    assert guidance_block("Closed Guard", (), OWN, model=None) == ""
