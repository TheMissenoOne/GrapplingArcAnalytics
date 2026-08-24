"""PoC-E1 — empirical-Bayes shrinkage eval, pure-function tests (no DB except the report
path, which asserts the read-only fallback the way test_interaction_graph.py does)."""

from __future__ import annotations

from typing import Any

from analysis.poc.e1_shrinkage_eval import (
    ADMISSIBLE_TYPES,
    CRITERION,
    EXCLUDED_TYPES,
    Bout,
    admissible_events,
    athlete_node_counts,
    evaluate_ranking,
    evaluate_signatures,
    fetch_corpus,
    measure_fill_rates,
    node_counts,
    render_markdown,
)

_OMIT = object()


def _e(t: str, label: str, actor: str, successful: Any = _OMIT) -> dict[str, Any]:
    ev: dict[str, Any] = {"type": t, "label": label, "actor_id": actor}
    if successful is not _OMIT:
        ev["successful"] = successful
    return ev


# ── precondition: fill rates + admissibility ─────────────────────────────────────
def test_admissible_and_excluded_types_partition_the_event_model() -> None:
    from analysis.attribution import EVENT_TYPES

    assert ADMISSIBLE_TYPES | EXCLUDED_TYPES == EVENT_TYPES
    assert ADMISSIBLE_TYPES.isdisjoint(EXCLUDED_TYPES)


def test_measure_fill_rates_counts_true_false_and_omitted() -> None:
    matches = [{"sequence": [
        _e("submission", "Armbar", "A", True),
        _e("submission", "Armbar", "A", False),
        _e("submission", "Armbar", "A"),  # omitted
        _e("guard", "Closed Guard", "A"),
        "not-a-dict",  # defensive: malformed rows are skipped
    ]}]
    fills = measure_fill_rates(matches)
    assert fills["submission"].n == 3 and fills["submission"].filled == 2
    assert fills["submission"].true_n == 1 and fills["submission"].false_n == 1
    assert fills["guard"].n == 1 and fills["guard"].filled == 0
    assert fills["guard"].fill_rate == 0.0


def test_admissible_events_drops_excluded_types_and_omitted_successful() -> None:
    b = Bout(key=(2024, "t", "m1"), a_id="A", b_id="B", sequence=[
        _e("submission", "Armbar", "A", True),   # kept
        _e("guard", "Closed Guard", "A", True),  # excluded type
        _e("takedown", "Double Leg", "B"),        # omitted successful -> dropped
        _e("pass", "Knee Slice Pass", "B", False),  # kept
    ])
    out = admissible_events([b])
    assert len(out) == 2
    assert {e["node_key"] for e in out} == {"armbar", "knee slice pass"}
    assert all("successful" in e for e in out)


# ── node/athlete counts ───────────────────────────────────────────────────────────
def test_node_counts_and_athlete_node_counts() -> None:
    events = [
        {"node_key": "armbar", "actor_id": "A", "successful": True},
        {"node_key": "armbar", "actor_id": "A", "successful": False},
        {"node_key": "armbar", "actor_id": "B", "successful": True},
    ]
    nc = node_counts(events)
    assert nc["armbar"].successes == 2 and nc["armbar"].trials == 3

    anc = athlete_node_counts(events)
    assert anc[("A", "armbar")].successes == 1 and anc[("A", "armbar")].trials == 2
    assert anc[("B", "armbar")].successes == 1 and anc[("B", "armbar")].trials == 1


# ── ranking eval: shrinkage pulls a noisy small-n node toward the population ──────
def test_evaluate_ranking_shrinks_small_n_toward_population_and_scores_both() -> None:
    # Population of 8 nodes, true rate ~0.3, most with plenty of trials; one node ("fluke")
    # goes 3-for-3 in train by chance and regresses to ~0.3 in eval -- shrinkage should have
    # pulled its train-period estimate down, unlike the raw 1.0.
    train = []
    eval_ = []
    for i in range(7):
        node = f"node{i}"
        train += [{"node_key": node, "successful": s} for s in
                  ([True] * 6 + [False] * 14)]  # 6/20 = 0.30
        eval_ += [{"node_key": node, "successful": s} for s in
                  ([True] * 3 + [False] * 7)]   # 3/10 = 0.30
    train += [{"node_key": "fluke", "successful": True}] * 3       # raw 1.0 on n=3
    eval_ += [{"node_key": "fluke", "successful": s} for s in
              [True, False, False, False]]      # 1/4 = 0.25, close to the population

    result = evaluate_ranking(train, eval_)
    assert result.n_nodes == 8
    # The shrunken estimate for "fluke" (raw 3/3 = 1.0) must sit strictly below its raw rate.
    from analysis.shrinkage import shrink_beta_binomial

    shrunk_fluke = shrink_beta_binomial(3, 3, result.prior)
    assert shrunk_fluke < 1.0
    assert result.raw_corr.n == 8 and result.shrunk_corr.n == 8


# ── signature survival: churn is counted, not judged ──────────────────────────────
def test_evaluate_signatures_counts_old_new_and_survivors() -> None:
    # Node "back" has 4 other athletes at a low, tight rate and one athlete ("hero") far above
    # them on n=3 -- clears the old z-rule. "hero" is otherwise a low performer overall, so the
    # shrunken CI for "back" should not clear her own (low) mean, and the old signature should
    # not survive.
    events = []
    # Slightly varied so the "other athletes" population has nonzero spread (a std=0
    # population makes the old-rule z uncomputable, which the function correctly skips).
    other_rates = {
        "p1": [True, False, False, False, False],       # 1/5 = 0.20
        "p2": [True, False, False, False, False],       # 1/5 = 0.20
        "p3": [True, True, False, False, False],        # 2/5 = 0.40
        "p4": [False, False, False, False, False],      # 0/5 = 0.00
    }
    for a, results in other_rates.items():
        events += [{"actor_id": a, "node_key": "back", "successful": s} for s in results]
    events += [{"actor_id": "hero", "node_key": "back", "successful": True}] * 3  # 3/3 = 1.0
    events += [{"actor_id": "hero", "node_key": "other", "successful": s} for s in
              [False] * 10]  # hero's own baseline is low overall

    sig = evaluate_signatures(events)
    assert sig.n_candidates >= 1
    assert sig.n_old >= 1  # hero's "back" clears the old z-rule


# ── doc scaffolding ────────────────────────────────────────────────────────────────
def test_render_markdown_carries_the_criterion_verbatim() -> None:
    from analysis.poc.e1_shrinkage_eval import FetchReport

    md = render_markdown({}, FetchReport(), None, None)
    assert CRITERION in md
    assert "## Consumers" in md


def test_corpus_loader_reports_instead_of_raising_without_a_db(monkeypatch: Any) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("db.base._engine", None, raising=False)
    rep = fetch_corpus()
    assert rep.bouts == [] and rep.error
