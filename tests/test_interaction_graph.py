"""PoC-E8 — the interaction graph and its harness.

The builder is a second graph PRODUCT, not a variant of ActionFlow, so the contract
worth pinning is exactly where the two differ: an actor-switch edge exists as topology,
ActionFlow refuses it, and neither one leaks into the other. The harness tests pin the
things a report would silently get wrong — the gate, the temporal split, the label, and
the fast AUC agreeing with `stats_rigor`'s.
"""

from __future__ import annotations

import math

from analysis.attribution import bout_flags
from analysis.poc.e8_interaction_graph import (
    Bout,
    actionflow_kernel,
    corpus_bouts,
    distinct_up_to_mirror,
    eval_rows,
    finish_label,
    fixture_bouts,
    interaction_kernel,
    rank_auc,
    run_pass,
    switch_edge_stability,
    temporal_split,
    verdict,
)
from analysis.stats_rigor import auc as rigor_auc
from analysis.transitions.build_graph import network_from_sequences
from analysis.transitions.interaction_graph import (
    interaction_graph,
    node_key,
    role_map,
    switch_edges,
)


def _ev(label: str, actor: str, type_: str = "control", ok: bool = True) -> dict[str, object]:
    return {"label": label, "actor_id": actor, "type": type_, "successful": ok}


# ── builder correctness ─────────────────────────────────────────────────────────
def test_actor_switch_edge_exists_where_actionflow_has_nothing() -> None:
    """`you:Turtle → opp:Back Control` — the edge the review's §1 is about."""
    seq = [_ev("Turtle Position", "you"), _ev("Back Control", "partner")]
    g = interaction_graph([seq])
    assert g.has_edge("you:turtle position", "opp:back control")
    assert g["you:turtle position"]["opp:back control"]["switch"] is True
    # ActionFlow, by construction, has the two nodes and NO edge between them.
    af = network_from_sequences([seq])
    assert af.has_node("Turtle Position") and af.has_node("Back Control")
    assert not af.has_edge("Turtle Position", "Back Control")


def test_within_actor_succession_is_also_an_edge() -> None:
    """The spec is 'consecutive events regardless of actor' — both kinds are edges."""
    seq = [_ev("Closed Guard", "you", "guard"), _ev("Armbar", "you", "submission")]
    g = interaction_graph([seq])
    assert g.has_edge("you:closed guard", "you:armbar")
    assert g["you:closed guard"]["you:armbar"]["switch"] is False
    assert switch_edges(g) == []


def test_self_loop_dropped_but_cross_actor_same_label_kept() -> None:
    same = interaction_graph([[_ev("Half Guard", "you"), _ev("Half Guard", "you")]])
    assert same.number_of_edges() == 0            # A → A is not a transition
    assert same.nodes["you:half guard"]["occ"] == 2
    contested = interaction_graph([[_ev("Half Guard", "you"), _ev("Half Guard", "partner")]])
    assert contested.has_edge("you:half guard", "opp:half guard")


def test_no_edge_spans_two_sequences_or_an_unattributed_event() -> None:
    a, b = [_ev("Mount", "you")], [_ev("Back Control", "you")]
    assert interaction_graph([a, b]).number_of_edges() == 0
    # An event with no actor BREAKS the chain rather than being joined through.
    broken = interaction_graph([[_ev("Mount", "you"),
                                 {"label": "Scramble", "type": "transition"},
                                 _ev("Back Control", "partner")]])
    assert broken.number_of_edges() == 0


def test_label_canonicalisation_is_the_system_chain() -> None:
    """clean_label → _normalize_name → canonicalize, same key space as everything else."""
    assert node_key("Turtle Escape", "escape") == "escape to turtle"   # names.SYNONYMS
    assert node_key("Armbar Attempt", "submission") == "armbar"        # clean_label
    g = interaction_graph([[_ev("TURTLE  ESCAPE", "you", "escape"),
                            _ev("Escape to Turtle", "you", "escape")]])
    assert list(g.nodes) == ["you:escape to turtle"]  # both spellings, one node
    assert g.nodes["you:escape to turtle"]["label"] == "escape to turtle"


def test_role_map_is_fixed_per_sequence() -> None:
    app = [_ev("Mount", "you"), _ev("Back Control", "partner")]
    assert role_map(app, None) == {"you": "you", "partner": "opp"}
    corpus = [_ev("Mount", "A"), _ev("Back Control", "B")]
    assert role_map(corpus, "B") == {"A": "opp", "B": "you"}
    assert role_map(corpus, None) == {"A": "you", "B": "opp"}  # first-seen


def test_reward_and_risk_are_immediate_successor_versions() -> None:
    seq = [_ev("Back Control", "you"), _ev("Rear Naked Choke", "you", "submission")]
    g = interaction_graph([seq])
    assert g.nodes["you:back control"]["reward"] == 1
    assert g.nodes["you:back control"]["risk"] == 0
    against = interaction_graph([[_ev("Turtle Position", "you"),
                                  _ev("Rear Naked Choke", "partner", "submission")]])
    assert against.nodes["you:turtle position"]["risk"] == 1
    assert against.nodes["you:turtle position"]["reward_risk"] == -1.0


def test_mirror_folding_counts_distinct_patterns() -> None:
    e = [("you:turtle position", "opp:back control"),
         ("opp:turtle position", "you:back control")]
    assert distinct_up_to_mirror(e) == 1
    assert distinct_up_to_mirror(e[:1]) == 1


# ── harness ─────────────────────────────────────────────────────────────────────
def test_temporal_split_is_chronological_and_never_splits_a_day() -> None:
    bouts = [Bout(key=(d,), sequence=[_ev("Mount", "you")]) for d in "abcdefgh"]
    train, held = temporal_split(bouts, eval_fraction=0.25)
    assert [b.key[0] for b in train] == list("abcdef")
    assert [b.key[0] for b in held] == list("gh")
    tied = [Bout(key=("a",), sequence=[]) for _ in range(8)]
    assert temporal_split(tied)[1] == []  # one key → nothing can be held out


def test_finish_label_is_same_actor_within_k() -> None:
    seq = [_ev("Back Control", "you"), _ev("Escape", "partner", "escape"),
           _ev("Rear Naked Choke", "you", "submission", ok=True)]
    assert finish_label(seq, 0) is True
    assert finish_label(seq, 1) is False           # the finish is not the partner's
    assert finish_label(seq, 0, k=1) is False      # outside the window
    missed = [_ev("Back Control", "you"), _ev("Armbar", "you", "submission", ok=False)]
    assert finish_label(missed, 0) is False        # attempted ≠ landed


def test_eval_rows_are_you_side_only_and_shared_by_both_kernels() -> None:
    b = Bout(key=(1,), sequence=[_ev("Mount", "you"), _ev("Back Control", "partner")],
             perspective=None)
    rows = eval_rows([b])
    assert [r[0]["label"] for r in rows] == ["Mount"]
    ik, ak = interaction_kernel(), actionflow_kernel()
    assert ik.node_of(rows[0][0]) == "you:mount"
    assert ak.node_of(rows[0][0]) == "Mount"


def test_rank_auc_matches_stats_rigor() -> None:
    scores = [0.1, 0.4, 0.35, 0.8, 0.8, 0.2, 0.9, 0.0]
    labels = [False, True, False, True, False, False, True, False]
    assert math.isclose(rank_auc(scores, labels), rigor_auc(scores, labels, n_boot=1).auc,
                        rel_tol=1e-12)
    assert rank_auc([1.0, 2.0], [True, True]) == 0.5  # one class → chance, never a crash


def test_stability_is_deterministic_and_bounded() -> None:
    bouts = [Bout(key=(i,), sequence=[_ev("Turtle Position", "you"),
                                      _ev("Back Control", "partner")]) for i in range(5)]
    a = switch_edge_stability(bouts, n_boot=20)
    assert a == switch_edge_stability(bouts, n_boot=20)          # fixed seed
    assert set(a) == {("you:turtle position", "opp:back control")}
    assert a[("you:turtle position", "opp:back control")] == 1.0  # in every resample


# ── the gate ────────────────────────────────────────────────────────────────────
def test_gate_refuses_a_one_sided_bout() -> None:
    """`perspective_reliable` is the corpus precondition: every event on one athlete
    carries no actor information, so its interaction edges measure the ingest batch."""
    one_sided = [_ev(lbl, "A") for lbl in
                 ("Half Guard", "Smash Pass", "Back Control", "Mount", "Armbar", "Scarf Hold")]
    assert bout_flags(one_sided, "A", "B")["perspective_reliable"] is False
    two_sided = [*one_sided[:5], _ev("Scarf Hold", "B")]
    assert bout_flags(two_sided, "A", "B")["perspective_reliable"] is True


def test_corpus_loader_reports_instead_of_raising_without_a_db(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("db.base._engine", None, raising=False)
    rep = corpus_bouts()
    assert rep.bouts == [] and rep.error


# ── fixture pass reproducibility (LGPD: numbers stay in docs/research/poc/e8.md) ──
def test_fixture_pass_reproduces_the_external_switch_edge_count() -> None:
    p = run_pass("fixture", fixture_bouts(), n_boot=200, n_stability=10)
    assert p.switch_types == 23           # external review §1 measured 23
    assert p.n_events == 136 and p.n_bouts == 26
    again = run_pass("fixture", fixture_bouts(), n_boot=200, n_stability=10)
    assert (again.switch_types, again.switch_occurrences) == (p.switch_types,
                                                              p.switch_occurrences)
    assert [r.auc for r in again.results] == [r.auc for r in p.results]   # fixed seed
    assert again.stability == p.stability


def test_verdict_needs_the_corpus_and_reads_the_criterion_verbatim() -> None:
    assert verdict(None)["decided"] is False
    p = run_pass("fixture", fixture_bouts(), n_boot=200, n_stability=10)
    v = verdict(p)
    # limb 1 = the paired ΔAUC interval must exclude 0; limb 2 = ≥1 stable switch edge.
    assert v["auc_win"] == (p.delta[1] > 0.0)
    assert v["stable_edges"] == len(p.stable_switch)
    assert v["accept"] == (v["auc_win"] or v["stable_edges"] > 0)
